#!/usr/bin/env python3
"""П-42 TK-674: правило перехода ступени k → k+1 четвёрок (PS-997 разд. 5, PS-999 п. 2) по карте объединения: для каждой ступени — доля четвёрок с `gain_sub` > 0 в первой и последней трети её рангов (Б),
лучшая охранённая клетка четвёрок ступени (таблица А: `winner`, Б: `both_moved`) и её прирост к якорю (А против шума карты); (В) — лучшая четвёрка ступени против лучшей четвёрки прежних ступеней. Без БД и без счёта.
  python3 tk674_stage_read.py <map674*-S.json> <level674*u-S.json> <число четвёрок ступени 1> [--anchor 3.63]
Порядок `pairs` в карте = порядок `taken` в файле объединения (проверяется по баллу отбора)."""
from __future__ import annotations

import argparse
import json


def stage_stats(pairs, lo, hi, anchor, noise):
    ps = pairs[lo:hi]
    n = len(ps)
    third = n // 3
    def share(chunk):
        a = sum(1 for p in chunk if p["both_moved"] and p["both_moved"].get("gain_sub") is not None and p["both_moved"]["gain_sub"] > 0)
        return a, len(chunk)
    first, last = share(ps[:third]), share(ps[n - third:])
    def best(key):
        c = [(p[key]["sharpe"], p["pair"], p[key]) for p in ps if p.get(key) and p[key].get("sharpe") is not None]
        return max(c, key=lambda x: x[0]) if c else None
    bw, bb = best("winner"), best("both_moved")
    return dict(quads=n, third=third, first=first, last=last, ratio=(last[0] / last[1]) / (first[0] / first[1]) if first[0] else None,
                best_a=bw, best_b=bb, gain_a=(bw[0] - anchor) if bw else None, above_noise=(bw[0] - anchor >= noise) if bw else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("map")
    ap.add_argument("level")
    ap.add_argument("n1", type=int)
    ap.add_argument("--anchor", type=float, default=3.63)
    a = ap.parse_args()
    m = json.load(open(a.map, encoding="utf-8"))
    lv = json.load(open(a.level, encoding="utf-8"))
    pairs, taken = m["pairs"], lv["taken"]
    assert len(pairs) == len(taken), (len(pairs), len(taken))
    bad = sum(1 for p, t in zip(pairs, taken) if abs(p["score"] - t["score"]) > 1e-9)
    print("карта: четвёрок %d, клеток %d, m %d, σ %.3f, шум %.2f; порядок карты = порядок отбора: расхождений %d" % (len(pairs), m["n_cells"], m["m"], m["sharpe_sd"], m["noise_max"], bad))
    noise = m["noise_max"]
    s1 = stage_stats(pairs, 0, a.n1, a.anchor, noise)
    s2 = stage_stats(pairs, a.n1, len(pairs), a.anchor, noise)
    for nm, s in (("ступень 1", s1), ("ступень 2", s2)):
        print("== %s: четвёрок %d; Б: доля gain_sub>0 первой трети %d/%d = %.1f %%, последней %d/%d = %.1f %%, отношение %.2f; лучшая строка А: Шарп %.2f (%s, прирост к якорю %+.2f, шум %.2f → %s); лучшая строка Б: Шарп %.2f" % (
            nm, s["quads"], s["first"][0], s["first"][1], 100 * s["first"][0] / s["first"][1], s["last"][0], s["last"][1], 100 * s["last"][0] / s["last"][1], s["ratio"] or 0,
            s["best_a"][0], s["best_a"][1], s["gain_a"], noise, "выше шума" if s["above_noise"] else "нет", s["best_b"][0]))
        print("   клетка А: %s; сд. %s, Σ %.0f $, просадка %.0f $ (%.0f %%)" % (s["best_a"][2]["cid"], s["best_a"][2]["n"], s["best_a"][2]["usd"], s["best_a"][2]["maxdd"], s["best_a"][2]["maxdd_pct"]))
    print("(В) лучшая четвёрка ступени 2 %.2f против ступени 1 %.2f: %s (разница %+.2f)" % (s2["best_a"][0], s1["best_a"][0], "да" if s2["best_a"][0] > s1["best_a"][0] else "нет", s2["best_a"][0] - s1["best_a"][0]))


if __name__ == "__main__":
    main()
