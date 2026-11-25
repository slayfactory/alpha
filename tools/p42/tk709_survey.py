#!/usr/bin/env python3
"""TK-709: покрытие данных для «премия ≤ −X % → покупка» — файлы премии (`/data/p42-premium`) и склада тиков (`/data/p42-tk599/tick-store`) по c539 × месяц.
Только имена файлов (ls), содержимое не читается. Запуск на srv: python3 tk709_survey.py <выход.json>"""
import collections
import json
import os
import sys

SYMS = [s for s in open("/data/p42-tk621/universe/symbols-crypto.txt", encoding="utf-8").read().replace(",", "\n").split() if s]
PREM, TICK = "/data/p42-premium", "/data/p42-tk599/tick-store"


def days(root, sym, pre, suf):
    try:
        names = os.listdir(os.path.join(root, sym))
    except FileNotFoundError:
        return set()
    out = set()
    for n in names:
        if n.startswith(pre) and n.endswith(suf):
            out.add(n[len(pre):-len(suf)])
    return out


def main():
    prem, tick = {}, {}
    for s in SYMS:
        prem[s] = days(PREM, s, s + "-1m-", ".zip")
        tick[s] = days(TICK, s, s + "-", ".tk")
    rep = {"symbols": len(SYMS), "prem_syms": sum(1 for s in SYMS if prem[s]), "tick_syms": sum(1 for s in SYMS if tick[s])}
    m_p, m_t, m_b = collections.Counter(), collections.Counter(), collections.Counter()
    for s in SYMS:
        for d in prem[s]:
            m_p[d[:7]] += 1
        for d in tick[s]:
            m_t[d[:7]] += 1
        for d in prem[s] & tick[s]:
            m_b[d[:7]] += 1
    rep["prem_by_month"], rep["tick_by_month"], rep["both_by_month"] = dict(sorted(m_p.items())), dict(sorted(m_t.items())), dict(sorted(m_b.items()))
    rep["prem_range"] = [min(min(v) for v in prem.values() if v), max(max(v) for v in prem.values() if v)]
    rep["tick_range"] = [min(min(v) for v in tick.values() if v), max(max(v) for v in tick.values() if v)]
    rep["no_tick"] = sorted(s for s in SYMS if not tick[s])[:40]
    rep["no_prem"] = sorted(s for s in SYMS if not prem[s])[:40]
    json.dump(rep, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)


main()
