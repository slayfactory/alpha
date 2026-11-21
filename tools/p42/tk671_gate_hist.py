#!/usr/bin/env python3
"""TK-671: ворота семейства history (`tk671_hist`): G-eq — столбцы против таблиц TK-625 `ftab-h775` (все ключи, точно, шаблон NaN); G-brute — перебор по сырым событиям на ≥ 1 000 строк набора;
G-foreign — чужие столбцы стороны = свои столбцы другой стороны на тех же ключах; G-causal — мусор вместо событий, закрывающихся после t, на ≥ 500 строк набора не меняет признак.
Использование (srv): python3 tk671_gate_hist.py --prod /data/p42-tk671/prod --out gate-hist.json [--brute 1000 --causal 500]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_stats as st  # noqa: E402
import tk625_feat as f5  # noqa: E402
import tk671_hist as H  # noqa: E402

WINS = {w.name: w for w in st.TRADE_WINDOWS}
FTAB = os.environ.get("TK671_FTAB", "/data/p42-tk625/ftab-h775")


def same(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return bool(np.array_equal(np.isnan(a), np.isnan(b)) and np.array_equal(a[~np.isnan(a)], b[~np.isnan(b)]))


def brute(cr, side, sym, t, d, m):
    k = (cr.side == side) & (cr.sym == sym) & (cr.code[m] == "o") & (cr.tres[m] + f5.L_MS <= t) & (cr.cross > t - d * f5.DAY_MS) & (cr.cross <= t)
    return int(k.sum())


def garbage(cr, t, rng):
    g = cr.take(np.ones(len(cr), dtype=bool))
    g.cross = g.cross.copy()
    g.code = {m: v.copy() for m, v in g.code.items()}
    g.tres = {m: v.copy() for m, v in g.tres.items()}
    for m in f5.MS_NAMES:
        late = g.tres[m] + f5.L_MS > t
        k = int(late.sum())
        g.code[m][late] = rng.choice(np.array(["o", "f", "s"], dtype=object), size=k)
        g.tres[m][late] = t + rng.integers(1, 10 ** 9, size=k)
    late_x = g.cross > t
    g.cross[late_x] = t + rng.integers(1, 10 ** 9, size=int(late_x.sum()))
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--brute", type=int, default=1000)
    ap.add_argument("--causal", type=int, default=500)
    a = ap.parse_args()
    res, bad = {}, []
    cr, hist = H.load()
    # G-eq: ftab TK-625
    eq = {}
    for wn, w in WINS.items():
        for side, ch in (("long", "L"), ("short", "S")):
            ft = pd.read_csv(os.path.join(FTAB, "%s-%s.csv" % (wn, ch)), float_precision="round_trip")
            rows = pd.DataFrame({"row_id": np.arange(len(ft)), "sym": ft["sym"].astype(object), "abs_ms": ft["sig_ms"].to_numpy(np.int64) + w.t0_ms})
            got = H.compute(rows, wn, side)
            ok = all(same(got[c].to_numpy(), ft[c].to_numpy()) for c in f5.COLS)
            eq["%s-%s" % (wn, ch)] = dict(keys=len(ft), equal=ok)
            if not ok:
                bad.append("G-eq %s-%s" % (wn, ch))
            # G-foreign на тех же ключах
            other = H.compute(rows, wn, "short" if side == "long" else "long")
            fo = all(same(got["x" + c].to_numpy(), other[c].to_numpy()) for c in f5.COLS)
            if not fo:
                bad.append("G-foreign %s-%s" % (wn, ch))
            eq["%s-%s" % (wn, ch)]["foreign_equal"] = fo
    res["G-eq"] = eq
    # набор строк
    parts = []
    for wn in WINS:
        for side in ("long", "short"):
            p = pd.read_parquet(os.path.join(a.prod, "lvl-%s-%s" % (wn, side), "rows.parquet"), columns=["row_id", "sym", "abs_ms"])
            p["win"], p["side"] = wn, side
            parts.append(p)
    allr = pd.concat(parts, ignore_index=True)
    rng = np.random.default_rng(671)
    idx = rng.choice(len(allr), size=a.brute, replace=False)
    nb = 0
    for i in idx:
        r = allr.iloc[int(i)]
        one = pd.DataFrame({"row_id": [0], "sym": [r["sym"]], "abs_ms": [int(r["abs_ms"])]})
        got = H.compute(one, r["win"], r["side"]).iloc[0]
        t = int(r["abs_ms"])
        sub = cr.take(cr.sym == r["sym"])
        for pref, s in (("", r["side"]), ("x", H.OPP[r["side"]])):
            for m in f5.MS_NAMES:
                for d in f5.DS:
                    v = hist[s].valid(np.array([r["sym"]], dtype=object), np.array([t]), d)[0]
                    want = float(brute(sub, s, r["sym"], t, d, m)) if v else np.nan
                    g = got[pref + f5.col_name(m, d)]
                    nb += 1
                    if not (g == want or (np.isnan(g) and np.isnan(want))):
                        bad.append("G-brute %s %s t=%d %s%s: %s ≠ %s" % (r["win"], r["sym"], t, pref, f5.col_name(m, d), g, want))
    res["G-brute"] = dict(rows=len(idx), values=nb)
    # G-causal
    idx2 = rng.choice(len(allr), size=a.causal, replace=False)
    nc = 0
    for i in idx2:
        r = allr.iloc[int(i)]
        t, s = int(r["abs_ms"]), r["sym"]
        one = pd.DataFrame({"row_id": [0], "sym": [s], "abs_ms": [t]})
        got = H.compute(one, r["win"], r["side"]).iloc[0]
        gcr = garbage(cr.take(cr.sym == s), t, np.random.default_rng(int(t % 2 ** 31)))
        cd = {s: H.load()[1]["long"].coin_days.get(s)} if H.load()[1]["long"].coin_days.get(s) is not None else {}
        gh = {sd: f5.History(gcr.take(gcr.side == sd), cd, [s]) for sd in ("long", "short")}
        out = {}
        for pref, sd in (("", r["side"]), ("x", H.OPP[r["side"]])):
            for k, v in f5.ok_matrix(f5.features(gh[sd], np.array([s], dtype=object), np.array([t]))).items():
                out[pref + k] = v[0]
        for c in H.COLUMNS:
            nc += 1
            if not (got[c] == out[c] or (np.isnan(got[c]) and np.isnan(out[c]))):
                bad.append("G-causal %s t=%d %s: %s → %s" % (s, t, c, got[c], out[c]))
    res["G-causal"] = dict(rows=len(idx2), values=nc)
    # охват (без P&L)
    cov = {}
    for wn in WINS:
        for side in ("long", "short"):
            sub = allr[(allr.win == wn) & (allr.side == side)]
            df = H.compute(sub, wn, side)
            cov["%s-%s" % (wn, side)] = {c: round(float(df[c].notna().mean()), 4) for c in ("ok10_d7", "ok10_d14", "ok10_d30", "xok10_d7")}
    res["coverage"] = cov
    res["bad"] = bad[:30]
    res["n_bad"] = len(bad)
    json.dump(res, open(a.out, "w"), ensure_ascii=False, indent=1)
    print("G-eq", all(v["equal"] and v["foreign_equal"] for v in eq.values()), "brute", res["G-brute"], "causal", res["G-causal"], "bad", len(bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
