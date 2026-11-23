#!/usr/bin/env python3
"""П-42 TK-697: ворота семейства `m697` (`tk697_cols`) на srv, без P&L.
  python3 tk697_gate.py --rows <rows.parquet> --data <side>.parquet --win wa --side long --out <gate.json> [--procs 2] [--k-brute 1500] [--k-causal 600] [--k-file 20]

G-brute   `compute` (боевой путь: пул по монетам, ядра numba) = независимый построчный счёт на чистом Python (`brute_row`: свои циклы, свой NATR по определению
          «истинный диапазон от последней минутки с данными», без префиксных сумм) на k строках реального файла (случайные + самые «молодые» по истории монеты):
          одинаковый рисунок NaN, относительная ошибка ≤ 1e-9.
G-causal  k строк: минутки монеты и BTC с минуты t и дальше заменены мусором (`tk668_feat.garbage_from`) — все 16 значений строки не меняются;
          контроль силы — те же значения, если мусор начинается с минуты t − 1 (последняя закрытая), отличаются хотя бы в части строк.
G-file    k строк через `compute` с файлами `.b42` (мусор записан в каталог): то же равенство — путь чтения файлов и сборки."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk668_feat as ft  # noqa: E402
import tk697_cols as fam  # noqa: E402

SEED = 697
NAN = float("nan")


# ---- независимый построчный счёт -------------------------------------------------------------------------------------------------------

def natr_at(o, h, l, c, b, p=fam.NATR_P):
    """NATR p в минуте b по определению: TR минутки m (с данными) = max(h − l, |h − prevc|, |l − prevc|), prevc — закрытие последней минутки с данными ДО m (0 — нет)."""
    if b < p - 1 or b >= len(o) or o[b] == 0:
        return NAN
    tr = 0
    k = 0
    for m in range(b - p + 1, b + 1):
        if o[m] == 0:
            continue
        prevc = 0
        for j in range(m - 1, -1, -1):
            if o[j] != 0:
                prevc = c[j]
                break
        x = h[m] - l[m]
        if prevc != 0:
            x = max(x, abs(h[m] - prevc), abs(l[m] - prevc))
        tr += x
        k += 1
    if k * 2 < p:
        return NAN
    return tr / (k * c[b])


def ffill_py(o, c):
    out, last = [], NAN
    for oi, ci in zip(o, c):
        if oi != 0:
            last = float(ci)
        out.append(last)
    return out


_LC = {}


def lists_of(a):
    """(o, h, l, c, cf) монеты списками Python — по одному разу на массив `a` (кэш на 3 монеты: строки выборки идут по монетам подряд)."""
    k = id(a)
    if k not in _LC:
        if len(_LC) > 3:
            _LC.clear()
        o, h, l, c = (a[x].tolist() for x in ("o", "h", "l", "c"))
        _LC[k] = (o, h, l, c, ffill_py(o, c))
    return _LC[k]


def round_py(L):
    if not (L > 0) or math.isnan(L):
        return NAN
    k0 = math.floor(math.log10(L))
    best = math.inf
    for dk in (-1, 0, 1):
        for m in (1.0, 2.0, 5.0):
            r = m * 10.0 ** (k0 + dk)
            if abs(L - r) < best:
                best = abs(L - r)
    return best


def brute_row(a, ab, t, dur, s, level):
    """16 значений строки (порядок `fam.COLUMNS`) по сырым минуткам монеты `a` и BTC `ab` (None — нет), минуте сигнала `t`, знаку `s`, approach_dur_min, цене уровня."""
    o, h, l, c, cf = lists_of(a)
    b = t - 1
    nat = natr_at(o, h, l, c, b)
    out = []
    ds, ags = [], []
    for w in fam.SW_W:
        d = ag = NAN
        if b - 2 * w + 1 >= 0 and b < len(o) and nat == nat and nat > 0 and cf[b] == cf[b] and cf[b] != 0:
            def fr_low(m):
                return (l[m] if s > 0 else -h[m]) if o[m] != 0 else None
            la = [(fr_low(m), m) for m in range(b - w + 1, b + 1) if fr_low(m) is not None]
            lb = [fr_low(m) for m in range(b - 2 * w + 1, b - w + 1) if fr_low(m) is not None]
            if la and lb:
                low_a = min(x for x, _ in la)
                m_a = max(m for x, m in la if x == low_a)
                low_b = min(lb)
                cfs = s * cf[b]
                if low_a < low_b and cfs >= low_b:
                    d = (low_b - low_a) / abs(cfs) / nat
                    ag = float(t - m_a)
                else:
                    d = 0.0
        ds.append(d)
        ags.append(ag)
    out += ds + ags

    def ret(e, hh):
        if e < 0 or e - hh < 0 or e >= len(cf):
            return NAN
        c1, c0 = cf[e], cf[e - hh]
        if math.isnan(c1) or math.isnan(c0) or c0 == 0:
            return NAN
        return c1 / c0 - 1.0

    e = -1 if math.isnan(dur) else int(round(t - dur)) - 1
    rr = {hh: ret(e, 60 * hh) for hh in fam.PM_H}
    out += [100.0 * s * rr[hh] for hh in fam.PM_H]
    out += [s * rr[hh] / (nat * math.sqrt(1440.0)) for hh in fam.PM_H]
    r6 = ret(e, fam.SHARP_NUM_MIN)
    out.append(abs(r6) / abs(rr[72]) if (rr[72] == rr[72] and rr[72] != 0 and r6 == r6) else NAN)

    def sod(arr):
        if arr is None or b >= len(arr["o"]):
            return NAN
        d0 = (t // 1440) * 1440
        if b < d0:
            return NAN
        ao, _, _, _, cfa = lists_of(arr)
        for d in range(d0, b + 1):
            if ao[d] != 0:
                return cfa[b] / float(ao[d]) - 1.0 if cfa[b] == cfa[b] else NAN
        return NAN

    out.append(s * sod(a) - s * sod(ab) if (sod(a) == sod(a) and sod(ab) == sod(ab)) else NAN)
    dist = round_py(level)
    out.append(100.0 * dist / level if level > 0 else NAN)
    out.append(dist / level / nat if (level > 0 and nat == nat and nat > 0) else NAN)
    assert len(out) == len(fam.COLUMNS)
    return out


def compare(got, ref, atol=1e-12, rtol=1e-9):
    got, ref = np.asarray(got, dtype=float), np.asarray(ref, dtype=float)
    nan_g, nan_r = np.isnan(got), np.isnan(ref)
    fin = ~nan_g & ~nan_r
    d = np.where(fin, np.abs(got - ref), 0.0)
    bad = (nan_g != nan_r) | (fin & (d > atol + rtol * np.abs(np.where(fin, ref, 0.0))))
    return bad.sum(axis=0), float(d.max()) if d.size else 0.0


def load_arr(cache_dir, sym, store):
    if sym not in store:
        p = os.path.join(cache_dir, sym + ".b42")
        store[sym] = ft.load_b42_full(p) if os.path.exists(p) else None
    return store[sym]


def write_b42(path, a):
    n = len(a["o"])
    with open(path, "wb") as f:
        f.write(b"B42\x01" + n.to_bytes(4, "little"))
        np.stack([a[k] for k in ("o", "h", "l", "c", "qv")]).astype("<i8").tofile(f)


def pick(rows, k, rng):
    """k строк: половина случайных, половина — с самыми короткими историями монеты (ближе к листингу)."""
    n = len(rows)
    if n <= k:
        return np.arange(n)
    first = rows.groupby("sym")["sig_ms"].transform("min").to_numpy()
    young = np.argsort((rows["sig_ms"].to_numpy() - first), kind="stable")[: k // 2]
    rest = rng.choice(np.setdiff1d(np.arange(n), young), k - len(young), replace=False)
    return np.sort(np.concatenate([young, rest]))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--k-brute", type=int, default=1200)
    ap.add_argument("--k-causal", type=int, default=300)
    ap.add_argument("--k-file", type=int, default=12)
    ap.add_argument("--cache-dir")
    ap.add_argument("--tmp", default="/data/p42-tk697/tmp")
    a = ap.parse_args()
    os.makedirs(a.tmp, exist_ok=True)
    rng = np.random.default_rng(SEED)
    cache_dir = a.cache_dir or os.path.join(fam.CACHE_ROOT, a.win, "cache")
    rows = fam.attach_dur(pd.read_parquet(a.rows), a.data, a.win).reset_index(drop=True)
    s = fam.SIGN[a.side]
    res = {"win": a.win, "side": a.side, "rows": int(len(rows))}
    store = {}
    abt = load_arr(cache_dir, fam.BTC, store)

    # G-brute: боевой путь целиком против построчного счёта
    full = fam.compute(rows, a.win, a.side, a.procs, cache_dir=cache_dir)
    ix = pick(rows, a.k_brute, rng)
    ix = ix[np.argsort(rows["sym"].to_numpy(dtype=object).astype(str)[ix], kind="stable")]
    got = full.iloc[ix].to_numpy()
    ref = np.array([brute_row(load_arr(cache_dir, rows["sym"].iat[i], store) if load_arr(cache_dir, rows["sym"].iat[i], store) is not None else None, abt,
                              int(rows["sig_ms"].iat[i] // ft.MIN_MS), float(rows["approach_dur_min"].iat[i]), s,
                              abs(int(rows["level_px_e8"].iat[i])) / 1e8) if load_arr(cache_dir, rows["sym"].iat[i], store) is not None
                    else [NAN] * len(fam.COLUMNS) for i in ix])
    bad, dmax = compare(got, ref)
    res["g_brute"] = dict(k=int(len(ix)), bad_by_col={c: int(x) for c, x in zip(fam.COLUMNS, bad) if x}, max_abs_diff=dmax, ok=bool(bad.sum() == 0),
                          nan_share={c: round(float(np.isnan(got[:, j]).mean()), 4) for j, c in enumerate(fam.COLUMNS)})

    # G-causal: мусор с минуты t и дальше (монета и BTC) не меняет строку; контроль силы — мусор с t − 1 меняет
    jx = pick(rows, a.k_causal, rng)
    bad_c = 0
    changed = 0
    for i in jx:
        sym = rows["sym"].iat[i]
        am = load_arr(cache_dir, sym, store)
        if am is None:
            continue
        t = int(rows["sig_ms"].iat[i] // ft.MIN_MS)
        one = rows.iloc[[i]]
        base = fam.compute(one, a.win, a.side, 1, cache_dir=cache_dir).to_numpy()
        for t_g, strong in ((t, False), (t - 1, True)):
            tmp = tempfile.mkdtemp(prefix="g697-", dir=a.tmp)
            write_b42(os.path.join(tmp, sym + ".b42"), ft.garbage_from(am, t_g, rng))
            if abt is not None and sym != fam.BTC:
                write_b42(os.path.join(tmp, fam.BTC + ".b42"), ft.garbage_from(abt, t_g, rng))
            g = fam.compute(one, a.win, a.side, 1, cache_dir=tmp).to_numpy()
            shutil.rmtree(tmp, ignore_errors=True)
            same = np.array_equal(base, g, equal_nan=True)
            if not strong and not same:
                bad_c += 1
            if strong and not same:
                changed += 1
    res["g_causal"] = dict(k=int(len(jx)), bad=int(bad_c), control_changed=int(changed), ok=bool(bad_c == 0 and changed > 0))

    # G-file: путь чтения файлов — те же значения на малой выборке по k-file строкам через compute с каталогом мусора после t
    fx = pick(rows, a.k_file, rng)
    bad_f = 0
    for i in fx:
        sym = rows["sym"].iat[i]
        am = load_arr(cache_dir, sym, store)
        if am is None:
            continue
        t = int(rows["sig_ms"].iat[i] // ft.MIN_MS)
        one = rows.iloc[[i]]
        tmp = tempfile.mkdtemp(prefix="f697-", dir=a.tmp)
        write_b42(os.path.join(tmp, sym + ".b42"), ft.garbage_from(am, t, rng))
        if abt is not None and sym != fam.BTC:
            write_b42(os.path.join(tmp, fam.BTC + ".b42"), ft.garbage_from(abt, t, rng))
        g = fam.compute(one, a.win, a.side, 1, cache_dir=tmp).to_numpy()
        shutil.rmtree(tmp, ignore_errors=True)
        if not np.array_equal(full.iloc[[i]].to_numpy(), g, equal_nan=True):
            bad_f += 1
    res["g_file"] = dict(k=int(len(fx)), bad=int(bad_f), ok=bool(bad_f == 0))
    res["ok"] = bool(res["g_brute"]["ok"] and res["g_causal"]["ok"] and res["g_file"]["ok"])
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: (v if not isinstance(v, dict) else v.get("ok")) for k, v in res.items()}))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
