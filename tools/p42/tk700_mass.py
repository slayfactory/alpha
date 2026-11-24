#!/usr/bin/env python3
"""TK-700 (В-183): массовый прогон клеток c539 движком по месяцам январь–июнь 2026 (посуточный P&L каждой клетки → TK-692). Замок — только на запуск (run-id), одного чтения нет.

  python3 tk700_mass.py plan  --cellset all1 [--dry] [--wins m01,…] [--sides long,short]         # клетки, охват по окнам, партии, без движка
  python3 tk700_mass.py run   --run-id R --cellset all1 [--dry] [--wins …] [--sides …] [--vmax 400 --sigmax 26] [--sigs N --seed S] [--max-batches K] [--threads 2] [--cap-gb 6]
  python3 tk700_mass.py check --run-id R                                                         # dry: сверка с числами БД (сделки, Σ $, посуточный ряд) по клеткам набора
  python3 tk700_mass.py timing --run-id R [--cells 10000]                                          # время движка по партиям → минуты на 10 000 клеток в месяц

БД результатов `/data/p42-tk700/mass/mass.sqlite` (ключ — run_id): `mass_run`, `mass_batch` (время, ЦП, память партии), `mass_cw` (клетка × окно: сделки, Σ $, ошибки охраны),
`mass_day` (клетка × сутки: P&L = gross − fees по дате входа, число сделок; сутки — от `D0` окна из `mass_run.params`), `mass_skip` (пара клетка × окно не считалась: охват ниже 0,7 / нет строк базы).
Партии — как TK-674: ≤ vmax вариантов и ≤ sigmax ключей прохода сигналов, клетки отсортированы по (sig, cid); готовая партия (run_id, окно, сторона, bkey) при повторном запуске пропускается.
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
SCHEMA = """
CREATE TABLE IF NOT EXISTS mass_run(run_id TEXT PRIMARY KEY, cellset TEXT, dry INTEGER, wins TEXT, bin_md5 TEXT, params TEXT, created TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS mass_batch(run_id TEXT, win TEXT, side TEXT, bkey TEXT, n_var INTEGER, n_sig INTEGER, n_filt INTEGER, ftab_rows INTEGER, engine_s REAL, cpu_s REAL, post_s REAL,
                                      rc INTEGER, maxrss_mb REAL, utc TEXT, PRIMARY KEY(run_id, win, side, bkey));
CREATE TABLE IF NOT EXISTS mass_cw(run_id TEXT, cid INTEGER, win TEXT, n INTEGER, usd REAL, errs TEXT, PRIMARY KEY(run_id, cid, win)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS mass_day(run_id TEXT, cid INTEGER, day INTEGER, pnl REAL, n INTEGER, PRIMARY KEY(run_id, cid, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS mass_skip(run_id TEXT, cid INTEGER, win TEXT, why TEXT, PRIMARY KEY(run_id, cid, win)) WITHOUT ROWID;
"""


def connect(path=MASS_DB):
    import tk700_cells as cl
    c = cl.connect(path)
    c.executescript(SCHEMA)
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


def cell_window(st, p8, w, side, vdir, filtered):
    """(n, Σ $, ошибки, сутки, P&L по суткам, сделок по суткам) одной клетки в одном окне: сделки по дате входа в диапазоне окна (как `tk604_stats.splice`)."""
    import numpy as np
    t = st.read_trades_csv(os.path.join(vdir, p8.ENG[side] + "-trades.csv"))
    dl = st.read_daily_csv(os.path.join(vdir, p8.ENG[side] + "-daily.csv"))
    errs = []
    if len(t) and np.abs(t.net - (t.gross - t.fees + t.funding)).max() > 2e-6:
        errs.append("net≠gross−fees+funding")
    d = st.abs_day(w, t.entry_ms)
    lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
    sel = np.nonzero((d >= lo_i) & (d <= hi_i))[0]
    first, last = (w.lod - w.t0d).days - st.GRID_START_DAY, (w.hid - w.t0d).days - st.GRID_START_DAY
    ssum = sum(dl.get(i, 0.0) for i in range(first, last + 1))
    nsum = float(t.net[sel].sum())
    if abs(ssum - nsum) > max(1e-4, 2e-6 * len(sel)):
        errs.append("G7 Σdaily %+.6f≠Σnet %+.6f" % (ssum, nsum))
    if len(t) and int(t.exit_ms.max()) + w.t0_ms >= st.LAST_MS:
        errs.append("G-tape")
    if filtered:
        meta = json.load(open(os.path.join(vdir, "meta.json"), encoding="utf-8"))[side]
        import tk604_run as r4
        fc = r4.filter_counters(meta)
        if fc is None or fc["no_key"]:
            errs.append("G-key no_key %s" % (fc,))
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
    return int(len(sel)), float(val.sum()), errs, days, pnl, cnt


def cmd_plan(a):
    db = connect(a.db)
    m = ho.modes(a.dry)
    st = m["st"]
    wins = a.wins.split(",") if a.wins else m["wins"]
    for side in a.sides.split(","):
        cells = load_cells(db, a.cellset, side)
        if a.sigs:
            cells = pick_sigs(cells, a.sigs, a.seed)
        tabs = ho.load_tabs(m, side)
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
    import tk638_post as p8
    db = connect(a.db)
    m = ho.modes(a.dry)
    st = m["st"]
    wins = a.wins.split(",") if a.wins else m["wins"]
    sides = a.sides.split(",")
    md5s = {s: hashlib.md5(open(BINS[s], "rb").read()).hexdigest() for s in sides}
    md5 = json.dumps(md5s)
    params = dict(vmax=a.vmax, sigmax=a.sigmax, sigs=a.sigs, seed=a.seed, threads=a.threads, cap_gb=a.cap_gb, sides=sides, D0=st.D0.isoformat(), max_batches=a.max_batches)
    row = db.execute("SELECT cellset, dry FROM mass_run WHERE run_id=?", (a.run_id,)).fetchone()
    if row and (row[0] != a.cellset or row[1] != int(a.dry)):
        raise SystemExit("run-id %s уже занят набором %s (dry=%s)" % (a.run_id, row[0], row[1]))
    if not row:
        db.execute("INSERT INTO mass_run VALUES(?,?,?,?,?,?,?,?)", (a.run_id, a.cellset, int(a.dry), ",".join(wins), md5, json.dumps(params), now(), "running"))
        db.commit()
    key, lj_path, nonce, att = claim_run(m, a.run_id, dict(cellset=a.cellset, dry=a.dry, wins=wins))
    token = lk.token_env(lj_path, nonce)
    lk.set_state(m["lock"], key, nonce, "started")
    done_b = {(r[0], r[1], r[2]) for r in db.execute("SELECT win, side, bkey FROM mass_batch WHERE run_id=? AND rc=0", (a.run_id,))}
    nb = 0
    t_run = time.time()
    try:
        for side in sides:
            binary = BINS[side]
            cells = load_cells(db, a.cellset, side)
            if a.sigs:
                cells = pick_sigs(cells, a.sigs, a.seed)
            other = db.execute("SELECT count(*) FROM cell WHERE cellset=? AND side=? AND bin!=?", (a.cellset, side, md5s[side])).fetchone()[0]
            if other:
                raise SystemExit("набор %s: %d клеток стороны %s не бинаря %s" % (a.cellset, other, side, md5s[side]))
            tabs = ho.load_tabs(m, side)
            for w in st.TRADE_WINDOWS:
                if w.name not in wins:
                    continue
                tab, cache = tabs[w.name], {}
                if not len(tab["sym"]):
                    db.executemany("INSERT OR REPLACE INTO mass_skip VALUES(?,?,?,?)", [(a.run_id, c["cid"], w.name, "нет строк базы") for c in cells])
                    db.commit()
                    continue
                runnable, skips = [], []
                for c in cells:
                    bad = [(f, covered(tab, cache, f)) for f in c["feats"] if covered(tab, cache, f) < COVER_MIN]
                    (skips.append((a.run_id, c["cid"], w.name, "охват %s" % ",".join("%s %.2f" % x for x in bad))) if bad else runnable.append(c))
                db.executemany("INSERT OR REPLACE INTO mass_skip VALUES(?,?,?,?)", skips)
                db.commit()
                for bi, cur in enumerate(make_batches(runnable, a.vmax, a.sigmax)):
                    bk = bkey_of(cur)
                    if (w.name, side, bk) in done_b:
                        continue
                    if a.max_batches and nb >= a.max_batches:
                        raise StopIteration
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
                            n, usd, errs, ds, pn, ct = cell_window(st, p8, w, side, os.path.join(out, "k%d" % c["cid"]), bool(c["filter"]))
                            cw.append((a.run_id, c["cid"], w.name, n, usd, ";".join(errs)))
                            days += [(a.run_id, c["cid"], d, p, k) for d, p, k in zip(ds, pn, ct)]
                    db.executemany("INSERT OR REPLACE INTO mass_cw VALUES(?,?,?,?,?,?)", cw)
                    db.executemany("INSERT OR REPLACE INTO mass_day VALUES(?,?,?,?,?)", [(r, c, d, p, k) for r, c, d, p, k in days])
                    db.execute("INSERT OR REPLACE INTO mass_batch VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (a.run_id, w.name, side, bk, len(cur), len({c["sig"] for c in cur}), len(cols), sum(kept.values()) if kept else 0,
                                                                                                         t1 - t0, cpu, time.time() - t2, rc, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0, now()))
                    db.commit()
                    shutil.rmtree(out, ignore_errors=True)
                    for p in (ftab, out + ".log"):
                        if rc == 0 and os.path.exists(p):
                            os.remove(p)
                    print("%s %s %s: вариантов %d, ключей %d, фильтров %d → движок %.0f с (ЦП %.0f с), разбор %.0f с, rc %d" % (
                        w.name, side, bk, len(cur), len({c['sig'] for c in cur}), len(cols), t1 - t0, cpu, time.time() - t2, rc), flush=True)
        lk.set_state(m["lock"], key, nonce, "done", extra=dict(seconds=round(time.time() - t_run, 1), batches=nb))
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
    for n in ("plan", "run", "check", "timing"):
        s = sp.add_parser(n)
        s.add_argument("--db", default=MASS_DB)
        if n in ("plan", "run"):
            s.add_argument("--cellset", required=True)
            s.add_argument("--dry", action="store_true")
            s.add_argument("--wins", default="")
            s.add_argument("--sides", default="long,short")
            s.add_argument("--vmax", type=int, default=VMAX)
            s.add_argument("--sigmax", type=int, default=SIGMAX)
            s.add_argument("--sigs", type=int, default=0)
            s.add_argument("--seed", type=int, default=1)
        if n in ("run", "check", "timing"):
            s.add_argument("--run-id", required=True)
        if n == "run":
            s.add_argument("--threads", type=int, default=2)
            s.add_argument("--cap-gb", type=int, default=6)
            s.add_argument("--max-batches", type=int, default=0)
    a = ap.parse_args(argv)
    return {"plan": cmd_plan, "run": cmd_run, "check": cmd_check, "timing": cmd_timing}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
