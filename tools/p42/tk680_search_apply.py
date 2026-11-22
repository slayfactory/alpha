#!/usr/bin/env python3
"""П-42, TK-680: подставить в features.json значения «Перебор» единичек из docs/findings/p42-titration-coverage-2026-11-22/search-values.json
(tk680_singles_json.py --search) — только в строки, где «Перебор» = «нет»: оси TK-674 (e, e2, tpm60, cons60, n24, zv6h, SHARP) остаются как есть.
  python tools/p42/tk680_search_apply.py <features.json>     — правит файл на месте (повтор ничего не меняет)"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VALUES = os.path.join(HERE, "..", "..", "docs", "findings", "p42-titration-coverage-2026-11-22", "search-values.json")


def apply(d: dict, values: dict) -> list:
    changed = []
    for s in d["sets"]:
        for g in s["groups"]:
            for x in g["features"]:
                v = values.get(x["name"])
                if v and x["search"] == "нет":
                    x["search"] = v
                    changed.append(x["n"])
    return changed


def main():
    path = sys.argv[1]
    d = json.load(open(path, encoding="utf-8"))
    changed = apply(d, json.load(open(VALUES, encoding="utf-8")))
    if changed:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(d, ensure_ascii=False, indent=1))
    print("строк изменено:", len(changed), path)


if __name__ == "__main__":
    main()
