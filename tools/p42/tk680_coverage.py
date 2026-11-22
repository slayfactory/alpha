#!/usr/bin/env python3
"""П-42, TK-680: таблица покрытия титровкой — что из ключей движка и столбцов таблицы признаков титровалось и на какой базе.
  python tools/p42/tk680_coverage.py            — docs/findings/p42-titration-coverage-2026-11-22.md из engine.json + cols.json (+ singles.json после счёта)
  python tools/p42/tk680_coverage.py --counts   — только счётчики
Источник строк — docs/findings/p42-titration-coverage-2026-11-22/{engine,cols}.json (история прогонов по летописи и карточкам; правится руками).
Статус: «текущая» — h775 и база стопа Л 3 % / Ш 4 %; «устарело» — только 227 / 566 / 544 или h775 при стопе 5 %; «никогда» — сетки с P&L не было."""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
DIR = os.path.join(ROOT, "docs", "findings", "p42-titration-coverage-2026-11-22")
OUT = DIR + ".md"

# поправки статуса после чтения прогонов (причина — в «Где»)
ENGINE_FIX = {
    "ls": ("stale", "на базе фиксированного стопа — только при 5 % (TK-643); TK-676 — поверх стопа от волатильности"),
    "ew": ("never", "TK-585: калибровка окна по ошибке цены на 33 сигналах, сетки с P&L не было"),
    "ex": ("never", "TK-585: 0 задан правилом до счёта, сетки не было"),
}
RU = {"current": "текущая", "stale": "устарело", "never": "никогда"}
FLAG_RU = {"reference_only": "справочный", "look_ahead": "заглядывание", "constant": "одно значение", "not_filter": "в фильтр не входит", "composite": "сводный",
           "binary": "0 / 1", "vacuous": "сделки не меняет", "feature_filter": "служебный"}


def norm_status(x, kind):
    s = x["status"]
    if kind == "engine" and x["key"] in ENGINE_FIX:
        return ENGINE_FIX[x["key"]][0]
    if kind == "cols" and s in ("ml_only", "unclear"):
        return "never"                              # порогов с P&L не было: только признак модели / часть сводного ранга
    return s


def flags(x):
    out = []
    for f in x.get("flags") or []:
        k, _, v = f.partition(":")
        if k in FLAG_RU:
            out.append(FLAG_RU[k])
        elif k in ("duplicate_of", "near_duplicate_of"):
            out.append("дубль `%s`" % v)
        elif k == "depends_on":
            out.append("нужен `%s`" % v.split()[0]) if not v.startswith("rule") else None
        elif k == "in_composite":
            out.append("часть `%s`" % v)
    seen = []
    for o in out:
        if o not in seen:
            seen.append(o)
    return seen


def rows():
    out = []
    singles = {}
    sp = os.path.join(DIR, "singles.json")
    if os.path.exists(sp):
        singles = json.load(open(sp, encoding="utf-8"))          # {имя: {"where": …, "verdict": …}} — единички TK-680 на текущей базе
    for kind, fn, nk in (("engine", "engine.json", "key"), ("cols", "cols.json", "col")):
        for x in json.load(open(os.path.join(DIR, fn), encoding="utf-8")):
            name = x[nk]
            st = norm_status(x, kind)
            where = x.get("where_ru") or "—"
            verdict = x.get("verdict_ru") or "—"
            if kind == "engine" and name in ENGINE_FIX:
                where = ENGINE_FIX[name][1]
            if st == "never" and kind == "cols" and x["status"] == "ml_only" and where == "—":
                where = "только признак модели"
            if name in singles:
                st, where, verdict = "current", singles[name]["where"] + "; раньше: " + where, singles[name]["verdict"]
            out.append(dict(kind=kind, name=name, status=st, where=where, verdict=verdict, flags=flags(x)))
    return out


def counts(rs):
    c = {}
    for r in rs:
        c.setdefault(r["kind"], {}).setdefault(r["status"], 0)
        c[r["kind"]][r["status"]] += 1
    return c


def main():
    rs = rows()
    c = counts(rs)
    line = "; ".join("%s: %s" % ({"engine": "ключи движка", "cols": "столбцы таблицы"}[k], ", ".join("%s %d" % (RU[s], c[k].get(s, 0)) for s in RU)) for k in c)
    if "--counts" in sys.argv:
        print(line)
        return
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("# П-42: что титровалось (22.11.2026, TK-680)\n\n")
        f.write("Текущая база — h775, стоп Л 3 % / Ш 4 %. " + line[0].upper() + line[1:] + ". № — порядок описи `p42-features-2026-11-22.md`.\n\n")
        f.write("| № | Признак | Вид | Статус | Где титровалось | Вердикт | Пометки |\n|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(rs, 1):
            f.write("| %d | `%s` | %s | %s | %s | %s | %s |\n" % (i, r["name"], "ключ" if r["kind"] == "engine" else "столбец", RU[r["status"]],
                                                           r["where"].replace("|", "/"), r["verdict"].replace("|", "/"), ", ".join(r["flags"]) or "—"))
    print(line)


if __name__ == "__main__":
    main()
