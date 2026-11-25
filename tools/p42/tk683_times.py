#!/usr/bin/env python3
"""П-42, TK-683: время и память вызовов движка `breakout tick` по журналам партий (без счёта, только чтение).
  python3 tk683_times.py <каталог с log/cells-*.log> [...] [--since 2026-11-22T19:00] [--until …] [--settings <файл>] [--name <regexp имени журнала>]
Строка на каталог и настройку вызова (метка — из `--settings` или `<каталог>/log/settings683.tsv`: «UTC-время начала<TAB>метка», иначе «?»):
вызовов, вариант-окон, проходов сигналов (и сколько взято из кэша), секунды: загрузка / фаза 1 / разогрев / фаза 2 (стена) / всего, с на вариант-окно,
чтений суток, пик памяти. Разогрев и стена фазы 2 — из строк «разогрев суток сделок», «фаза 2: стена» (бинарь j и новее; раньше — 0).
Вариант-окно = одна клетка на одном окне и стороне. P&L не читается."""
from __future__ import annotations

import argparse
import datetime
import glob
import os
import re


def parse(path):
    s = open(path, encoding="utf-8", errors="replace").read()
    g = lambda pat: re.search(pat, s)
    a, b, c, d = g(r"загружено символов (\d+) за ([\d.]+)"), g(r"заданий сигналов (\d+)"), g(r"фаза 1 готова за ([\d.]+)"), g(r"готово: (\d+) вариантов, ([\d.]+) с")
    if not (a and b and c and d):
        return None
    hit = g(r"sig-cache: hit (\d+)") or g(r"кэш сигналов: hit (\d+)")
    peak = re.findall(r"память в конце: сейчас [\d.]+ ГБ, пик ([\d.]+)", s)
    warm = g(r"разогрев суток сделок: ([\d.]+) с")
    wall2 = g(r"фаза 2: стена ([\d.]+) с")
    reads = g(r"файлов прочитано (\d+)")
    hot = len(re.findall(r"^\[\d+/\d+\] \S*(?:e2t[1-9]|mk[1-9])", s, flags=re.M))
    return dict(load=float(a[2]), jobs=int(b[1]), ph1=float(c[1]) - float(a[2]), n=int(d[1]), total=float(d[2]), ph2=float(d[2]) - float(c[1]),
                hit=int(hit[1]) if hit else 0, peak=float(peak[-1]) if peak else 0.0, hot=hot, coins=int(a[1]), mtime=os.path.getmtime(path),
                warm=float(warm[1]) if warm else 0.0, wall2=float(wall2[1]) if wall2 else 0.0, reads=int(reads[1]) if reads else 0)


def setting(root, name, f=""):
    """Настройка вызова: файл `f` или `<root>/log/settings683.tsv` (строки «UTC-время начала<TAB>метка»), иначе «?»."""
    f = f or os.path.join(root, "log", "settings683.tsv")
    if not os.path.exists(f):
        return "?"
    rows = sorted((datetime.datetime.fromisoformat(x.split("\t")[0]).replace(tzinfo=datetime.timezone.utc).timestamp(), x.split("\t")[1].strip())
                  for x in open(f, encoding="utf-8") if "\t" in x)
    lab = "до " + rows[0][1] if rows else "?"
    for t0, lb in rows:
        if name >= t0:
            lab = lb
    return lab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--settings", default="")
    ap.add_argument("--name", default="", help="regexp имени журнала, например «-S-p» — тяжёлые партии стороны S")
    a = ap.parse_args()
    ts = lambda x: datetime.datetime.fromisoformat(x).replace(tzinfo=datetime.timezone.utc).timestamp()
    since, until = ts(a.since) if a.since else 0, ts(a.until) if a.until else float("inf")
    agg = {}
    for root in a.roots:
        for f in glob.glob(os.path.join(root, "log", "cells-*.log")):
            if a.name and not re.search(a.name, os.path.basename(f)):
                continue
            r = parse(f)
            if not r or r["mtime"] < since or r["mtime"] >= until:
                continue
            k = (os.path.basename(root.rstrip("/")), setting(root, r["mtime"] - r["total"], a.settings))
            x = agg.setdefault(k, dict(calls=0, n=0, jobs=0, hit=0, load=0.0, ph1=0.0, ph2=0.0, total=0.0, peak=0.0, hot=0, warm=0.0, wall2=0.0, reads=0))
            x["calls"] += 1
            for key in ("n", "jobs", "hit", "load", "ph1", "ph2", "total", "hot", "warm", "wall2", "reads"):
                x[key] += r[key]
            x["peak"] = max(x["peak"], r["peak"])
    print("| каталог | настройка | вызовов | вариант-окон | из них e2t/mk | проходов (из кэша) | загрузка, с | фаза 1, с | разогрев, с | фаза 2 стена, с | всего, с | с на вариант-окно | с на проход | чтений | пик, ГБ |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (root, lab), x in sorted(agg.items()):
        print("| %s | %s | %d | %d | %d | %d (%d) | %.0f | %.0f | %.0f | %.0f | %.0f | %.3f | %.2f | %d | %.1f |" % (
            root, lab, x["calls"], x["n"], x["hot"], x["jobs"], x["hit"], x["load"], x["ph1"], x["warm"], x["wall2"] or x["ph2"], x["total"],
            x["total"] / max(x["n"], 1), x["ph1"] / max(x["jobs"] - x["hit"], 1), x["reads"], x["peak"]))


if __name__ == "__main__":
    main()
