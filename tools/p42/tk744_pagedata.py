#!/usr/bin/env python3
"""TK-744 (В-195): данные страницы :8090 — вариант V1 в «Эквити» (вкладку «Сделки» не трогает). Только srv; движок не запускает.

  python3 tk744_pagedata.py equity     # /data/p42-tk744/page/{wa,wb,wc}/short-{trades,daily}.csv для варианта `sv1` (tk684_variants.json)
"""
from __future__ import annotations

import csv
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk744_lib as L  # noqa: E402

T = L.T


def build_equity():
    """Каталоги окон wa / wb / wc: сделки V1 (копия файла прогона) и суточный ряд по дню входа (охрана G7: Σ ряда == Σ net сделок)."""
    for w in T.SEL:
        d = "%s/page/%s" % (L.ROOT, w)
        os.makedirs(d, exist_ok=True)
        src = os.path.join(T.run_dir("fh0", w), w, "%d-trades.csv" % T.CID["fh0"])
        shutil.copyfile(src, d + "/short-trades.csv")
        rows = list(csv.DictReader(open(src, encoding="utf-8", newline="")))
        day = {}
        for x in rows:
            day[int(x["entry_day"])] = day.get(int(x["entry_day"]), 0.0) + float(x["net"])
        with open(d + "/short-daily.csv", "w", encoding="utf-8", newline="") as f:
            f.write("day,pnl\n")
            for k in sorted(day):
                f.write("%d,%.6f\n" % (k, day[k]))
        print(d, len(rows), "сделок")
    return 0


if __name__ == "__main__":
    sys.exit(build_equity())
