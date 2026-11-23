#!/usr/bin/env python3
"""TK-700: покрытие столбцов таблиц признаков отложенной выборки — столбец × месяц (охрана `COVER_MIN` = 0,7 в месяц, как в поиске TK-674 / TK-680) — без P&L и без меток.

  python3 tk700_cov.py --asm /data/p42-tk700/p671/asm --wins m01,m02,m03,m04,m05,m06 --out /data/p42-tk700/cov/cov.json [--csv /data/p42-tk700/cov/cov.csv] [--cols-file <json со списком имён>]

Столбцы-оси: `tk680_axes.column_list(tk680_cols.json)` (окно ≤ 60 мин или снимок) + ctx (`btc_mv5`, `cosig15`) + hist (`ok10_d3…d30`) + FAST TK-681 (`FAST_NAMES`) — или `--cols-file`.
Доля = не-пустых значений / строк базы месяца (окно m0N = месяц N); столбец «покрыт», если доля ≥ 0,7 в КАЖДОМ месяце обеих сторон; «не построен» — столбца нет в таблице совсем (семейство не считалось).
Выход: json {по стороне: {столбец: {месяц: доля}}, непокрытые, не построенные} + csv (сторона, столбец, статус, доли по месяцам)."""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
COVER_MIN = 0.7
SIDES = ("long", "short")


def axis_columns():
    import tk680_axes as a80
    cols = json.load(open(os.path.join(HERE, "tk680_cols.json"), encoding="utf-8"))
    out = []
    for c, src, _ in a80.column_list(cols):
        for x in (src if isinstance(src, tuple) else (src,)):
            if x not in out:
                out.append(x)
    for x in list(a80.CTX_COLS) + list(a80.HIST_COLS) + list(a80.FAST_NAMES):
        if x not in out:
            out.append(x)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--asm", required=True)
    ap.add_argument("--wins", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--cols-file", default=None)
    ap.add_argument("--min", type=float, default=COVER_MIN)
    a = ap.parse_args(argv)
    wins = [w for w in a.wins.split(",") if w]
    cols = json.load(open(a.cols_file, encoding="utf-8")) if a.cols_file else axis_columns()
    tabs = {(w, s): pd.read_parquet(os.path.join(a.asm, "%s-%s.parquet" % (w, s))) for w in wins for s in SIDES}
    res = {"min": a.min, "wins": wins, "rows": {s: {w: int(len(tabs[(w, s)])) for w in wins} for s in SIDES}, "cov": {}, "not_built": [], "uncovered": [], "covered": []}
    for s in SIDES:
        res["cov"][s] = {}
    status = {}
    for c in cols:
        have = [c in tabs[(w, s)].columns for w in wins for s in SIDES]
        if not any(have):
            res["not_built"].append(c)
            status[c] = "not_built"
            continue
        ok = True
        for s in SIDES:
            res["cov"][s][c] = {w: (round(float(tabs[(w, s)][c].notna().mean()), 4) if c in tabs[(w, s)].columns and len(tabs[(w, s)]) else None) for w in wins}
            ok &= all(v is not None and v >= a.min for v in res["cov"][s][c].values())
        status[c] = "covered" if ok else "uncovered"
        (res["covered"] if ok else res["uncovered"]).append(c)
    res["counts"] = {k: len(v) for k, v in (("covered", res["covered"]), ("uncovered", res["uncovered"]), ("not_built", res["not_built"]))}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    if a.csv:
        with open(a.csv, "w", encoding="utf-8", newline="\n") as f:
            f.write("side,column,status,%s\n" % ",".join(wins))
            for s in SIDES:
                for c in cols:
                    v = res["cov"][s].get(c)
                    f.write("%s,%s,%s,%s\n" % (s, c, status[c], ",".join("" if v is None or v[w] is None else "%.3f" % v[w] for w in wins)))
    print("столбцов %d: покрыто %d, не покрыто %d, не построено %d (порог %.2f в каждом месяце, обе стороны)" % (len(cols), len(res["covered"]), len(res["uncovered"]), len(res["not_built"]), a.min))
    return 0


if __name__ == "__main__":
    sys.exit(main())
