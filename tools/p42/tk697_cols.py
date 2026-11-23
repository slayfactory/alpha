#!/usr/bin/env python3
"""П-42 TK-697, семейство `m697`: столбцы из разбора сделок трейдера пробоев, считаемые по минуткам `.b42` (тики не читаются), 16 штук.

  compute(rows, win, side, procs=3, cache_dir=None, info=None) -> DataFrame (индекс row_id, float64, NaN = не определено, строки не выбрасываются)
  python3 tk697_cols.py --rows <rows.parquet> --data <side>.parquet --win wa --side long --out <out.parquet> --procs 2 [--cache-dir <каталог .b42 окна>]

`rows` — строки `prod/lvl-<окно>-<side>/rows.parquet` TK-671 (row_id, sym, sig_ms, level_px_e8, abs_ms, win, side) + столбец `approach_dur_min` (из таблицы
признаков TK-671, ключ окно + row_id; `main` присоединяет и сверяет sym / sig_ms). Минута сигнала `t = sig_ms // 60000`, последняя ЗАКРЫТАЯ минута `b = t − 1`;
все окна — минуты ≤ b (как `tk671_mkt`). Знак стороны `s` = +1 лонг, −1 шорт. Ряд закрытий — `ffill_close` (до листинга NaN), «минутка с данными» — `o ≠ 0`.
NATR — NATR 60 (период 60, как `sigfeat_lib.natr_series(p=60)`, доля цены, на минуте b): окно 60 назначено критиком TK-697.

  № 3 вынос в обратную сторону (6), окна W ∈ {15, 30, 60}. Лонг: A = минуты [b − W + 1, b], B = [b − 2W + 1, b − W]; lowA / lowB — минимумы low минуток с данными.
      Вынос есть, если lowA < lowB И закрытие минуты b ≥ lowB (возврат за минимум предыдущего окна); у шорта — зеркало (high, возврат ≤ максимума B).
      sw_d{W} = (lowB − lowA) / close[b] / NATR60[b] (0, если выноса нет; NaN — нет данных в A или B, окно не помещается, нет NATR / закрытия);
      sw_a{W} = t − m, где m — последняя минута из A с lowA (давность в минутах, ≥ 1); NaN, если выноса нет или sw_d = NaN.
  № 4 ход до начала подхода (7). Начало подхода a0 = t − approach_dur_min (определение `sigfeat_lib.fresh_prior_features`), e = a0 − 1 (последняя минута до подхода),
      r_H = cf[e] / cf[e − 60·H] − 1, H ∈ {24, 48, 72} ч; нет e − 60·H ≥ 0 или закрытия — NaN.
      pm_p{H} = 100 · s · r_H (% к пробою); pm_n{H} = s · r_H / (NATR60[b] · √1440) (суточные NATR: NATR минутный, масштаб суток — √1440, назначено решением);
      pm_sharp = |r_6ч| / |r_72ч| (6 ч = 360 минут, тот же e; NaN при r_72ч = 0). Знак стороны в резкости не нужен — это доля хода, а не его направление.
  № 6 сила против рынка с 00:00 UTC (1): mk_rel_sd = s · (cf_own[b] / o_own[d] − 1) − s · (cf_btc[b] / o_btc[d] − 1), d — первая минута суток сигнала (с 00:00 UTC) с данными
      (кэш начинается в 00:00 UTC; d ≤ b); нет такой минуты (первая минута суток) — NaN; нет кэша BTC — NaN. Окна 1 / 4 / 24 ч — готовые `mk_rel_s60 / 240 / 1440` (TK-671).
  круглое число (2): R — ближайшее к уровню L число вида m · 10^k, m ∈ {1, 2, 5}; rn_p = 100 · |L − R| / L (%), rn_n = |L − R| / L / NATR60[b]. L = |level_px_e8| / 1e8.
Причинность: читаются только минуты ≤ b = t − 1 (сверка — `tk697_gate.py`: G-brute, G-causal)."""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk668_feat as ft  # noqa: E402
from tk668_feat import njit  # noqa: E402

SW_W = (15, 30, 60)
PM_H = (24, 48, 72)
SHARP_NUM_MIN = 360
DAY_MIN = 1440
NATR_P = 60
SIGN = {"long": 1.0, "short": -1.0}
CACHE_ROOT = "/data/p42-tk621"
BTC = "BTCUSDT"
NAN = float("nan")
ROUND_M = (1.0, 2.0, 5.0)

COLUMNS = ([f"sw_d{w}" for w in SW_W] + [f"sw_a{w}" for w in SW_W] + [f"pm_p{h}" for h in PM_H] + [f"pm_n{h}" for h in PM_H] + ["pm_sharp"]
           + ["mk_rel_sd", "rn_p", "rn_n"])


