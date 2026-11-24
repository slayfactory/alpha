#!/usr/bin/env python3
"""TK-699 шаг 3: клетки БД летописи (c539), чей фильтр `F=` содержит один из 10 столбцов f12 (age_h, approach_dur_min, pierce_ep, pierce_max_bp, touch_ep,
since_touch_h, depth_min_bp, depth_mean_bp, since_touch_prior_h, touch_prior_none) — «охват» до дельты. Только чтение БД.

  tk699_scope.py --out /data/p42-tk699/run/scope-cells.json
      по (сторона, окно БД, смысл фильтра): число клеток, записи, партии бинаря; смыслы с `bd_*` (клетки заменены строками TK690-xb) помечены.
Смысл фильтра — всё после `F=` в ckey (`tok&tok`, токен `имя<=число` / `имя>=число`), имя столбца — до знака.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import time

COLS10 = ["age_h", "approach_dur_min", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h", "depth_min_bp", "depth_mean_bp", "since_touch_prior_h",
          "touch_prior_none"]
OPS = re.compile(r"<=|>=|==")


def sem_feats(sem: str):
    return [OPS.split(t, 1)[0] for t in sem.split("&")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/data/chron/chron.sqlite")
    ap.add_argument("--uni", default="c539")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t0 = time.time()
    for k in range(30):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True, timeout=120)
            rows = con.execute("select id, entry, side, win, ckey, bin, trades from metric where uni=? and ckey glob '* F=*'", (a.uni,)).fetchall()
            break
        except sqlite3.OperationalError as e:           # «database is locked» (индекс TK-704) — повторить
            print("retry", k, e, flush=True)
            time.sleep(20)
    else:
        raise SystemExit("БД занята")
    fixed = {r[0] for r in con.execute("select metric from metric_fix")}
    print("rows with F=: %d, %.0f s" % (len(rows), time.time() - t0), flush=True)
    cols = set(COLS10)
    out = collections.defaultdict(lambda: dict(n=0, entries=collections.Counter(), wins=collections.Counter(), ids=[], bd=False, cols=set()))
    tot = collections.Counter()
    for mid, entry, side, win, ckey, bn, trades in rows:
        sem = ckey.split("F=", 1)[1]
        fs = set(sem_feats(sem))
        tot[(side, win)] += 1
        if not fs & cols:
            continue
        k = (side, sem)
        d = out[k]
        d["n"] += 1
        d["entries"][entry] += 1
        d["wins"][win] += 1
        d["ids"].append(mid)
        d["bd"] = d["bd"] or any(f.startswith("bd_") for f in fs)
        d["cols"] |= fs & cols
    res = dict(db=a.db, uni=a.uni, total_rows_F=sum(tot.values()), by_side_win={"%s|%s" % k: v for k, v in tot.items()}, fixed_rows=len(fixed),
               sems=[dict(side=k[0], sem=k[1], n=d["n"], entries=dict(d["entries"]), wins=dict(d["wins"]), bd=d["bd"], cols=sorted(d["cols"]), ids=d["ids"])
                     for k, d in sorted(out.items())])
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    c = collections.Counter()
    for s in res["sems"]:
        for w, n in s["wins"].items():
            c[(s["side"], w)] += n
    print("sems with f12 token: %d; rows by (side, win): %s" % (len(res["sems"]), dict(c)))
    print("sems with bd token too: %d" % sum(1 for s in res["sems"] if s["bd"]))
    ce = collections.Counter()
    for s in res["sems"]:
        for e, n in s["entries"].items():
            ce[(s["side"], e)] += n
    print("rows by (side, entry): %s" % dict(sorted(ce.items())))


if __name__ == "__main__":
    main()
