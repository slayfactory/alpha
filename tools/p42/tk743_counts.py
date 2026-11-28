#!/usr/bin/env python3
"""TK-743: счёт заявок по ролям и событиям — по файлам `<cid>-orders.csv` прогона (`tk743_run.sh`) и сверка с `<cid>-trades.csv`.

  python3 tk743_counts.py [корень=/data/p42-tk743/trades]   # по всем окнам; в конце — итог и проверка «каждая заявка закрыта»
"""
import csv
import glob
import os
import sys
from collections import Counter

root = sys.argv[1] if len(sys.argv) > 1 else "/data/p42-tk743/trades"
roles = Counter()
trades = setups = 0
open_left = 0
for f in sorted(glob.glob(os.path.join(root, "*", "*", "*-orders.csv"))):
    last = {}
    with open(f, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            roles[(r["role"] if not r["role"].startswith("take") else "take", r["event"])] += 1
            last[(r["sym"], r["level"], r["sig_ms"], r["lot"], r["oid"])] = r["event"]
    open_left += sum(1 for v in last.values() if v in ("place", "reprice"))
    tf = f.replace("-orders.csv", "-trades.csv")
    if os.path.exists(tf):
        trades += sum(1 for _ in open(tf, encoding="utf-8")) - 1
    sf = f.replace("-orders.csv", "-setup.csv")
    if os.path.exists(sf):
        with open(sf, newline="", encoding="utf-8") as fh:
            setups += sum(1 for r in csv.DictReader(fh) if r["role"] == "level")
print("сделок %d, сделок с уровнями сетапа %d, заявок незакрытых %d" % (trades, setups, open_left))
for (role, ev), n in sorted(roles.items()):
    print("%-12s %-8s %d" % (role, ev, n))