# ---- ядра ------------------------------------------------------------------------------------------------------------------------------

@njit(cache=True)
def natr_kernel(o, h, l, c, p):
    """NATR p минут как `sigfeat_lib.natr_series` / `data.rs::natr_series`, но доля цены float64 (не e8); NaN — нет значения.
    Истинный диапазон считается по минуткам с данными (o ≠ 0), prevc — закрытие последней минутки с данными; окно [t − p + 1, t],
    значение есть, если минутка t с данными и в окне ≥ p / 2 минуток с данными. Суммы — int64 (точно), деление — float64."""
    n = len(o)
    out = np.full(n, np.nan)
    ptr = np.zeros(n + 1, dtype=np.int64)
    pcnt = np.zeros(n + 1, dtype=np.int64)
    prevc = np.int64(0)
    for t in range(n):
        tr = np.int64(0)
        k = 0
        if o[t] != 0:
            x = h[t] - l[t]
            if prevc != 0:
                x = max(x, abs(h[t] - prevc), abs(l[t] - prevc))
            tr = x
            k = 1
            prevc = c[t]
        ptr[t + 1] = ptr[t] + tr
        pcnt[t + 1] = pcnt[t] + k
    for t in range(p - 1, n):
        if o[t] == 0:
            continue
        k = pcnt[t + 1] - pcnt[t + 1 - p]
        if k * 2 < p:
            continue
        s = ptr[t + 1] - ptr[t + 1 - p]
        out[t] = float(s) / (float(k) * float(c[t]))
    return out


@njit(cache=True)
def sweep_kernel(lw, cfs, nat, b, w, out_d, out_a):
    """Вынос в «длинной раме»: `lw` — low (у шорта −high), +inf на минутках без данных; `cfs` — закрытие в той же раме (у шорта −cf); см. docstring модуля."""
    n = len(lw)
    for i in range(len(b)):
        out_d[i] = np.nan
        out_a[i] = np.nan
        bi = b[i]
        if bi < 0 or bi >= n or bi - 2 * w + 1 < 0:
            continue
        low_a = np.inf
        m_a = -1
        for m in range(bi - w + 1, bi + 1):
            if lw[m] <= low_a:
                low_a = lw[m]
                m_a = m
        low_b = np.inf
        for m in range(bi - 2 * w + 1, bi - w + 1):
            if lw[m] < low_b:
                low_b = lw[m]
        if not (np.isfinite(low_a) and np.isfinite(low_b)):
            continue
        c = cfs[bi]
        nt = nat[bi]
        if not (np.isfinite(c) and c != 0.0 and np.isfinite(nt) and nt > 0.0):
            continue
        if low_a < low_b and c >= low_b:
            out_d[i] = (low_b - low_a) / abs(c) / nt
            out_a[i] = bi + 1 - m_a
        else:
            out_d[i] = 0.0


@njit(cache=True)
def ret_at_kernel(cf, e, h, out):
    """r = cf[e] / cf[e − h] − 1; NaN при e < h, e ≥ N, неопределённом закрытии или cf[e − h] = 0."""
    n = len(cf)
    for i in range(len(e)):
        out[i] = np.nan
        ei = e[i]
        j = ei - h
        if ei < 0 or j < 0 or ei >= n:
            continue
        c1, c0 = cf[ei], cf[j]
        if np.isfinite(c1) and np.isfinite(c0) and c0 != 0.0:
            out[i] = c1 / c0 - 1.0


