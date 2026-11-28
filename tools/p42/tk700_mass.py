#!/usr/bin/env python3
"""TK-700 (В-183): массовый прогон клеток c539 движком по месяцам январь–июнь 2026 (посуточный P&L каждой клетки → TK-692). Замок — только на запуск (run-id), одного чтения нет.

  python3 tk700_mass.py plan  --cellset all1 [--dry] [--wins m01,…] [--sides long,short]         # клетки, охват по окнам, партии, без движка
  python3 tk700_mass.py run   --run-id R --cellset all1 [--dry] [--wins …] [--sides …] [--vmax 400 --sigmax 26] [--sigs N --seed S] [--every N] [--max-batches K] [--only-skipped R0] [--threads 2] [--cap-gb 6]
  python3 tk700_mass.py check --run-id R                                                         # dry: сверка с числами БД (сделки, Σ $, посуточный ряд) по клеткам набора
  python3 tk700_mass.py timing --run-id R [--cells 10000]                                          # время движка по партиям → минуты на 10 000 клеток в месяц
  python3 tk700_mass.py need  --cellset all1 [--cov /data/p42-tk700/cov/cov.json]                  # клетки по столбцам: не построены / не покрыты (`tk700_cov.py`) — без движка
  python3 tk700_mass.py classes --cellset all1 [--cov …]                                         # клетки по классам A / B / C / H / X / P (см. `cell_class`) — без движка
  … plan / run … --class A[,B,…]                                                                  # только клетки этих классов (по умолчанию все, как раньше)

БД результатов `/data/p42-tk700/mass/mass.sqlite` (ключ — run_id): `mass_run`, `mass_batch` (время, ЦП, память партии), `mass_cw` (клетка × окно: сделки, Σ $, ошибки охраны),
`mass_day` (клетка × сутки: P&L = gross − fees по дате входа, число сделок; сутки — от `D0` окна из `mass_run.params`), `mass_skip` (пара клетка × окно не считалась: охват ниже 0,7 / нет строк базы).
Партии — как TK-674: ≤ vmax вариантов и ≤ sigmax ключей прохода сигналов, клетки отсортированы по (sig, cid); готовая партия (run_id, окно, сторона, bkey) при повторном запуске пропускается.
Классы клеток (решение координатора 24.11, В-172): A — каждый столбец фильтра построен и посчитан на тех же строках, что в Jul–Nov; B — столбцы TK-654 (в БД есть у ВСЕХ кандидатов дампа, на H1 пока только на строках базы: дыра G-key — считать после достройки); C — hist ok10_*;
H — столбцы истории уровня из `--allow-diff` ворот (H1 ≠ таблица TK-671 по построению); X — исключены (cc_* / clu_* / bd_*, правило TK-674); P — иной столбец не построен (`cov.json`); покрытие < 0,7 в окне — пропуск пары клетка × окно (`mass_skip`), класс не меняет.
`--dry` — окна wa / wb / wc (Jul–Nov), числа БД сверяются (`check`); без `--dry` — окна m01…m06 (H1), движок только с жетоном замка запуска (`tk700_lock`)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import resource
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk700_holdout as ho  # noqa: E402
import tk700_lock as lk  # noqa: E402

MASS_DB = "/data/p42-tk700/mass/mass.sqlite"
OUT_ROOT = "/data/p42-tk700/mass/out"
BINS = {"long": "/data/p42-tk683/bin/alpha-tk683k-plain.c400af11", "short": "/data/p42-tk683/bin/alpha-tk683l-plain.562f6899"}     # бинарь строк БД своей стороны
VMAX, SIGMAX = 400, 26
COVER_MIN = ho.COVER_MIN
COLS_B = {"tpm60", "cons60", "mv5", "pierce_ep", "spread_tau", "zv6h", "n24", "m24"}          # TK-654: ключи у всех кандидатов дампа в БД, на H1 только на строках базы
COLS_H = {"age_h", "pierce_max_bp", "touch_ep", "since_touch_h", "depth_min_bp", "depth_mean_bp", "approach_dur_min", "since_touch_prior_h", "touch_prior_none"}   # `--allow-diff` ворот строк базы
EXCLUDED = "cc_* / clu_* / bd_*"
GKEY = "/data/p42-tk700/gkey"                                                                      # таблицы кандидатов TK-654 по окнам H1 (`tk700_gkey.py table`)
GCOLS = ("mv5", "tpm60", "pierce_ep", "spread_tau", "cons60", "zv6h", "n24", "m24")
SCHEMA = """
CREATE TABLE IF NOT EXISTS mass_run(run_id TEXT PRIMARY KEY, cellset TEXT, dry INTEGER, wins TEXT, bin_md5 TEXT, params TEXT, created TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS mass_batch(run_id TEXT, win TEXT, side TEXT, bkey TEXT, n_var INTEGER, n_sig INTEGER, n_filt INTEGER, ftab_rows INTEGER, engine_s REAL, cpu_s REAL, post_s REAL,
                                      rc INTEGER, maxrss_mb REAL, utc TEXT, PRIMARY KEY(run_id, win, side, bkey));
CREATE TABLE IF NOT EXISTS mass_cw(run_id TEXT, cid INTEGER, win TEXT, n INTEGER, usd REAL, errs TEXT, note TEXT, PRIMARY KEY(run_id, cid, win)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS mass_day(run_id TEXT, cid INTEGER, day INTEGER, pnl REAL, n INTEGER, PRIMARY KEY(run_id, cid, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS mass_skip(run_id TEXT, cid INTEGER, win TEXT, why TEXT, PRIMARY KEY(run_id, cid, win)) WITHOUT ROWID;
"""


def connect(path=MASS_DB):
    import tk700_cells as cl
    c = cl.connect(path)
    c.executescript(SCHEMA)
    if "note" not in {r[1] for r in c.execute("PRAGMA table_info(mass_cw)")}:
        c.execute("ALTER TABLE mass_cw ADD COLUMN note TEXT")                # прежние запуски (dry*, probe*) — без заметок
    return c


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_cells(db, cellset, side):
    out = []
    for cid, ckey, line, sig, filt, feats, ref_n, ref_usd in db.execute(
            "SELECT cid, ckey, line, sig, filt, feats, ref_n, ref_usd FROM cell WHERE cellset=? AND side=? ORDER BY sig, cid", (cellset, side)):
        out.append(dict(cid=cid, ckey=ckey, line=line, sig=sig, filter=[(f, o, x) for f, o, x in json.loads(filt)], feats=feats.split(",") if feats else [], ref_n=ref_n, ref_usd=ref_usd))
    return out


def pick_sigs(cells, nsig, seed):
    """Кластерная выборка: nsig случайных ключей прохода сигналов (seed) со всеми их клетками."""
    sigs = sorted({c["sig"] for c in cells})
    keep = set(random.Random(seed).sample(sigs, min(nsig, len(sigs))))
    return [c for c in cells if c["sig"] in keep]


def make_batches(cells, vmax, sigmax):
    out, cur, sg = [], [], set()
    for c in cells:
        if cur and (len(cur) >= vmax or (c["sig"] not in sg and len(sg) >= sigmax)):
            out.append(cur)
            cur, sg = [], set()
        cur.append(c)
        sg.add(c["sig"])
    if cur:
        out.append(cur)
    return out


def bkey_of(cur):
    return hashlib.md5(",".join(str(c["cid"]) for c in cur).encode()).hexdigest()[:10]


def need_cols(feat):
    return {"sharp": ("cons60", "mv5"), "n24": ("n24", "m24")}.get(feat, (feat,))


def covered(tab, share_cache, feat):
    """Охват столбца(ов) признака фильтра в таблице окна: доля конечных значений ≥ 0,7 (охрана, как `tk700_holdout.coverage`)."""
    import numpy as np
    worst = 1.0
    for col in need_cols(feat):
        if col not in tab:
            return 0.0
        if col not in share_cache:
            share_cache[col] = float(np.isfinite(tab[col]).mean()) if len(tab["sym"]) else 0.0
        worst = min(worst, share_cache[col])
    return worst


def load_tabs_b(m, side, root=GKEY):
    """Таблицы кандидатов для класса B — как в БД Jul–Nov: строки = кандидаты дампа базы окна (`tk700_gkey.py`, столбцы TK-654 у каждого) ∪ строки базы asm; столбцы asm — по ключу (sym, sig_ms), нет строки — пусто (проходит);
    столбцы TK-654 при совпадении имён — из таблицы кандидатов (в БД они были оттуда)."""
    import numpy as np
    import pandas as pd
    base = ho.load_tabs(m, side)
    out = {}
    for w in m["st"].TRADE_WINDOWS:
        g = pd.read_parquet(os.path.join(root, w.name, "tab-%s.parquet" % side))
        b = base[w.name]
        kb = {(x, int(t)): i for i, (x, t) in enumerate(zip(b["sym"], b["ms"]))}
        kg = {(x, int(t)): i for i, (x, t) in enumerate(zip(g["sym"], g["sig_ms"]))}
        keys = sorted(set(kb) | set(kg))
        n = len(keys)
        ib = np.array([kb.get(k, -1) for k in keys], dtype=np.int64)
        ig = np.array([kg.get(k, -1) for k in keys], dtype=np.int64)
        tab = {"sym": np.array([k[0] for k in keys], dtype=object), "ms": np.array([k[1] for k in keys], dtype=np.int64)}
        for c, v in b.items():
            if c not in ("sym", "ms"):
                col = np.full(n, np.nan)
                col[ib >= 0] = v[ib[ib >= 0]]
                tab[c] = col
        for c in GCOLS:
            col = np.full(n, np.nan)
            col[ig >= 0] = g[c].to_numpy(np.float64)[ig[ig >= 0]]
            tab[c] = col
        out[w.name] = tab
    return out


def tabs_for(m, side, classes):
    """Таблицы окон: для класса B (дыра G-key закрыта) — кандидаты дампа, иначе строки базы asm."""
    return load_tabs_b(m, side) if "B" in classes else ho.load_tabs(m, side)


def cell_window(st, p8, w, side, vdir, filtered, strict_key=False):
    """(n, Σ $, ошибки, заметки, сутки, P&L по суткам, сделок по суткам) одной клетки в одном окне: сделки по дате входа в диапазоне окна (как `tk604_stats.splice`)."""
    import numpy as np
    eng = p8.ENG[ho.SIDE_LETTER[side]]
    t = st.read_trades_csv(os.path.join(vdir, eng + "-trades.csv"))
    dl = st.read_daily_csv(os.path.join(vdir, eng + "-daily.csv"))
    errs, notes = [], []
    if len(t) and np.abs(t.net - (t.gross - t.fees + t.funding)).max() > 2e-6:
        errs.append("net≠gross−fees+funding")
    d = st.abs_day(w, t.entry_ms)
    lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
    sel = np.nonzero((d >= lo_i) & (d <= hi_i))[0]
    first, last = (w.lod - w.t0d).days - st.GRID_START_DAY, (w.hid - w.t0d).days - st.GRID_START_DAY
    ssum = sum(dl.get(i, 0.0) for i in range(first, last + 1))
    nsum = float(t.net[sel].sum())
    if abs(ssum - nsum) > max(1e-4, 2e-6 * len(sel)):
        # окно H1 режет сделки на границах (вход до начала зачёта — сутки 14–19 прогрева, выход после конца месяца): Σ посуточного ряда окна ≠ Σ сделок по дате входа по построению;
        # ошибка — только если не сходится весь прогон (все сутки против всех сделок), иначе заметка с разностью
        if len(t) and abs(sum(dl.values()) - float(t.net.sum())) > max(1e-4, 2e-6 * len(t)):
            errs.append("G7 Σdaily %+.6f≠Σnet %+.6f (весь прогон)" % (sum(dl.values()), float(t.net.sum())))
        else:
            notes.append("граница Σdaily−Σnet %+.4f" % (ssum - nsum))
    if len(t) and int(t.exit_ms.max()) + w.t0_ms >= st.LAST_MS:
        errs.append("G-tape")
    if filtered:
        meta = json.load(open(os.path.join(vdir, "meta.json"), encoding="utf-8"))[side]
        import tk604_run as r4
        fc = r4.filter_counters(meta)
        if fc is None:
            errs.append("G-key: нет счётчиков фильтра")
        elif fc["no_key"] and strict_key:
            errs.append("G-key no_key %s (класс B: ключи TK-654 должны быть у всех кандидатов)" % fc["no_key"])
        elif fc["no_key"]:
            notes.append("no_key=%s" % fc["no_key"])                         # кандидаты движка без строки базы: как в БД Jul–Nov — не сбой (класс A); B — после достройки G-key
    val = (t.gross - t.fees)[sel]
    ds = d[sel]
    if len(ds):
        base = int(ds.min())
        s = np.bincount(ds - base, weights=val)
        k = np.bincount(ds - base)
        nz = np.nonzero(k)[0]
        days, pnl, cnt = [int(base + i) for i in nz], [float(s[i]) for i in nz], [int(k[i]) for i in nz]
    else:
        days, pnl, cnt = [], [], []
    return int(len(sel)), float(val.sum()), errs, notes, days, pnl, cnt


def cmd_plan(a):
    db = connect(a.db)
    m = ho.modes(a.dry)
    st = m["st"]
    wins = a.wins.split(",") if a.wins else m["wins"]
    classes = set(a.cls.split(",")) if a.cls else set()
    unbuilt = bad_columns(a.cov)[0] if classes else set()
    for side in a.sides.split(","):
        cells = keep_classes(load_cells(db, a.cellset, side), classes, unbuilt)
        cells = [c for c in cells if c["cid"] % a.every == 0]
        if a.sigs:
            cells = pick_sigs(cells, a.sigs, a.seed)
        tabs = tabs_for(m, side, classes)
        bt = make_batches(cells, a.vmax, a.sigmax)
        print("%s: клеток %d, ключей сигналов %d, партий на окно %d (vmax %d, sigmax %d)" % (side, len(cells), len({c["sig"] for c in cells}), len(bt), a.vmax, a.sigmax))
        for w in st.TRADE_WINDOWS:
            if w.name not in wins:
                continue
            tab, cache = tabs[w.name], {}
            skip = {}
            for c in cells:
                bad = [f for f in c["feats"] if covered(tab, cache, f) < COVER_MIN]
                if bad:
                    skip[c["cid"]] = bad
            why = {}
            for bs in skip.values():
                for f in bs:
                    why[f] = why.get(f, 0) + 1
            print("  %-4s строк базы %4d, не считаются из-за охвата < %.1f: %d клеток (%.1f %%); по признакам: %s" % (
                w.name, len(tab["sym"]), COVER_MIN, len(skip), 100.0 * len(skip) / max(1, len(cells)), dict(sorted(why.items(), key=lambda x: -x[1])[:8])))
    return 0


def fam_of(col):
    """Семейство столбца для `need`: hist (ok10_d*), mkt (cosig15), fast (FAST TK-681), cc (кластеры монет), bd (глубина книги)."""
    if col.startswith("ok10_d"):
        return "hist"
    if col == "cosig15":
        return "mkt"
    if col.startswith(("cc_", "clu_")):
        return "cc"
    if col.startswith("bd_"):
        return "bd"
    return "fast"


def cmd_need(a):
    """Сколько клеток набора использует не построенные / не покрытые на H1 столбцы (`cov.json` из `tk700_cov.py`) — по сторонам, семействам и столбцам."""
    cov = json.load(open(a.cov, encoding="utf-8"))
    bad_cols = set(cov["not_built"]) | set(cov["uncovered"])
    db = connect(a.db)
    tot, fam, per = {}, {}, {}
    for side, feats in db.execute("SELECT side, feats FROM cell WHERE cellset=?", (a.cellset,)):
        cols = {x for f in (feats.split(",") if feats else []) for x in need_cols(f)} & bad_cols
        tot[side] = tot.get(side, 0) + 1
        fs = {fam_of(x) for x in cols}
        for f in fs:
            fam[side, f] = fam.get((side, f), 0) + 1
        if cols:
            fam[side, "любое"] = fam.get((side, "любое"), 0) + 1
            if len(fs) > 1:
                fam[side, "+".join(sorted(fs))] = fam.get((side, "+".join(sorted(fs))), 0) + 1
        for x in cols:
            per[side, x] = per.get((side, x), 0) + 1
    for side in sorted(tot):
        print("%s: клеток %d, со столбцом вне H1 (не построен / не покрыт) %d (%.1f %%)" % (side, tot[side], fam.get((side, "любое"), 0), 100.0 * fam.get((side, "любое"), 0) / tot[side]))
        print("   по семействам (клеток с хотя бы одним):", {k[1]: v for k, v in sorted(fam.items(), key=lambda t: -t[1]) if k[0] == side and "+" not in k[1] and k[1] != "любое"})
        print("   по сочетаниям:", {k[1]: v for k, v in sorted(fam.items(), key=lambda t: -t[1]) if k[0] == side and "+" in k[1]})
        print("   по столбцам:", dict(sorted(((x, n) for (s_, x), n in per.items() if s_ == side), key=lambda t: -t[1])[:40]))
    return 0


def bad_columns(cov_path):
    """Столбцы осей, не построенные на H1 (`cov.json` из `tk700_cov.py`), и sha файла. Не покрытые (доля < 0,7 в каком-то месяце: sac10) классом не отсекаются — охрана охвата по окнам (`covered` → `mass_skip`), как в поиске TK-674."""
    raw = open(cov_path, "rb").read()
    return set(json.loads(raw)["not_built"]), hashlib.sha256(raw).hexdigest()[:12]


def cell_class(feats, bad):
    """Класс клетки по столбцам её фильтра (`need_cols`): X — cc_* / clu_* / bd_* (исключены); C — hist ok10_*; B — столбцы TK-654; H — история уровня (`--allow-diff`); P — иной столбец не построен на H1 (`bad`); A — всё построено."""
    cols = {x for f in feats for x in need_cols(f)}
    if any(x.startswith(("cc_", "clu_", "bd_")) for x in cols):
        return "X"
    if any(x.startswith("ok10_") for x in cols):
        return "C"
    if cols & COLS_B:
        return "B"
    if cols & COLS_H:
        return "H"
    if cols & bad:
        return "P"
    return "A"


def keep_classes(cells, classes, bad):
    """Клетки нужных классов (`classes` — множество букв; пусто = все)."""
    return [c for c in cells if not classes or cell_class(c["feats"], bad) in classes]


def cmd_classes(a):
    bad, sha = bad_columns(a.cov)
    db = connect(a.db)
    tot = {}
    for side, feats in db.execute("SELECT side, feats FROM cell WHERE cellset=?", (a.cellset,)):
        k = (side, cell_class(feats.split(",") if feats else [], bad))
        tot[k] = tot.get(k, 0) + 1
    print("классы клеток %s (cov.json sha %s; не построено / не покрыто на H1: %d столбцов)" % (a.cellset, sha, len(bad)))
    for side in sorted({k[0] for k in tot}):
        n = sum(v for k, v in tot.items() if k[0] == side)
        print("  %-5s всего %d: %s" % (side, n, ", ".join("%s %d (%.1f %%)" % (c, tot.get((side, c), 0), 100.0 * tot.get((side, c), 0) / max(1, n)) for c in "ABCHXP")))
    return 0


def claim_run(m, run_id, info):
    """Замок запуска: ключ — sha256 «run_id#попытка» (повторный запуск того же run-id — новая попытка, дубль одновременной попытки отказан)."""
    for att in range(1, 100):
        key = hashlib.sha256(("%s#%d" % (run_id, att)).encode()).hexdigest()
        if not os.path.isdir(lk.lock_dir(m["lock"], key)):
            lj_path, nonce = lk.claim(m["lock"], key, info=dict(run_id=run_id, attempt=att, **info))
            return key, lj_path, nonce, att
        lj = lk.read_lock(m["lock"], key)
        if lj is None or lj["state"] in ("claimed", "started"):
            try:
                os.kill(int(lj["pid"]), 0) if lj else None
            except (OSError, ValueError, TypeError):
                continue                                                    # процесс умер — следующая попытка
            raise SystemExit("run-id %s: попытка %d ещё идёт (pid %s)" % (run_id, att, lj and lj.get("pid")))
    raise SystemExit("run-id %s: слишком много попыток" % run_id)


def cmd_run(a):
    m = ho.modes(a.dry)                                                     # до tk638_post: он тянет tk604_stats, а окна H1 подменяются переменной среды при первом импорте
    import tk638_post as p8
    db = connect(a.db)
    st = m["st"]
    wins = a.wins.split(",") if a.wins else m["wins"]
    sides = a.sides.split(",")
    md5s = {s: hashlib.md5(open(BINS[s], "rb").read()).hexdigest() for s in sides}
    md5 = json.dumps(md5s)
    hold = os.environ.get("TK700_HOLD_FILE", "")
    eng = os.environ.get("TK700_ENGINE", "")                                # подмена исполняемого файла движка (LTO TK-706, результаты клеток побайтно равны): файл и md5 заданы вместе; сверка клеток с БД — по BINS
    eng_md5 = hashlib.md5(open(eng, "rb").read()).hexdigest() if eng else ""
    if eng and eng_md5 != os.environ.get("TK700_ENGINE_MD5", ""):
        raise SystemExit("движок %s: md5 %s ≠ TK700_ENGINE_MD5" % (eng, eng_md5))
    classes = set(a.cls.split(",")) if a.cls else set()
    unbuilt, cov_sha = bad_columns(a.cov) if classes else (set(), "")
    unguarded = {x for x in a.unguarded.split(",") if x}
    params = dict(vmax=a.vmax, sigmax=a.sigmax, sigs=a.sigs, every=a.every, part=a.part, unguarded=sorted(unguarded), seed=a.seed, threads=a.threads, cap_gb=a.cap_gb, sides=sides, D0=st.D0.isoformat(), max_batches=a.max_batches, only_skipped=a.only_skipped,
                  classes=sorted(classes), excluded=EXCLUDED if classes else "", cov_sha=cov_sha, engine=eng_md5)
    row = db.execute("SELECT cellset, dry FROM mass_run WHERE run_id=?", (a.run_id,)).fetchone()
    if row and (row[0] != a.cellset or row[1] != int(a.dry)):
        raise SystemExit("run-id %s уже занят набором %s (dry=%s)" % (a.run_id, row[0], row[1]))
    if not row:
        db.execute("INSERT OR IGNORE INTO mass_run VALUES(?,?,?,?,?,?,?,?)", (a.run_id, a.cellset, int(a.dry), ",".join(wins), md5, json.dumps(params), now(), "running"))
        db.commit()
    elif eng:                                                               # запись о подмене движка в params прежнего запуска (какая партия чем считана — по времени mass_batch.done против `engine_swaps.at`)
        pj = json.loads(db.execute("SELECT params FROM mass_run WHERE run_id=?", (a.run_id,)).fetchone()[0])
        if eng_md5 not in [x["md5"] for x in pj.get("engine_swaps", [])]:
            pj.setdefault("engine_swaps", []).append(dict(at=now(), md5=eng_md5, path=eng))
            db.execute("UPDATE mass_run SET params=? WHERE run_id=?", (json.dumps(pj), a.run_id))
            db.commit()
    if eng:
        print("движок: %s md5 %s" % (eng, eng_md5), flush=True)
    part_k, part_n = (int(x) for x in a.part.split("/"))
    if not (part_n >= 1 and 0 <= part_k < part_n):
        raise SystemExit("--part K/N: 0 ≤ K < N")
    key, lj_path, nonce, att = claim_run(m, a.run_id if part_n == 1 else "%s@p%d/%d" % (a.run_id, part_k, part_n), dict(cellset=a.cellset, dry=a.dry, wins=wins, part=a.part))
    token = lk.token_env(lj_path, nonce)
    lk.set_state(m["lock"], key, nonce, "started")
    done_b = {(r[0], r[1], r[2]) for r in db.execute("SELECT win, side, bkey FROM mass_batch WHERE run_id=? AND rc=0", (a.run_id,))}
    nb = 0
    t_run = time.time()
    try:
        for side in sides:
            binary = eng or BINS[side]
            cells = [c for c in keep_classes(load_cells(db, a.cellset, side), classes, unbuilt) if c["cid"] % a.every == 0]
            print("%s: клеток к счёту %d (классы %s%s)" % (side, len(cells), ",".join(sorted(classes)) or "все", ", каждая %d-я по cid" % a.every if a.every > 1 else ""), flush=True)
            if a.sigs:
                cells = pick_sigs(cells, a.sigs, a.seed)
            other = db.execute("SELECT count(*) FROM cell WHERE cellset=? AND side=? AND bin!=?", (a.cellset, side, md5s[side])).fetchone()[0]
            if other:
                raise SystemExit("набор %s: %d клеток стороны %s не бинаря %s" % (a.cellset, other, side, md5s[side]))
            tabs = tabs_for(m, side, classes)
            for w in st.TRADE_WINDOWS:
                if w.name not in wins:
                    continue
                tab, cache = tabs[w.name], {}
                redo = {r[0] for r in db.execute("SELECT cid FROM mass_skip WHERE run_id=? AND win=?", (a.only_skipped, w.name))} if a.only_skipped else None
                if not len(tab["sym"]):
                    db.executemany("INSERT OR REPLACE INTO mass_skip VALUES(?,?,?,?)", [(a.run_id, c["cid"], w.name, "нет строк базы") for c in cells])
                    db.commit()
                    continue
                runnable, skips, lowcov = [], [], {}
                for c in cells:
                    if redo is not None and c["cid"] not in redo:
                        continue                                            # только пары, пропущенные в запуске R0 (столбцы построены позже)
                    bad = [(f, covered(tab, cache, f)) for f in c["feats"] if covered(tab, cache, f) < COVER_MIN]
                    if bad and side in unguarded:
                        lowcov[c["cid"]] = "низкое покрытие %s" % ",".join("%s %.2f" % x for x in bad)             # охрана выключена приказом координатора: пустое проходит, как в строках БД Jul–Nov
                        bad = []
                    (skips.append((a.run_id, c["cid"], w.name, "охват %s" % ",".join("%s %.2f" % x for x in bad))) if bad else runnable.append(c))
                db.executemany("INSERT OR REPLACE INTO mass_skip VALUES(?,?,?,?)", skips)
                db.commit()
                for bi, cur in enumerate(make_batches(runnable, a.vmax, a.sigmax)):
                    bk = bkey_of(cur)
                    if bi % part_n != part_k or (w.name, side, bk) in done_b:
                        continue
                    if a.max_batches and nb >= a.max_batches:
                        raise StopIteration
                    while hold and os.path.exists(hold):                    # окно «srv целиком» (TK706_HOLD): новая партия не стартует, пока файл есть; идущая партия дорабатывает
                        time.sleep(20)
                    nb += 1
                    out = os.path.join(OUT_ROOT, a.run_id, "%s-%s-%s" % (w.name, side, bk))
                    shutil.rmtree(out, ignore_errors=True)
                    cols = ho.filter_cols([dict(filter=c["filter"]) for c in cur])
                    ftab = os.path.join(out + ".ftab.csv")
                    kept = ho.write_ftab(ftab, tab, cols, [dict(filter=c["filter"]) for c in cur]) if cols else {}
                    variants = [("k%d" % c["cid"], c["line"] + ((" fa=%s fo=lo fv=%g" % (cols[ho.sem_of(c["filter"])], ho.FV_PASS)) if c["filter"] else "")) for c in cur]
                    r0, t0 = resource.getrusage(resource.RUSAGE_CHILDREN), time.time()
                    rc = 0
                    try:
                        ho.engine(m, w, side, variants, out, ftab if cols else None, token, binary, a.threads, a.cap_gb, out + ".log")
                    except Exception as e:                                  # noqa: BLE001
                        rc = 1
                        print("движок %s %s %s: %r" % (w.name, side, bk, e), flush=True)
                    r1, t1 = resource.getrusage(resource.RUSAGE_CHILDREN), time.time()
                    cpu = (r1.ru_utime + r1.ru_stime) - (r0.ru_utime + r0.ru_stime)
                    t2 = time.time()
                    cw, days = [], []
                    if rc == 0:
                        for c in cur:
                            n, usd, errs, notes, ds, pn, ct = cell_window(st, p8, w, side, os.path.join(out, "k%d" % c["cid"]), bool(c["filter"]), strict_key="B" in classes and cell_class(c["feats"], unbuilt) == "B")
                            cw.append((a.run_id, c["cid"], w.name, n, usd, ";".join(errs), ";".join(notes + ([lowcov[c["cid"]]] if c["cid"] in lowcov else []))))
                            days += [(a.run_id, c["cid"], d, p, k) for d, p, k in zip(ds, pn, ct)]
                    db.executemany("INSERT OR REPLACE INTO mass_cw VALUES(?,?,?,?,?,?,?)", cw)
                    db.executemany("INSERT OR REPLACE INTO mass_day VALUES(?,?,?,?,?)", [(r, c, d, p, k) for r, c, d, p, k in days])
                    db.execute("INSERT OR REPLACE INTO mass_batch VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (a.run_id, w.name, side, bk, len(cur), len({c["sig"] for c in cur}), len(cols), sum(kept.values()) if kept else 0,
                                                                                                         t1 - t0, cpu, time.time() - t2, rc, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0, now()))
                    db.commit()
                    if a.keep_trades and rc == 0:                           # TK-718: сделки клеток партии (T5 / T6) — файлы `<keep>/<run>/<окно>/<cid>-trades.csv`, t0_ms окна — в `<keep>/<run>/t0.json`
                        kd = os.path.join(a.keep_trades, a.run_id, w.name)
                        os.makedirs(kd, exist_ok=True)
                        for c in cur:
                            shutil.copyfile(os.path.join(out, "k%d" % c["cid"], p8.ENG[ho.SIDE_LETTER[side]] + "-trades.csv"), os.path.join(kd, "%d-trades.csv" % c["cid"]))
                            vsf = os.path.join(out, "k%d" % c["cid"], p8.ENG[ho.SIDE_LETTER[side]] + "-vstop.csv")      # TK-736: расстояние стопа от волатильности по сделкам (только при vf > 0)
                            if os.path.exists(vsf):
                                shutil.copyfile(vsf, os.path.join(kd, "%d-vstop.csv" % c["cid"]))
                        t0p = os.path.join(a.keep_trades, a.run_id, "t0.json")
                        t0d = json.load(open(t0p, encoding="utf-8")) if os.path.exists(t0p) else {}
                        t0d[w.name] = int(w.t0_ms)
                        json.dump(t0d, open(t0p, "w", encoding="utf-8"))
                    shutil.rmtree(out, ignore_errors=True)
                    for p in (ftab, out + ".log"):
                        if rc == 0 and os.path.exists(p):
                            os.remove(p)
                    print("%s %s %s: вариантов %d, ключей %d, фильтров %d → движок %.0f с (ЦП %.0f с), разбор %.0f с, rc %d" % (
                        w.name, side, bk, len(cur), len({c['sig'] for c in cur}), len(cols), t1 - t0, cpu, time.time() - t2, rc), flush=True)
        lk.set_state(m["lock"], key, nonce, "done", extra=dict(seconds=round(time.time() - t_run, 1), batches=nb))
        if part_n == 1:                                                     # части запуска (--part) оставляют state=running; закрывает прогон без части (все партии готовы — пропуск)
            db.execute("UPDATE mass_run SET state='done' WHERE run_id=?", (a.run_id,))
    except StopIteration:
        lk.set_state(m["lock"], key, nonce, "done", extra=dict(seconds=round(time.time() - t_run, 1), batches=nb, stopped="max-batches"))
    except BaseException as e:
        try:
            lk.set_state(m["lock"], key, nonce, "failed", extra=dict(error=repr(e)[:300]))
        finally:
            db.execute("UPDATE mass_run SET state='failed' WHERE run_id=?", (a.run_id,))
            db.commit()
            raise
    db.commit()
    print("запуск %s: партий %d, %.0f с" % (a.run_id, nb, time.time() - t_run))
    return 0


def cmd_check(a):
    """dry: числа движка против БД (сделки, Σ $; посуточный ряд — если набор взят с --with-daily) по клеткам, посчитанным во всех окнах."""
    import numpy as np
    db = connect(a.db)
    r = db.execute("SELECT cellset, wins, params FROM mass_run WHERE run_id=?", (a.run_id,)).fetchone()
    if not r:
        raise SystemExit("нет запуска %s" % a.run_id)
    cellset, wins = r[0], r[1].split(",")
    d0 = json.loads(r[2])["D0"]
    n_ok = n_bad = n_part = n_err = 0
    bad = []
    for cid, side, ckey, ref_n, ref_usd, ref_daily in db.execute("SELECT cid, side, ckey, ref_n, ref_usd, ref_daily FROM cell WHERE cellset=? AND cid IN (SELECT DISTINCT cid FROM mass_cw WHERE run_id=?)", (cellset, a.run_id)):
        cw = db.execute("SELECT win, n, usd, errs FROM mass_cw WHERE run_id=? AND cid=?", (a.run_id, cid)).fetchall()
        sk = db.execute("SELECT win FROM mass_skip WHERE run_id=? AND cid=?", (a.run_id, cid)).fetchall()
        if len({x[0] for x in cw} | {x[0] for x in sk}) < len(wins) or sk:
            n_part += 1
            continue
        n_err += sum(1 for x in cw if x[3])
        n, usd = sum(x[1] for x in cw), sum(x[2] for x in cw)
        same = n == ref_n and abs(usd - ref_usd) <= ho.TOL_USD * max(1.0, abs(ref_usd))
        dd = ""
        if ref_daily:
            ref = json.loads(ref_daily)
            got = np.zeros(len(ref))
            for day, pnl in db.execute("SELECT day, pnl FROM mass_day WHERE run_id=? AND cid=?", (a.run_id, cid)):
                if 0 <= day < len(ref):
                    got[day] += pnl
            same_d = bool(np.allclose(np.round(got, 6), np.array(ref), atol=2e-6, rtol=0))
            same = same and same_d
            dd = " ряд %s" % ("=" if same_d else "≠")
        (n_ok := n_ok + 1) if same else (n_bad := n_bad + 1)
        if not same:
            bad.append("%s %s: сделки %s/%s, Σ $ %+.6f/%+.6f%s | %s" % (cid, side, n, ref_n, usd, ref_usd, dd, ckey[-70:]))
    print("сверка %s: равно %d, разно %d, не во всех окнах (охват / нет строк) %d; ошибок охраны в окнах %d" % (a.run_id, n_ok, n_bad, n_part, n_err))
    for b in bad[:20]:
        print("  РАЗНО", b)
    return 0 if n_bad == 0 and n_ok > 0 else 3


def cmd_timing(a):
    """Минуты движка на 10 000 клеток в месяц: ЦП и стена партий; модель ЦП = f0 + s·ключи + v·варианты (по партиям, МНК)."""
    import numpy as np
    db = connect(a.db)
    r = db.execute("SELECT wins, params, dry FROM mass_run WHERE run_id=?", (a.run_id,)).fetchone()
    params = json.loads(r[1])
    import tk700_win as w7
    days = {}
    m = ho.modes(bool(r[2]))
    for w in m["st"].TRADE_WINDOWS:
        days[w.name] = (w.hid - w.lod).days + 1
    rows = db.execute("SELECT win, side, n_var, n_sig, n_filt, engine_s, cpu_s, post_s, maxrss_mb FROM mass_batch WHERE run_id=? AND rc=0", (a.run_id,)).fetchall()
    if not rows:
        raise SystemExit("нет партий")
    tot = {}
    for win, side, nv, ns, nf, es, cs, ps, mem in rows:
        t = tot.setdefault(win, [0, 0.0, 0.0, 0.0])
        t[0] += nv
        t[1] += es
        t[2] += cs
        t[3] += ps
    print("партий %d; по окнам (дни окна, клеток, движок с стена, ЦП с, разбор с, движок мин на 10 000 клеток-окон / на месяц 30 дн, ЦП мин на 10 000 / месяц):" % len(rows))
    sum_v = sum_e = sum_c = sum_p = sum_dm = 0.0
    for win, (nv, es, cs, ps) in tot.items():
        dm = days[win] / 30.0
        print("  %-4s %3d дн %6d кл  движок %7.0f с  ЦП %7.0f с  разбор %6.0f с  → стена %6.1f мин / %6.1f мин-мес   ЦП %6.1f / %6.1f" % (
            win, days[win], nv, es, cs, ps, es / nv * 10000 / 60, es / nv * 10000 / 60 / dm, cs / nv * 10000 / 60, cs / nv * 10000 / 60 / dm))
        sum_v += nv
        sum_e += es
        sum_c += cs
        sum_p += ps
        sum_dm += nv * dm
    print("всего: клеток-окон %d; стена движка %.1f мин / 10 000 клеток-окон, на месяц %.1f мин; ЦП %.1f мин на 10 000 клеток-окон, на месяц %.1f; разбор %.1f мин на 10 000 клеток-окон" % (
        sum_v, sum_e / sum_v * 10000 / 60, sum_e / sum_dm * 10000 / 60, sum_c / sum_v * 10000 / 60, sum_c / sum_dm * 10000 / 60, sum_p / sum_v * 10000 / 60))
    X = np.array([[1.0, ns, nv] for _w, _s, nv, ns, _nf, _e, _c, _p, _m in rows])
    for nm, col in (("стена", 5), ("ЦП", 6)):
        y = np.array([r_[col] for r_ in rows])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        print("  модель %s: f0 %.1f с + %.2f с на ключ сигналов + %.3f с на вариант (МНК по %d партиям)" % (nm, coef[0], coef[1], coef[2], len(rows)))
    print("  память движка (макс. RSS ребёнка): %.0f МБ" % max(r_[8] for r_ in rows))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("plan", "run", "check", "timing", "need", "classes"):
        s = sp.add_parser(n)
        s.add_argument("--db", default=MASS_DB)
        if n in ("need", "classes", "plan", "run"):
            s.add_argument("--cov", default="/data/p42-tk700/cov/cov.json")
        if n in ("need", "classes"):
            s.add_argument("--cellset", required=True)
        if n in ("plan", "run"):
            s.add_argument("--cellset", required=True)
            s.add_argument("--dry", action="store_true")
            s.add_argument("--wins", default="")
            s.add_argument("--sides", default="long,short")
            s.add_argument("--class", dest="cls", default="", help="только клетки этих классов (A,B,C,H,X,P через запятую; см. `cell_class`); пусто — все")
            s.add_argument("--vmax", type=int, default=VMAX)
            s.add_argument("--sigmax", type=int, default=SIGMAX)
            s.add_argument("--sigs", type=int, default=0)
            s.add_argument("--every", type=int, default=1, help="только клетки с cid кратным N (выборка для сухого прогона, все ключи сигналов остаются)")
            s.add_argument("--seed", type=int, default=1)
        if n in ("run", "check", "timing"):
            s.add_argument("--run-id", required=True)
        if n == "run":
            s.add_argument("--threads", type=int, default=2)
            s.add_argument("--cap-gb", type=int, default=6)
            s.add_argument("--max-batches", type=int, default=0)
            s.add_argument("--unguarded", default="", help="стороны (long,short), для которых охрана охвата < 0,7 выключена: клетка считается, в mass_cw.note — «низкое покрытие <признак доля>» (приказ координатора 24.11 для short-B)")
            s.add_argument("--part", default="0/1", help="K/N: считать партии с номером ≡ K (mod N) в списке партий окна × стороны; части одного run-id идут параллельными процессами, закрыть run-id — запуском без --part")
            s.add_argument("--keep-trades", default="", help="каталог: сохранить файлы сделок клеток каждой партии (TK-718, T5 / T6; без ключа партия удаляется после разбора)")
            s.add_argument("--only-skipped", default="", help="run-id прежнего запуска: считать только пары клетка × окно из его mass_skip (после достройки столбцов)")
    a = ap.parse_args(argv)
    return {"plan": cmd_plan, "run": cmd_run, "check": cmd_check, "timing": cmd_timing, "need": cmd_need, "classes": cmd_classes}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
