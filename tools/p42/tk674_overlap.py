#!/usr/bin/env python3
"""TK-674 шаг 0: какие сделки меняет каждый кандидат против базы (по ГОТОВЫМ сделкам клеток, движок не запускался, В-213) и как пересекаются
наборы «затронутых» сделок у пар кандидатов. Затронутая сделка = есть только в базе, только в клетке или P&L отличается > 1e-6.
Пересечение пары = |A_i ∩ A_j| / min(|A_i|, |A_j|). Малое пересечение = рычаги бьют по разным сделкам (кандидат в пару).
Запуск на srv: python3 tk674_overlap.py <код TK-673 (tk604_stats, tk625_run, tk638_post)> <каталог выхода>"""
import json
import os
import sys

import numpy as np

code, out = sys.argv[1:3]
sys.path.insert(0, code)
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk638_post as p8  # noqa: E402

u = r5.Uni("h775")
W = [w.name for w in st.TRADE_WINDOWS]
gmk = "/data/p42-tk626/gmk/out"
C = lambda tk, cid, side, sub="cells": (lambda w: "/data/p42-tk%d/%s/%s-%s/%s" % (tk, sub, w, side, cid))  # noqa: E731
CAND = {
    "S": dict(base=lambda w: "/data/p42-tk621/out/all_t24/%s/S_base" % w,
              n_bounce1=C(657, "A_n_1", "S"), k1=C(657, "A_k_1", "S"), tf30=C(657, "A_tf_30", "S"), d1=C(657, "A_d_1", "S"),
              rt_off=C(643, "B_off_tt20_tb0.1", "S"), s8=C(643, "A_s8_none", "S"), natr_p15=C(650, "N_p15", "S"), take_g05_k100=C(650, "G_g0.5_k100", "S"),
              TPM60_le_q80=C(654, "TPM60_le_q80", "S", "cells54"), CN60_le_q80=C(654, "CN60_le_q80", "S", "cells54"), ZV6_dense=C(654, "ZV6_le_q20", "S", "cells54"),
              mk1=lambda w: os.path.join(gmk, w, "cd0838447e287"), D_S=lambda w: "/data/p42-tk668/e4/cells/%s/S_lx1" % w),
    "L": dict(base=lambda w: "/data/p42-tk621/out/all_t24/%s/L_base" % w,
              s3=C(643, "A_s3_none", "L"), s3_nx5=C(643, "A_s3_c5", "L"), e01=C(655, "E_e0.1", "L"), tu240=C(649, "tu240", "L"), take_g025_k100=C(650, "G_g0.25_k100", "L"),
              tl2=C(652, "tl2_rr2", "L"), SHARP=C(654, "SHARP", "L", "cells54"), CN60_le_q20=C(654, "CN60_le_q20", "L", "cells54"), ZV6_dense=C(654, "ZV6_le_q20", "L", "cells54"),
              TPM60_le_q80=C(654, "TPM60_le_q80", "L", "cells54"),
              n24=lambda w: p8.kind_dir(u, "ladd", w, "L", "N24_q60"), mk2=lambda w: os.path.join(gmk, w, "cf280a1e8fc7c")),
}


def load(side, f):
    d = {w: (f(w), side) for w in W}
    try:
        ser, kinds, pnl = p8.load_cell(u, side, d)
    except st.GuardError:      # G7 §13.43 (как tk663_shape.load_lines для A_n_1): сверка Σ daily ≠ Σ net у клеток с n_bounce — склейка без daily
        tables, _ = p8.read_tables(d)
        ser = st.splice(tables, u.coins(), daily=None)
        kinds, pnl = p8.kinds_pnl(tables)
    return {(str(s), int(t)): (float(v), str(k)) for s, t, v, k in zip(ser.sym_name, np.asarray(ser.t_in, np.int64), ser.val, kinds)}


res = {}
for side in "LS":
    T = {k: load(side, f) for k, f in CAND[side].items()}
    b = T.pop("base")
    A, info = {}, {}
    for k, t in T.items():
        keys = set(b) | set(t)
        aff = {x for x in keys if (x in b) != (x in t) or (x in b and x in t and abs(b[x][0] - t[x][0]) > 1e-6)}
        A[k] = aff
        info[k] = dict(n=len(t), d_n=len(t) - len(b), affected=len(aff), only_cell=len(set(t) - set(b)), only_base=len(set(b) - set(t)),
                       d_sum=sum(v[0] for v in t.values()) - sum(v[0] for v in b.values()))
    names = list(T)
    M = {x: {y: (len(A[x] & A[y]) / max(1, min(len(A[x]), len(A[y])))) for y in names} for x in names}
    res[side] = dict(base_n=len(b), info=info, overlap=M)
    print("== %s base n=%d" % (side, len(b)))
    for k in names:
        i = info[k]
        print("%-16s n=%d (%+d) affected=%d (+%d/-%d) dΣ=%+.0f" % (k, i["n"], i["d_n"], i["affected"], i["only_cell"], i["only_base"], i["d_sum"]))
    print("overlap (share of the smaller affected set):")
    print(" " * 16 + " ".join("%5s" % n[:5] for n in names))
    for x in names:
        print("%-16s" % x + " ".join("%5.2f" % M[x][y] for y in names))
os.makedirs(out, exist_ok=True)
json.dump(res, open(os.path.join(out, "overlap.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