@njit(cache=True)
def since_open_kernel(o, cf, b, out):
    """cf[b] / o[d] − 1, d — первая минута суток сигнала с данными (d ∈ [d0, b], d0 — 00:00 UTC; кэш начинается в 00:00 UTC); иначе NaN."""
    n = len(cf)
    for i in range(len(b)):
        out[i] = np.nan
        bi = b[i]
        if bi < 0 or bi >= n:
            continue
        d0 = ((bi + 1) // DAY_MIN) * DAY_MIN
        if bi < d0:
            continue
        for d in range(d0, bi + 1):
            if o[d] != 0:
                c1 = cf[bi]
                if np.isfinite(c1):
                    out[i] = c1 / float(o[d]) - 1.0
                break


def round_distance(level):
    """(|L − R|, R) для ближайшего R = m · 10^k, m ∈ {1, 2, 5}, к уровню `level` (> 0); NaN — не определён."""
    L = np.asarray(level, dtype=np.float64)
    ok = np.isfinite(L) & (L > 0)
    Ls = np.where(ok, L, 1.0)
    k0 = np.floor(np.log10(Ls)).astype(np.int64)
    best = np.full(L.shape, np.inf)
    best_r = np.full(L.shape, np.nan)
    for dk in (-1, 0, 1):
        for m in ROUND_M:
            r = m * np.power(10.0, (k0 + dk).astype(np.float64))
            d = np.abs(Ls - r)
            take = d < best
            best = np.where(take, d, best)
            best_r = np.where(take, r, best_r)
    return np.where(ok, best, np.nan), np.where(ok, best_r, np.nan)


def warm_up():
    z = np.zeros(8, dtype=np.int64)
    natr_kernel(z, z, z, z, 4)
    x = np.zeros(8)
    b = np.zeros(1, dtype=np.int64)
    o = np.empty(1)
    sweep_kernel(x, x, x, b, 1, o, o.copy())
    ret_at_kernel(x, b, 1, o)
    since_open_kernel(z, x, b, o)


# ---- блоки -----------------------------------------------------------------------------------------------------------------------------

def frames(a, s):
    """(lw, cfs, cf) монеты в «длинной раме» стороны `s`: lw — low (шорт: −high), +inf без данных; cfs — закрытие в раме; cf — ffill-закрытие."""
    valid = a["o"] != 0
    cf = ft.ffill_close(a["o"], a["c"])
    if s > 0:
        lw = np.where(valid, a["l"].astype(np.float64), np.inf)
        cfs = cf
    else:
        lw = np.where(valid, -a["h"].astype(np.float64), np.inf)
        cfs = -cf
    return lw, cfs, cf


def own_block(a, b, dur, s, sod_btc, level):
    """(len(b), 16) столбцов `COLUMNS` для строк одной монеты по её минуткам `a`: `b` — последние закрытые минуты, `dur` — approach_dur_min, `sod_btc` — ход BTC с 00:00 UTC в этих минутах, `level` — |цена уровня|."""
    n = len(b)
    lw, cfs, cf = frames(a, s)
    nat = natr_kernel(a["o"], a["h"], a["l"], a["c"], NATR_P)
    nb = nat[np.clip(b, 0, len(nat) - 1)]
    nb = np.where((b >= 0) & (b < len(nat)), nb, np.nan)
    out = np.full((n, len(COLUMNS)), np.nan)
    d, ag = np.empty(n), np.empty(n)
    for k, w in enumerate(SW_W):
        sweep_kernel(lw, cfs, nat, b, w, d, ag)
        out[:, k] = d
        out[:, len(SW_W) + k] = ag
    t = b + 1
    with np.errstate(invalid="ignore"):
        a0 = t - dur
        e = np.where(np.isfinite(a0), np.rint(np.where(np.isfinite(a0), a0, 0.0)).astype(np.int64) - 1, -1)
    col = np.empty(n)
    base = 2 * len(SW_W)
    rr = {}
    for k, h in enumerate(PM_H):
        ret_at_kernel(cf, e, 60 * h, col)
        rr[h] = col.copy()
        out[:, base + k] = 100.0 * s * col
        with np.errstate(invalid="ignore", divide="ignore"):
            out[:, base + len(PM_H) + k] = s * col / (nb * math.sqrt(DAY_MIN))
    r6 = np.empty(n)
    ret_at_kernel(cf, e, SHARP_NUM_MIN, r6)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[:, base + 2 * len(PM_H)] = np.where(rr[72] != 0.0, np.abs(r6) / np.abs(rr[72]), np.nan)
    own_sod = np.empty(n)
    since_open_kernel(a["o"], cf, b, own_sod)
    j = base + 2 * len(PM_H) + 1
    out[:, j] = s * own_sod - s * sod_btc
    dist, _ = round_distance(level)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[:, j + 1] = 100.0 * dist / level
        out[:, j + 2] = dist / level / nb
    return out


# ---- сборка ----------------------------------------------------------------------------------------------------------------------------

_CTX = {}


def _coin_task(sym):
    c = _CTX
    idx = c["groups"][sym]
    path = os.path.join(c["cache_dir"], sym + ".b42")
    if not os.path.exists(path):
        return idx, None
    a = ft.load_b42_full(path)
    return idx, own_block(a, c["b"][idx], c["dur"][idx], c["s"], c["sod_btc"][idx], c["level"][idx])


def _run(tasks, fn, procs):
    if procs <= 1 or os.name == "nt" or len(tasks) < 2:
        return [fn(x) for x in tasks]
    with mp.get_context("fork").Pool(min(procs, len(tasks))) as pool:
        return list(pool.imap_unordered(fn, tasks, chunksize=1))


def sod_btc_for(cache_dir, b):
    """Ход BTC с 00:00 UTC на минутах `b`; нет кэша BTC — NaN."""
    path = os.path.join(cache_dir, BTC + ".b42")
    out = np.full(len(b), np.nan)
    if not os.path.exists(path):
        return out, False
    a = ft.load_b42_full(path)
    since_open_kernel(a["o"], ft.ffill_close(a["o"], a["c"]), b, out)
    return out, True


def compute(rows, win, side, procs=3, cache_dir=None, info=None):
    """Столбцы `m697` на строках одного окна и одной стороны. `rows` — row_id, sym, sig_ms, level_px_e8, approach_dur_min (+ abs_ms, win, side — проверяются, если есть)."""
    import pandas as pd
    t_start = time.time()
    if side not in SIGN:
        raise ValueError("side %r" % side)
    for col in ("win", "side"):
        if col in rows.columns and not (rows[col] == (win if col == "win" else side)).all():
            raise ValueError("строки не одного (окна, стороны): %s" % col)
    if rows["row_id"].duplicated().any():
        raise ValueError("row_id не уникален")
    if "abs_ms" in rows.columns:
        import tk604_stats as st
        st.check_abs_ms(rows["abs_ms"].to_numpy(np.int64), "строка TK-671")
    cache_dir = cache_dir or os.path.join(CACHE_ROOT, win, "cache")
    n = len(rows)
    s = SIGN[side]
    b = rows["sig_ms"].to_numpy(np.int64) // ft.MIN_MS - 1
    dur = rows["approach_dur_min"].to_numpy(np.float64)
    level = np.abs(rows["level_px_e8"].to_numpy(np.int64)).astype(np.float64) / 1e8
    syms, sym_code = np.unique(rows["sym"].to_numpy(dtype=object).astype(str), return_inverse=True)
    stat = dict(win=win, side=side, rows=int(n), coins=int(len(syms)), missing_ref=[], missing_cache=[])
    sod_btc, have_btc = sod_btc_for(cache_dir, b)
    if not have_btc:
        stat["missing_ref"].append(BTC)
        sys.stderr.write("tk697_cols: нет кэша %s окна %s — mk_rel_sd = NaN\n" % (BTC, win))
    warm_up()
    order = np.argsort(sym_code, kind="stable")
    cuts = np.flatnonzero(np.diff(sym_code[order])) + 1
    groups = {syms[sym_code[g[0]]]: g for g in np.split(order, cuts) if len(g)}
    _CTX.clear()
    _CTX.update(cache_dir=cache_dir, groups=groups, b=b, dur=dur, s=s, sod_btc=sod_btc, level=level)
    out = np.full((n, len(COLUMNS)), np.nan)
    for idx, blk in _run(sorted(groups, key=lambda x: -len(groups[x])), _coin_task, procs):
        if blk is not None:
            out[idx] = blk
    _CTX.clear()
    stat["missing_cache"] = sorted(sym for sym in groups if not os.path.exists(os.path.join(cache_dir, sym + ".b42")))
    df = pd.DataFrame(out, index=pd.Index(rows["row_id"].to_numpy(np.int64), name="row_id"), columns=list(COLUMNS))
    stat["seconds"] = round(time.time() - t_start, 3)
    stat["ms_per_row"] = round(1000.0 * stat["seconds"] / max(n, 1), 4)
    stat["non_nan_share"] = {c: round(float(np.isfinite(df[c]).mean()), 6) if n else 0.0 for c in COLUMNS}
    if info is not None:
        info.update(stat)
    return df


def attach_dur(rows, data_path, win):
    """Присоединить `approach_dur_min` из таблицы TK-671 (ключ окно + row_id) с проверкой sym / sig_ms."""
    import pandas as pd
    d = pd.read_parquet(data_path, columns=["win", "row_id", "sym", "meta_sig_ms", "approach_dur_min"])
    d = d[d["win"] == win].set_index("row_id")
    j = d.reindex(rows["row_id"].to_numpy())
    if j["approach_dur_min"].isna().all() and len(rows):
        raise ValueError("таблица TK-671: нет строк окна %s" % win)
    if not (j["sym"].to_numpy(dtype=object) == rows["sym"].to_numpy(dtype=object)).all() or not (
            j["meta_sig_ms"].to_numpy() == rows["sig_ms"].to_numpy()).all():
        raise ValueError("таблица TK-671 и rows.parquet: sym / sig_ms не совпали по row_id")
    rows = rows.copy()
    rows["approach_dur_min"] = j["approach_dur_min"].to_numpy()
    return rows


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--data", required=True, help="таблица TK-671 <side>.parquet (approach_dur_min)")
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--cache-dir")
    a = ap.parse_args()
    rows = attach_dur(pd.read_parquet(a.rows), a.data, a.win)
    info = {}
    df = compute(rows, a.win, a.side, a.procs, cache_dir=a.cache_dir, info=info)
    df.to_parquet(a.out)
    with open(a.out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: info[k] for k in ("rows", "seconds", "ms_per_row")}), flush=True)


if __name__ == "__main__":
    main()
