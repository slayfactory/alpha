#!/usr/bin/env python3
"""П-42 этап A, шаг 4а (TK-728, §13.78.4): тест на заглядывание семейства bk (`tk671_bk`, bk20/40/60) на K случайных строках — у ворот TK-671 для bk теста усечения нет. srv.
Строка (монета, момент сигнала t): признак на полной истории эпизодов = признак на истории, усечённой моментом t (только эпизоды с закрытием ≤ t), = признак, когда исходы эпизодов, закрывающихся после t, заменены случайными 0 / 1.
Контроль силы теста: замена исходов эпизодов, закрывшихся ДО t, меняет признак хотя бы у одной строки. Любое расхождение первых двух = заглядывание.
  python3 tk728_causal_bk.py --rows <rows.parquet> --win wb --side long --k 2000 --out <json>"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_feat as tf  # noqa: E402
import tk671_bk as BK  # noqa: E402
import tk728_lib as L  # noqa: E402


def feats(eps_sub, side, sym, t):
    hist = tf.episode_history(eps_sub, side)
    n, k, bk = tf.features(hist, np.array([sym], dtype=object), np.array([t], dtype=np.int64))
    return int(n[0]), int(k[0]), [float(bk[K][0]) for K in tf.KS]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True)
    ap.add_argument("--side", required=True)
    ap.add_argument("--k", type=int, default=2000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t0 = int(pd.Timestamp({"wa": "2026-06-17", "wb": "2026-07-25", "wc": "2026-09-01"}[a.win], tz="UTC").timestamp() * 1000)
    rows = pd.read_parquet(a.rows, columns=["sym", "sig_ms"])
    rng = np.random.default_rng(L.SEED)
    pick = rng.choice(len(rows), size=min(a.k, len(rows)), replace=False)
    eps = BK.load()
    nm = {}
    for f in ("sym", "abs_ms", "y"):
        nm[f] = np.asarray(getattr(eps, f))
    side_arr = np.asarray(eps.side)
    by_sym = {}
    for i in np.nonzero(side_arr == a.side)[0]:
        by_sym.setdefault(nm["sym"][i], []).append(i)
    bad_trunc = bad_garb = changed_ctrl = 0
    rng2 = np.random.default_rng(L.SEED + 1)

    class E:
        pass

    def sub(idx, y):
        e = E()
        e.sym = nm["sym"][idx]
        e.abs_ms = nm["abs_ms"][idx]
        e.y = y
        e.side = np.array([a.side] * len(idx), dtype=object)
        return e

    for j in pick:
        sym, t = rows["sym"].iloc[j], t0 + int(rows["sig_ms"].iloc[j])
        idx = np.array(by_sym.get(sym, []), dtype=np.int64)
        if len(idx) == 0:
            continue
        closed = nm["abs_ms"][idx] + tf.CLOSE_MS <= t
        full = feats(sub(idx, nm["y"][idx]), a.side, sym, t)
        trunc = feats(sub(idx[closed], nm["y"][idx[closed]]), a.side, sym, t)
        y_g = nm["y"][idx].astype(float).copy()
        y_g[~closed] = rng2.integers(0, 2, size=int((~closed).sum()))
        garb = feats(sub(idx, y_g), a.side, sym, t)
        y_c = nm["y"][idx].astype(float).copy()
        y_c[closed] = 1 - y_c[closed]
        ctrl = feats(sub(idx, y_c), a.side, sym, t)
        bad_trunc += int(not np.array_equal(np.array(full[:2] + tuple(full[2]), dtype=float), np.array(trunc[:2] + tuple(trunc[2]), dtype=float), equal_nan=True))
        bad_garb += int(not np.array_equal(np.array(full[:2] + tuple(full[2]), dtype=float), np.array(garb[:2] + tuple(garb[2]), dtype=float), equal_nan=True))
        changed_ctrl += int(not np.array_equal(np.array(full[:2] + tuple(full[2]), dtype=float), np.array(ctrl[:2] + tuple(ctrl[2]), dtype=float), equal_nan=True))
    res = dict(family="bk", win=a.win, side=a.side, rows=int(len(pick)), bad_truncated=bad_trunc, bad_garbage_after=bad_garb, control_changed=changed_ctrl, ok=bool(bad_trunc == 0 and bad_garb == 0 and changed_ctrl > 0), seed=L.SEED)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
