#!/usr/bin/env python3
"""П-42 этап A, шаг 4д (TK-728, §13.78.4): охват 10 признаков открытого интереса и лонг / шорт (`OI_LS10`) на выборке SEL c539 (200 000 строк, `tk728_sample.py`) — по всей выборке и по месяцам.
Правило (до счёта): охват 0,3–0,5 → статус «нет данных» до докачки; иначе — без изменения. srv.
  python3 tk728_oi.py --sample /data/p42-tk728/res/sample.parquet --out /data/p42-tk728/res/oi.json --db /data/p42-tk728/res728.sqlite"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk728_lib as L  # noqa: E402
import tk728_db as D  # noqa: E402

LO, HI = 0.3, 0.5


def verdict(cov: float) -> str:
    """Правило 4д: охват в [0,3; 0,5] → «нет данных»."""
    return "нет данных" if LO <= cov <= HI else "охват вне 0,3–0,5"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    cols = L.OI_LS10
    df = pd.read_parquet(a.sample, columns=["abs_ms", "win"] + cols)
    df["month"] = pd.to_datetime(df["abs_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    res = {"n_rows": int(len(df)), "by_feature": {}, "by_month": {}, "by_win": {}}
    for c in cols:
        cov = float(df[c].notna().mean())
        res["by_feature"][c] = dict(cov=cov, verdict=verdict(cov))
    for key, name in (("month", "by_month"), ("win", "by_win")):
        for g, sub in df.groupby(key):
            res[name][str(g)] = dict(n=int(len(sub)), cov_min=float(sub[cols].notna().mean().min()), cov_max=float(sub[cols].notna().mean().max()))
    res["rule_triggered"] = [c for c, v in res["by_feature"].items() if v["verdict"] == "нет данных"]
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.db:
        db = D.connect(a.db)
        D.new_part(db, "A4-oi", ["t728_mem"], "4д: охват OI_LS10", "SEL c539, выборка 200 000, зерно %d" % L.SEED)
        D.put(db, "t728_mem", "A4-oi", [("oi_ls10", c, v["cov"], None, None, res["n_rows"], v["verdict"]) for c, v in res["by_feature"].items()])
        db.commit()
    for c, v in res["by_feature"].items():
        print("%-9s охват %.4f  %s" % (c, v["cov"], v["verdict"]))
    print("по месяцам (min–max охвата по 10 признакам):", {k: (round(v["cov_min"], 3), round(v["cov_max"], 3)) for k, v in res["by_month"].items()})


if __name__ == "__main__":
    main()
