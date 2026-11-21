#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): ворота семейства mkt (`tk671_mkt`) на srv, без P&L.
  python3 tk671_gate_mkt.py --rows <rows.parquet> --win wa --side long --out <gate.json> [--got <готовый выход compute.parquet>] [--procs 3] [--k-brute 1500] [--k-causal 600] [--k-file 30]

G-brute   `compute` (боевой путь целиком: пул по монетам, ядра) = независимый построчный счёт на чистом Python / numpy (`brute_row`: свой ffill, свои окна и суммы, `np.std`, `np.corrcoef`)
          на k строках реального файла (случайные + самые «молодые» по истории монеты): одинаковый рисунок NaN, |Δ| ≤ 1e-12 + 1e-9·|эталон|; печатаются max |Δ|, max относительная, доля точно равных.
G-causal  k строк: все минутки монеты, BTC и ETH с минуты t и дальше заменены мусором (`tk668_feat.garbage_from`) — все 27 значений строки не меняются (точное равенство, одинаковый рисунок NaN);
          контроль силы — те же значения на минуте t (мусорная минута — последняя закрытая) отличаются от значений на настоящих данных.
G-file    k строк через `compute` с файлами `.b42` (мусор записан в каталог): то же равенство — путь чтения файлов и сборки."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk668_feat as ft  # noqa: E402
import tk671_mkt as fam  # noqa: E402

SEED = 671
NAN = float("nan")


# ---- независимый построчный счёт ------------------------------------------------------------------------------------------------------------

def ffill_py(o, c):
    """Последнее закрытие с данными по минутам (список float): до первой минутки с данными — NaN (своя реализация, не `tk668_feat.ffill_close`)."""
    out, last = [], NAN
    for oi, ci in zip(o.tolist(), c.tolist()):
        if oi != 0:
            last = float(ci)
        out.append(last)
    return out


