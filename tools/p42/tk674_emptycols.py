#!/usr/bin/env python3
"""П-42 TK-674: пустые столбцы таблицы TK-671 в таблицах фильтров («нет строки — пусто проходит», tk680_singles.add_columns) — без движка и без P&L.
  srv:  TK674_ROOT=/data/p42-tk674c python3 tk674_emptycols.py [rows | cells | all]
rows  — строки кандидатов c539 в `ftab674x/<окно>-<сторона>.csv` (ключ sym + sig_ms; всего и в зачётном диапазоне окна) без строки таблицы TK-671 (join как в add_columns: meta_base_pass > 0,
        бит 1 раньше бита 2) и доля NaN по столбцам у строк, которые в таблице есть (только столбцы, лежащие в parquet напрямую);
cells — клетки шагов 2–5 (plan674bs / plan674bsx / level674bs3* / level674bs4*): сколько с фильтром по столбцу вне таблиц TK-654 / TK-638 (A: tpm60, cons60, zv6h, n24, sharp) —
        такой фильтр у строки без строки TK-671 проходит молча;
classes — уникальные клетки шагов 2–5 по классам фильтра (bd_* / cc_* / иные вне таблиц TK-654 / TK-638 / только они; правило критика 23.11, п. б–г);
read  <имена.json> — класс фильтра и сделки на строках без строки TK-671 у прочитанных строк (имена из `pair-map*.md`: tools/p42/tk674_emptycols.py names), п. а, д;
without <cid.json> — Σ $ и Шарп клеток с зачётными сделками на строках без строки TK-671 и без них (п. а);
trades <каталоги.json> — то же по каталогам сделок клеток."""
import csv
import glob
import json
import os
import sys
from collections import Counter

ROOT = os.environ.get("TK674_ROOT", "/data/p42-tk674c")
PARQ = "/data/p42-tk671/data/%s.parquet"
ENG = {"L": "long", "S": "short"}
WINS = ("wa", "wb", "wc")
AXIS_COLS = ("gap60_s", "since_born_h", "n60", "tfv10", "flat60", "pierce_max_bp", "touch_ep", "approach_dur_min", "btc_mv5", "cb_1h", "ce_1h", "bt_1h", "cc_15m", "cc_1h",
             "bd_imb02", "bd_thin5", "bd_thin30")           # оси-столбцы, лежащие в parquet TK-671 напрямую (big1800 / vpin1h — отношения, ok10_* — файл истории, sac* / accdv1 — FAST: здесь не мерятся)
A_NAMES = {"tpm60", "TPM60", "cons60", "CN60", "zv6h", "ZV6", "n24", "N24", "SHARP", "sharp", "mv5"}      # столбцы таблиц TK-654 / TK-638 (полные строки)


def axis_name(f):
    return f[0].split(".")[0].split("@")[0]


MISS = {}                                                 # {(сторона, окно): {(sym, sig_ms)}} — строки кандидатов без строки таблицы TK-671


def rows_report():
    import numpy as np
    import pandas as pd
    import tk604_stats as st
    import tk638_run as t8
    wins = {w.name: w for w in st.TRADE_WINDOWS}
    import pyarrow.parquet as pq
    out = {}
    for side in "LS":
        have = pq.ParquetFile(PARQ % ENG[side]).schema.names
        df = pd.read_parquet(PARQ % ENG[side])
        df = df[df["meta_base_pass"] > 0].copy()
        df["win"], df["sym"] = df["win"].astype(str), df["sym"].astype(str)
        df["_pri"] = (df["meta_base_pass"] != 1).astype(int)
        df = df.sort_values("_pri", kind="stable").drop_duplicates(["win", "sym", "meta_sig_ms"])
        for w in WINS:
            with open(os.path.join(ROOT, "ftab674x", "%s-%s.csv" % (w, side)), encoding="utf-8", newline="") as f:
                keys = [(r["sym"], int(r["sig_ms"])) for r in csv.DictReader(f)]
            key = pd.DataFrame(dict(sym=[k[0] for k in keys], meta_sig_ms=[k[1] for k in keys]))
            m = key.merge(df[df["win"] == w], on=["sym", "meta_sig_ms"], how="left", validate="one_to_one")
            miss = m["meta_base_pass"].isna()
            MISS[(side, w)] = {(k[0], k[1]) for k, z in zip(keys, miss) if z}
            rng = t8.in_scored_range(wins[w], np.asarray([k[1] for k in keys], dtype=np.int64))      # зачётный диапазон окна по суткам сигнала (строки таблицы шире)
            sc = m[rng & ~miss.to_numpy()]
            nan = {c: int(sc[c].isna().sum()) for c in AXIS_COLS if c in m.columns}
            top = sorted(((c, n) for c, n in nan.items() if n), key=lambda x: -x[1])
            out["%s-%s" % (w, side)] = dict(rows=len(m), miss=int(miss.sum()), matched=int((~miss).sum()), rows_scored=int(rng.sum()), miss_scored=int((miss.to_numpy() & rng).sum()),
                                           matched_scored=len(sc), nan_scored=top)
    return out


