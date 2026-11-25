#!/usr/bin/env python3
"""П-42 TK-674: отбор кандидатов вперёд (≤ 2 на сторону) по предрегистрации отбора (поправка к PS-999 п. 5, §6.3 prereg-13.73) — из карт `winner` всех комбинаций пар / троек / четвёрок. Без БД и без счёта.
  python3 tk674_select.py --counts [--dir DIR]            — только числа пула (без Шарпа и $): сколько победителей, сколько снято исключениями, дублей, выше пола
  python3 tk674_select.py [--dir DIR] [--out sel.json]    — отбор: таблица кандидатов и верх ранга
Пул стороны — `winner` каждой комбинации карт: лонг `map674bsx-L` + `map674bs3u-L` + `map674bs4u-L`, шорт `map674bsx-S` + `map674bs3uf-S` + `map674bs4u3-S`.
Исключения (до ранга): победитель, чья клетка отличается от базы стороны по оси `bd_*` / `cc_*` или по f12-оси PS-999 п. 3. Дубль (одинаковый `tsig` — те же сделки) — одна клетка, с меньшим числом отличий от базы.
Пол: Шарп строго выше базы стороны и Σ не ниже базы. (1) — наибольший Шарп; разница ≤ 0,1 → меньший самый длинный перехай (`gap_longest`), затем меньшая просадка. (2) — то же среди клеток с Жаккаром осей ≤ 0,5 к (1) и другим `tsig`; нет такой — второго нет."""
from __future__ import annotations

