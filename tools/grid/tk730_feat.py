"""TK-730 §6.2: признаки режима по монетам и неделям (7 из 8; восьмой — P&L прошлой недели — берётся из суточных рядов ботов).

    python tk730_feat.py --out /data/tk730 --procs 4
Результат — feat/<SYM>.npz: f (NW, 7) в порядке chop, vr, hl, cover, turn, beta, trend (строго по минуткам до начала недели). Загрузка и ou_rolling — те же функции, что у ботов (tk729_lib).
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
import tk730_lib as W  # noqa: E402

_BTC = {}


def btc_ln() -> np.ndarray:
    if "x" not in _BTC:
        _BTC["x"] = W.ln_ffill(L.load_coin("BTCUSDT"))
    return _BTC["x"]


def process(args):
    sym, out = args
    dst = os.path.join(out, "feat", sym + ".npz")
    if os.path.exists(dst):
        return sym, 0.0
    t0 = time.time()
    S = L.load_coin(sym)
    ou = L.ou_rolling(S, 1440)
    lnc = W.ln_ffill(S)
    lb = btc_ln()
    f = np.stack([W.week_features(S, ou, lb, k, lnc) for k in range(W.NW)])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp.npz"
    np.savez(tmp, f=f)
    os.replace(tmp, dst)
    return sym, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/data/tk730")
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    syms = L.read_syms()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    done = 0
    with mp.Pool(a.procs) as pool:
        for sym, dt in pool.imap_unordered(process, [(s, a.out) for s in syms]):
            done += 1
            with open(os.path.join(a.out, "progress-feat.txt"), "w") as fh:
                fh.write("%d/%d %.0f%% %s %.1fs elapsed %.0fs\n" % (done, len(syms), 100.0 * done / len(syms), sym, dt, time.time() - t0))
    print("готово", done, "монет за", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