def brute_row(cf, cfb, cfe, t, s):
    """27 значений строки (порядок `fam.COLUMNS`) по спискам закрытий монеты / BTC / ETH (None — нет кэша), минуте сигнала `t`, знаку `s`."""
    b = t - 1

    def ret(arr, m, h):
        if arr is None or m >= len(arr) or m - h < 0:
            return NAN
        c1, c0 = arr[m], arr[m - h]
        if math.isnan(c1) or math.isnan(c0) or c0 == 0:
            return NAN
        return c1 / c0 - 1.0

    def sret(arr, h):
        r = ret(arr, b, h)
        return s * r

    def grid(arr, w):
        if arr is None or b >= len(arr):
            return None
        return [ret(arr, b - 5 * k, 5) for k in range(w // 5)]

    def rv(arr, w):
        g = grid(arr, w)
        if g is None:
            return NAN
        v = [x for x in g if not math.isnan(x)]
        return float(np.std(v, ddof=1)) if len(v) >= 10 else NAN

    def corr(w):
        gx, gy = grid(cf, w), grid(cfb, w)
        if gx is None or gy is None:
            return NAN
        pairs = [(x, y) for x, y in zip(gx, gy) if not math.isnan(x) and not math.isnan(y)]
        if len(pairs) < 10:
            return NAN
        x = np.array([p[0] for p in pairs])
        y = np.array([p[1] for p in pairs])
        if len(set(x.tolist())) == 1 or len(set(y.tolist())) == 1:
            return NAN
        return float(min(1.0, max(-1.0, np.corrcoef(x, y)[0, 1])))

    out = [sret(cfb, h) for h in fam.HORIZONS] + [sret(cfe, h) for h in fam.HORIZONS] + [sret(cf, h) for h in fam.HORIZONS]
    out += [sret(cf, h) - sret(cfb, h) for h in fam.REL_H]
    out += [rv(cfb, w) for w in fam.RV_W] + [rv(cf, w) for w in fam.RV_W] + [corr(w) for w in fam.CORR_W]
    assert len(out) == len(fam.COLUMNS)
    return out


def compare(got, ref, atol=1e-12, rtol=1e-9):
    """(число несовпадений по столбцам, max |Δ|, max относительная, число точно равных) для двух матриц одной формы; несовпадение — другой рисунок NaN или |Δ| > atol + rtol·|ref|."""
    got, ref = np.asarray(got, dtype=float), np.asarray(ref, dtype=float)
    nan_g, nan_r = np.isnan(got), np.isnan(ref)
    fin = ~nan_g & ~nan_r
    d = np.where(fin, np.abs(got - ref), 0.0)
    bad_val = fin & (d > atol + rtol * np.abs(np.where(fin, ref, 0.0)))
    bad = (nan_g != nan_r) | bad_val
    rel = np.where(fin & (np.abs(ref) > 0), d / np.where(np.abs(ref) > 0, np.abs(ref), 1.0), 0.0)
    exact = int(((got == ref) | (nan_g & nan_r)).sum())
    return bad.sum(axis=0), float(d.max()) if d.size else 0.0, float(rel.max()) if rel.size else 0.0, exact


def load_cf(cache_dir, sym, store):
    """(raw dict, cf float64 ndarray) монеты из кэша окна; None, если файла нет."""
    if sym not in store:
        p = os.path.join(cache_dir, sym + ".b42")
        if os.path.exists(p):
            a = ft.load_b42_full(p)
            store[sym] = (a, ft.ffill_close(a["o"], a["c"]))
        else:
            store[sym] = (None, None)
    return store[sym]


def write_b42(path, a):
    n = len(a["o"])
    with open(path, "wb") as f:
        f.write(b"B42\x01" + n.to_bytes(4, "little"))
        f.write(np.stack([a["o"], a["h"], a["l"], a["c"], a["qv"]]).astype("<i8").tobytes())


# ---- ворота -----------------------------------------------------------------------------------------------------------------------------

def g_brute(rows, got, win, side, k, store, cache_dir):
    rng = np.random.default_rng(SEED)
    s = fam.SIGN[side]
    t_all = rows["sig_ms"].to_numpy(np.int64) // ft.MIN_MS
    syms = rows["sym"].to_numpy(dtype=object).astype(str)
    # самые «молодые» строки: число минут от первой минутки монеты с данными до последней закрытой
    age = np.full(len(rows), np.inf)
    for sym in np.unique(syms):
        a, _ = load_cf(cache_dir, sym, store)
        if a is None:
            continue
        nz = np.flatnonzero(a["o"] != 0)
        first = nz[0] if len(nz) else len(a["o"])
        m = syms == sym
        age[m] = t_all[m] - 1 - first
    k_young = k // 5
    young = np.argsort(age, kind="stable")[:k_young]
    rest = np.setdiff1d(np.arange(len(rows)), young)
    pick = np.concatenate([young, rng.choice(rest, size=min(k - len(young), len(rest)), replace=False)])
    cb_raw, cfb_np = load_cf(cache_dir, fam.BTC, store)
    ce_raw, cfe_np = load_cf(cache_dir, fam.ETH, store)
    py = {}

    def pyc(sym):
        if sym not in py:
            a, cf_np = load_cf(cache_dir, sym, store)
            if a is None:
                py[sym] = None
            else:
                py[sym] = ffill_py(a["o"], a["c"])
                assert np.array_equal(np.asarray(py[sym]), cf_np, equal_nan=True), sym    # свой ffill = `tk668_feat.ffill_close`
        return py[sym]

    cfb, cfe = pyc(fam.BTC), pyc(fam.ETH)
    ref = np.empty((len(pick), len(fam.COLUMNS)))
    for j, i in enumerate(pick):
        ref[j] = brute_row(pyc(syms[i]), cfb, cfe, int(t_all[i]), s)
    g = got.to_numpy()[pick]
    bad, mx, mxr, exact = compare(g, ref)
    nan_share = {c: round(float(np.isnan(ref[:, q]).mean()), 4) for q, c in enumerate(fam.COLUMNS)}
    res = dict(rows=int(len(pick)), young_rows=int(len(young)), mismatches={c: int(bad[q]) for q, c in enumerate(fam.COLUMNS) if bad[q]}, max_abs=mx, max_rel=mxr,
               exact_cells=exact, cells=int(ref.size), nan_share_in_sample=nan_share)
    return res, int(bad.sum())


def g_causal(rows, win, side, k, store, cache_dir):
    rng = np.random.default_rng(SEED + 1)
    s = fam.SIGN[side]
    pick = rng.choice(len(rows), size=min(k, len(rows)), replace=False)
    _, cfb = load_cf(cache_dir, fam.BTC, store)
    _, cfe = load_cf(cache_dir, fam.ETH, store)
    ab, ae = store[fam.BTC][0], store[fam.ETH][0]
    bad = leaks_seen = 0
    for i in pick:
        x = rows.iloc[int(i)]
        t = int(x.sig_ms) // ft.MIN_MS
        a, cf = load_cf(cache_dir, str(x.sym), store)
        if a is None:
            continue
        base = fam.columns_for(cf, cfb, cfe, [t - 1], [s])
        ga, gb, ge = ft.garbage_from(a, t, rng), ft.garbage_from(ab, t, rng), ft.garbage_from(ae, t, rng)
        cg, cgb, cge = (ft.ffill_close(z["o"], z["c"]) for z in (ga, gb, ge))
        trash = fam.columns_for(cg, cgb, cge, [t - 1], [s])
        bad += int(not np.array_equal(base, trash, equal_nan=True))
        # контроль силы: на минуте t (мусорная минута — последняя закрытая) значения на мусоре и на настоящих данных различаются
        real_t = fam.columns_for(cf, cfb, cfe, [t], [s])
        trash_t = fam.columns_for(cg, cgb, cge, [t], [s])
        leaks_seen += int(not np.array_equal(real_t, trash_t, equal_nan=True))
    return dict(rows=int(len(pick)), bad=bad, control_garbage_visible_at_t=leaks_seen), bad


def g_file(rows, win, side, k, store, cache_dir, tmp):
    rng = np.random.default_rng(SEED + 2)
    pick = rng.choice(len(rows), size=min(k, len(rows)), replace=False)
    os.makedirs(tmp, exist_ok=True)
    bad = n = 0
    for i in pick:
        x = rows.iloc[[int(i)]]
        t = int(x["sig_ms"].iloc[0]) // ft.MIN_MS
        sym = str(x["sym"].iloc[0])
        a, _ = load_cf(cache_dir, sym, store)
        if a is None:
            continue
        base = fam.compute(x, win, side, procs=1, cache_dir=cache_dir)
        d = os.path.join(tmp, "g")
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for name in {sym, fam.BTC, fam.ETH}:
            write_b42(os.path.join(d, name + ".b42"), ft.garbage_from(load_cf(cache_dir, name, store)[0], t, rng))
        got = fam.compute(x, win, side, procs=1, cache_dir=d)
        bad += int(not np.array_equal(base.to_numpy(), got.to_numpy(), equal_nan=True))
        n += 1
    shutil.rmtree(os.path.join(tmp, "g"), ignore_errors=True)
    return dict(rows=n, bad=bad), bad


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--got")
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--k-brute", type=int, default=1500)
    ap.add_argument("--k-causal", type=int, default=600)
    ap.add_argument("--k-file", type=int, default=30)
    ap.add_argument("--tmp", default="/data/p42-tk671/fam/mkt/tmp")
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows)
    cache_dir = os.path.join(fam.CACHE_ROOT, a.win, "cache")
    store = {}
    info = {}
    if a.got:                                                                  # готовый выход CLI (то, что пойдёт в сборку); без него — считаем здесь
        got = pd.read_parquet(a.got)
        assert got.index.tolist() == rows["row_id"].tolist() and list(got.columns) == list(fam.COLUMNS), "выход не соответствует строкам"
        with open(a.got + ".info.json", encoding="utf-8") as f:
            info = json.load(f)
    else:
        got = fam.compute(rows, a.win, a.side, procs=a.procs, info=info)
    res, nbad = {"info": {k: info[k] for k in ("rows", "coins", "seconds", "ms_per_row", "missing_ref", "missing_cache")}}, 0
    print("compute:", json.dumps(res["info"]), flush=True)
    for name, fn in (("G-brute", lambda: g_brute(rows, got, a.win, a.side, a.k_brute, store, cache_dir)), ("G-causal", lambda: g_causal(rows, a.win, a.side, a.k_causal, store, cache_dir)),
                     ("G-file", lambda: g_file(rows, a.win, a.side, a.k_file, store, cache_dir, a.tmp))):
        v, b = fn()
        res[name] = v
        res[name]["fail"] = int(b)
        nbad += b
        print(name, "FAIL %d" % b if b else "ok", json.dumps({k: v[k] for k in v if k in ("rows", "max_abs", "max_rel", "exact_cells", "cells", "control_garbage_visible_at_t", "mismatches")}), flush=True)
    res["fail_total"] = int(nbad)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    sys.exit(1 if nbad else 0)


if __name__ == "__main__":
    main()
