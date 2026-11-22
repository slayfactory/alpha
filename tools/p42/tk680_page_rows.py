#!/usr/bin/env python3
"""П-42, TK-680: добавить на вкладку «Признаки» строки № 308–311 (история пробоев за 3 и 5 суток) в любой features.json — локальный или живой с srv.
  python tools/p42/tk680_page_rows.py <features.json>            — правит файл на месте (повтор ничего не меняет)
Строки: 308 ok10_d3, 309 ok10_d5 (своя сторона), 310 xok10_d3, 311 xok10_d5 (чужая) в наборе history; связи «Связан с №» — в обе стороны
(203 / 204 / 247 ↔ 308 / 309; 205 / 206 / 248 ↔ 310 / 311; пара своя ↔ чужая того же окна). «Перебор» — только у титрованных ok10_* своей стороны (PS-961, L-308);
xok10_d3 / d5 осью не были. Ничего, кроме этих строк и связей, файл не трогает — живой файл может нести № 252–307 другой ветки (TK-681)."""
from __future__ import annotations

import json
import sys

SEARCH = "≥ 1 / 2 / 3; ≤ 0 / 1 / 2"                       # PS-961 п. 3: .ge — 1 / 2 / 3, .le — 0 / 1 / 2
OWN, CROSS = (203, 204, 247), (205, 206, 248)
ROWS = [  # (№, имя, что значит, группа, связи среди новых)
    (308, "ok10_d3", "Успешных пробоев своей стороны у монеты за 3 суток", "Своя сторона", OWN, [309, 310]),
    (309, "ok10_d5", "Успешных пробоев своей стороны у монеты за 5 суток", "Своя сторона", OWN, [308, 311]),
    (310, "xok10_d3", "Успешных пробоев противоположной стороны за 3 суток", "Чужая сторона", CROSS, [308, 311]),
    (311, "xok10_d5", "Успешных пробоев противоположной стороны за 5 суток", "Чужая сторона", CROSS, [309, 310]),
]
SEARCH_OWN = {203, 204, 247}                               # титрованы единичками PS-960 / PS-961 (ok10_d7 / d14 / d30)


def apply(d: dict) -> bool:
    hist = next(s for s in d["sets"] if s["id"] == "history")
    groups = {g["title"]: g for g in hist["groups"]}
    feats = {x["n"]: x for s in d["sets"] for g in s["groups"] for x in g["features"]}
    before = json.dumps(d, ensure_ascii=False)
    for n, name, what, grp, family, pair in ROWS:
        row = {"n": n, "name": name, "what": what, "src": ["history"], "base": "—", "search": SEARCH if n in (308, 309) else "нет", "rel": sorted(list(family) + pair)}
        if n in feats:
            feats[n].update(row)
        else:
            groups[grp]["features"].append(row)
            feats[n] = row
        for k in family:
            feats[k]["rel"] = sorted(set(feats[k]["rel"]) | {n})
    for k in SEARCH_OWN:
        feats[k]["search"] = SEARCH
    if "TK-680" not in hist["task"]:
        hist["task"] += ", TK-680"
    return json.dumps(d, ensure_ascii=False) != before


def main():
    path = sys.argv[1]
    d = json.load(open(path, encoding="utf-8"))
    changed = apply(d)
    if changed:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(d, ensure_ascii=False, indent=1))
    print("изменён" if changed else "без изменений", path)


if __name__ == "__main__":
    main()
