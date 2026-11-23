#!/usr/bin/env python3
"""TK-700, ворота прогрева 20 суток: сигналы окна `wa20` (T0 = 11.06, дамп `tk700_wa20.sh`) против сигналов `wa` (T0 = 17.06, прогрев 14 суток) на общем отрезке — строки с
sig_ms ≥ суток 20 окна wa20 (= суток 14 окна wa, 01.07). Только сигналы, без P&L (июнь отложенной выборки читается один раз после заморозки).

  python3 tk700_warm20.py --new /data/p42-tk700/gate/wa20/dumps [--out /data/p42-tk700/gate/warm20.json]

Эталоны: 12 источников — `/data/p42-tk671/prod/siglv-wa-<side>/dumps/<источник>/<side>-sigsx.csv`; L_ind / S_ind / D_ind — `/data/p42-tk700/gate/g1/wa/dumps/<вариант>/<side>-sigsx.csv`.
Сдвиг T0 = 6 суток: sig_ms + 518 400 000, born / end + 8 640 мин, first_bar и индексы `members` + 8 640 / tf (бары tf минут); номер `level` — порядковый, не сравнивается.
Совпали все строки (множества целиком, все столбцы после сдвига) на всех (источник, сторона) → passed; расхождения — до 20 строк на проверку. Печатает только счётчики."""
from __future__ import annotations

import argparse
import json
import os
import sys

T0_SHIFT_MS = 6 * 86400 * 1000
T0_SHIFT_MIN = 6 * 1440
DAY20_MS = 20 * 86400 * 1000
END_MS = 73 * 86400 * 1000                       # wa20: SIGSX 14:73; wa: 14:73 от T0 = 17.06 → общий отрезок кончается на сутках 67 окна wa
SRC12 = ["p%dn%d" % (tf, nb) for tf in (30, 60, 240) for nb in (1, 2, 3)] + ["lxD", "lxW", "lxP"]
IND = ["L_ind", "S_ind", "D_ind"]
REF12 = "/data/p42-tk671/prod/siglv-wa-%s/dumps/%s/%s-sigsx.csv"
REFIND = "/data/p42-tk700/gate/g1/wa/dumps/%s/%s-sigsx.csv"


def tf_of(var):
    if var.startswith("p") and "n" in var:
        return int(var[1:var.index("n")])
    return 60


def read_rows(path):
    import csv
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        head = next(r)
        return head, list(r)


def norm(head, row, shift_in, tf):
    """Строка → кортеж без `level`; shift_in=True — строки эталона (wa) приводятся к координатам wa20."""
    d = dict(zip(head, row))
    d.pop("level", None)
    if shift_in:
        d["sig_ms"] = str(int(d["sig_ms"]) + T0_SHIFT_MS)
        for k in ("born", "end"):
            if k in d:
                d[k] = str(int(d[k]) + T0_SHIFT_MIN)
        fb = T0_SHIFT_MIN // tf
        if "first_bar" in d:
            d["first_bar"] = str(int(d["first_bar"]) + fb)
        if "members" in d and d["members"]:
            d["members"] = "|".join("%d:%s" % (int(i) + fb, p) for i, p in (m.split(":") for m in d["members"].split("|")))
    return tuple(sorted(d.items()))


def compare(new_path, ref_path, tf):
    hn, rn = read_rows(new_path)
    hr, rr = read_rows(ref_path)
    if hn != hr:
        return dict(ok=False, note="столбцы различаются: %s | %s" % (hn, hr))
    ia = hn.index("sig_ms")
    new = {norm(hn, r, False, tf) for r in rn if DAY20_MS <= int(r[ia]) < END_MS}
    ref = {norm(hr, r, True, tf) for r in rr if DAY20_MS <= int(r[ia]) + T0_SHIFT_MS < END_MS}
    on, orf = new - ref, ref - new
    return dict(ok=not on and not orf, new_rows=len(new), ref_rows=len(ref), only_new=len(on), only_ref=len(orf),
                examples_new=[dict(x) for x in sorted(on)[:5]] if on else [], examples_ref=[dict(x) for x in sorted(orf)[:5]] if orf else [])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", default="/data/p42-tk700/gate/wa20/dumps")
    ap.add_argument("--out", default="/data/p42-tk700/gate/warm20.json")
    a = ap.parse_args(argv)
    checks = []
    for var in SRC12 + IND:
        for side in ("long", "short"):
            newp = os.path.join(a.new, var, "%s-sigsx.csv" % side)
            refp = (REFIND % (var, side)) if var in IND else (REF12 % (side, var, side))
            if not (os.path.exists(newp) and os.path.exists(refp)):
                checks.append(dict(var=var, side=side, ok=False, note="нет файла: %s" % (newp if not os.path.exists(newp) else refp)))
                continue
            c = compare(newp, refp, tf_of(var))
            c.update(var=var, side=side)
            checks.append(c)
            print("%-6s %-5s %s new=%s ref=%s только_new=%s только_ref=%s" % (var, side, "ok " if c["ok"] else "РАСХ", c.get("new_rows"), c.get("ref_rows"), c.get("only_new"), c.get("only_ref")))
    res = dict(passed=all(c["ok"] for c in checks), n=len(checks), failed=sum(1 for c in checks if not c["ok"]), shift_days=6, from_day=20, checks=checks)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WARM20", "PASS" if res["passed"] else "РАСХОЖДЕНИЯ", "(%d проверок, провалено %d) → %s" % (res["n"], res["failed"], a.out))
    return 0 if res["passed"] else 3


if __name__ == "__main__":
    sys.exit(main())
