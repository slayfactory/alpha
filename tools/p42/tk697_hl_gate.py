#!/usr/bin/env python3
"""П-42 TK-697 (№ 8): ворота семейства `h697` (`tk697_hl`) на srv, без P&L.
  python3 tk697_hl_gate.py --rows <rows.parquet> --daily <daily.parquet> --win wa --side long --out <gate.json> [--k-brute 1500] [--k-causal 400] [--k-eq 4000]

G-brute   `compute` (боевой путь: ядра numba) = независимый построчный счёт на чистом Python (`brute_row`: свой отбор суток и минуток) на k строках реального файла
          (случайные + самые «молодые» по листингу): одинаковый рисунок NaN, относительная ошибка ≤ 1e-9.
G-causal  k строк: дневные свечи суток ≥ D и минутки ≥ t заменены мусором — 12 значений строки не меняются; контроль силы — мусор с суток D − 1 / минуты t − 1 меняет строки.
G-eq      дневная свеча биржи = минутки кэша на сутках, лежащих целиком в кэше окна (k монето-суток): high / low дневной свечи == max / min минуток с данными (e8, точно),
          т. е. дневной и минутный источник — одни данные биржи."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk668_feat as ft  # noqa: E402
import tk697_hl as fam  # noqa: E402
import tk697_cols as m6  # noqa: E402
import tk697_gate as g6  # noqa: E402

SEED = 698
NAN = float("nan")


def brute_row(a, days, dh, dl, abs_ms, sig_ms, level, natr_b):
    """12 значений строки (порядок `fam.COLUMNS`): a — минутки монеты (None — нет), days / dh / dl — дневные свечи монеты (списки), natr_b — NATR 60 на минуте b."""
    t = sig_ms // 60_000
    b = t - 1
    D = abs_ms // fam.DAY_MS
    d0 = (t // 1440) * 1440
    o, h, l, _, _ = g6.lists_of(a)
    th = [h[m] for m in range(d0, b + 1) if 0 <= m < len(o) and o[m] != 0]
    tl = [l[m] for m in range(d0, b + 1) if 0 <= m < len(o) and o[m] != 0]
    L8 = level * 1e8
    hi_p, lo_p, hi_n, lo_n = [], [], [], []
    for n in fam.NS:
        xs_h = [dh[i] for i, d in enumerate(days) if D - n <= d <= D - 1] + th
        xs_l = [dl[i] for i, d in enumerate(days) if D - n <= d <= D - 1] + tl
        if not xs_h:
            hi_p.append(NAN), lo_p.append(NAN), hi_n.append(NAN), lo_n.append(NAN)
            continue
        H, Lw = max(xs_h), min(xs_l)
        hi_p.append(100.0 * (H - L8) / L8)
        lo_p.append(100.0 * (L8 - Lw) / L8)
        hi_n.append((H - L8) / L8 / natr_b if natr_b == natr_b and natr_b != 0 else NAN)
        lo_n.append((L8 - Lw) / L8 / natr_b if natr_b == natr_b and natr_b != 0 else NAN)
    return hi_p + lo_p + hi_n + lo_n


def garbage_daily(daily, D_from, rng):
    d = daily.copy()
    m = d["day"] >= D_from
    k = int(m.sum())
    if k:
        d.loc[m, "h"] = rng.integers(10**3, 10**9, k) * 1.0 + 10**15
        d.loc[m, "l"] = rng.integers(1, 10**3, k) * 1.0
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--daily", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--k-brute", type=int, default=1200)
    ap.add_argument("--k-causal", type=int, default=300)
    ap.add_argument("--k-eq", type=int, default=4000)
    ap.add_argument("--cache-dir")
    ap.add_argument("--tmp", default="/data/p42-tk697/tmp")
    a = ap.parse_args()
    import tempfile
    import shutil
    os.makedirs(a.tmp, exist_ok=True)
    rng = np.random.default_rng(SEED)
    cache_dir = a.cache_dir or os.path.join(fam.CACHE_ROOT, a.win, "cache")
    rows = pd.read_parquet(a.rows).reset_index(drop=True)
    daily = pd.read_parquet(a.daily)
    arrs = fam.daily_arrays(daily)
    res = {"win": a.win, "side": a.side, "rows": int(len(rows))}
    store = {}
    full = fam.compute(rows, arrs, a.win, a.side, cache_dir=cache_dir)

    ix = g6.pick(rows, a.k_brute, rng)
    ix = ix[np.argsort(rows["sym"].to_numpy(dtype=object).astype(str)[ix], kind="stable")]
    got, ref = full.iloc[ix].to_numpy(), []
    for i in ix:
        sym = rows["sym"].iat[i]
        am = g6.load_arr(cache_dir, sym, store)
        if am is None:
            ref.append([NAN] * len(fam.COLUMNS))
            continue
        b = int(rows["sig_ms"].iat[i] // 60_000) - 1
        o_, h_, l_, c_, _ = g6.lists_of(am)
        nb = g6.natr_at(o_, h_, l_, c_, b)
        dd, dh, dl = arrs.get(sym, (np.zeros(0, np.int64), np.zeros(0), np.zeros(0)))
        ref.append(brute_row(am, dd.tolist(), dh.tolist(), dl.tolist(), int(rows["abs_ms"].iat[i]), int(rows["sig_ms"].iat[i]),
                             abs(int(rows["level_px_e8"].iat[i])) / 1e8, nb))
    bad, dmax = g6.compare(got, np.array(ref))
    res["g_brute"] = dict(k=int(len(ix)), bad_by_col={c: int(x) for c, x in zip(fam.COLUMNS, bad) if x}, max_abs_diff=dmax, ok=bool(bad.sum() == 0),
                          nan_share={c: round(float(np.isnan(got[:, j]).mean()), 4) for j, c in enumerate(fam.COLUMNS)})

    jx = g6.pick(rows, a.k_causal, rng)
    bad_c = changed = 0
    for i in jx:
        sym = rows["sym"].iat[i]
        am = g6.load_arr(cache_dir, sym, store)
        if am is None:
            continue
        one = rows.iloc[[i]]
        t = int(rows["sig_ms"].iat[i] // 60_000)
        D = int(rows["abs_ms"].iat[i] // fam.DAY_MS)
        base = fam.compute(one, arrs, a.win, a.side, cache_dir=cache_dir).to_numpy()
        for (t_g, D_g, strong) in ((t, D, False), (t - 1, D - 1, True)):
            tmp = tempfile.mkdtemp(prefix="h697-", dir=a.tmp)
            g6.write_b42(os.path.join(tmp, sym + ".b42"), ft.garbage_from(am, t_g, rng))
            if sym != m6.BTC:                                   # NATR и дневные берутся по своей монете; BTC не читается
                pass
            gd = fam.daily_arrays(garbage_daily(daily[daily["sym"] == sym], D_g, rng))
            got1 = fam.compute(one, gd, a.win, a.side, cache_dir=tmp).to_numpy()
            shutil.rmtree(tmp, ignore_errors=True)
            same = np.array_equal(base, got1, equal_nan=True)
            if not strong and not same:
                bad_c += 1
            if strong and not same:
                changed += 1
    res["g_causal"] = dict(k=int(len(jx)), bad=int(bad_c), control_changed=int(changed), ok=bool(bad_c == 0 and changed > 0))

    # G-eq: дневная свеча == минутки кэша на сутках целиком внутри кэша окна
    t0_day = int((rows["abs_ms"].iat[0] - rows["sig_ms"].iat[0]) // fam.DAY_MS)
    syms = rows["sym"].unique()
    checks = bad_e = 0
    for sym in rng.permutation(syms)[: max(1, min(len(syms), 400))]:
        am = g6.load_arr(cache_dir, sym, store)
        if am is None or sym not in arrs:
            continue
        dd, dh, dl = arrs[sym]
        ndays = len(am["o"]) // fam.DAY_MIN
        for k in range(1, ndays - 1):
            day = t0_day + k
            j = np.searchsorted(dd, day)
            if j >= len(dd) or dd[j] != day:
                continue
            sl_ = slice(k * fam.DAY_MIN, (k + 1) * fam.DAY_MIN)
            v = am["o"][sl_] != 0
            if not v.any():
                continue
            checks += 1
            if int(am["h"][sl_][v].max()) != int(dh[j]) or int(am["l"][sl_][v].min()) != int(dl[j]):
                bad_e += 1
            if checks >= a.k_eq:
                break
        if checks >= a.k_eq:
            break
    res["g_eq"] = dict(k=int(checks), bad=int(bad_e), ok=bool(checks > 0 and bad_e == 0))
    res["ok"] = bool(res["g_brute"]["ok"] and res["g_causal"]["ok"] and res["g_eq"]["ok"])
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: (v if not isinstance(v, dict) else v.get("ok")) for k, v in res.items()}))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
