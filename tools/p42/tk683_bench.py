#!/usr/bin/env python3
"""П-42, TK-683: время одного настоящего вызова движка (партия карты целиком) на нескольких бинарях — один вход, по очереди.
  TK674_UNI=c539 TK674_ROOT=/data/p42-tk683 python3 tk683_bench.py --code /data/p42-tk674c/code --root /data/p42-tk674c --ws wc-L --batch p68c2b7c5aa \
      --bins <бинарь A> <бинарь B> … [--threads 4] [--cap-gb 2] [--reps 2]
Каждый бинарь считает все посчитанные клетки партии в <ROOT>/bench/<партия>/<имя бинаря>; каталоги клеток сверяются с посчитанными (`cells`).
Печатает: бинарь, повтор, стена вызова, «фаза 1 готова за», «готово … файлов прочитано», различий с посчитанным. P&L не печатается."""
from __future__ import annotations

import argparse
import filecmp
import os
import re
import sys
import time

AP = argparse.ArgumentParser()
AP.add_argument("--sig-cache", default="", help="каталог кэша сигналов движка (`--sig-cache`; в нём же файлы ступеней НОД)")
AP.add_argument("--code", default=os.path.dirname(os.path.abspath(__file__)))
AP.add_argument("--root", required=True, help="каталог карты с посчитанными клетками")
AP.add_argument("--ws", required=True, help="окно-сторона, например wc-L")
AP.add_argument("--batch", required=True, nargs="+", help="несколько партий — один вызов движка на все их клетки")
AP.add_argument("--bins", nargs="+", required=True)
AP.add_argument("--threads", type=int, default=4)
AP.add_argument("--cap-gb", type=int, default=2)
AP.add_argument("--reps", type=int, default=2)
A = AP.parse_args()
sys.path.insert(0, A.code)
import tk674_run as m  # noqa: E402

t = m.t


def main():
    st, r5, _, _ = t._srv()
    import tk604_run as r4
    r4.THREADS = A.threads
    r5.TICK_STORE = list(r5.TICK_STORE) + ["--cap-gb", str(A.cap_gb)]
    if A.sig_cache:
        r5.TICK_STORE += ["--sig-cache", A.sig_cache]
    wname, side = A.ws.split("-")
    w = next(x for x in st.TRADE_WINDOWS if x.name == wname)
    u = r5.Uni(m.UNI)
    vs, olds = [], {}
    for bt in A.batch:
        old = os.path.join(A.root, "cells", A.ws, bt)
        for x in open(old + ".variants.txt", encoding="utf-8"):
            v = tuple(x.rstrip("\n").split(" ", 1))
            if x.strip() and v[0] not in olds and os.path.isdir(os.path.join(old, v[0])):
                vs.append(v)
                olds[v[0]] = old
    tag = A.batch[0] if len(A.batch) == 1 else "%s+%d" % (A.batch[0], len(A.batch) - 1)
    print("партия %s %s: клеток %d, потоков %d, cap-gb %d" % (A.ws, " ".join(A.batch), len(vs), A.threads, A.cap_gb), flush=True)
    for rep in range(A.reps):
        for b in A.bins:
            name = os.path.basename(b)
            out = os.path.join(m.ROOT, "bench", tag, name)
            log = t.q("log", "bench-%s-%s.log" % (tag, name))
            r4.BIN = b
            t0 = time.time()
            r5.tick(u, w, vs, out, m.one_side_env(side), log)
            wall = time.time() - t0
            txt = open(log, encoding="utf-8", errors="replace").read()
            p1 = re.findall(r"фаза 1 готова за ([0-9.]+)", txt)
            warm = re.findall(r"разогрев суток сделок: ([0-9.]+)", txt)
            fin = re.findall(r"готово: \d+ вариантов, ([0-9.]+) с; файлов прочитано (\d+)", txt)
            bad = 0
            for cid, _ in vs:
                for f in os.listdir(os.path.join(olds[cid], cid)):
                    nf = os.path.join(out, cid, f)
                    bad += not (os.path.exists(nf) and filecmp.cmp(os.path.join(olds[cid], cid, f), nf, shallow=False))
            print("%-34s повтор %d: стена %.1f с, фаза 1 к %s с, разогрев %s с, готово %s с, файлов прочитано %s, различий %d" % (
                name, rep + 1, wall, p1[-1] if p1 else "?", warm[-1] if warm else "-", fin[-1][0] if fin else "?", fin[-1][1] if fin else "?", bad), flush=True)


if __name__ == "__main__":
    main()
