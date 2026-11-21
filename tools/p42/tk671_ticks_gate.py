#!/usr/bin/env python3
"""TK-671: ворота равенства `StoreTicks` с `sigfeat_lib.Ticks` — массивы (t, p) суток побитово (в т. ч. сутки с несортированным файлом), доля суток со складом, скорость чтения.
Использование (srv): SIGFEAT_BASE_DATE=<T0> SIGFEAT_DAYS=80 python3 tk671_ticks_gate.py --win wa --days 300 [--unsorted 20] --out gate.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sigfeat_lib as L  # noqa: E402
import tk671_ticks as T  # noqa: E402

T0 = {"wa": "2026-06-17", "wb": "2026-07-25", "wc": "2026-09-01"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", required=True, choices=sorted(T0))
    ap.add_argument("--days", type=int, default=300)
    ap.add_argument("--unsorted", type=int, default=20, help="сколько суток с несортированным файлом добавить (по заголовку склада)")
    ap.add_argument("--seed", type=int, default=671)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if L.BASE_DATE != T0[a.win] or L.DAYS_TOTAL != 80:
        sys.exit("SIGFEAT_BASE_DATE=%s / SIGFEAT_DAYS=%d ≠ окно %s" % (L.BASE_DATE, L.DAYS_TOTAL, a.win))
    root = "/data/p42-tk621/%s/aggtrades" % a.win
    rnd = random.Random(a.seed)
    syms = sorted(os.listdir(root))
    pairs, uns = [], []
    t_scan = time.time()
    while len(pairs) < a.days or len(uns) < a.unsorted:
        s = rnd.choice(syms)
        d = rnd.randrange(0, L.DAYS_TOTAL)
        date = L.day_date(d)
        if date >= "2026-11-14" or not os.path.exists(f"{root}/{s}/{s}-aggTrades-{date}.zip"):
            continue
        try:
            with open(f"{T.STORE_ROOT}/{s}/{s}-{date}.tk", "rb") as f:
                h = f.read(T.HEAD_LEN)
        except OSError:
            h = b""
        if len(h) == T.HEAD_LEN and struct.unpack_from("<Q", h, 40)[0] > 0:
            if len(uns) < a.unsorted:
                uns.append((s, d))
        elif len(pairs) < a.days:
            pairs.append((s, d))
        if time.time() - t_scan > 600:
            break
    rows = []
    t_old = t_new = 0.0
    bad = 0
    for kind, lst in (("sorted", pairs), ("unsorted", uns)):
        for s, d in lst:
            o = L.Ticks(root, s)
            n = T.StoreTicks(root, s)
            t = time.time()
            vo = L.Ticks._read(o, d)
            t_old += time.time() - t
            t = time.time()
            vn = T.StoreTicks._read(n, d)
            t_new += time.time() - t
            same = (vo is None and vn is None) or (vo is not None and vn is not None and np.array_equal(vo[0], vn[0]) and np.array_equal(vo[1], vn[1]))
            bad += 0 if same else 1
            rows.append(dict(kind=kind, sym=s, day=d, same=bool(same), n=0 if vo is None else len(vo[0]), store=n.n_store, zip=n.n_zip, unsorted_zip=o.unsorted_days))
    res = dict(win=a.win, coin_days=len(rows), unsorted_days=len(uns), mismatches=bad, from_store=sum(r["store"] for r in rows), fallback_zip=sum(r["zip"] for r in rows),
               rows_total=sum(r["n"] for r in rows), sec_zip=round(t_old, 2), sec_store=round(t_new, 2), speedup=round(t_old / max(t_new, 1e-9), 1),
               bad=[r for r in rows if not r["same"]][:10])
    json.dump(res, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
