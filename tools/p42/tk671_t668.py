#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), семейство TK-668 на всех строках `rows.parquet`: признаки `tk668_feat` (place_features / btc_shock / room_to_next) без цикла по строкам Python.

  compute(rows, win, side, procs=3, cache_dir=None, levels_dir=None, level_paths=None, info=None) -> DataFrame (индекс row_id, 14 столбцов float64, NaN = не определено, строки не выбрасываются)
  python3 tk671_t668.py --rows <rows.parquet> --win wa --side long --out <out.parquet> --procs 3 [--levels-dir <каталог дампов окна: <источник>/<side>-levels.csv>]

Столбцы: R, X24, X7, XP, XV, bz1, bz5, bz15 (признаки TK-668) + r1, r5, r15, sg1, sg5, sg15 (ход BTC за h мин и σ_h — слагаемые bz, тоже свойства строки на сигнале; без знака стороны).
Не берутся: `own` (контроль «уровень найден живым с ценой версии = p»: не свойство строки, счётчик пишется в info.json), `pnext` (абсолютная цена следующего уровня в e8: масштаб монеты, R её уже содержит в NATR),
`level`, `px_e8` (идентификатор и цена строки), `natr` (= natr_e8 строки). Ни один из столбцов не смотрит на исход сделки.
R считается по списку уровней ИСТОЧНИКА строки (`src` → `<levels_dir>/<источник>/<side>-levels.csv`, дамп движка `P42_LEVELS=1` того же прогона, что и `sigsx`; номер `level` строки — индекс в этом списке).
Нет файла уровней источника — R = NaN у его строк (другие столбцы считаются всегда). R = +∞ — следующего живого уровня нет (как в TK-668; ассемблеру решать, чем заменить).
Числа: те же функции и порядок действий, что у `tk668_feat` (суммы — парная схема numpy), сверка с оригиналом — `tk671_t668_gate.py`."""
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
from tk700_win import win_choices  # noqa: E402

import tk668_feat as ft  # noqa: E402
from tk668_feat import njit  # noqa: E402

FEATURES = ("R", "X24", "X7", "XP", "XV", "bz1", "bz5", "bz15", "r1", "r5", "r15", "sg1", "sg5", "sg15")
SIGN = {"long": 1.0, "short": -1.0}
CACHE_ROOT = "/data/p42-tk621"
DAY_MIN, WEEK_MIN, MIN_VALID_DAY, MIN_VALID_SIG = ft.DAY_MIN, ft.WEEK_MIN, ft.MIN_VALID_DAY, ft.MIN_VALID_SIG
NAN = float("nan")


# ---- суммы как у numpy ----------------------------------------------------------------------------------------------------------------

@njit(cache=True)
def _pw_leaf(a, lo, n):
    """Парная сумма блока до 128 элементов (8 накопителей) — лист схемы numpy."""
    if n < 8:
        res = 0.0
        for i in range(n):
            res += a[lo + i]
        return res
    r0, r1, r2, r3 = a[lo], a[lo + 1], a[lo + 2], a[lo + 3]
    r4, r5, r6, r7 = a[lo + 4], a[lo + 5], a[lo + 6], a[lo + 7]
    i = 8
    lim = n - (n % 8)
    while i < lim:
        r0 += a[lo + i]
        r1 += a[lo + i + 1]
        r2 += a[lo + i + 2]
        r3 += a[lo + i + 3]
        r4 += a[lo + i + 4]
        r5 += a[lo + i + 5]
        r6 += a[lo + i + 6]
        r7 += a[lo + i + 7]
        i += 8
    res = ((r0 + r1) + (r2 + r3)) + ((r4 + r5) + (r6 + r7))
    while i < n:
        res += a[lo + i]
        i += 1
    return res


@njit(cache=True)
def _pw(a, lo, n):
    """Парная сумма `a[lo:lo+n]` — тот же порядок сложения, что у `ndarray.sum()` для непрерывного float64 (листы по ≤ 128, деление пополам с кратностью 8). Без рекурсии:
    numba с `cache=True` роняет процесс (segfault) при загрузке рекурсивной функции из кэша, поэтому обход дерева — на явном стеке."""
    if n <= 128:
        return _pw_leaf(a, lo, n)
    s_lo = np.empty(64, dtype=np.int64)
    s_n = np.empty(64, dtype=np.int64)
    s_st = np.zeros(64, dtype=np.int64)
    s_val = np.zeros(64)
    s_lo[0], s_n[0] = lo, n
    sp = 1
    ret = 0.0
    while sp > 0:
        top = sp - 1
        cl, cn = s_lo[top], s_n[top]
        if cn <= 128:
            ret = _pw_leaf(a, cl, cn)
            sp -= 1
            continue
        n2 = cn // 2
        n2 -= n2 % 8
        if s_st[top] == 0:
            s_st[top] = 1
            s_lo[sp], s_n[sp], s_st[sp] = cl, n2, 0
            sp += 1
        elif s_st[top] == 1:
            s_val[top] = ret
            s_st[top] = 2
            s_lo[sp], s_n[sp], s_st[sp] = cl + n2, cn - n2, 0
            sp += 1
        else:
            ret = s_val[top] + ret
            sp -= 1
    return ret


# ---- минутки монеты: X24, X7, XP, XV ------------------------------------------------------------------------------------------------------

@njit(cache=True)
def _place_kernel(cf, hh, ll, nv, P1, P2, n, t, s, natr, out):
    """`tk668_feat.place_features` по строкам монеты: t — минуты сигналов, s — знак стороны, natr — доля; out[:, 0..3] = X24, X7, XP, XV."""
    for i in range(len(t)):
        ti, si, na = t[i], s[i], natr[i]
        for k in range(4):
            out[i, k] = np.nan
        a, b = ti - DAY_MIN, ti - 1
        if a - 1 < 0 or b >= n or not (na > 0.0):
            continue
        if nv[b + 1] - nv[a] < MIN_VALID_DAY:
            continue
        c = cf[b]
        c24 = cf[a - 1]
        cfin = np.isfinite(c)
        if cfin and np.isfinite(c24) and c24 != 0.0:
            out[i, 0] = si * (c / c24 - 1.0)
        if ti - 1 - WEEK_MIN >= 0:
            c7 = cf[ti - 1 - WEEK_MIN]
            if cfin and np.isfinite(c7) and c7 != 0.0:
                out[i, 1] = si * (c / c7 - 1.0)
        hi = hh[a]
        lo = ll[a]
        for j in range(a + 1, b + 1):
            if hh[j] > hi:
                hi = hh[j]
            if ll[j] < lo:
                lo = ll[j]
        if cfin and hi > lo:
            if si > 0:
                out[i, 2] = (c - lo) / (hi - lo)
            else:
                out[i, 2] = (hi - c) / (hi - lo)
        s1 = P1[b + 1] - P1[a]
        s2 = P2[b + 1] - P2[a]
        if cfin and s1 > 0 and s2 > 0:
            out[i, 3] = si * (c / (s1 / s2) - 1.0) / na


def place_columns(m, t, s, natr):
    """(n, 4) = X24, X7, XP, XV по строкам монеты с минутками `m` (`tk668_feat.Mins`); t, s, natr — массивы строк."""
    out = np.empty((len(t), 4))
    _place_kernel(m.cf, m.hh, m.ll, m.nv, m.P1, m.P2, m.n, t, s, natr, out)
    return out


# ---- шок BTC ------------------------------------------------------------------------------------------------------------------------------

@njit(cache=True)
def _btc_kernel(cf, r, tu, h, out_r, out_s):
    """`tk668_feat.btc_shock` по минутам `tu` (уникальные t): out_r = r_h, out_s = σ_h = std(ddof 1)·√h; нет данных — NaN."""
    n = len(cf)
    buf = np.empty(DAY_MIN + 1)
    dev = np.empty(DAY_MIN + 1)
    sq = math.sqrt(h)
    for i in range(len(tu)):
        out_r[i] = np.nan
        out_s[i] = np.nan
        t = tu[i]
        a, b = t - 1, t - 1 - h
        lo = b - DAY_MIN
        if lo < 0 or a >= n:
            continue
        k = 0
        for j in range(lo, b + 1):
            v = r[j]
            if np.isfinite(v):
                buf[k] = v
                k += 1
        if k < MIN_VALID_SIG:
            continue
        if not (np.isfinite(cf[a]) and np.isfinite(cf[b]) and cf[b] != 0.0):
            continue
        mean = _pw(buf, 0, k) / k
        for j in range(k):
            x = buf[j] - mean
            dev[j] = x * x
        out_s[i] = math.sqrt(_pw(dev, 0, k) / (k - 1)) * sq
        out_r[i] = cf[a] / cf[b] - 1.0


def btc_columns(cf, r1, t, s):
    """{'r1','r5','r15','sg1','sg5','sg15','bz1','bz5','bz15'} по строкам (t — минуты сигналов, s — знак стороны)."""
    tu, inv = np.unique(t, return_inverse=True)
    out = {}
    for h in ft.HORIZONS:
        rr, sg = np.empty(len(tu)), np.empty(len(tu))
        _btc_kernel(cf, r1, tu, h, rr, sg)
        rh, sgh = rr[inv], sg[inv]
        ok = np.isfinite(rh) & np.isfinite(sgh) & (sgh > 0)
        bz = np.full(len(t), np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            bz[ok] = s[ok] * rh[ok] / sgh[ok]
        out["r%d" % h], out["sg%d" % h], out["bz%d" % h] = rh, sgh, bz
    return out


# ---- уровни: R ------------------------------------------------------------------------------------------------------------------------------

@njit(cache=True)
def _px_idx(off, segm, j, t):
    """Индекс последней версии цены уровня `j` с минутой ≤ t (как `searchsorted(segm, t, 'right') − 1`), нет такой — −1."""
    k = -1
    for q in range(off[j], off[j + 1]):
        if segm[q] <= t:
            k = q
        else:
            break
    return k


@njit(cache=True)
def _room_kernel(t, p, lev, sign, natr, b, e, off, segm, segp, R, own):
    """`tk668_feat.room_to_next` по строкам монеты: уровни — массивы рождений `b`, концов `e`, версий цены (`off` — границы по уровням, `segm` — минуты, `segp` — |цена|)."""
    nl = len(b)
    for i in range(len(t)):
        ti, pi, li, sg, na = t[i], p[i], lev[i], sign[i], natr[i]
        R[i] = np.nan
        own[i] = False
        if li < 0 or li >= nl or not (na > 0.0):
            continue
        if b[li] <= ti and ti < e[li]:
            k = _px_idx(off, segm, li, ti)
            own[i] = k >= 0 and segp[k] == pi
        have = False
        best = 0
        for j in range(nl):
            if j == li or not (b[j] <= ti and ti < e[j]):
                continue
            k = _px_idx(off, segm, j, ti)
            if k < 0:
                continue
            px = segp[k]
            if sg > 0:
                if px > pi and (not have or px < best):
                    have, best = True, px
            elif px < pi and (not have or px > best):
                have, best = True, px
        if have:
            R[i] = abs(best - pi) / (pi * na)
        else:
            R[i] = np.inf


def flatten_levels(sl):
    """`tk668_feat.SymLevels` → (b, e, off, segm, segp) для ядра."""
    n = len(sl.born)
    off = np.zeros(n + 1, dtype=np.int64)
    if n:
        np.cumsum(np.fromiter((len(x) for x in sl.segm), dtype=np.int64, count=n), out=off[1:])
        return sl.b, sl.e, off, np.concatenate(sl.segm), np.concatenate(sl.segp)
    z = np.zeros(0, dtype=np.int64)
    return z, z, off, z, z


def room_rows(sl, t, p, lev, s, natr):
    """(R, own) для строк одной монеты по её уровням `sl` (`SymLevels`)."""
    R, own = np.empty(len(t)), np.zeros(len(t), dtype=np.bool_)
    b, e, off, segm, segp = flatten_levels(sl)
    _room_kernel(t, p, lev, s, natr, b, e, off, segm, segp, R, own)
    return R, own


def garbage_levels(sl, t, rng, n_junk=0):
    """G-causal для R: копия `SymLevels`, где всё со штампом позже минуты `t` (рождение, конец, минуты версий цены) заменено случайным того же вида; `n_junk` — добавить столько выдуманных уровней, рождённых после t."""
    g = ft.SymLevels()
    for i in range(len(sl.born)):
        b, e = int(sl.b[i]), int(sl.e[i])
        segs = list(zip(sl.segm[i].tolist(), sl.segp[i].tolist()))
        if b > t:
            b = int(rng.integers(t + 1, t + 5000))
            e = b + int(rng.integers(1, 3000))
            p0 = segs[0][1] if segs else 1000
            segs = [(b, int(max(1, p0 * rng.uniform(0.5, 1.5))))]
        else:
            if e > t:
                e = int(rng.integers(t + 1, t + 6000)) if rng.random() < 0.7 else ft.U32_MAX
            hi = max([p for _, p in segs] or [1000])
            segs = [(m, p) if m <= t else (int(rng.integers(t + 1, t + 4000)), int(max(1, hi * rng.uniform(0.5, 1.5)))) for m, p in segs]
            segs.sort()
        g.add(b, e, segs)
    for _ in range(n_junk):
        b = int(rng.integers(t + 1, t + 5000))
        g.add(b, b + int(rng.integers(1, 3000)), [(b, int(rng.integers(1, 10 ** 9)))])
    g.freeze()
    return g


# ---- сборка -----------------------------------------------------------------------------------------------------------------------------------

_CTX = {}


def _coin_task(sym):
    c = _CTX
    idx = c["groups"][sym]
    path = os.path.join(c["cache_dir"], sym + ".b42")
    if not os.path.exists(path):
        return idx, None
    m = ft.Mins(ft.load_b42_full(path))
    return idx, place_columns(m, c["t"][idx], c["s"][idx], c["natr"][idx])


def _lvl_task(si):
    """Строки источника `si`: R и «свой уровень найден» по дампу уровней источника; ключ строки в дампе — (монета, номер уровня)."""
    c = _CTX
    idx = c["src_idx"][si]
    lv = ft.parse_levels(c["level_paths"][si])
    R, own = np.full(len(idx), np.nan), np.zeros(len(idx), dtype=bool)
    stat = dict(rows=int(len(idx)), no_coin=0, level_oob=0)
    code = c["sym_code"][idx]
    order = np.argsort(code, kind="stable")
    cuts = np.flatnonzero(np.diff(code[order])) + 1
    for part in np.split(order, cuts):
        if not len(part):
            continue
        g = idx[part]
        sl = lv.get(c["syms"][c["sym_code"][g[0]]])
        if sl is None:
            stat["no_coin"] += len(g)
            continue
        stat["level_oob"] += int((c["lev"][g] >= len(sl.born)).sum())
        R[part], own[part] = room_rows(sl, c["t"][g], c["p"][g], c["lev"][g], c["s"][g], c["natr"][g])
    return idx, R, own, stat


def _run(tasks, fn, procs):
    if procs <= 1 or os.name == "nt" or len(tasks) < 2:
        return [fn(x) for x in tasks]
    with mp.get_context("fork").Pool(min(procs, len(tasks))) as pool:
        return list(pool.imap_unordered(fn, tasks, chunksize=1))


def compute(rows, win, side, procs=3, cache_dir=None, levels_dir=None, level_paths=None, info=None):
    """Признаки TK-668 на строках одного окна и одной стороны. `rows` — столбцы row_id, src, sym, level, sig_ms, level_px_e8, natr_e8 (+ abs_ms, win, side — проверяются, если есть).
    `cache_dir` — каталог `.b42` окна (по умолчанию /data/p42-tk621/<win>/cache); `levels_dir` — каталог дампов окна (`<источник>/<side>-levels.csv`); `level_paths` — {номер источника: путь} (перекрывает `levels_dir`)."""
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
    sig_ms = rows["sig_ms"].to_numpy(np.int64)
    t = sig_ms // ft.MIN_MS
    natr = rows["natr_e8"].to_numpy(np.int64) / 1e8
    s = np.full(n, SIGN[side])
    syms, sym_code = np.unique(rows["sym"].to_numpy(dtype=object).astype(str), return_inverse=True)
    out = {c: np.full(n, np.nan) for c in FEATURES}
    stat = dict(win=win, side=side, rows=int(n), coins=int(len(syms)), missing_cache=[], levels={})
    # BTC: один раз на окно
    btc_path = os.path.join(cache_dir, "BTCUSDT.b42")
    a = ft.load_b42_full(btc_path)
    cf = ft.ffill_close(a["o"], a["c"])
    r1 = ft.minute_returns(cf)
    for k, v in btc_columns(cf, r1, t, s).items():
        out[k] = v
    # прогрев ядер до fork (компиляция numba — один раз)
    _place_kernel(cf[:1], cf[:1], cf[:1], np.zeros(2, dtype=np.int64), np.zeros(2), np.zeros(2), 1, t[:0], s[:0], natr[:0], np.empty((0, 4)))
    order = np.argsort(sym_code, kind="stable")
    cuts = np.flatnonzero(np.diff(sym_code[order])) + 1
    groups = {syms[sym_code[g[0]]]: g for g in np.split(order, cuts) if len(g)}
    _CTX.clear()
    _CTX.update(cache_dir=cache_dir, groups=groups, t=t, s=s, natr=natr, syms=syms, sym_code=sym_code,
                p=np.abs(rows["level_px_e8"].to_numpy(np.int64)), lev=rows["level"].to_numpy(np.int64))
    for idx, o4 in _run(sorted(groups), _coin_task, procs):
        if o4 is None:
            continue
        for k, name in enumerate(("X24", "X7", "XP", "XV")):
            out[name][idx] = o4[:, k]
    stat["missing_cache"] = sorted(sym for sym in groups if not os.path.exists(os.path.join(cache_dir, sym + ".b42")))
    # R: по источникам
    src = rows["src"].to_numpy(np.int64)
    paths = dict(level_paths or {})
    names = _source_names()
    if levels_dir:
        for si in np.unique(src):
            f = os.path.join(levels_dir, names[int(si)], side + "-levels.csv")
            if int(si) not in paths and os.path.exists(f):
                paths[int(si)] = f
    src_idx = {int(si): np.flatnonzero(src == si) for si in np.unique(src)}
    todo = sorted((si for si in src_idx if si in paths), key=lambda si: -len(src_idx[si]))
    _CTX.update(src_idx=src_idx, level_paths=paths)
    for idx, R, own, ls in _run(todo, _lvl_task, procs):
        out["R"][idx] = R
        si = int(src[idx[0]])
        ls["own"] = int(own.sum())
        stat["levels"][names[si] if si < len(names) else str(si)] = ls
    for si in src_idx:
        if si not in paths:
            stat["levels"][names[si] if si < len(names) else str(si)] = dict(rows=int(len(src_idx[si])), no_levels_file=True)
    df = pd.DataFrame(out, index=pd.Index(rows["row_id"].to_numpy(np.int64), name="row_id"))[list(FEATURES)]
    stat["seconds"] = round(time.time() - t_start, 3)
    stat["ms_per_row"] = round(1000.0 * stat["seconds"] / max(n, 1), 4)
    stat["non_nan_share"] = {c: round(float(np.isfinite(df[c]).mean()), 6) if n else 0.0 for c in FEATURES}
    stat["R_inf_share"] = round(float(np.isinf(df["R"]).mean()), 6) if n else 0.0
    if info is not None:
        info.update(stat)
    return df


def _source_names():
    try:
        import tk671_lvl
        return list(tk671_lvl.SOURCES)
    except Exception:                                  # noqa: BLE001 — нет pyarrow и т. п.
        return ["p30n1", "p30n2", "p30n3", "p60n1", "p60n2", "p60n3", "p240n1", "p240n2", "p240n3", "lxD", "lxW", "lxP"]


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=win_choices())
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--levels-dir")
    ap.add_argument("--cache-dir")
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows)
    info = {}
    df = compute(rows, a.win, a.side, a.procs, cache_dir=a.cache_dir, levels_dir=a.levels_dir, info=info)
    df.to_parquet(a.out)
    with open(a.out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: info[k] for k in ("rows", "seconds", "ms_per_row")}), flush=True)


if __name__ == "__main__":
    main()
