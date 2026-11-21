#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), семейство mkt: фон рынка на сигнале — ход BTC / ETH / своей монеты, относительный ход, волатильность и связь с BTC по 5-минутным доходностям. Только минутки `.b42` (читаются `tk668_feat`).

  compute(rows, win, side, procs=3, cache_dir=None, info=None) -> DataFrame (индекс row_id, 27 столбцов float64, NaN = не определено, строки не выбрасываются)
  python3 tk671_mkt.py --rows <rows.parquet> --win wa --side long --out <out.parquet> --procs 3 [--cache-dir <каталог .b42 окна>]

Минута, как в `tk668_feat`: `t = sig_ms // 60000`, последняя ЗАКРЫТАЯ минута `b = t − 1`; ряд закрытий — `ffill_close` (до первой минутки с данными — NaN). Знак стороны `s` = +1 лонг, −1 шорт; h ∈ {1, 5, 15, 60, 240, 1440} минут.
  mk_btc_s{h}, mk_eth_s{h}, mk_own_s{h} = s · r_h монеты BTCUSDT / ETHUSDT / своей, `r_h = cf[b] / cf[b − h] − 1`; NaN, если b − h < 0, b ≥ N (кэш кончился), любое из двух закрытий не определено или cf[b − h] = 0 (18 столбцов);
  mk_rel_s{h} = mk_own_s{h} − mk_btc_s{h}, h ∈ {60, 240, 1440} (3);
  mk_rv5_btc_{60,1440}, mk_rv5_own_{60,1440} = станд. откл. (ddof 1) 5-минутных доходностей закрытий `q[m] = cf[m] / cf[m − 5] − 1` на сетке m = b, b − 5, b − 10, … за последние 60 / 1 440 минут (12 / 288 доходностей), без знака (4);
  mk_corr5_{240,1440} = корреляция Пирсона 5-минутных доходностей своей монеты и BTC на той же сетке за последние 240 / 1 440 минут (48 / 288 пар) (2).
