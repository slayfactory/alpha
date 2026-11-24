#!/usr/bin/env python3
"""П-42, TK-697 D: дымовой прогон новых ключей движка на настоящем бинаре (после гейта «байт в байт» при выключенных ключах, `tk688_gate.py`): окно wa обеих сторон, якорь стороны,
база + `tn` (№ 9, тейк у следующего уровня) и `lx = 4` (№ 1, наклонная линия). Печатает число сделок без P&L; сделок должно быть > 0, ключ меняет набор сделок (≠ базы) или молчит по причине.
  TK674_UNI=c539 TK674_ROOT=/data/p42-tk697g TK674_BIN=<новый бинарь> TK674_CAP_GB=2 python3 tk697_smoke_d.py [--code /data/p42-tk674c/code]
Итог — <ROOT>/res/smoke697d.json, код возврата 1, если у какого-то варианта нет сделок или движок упал."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--code", default="/data/p42-tk674c/code")
ap.add_argument("--anchors", default="/data/p42-tk674c")
A = ap.parse_args()
sys.path.insert(0, A.code)
import tk674_run as m  # noqa: E402

t, say = m.t, m.say
KEYS = [("tn50", "tn=50"), ("tn100", "tn=100"), ("tn50o", "tn=50 tno=0.1"), ("lx4k2", "lx=4 lk=2 ld=7"), ("lx4k3", "lx=4 lk=3 ld=14 lsl=2")]


def base_line(side):
    vf = sorted(glob.glob(os.path.join(A.anchors, "cells", "wa-" + side, "p*.variants.txt")))[0]
    return next(x.rstrip("\n").split(" ", 1)[1] for x in open(vf, encoding="utf-8") if x.strip())


def main():
    st, r5, _, _ = t._srv()
    m.engine_opts(r5)
    import tk604_run as r4
    r4.BIN = m.BIN
    uc = r5.Uni("c539")
    w = {x.name: x for x in st.TRADE_WINDOWS}["wa"]
    rows, bad = [], []
    for side in m.SIDES:
        base = base_line(side)
        vs = [("base", base)] + [(cid, base + " " + k) for cid, k in KEYS]
        out = os.path.join(m.ROOT, "gate", "smoke697-wa-" + side)
        r5.tick(uc, w, vs, out, m.one_side_env(side), t.q("log", "smoke697d-%s.log" % side))
        for cid, _ in vs:
            f = os.path.join(out, cid, "%s-trades.csv" % m.ENGNAME[side])
            n = sum(1 for _ in open(f, encoding="utf-8")) - 1 if os.path.exists(f) else -1
            rows.append(dict(side=side, cid=cid, trades=n))
            if n <= 0:
                bad.append("%s %s: сделок %d" % (side, cid, n))
            say("smoke697 %s %s: сделок %d" % (side, cid, n))
    json.dump(rows, open(t.q("res", "smoke697d.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if bad:
        say("smoke697: ПУСТО / ОШИБКА — " + "; ".join(bad))
        sys.exit(1)


if __name__ == "__main__":
    main()