def cell_files():
    """{метка: файл в res/} — файлы клеток шагов 2–5 (план / уровень / партии остатка шорта)."""
    files = {"шаг 2 L": "plan674bs-L.json", "шаг 2 S": "plan674bs-S.json", "шаг 3 L": "plan674bsx-L.json", "шаг 3 S": "plan674bsx-S.json",
             "шаг 4 L (объединение)": "level674bs3u-L.json", "шаг 5 L (четвёрки)": "level674bs4u-L.json"}
    for t in ["level674bs3-S.json"] + ["level674bs3r%02d-S.json" % i for i in range(2, 14)]:
        if os.path.exists(os.path.join(ROOT, "res", t)):
            files["шаг 4 S: " + (t[len("level674bs3"):-len("-S.json")] or "основа")] = t
    return files


def cells_report():
    files = cell_files()
    out, uniq = {}, {"L": {}, "S": {}}
    for k, fn in files.items():
        d = json.load(open(os.path.join(ROOT, "res", fn), encoding="utf-8"))
        cells = d["cells"]
        it = list(cells.values()) if isinstance(cells, dict) else list(cells)
        bad = {(x[0] if isinstance(x, (list, tuple)) else x) for x in (d.get("bad") or [])} if isinstance(d.get("bad"), list) else set()
        n = nb = na = n0 = 0
        names = Counter()
        for c in it:
            if c["cid"] in bad:
                continue
            n += 1
            if not c.get("filt"):
                n0 += 1
                continue
            ax = {axis_name(f) for f in c["filt"]}
            if ax - A_NAMES:
                uniq["L" if fn.endswith("-L.json") else "S"][c["cid"]] = sorted(ax - A_NAMES)
                nb += 1
                for a in ax - A_NAMES:
                    names[a] += 1
            else:
                na += 1
        out[k] = dict(file=fn, cells=n, no_filter=n0, only_A=na, with_B=nb, bad=len(bad), B_names=dict(names.most_common(8)))
    cnt = {sd: Counter() for sd in uniq}
    for sd, dct in uniq.items():
        for cid, names in dct.items():
            for a in names:
                cnt[sd][a] += 1
    out["уникальные клетки с фильтром вне таблиц TK-654 / TK-638"] = {sd: dict(cells=len(dct), по_столбцам=dict(cnt[sd].most_common())) for sd, dct in uniq.items()}
    return out


def filt_class(filt):
    """Класс фильтра клетки (правило критика 23.11, п. б–в): bd — есть ось bd_* (недействительна по счётчику); cc — есть cc_*, bd_* нет (отложена); B — иные оси вне таблиц
    TK-654 / TK-638; A — только они; 0 — без фильтра."""
    if not filt:
        return "0"
    ax = {axis_name(f) for f in filt}
    if any(a.startswith("bd_") for a in ax):
        return "bd"
    if any(a.startswith("cc_") for a in ax):
        return "cc"
    return "B" if ax - A_NAMES else "A"


