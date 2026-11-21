#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): ворота семейства `fund` (`tk671_fund.py`) на реальных строках, srv. Только чтение.

  python3 tk671_gate_fund.py eq [--slice <rows.parquet>]  — G-eq: `compute` против ОРИГИНАЛЬНЫХ скалярных функций `tk666_feat` (`funding_at`, `Prem.at`, `round_feat`, `confluence`) на ≥ 1000 реальных строках
                                                           (срез `slice10b` wa-long с `mask`/`first_bar` + строки `b0p0` wb / wc обеих сторон, в т.ч. ноябрь)
  python3 tk671_gate_fund.py causal --n 500               — G-causal: данные строго позже σ (выплаты, свечи премии, версии цен и рождение уровней) заменены мусором → выходы те же
  python3 tk671_gate_fund.py bench --procs 3              — мс/строка и охват на срезе и на плотных строках wc
Результат — `/data/p42-tk671/fam/fund/gate-<имя>.json`."""
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
import tk666_feat as ft  # noqa: E402
import tk671_fund as fd  # noqa: E402

OUT = "/data/p42-tk671/fam/fund"
SLICE = "/data/p42-tk671/pilot/slice10b/rows-slice.parquet"
B0 = "/data/p42-tk625/h775/eng/%s/b0p0/%s-sigsx.csv"
WIN = {x.name: x for x in st.TRADE_WINDOWS}


def save(name, res):
    os.makedirs(OUT, exist_ok=True)
    json.dump(res, open(os.path.join(OUT, "gate-%s.json" % name), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False)[:3000])


def b0_rows(win, side, n, seed):
    """Реальные строки дампа `b0p0` (без mask / first_bar): случайные n."""
    t = pd.read_csv(B0 % (win, side), usecols=["sym", "sig_ms", "level_px_e8", "natr_e8"])
    t = t.iloc[np.sort(np.random.default_rng(seed).choice(len(t), size=min(n, len(t)), replace=False))].reset_index(drop=True)
    t.insert(0, "row_id", np.arange(len(t), dtype=np.int64))
    t["abs_ms"] = t["sig_ms"].astype(np.int64) + WIN[win].t0_ms
    t["side"], t["win"] = side, win
    return t


def orig_rows(rows, win, side, ext=False):
    """Оригинальные скалярные функции TK-666 на каждой строке. ext=False — оригинальные корни данных и месяцы 07–10; ext=True — те же функции на корнях и месяцах `tk671_fund`
    (июнь, `/data/p42-tk600/*`): подмена только путей к файлам, формулы оригинала."""
    if ext:
        saved = (ft.prem_zip_path, ft.funding_zip_path, ft.MONTHS)
        ft.prem_zip_path, ft.funding_zip_path, ft.MONTHS = fd.prem_path, fd.fund_path, fd.FUND_MONTHS
        try:
            return orig_rows(rows, win, side, False)
        finally:
            ft.prem_zip_path, ft.funding_zip_path, ft.MONTHS = saved
    n = len(rows)
    out = {k: np.full(n, np.nan) for k in fd.COLS}
    fcache, prem = {}, ft.Prem()
    lpath = fd.levels_file(win, side)
    lv = ft.read_levels(lpath) if os.path.exists(lpath) else None
    sym, ms, m = rows["sym"].tolist(), rows["abs_ms"].to_numpy(), rows["sig_ms"].to_numpy() // 60_000
    px, nat = rows["level_px_e8"].to_numpy(), rows["natr_e8"].to_numpy()
    for i in range(n):
        s_, t = sym[i], int(ms[i])
        if s_ not in fcache:
            fcache[s_] = ft.load_funding(s_)
        out["f8"][i], out["f_age_h"][i] = ft.funding_at(*fcache[s_], t)
        out["prem"][i], out["prem_age_s"][i] = prem.at(s_, t)
        out["rho"][i], out["mant"][i] = ft.round_feat(px[i], nat[i])
        out["lvl_px"][i] = abs(int(px[i]))
        if lv is not None:
            c = ft.confluence(lv, s_, int(m[i]), px[i])
            out["conf01"][i], out["conf02"][i], out["conf04"][i] = c
    return out


def cmp(a, b, rel=1e-9):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    both = ~np.isnan(a) & ~np.isnan(b)
    err = np.zeros(len(a))
    err[both] = np.abs(a[both] - b[both]) / np.maximum(np.abs(b[both]), 1e-300)
    err[both & (a == b)] = 0.0
    return dict(max_rel=float(err.max()) if len(err) else 0.0, value_bad=int((err > rel).sum()), defined_only_in_mine=int((np.isnan(b) & ~np.isnan(a)).sum()),
                nan_where_orig_defined=int((np.isnan(a) & ~np.isnan(b)).sum()), both_defined=int(both.sum()))


def cmd_eq(a):
    res, bad_total, n_total = {}, 0, 0
    sets = []
    if os.path.exists(a.slice):
        sets.append(("slice10b-wa-long", pd.read_parquet(a.slice), "wa", "long"))
    for win in ("wb", "wc"):
        for side in ("long", "short"):
            sets.append(("b0p0-%s-%s" % (win, side), b0_rows(win, side, a.n_b0, 671), win, side))
    for name, rows, win, side in sets:
        if name.startswith("slice") and a.slice_n and len(rows) > a.slice_n:
            rows = rows.sample(n=a.slice_n, random_state=671).sort_values("row_id").reset_index(drop=True)
        t0 = time.time()
        st_ = {}
        got = fd.compute(rows, win, side, procs=3, stats=st_)
        t_new = time.time() - t0
        t0 = time.time()
        want = orig_rows(rows, win, side, ext=True)                      # строгая сверка: те же файлы, формулы оригинала
        t_old = time.time() - t0
        want0 = orig_rows(rows, win, side, ext=False)                    # справочно: оригинальные корни / месяцы (что даёт расширение данных)
        has_own = "mask" in rows.columns
        own = ((rows["mask"].to_numpy(dtype=np.int64) & fd.P240_BITS) != 0) if has_own else np.zeros(len(rows), dtype=bool)
        r = {}
        for c in fd.COLS:
            g, w = got[c].to_numpy(), want[c]
            if c.startswith("conf"):                                     # строки с источником tf=240 отличаются по определению (свой уровень исключён); остальные — равны
                keep = ~own
                x = cmp(g[keep], w[keep])
                x["rows_with_own240_excluded"] = int(own.sum())
                x["own240_rows_where_orig_is_1"] = int((w[own] == 1.0).sum())
                x["own240_rows_changed"] = int((g[own] != w[own]).sum())
            else:
                x = cmp(g, w)
            r[c] = x
        bad = sum(v["value_bad"] + v["nan_where_orig_defined"] + v["defined_only_in_mine"] for v in r.values())
        bad_total += bad
        ext_effect = {}
        for c in ("f8", "f_age_h", "prem", "prem_age_s"):
            x = cmp(got[c].to_numpy(), want0[c])
            ext_effect[c] = dict(defined_only_in_mine=x["defined_only_in_mine"], value_changed=x["value_bad"], nan_where_orig_defined=x["nan_where_orig_defined"])
        res[name] = dict(rows=len(rows), sec_new=round(t_new, 2), sec_orig=round(t_old, 2), bad=bad, cols=r, effect_of_extended_data_roots_vs_original_roots=ext_effect,
                         defined={c: int(np.isfinite(got[c].to_numpy()).sum()) for c in fd.COLS})
        n_total += len(rows)
    res.update(total_rows=n_total, total_bad=bad_total, ok=bad_total == 0 and n_total >= 1000)
    save("eq", res)
    return 0 if res["ok"] else 1


def cmd_causal(a):
    rng = np.random.default_rng(671)
    n_bad = dict(F=0, P=0, C=0)
    n = 0
    sets = [("slice", pd.read_parquet(a.slice), "wa", "long")] if os.path.exists(a.slice) else []
    for win in ("wb", "wc"):
        for side in ("long", "short"):
            sets.append(("b0p0", b0_rows(win, side, 2000, 672), win, side))
    per = max(1, a.n // len(sets) + 1)
    for name, rows, win, side in sets:
        rows = rows.sample(n=min(per, len(rows)), random_state=671).reset_index(drop=True)
        lpath = fd.levels_file(win, side)
        levels = fd.read_levels_fb(lpath) if os.path.exists(lpath) else None
        for i in range(len(rows)):
            r = rows.iloc[i]
            sym, ms, m = r["sym"], np.array([int(r["abs_ms"])], dtype=np.int64), np.array([int(r["sig_ms"]) // 60_000], dtype=np.int64)
            px = np.array([abs(int(r["level_px_e8"]))], dtype=np.int64)
            n += 1
            # F: выплаты строго позже σ — мусор
            t, rate8, inter = fd.load_funding(sym)
            clean = fd.funding_vec(t, rate8, inter, ms)
            after = t > ms[0]
            r2, i2 = rate8.copy(), inter.copy()
            r2[after], i2[after] = rng.normal(0.0, 0.05, int(after.sum())), 8.0
            t2 = t.copy()
            dirty = fd.funding_vec(t2, r2, i2, ms)
            n_bad["F"] += not all(np.array_equal(x, y, equal_nan=True) for x, y in zip(clean, dirty))
            # P: свечи, закрывающиеся после σ (open + 60 с > σ), — мусор
            days = [fd.day_str(ms[0]), fd.day_str(ms[0] - 360_000)]
            o, c, _ = fd.load_prem(sym, sorted(set(days)))
            clean = fd.prem_vec(o, c, ms)
            c2 = np.where(o + 60_000 > ms[0], rng.normal(0.0, 0.05, len(c)), c)
            dirty = fd.prem_vec(o, c2, ms)
            n_bad["P"] += not all(np.array_equal(x, y, equal_nan=True) for x, y in zip(clean, dirty))
            # C: версии цены со временем > m — мусор; уровни, рождённые после m, — целиком мусор; `end` у живых — любой > m
            if levels is not None:
                lv = levels.get(sym, [])
                fbv = np.array([int(r["first_bar"])]) if "first_bar" in rows.columns else None
                own = np.array([(int(r["mask"]) & fd.P240_BITS) != 0]) if "mask" in rows.columns and fbv is not None else None
                clean = fd.conf_vec(lv, m, px, fbv, own)
                dl = []
                for born, end, mins, pxs, fb in lv:
                    if born > m[0]:
                        dl.append((int(rng.integers(m[0] + 1, m[0] + 10 ** 6)), int(rng.integers(m[0] + 2, 10 ** 7)), np.array([m[0] + 5]), np.array([int(rng.integers(1, 10 ** 12))]), fb))
                        continue
                    pxs2 = np.where(mins > m[0], rng.integers(1, 10 ** 12, size=len(pxs)), pxs)
                    end2 = end if end <= m[0] else int(rng.integers(m[0] + 1, 10 ** 7))
                    dl.append((born, end2, mins, pxs2, fb))
                dirty = fd.conf_vec(dl, m, px, fbv, own)
                n_bad["C"] += not np.array_equal(clean, dirty)
    res = dict(rows=n, bad_F=n_bad["F"], bad_P=n_bad["P"], bad_C=n_bad["C"], seed=671, ok=n >= 500 and sum(n_bad.values()) == 0)
    save("causal", res)
    return 0 if res["ok"] else 1


def cmd_bench(a):
    res = {}
    if os.path.exists(a.slice):
        rows = pd.read_parquet(a.slice)
        st_ = {}
        t0 = time.time()
        got = fd.compute(rows, "wa", "long", procs=a.procs, stats=st_)
        sec = time.time() - t0
        cd = len(set(zip(rows["sym"], rows["abs_ms"] // 86_400_000)))
        need = st_.pop("need")
        res["slice10b-wa-long"] = dict(rows=len(rows), coin_days=cd, procs=a.procs, seconds=round(sec, 2), ms_per_row=round(1000 * sec / len(rows), 4),
                                      ms_per_row_cpu=round(1000 * sec * a.procs / len(rows), 4), nonnan={c: round(float(got[c].notna().mean()), 4) for c in fd.COLS}, stats=st_,
                                      need_head={k: v[:5] for k, v in need.items()})
    # плотные реальные сигналы wc (long): те же 71 монета (каждая 10-я по имени), ≈ 13 строк на монето-сутки — плотность производства
    w = WIN["wc"]
    t = pd.read_csv(B0 % ("wc", "long"), usecols=["sym", "sig_ms", "level_px_e8", "natr_e8"])
    coins = sorted(t["sym"].unique())[::10][:71]
    t = t[t["sym"].isin(set(coins))]
    t = t.iloc[np.sort(np.random.default_rng(5).choice(len(t), size=min(13 * len(coins) * 40, len(t)), replace=False))].reset_index(drop=True)
    t.insert(0, "row_id", np.arange(len(t), dtype=np.int64))
    t["abs_ms"] = t["sig_ms"].astype(np.int64) + w.t0_ms
    t["side"], t["win"] = "long", "wc"
    st_ = {}
    t0 = time.time()
    got = fd.compute(t, "wc", "long", procs=a.procs, stats=st_)
    sec = time.time() - t0
    cd = len(set(zip(t["sym"], t["abs_ms"] // 86_400_000)))
    need = st_.pop("need")
    res["wc-dense-long"] = dict(rows=len(t), coin_days=cd, procs=a.procs, seconds=round(sec, 2), ms_per_row=round(1000 * sec / len(t), 4), ms_per_row_cpu=round(1000 * sec * a.procs / len(t), 4),
                                nonnan={c: round(float(got[c].notna().mean()), 4) for c in fd.COLS}, need_counts=st_["need_counts"])
    save("bench", res)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("eq", "causal", "bench"))
    ap.add_argument("--slice", default=SLICE)
    ap.add_argument("--slice-n", type=int, default=0)
    ap.add_argument("--n-b0", type=int, default=600)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--procs", type=int, default=3)
    a = ap.parse_args()
    return {"eq": cmd_eq, "causal": cmd_causal, "bench": cmd_bench}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
