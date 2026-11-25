#!/usr/bin/env python3
"""TK-700: ворота FAST — столбцы FAST TK-681 на строках базы (wa / wb / wc, таблица ворот `gate/p671/asm`) равны прежним `fast.csv` TK-681 (`/data/p42-tk681/fast/<окно>-<сторона>/`) по ключу (монета, sig_ms, level_px_e8).

  python3 tk700_fast_gate.py --asm /data/p42-tk700/gate/p671/asm --out /data/p42-tk700/gate/fast-gate.json [--wins wa,wb,wc]

Берутся строки обеих таблиц с общим ключом (вселенная TK-700 — c539, у TK-681 — 775 монет); печатается: строк общих / только у меня / только в TK-681 (вне вселенной), по столбцу — пустых не на тех местах и расходящихся значений (|a−b| > 1e-6·max(1,|b|), `fast.csv` — текст). Код возврата 0 — расхождений нет."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FASTDIR = "/data/p42-tk681/fast/%s-%s"
KEY = ["sym", "sig_ms", "level_px_e8"]


def compare(mine, ref, cols, rtol=1e-6):
    """mine / ref — таблицы с ключом KEY и столбцами `cols`: {общих, только_у_меня, только_у_них, по_столбцу: {пустых_не_там, разных, макс}}."""
    def keyed(t):
        t = t.reset_index(drop=True)
        k = t["sym"].astype(str) + "|" + t["sig_ms"].astype("int64").astype(str) + "|" + t["level_px_e8"].astype("int64").astype(str)
        return t.drop(columns=KEY).set_index(k.to_numpy())
    km, kr = keyed(mine), keyed(ref)
    if not km.index.is_unique or not kr.index.is_unique:
        raise SystemExit("ключ (монета, sig_ms, уровень) не уникален")
    common = km.index.intersection(kr.index)
    res = {"common": int(len(common)), "only_mine": int(len(km.index.difference(kr.index))), "only_ref": int(len(kr.index.difference(km.index))), "cols": {}}
    a, b = km.loc[common], kr.loc[common]
    for c in cols:
        x, y = a[c].to_numpy(np.float64), b[c].to_numpy(np.float64)
        nan_bad = int((np.isnan(x) != np.isnan(y)).sum())
        both = ~np.isnan(x) & ~np.isnan(y)
        d = np.abs(x[both] - y[both])
        bad = int((d > rtol * np.maximum(1.0, np.abs(y[both]))).sum())
        res["cols"][c] = {"nan_bad": nan_bad, "diff": bad, "max": float(d.max()) if len(d) else 0.0}
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--asm", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--wins", default="wa,wb,wc")
    a = ap.parse_args(argv)
    import tk604_stats as st
    import tk680_axes as a80
    names = list(a80.FAST_NAMES)
    t0s = {w.name: int(w.t0_ms) for w in st.TRADE_WINDOWS}
    out, rc = {}, 0
    for w in a.wins.split(","):
        for side in ("long", "short"):
            m = pd.read_parquet(os.path.join(a.asm, "%s-%s.parquet" % (w, side)))
            m = m[m["meta_base_pass"] > 0]
            cols = [c for c in names if c in m.columns]
            d = FASTDIR % (w, side)
            key = pd.read_csv(d + "/eps-key.csv", usecols=["sym", "sig_ms", "level_px_e8"])
            fc = pd.read_csv(d + "/fast.csv")
            ref = pd.concat([key.reset_index(drop=True), fc.drop(columns=["sym", "sig_ms"]).reset_index(drop=True)], axis=1)
            t0 = t0s[w]
            mm = m.drop(columns=["sym"], errors="ignore").rename(columns={"meta_sym": "sym", "meta_level_px_e8": "level_px_e8"}).copy()
            mm["sig_ms"] = (mm["meta_abs_ms"].astype("int64") - t0)
            r = compare(mm[KEY + cols], ref[KEY + cols], cols)
            r["t0_ms"], r["rows_mine"], r["rows_ref"], r["fast_cols"] = t0, int(len(mm)), int(len(ref)), len(cols)
            bad = {c: v for c, v in r["cols"].items() if v["nan_bad"] or v["diff"]}
            r["bad_cols"] = bad
            out["%s-%s" % (w, side)] = r
            print("%s-%s: строк мои %d / TK-681 %d, общих %d, только у меня %d, столбцов FAST %d, расходятся %d%s" % (
                w, side, r["rows_mine"], r["rows_ref"], r["common"], r["only_mine"], len(cols), len(bad), " → FAIL" if bad else " → PASS"))
            for c, v in list(bad.items())[:12]:
                print("   %s: пустых не на тех местах %d, значений расходится %d (макс. %.3g)" % (c, v["nan_bad"], v["diff"], v["max"]))
            rc |= 3 if bad or r["common"] == 0 or r["common"] < r["rows_mine"] else 0
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
