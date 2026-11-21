#!/usr/bin/env python3
"""TK-671: ворота семейства bk (`tk671_bkep` + `tk671_bk`): G-y — метка y / ok_t60 / locate_fail против таблиц TK-604 (`/data/p42-tk604/ep`) на всех совпавших ключах (сторона, монета, уровень, sig), доля
независимых эпизодов TK-604, найденных в нашей таблице; G-eq — n, k, bk против таблиц `ftab` TK-604 на совпавших ключах (монеты обеих вселенных); G-brute — `tk604_feat.brute_nk` на ≥ 1 000 строк набора;
G-causal — `garbage_after` (метка и время закрывающихся после t эпизодов — мусор) на ≥ 500 строк не меняет признак; охват (без P&L).
Использование (srv): python3 tk671_gate_bk.py --prod /data/p42-tk671/prod --out gate-bk.json
"""
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
import tk604_stats as st  # noqa: E402
import tk671_bk as B  # noqa: E402

WINS = {w.name: w for w in st.TRADE_WINDOWS}
OLD_EP = "/data/p42-tk604/ep"
OLD_FTAB = "/data/p42-tk604/ftab"
KEY = ["side", "sym", "level", "sig_ms"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--brute", type=int, default=1000)
    ap.add_argument("--causal", type=int, default=500)
    a = ap.parse_args()
    res, bad = {}, []
    # G-y
    gy = {}
    for wn in ("w0", "wa", "wb", "wc"):
        new = pd.read_csv(os.path.join(B.EP_DIR, wn + ".csv"))
        old = pd.read_csv(os.path.join(OLD_EP, wn + ".csv"))
        old = old[old.indep == 1]
        m = new.merge(old, on=KEY, suffixes=("", "_o"))
        ok = bool((m.y.fillna(-1) == m.y_o.fillna(-1)).all() and (m.ok_t60 == m.ok_t60_o).all() and (m.locate_fail == m.locate_fail_o).all())
        gy[wn] = dict(ours=len(new), old_indep=len(old), matched=len(m), old_found=round(len(m) / max(len(old), 1), 4), equal=ok,
                      y_diff=int((m.y.fillna(-1) != m.y_o.fillna(-1)).sum()))
        if not ok:
            bad.append("G-y %s" % wn)
        if len(m) < len(old):
            bad.append("G-y %s: в таблице TK-604 %d независимых эпизодов, найдено %d" % (wn, len(old), len(m)))
    res["G-y"] = gy
    # G-eq против ftab TK-604
    geq = {}
    for wn, w in WINS.items():
        for side, ch in (("long", "L"), ("short", "S")):
            ft = pd.read_csv(os.path.join(OLD_FTAB, "%s-%s.csv" % (wn, ch)), float_precision="round_trip")
            rows = pd.DataFrame({"row_id": np.arange(len(ft)), "sym": ft["sym"].astype(object), "abs_ms": ft["sig_ms"].to_numpy(np.int64) + w.t0_ms})
            got = B.compute(rows, wn, side)
            eq_nk = bool(np.array_equal(got["bk_n"].to_numpy(), ft["n"].to_numpy(float)) and np.array_equal(got["bk_k"].to_numpy(), ft["k"].to_numpy(float)))
            e2 = True
            for K in tf.KS:
                x, y = got["bk%d" % K].to_numpy(), ft["bk%d" % K].to_numpy(float)
                e2 &= bool(np.array_equal(np.isnan(x), np.isnan(y)) and np.array_equal(x[~np.isnan(x)], y[~np.isnan(y)]))
            geq["%s-%s" % (wn, ch)] = dict(keys=len(ft), n_k_equal=eq_nk, bk_equal=e2)
            if not (eq_nk and e2):
                bad.append("G-eq %s-%s" % (wn, ch))
    res["G-eq"] = geq
    # набор строк
    parts = []
    for wn in WINS:
        for side in ("long", "short"):
            p = pd.read_parquet(os.path.join(a.prod, "lvl-%s-%s" % (wn, side), "rows.parquet"), columns=["row_id", "sym", "abs_ms"])
            p["win"], p["side"] = wn, side
            parts.append(p)
    allr = pd.concat(parts, ignore_index=True)
    eps = B.load()
    rng = np.random.default_rng(671)
    idx = rng.choice(len(allr), size=a.brute, replace=False)
    for i in idx:
        r = allr.iloc[int(i)]
        t = int(r["abs_ms"])
        got = B.compute(pd.DataFrame({"row_id": [0], "sym": [r["sym"]], "abs_ms": [t]}), r["win"], r["side"]).iloc[0]
        m = eps.side == r["side"]
        n, k = tf.brute_nk(eps.sym[m], eps.abs_ms[m], eps.y[m], r["sym"], t)
        if int(got["bk_n"]) != n or int(got["bk_k"]) != k:
            bad.append("G-brute %s %s t=%d: (%d,%d) ≠ (%d,%d)" % (r["win"], r["sym"], t, got["bk_n"], got["bk_k"], n, k))
    res["G-brute"] = dict(rows=len(idx))
    idx2 = rng.choice(len(allr), size=a.causal, replace=False)
    for i in idx2:
        r = allr.iloc[int(i)]
        t, s = int(r["abs_ms"]), r["sym"]
        got = B.compute(pd.DataFrame({"row_id": [0], "sym": [s], "abs_ms": [t]}), r["win"], r["side"]).iloc[0]
        sub = eps.take(eps.sym == s)
        g = tf.garbage_after(sub, t, seed=int(t % 2 ** 31))
        n, k, bk = tf.features(tf.episode_history(g, r["side"]), np.array([s], dtype=object), np.array([t]))
        want = {"bk_n": float(n[0]), "bk_k": float(k[0]), **{"bk%d" % K: float(bk[K][0]) for K in tf.KS}}
        for c, v in want.items():
            x = got[c]
            if not (x == v or (np.isnan(x) and np.isnan(v))):
                bad.append("G-causal %s t=%d %s: %s → %s" % (s, t, c, x, v))
    res["G-causal"] = dict(rows=len(idx2))
    cov = {}
    for wn in WINS:
        for side in ("long", "short"):
            sub = allr[(allr.win == wn) & (allr.side == side)]
            df = B.compute(sub, wn, side)
            cov["%s-%s" % (wn, side)] = {c: round(float(df[c].notna().mean()), 4) for c in ("bk20", "bk40", "bk60")}
    res["coverage"] = cov
    res["bad"], res["n_bad"] = bad[:30], len(bad)
    json.dump(res, open(a.out, "w"), ensure_ascii=False, indent=1)
    print("G-y", {k: (v["old_found"], v["equal"]) for k, v in gy.items()}, "G-eq", all(v["n_k_equal"] and v["bk_equal"] for v in geq.values()), "brute", len(idx), "causal", len(idx2), "bad", len(bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