def classes_report():
    """Уникальные клетки шагов 2–5 по классам фильтра (`filt_class`), по сторонам и по файлам; клетки из списка bad файла не считаются (как в cells_report)."""
    out, uniq = {}, {"L": {}, "S": {}}
    for k, fn in cell_files().items():
        d = json.load(open(os.path.join(ROOT, "res", fn), encoding="utf-8"))
        cells = d["cells"]
        it = list(cells.values()) if isinstance(cells, dict) else list(cells)
        bad = {(x[0] if isinstance(x, (list, tuple)) else x) for x in (d.get("bad") or [])} if isinstance(d.get("bad"), list) else set()
        side = "L" if fn.endswith("-L.json") else "S"
        cnt = Counter()
        for c in it:
            if c["cid"] in bad:
                continue
            cl = filt_class(c.get("filt"))
            cnt[cl] += 1
            uniq[side][c["cid"]] = cl
        out[k] = dict(file=fn, **dict(cnt))
    out["уникальные cid"] = {sd: dict(all=len(dct), **dict(Counter(dct.values()))) for sd, dct in uniq.items()}
    return out, uniq


def find_dirs(side, want):
    """{окно: {cid: каталог клетки}} по `cells/<окно>-<сторона>/*.variants.txt` (хеш каталога партии — не по имени клетки); клетки запакованных партий — на место из архива (`tk674_pack`)."""
    import tk674_pack as pk
    pk.ROOT = ROOT
    where = {w: {} for w in WINS}
    for w in WINS:
        for vf in glob.glob(os.path.join(ROOT, "cells", "%s-%s" % (w, side), "*.variants.txt")):
            with open(vf, encoding="utf-8") as f:
                for line in f:
                    cid = line.split(" ", 1)[0]
                    if cid in want and cid not in where[w]:
                        where[w][cid] = pk.ensure_cell(os.path.join(vf[:-len(".variants.txt")], cid))
    return where


def without_report(cids_file):
    """Σ $ и Sharpe клетки с зачётными сделками со строк без строки TK-671 и без них (правило критика 23.11, п. а; ряд и Sharpe — как у `tk674_run._row_job`: `splice` → суточные суммы →
    `tk625_stats.sharpe_year`); `cids_file`: {сторона: [cid, …]}."""
    import numpy as np
    import tk604_stats as st
    import tk625_run as r5
    import tk625_stats as s5
    import tk638_post as p8
    if not MISS:
        rows_report()
    u = r5.Uni(os.environ.get("TK674_UNI", "c539"))
    out = {}
    for side, cids in json.load(open(cids_file, encoding="utf-8")).items():
        where = find_dirs(side, set(cids))
        for c in cids:
            if not all(c in where[w] for w in WINS):
                out[side + ":" + c] = dict(error="нет каталога")
                continue
            tables, _ = p8.read_tables({w: (where[w][c], side) for w in WINS})
            ser = st.splice(tables, u.coins(), daily=None)
            drop = []                                                             # маска в порядке `splice`: по окнам, внутри окна — зачётные строки таблицы сделок
            for w in st.TRADE_WINDOWS:
                t = tables[w.name]
                d = st.abs_day(w, t.entry_ms)
                sel = np.nonzero((d >= (w.lod - st.D0).days) & (d <= (w.hid - st.D0).days))[0]
                drop += [(t.sym[i], int(t.sig_ms[i])) in MISS[(side, w.name)] for i in sel]
            drop = np.array(drop, dtype=bool)
            assert len(drop) == len(ser.val)
            keep = ~drop
            sd_all = np.bincount(ser.day, weights=ser.val, minlength=st.NDAYS)
            sd_wo = np.bincount(ser.day[keep], weights=ser.val[keep], minlength=st.NDAYS)
            out[side + ":" + c] = dict(n=int(len(ser.val)), n_empty=int(drop.sum()), usd=float(ser.val.sum()), usd_without=float(ser.val[keep].sum()),
                                      sharpe=float(s5.sharpe_year(sd_all)), sharpe_without=float(s5.sharpe_year(sd_wo)))
    return out


