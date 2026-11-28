"""TK-731 (П-TK729 §8): прогон 9 вариантов (C0 = ES(10) с журналом циклов + 8 из §8) по монетам c539 на srv.

    python tk731_run.py --out /data/tk731 --procs 4 [--only SYM,SYM] [--limit N]
Результат — res/<SYM>.npz: runs (имена), out (runs, NR2, 328), hist_idx / hist_n / hist_s (разреженные гистограммы убытка на стоп: вид × половина × бин), present, turn, natr (из res/base TK-729 — не пересчитываются).
Тик цены (tick_days) и загрузка — функции tk729_lib. C0 из TK-729 (`/data/tk729/res/base`) читается анализом для парной разницы; здесь C0 — только журнал циклов и сверка Σ с TK-729 до цента.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402
import tk731_lib as W  # noqa: E402

B729 = "/data/tk729/res/base"


def process(args):
    sym, out = args
    dst = os.path.join(out, "res", sym + ".npz")
    if os.path.exists(dst):
        return sym, 0.0, "skip"
    t0 = time.time()
    S = L.load_coin(sym)
    tick = L.tick_days(S)
    vs = W.variants()
    res = np.zeros((len(vs), W.NR2, L.NOUT))
    hidx, hn_, hs_ = [], [], []
    for i, v in enumerate(vs):
        o, hn, hs, dbg = W.run_rr(S, tick, W.make_params(**v["P"]))
        assert dbg[0] == 0, (sym, v["name"], "переполнение буфера событий")
        res[i] = o
        nz = np.flatnonzero(hn.reshape(-1))
        hidx.append(nz.astype(np.int64) + i * hn.size)
        hn_.append(hn.reshape(-1)[nz])
        hs_.append(hs.reshape(-1)[nz])
    b = np.load(os.path.join(B729, sym + ".npz"), allow_pickle=False)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp.npz"
    np.savez(tmp, runs=np.array([v["name"] for v in vs]), out=res, hist_idx=np.concatenate(hidx), hist_n=np.concatenate(hn_), hist_s=np.concatenate(hs_),
             present=b["present"], turn=float(b["turn"]), natr=float(b["natr"]))
    os.replace(tmp, dst)
    return sym, time.time() - t0, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/data/tk731")
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    syms = L.read_syms()
    if a.only:
        syms = [s for s in syms if s in set(a.only.split(","))]
    if a.limit:
        syms = syms[: a.limit]
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    done = 0
    with mp.Pool(a.procs) as pool:
        for sym, dt, st in pool.imap_unordered(process, [(s, a.out) for s in syms]):
            done += 1
            with open(os.path.join(a.out, "progress-run.txt"), "w") as f:
                f.write("%d/%d %.0f%% %s %.1fs elapsed %.0fs\n" % (done, len(syms), 100.0 * done / len(syms), sym, dt, time.time() - t0))
    open(os.path.join(a.out, "DONE-run"), "w").write("%d монет за %.0f с\n" % (done, time.time() - t0))
    print("готово", done, "монет за", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
