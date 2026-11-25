#!/usr/bin/env python3
"""П-42, TK-706: стадии вызовов движка `breakout tick` по журналам партий (только чтение, без счёта).
  python3 tk706_logs.py <каталог log> [--since 2026-11-24T18:25] [--name <regexp>] [--json out.json]
Строка на вызов: монеты, заданий сигналов (из кэша / посчитано), варианты, секунды: минутки / фаза 1 / разогрев / фаза 2 / хвост,
поток-секунды фазы 2 (варианты, разбор суток, ожидание суток и разрешения, запись), чтения суток, пик памяти. Итог — по классам вызовов
(лёгкий < 20 с, средний 20–60 с, тяжёлый ≥ 60 с) и по всем."""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import sys


def parse(path):
    s = open(path, encoding="utf-8", errors="replace").read()
    g = lambda pat: re.search(pat, s)
    a, j, c, d = g(r"загружено символов (\d+) за ([\d.]+)"), g(r"заданий сигналов (\d+)"), g(r"фаза 1 готова за ([\d.]+)"), g(r"готово: (\d+) вариантов, ([\d.]+) с; файлов прочитано (\d+)")
    if not (a and j and c and d):
        return None
    sc = g(r"sig-cache: hit (\d+) miss (\d+)")
    warm = g(r"разогрев суток сделок: ([\d.]+) с")
    w2 = g(r"фаза 2: стена ([\d.]+) с; по потокам, с: варианты \(портфель и исходы, с сутками\) ([\d.]+), из них разбор суток ([\d.]+) и ожидание суток и разрешения ([\d.]+); запись основных файлов ([\d.]+)")
    peak = re.findall(r"память в конце: сейчас [\d.]+ ГБ, пик ([\d.]+)", s)
    f = lambda m, i: float(m[i]) if m else 0.0
    r = dict(name=os.path.basename(path)[6:-4], mtime=os.path.getmtime(path), coins=int(a[1]), load=float(a[2]), jobs=int(j[1]),
             hit=int(sc[1]) if sc else 0, miss=int(sc[2]) if sc else 0, n=int(d[1]), total=float(d[2]), reads=int(d[3]),
             ph1=float(c[1]) - float(a[2]), warm=f(warm, 1), peak=float(peak[-1]) if peak else 0.0)
    r["ph2"] = f(w2, 1)
    r["thr_var"], r["thr_parse"], r["thr_wait"], r["thr_write"] = f(w2, 2), f(w2, 3), f(w2, 4), f(w2, 5)
    r["tail"] = r["total"] - float(c[1]) - r["warm"] - r["ph2"]
    return r


def cls(r):
    return "лёгкий" if r["total"] < 20 else "средний" if r["total"] < 60 else "тяжёлый"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    ts = lambda x: datetime.datetime.fromisoformat(x).replace(tzinfo=datetime.timezone.utc).timestamp()
    t_lo, t_hi = (ts(a.since) if a.since else 0), (ts(a.until) if a.until else float("inf"))
    rows = []
    for p in glob.glob(os.path.join(a.log, "cells-*.log")):
        if a.name and not re.search(a.name, os.path.basename(p)):
            continue
        if not t_lo <= os.path.getmtime(p) <= t_hi:
            continue
        r = parse(p)
        if r:
            rows.append(r)
    rows.sort(key=lambda r: r["mtime"])
    if a.json:
        json.dump(rows, open(a.json, "w"))
    cols = ["load", "ph1", "warm", "ph2", "tail", "total"]
    print(f"{'класс':8} {'вызовов':>7} {'вариантов':>9} {'проходов hit/miss':>18} " + " ".join(f"{c:>8}" for c in cols) + f" {'чтений':>8} {'пик ГБ':>7} {'Σпот.вар':>9} {'разбор':>8} {'ожид.':>8} {'запись':>7}")
    for k in ("лёгкий", "средний", "тяжёлый", "все"):
        rs = [r for r in rows if k == "все" or cls(r) == k]
        if not rs:
            continue
        S = lambda key: sum(r[key] for r in rs)
        T = S("total")
        print(f"{k:8} {len(rs):7d} {S('n'):9d} {S('hit'):>8d}/{S('miss'):<9d} " + " ".join(f"{S(c):8.0f}" for c in cols)
              + f" {S('reads'):8d} {max(r['peak'] for r in rs):7.1f} {S('thr_var'):9.0f} {S('thr_parse'):8.0f} {S('thr_wait'):8.0f} {S('thr_write'):7.0f}")
        print(f"{'  доля':8} {'':7} {'':9} {'':18} " + " ".join(f"{100 * S(c) / T:7.1f}%" for c in cols))
    return 0


if __name__ == "__main__":
    sys.exit(main())
