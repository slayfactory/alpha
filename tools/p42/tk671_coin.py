#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), семейство TK-636 (монета) на всех строках `rows.parquet`: те же определения, что `tk636_feat` (`row_values`, `corr_features`, `cluster_labels`, `Events`), но без цикла по строкам Python:
матрицы доходностей окна грузятся один раз, строки идут группами (монета, месяц) — соседи кластера и их средний ряд считаются на группу, а не на строку; окна корреляций — ядра numba с той же парной суммой, что у numpy.

  compute(rows, win, side, procs=3, cache_dir=None, ev_dir=None, labels_dir=None, contracts_path=None, coins=None, labels=None, info=None) -> DataFrame (индекс row_id, 30 столбцов float64, NaN = не определено, строки не выбрасываются)
  python3 tk671_coin.py --rows <rows.parquet> --win wa --side long --out <out.parquet> --procs 3 [--labels-dir <каталог кэша меток кластеров>]

Столбцы (имена как у `tk636_feat.OUT_COLS`): cls (CLS), turn24h (TOV), natr_bp (VOL), age_d (AGE), clu_id, clu_n, peer5, peer15, peer60, peerall15, cb_/ce_/bt_/cc_ × 15m, 1h, 4h, 24h, 7d (COUP, 20 штук).
turn24h = Σ qv за 1 440 минут до `t` / 1e5 (как `sigfeat_lib.context_features`; в TK-636 столбец брался из таблицы TK-634), natr_bp = natr_e8 / 1e4 — оба пересчитаны из строки и минуток, не из чужой таблицы; в f12 есть одноимённые столбцы.
События для PEER — пересечения TK-625 базы (`ev_dir/<окно>.csv`, `cross_ms` мс от T0 окна, сторона строки): определены для любой строки (не зависят от набора строк), поэтому определение оригинала сохранено.
Кластеры месяца — по 14 суткам до 1-го числа (`REFRESH_WINDOW`), кэш меток — `labels_dir/clusters-<мм>.npz` (иначе считаются из минуток окна обновления). Сутки ≥ 14.11 не читаются: `check_abs_ms` на строках."""
from __future__ import annotations

import argparse
import datetime
import json
import math
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tk700_win import win_choices  # noqa: E402

import tk636_feat as f6  # noqa: E402
import tk668_feat as ft  # noqa: E402
from tk668_feat import njit  # noqa: E402
from tk671_t668 import _pw  # noqa: E402  (парная сумма numpy; тот же ядро, что в семействе TK-668)

COLS = tuple(f6.OUT_COLS)
CACHE_ROOT = "/data/p42-tk621"
EV_DIR = f6.EV_DIR["h775"]
SYMS_FILE = "/data/p42-tk621/universe/symbols-all.txt"
LABELS_DIR = "/data/p42-tk671/fam/coin/clusters"
MIN_MS = f6.MIN_MS
NW = len(f6.WIN_ORDER)
W_N = np.array([f6.WIN_SPEC[w][1] for w in f6.WIN_ORDER], dtype=np.int64)          # число баров окна
W_MIN = np.array([f6.WIN_SPEC[w][2] for w in f6.WIN_ORDER], dtype=np.int64)        # минимум общих пар
W_R1 = np.array([f6.WIN_SPEC[w][0] == "r1" for w in f6.WIN_ORDER])                  # окно по 1-мин доходностям (иначе 5-мин блоки)
PEER_MIN = np.array([w for _, w in f6.PEER_W], dtype=np.int64)
SIDE_NAME = {"long": "long", "short": "short"}
NAN = float("nan")


# ---- ядра ------------------------------------------------------------------------------------------------------------------------------------

@njit(cache=True)
def _wstat(x, y, a, last, min_n, bx, by, dv):
    """`tk636_feat.wstat` по барам [a, last] (a = max(last − n + 1, 0) уже посчитан, last ≥ a и last < len(x) проверены снаружи): (корреляция, бета x к y) по общим конечным парам; scratch — bx, by, dv."""
    k = 0
    for j in range(a, last + 1):
        u, v = x[j], y[j]
        if np.isfinite(u) and np.isfinite(v):
            bx[k] = u
            by[k] = v
            k += 1
    if k < min_n:
        return np.nan, np.nan
    mx = _pw(bx, 0, k) / k
    my = _pw(by, 0, k) / k
    for j in range(k):
        bx[j] = bx[j] - mx
        by[j] = by[j] - my
    for j in range(k):
        dv[j] = bx[j] * bx[j]
    sxx = _pw(dv, 0, k)
    for j in range(k):
        dv[j] = by[j] * by[j]
    syy = _pw(dv, 0, k)
    for j in range(k):
        dv[j] = bx[j] * by[j]
    sxy = _pw(dv, 0, k)
    corr = sxy / math.sqrt(sxx * syy) if (sxx > 0 and syy > 0) else np.nan
    beta = sxy / syy if syy > 0 else np.nan
    return corr, beta


@njit(cache=True)
def _mate_mean_at(R, mates, j):
    """Среднее доходностей соседей на баре `j`: бар определён при ≥ 2 соседях с конечной доходностью (как `tk636_feat.mate_mean`; суммы — по порядку соседей)."""
    s = 0.0
    cnt = 0
    for q in range(len(mates)):
        v = R[mates[q], j]
        if np.isfinite(v):
            s += v
            cnt += 1
    if cnt >= 2:
        return s / cnt
    return np.nan


@njit(cache=True)
def _coup_group(R1, R5, ci, bi, ei, skip_b, skip_e, mates, t, w_n, w_min, w_r1, out):
    """Компоненты cb / ce / bt / cc во всех окнах для строк одной монеты (`ci`) и месяца (`mates` — соседи кластера, ≥ 2 для cc): out[i, 4·w + {0: cb, 1: ce, 2: bt, 3: cc}]; `bi`, `ei` < 0 — нет ряда."""
    n1, n5 = R1.shape[1], R5.shape[1]
    mm5 = np.full(n5, np.nan)
    have_mates = len(mates) >= 2
    if have_mates:
        for j in range(n5):
            mm5[j] = _mate_mean_at(R5, mates, j)
    mm1 = np.full(n1, np.nan)
    bx = np.empty(2016)
    by = np.empty(2016)
    dv = np.empty(2016)
    for i in range(len(t)):
        for q in range(NW):
            out[i, 4 * q] = np.nan
            out[i, 4 * q + 1] = np.nan
            out[i, 4 * q + 2] = np.nan
            out[i, 4 * q + 3] = np.nan
        for q in range(NW):
            n, mn = w_n[q], w_min[q]
            if w_r1[q]:
                last = t[i] - 1
                ncol = n1
            else:
                last = t[i] // 5 - 1
                ncol = n5
            a = max(last - n + 1, 0)
            if last < a or last >= ncol:
                continue
            if w_r1[q]:
                if bi >= 0 and not skip_b:
                    c, b = _wstat(R1[ci], R1[bi], a, last, mn, bx, by, dv)
                    out[i, 4 * q] = c
                    out[i, 4 * q + 2] = b
                if ei >= 0 and not skip_e:
                    c, b = _wstat(R1[ci], R1[ei], a, last, mn, bx, by, dv)
                    out[i, 4 * q + 1] = c
                if have_mates:
                    for j in range(a, last + 1):
                        mm1[j] = _mate_mean_at(R1, mates, j)
                    c, b = _wstat(R1[ci], mm1, a, last, mn, bx, by, dv)
                    out[i, 4 * q + 3] = c
            else:
                if bi >= 0 and not skip_b:
                    c, b = _wstat(R5[ci], R5[bi], a, last, mn, bx, by, dv)
                    out[i, 4 * q] = c
                    out[i, 4 * q + 2] = b
                if ei >= 0 and not skip_e:
                    c, b = _wstat(R5[ci], R5[ei], a, last, mn, bx, by, dv)
                    out[i, 4 * q + 1] = c
                if have_mates:
                    c, b = _wstat(R5[ci], mm5, a, last, mn, bx, by, dv)
                    out[i, 4 * q + 3] = c


@njit(cache=True)
def _lower(arr, lo, hi, x):
    """Первый индекс в arr[lo:hi] со значением ≥ x (как `searchsorted(..., 'left')`), hi — если такого нет."""
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


@njit(cache=True)
def _peer_group(mates, t_ms, ev_off, ev_t, w_min, out):
    """peer5 / peer15 / peer60: число различных соседей по кластеру (`mates` — индексы монет), у которых есть событие в [t − W, t)."""
    for i in range(len(t_ms)):
        for k in range(len(w_min)):
            lo = t_ms[i] - w_min[k] * 60000
            hi = t_ms[i]
            cnt = 0
            for q in range(len(mates)):
                m = mates[q]
                a0, a1 = ev_off[m], ev_off[m + 1]
                p = _lower(ev_t, a0, a1, lo)
                if p < a1 and ev_t[p] < hi:
                    cnt += 1
            out[i, k] = cnt


@njit(cache=True)
def _peerall(t_ms, own, all_t, all_id, n_ids, w_min, out):
    """peerall15: число различных монет (кроме собственной, `own` < 0 — монеты нет среди событий) с событием в [t − W, t) по всей вселенной."""
    stamp = np.zeros(n_ids, dtype=np.int64)
    for i in range(len(t_ms)):
        i0 = _lower(all_t, 0, len(all_t), t_ms[i] - w_min * 60000)
        i1 = _lower(all_t, 0, len(all_t), t_ms[i])
        cnt = 0
        for q in range(i0, i1):
            sid = all_id[q]
            if sid != own[i] and stamp[sid] != i + 1:
                stamp[sid] = i + 1
                cnt += 1
        out[i] = cnt


# ---- данные окна -----------------------------------------------------------------------------------------------------------------------------

def universe_coins(path=SYMS_FILE):
    """Вселенная h775 в том же порядке, что `tk625_run.Uni('h775').coins()` (порядок задаёт нумерацию кластеров)."""
    return sorted(x for x in open(path, encoding="utf-8").read().replace("\n", ",").split(",") if x)


class World:
    """Матрицы доходностей окна по монетам вселенной (как `tk636_feat.Win`): R1 — 1-мин, R5 — 5-мин блоки; `turn24h` — по строкам, посчитан при загрузке."""

    def __init__(self, coins, R1, R5, have):
        self.coins, self.R1, self.R5, self.have = list(coins), R1, R5, have
        self.ix = {s: i for i, s in enumerate(self.coins)}


def load_world(cache_dir, coins, procs=3, rows_by_coin=None, turn_t=None, with_r1=True):
    """Грузит кэши монет (потоки), строит R1 / R5 и по ходу считает turn24h строк монеты: rows_by_coin = {монета: индексы строк}, turn_t — минуты строк; возвращает (World, turn24h по строкам или None)."""
    path = lambda s: os.path.join(cache_dir, s + ".b42")  # noqa: E731
    have = np.array([os.path.exists(path(s)) for s in coins])
    if not have.any():
        raise RuntimeError("в %s нет ни одного кэша" % cache_dir)
    first = ft.load_b42_full(path(coins[int(np.argmax(have))]))
    n = len(first["o"])
    R1 = np.full((len(coins), n), np.nan) if with_r1 else None
    R5 = np.full((len(coins), n // 5), np.nan)
    turn = np.full(len(turn_t), np.nan) if turn_t is not None else None

    def one(i):
        a = ft.load_b42_full(path(coins[i]))
        o, c = a["o"], a["c"]
        if with_r1:
            R1[i] = f6.ret1(o, c)
        R5[i] = f6.ret5(o, c)
        if turn is not None and rows_by_coin and coins[i] in rows_by_coin:
            idx = rows_by_coin[coins[i]]
            turn[idx] = turn24h_rows(a["qv"], turn_t[idx])

    todo = [i for i in range(len(coins)) if have[i]]
    if procs > 1:
        with ThreadPoolExecutor(procs) as ex:
            list(ex.map(one, todo))
    else:
        for i in todo:
            one(i)
    return World(coins, R1, R5, have), turn


def turn24h_rows(qv, t):
    """Σ qv[t − 1440 : t] / 1e5 (целые суммы точно); t < 1440 — NaN; срез за пределом ряда обрезается, как у `ndarray`."""
    cs = np.concatenate([[0], np.cumsum(qv, dtype=np.int64)])
    n = len(qv)
    t = np.asarray(t, dtype=np.int64)
    s = cs[np.minimum(t, n)] - cs[np.clip(t - 1440, 0, n)]
    return np.where(t >= 1440, s.astype(np.float64) / 1e5, np.nan)


def month_of_vec(abs_ms):
    d = np.asarray(abs_ms, dtype=np.int64).astype("datetime64[ms]")
    yr = d.astype("datetime64[Y]").astype(np.int64) + 1970
    mo = d.astype("datetime64[M]").astype(np.int64) % 12 + 1
    return np.where(yr == 2026, mo, 0)


def month_labels(months, coins, cache_root=CACHE_ROOT, labels_dir=None, procs=3):
    """{месяц: метки кластеров по `coins`}: из `labels_dir/clusters-<мм>.npz` (если монеты те же), иначе — из 5-мин блоков окна обновления (`REFRESH_WINDOW`) через `tk636_feat.cluster_labels`; посчитанные пишутся в `labels_dir`."""
    import tk604_stats as st
    out, todo = {}, {}
    for mon in sorted(set(int(m) for m in months if int(m) in f6.REFRESH_WINDOW)):
        p = os.path.join(labels_dir, "clusters-%02d.npz" % mon) if labels_dir else None
        if p and os.path.exists(p):
            with np.load(p, allow_pickle=False) as z:
                if list(z["coins"]) == list(coins):
                    out[mon] = z["lab"]
                    continue
        todo.setdefault(f6.REFRESH_WINDOW[mon], []).append(mon)
    wins = {w.name: w for w in st.TRADE_WINDOWS}
    for wn, mons in todo.items():
        w = wins[wn]
        wo, _ = load_world(os.path.join(cache_root, wn, "cache"), coins, procs=procs, with_r1=False)
        for mon in mons:
            a, b = f6.refresh_blocks(w.t0d, datetime.date(2026, mon, 1))
            if a < 0 or b > wo.R5.shape[1]:
                raise RuntimeError("месяц %d: 14 суток до обновления вне кэша окна (блоки %d…%d из %d)" % (mon, a, b, wo.R5.shape[1]))
            out[mon] = f6.cluster_labels(wo.R5, wo.ix.get(f6.REFS[0]), wo.ix.get(f6.REFS[1]), a, b)
            if labels_dir:
                os.makedirs(labels_dir, exist_ok=True)
                tmp = os.path.join(labels_dir, "clusters-%02d.tmp%d.npz" % (mon, os.getpid()))
                np.savez(tmp, lab=out[mon], coins=np.array(coins))
                os.replace(tmp, os.path.join(labels_dir, "clusters-%02d.npz" % mon))
        del wo
    return out


class EventIndex:
    """События TK-625 стороны окна для ядер: по монетам вселенной (`off`, `t`) и общая лента по времени (`all_t`, `all_id`; id — индекс монеты вселенной или большие номера чужих монет)."""

    def __init__(self, coins, ev_syms, ev_cross):
        ix = {s: i for i, s in enumerate(coins)}
        ev_syms = np.asarray(ev_syms, dtype=object)
        cross = np.asarray(ev_cross, dtype=np.int64)
        ids = np.empty(len(cross), dtype=np.int64)
        extra = {}
        for k, s in enumerate(ev_syms):
            i = ix.get(s)
            if i is None:
                i = len(coins) + extra.setdefault(s, len(extra))
            ids[k] = i
        self.n_ids = len(coins) + len(extra)
        self.ix, self.extra = ix, extra
        o = np.argsort(cross, kind="stable")
        self.all_t, self.all_id = cross[o], ids[o]
        # по монетам: отсортированные времена, склеенные в один массив
        k = np.lexsort((cross, ids))
        ids_s, t_s = ids[k], cross[k]
        keep = ids_s < len(coins)
        ids_s, t_s = ids_s[keep], t_s[keep]
        self.ev_t = t_s
        self.off = np.zeros(len(coins) + 1, dtype=np.int64)
        np.add.at(self.off, ids_s + 1, 1)
        np.cumsum(self.off, out=self.off)

    def own_id(self, syms):
        """Номер собственной монеты строки в ленте событий (−1 — монеты нет ни во вселенной, ни среди событий)."""
        nco = len(self.ix)
        return np.array([self.ix[s] if s in self.ix else (nco + self.extra[s] if s in self.extra else -1) for s in syms], dtype=np.int64)


def load_events(win, side, ev_dir=None):
    """(монеты, cross_ms) событий стороны окна с записанным пересечением — как `tk636_feat.load_events_df`."""
    if ev_dir:
        import pandas as pd
        d = pd.read_csv(os.path.join(ev_dir, win + ".csv"), usecols=["side", "sym", "cross_ms"])
        d = d[(d["side"] == f6.SIDE_NAME["L" if side == "long" else "S"]) & d["cross_ms"].notna()]
    else:
        d = f6.load_events_df("h775", win, "L" if side == "long" else "S")
    return d["sym"].to_numpy(dtype=object), d["cross_ms"].to_numpy(dtype=np.float64).astype(np.int64)


# ---- сборка -----------------------------------------------------------------------------------------------------------------------------------

_CTX = {}


def _group_task(g):
    c = _CTX
    ci, mon, idx = g
    w, ev = c["world"], c["events"]
    lab = c["labels"].get(mon)
    mates = np.zeros(0, dtype=np.int64)
    if ci >= 0 and lab is not None and lab[ci] >= 0:
        members = np.flatnonzero(lab == lab[ci])
        mates = members[members != ci].astype(np.int64)
    out = np.empty((len(idx), 4 * NW))
    peers = np.full((len(idx), len(PEER_MIN)), np.nan)
    if ci >= 0:
        sym = w.coins[ci]
        bi, ei = w.ix.get(f6.REFS[0], -1), w.ix.get(f6.REFS[1], -1)
        _coup_group(w.R1, w.R5, ci, bi, ei, sym == f6.REFS[0], sym == f6.REFS[1], mates, c["t"][idx], W_N, W_MIN, W_R1, out)
        if len(mates):
            _peer_group(mates, c["sig_ms"][idx], ev.off, ev.ev_t, PEER_MIN, peers)
    else:
        out[:] = np.nan
    return idx, out, peers, len(mates)


def _run(tasks, fn, procs):
    if procs <= 1 or os.name == "nt" or len(tasks) < 2:
        return [fn(x) for x in tasks]
    with mp.get_context("fork").Pool(min(procs, len(tasks))) as pool:
        return list(pool.imap_unordered(fn, tasks, chunksize=8))


def contract_columns(rows_sym, abs_ms, contracts):
    """cls и age_d по строкам: как `row_values` (`tradfi` монеты, возраст листинга в сутках на дату сигнала UTC, < 0 → 0; нет контракта / даты — NaN)."""
    n = len(rows_sym)
    cls, age = np.full(n, np.nan), np.full(n, np.nan)
    days = np.asarray(abs_ms, dtype=np.int64) // 86_400_000
    epoch = datetime.date(1970, 1, 1)
    for sym in np.unique(rows_sym):
        ct = contracts.get(sym)
        if not ct:
            continue
        m = rows_sym == sym
        cls[m] = float(ct["tradfi"])
        if ct["onboard_date"]:
            age[m] = np.maximum(days[m] - (datetime.date.fromisoformat(ct["onboard_date"]) - epoch).days, 0).astype(np.float64)
    return cls, age


def compute(rows, win, side, procs=3, cache_dir=None, ev_dir=None, labels_dir=None, contracts_path=None, coins=None, labels=None, info=None, world=None, turn=None, cache_root=None, events=None):
    """Признаки монеты на строках одного окна и стороны. `rows` — row_id, sym, sig_ms, natr_e8 (+ abs_ms, win, side — проверяются, если есть). `labels` — {месяц: метки} (иначе `month_labels` из кэшей `cache_root/<окно обновления>/cache`); `world` / `turn` — готовые матрицы окна, `events` — (монеты, cross_ms) вместо файла (для ворот и тестов)."""
    import pandas as pd
    t_start = time.time()
    if side not in SIDE_NAME:
        raise ValueError("side %r" % side)
    for col in ("win", "side"):
        if col in rows.columns and not (rows[col] == (win if col == "win" else side)).all():
            raise ValueError("строки не одного (окна, стороны): %s" % col)
    if rows["row_id"].duplicated().any():
        raise ValueError("row_id не уникален")
    abs_ms = rows["abs_ms"].to_numpy(np.int64) if "abs_ms" in rows.columns else None
    if abs_ms is None:
        import tk604_stats as st
        abs_ms = {w.name: w for w in st.TRADE_WINDOWS}[win].t0_ms + rows["sig_ms"].to_numpy(np.int64)
    import tk604_stats as st
    st.check_abs_ms(abs_ms, "строка TK-671")
    cache_root = cache_root or CACHE_ROOT
    cache_dir = cache_dir or os.path.join(cache_root, win, "cache")
    coins = list(coins) if coins is not None else universe_coins()
    n = len(rows)
    sym = rows["sym"].to_numpy(dtype=object).astype(str)
    sig_ms = rows["sig_ms"].to_numpy(np.int64)
    t = sig_ms // MIN_MS
    natr_e8 = rows["natr_e8"].to_numpy(np.int64)
    rows_by_coin = {s: np.flatnonzero(sym == s) for s in np.unique(sym)}
    stat = dict(win=win, side=side, rows=int(n), coins=int(len(rows_by_coin)))
    if world is None:
        world, turn = load_world(cache_dir, coins, procs=procs, rows_by_coin=rows_by_coin, turn_t=t)
    stat["load_seconds"] = round(time.time() - t_start, 3)
    mon = month_of_vec(abs_ms)
    if labels is None:
        labels = month_labels(np.unique(mon), coins, cache_root=cache_root, labels_dir=labels_dir, procs=procs)
    stat["label_months"] = sorted(int(m) for m in labels)
    ev_syms, ev_cross = events if events is not None else load_events(win, side, ev_dir)
    ev = EventIndex(coins, ev_syms, ev_cross)
    contracts = f6.read_contracts(contracts_path) if contracts_path else f6.read_contracts()
    cls, age = contract_columns(sym, abs_ms, contracts)
    # группы (монета, месяц)
    uq, inv = np.unique(sym, return_inverse=True)
    ci = np.array([world.ix[s] if (s in world.ix and world.have[world.ix[s]]) else -1 for s in uq], dtype=np.int64)[inv] if n else np.zeros(0, dtype=np.int64)
    order = np.lexsort((mon, ci))
    key = np.stack([ci[order], mon[order]], axis=1)
    cuts = np.flatnonzero(np.any(key[1:] != key[:-1], axis=1)) + 1 if n else np.zeros(0, dtype=np.int64)
    groups = [(int(ci[g[0]]), int(mon[g[0]]), g) for g in np.split(order, cuts) if len(g)]
    # прогрев ядер до fork (компиляция numba — один раз)
    z1, z5 = np.zeros((1, 1)), np.zeros((1, 1))
    _coup_group(z1, z5, 0, -1, -1, False, False, np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), W_N, W_MIN, W_R1, np.empty((0, 4 * NW)))
    _peer_group(np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), np.zeros(2, dtype=np.int64), np.zeros(1, dtype=np.int64), PEER_MIN, np.empty((0, 3)))
    _CTX.clear()
    _CTX.update(world=world, events=ev, labels=labels, t=t, sig_ms=sig_ms)
    coup, peers = np.full((n, 4 * NW), np.nan), np.full((n, len(PEER_MIN)), np.nan)
    clu_id, clu_n = np.full(n, np.nan), np.full(n, np.nan)
    for idx, o, p, n_mates in _run(groups, _group_task, procs):
        coup[idx], peers[idx] = o, p
    for ci_, mon_, idx in groups:
        lab = labels.get(mon_)
        if ci_ >= 0 and lab is not None and lab[ci_] >= 0:
            clu_id[idx] = float(lab[ci_])
            clu_n[idx] = float((lab == lab[ci_]).sum())
    pall = np.empty(n)
    _peerall(sig_ms, ev.own_id(sym), ev.all_t, ev.all_id, ev.n_ids, f6.PEERALL_W, pall)
    cols = {"cls": cls, "turn24h": turn if turn is not None else np.full(n, np.nan), "natr_bp": natr_e8 / 1e4, "age_d": age, "clu_id": clu_id, "clu_n": clu_n,
            "peer5": peers[:, 0], "peer15": peers[:, 1], "peer60": peers[:, 2], "peerall15": pall}
    for q, w in enumerate(f6.WIN_ORDER):
        for k, comp in enumerate(f6.COMP):
            cols["%s_%s" % (comp, w)] = coup[:, 4 * q + k]
    df = pd.DataFrame({c: cols[c] for c in COLS}, index=pd.Index(rows["row_id"].to_numpy(np.int64), name="row_id"))
    stat["seconds"] = round(time.time() - t_start, 3)
    stat["ms_per_row"] = round(1000.0 * stat["seconds"] / max(n, 1), 4)
    stat["missing_cache_coins"] = sorted(s for s in rows_by_coin if not (s in world.ix and world.have[world.ix[s]]))
    stat["non_nan_share"] = {c: round(float(np.isfinite(df[c]).mean()), 6) if n else 0.0 for c in COLS}
    if info is not None:
        info.update(stat)
    return df


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=win_choices())
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--labels-dir", default=LABELS_DIR)
    ap.add_argument("--cache-dir")
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows)
    info = {}
    df = compute(rows, a.win, a.side, a.procs, cache_dir=a.cache_dir, labels_dir=a.labels_dir, info=info)
    df.to_parquet(a.out)
    with open(a.out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: info[k] for k in ("rows", "seconds", "ms_per_row", "load_seconds")}), flush=True)


if __name__ == "__main__":
    main()
