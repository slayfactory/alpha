#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): ворота семейства TK-636 (`tk671_coin`) на srv, без P&L.
  python3 tk671_coin_gate.py --rows <rows-slice.parquet> --win wa --side long --out <gate.json> [--procs 3] [--k-causal 600] [--k-eq 0]

G-clu       метки кластеров месяцев 07…11 = `clusters-<мм>.csv` TK-636 и = пересчёту оригиналом (`month_clusters` на полной матрице окна обновления).
G-eq-A      строки среза 12 источников (все, `--k-eq N` — случайные N): 28 столбцов `row_values` оригинала (точное равенство, одинаковый рисунок NaN); turn24h = Σ qv[t − 1440 : t] / 1e5 по минуткам; natr_bp = natr_e8 / 1e4.
G-eq-B      строки баз TK-636 (3 окна × 2 стороны, 1 897 строк): все 30 столбцов = готовым таблицам `/data/p42-tk636/feat/h775/<окно>-<L|S>.csv` (в том числе turn24h и natr_bp из таблицы TK-634).
G-causal    k строк среза: мусор в доходностях монеты, BTC / ETH и соседей с минуты t, в событиях ≥ t (плюс 20 лишних) и в обороте ≥ t не меняет 28 столбцов и turn24h; контроль силы — та же строка на t + 10 мин читает мусор и меняется.
            Мусор в доходностях после дня обновления кластеров меток не меняет (по каждому из 5 месяцев)."""
from __future__ import annotations

import argparse
import datetime
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
import tk636_feat as f6  # noqa: E402
import tk668_feat as ft  # noqa: E402
import tk671_coin as fam  # noqa: E402

SEED = 671
TABLES = "/data/p42-tk636/feat/h775"
SIDE_CODE = {"long": "L", "short": "S"}


def same(a, b):
    return (a != a and b != b) or a == b


def cmp_frames(got, ref, cols, tol=0.0):
    """По столбцам: число значений за допуском (`|x − y| > tol·max(1, |y|)`; tol = 0 — точное равенство) и число расхождений рисунка NaN; плюс максимум |Δ|."""
    bad, nanpat, mx = {}, {}, 0.0
    for c in cols:
        x, y = np.asarray(got[c], dtype=float), np.asarray(ref[c], dtype=float)
        nx, ny = np.isnan(x), np.isnan(y)
        both = ~nx & ~ny
        with np.errstate(invalid="ignore"):
            far = np.where(both, ~((x == y) | (np.abs(x - y) <= tol * np.maximum(1.0, np.abs(y)))), False)
        bad[c] = int(far.sum())
        nanpat[c] = int((nx != ny).sum())
        fin = both & np.isfinite(x) & np.isfinite(y)
        if fin.any():
            mx = max(mx, float(np.abs(x[fin] - y[fin]).max()))
    return bad, nanpat, mx


def g_clu(coins, procs, cached):
    labels = fam.month_labels(sorted(f6.REFRESH_WINDOW), coins, labels_dir=None, procs=procs)       # свежий счёт из минуток, не из кэша меток
    u = r5.Uni("h775")
    res, bad, wobjs = {}, 0, {}
    for mon in sorted(f6.REFRESH_WINDOW):
        wn = f6.REFRESH_WINDOW[mon]
        if wn not in wobjs:
            wobjs = {wn: f6.Win(u.wdir(wn), coins)}                       # одна матрица в памяти за раз
        w = next(x for x in st.TRADE_WINDOWS if x.name == wn)
        ref = f6.month_clusters(wobjs[wn], w.t0d, mon)
        tab = pd.read_csv(os.path.join(TABLES, "clusters-%02d.csv" % mon))
        from_file = np.full(len(coins), -1, dtype=np.int64)
        ix = {s: i for i, s in enumerate(coins)}
        for s, c in zip(tab["sym"], tab["cluster"]):
            from_file[ix[s]] = int(c)
        d1 = int((labels[mon] != ref).sum())
        d2 = int((labels[mon] != from_file).sum())
        sizes = sorted((int((labels[mon] == c).sum()) for c in np.unique(labels[mon]) if c >= 0), reverse=True)
        d3 = int((labels[mon] != cached[mon]).sum())
        res[mon] = dict(window=wn, clusters=len(sizes), coins_in_clusters=int((labels[mon] >= 0).sum()), differs_from_original_recompute=d1, differs_from_tk636_file=d2, differs_from_label_cache=d3)
        bad += d1 + d2 + d3
    return res, bad


def original_labels(coins):
    """Метки месяцев оригиналом (по одной матрице окна за раз) + матрица `wa` для построчной сверки."""
    u = r5.Uni("h775")
    lab, keep = {}, None
    for wn in ("wa", "wb", "wc"):
        w = next(x for x in st.TRADE_WINDOWS if x.name == wn)
        wo = f6.Win(u.wdir(wn), coins)
        for mon in sorted(m for m, x in f6.REFRESH_WINDOW.items() if x == wn):
            lab[mon] = f6.month_clusters(wo, w.t0d, mon)
        if wn == "wa":
            keep = wo
        else:
            del wo
    return lab, keep


def g_eq_a(rows, win, side, procs, coins, k_eq, labels):
    rng = np.random.default_rng(SEED)
    if k_eq and k_eq < len(rows):
        rows = rows.iloc[np.sort(rng.choice(len(rows), k_eq, replace=False))].reset_index(drop=True)
    info = {}
    t0 = time.time()
    got = fam.compute(rows, win, side, procs=procs, labels=labels, info=info)
    sec = time.time() - t0
    u = r5.Uni("h775")
    wobj = f6.Win(u.wdir(win), coins)
    ev = f6.load_events("h775", win, SIDE_CODE[side])
    ct = f6.read_contracts()
    lab_orig = {m: f6.month_clusters(wobj, next(x for x in st.TRADE_WINDOWS if x.name == f6.REFRESH_WINDOW[m]).t0d, m) for m in f6.REFRESH_WINDOW if f6.REFRESH_WINDOW[m] == win}
    lab_orig.update({m: labels[m] for m in labels if m not in lab_orig})       # метки месяцев других окон — те, что проверены в G-clu
    t1 = time.time()
    ref = pd.DataFrame([f6.row_values(wobj, lab_orig, ev, x.sym, int(x.sig_ms), int(x.abs_ms), ct.get(x.sym)) for x in rows.itertuples()], index=rows["row_id"].to_numpy())
    ref_sec = time.time() - t1
    cols = [c for c in fam.COLS if c not in ("turn24h", "natr_bp")]
    bad, nanpat, mx = cmp_frames(got, ref, cols)
    bad.update({c: bad[c] + nanpat[c] for c in cols})
    # turn24h и natr_bp — по определению
    t = (rows["sig_ms"].to_numpy(np.int64) // 60_000)
    turn_ref = np.full(len(rows), np.nan)
    cache = os.path.join(u.wdir(win), "cache")
    for sym in np.unique(rows["sym"].to_numpy(dtype=object).astype(str)):
        p = os.path.join(cache, sym + ".b42")
        if not os.path.exists(p):
            continue
        qv = ft.load_b42_full(p)["qv"]
        for i in np.flatnonzero(rows["sym"].to_numpy(dtype=object).astype(str) == sym):
            turn_ref[i] = float(qv[t[i] - 1440:t[i]].sum()) / 1e5 if t[i] >= 1440 else np.nan
    bad["turn24h"] = int((~((got["turn24h"].to_numpy() == turn_ref) | (np.isnan(got["turn24h"].to_numpy()) & np.isnan(turn_ref)))).sum())
    bad["natr_bp"] = int((got["natr_bp"].to_numpy() != rows["natr_e8"].to_numpy() / 1e4).sum())
    n_bad = sum(bad.values())
    return dict(rows=int(len(rows)), mismatches=bad, max_abs=mx, seconds_mine=round(sec, 2), seconds_original_rowwise=round(ref_sec, 2), non_nan=info["non_nan_share"],
                ms_per_row_mine=round(1000 * sec / max(len(rows), 1), 4), ms_per_row_original=round(1000 * ref_sec / max(len(rows), 1), 4)), n_bad


def g_eq_b(procs, labels):
    """Таблицы TK-636 получены прежней сборкой на другом numpy: допуск 1e-9 (относительный при |y| ≥ 1), рисунок NaN — точный."""
    u = r5.Uni("h775")
    out, n_bad, n_rows = {}, 0, 0
    for w in st.TRADE_WINDOWS:
        for side, code in (("long", "L"), ("short", "S")):
            sx = pd.read_csv(os.path.join(u.base(w.name, code), r5.ENG[code] + "-sigsx.csv"), usecols=["sym", "sig_ms", "natr_e8"])
            sx = sx.drop_duplicates(["sym", "sig_ms"]).reset_index(drop=True)
            rows = pd.DataFrame(dict(row_id=np.arange(len(sx), dtype=np.int64), sym=sx["sym"], sig_ms=sx["sig_ms"].astype(np.int64), natr_e8=sx["natr_e8"].astype(np.int64)))
            rows["win"], rows["side"] = w.name, side
            rows["abs_ms"] = w.t0_ms + rows["sig_ms"]
            got = fam.compute(rows, w.name, side, procs=procs, labels=labels)
            tab = pd.read_csv(os.path.join(TABLES, "%s-%s.csv" % (w.name, code)), dtype={"sym": str})
            mg = rows[["row_id", "sym", "sig_ms"]].merge(tab, on=["sym", "sig_ms"], how="left", validate="one_to_one")
            missing = int(abs(len(rows) - len(rows.merge(tab[["sym", "sig_ms"]], on=["sym", "sig_ms"]))) + abs(len(tab) - len(rows)))      # ключи таблицы и строк совпадают взаимно однозначно
            g = got.loc[mg["row_id"].to_numpy()]
            bad, nanpat, mx = cmp_frames(g, mg, list(fam.COLS), tol=1e-9)
            exact, _, _ = cmp_frames(g, mg, list(fam.COLS), tol=0.0)
            n_bad += sum(bad.values()) + sum(nanpat.values()) + missing
            n_rows += len(rows)
            n_vals = len(rows) * len(fam.COLS)
            out["%s-%s" % (w.name, side)] = dict(rows=int(len(rows)), table_rows=int(len(tab)), keys_not_matched=missing, beyond_tol_1e9=sum(bad.values()), nan_pattern_diffs=sum(nanpat.values()), exact_equal_share=round(1 - sum(exact.values()) / n_vals, 4), max_abs=mx)
    return dict(rows_total=int(n_rows), groups=out), n_bad


def g_causal(rows, win, side, k, coins, labels, procs):
    rng = np.random.default_rng(SEED + 1)
    u = r5.Uni("h775")
    sub = rows.iloc[np.sort(rng.choice(len(rows), min(k, len(rows)), replace=False))].reset_index(drop=True)
    t_all = sub["sig_ms"].to_numpy(np.int64) // 60_000
    sym_all = sub["sym"].to_numpy(dtype=object).astype(str)
    rows_by_coin = {s: np.flatnonzero(sym_all == s) for s in np.unique(sym_all)}
    world, turn = fam.load_world(os.path.join(u.wdir(win), "cache"), coins, procs=procs, rows_by_coin=rows_by_coin, turn_t=t_all)
    evs, evc = fam.load_events(win, side)
    kw = dict(procs=1, labels=labels, contracts_path=None, coins=coins)
    base = fam.compute(sub, win, side, world=world, turn=turn, events=(evs, evc), **kw)
    cols = [c for c in fam.COLS if c not in ("turn24h", "natr_bp")]
    bad = leak_differs = n_cmp = 0
    bi, ei = world.ix[f6.REFS[0]], world.ix[f6.REFS[1]]
    for i, x in enumerate(sub.itertuples()):
        t, ts = int(t_all[i]), int(x.sig_ms)
        ci = world.ix.get(x.sym)
        series = {bi, ei}
        if ci is not None:
            series.add(ci)
            lab = labels.get(int(fam.month_of_vec([x.abs_ms])[0]))
            if lab is not None and lab[ci] >= 0:
                series |= set(np.flatnonzero(lab == lab[ci]).tolist())
        idx = np.array(sorted(series), dtype=np.int64)
        saved1, saved5 = world.R1[idx, t:].copy(), world.R5[idx, t // 5:].copy()
        world.R1[idx, t:] = rng.normal(0, 0.05, saved1.shape)
        world.R5[idx, t // 5:] = rng.normal(0, 0.05, saved5.shape)
        late = evc >= ts
        c2 = evc.copy()
        c2[late] = ts + rng.integers(0, 6 * 3_600_000, int(late.sum()))
        ev2 = (np.concatenate([evs, rng.choice(np.array(coins, dtype=object), 20)]), np.concatenate([c2, ts + rng.integers(0, 3_600_000, 20)]))
        one = sub.iloc[[i]]
        got = fam.compute(one, win, side, world=world, turn=None, events=ev2, **kw)
        ok = all(same(got.iloc[0][c], base.iloc[i][c]) for c in cols)
        bad += int(not ok)
        n_cmp += len(cols)
        if x.sym in world.ix and world.have[world.ix[x.sym]]:
            qv = ft.load_b42_full(os.path.join(u.wdir(win), "cache", x.sym + ".b42"))["qv"].copy()
            qv[t:] = rng.integers(1, 10 ** 9, len(qv) - t)
            bad += int(not same(fam.turn24h_rows(qv, np.array([t]))[0], base.iloc[i]["turn24h"]))
        late_row = one.assign(sig_ms=ts + 10 * 60_000, abs_ms=int(x.abs_ms) + 10 * 60_000)           # контроль силы: на t + 10 мин мусор читается
        got2 = fam.compute(late_row, win, side, world=world, turn=None, events=ev2, **kw)
        leak_differs += int(not all(same(got2.iloc[0][c], base.iloc[i][c]) for c in cols))
        world.R1[idx, t:], world.R5[idx, t // 5:] = saved1, saved5
    # кластеры: мусор в доходностях после дня обновления
    res_clu = {}
    wins = {}
    for mon in sorted(f6.REFRESH_WINDOW):
        wn = f6.REFRESH_WINDOW[mon]
        if wn not in wins:
            wins = {wn: f6.Win(u.wdir(wn), coins)}
        wo = wins[wn]
        w = next(y for y in st.TRADE_WINDOWS if y.name == wn)
        a_, b_ = f6.refresh_blocks(w.t0d, datetime.date(2026, mon, 1))
        junk = wo.R5.copy()
        junk[:, b_:] = rng.normal(0, 0.01, junk[:, b_:].shape)
        same_lab = bool(np.array_equal(f6.cluster_labels(junk, wo.ix[f6.REFS[0]], wo.ix[f6.REFS[1]], a_, b_), labels[mon]))
        res_clu[mon] = same_lab
        bad += int(not same_lab)
    return dict(rows=int(len(sub)), values_compared=int(n_cmp), bad=int(bad), control_t_plus_10min_differs=int(leak_differs), clusters_junk_after_refresh_unchanged=res_clu), bad


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--k-causal", type=int, default=600)
    ap.add_argument("--k-eq", type=int, default=0)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows)
    u = r5.Uni("h775")
    coins = fam.universe_coins()
    assert coins == u.coins(), "порядок вселенной ≠ Uni('h775').coins()"
    labels = fam.month_labels(sorted(f6.REFRESH_WINDOW), coins, labels_dir=fam.LABELS_DIR, procs=a.procs)
    res, nbad = {}, 0
    steps = (("G-clu", lambda: g_clu(coins, a.procs, labels)), ("G-eq-A", lambda: g_eq_a(rows, a.win, a.side, a.procs, coins, a.k_eq, labels)), ("G-eq-B", lambda: g_eq_b(a.procs, labels)),
             ("G-causal", lambda: g_causal(rows, a.win, a.side, a.k_causal, coins, labels, a.procs)))
    for name, fn in steps:
        if a.only and name not in a.only.split(","):
            continue
        v, b = fn()
        v = dict(v) if isinstance(v, dict) else {"result": v}
        v["fail"] = int(b)
        res[name] = v
        nbad += b
        print(name, "FAIL %d" % b if b else "ok", json.dumps({k: v[k] for k in v if k in ("rows", "rows_total", "max_abs", "ms_per_row_mine", "ms_per_row_original", "control_t_plus_10min_differs")}), flush=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    res["fail_total"] = int(nbad)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    sys.exit(1 if nbad else 0)


if __name__ == "__main__":
    main()