Решения по неоднозначному (замечены при реализации, не заданы брифом): доходность q определена, если оба закрытия конечны и cf[m − 5] ≠ 0; в окне считаются только определённые q (у rv — одна серия, у corr — пары, где определены обе);
нужно ≥ 10 определённых (MIN_VALID); меньше — NaN (частичное окно у только что вышедшей монеты допускается при ≥ 10). corr: NaN, если у любой из двух серий в паре все значения равны (нулевая дисперсия) — проверка точным равенством; результат зажат в [−1, 1].
Нет кэша BTC / ETH окна — их столбцы (и всё, что от них зависит: mk_rel, mk_corr5) = NaN, имя пишется в info["missing_ref"] и в stderr; нет кэша своей монеты — NaN в её столбцах (info["missing_cache"]).
Причинность: читаются закрытия минут ≤ b = t − 1 (окна уходят только назад); сверка — `tk671_gate_mkt.py` (G-brute: независимый построчный счёт; G-causal: мусор в минутках ≥ t).
Скорость: ядра numba по монете (двухпроходные суммы по окну, без цикла по строкам Python); BTC-окна считаются один раз на минуту сигнала."""
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

HORIZONS = (1, 5, 15, 60, 240, 1440)
REL_H = (60, 240, 1440)
RV_W = (60, 1440)
CORR_W = (240, 1440)
STEP = 5                                              # шаг сетки 5-минутных доходностей, минут
MIN_VALID = 10                                        # минимум определённых доходностей (пар) в окне
SIGN = {"long": 1.0, "short": -1.0}
CACHE_ROOT = "/data/p42-tk621"
BTC, ETH = "BTCUSDT", "ETHUSDT"
NAN = float("nan")

COLUMNS = ([f"mk_{ref}_s{h}" for ref in ("btc", "eth", "own") for h in HORIZONS] + [f"mk_rel_s{h}" for h in REL_H]
           + [f"mk_rv5_{ref}_{w}" for ref in ("btc", "own") for w in RV_W] + [f"mk_corr5_{w}" for w in CORR_W])
NH, NR, NC = len(HORIZONS), len(RV_W), len(CORR_W)
# блоки промежуточных значений (без знака стороны): ref — [r_btc (6) | r_eth (6) | rv_btc (2)], own — [r_own (6) | rv_own (2) | corr (2)]
REF_W, OWN_W = 2 * NH + NR, NH + NR + NC


# ---- минутки ---------------------------------------------------------------------------------------------------------------------------

def step_returns(cf, step=STEP):
    """`q[m] = cf[m] / cf[m − step] − 1` (float64): NaN при m < step, если любое закрытие не определено или cf[m − step] = 0."""
    q = np.full(len(cf), np.nan)
    if len(cf) > step:
        with np.errstate(invalid="ignore", divide="ignore"):
            q[step:] = cf[step:] / cf[:-step] - 1.0
        q[np.isinf(q)] = np.nan
    return q


# ---- ядра ------------------------------------------------------------------------------------------------------------------------------

@njit(cache=True)
def _ret_kernel(cf, b, h, out):
    """r_h = cf[b] / cf[b − h] − 1 по минутам `b` (последняя закрытая минута); нет данных — NaN."""
    n = len(cf)
    for i in range(len(b)):
        out[i] = np.nan
        bi = b[i]
        j = bi - h
        if j < 0 or bi >= n:
            continue
        c1, c0 = cf[bi], cf[j]
        if np.isfinite(c1) and np.isfinite(c0) and c0 != 0.0:
            out[i] = c1 / c0 - 1.0


@njit(cache=True)
def _rv_kernel(q, b, w, out):
    """Станд. откл. (ddof 1) определённых q[b − 5k], k = 0 … w/5 − 1; меньше MIN_VALID определённых или b ≥ N — NaN. Двухпроходная сумма (среднее, потом сумма квадратов отклонений)."""
    n = len(q)
    kn = w // STEP
    for i in range(len(b)):
        out[i] = np.nan
        bi = b[i]
        if bi < 0 or bi >= n:
            continue
        cnt = 0
        sm = 0.0
        for k in range(kn):
            m = bi - STEP * k
            if m < 0:
                break
            v = q[m]
            if np.isfinite(v):
                cnt += 1
                sm += v
        if cnt < MIN_VALID:
            continue
        mean = sm / cnt
        ss = 0.0
        for k in range(kn):
            m = bi - STEP * k
            if m < 0:
                break
            v = q[m]
            if np.isfinite(v):
                d = v - mean
                ss += d * d
        out[i] = math.sqrt(ss / (cnt - 1))


@njit(cache=True)
def _corr_kernel(qo, qb, b, w, out):
    """Корреляция Пирсона q своей монеты и BTC по парам, где определены обе, на сетке b − 5k, k = 0 … w/5 − 1; < MIN_VALID пар, нулевая дисперсия (все значения одной из серий равны) или b ≥ N — NaN."""
    no, nb = len(qo), len(qb)
    kn = w // STEP
    for i in range(len(b)):
        out[i] = np.nan
        bi = b[i]
        if bi < 0 or bi >= no or bi >= nb:
            continue
        cnt = 0
        sx = 0.0
        sy = 0.0
        x0 = 0.0
        y0 = 0.0
        xc = True
        yc = True
        for k in range(kn):
            m = bi - STEP * k
            if m < 0:
                break
            x, y = qo[m], qb[m]
            if np.isfinite(x) and np.isfinite(y):
                if cnt == 0:
                    x0, y0 = x, y
                else:
                    if x != x0:
                        xc = False
                    if y != y0:
                        yc = False
                cnt += 1
                sx += x
                sy += y
        if cnt < MIN_VALID or xc or yc:
            continue
        mx, my = sx / cnt, sy / cnt
        sxx = 0.0
        syy = 0.0
        sxy = 0.0
        for k in range(kn):
            m = bi - STEP * k
            if m < 0:
                break
            x, y = qo[m], qb[m]
            if np.isfinite(x) and np.isfinite(y):
                dx, dy = x - mx, y - my
                sxx += dx * dx
                syy += dy * dy
                sxy += dx * dy
        den = math.sqrt(sxx) * math.sqrt(syy)
        if den > 0.0:
            c = sxy / den
            out[i] = min(1.0, max(-1.0, c))


def warm_up():
    """Компиляция ядер до fork (numba: один раз, а не в каждом процессе)."""
    b = np.zeros(1, dtype=np.int64)
    x = np.zeros(8)
    o = np.empty(1)
    _ret_kernel(x, b, 1, o)
    _rv_kernel(x, b, 60, o)
    _corr_kernel(x, x, b, 240, o)


# ---- блоки ------------------------------------------------------------------------------------------------------------------------------

def returns_block(cf, b):
    """(len(b), 6): r_h для h из HORIZONS; `cf is None` (нет кэша) — NaN."""
    out = np.full((len(b), NH), np.nan)
    if cf is None:
        return out
    col = np.empty(len(b))
    for k, h in enumerate(HORIZONS):
        _ret_kernel(cf, b, h, col)
        out[:, k] = col
    return out


def rv_block(q, b):
    """(len(b), 2): rv5 за 60 / 1 440 минут; `q is None` — NaN. Минуты считаются один раз на уникальное `b` (у строк одной минуты сигнала значения общие)."""
    out = np.full((len(b), NR), np.nan)
    if q is None or not len(b):
        return out
    bu, inv = np.unique(b, return_inverse=True)
    col = np.empty(len(bu))
    for k, w in enumerate(RV_W):
        _rv_kernel(q, bu, w, col)
        out[:, k] = col[inv]
    return out


def ref_block(cf_btc, q_btc, cf_eth, b):
    """(len(b), 14): r_btc (6), r_eth (6), rv5_btc (2) — значения без знака стороны; одинаковы у всех строк минуты `b`."""
    return np.hstack([returns_block(cf_btc, b), returns_block(cf_eth, b), rv_block(q_btc, b)])


def own_block(cf, q, q_btc, b):
    """(len(b), 10): r_own (6), rv5_own (2), corr5 (2) для монеты с закрытиями `cf` / 5-минутными доходностями `q`; `q_btc is None` — corr = NaN; `cf is None` — всё NaN."""
    if cf is None:
        return np.full((len(b), OWN_W), np.nan)
    corr = np.full((len(b), NC), np.nan)
    if q_btc is not None:
        col = np.empty(len(b))
        for k, w in enumerate(CORR_W):
            _corr_kernel(q, q_btc, b, w, col)
            corr[:, k] = col
    return np.hstack([returns_block(cf, b), rv_block(q, b), corr])


def assemble(ref, own, s):
    """(n, 27) в порядке COLUMNS: знак стороны `s` — только у mk_*_s*; mk_rel = mk_own − mk_btc (NaN, если любое из двух NaN)."""
    out = np.empty((len(s), len(COLUMNS)))
    sg = s[:, None]
    out[:, 0:NH] = sg * ref[:, 0:NH]
    out[:, NH:2 * NH] = sg * ref[:, NH:2 * NH]
    out[:, 2 * NH:3 * NH] = sg * own[:, 0:NH]
    j = [HORIZONS.index(h) for h in REL_H]
    out[:, 3 * NH:3 * NH + len(REL_H)] = out[:, 2 * NH:3 * NH][:, j] - out[:, 0:NH][:, j]
    o = 3 * NH + len(REL_H)
    out[:, o:o + NR] = ref[:, 2 * NH:2 * NH + NR]
    out[:, o + NR:o + 2 * NR] = own[:, NH:NH + NR]
    out[:, o + 2 * NR:o + 2 * NR + NC] = own[:, NH + NR:NH + NR + NC]
    return out


def columns_for(cf_own, cf_btc, cf_eth, b, s):
    """Все 27 значений для строк одной монеты (`b` — последние закрытые минуты, `s` — знак стороны) по рядам закрытий; None вместо ряда — нет кэша. Один путь с `compute` — на нём G-causal."""
    b = np.asarray(b, dtype=np.int64)
    s = np.asarray(s, dtype=np.float64)
    q_btc = None if cf_btc is None else step_returns(cf_btc)
    q_own = None if cf_own is None else step_returns(cf_own)
    return assemble(ref_block(cf_btc, q_btc, cf_eth, b), own_block(cf_own, q_own, q_btc, b), s)


# ---- сборка -----------------------------------------------------------------------------------------------------------------------------

_CTX = {}


def _coin_task(sym):
    c = _CTX
    idx = c["groups"][sym]
    path = os.path.join(c["cache_dir"], sym + ".b42")
    if not os.path.exists(path):
        return idx, None
    a = ft.load_b42_full(path)
    cf = ft.ffill_close(a["o"], a["c"])
    return idx, own_block(cf, step_returns(cf), c["q_btc"], c["b"][idx])


def _run(tasks, fn, procs):
    if procs <= 1 or os.name == "nt" or len(tasks) < 2:
        return [fn(x) for x in tasks]
    with mp.get_context("fork").Pool(min(procs, len(tasks))) as pool:
        return list(pool.imap_unordered(fn, tasks, chunksize=1))


def _load_ref(cache_dir, sym):
    path = os.path.join(cache_dir, sym + ".b42")
    if not os.path.exists(path):
        return None, None
    a = ft.load_b42_full(path)
    cf = ft.ffill_close(a["o"], a["c"])
    return cf, step_returns(cf)


def compute(rows, win, side, procs=3, cache_dir=None, info=None):
    """Столбцы mkt на строках одного окна и одной стороны. `rows` — столбцы row_id, sym, sig_ms (+ abs_ms, win, side — проверяются, если есть); `cache_dir` — каталог `.b42` окна (по умолчанию /data/p42-tk621/<win>/cache)."""
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
    t = rows["sig_ms"].to_numpy(np.int64) // ft.MIN_MS
    b = t - 1
    s = np.full(n, SIGN[side])
    syms, sym_code = np.unique(rows["sym"].to_numpy(dtype=object).astype(str), return_inverse=True)
    stat = dict(win=win, side=side, rows=int(n), coins=int(len(syms)), missing_ref=[], missing_cache=[])
    cf_btc, q_btc = _load_ref(cache_dir, BTC)
    cf_eth, _ = _load_ref(cache_dir, ETH)
    for sym, cf in ((BTC, cf_btc), (ETH, cf_eth)):
        if cf is None:
            stat["missing_ref"].append(sym)
            sys.stderr.write("tk671_mkt: нет кэша %s окна %s — его столбцы (и mk_rel / mk_corr5 при BTC) = NaN\n" % (sym, win))
    ref = ref_block(cf_btc, q_btc, cf_eth, b)
    own = np.full((n, OWN_W), np.nan)
    warm_up()
    order = np.argsort(sym_code, kind="stable")
    cuts = np.flatnonzero(np.diff(sym_code[order])) + 1
    groups = {syms[sym_code[g[0]]]: g for g in np.split(order, cuts) if len(g)}
    _CTX.clear()
    _CTX.update(cache_dir=cache_dir, groups=groups, b=b, q_btc=q_btc)
    for idx, blk in _run(sorted(groups, key=lambda x: -len(groups[x])), _coin_task, procs):
        if blk is not None:
            own[idx] = blk
    _CTX.clear()
    stat["missing_cache"] = sorted(sym for sym in groups if not os.path.exists(os.path.join(cache_dir, sym + ".b42")))
    df = pd.DataFrame(assemble(ref, own, s), index=pd.Index(rows["row_id"].to_numpy(np.int64), name="row_id"), columns=list(COLUMNS))
    stat["seconds"] = round(time.time() - t_start, 3)
    stat["ms_per_row"] = round(1000.0 * stat["seconds"] / max(n, 1), 4)
    stat["non_nan_share"] = {c: round(float(np.isfinite(df[c]).mean()), 6) if n else 0.0 for c in COLUMNS}
    if info is not None:
        info.update(stat)
    return df


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--cache-dir")
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows)
    info = {}
    df = compute(rows, a.win, a.side, a.procs, cache_dir=a.cache_dir, info=info)
    df.to_parquet(a.out)
    with open(a.out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: info[k] for k in ("rows", "seconds", "ms_per_row")}), flush=True)


if __name__ == "__main__":
    main()
