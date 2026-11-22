#!/usr/bin/env python3
"""П-42 TK-680: справки к чтению единичек (§13.73.10 / PS-965), srv, из файлов сделок клеток — без нового счёта.
  python3 tk680_read.py stops   — оси sl / nx / cw / ls / sn / sg: у клетки число stop-выходов, из них не позже 1 с после входа и с |gross| ≤ 0,05 $ (в пределах ≈ 1 бп от входа)
  python3 tk680_read.py meta <ось> — счётчики meta.json клеток оси рядом с базой (сигналы, кандидаты)
Окружение — как у tk680_chain.sh (TK674_ROOT, TK674_REBASE=1, TK674_LEVEL=1)."""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_axes as ax  # noqa: E402
import tk674_run as run  # noqa: E402

ENGNAME = {"L": "long", "S": "short"}


def rows_of(side, axes):
    lv = json.load(open(run.level_path(side), encoding="utf-8"))
    base_ck = ax.base_ckey(side)
    want = [(a["axis"], v[0], v[1]) for a in lv["axes"] if a["axis"] in axes for v in a["vals"]]
    rows = run.chron_store(ro=True).cells_lookup(ENGNAME[side], run.UNI, run.WIN, sorted({w[2] for w in want} | {base_ck}), bins=run.eq_bins())
    return [("база", "", base_ck)] + [w for w in want if w[2] != base_ck], rows


def stops(side):
    want, rows = rows_of(side, ("sl", "nx", "cw", "ls", "sn", "sg"))
    for axis, lab, ck in want:
        r = rows.get(ck)
        if r is None:
            continue
        n = ns = fast = flat = 0
        for d in r["extra"]["dirs"].values():
            for t in csv.DictReader(open(os.path.join(d, "%s-trades.csv" % ENGNAME[side]), encoding="utf-8")):
                n += 1
                if t["kind"] == "stop":
                    ns += 1
                    fast += int(t["exit_ms"]) - int(t["entry_ms"]) <= 1000
                    flat += abs(float(t["gross"])) <= 0.05
        print("%s %-4s %-10s сделок %4d stop %4d  ≤1с %3d  у входа %3d (%.1f %%)" % (side, axis, lab, n, ns, fast, flat, 100.0 * flat / max(n, 1)))


def meta(side, axis):
    want, rows = rows_of(side, (axis,))
    for a, lab, ck in want:
        r = rows.get(ck)
        if r is None:
            continue
        tot = {}
        for d in r["extra"]["dirs"].values():
            m = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
            for k, v in m.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    tot[k] = tot.get(k, 0) + v
        print(side, a, lab, "сделок", r["trades"], "Σ $ %.0f" % r["usd"], {k: v for k, v in tot.items() if any(x in k for x in ("sig", "cand", "rej", "color", "entr"))})


if __name__ == "__main__":
    for side in ax.SIDES:
        stops(side) if sys.argv[1] == "stops" else meta(side, sys.argv[2])
