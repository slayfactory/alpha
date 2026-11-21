#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): ворота семейства TK-668 (`tk671_t668`) на srv, без P&L.
  python3 tk671_t668_gate.py --rows <rows-slice.parquet> --win wa --side long --out <gate.json> [--procs 3] [--k-causal 600]

G-eq-A  все строки среза 12 источников: X24 / X7 / XP / XV / r_h / σ_h / bz — `compute` = оригинал `tk668_feat` построчно (точное равенство, одинаковый рисунок NaN).
G-eq-B  все строки баз TK-668 (3 окна × 2 стороны, 1 897 строк, дамп уровней базы — реальный): все 14 столбцов и «свой уровень» = `tk668_run.compute_rows` (оригинальный драйвер).
G-causal-A  k строк среза: мусор в минутках монеты и BTC с минуты t (`tk668_feat.garbage_from`) не меняет X24 / X7 / XP / XV / r_h / σ_h / bz; контроль силы — те же значения на минуте t + 1 меняются.
G-causal-B  k строк баз: мусор в метках уровней позже t (рождение, конец, версии цены, выдуманные уровни; `garbage_levels`) не меняет R и «свой уровень»; контроль силы — доля строк, где мусор что-то менял.
Дамп уровней у строк 12 источников на момент проверки отсутствует (нужен `P42_LEVELS=1` в прогоне `sigsx` — см. отчёт), поэтому R на строках среза не сверяется; ядро R проверено на реальных уровнях базы."""
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
import tk625_run as r5  # noqa: E402
import tk668_feat as ft  # noqa: E402
import tk668_run as r  # noqa: E402
import tk671_t668 as fam  # noqa: E402

SEED = 671


def same(a, b):
    return (a != a and b != b) or a == b


def cmp_cols(got, ref, cols):
    """Число несовпадений и максимум |Δ| по столбцам (ref — dict/DataFrame с теми же столбцами)."""
    bad, mx = {}, 0.0
    for c in cols:
        x, y = np.asarray(got[c], dtype=float), np.asarray(ref[c], dtype=float)
        eq = (x == y) | (np.isnan(x) & np.isnan(y))
        bad[c] = int((~eq).sum())
        fin = np.isfinite(x) & np.isfinite(y)
        if fin.any():
            mx = max(mx, float(np.abs(x[fin] - y[fin]).max()))
    return bad, mx


def g_eq_a(rows, win, side, procs, cache_dir):
    t0 = time.time()
    info = {}
    got = fam.compute(rows, win, side, procs=procs, cache_dir=cache_dir, info=info)
    sec = time.time() - t0
    s = fam.SIGN[side]
    a = ft.load_b42_full(os.path.join(cache_dir, "BTCUSDT.b42"))
    cf = ft.ffill_close(a["o"], a["c"])
    r1 = ft.minute_returns(cf)
    ref = {c: np.full(len(rows), np.nan) for c in fam.FEATURES}
    mc = {}
    for i, x in enumerate(rows.itertuples()):
        t = x.sig_ms // ft.MIN_MS
        natr = x.natr_e8 / 1e8
        if x.sym not in mc:
            mc[x.sym] = ft.Mins(ft.load_b42_full(os.path.join(cache_dir, x.sym + ".b42")))
        ref["X24"][i], ref["X7"][i], ref["XP"][i], ref["XV"][i] = ft.place_features(mc[x.sym], t, s, natr)
        for h in ft.HORIZONS:
            rh, sg = ft.btc_shock(cf, r1, t, h)
            ref["r%d" % h][i], ref["sg%d" % h][i] = rh, sg
            ref["bz%d" % h][i] = s * rh / sg if np.isfinite(rh) and np.isfinite(sg) and sg > 0 else np.nan
    cols = [c for c in fam.FEATURES if c != "R"]
    bad, mx = cmp_cols(got, ref, cols)
    return dict(rows=int(len(rows)), mismatches=bad, max_abs=mx, seconds=round(sec, 3), non_nan={c: info["non_nan_share"][c] for c in cols}), sum(bad.values())


def g_eq_b(procs):
    u = r.uni()
    out, nbad_all, rows_all = {}, 0, 0
    for w in st.TRADE_WINDOWS:
        for code, side in (("L", "long"), ("S", "short")):
            lvp = r.levels_path(w.name, code)
            lv = ft.parse_levels(lvp)
            ref_rows, dups = r.compute_rows(u, w, code, lv)
            sx, _ = r.read_sigsx(r.sigsx_path(u, w.name, code))
            df = pd.DataFrame(dict(row_id=np.arange(len(sx), dtype=np.int64), src=0, sym=[x["sym"] for x in sx], level=[x["level"] for x in sx], sig_ms=[x["sig_ms"] for x in sx],
                                   level_px_e8=[x["px_e8"] for x in sx], natr_e8=[x["natr_e8"] for x in sx], win=w.name, side=side, abs_ms=[w.t0_ms + x["sig_ms"] for x in sx]))
            info = {}
            got = fam.compute(df, w.name, side, procs=procs, level_paths={0: lvp}, info=info)
            ref = {c: [x[c] for x in ref_rows] for c in fam.FEATURES}
            bad, mx = cmp_cols(got, ref, fam.FEATURES)
            own_ref = int(sum(1 for x in ref_rows if x["own"]))
            own_got = info["levels"][fam._source_names()[0]]["own"]
            n_bad = sum(bad.values()) + int(own_ref != own_got)
            out["%s-%s" % (w.name, side)] = dict(rows=len(df), dups_in_original=dups, mismatches=bad, max_abs=mx, own_ref=own_ref, own_mine=own_got, R_inf=int(np.isinf(got["R"]).sum()), R_finite=int(np.isfinite(got["R"]).sum()))
            nbad_all += n_bad
            rows_all += len(df)
    return dict(rows_total=rows_all, groups=out), nbad_all


def g_causal_a(rows, win, side, k, cache_dir):
    rng = np.random.default_rng(SEED)
    s = fam.SIGN[side]
    pick = rng.choice(len(rows), size=min(k, len(rows)), replace=False)
    a_btc = ft.load_b42_full(os.path.join(cache_dir, "BTCUSDT.b42"))
    cf0 = ft.ffill_close(a_btc["o"], a_btc["c"])
    r10 = ft.minute_returns(cf0)
    cache, bad, leak_differs = {}, 0, 0
    cols = [c for c in fam.FEATURES if c != "R"]
    for i in pick:
        x = rows.iloc[int(i)]
        t = int(x.sig_ms) // ft.MIN_MS
        natr = x.natr_e8 / 1e8
        if x.sym not in cache:
            cache[x.sym] = ft.load_b42_full(os.path.join(cache_dir, x.sym + ".b42"))
        a = cache[x.sym]
        ta, ts = np.array([t], dtype=np.int64), np.array([s])
        def feats(a_coin, cfb, r1b, tt):
            col = fam.btc_columns(cfb, r1b, np.array([tt], dtype=np.int64), ts)
            p4 = fam.place_columns(ft.Mins(a_coin), np.array([tt], dtype=np.int64), ts, np.array([natr]))[0]
            return dict(zip(("X24", "X7", "XP", "XV"), p4), **{k_: v[0] for k_, v in col.items()})
        base = feats(a, cf0, r10, t)
        ga = ft.garbage_from(a, t, rng)
        gb = ft.garbage_from(a_btc, t, rng)
        cfg = ft.ffill_close(gb["o"], gb["c"])
        trash = feats(ga, cfg, ft.minute_returns(cfg), t)
        bad += int(not all(same(base[c], trash[c]) for c in cols))
        late = feats(ga, cfg, ft.minute_returns(cfg), t + 1)                                    # контроль силы: минута t + 1 читает мусор — значения должны отличаться
        leak_differs += int(not all(same(base[c], late[c]) for c in cols))
    return dict(rows=int(len(pick)), bad=bad, control_t_plus_1_differs=leak_differs), bad


def g_causal_b(k):
    rng = np.random.default_rng(SEED + 1)
    u = r.uni()
    pool = []
    for w in st.TRADE_WINDOWS:
        for code in ("L", "S"):
            lv = ft.parse_levels(r.levels_path(w.name, code))
            sx, _ = r.read_sigsx(r.sigsx_path(u, w.name, code))
            for x in sx:
                pool.append((lv, code, x))
    pick = rng.choice(len(pool), size=min(k, len(pool)), replace=False)
    bad = changed = 0
    for i in pick:
        lv, code, x = pool[int(i)]
        sl = lv[x["sym"]]
        t = x["sig_ms"] // ft.MIN_MS
        args = [np.array([v]) for v in (t, abs(x["px_e8"]), x["level"])] + [np.array([fam.SIGN["long" if code == "L" else "short"]]), np.array([x["natr_e8"] / 1e8])]
        ref = fam.room_rows(sl, *args)
        g = fam.garbage_levels(sl, t, rng, n_junk=5)
        changed += int(not (np.array_equal(g.b, sl.b) and np.array_equal(g.e, sl.e) and all(np.array_equal(p, q) for p, q in zip(g.segm, sl.segm)) and all(np.array_equal(p, q) for p, q in zip(g.segp, sl.segp))))
        got = fam.room_rows(g, *args)
        bad += int(not (same(ref[0][0], got[0][0]) and ref[1][0] == got[1][0]))
    return dict(rows=int(len(pick)), bad=bad, garbage_changed_levels=changed), bad


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--k-causal", type=int, default=600)
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows)
    cache_dir = os.path.join(fam.CACHE_ROOT, a.win, "cache")
    res, nbad = {}, 0
    for name, fn in (("G-eq-A", lambda: g_eq_a(rows, a.win, a.side, a.procs, cache_dir)), ("G-eq-B", lambda: g_eq_b(a.procs)),
                     ("G-causal-A", lambda: g_causal_a(rows, a.win, a.side, a.k_causal, cache_dir)), ("G-causal-B", lambda: g_causal_b(a.k_causal))):
        v, b = fn()
        res[name] = v
        res[name]["fail"] = int(b)
        nbad += b
        print(name, "FAIL %d" % b if b else "ok", json.dumps({k: v[k] for k in v if k in ("rows", "rows_total", "max_abs", "control_t_plus_1_differs", "garbage_changed_levels")}), flush=True)
    res["fail_total"] = int(nbad)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    sys.exit(1 if nbad else 0)


if __name__ == "__main__":
    main()
