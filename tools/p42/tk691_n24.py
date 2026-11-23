#!/usr/bin/env python3
"""TK-691: порог `N24_q60` лонга по ЗАМОРОЖЕННОМУ правилу tk638 (`tk638_run.compute_thresholds`: объединённый квантиль 0,6 по n24 кандидатов `L_base` в зачётных диапазонах трёх окон,
m24 исключён, метод linear, 2 значащие цифры) на заданной вселенной. Входы — готовые выходы `alpha-feat tcount` (md5 69fcf7cd) TK-638: `/data/p42-tk638/feat/h775/<окно>-L.csv`;
вселенная = строки, у которых монета ∈ списка `--symbols` (кандидат монеты не зависит от остальных монет; на `c539` это проверяет движок: `no_key` = 0). P&L не читается.
  python3 tk691_n24.py --symbols /data/p42-tk621/universe/symbols-crypto.txt --out res/n24-c539.json       # c539
  python3 tk691_n24.py --out res/n24-h775.json                                                              # сверка: 217550,8 → 220000, 914 кандидатов (thr38.json)"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk604_stats as st  # noqa: E402
import tk638_feat as ft  # noqa: E402
import tk638_run as t8  # noqa: E402

COUNTS = "/data/p42-tk638/feat"
LADDER_Q = "0.60"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_symbols(path):
    text = open(path, encoding="utf-8").read().rstrip("\n")             # как `retitr_full_run.read_symbols`: через запятую, переводы строк = запятые
    return {x for x in text.replace("\n", ",").split(",") if x}


def tabs_of(counts_root, coins):
    """{окно: столбцы кандидатов лонга} по строкам таблицы tcount h775; coins — множество монет или None (все)."""
    tabs, files = {}, {}
    for w in st.TRADE_WINDOWS:
        p = ft.feat_path(counts_root, "h775", w.name, "L")
        sym, c = ft.read_counts(p)
        keep = np.array([s in coins for s in sym], dtype=bool) if coins is not None else np.ones(len(sym), dtype=bool)
        tabs[w.name] = dict(ms=c["sig_ms"][keep], n60=c["n60"][keep], n24=c["n24"][keep], m60=c["m60"][keep], m24=c["m24"][keep])
        files[w.name] = {"path": p, "sha256": sha(p), "rows": int(len(sym)), "rows_kept": int(keep.sum())}
    return tabs, files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--counts-root", default=COUNTS)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    coins = read_symbols(a.symbols) if a.symbols else None
    tabs, files = tabs_of(a.counts_root, coins)
    thr = t8.compute_thresholds(tabs)["N24"]
    res = {"universe": os.path.basename(a.symbols) if a.symbols else "all (h775, без фильтра)", "symbols_sha256": sha(a.symbols) if a.symbols else None,
           "n_symbols": len(coins) if coins is not None else None, "rule": "tk638_run.compute_thresholds: квантиль 0,6, linear, зачётные диапазоны, m24 исключён, 2 значащие цифры",
           "n_scored": thr["n_scored"], "n_all_rows": thr["n_all_rows"], "q_raw_0.60": thr["q_raw"][LADDER_Q], "N": thr["q"][LADDER_Q], "q_all_rows_rounded_0.60": thr["q_all_rows_rounded"][LADDER_Q],
           "m24_rows": {w: int(t["m24"].sum()) for w, t in tabs.items()}, "inputs": files}
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("N24_q60: N = %g (сырой %.1f), кандидатов в зачётных диапазонах %d из %d строк; m24-строк %s" % (res["N"], res["q_raw_0.60"], res["n_scored"], res["n_all_rows"], res["m24_rows"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