import argparse
import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "..", "..", "docs", "findings", "p42-joint-search-2026-11-22")
MAPS = {"L": ("map674bsx-L", "map674bs3u-L", "map674bs4u-L"), "S": ("map674bsx-S", "map674bs3uf-S", "map674bs4u3-S")}
F12_AXES = ("age_h", "approach_dur_min", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h", "depth_min_bp", "depth_mean_bp", "since_touch_prior_h", "touch_prior_none")
EXCL_PREFIX = ("bd_", "cc_")
TIE = 0.1
JACCARD_MAX = 0.5
STAKE, MARGIN = 500.0, 250.0
_TERM = re.compile(r"(.+?)(<=|>=|<|>|==)(.*)$")


def parse_ckey(ck):
    """Ключ клетки → {ключ: значение}; термы `F=a<=x&b>=y` разворачиваются в `F:a<=` / `F:b>=` со значением порога."""
    out = {}
    for t in ck.split(" "):
        if t.startswith("F="):
            for term in t[2:].split("&"):
                m = _TERM.match(term)
                out["F:" + (m.group(1) + m.group(2) if m else term)] = m.group(3) if m else ""
        elif "=" in t:
            k, v = t.split("=", 1)
            out[k] = v
    return out


def axes_of(ck, base_ck):
    """Заплатка клетки к базе стороны: ключи (оси), значение которых отличается от базы или которых нет в одной из двух."""
    c, b = parse_ckey(ck), parse_ckey(base_ck)
    return frozenset(k for k in set(c) | set(b) if c.get(k) != b.get(k))


def axis_name(axis):
    """`F:bd_thin5<=` → `bd_thin5`; ось-ключ движка (`q`, `tf`) — как есть."""
    return axis[2:].rstrip("<>=") if axis.startswith("F:") else axis


def excluded_axis(name):
    base = name.split(".")[0]
    return base in F12_AXES or base.startswith(EXCL_PREFIX)


def is_excluded(w, base_ck):
    """Клетка с осью bd_* / cc_* или f12 (по ключу клетки и по токенам cid)."""
    if any(excluded_axis(axis_name(a)) for a in axes_of(w["ckey"], base_ck)):
        return True
    return any(excluded_axis(tok.split("_q")[0].split("@")[0]) for tok in w["cid"].split("+"))


def jaccard(a, b):
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def pick(cands, tie=TIE):
    """Правило §6.1: Шарп в пределах `tie` от наибольшего → меньший `gap_longest`, затем меньшая просадка, затем больший Шарп."""
    if not cands:
        return None
    top = max(c["w"]["sharpe"] for c in cands)
    near = [c for c in cands if c["w"]["sharpe"] >= top - tie - 1e-12]
    return min(near, key=lambda c: (c["w"]["gap_longest"], c["w"]["maxdd"], -c["w"]["sharpe"]))


def load_pool(side, d):
    """[(карта, строка карты, база)] по трём картам стороны; база стороны — одна и та же во всех картах."""
    pool, base, last = [], None, None
    for name in MAPS[side]:
        m = json.load(open(os.path.join(d, name + ".json"), encoding="utf-8"))
        if base is None:
            base = m["base"]
        assert m["base"]["ckey"] == base["ckey"], (name, "база стороны отличается")
        for p in m["pairs"]:
            if p.get("winner"):
                pool.append(dict(src=name, pair=p["pair"], w=p["winner"]))
        last = m
    return pool, base, last


def build(side, d):
    pool, base, last = load_pool(side, d)
    st = dict(winners=len(pool), by_map={n: sum(1 for c in pool if c["src"] == n) for n in MAPS[side]})
    for c in pool:
        c["axes"] = axes_of(c["w"]["ckey"], base["ckey"])
    kept = [c for c in pool if not is_excluded(c["w"], base["ckey"])]
    st["excluded_axes"] = len(pool) - len(kept)
    best = {}                                                        # дубль по `tsig`: одна клетка с меньшим числом отличий от базы, затем по ключу (детерминированно)
    for c in kept:
        k = c["w"]["tsig"] or c["w"]["ckey"]
        if k not in best or (len(c["axes"]), c["w"]["ckey"]) < (len(best[k]["axes"]), best[k]["w"]["ckey"]):
            best[k] = c
    dedup = list(best.values())
    st["duplicates"] = len(kept) - len(dedup)
    up = [c for c in dedup if c["w"]["sharpe"] > base["sharpe"] and c["w"]["usd"] >= base["usd"]]
    st["distinct"], st["above_floor"] = len(dedup), len(up)
    first = pick(up)
    second = None
    if first:
        rest = [c for c in up if jaccard(c["axes"], first["axes"]) <= JACCARD_MAX and c["w"]["tsig"] != first["w"]["tsig"]]
        second = pick(rest)
        st["second_pool"] = len(rest)
    noise = last["sharpe_sd"] * math.sqrt(2 * math.log(last["m"]))
    return dict(side=side, base=base, stats=st, up=up, first=first, second=second, noise=noise, m=last["m"], sigma=last["sharpe_sd"])


def line(c, base, noise, tag):
    w = c["w"]
    return ("%s %s [%s / %s]\n    сд. %d · Σ %+.0f $ (%+.2f $/сд.) · Шарп %.2f (+%.2f к базе %.2f, шум %.2f) · просадка %.0f $ (%.0f %% маржи) · перехай %d / %.1f сут. · R:R %.2f · стоп-убыток %.1f $, худшая %.1f $ · плато %.2f · осей %d" % (
        tag, w["cid"], c["src"], c["pair"], w["n"], w["usd"], w["mean_usd"], w["sharpe"], w["sharpe"] - base["sharpe"], base["sharpe"], noise, w["maxdd"], 100 * w["maxdd"] / MARGIN, w["gap_longest"],
        w["gap_mean"], w["rr"], w["lstop"], w["worst"], w["nb_mean"] or 0, len(c["axes"])))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=DIR)
    ap.add_argument("--counts", action="store_true", help="только числа пула, без Шарпа и $")
    ap.add_argument("--out", default=None)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    res = {}
    for side in "LS":
        r = build(side, a.dir)
        res[side] = r
        st, b = r["stats"], r["base"]
        print("== %s: победителей в пуле %d (%s); без осей bd_* / cc_* / f12 −%d; дублей по tsig −%d; различных %d; выше пола %d; m %d, σ %.3f, шум %.2f" % (
            side, st["winners"], ", ".join("%s %d" % kv for kv in st["by_map"].items()), st["excluded_axes"], st["duplicates"], st["distinct"], st["above_floor"], r["m"], r["sigma"], r["noise"]))
        if a.counts:
            continue
        print("   база: сд. %d · Σ %+.0f $ · Шарп %.2f · просадка %.0f $ · перехай %d · R:R %.2f" % (b["n"], b["usd"], b["sharpe"], b["maxdd"], b["gap_longest"] if "gap_longest" in b else b["kpi"]["gap_longest"], b["rr"]))
        top = sorted(r["up"], key=lambda c: -c["w"]["sharpe"])[:a.top]
        for i, c in enumerate(top, 1):
            print("   #%d %s Шарп %.2f Σ %+.0f $ перехай %d просадка %.0f $ осей %d [%s]" % (i, c["w"]["cid"], c["w"]["sharpe"], c["w"]["usd"], c["w"]["gap_longest"], c["w"]["maxdd"], len(c["axes"]), c["src"]))
        for tag, c in (("(1)", r["first"]), ("(2)", r["second"])):
            print(line(c, b, r["noise"], tag) if c else "%s кандидата нет" % tag)
            if c and tag == "(2)":
                print("    Жаккар осей к (1): %.2f" % jaccard(c["axes"], r["first"]["axes"]))
    if a.out:
        out = {s: dict(base=r["base"], stats=r["stats"], noise=r["noise"], m=r["m"], sigma=r["sigma"],
                       candidates=[dict(rank=i, src=c["src"], pair=c["pair"], axes=sorted(c["axes"]), cell=c["w"]) for i, c in enumerate((r["first"], r["second"]), 1) if c]) for s, r in res.items()}
        json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("записано:", a.out)


if __name__ == "__main__":
    main()
