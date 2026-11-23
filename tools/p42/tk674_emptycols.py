#!/usr/bin/env python3
"""П-42 TK-674: пустые столбцы таблицы TK-671 в таблицах фильтров («нет строки — пусто проходит», tk680_singles.add_columns) — без движка и без P&L.
  srv:  TK674_ROOT=/data/p42-tk674c python3 tk674_emptycols.py [rows | cells | all]
rows  — строки кандидатов c539 в `ftab674x/<окно>-<сторона>.csv` (ключ sym + sig_ms; всего и в зачётном диапазоне окна) без строки таблицы TK-671 (join как в add_columns: meta_base_pass > 0,
        бит 1 раньше бита 2) и доля NaN по столбцам у строк, которые в таблице есть (только столбцы, лежащие в parquet напрямую);
cells — клетки шагов 2–5 (plan674bs / plan674bsx / level674bs3* / level674bs4*): сколько с фильтром по столбцу вне таблиц TK-654 / TK-638 (A: tpm60, cons60, zv6h, n24, sharp) —
        такой фильтр у строки без строки TK-671 проходит молча."""
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


def cells_report():
    files = {"шаг 2 L": "plan674bs-L.json", "шаг 2 S": "plan674bs-S.json", "шаг 3 L": "plan674bsx-L.json", "шаг 3 S": "plan674bsx-S.json",
             "шаг 4 L (объединение)": "level674bs3u-L.json", "шаг 5 L (четвёрки)": "level674bs4u-L.json"}
    for t in ["level674bs3-S.json"] + ["level674bs3r%02d-S.json" % i for i in range(2, 14)]:
        if os.path.exists(os.path.join(ROOT, "res", t)):
            files["шаг 4 S: " + (t[len("level674bs3"):-len("-S.json")] or "основа")] = t
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


def trades_report(dirs_file):
    """Сделки клеток из файлов клеток: сколько зачётных сделок (вход в сутках окна) пришло со строк без строки TK-671 (`dirs_file`: {cid: {сторона, dirs: {окно: каталог}}});
    сумма по окнам должна равняться числу сделок клетки в БД."""
    import tk604_stats as st
    if not MISS:
        rows_report()
    wins = {w.name: w for w in st.TRADE_WINDOWS}
    out = {}
    for cid, v in json.load(open(dirs_file, encoding="utf-8")).items():
        side, tot, emp = v["side"], 0, 0
        per = {}
        for w, d in v["dirs"].items():
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
    if what == "trades":
        res["trades"] = trades_report(sys.argv[2])
    print(json.dumps(res, ensure_ascii=False, indent=1))