def read_report(names_file):
    """Прочитанные строки (`names_file`: {сторона: {метка: [cid, …]}}, имена — из таблиц отчётов `pair-map*.md`): класс фильтра и, для классов B / cc / bd, сколько зачётных сделок
    (вход в сутках окна) со строк без строки TK-671 — по трём окнам (каталоги клеток — по `cells/<окно>-<сторона>/*.variants.txt`)."""
    import tk604_stats as st
    if not MISS:
        rows_report()
    _, uniq = classes_report()
    wins = {w.name: w for w in st.TRADE_WINDOWS}
    names = json.load(open(names_file, encoding="utf-8"))
    out = {}
    for side, labs in names.items():
        want = {c for cids in labs.values() for c in cids if uniq[side].get(c, "?") in ("B", "cc", "bd")}
        where = find_dirs(side, want)
        res = {}
        for lab, cids in labs.items():
            for c in cids:
                cl = uniq[side].get(c, "?")
                row = dict(cls=cl)
                if cl in ("B", "cc", "bd"):
                    per, tot, emp = {}, 0, 0
                    for w in WINS:
                        fp = os.path.join(where[w].get(c, "/нет"), "%s-trades.csv" % ENG[side])
                        if not os.path.exists(fp):
                            continue
                        with open(fp, encoding="utf-8", newline="") as f:
                            rows = list(csv.DictReader(f))
                        win = wins[w]
                        day = st.abs_day(win, [int(r["entry_ms"]) for r in rows])
                        lo_i, hi_i = (win.lod - st.D0).days, (win.hid - st.D0).days
                        rows = [r for r, dd in zip(rows, day) if lo_i <= dd <= hi_i]
                        e = sum(1 for r in rows if (r["sym"], int(r["sig_ms"])) in MISS[(side, w)])
                        per[w] = (len(rows), e)
                        tot, emp = tot + len(rows), emp + e
                    row.update(trades=tot, on_empty=emp, per_window=per, windows=len(per))
                res.setdefault(lab, {})[c] = row
        out[side] = res
    return out


def trades_report(dirs_file):
    """Сделки клеток из файлов клеток: сколько зачётных сделок (вход в сутках окна) пришло со строк без строки TK-671 (`dirs_file`: {cid: {сторона, dirs: {окно: каталог}}});
    сумма по окнам должна равняться числу сделок клетки в БД."""
    import tk604_stats as st
    import tk674_pack as pk
    pk.ROOT = ROOT
    if not MISS:
        rows_report()
    wins = {w.name: w for w in st.TRADE_WINDOWS}
    out = {}
    for cid, v in json.load(open(dirs_file, encoding="utf-8")).items():
        side, tot, emp = v["side"], 0, 0
        per = {}
        for w, d in v["dirs"].items():
            pk.ensure_cell(d)
            with open(os.path.join(d, "%s-trades.csv" % ENG[side]), encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            win = wins[w]
            day = st.abs_day(win, [int(r["entry_ms"]) for r in rows])
            lo_i, hi_i = (win.lod - st.D0).days, (win.hid - st.D0).days
            rows = [r for r, dd in zip(rows, day) if lo_i <= dd <= hi_i]          # зачётные: вход в сутках окна (как tk604_stats.splice)
            e = sum(1 for r in rows if (r["sym"], int(r["sig_ms"])) in MISS[(side, w)])
            per[w] = (len(rows), e)
            tot, emp = tot + len(rows), emp + e
        out[cid] = dict(side=side, trades=tot, on_empty=emp, per_window=per)
    return out


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    res = {}
    if what in ("rows", "all"):
        res["rows"] = rows_report()
    if what in ("cells", "all"):
        res["cells"] = cells_report()
    if what == "classes":
        res["classes"] = classes_report()[0]
    if what == "read":
        res["read"] = read_report(sys.argv[2])
    if what == "without":
        res["without"] = without_report(sys.argv[2])
    if what == "trades":
        res["trades"] = trades_report(sys.argv[2])
    print(json.dumps(res, ensure_ascii=False, indent=1))
