#!/usr/bin/env python3
"""П-42 TK-697 (№ 8), семейство `h697`: расстояние уровня до максимума / минимума монеты за 30 / 90 / 365 суток по дневным свечам биржи (12 столбцов).

  python3 tk697_hl.py build-daily --root /data/p42-tk697/daily --out /data/p42-tk697/daily/daily.parquet      # zip data.binance.vision 1d → таблица (sym, day, o, h, l, c; цены e8)
  python3 tk697_hl.py build --rows <rows.parquet> --daily <daily.parquet> --win wa --side long --out <out.parquet> [--cache-dir <каталог .b42 окна>]
  compute(rows, daily, win, side, cache_dir=None, info=None) -> DataFrame (индекс row_id, float64, NaN = не определено, строки не выбрасываются)

Определение (окна N ∈ {30, 90, 365} суток, назначены критиком TK-697). D — сутки сигнала (UTC), t — минута сигнала, b = t − 1 — последняя закрытая минута. Окно N:
дневные максимум / минимум за N ПОЛНЫХ суток D − N … D − 1 (меньше, если монета листингом моложе: берутся все имеющиеся сутки с листинга, `AGE` рядом) плюс минутки
суток D с 00:00 UTC до b включительно (из кэша `.b42`; сутки D дневной свечой не читаются — она не закрыта). H_N, L_N — получившиеся максимум и минимум.
  hl_hi_p{N} = 100 · (H_N − L) / L,  hl_lo_p{N} = 100 · (L − L_N) / L          (в %, L = |level_px_e8| / 1e8; 0 — уровень равен экстремуму, < 0 — уровень за экстремумом)
  hl_hi_n{N} = (H_N − L) / L / NATR60[b],  hl_lo_n{N} = (L − L_N) / L / NATR60[b]  (в NATR 60 на минуте b, как у `m697`)
Нет ни одних суток истории и ни одной минутки суток D с данными — NaN. Причинность: дневные свечи суток ≥ D и минутки ≥ t не читаются (сверка — `tk697_hl_gate.py`)."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk668_feat as ft  # noqa: E402
from tk668_feat import njit  # noqa: E402
import tk697_cols as m6  # noqa: E402

NS = (30, 90, 365)
DAY_MS = 86_400_000
DAY_MIN = 1440
CACHE_ROOT = "/data/p42-tk621"
NAN = float("nan")
COLUMNS = ([f"hl_hi_p{n}" for n in NS] + [f"hl_lo_p{n}" for n in NS] + [f"hl_hi_n{n}" for n in NS] + [f"hl_lo_n{n}" for n in NS])


# ---- дневные свечи ---------------------------------------------------------------------------------------------------------------------

def e8(x):
    """Десятичная цена из CSV → целое e8 (как кэш минуток)."""
    return int(round(float(x) * 1e8))


def parse_zip(path):
    """Строки 1d-свечей из zip data.binance.vision: [(день UTC (число суток с эпохи), o, h, l, c) e8], без заголовка."""
    rows = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            with z.open(name) as f:
                for rec in csv.reader(io.TextIOWrapper(f, encoding="utf-8")):
                    if not rec or not rec[0].strip().isdigit():
                        continue
                    ms = int(rec[0])
                    if ms % DAY_MS:
                        raise ValueError("%s: open_time %d не на границе суток" % (path, ms))
                    rows.append((ms // DAY_MS, e8(rec[1]), e8(rec[2]), e8(rec[3]), e8(rec[4])))
    return rows


def build_daily(root, out):
    import pandas as pd
    recs = []
    for sym in sorted(os.listdir(root)):
        d = os.path.join(root, sym)
        if sym == "log-batch" or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".zip"):
                for r in parse_zip(os.path.join(d, fn)):
                    recs.append((sym,) + r)
    df = pd.DataFrame(recs, columns=["sym", "day", "o", "h", "l", "c"]).drop_duplicates(["sym", "day"]).sort_values(["sym", "day"]).reset_index(drop=True)
    df.to_parquet(out)
    return df


def daily_arrays(daily):
    """sym → (дни int64 по возрастанию, high float64, low float64)."""
    out = {}
    for sym, g in daily.groupby("sym", sort=False):
        g = g.sort_values("day")
        out[sym] = (g["day"].to_numpy(np.int64), g["h"].to_numpy(np.float64), g["l"].to_numpy(np.float64))
    return out


# ---- ядра ------------------------------------------------------------------------------------------------------------------------------

@njit(cache=True)
def hist_kernel(days, dh, dl, d_sig, n, out_hi, out_lo):
    """max(high) / min(low) по дневным свечам с днями в [D − n, D − 1]; нет ни одних — NaN."""
    lo_i = np.searchsorted(days, d_sig - n, side="left")
    hi_i = np.searchsorted(days, d_sig, side="left")
    for i in range(len(d_sig)):
        out_hi[i] = np.nan
        out_lo[i] = np.nan
        a, b = lo_i[i], hi_i[i]
        if b <= a:
            continue
        mx = dh[a]
        mn = dl[a]
        for j in range(a + 1, b):
            if dh[j] > mx:
                mx = dh[j]
            if dl[j] < mn:
                mn = dl[j]
        out_hi[i] = mx
        out_lo[i] = mn


@njit(cache=True)
def today_kernel(o, h, l, b, d0, out_hi, out_lo):
    """max(high) / min(low) минуток с данными в [d0, b] (d0 — индекс 00:00 UTC суток сигнала для каждой строки); пусто — NaN."""
    n = len(o)
    for i in range(len(b)):
        out_hi[i] = np.nan
        out_lo[i] = np.nan
        bi = b[i]
        if bi < 0 or bi >= n:
            continue
        mx = -np.inf
        mn = np.inf
        for m in range(max(d0[i], 0), bi + 1):
            if o[m] != 0:
                if h[m] > mx:
                    mx = h[m]
                if l[m] < mn:
                    mn = l[m]
        if np.isfinite(mx):
            out_hi[i] = mx
            out_lo[i] = mn


def warm_up():
    z = np.zeros(4, dtype=np.int64)
    x = np.zeros(4)
    o = np.empty(1)
    hist_kernel(z, x, x, z[:1], 1, o, o.copy())
    today_kernel(z, z, z, z[:1], z[:1], o, o.copy())


# ---- сборка ----------------------------------------------------------------------------------------------------------------------------

def merge_ext(hist_hi, hist_lo, t_hi, t_lo):
    """Объединение экстремумов: NaN игнорируется; оба NaN — NaN."""
    hi = np.fmax(hist_hi, t_hi)
    lo = np.fmin(hist_lo, t_lo)
    return hi, lo


def compute(rows, daily, win, side, cache_dir=None, info=None):
    """`rows` — row_id, sym, sig_ms, abs_ms, level_px_e8 (+ win, side — проверяются); `daily` — таблица `build_daily` (или её `daily_arrays`)."""
    import pandas as pd
    t_start = time.time()
    for col in ("win", "side"):
        if col in rows.columns and not (rows[col] == (win if col == "win" else side)).all():
            raise ValueError("строки не одного (окна, стороны): %s" % col)
    if rows["row_id"].duplicated().any():
        raise ValueError("row_id не уникален")
    import tk604_stats as st
    st.check_abs_ms(rows["abs_ms"].to_numpy(np.int64), "строка TK-671")
    cache_dir = cache_dir or os.path.join(CACHE_ROOT, win, "cache")
    arrs = daily if isinstance(daily, dict) else daily_arrays(daily)
    n = len(rows)
    abs_ms = rows["abs_ms"].to_numpy(np.int64)
    sig_ms = rows["sig_ms"].to_numpy(np.int64)
    t0 = abs_ms - sig_ms
    if len(t0) and (t0 != t0[0]).any():
        raise ValueError("abs_ms − sig_ms не постоянен: начало окна разное")
    if len(t0) and t0[0] % DAY_MS:
        raise ValueError("начало окна не на границе суток")
    b = sig_ms // ft.MIN_MS - 1
    d_sig = abs_ms // DAY_MS
    d0 = (sig_ms // ft.MIN_MS // DAY_MIN) * DAY_MIN
    level = np.abs(rows["level_px_e8"].to_numpy(np.int64)).astype(np.float64) / 1e8
    L8 = level * 1e8
    syms, sym_code = np.unique(rows["sym"].to_numpy(dtype=object).astype(str), return_inverse=True)
    out = np.full((n, len(COLUMNS)), np.nan)
    stat = dict(win=win, side=side, rows=int(n), coins=int(len(syms)), missing_cache=[], missing_daily=[])
    warm_up()
    for k, sym in enumerate(syms):
        idx = np.flatnonzero(sym_code == k)
        path = os.path.join(cache_dir, sym + ".b42")
        if not os.path.exists(path):
            stat["missing_cache"].append(sym)
            continue
        a = ft.load_b42_full(path)
        if sym not in arrs:
            stat["missing_daily"].append(sym)
            days, dh, dl = np.zeros(0, np.int64), np.zeros(0), np.zeros(0)
        else:
            days, dh, dl = arrs[sym]
        nat = m6.natr_kernel(a["o"], a["h"], a["l"], a["c"], m6.NATR_P)
        nb = np.where((b[idx] >= 0) & (b[idx] < len(nat)), nat[np.clip(b[idx], 0, len(nat) - 1)], np.nan)
        t_hi, t_lo = np.empty(len(idx)), np.empty(len(idx))
        today_kernel(a["o"], a["h"].astype(np.float64), a["l"].astype(np.float64), b[idx], d0[idx], t_hi, t_lo)
        for j, nn in enumerate(NS):
            h_hi, h_lo = np.empty(len(idx)), np.empty(len(idx))
            hist_kernel(days, dh, dl, d_sig[idx], nn, h_hi, h_lo)
            hi, lo = merge_ext(h_hi, h_lo, t_hi, t_lo)
            with np.errstate(invalid="ignore", divide="ignore"):
                out[idx, j] = 100.0 * (hi - L8[idx]) / L8[idx]
                out[idx, len(NS) + j] = 100.0 * (L8[idx] - lo) / L8[idx]
                out[idx, 2 * len(NS) + j] = (hi - L8[idx]) / L8[idx] / nb
                out[idx, 3 * len(NS) + j] = (L8[idx] - lo) / L8[idx] / nb
    df = pd.DataFrame(out, index=pd.Index(rows["row_id"].to_numpy(np.int64), name="row_id"), columns=list(COLUMNS))
    stat["seconds"] = round(time.time() - t_start, 3)
    stat["ms_per_row"] = round(1000.0 * stat["seconds"] / max(n, 1), 4)
    stat["non_nan_share"] = {c: round(float(np.isfinite(df[c]).mean()), 6) if n else 0.0 for c in COLUMNS}
    if info is not None:
        info.update(stat)
    return df


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b1 = sub.add_parser("build-daily")
    b1.add_argument("--root", required=True)
    b1.add_argument("--out", required=True)
    b2 = sub.add_parser("build")
    b2.add_argument("--rows", required=True)
    b2.add_argument("--daily", required=True)
    b2.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    b2.add_argument("--side", required=True, choices=["long", "short"])
    b2.add_argument("--out", required=True)
    b2.add_argument("--cache-dir")
    a = ap.parse_args()
    if a.cmd == "build-daily":
        df = build_daily(a.root, a.out)
        print(json.dumps(dict(rows=int(len(df)), coins=int(df["sym"].nunique()), day_min=int(df["day"].min()), day_max=int(df["day"].max()))))
        return
    rows = pd.read_parquet(a.rows)
    info = {}
    df = compute(rows, pd.read_parquet(a.daily), a.win, a.side, cache_dir=a.cache_dir, info=info)
    df.to_parquet(a.out)
    with open(a.out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: info[k] for k in ("rows", "seconds", "ms_per_row")}), flush=True)


if __name__ == "__main__":
    main()
