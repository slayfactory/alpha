#!/usr/bin/env python3
"""П-42 §13.76 (TK-685): чистые функции разбора сделок по группам исхода — группы, путь по ленте, метрики набора, AUC всех признаков сразу,
шумы (перестановка меток N1, циклический сдвиг по времени N2), семейный порог, дерево глубины ≤ 3 на numpy, присоединение признаков по ключу.
Без чтения файлов и без движка; протокол — `docs/findings/p42-trade-groups-2026-11-22/prereg-13.76.md`."""
from __future__ import annotations

import math

import numpy as np

NOTIONAL = 500.0
MARGIN = 250.0
MIN_MS = 60_000
CHECK_MIN = (1, 2, 3, 5, 10, 15, 20, 30, 45, 60)
LOSS, ZERO, PROFIT = 0, 1, 2
GROUP_NAMES = ("убыток", "около нуля", "прибыль")
HALF_SPLIT_DAY = 68          # половина 1 = сутки 0–67 (01.07–06.09), половина 2 = 68–135 (07.09–13.11)
SQRT365 = math.sqrt(365.0)


# ───────────────────────────── столбцы, исключённые до счёта (§13.76 п. 5, окно > 60 мин) ─────────────────────────────

DROP_PAGE = tuple("emp2h emp6h zv2h zv6h cons120 turn24h c5_btc_7d c5_btc_24h c15_btc_7d c15_btc_24h c1_btc_6h c5_eth_7d c5_eth_24h c15_eth_7d c15_eth_24h c1_eth_6h "
                  "lturn24 vpin oi_c4 oi_c24 loi_turn fund_chg24 n24 n360 n720 vpin6h kyle240 amih240 cb_4h ce_4h bt_4h cc_4h cb_24h ce_24h bt_24h cc_24h ok05_d14 "
                  "xok10_d7 xok10_d14 xok10_d30 bk20 bk40 bk60".split())
DROP_OFF = tuple("n1440 v360 v720 v1440 m360 m720 m1440 X24 X7 XP XV cb_7d ce_7d bt_7d cc_7d ok05_d7 ok05_d30 ok20_d7 ok20_d14 ok20_d30 xok05_d7 xok05_d14 xok05_d30 "
                 "xok20_d7 xok20_d14 xok20_d30 bk_n bk_k mk_btc_s240 mk_btc_s1440 mk_eth_s240 mk_eth_s1440 mk_own_s240 mk_own_s1440 mk_rel_s240 mk_rel_s1440 "
                 "mk_rv5_btc_1440 mk_rv5_own_1440 mk_corr5_240 mk_corr5_1440".split())
DROP_ALL = frozenset(DROP_PAGE + DROP_OFF)
SERVICE_PREFIX = ("meta_", "y_")
SERVICE_NAMES = ("win", "row_id", "sym")


def feature_columns(names):
    """305 столбцов матрицы без служебных → минус DROP_ALL (222), порядок матрицы."""
    return [n for n in names if n not in SERVICE_NAMES and not n.startswith(SERVICE_PREFIX) and n not in DROP_ALL]


# ───────────────────────────── группы и половины ─────────────────────────────

def groups(net, fees):
    """§13.76 п. 2 (правка критика): по net = gross − fees, полоса вокруг нуля симметрична: убыток net < −fees; около нуля |net| ≤ fees; прибыль net > fees."""
    n, f = np.asarray(net, float), np.asarray(fees, float)
    return np.where(n < -f, LOSS, np.where(n > f, PROFIT, ZERO)).astype(np.int8)


def groups_gross(gross, fees):
    """Прежнее правило на gross (только описание размеров групп): убыток gross < −fees; около нуля |gross| ≤ fees; прибыль gross > fees."""
    g, f = np.asarray(gross, float), np.asarray(fees, float)
    return np.where(g < -f, LOSS, np.where(g > f, PROFIT, ZERO)).astype(np.int8)


def halves(day_in):
    """1 — сутки входа 0–67, 2 — 68–135."""
    return np.where(np.asarray(day_in) < HALF_SPLIT_DAY, 1, 2).astype(np.int8)


# ───────────────────────────── путь сделки по ленте ─────────────────────────────

def path_one(t, p, t_in, t_out, p0, sign, fees, notional=NOTIONAL):
    """Путь позиции `notional` $ от входа до выхода по тикам (t мс по возрастанию, p e8) в [t_in, t_out]; вход — точка (t_in, p0), ход 0.
    sign +1 лонг / −1 шорт. Возвращает словарь: mfe, mae ($), t_mfe, t_mae (мин), green, green_first, exit_move, giveback, giveback_share, hold_min,
    mae_to_exit_min, mfe_k / mae_k (бегущие на CHECK_MIN, мин). Пустая лента → ticks = 0 и NaN."""
    t = np.asarray(t, np.int64)
    p = np.asarray(p, np.int64)
    out = {"ticks": int(len(t))}
    nan = float("nan")
    hold = (t_out - t_in) / MIN_MS
    out["hold_min"] = hold
    if len(t) == 0:
        for k in ("mfe", "mae", "t_mfe", "t_mae", "exit_move", "giveback", "giveback_share", "mae_to_exit_min"):
            out[k] = nan
        out["green"] = out["green_first"] = False
        out["mfe_k"] = [nan] * len(CHECK_MIN)
        out["mae_k"] = [nan] * len(CHECK_MIN)
        return out
    tt = np.concatenate([[t_in], t])
    ff = np.concatenate([[0.0], sign * (p - p0) / float(p0) * notional])
    cmax = np.maximum.accumulate(ff)
    cmin = np.maximum.accumulate(-ff)
    i_mfe, i_mae = int(np.argmax(ff)), int(np.argmax(-ff))
    mfe, mae = float(ff[i_mfe]), float(-ff[i_mae])
    out.update(mfe=mfe, mae=mae, t_mfe=(tt[i_mfe] - t_in) / MIN_MS, t_mae=(tt[i_mae] - t_in) / MIN_MS)
    g1 = np.nonzero(ff >= fees)[0]
    r1 = np.nonzero(-ff >= fees)[0]
    green = bool(len(g1))
    out["green"] = green
    out["green_first"] = bool(green and (not len(r1) or g1[0] < r1[0]))
    em = float(ff[-1])
    out["exit_move"] = em
    out["giveback"] = mfe - em
    out["giveback_share"] = (mfe - em) / mfe if green and mfe > 0 else nan
    out["mae_to_exit_min"] = (t_out - tt[i_mae]) / MIN_MS
    idx = np.searchsorted(tt, t_in + np.asarray(CHECK_MIN, np.int64) * MIN_MS, side="right") - 1
    out["mfe_k"] = [float(cmax[j]) for j in idx]
    out["mae_k"] = [float(cmin[j]) for j in idx]
    return out


def entry_in_range(t, p, t_in, p0, win_ms=1000):
    """G-tape-entry: цена входа движка лежит в [min, max] цен ленты за ±win_ms от входа (лента — тики, отсортированы)."""
    t = np.asarray(t, np.int64)
    i0, i1 = np.searchsorted(t, t_in - win_ms, "left"), np.searchsorted(t, t_in + win_ms, "right")
    if i1 <= i0:
        return False
    seg = np.asarray(p)[i0:i1]
    return bool(seg.min() <= p0 <= seg.max())


# ───────────────────────────── метрики набора ─────────────────────────────

def uw(x):
    """Просадка и перехай по шагам (сутки): макс. просадка, самый длинный / средний зазор между новыми максимумами, хвост — как `tk663_shape.uw`."""
    x = np.asarray(x, float)
    n = len(x)
    cum = np.cumsum(x)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    prev = np.concatenate([[0.0], peak[:-1]])
    new = cum > prev + 1e-9
    hi = np.nonzero(new)[0]
    pts = np.concatenate([[-1], hi]).astype(int)
    gaps = np.diff(pts)
    tail = n - 1 - int(pts[-1])
    lens = [int(g) for g in gaps] + [tail]
    return {"maxdd": float((peak - cum).max()) if n else 0.0, "gap_longest": max(lens), "gap_mean": float(gaps.mean()) if len(gaps) else float("nan"),
            "tail": tail, "highs": int(len(hi))}


def set_metrics(day_out, pnl, nd=136):
    """n, Σ $, $ на сделку, Шарп (годовой по дневному P&L по дню выхода, все nd суток), просадка $ и % маржи, перехай, R:R = средний выигрыш ÷ |средний проигрыш|."""
    pnl = np.asarray(pnl, float)
    d = np.clip(np.asarray(day_out, np.int64), 0, nd - 1)
    daily = np.bincount(d, weights=pnl, minlength=nd)[:nd]
    sd = float(np.std(daily, ddof=1)) if nd > 1 else 0.0
    w, l = pnl[pnl > 0], pnl[pnl < 0]
    u = uw(daily)
    return {"n": int(len(pnl)), "sum": float(pnl.sum()), "mean": float(pnl.mean()) if len(pnl) else float("nan"),
            "sharpe": float(daily.mean() / sd * SQRT365) if sd > 0 else 0.0, "maxdd": u["maxdd"], "maxdd_pct": u["maxdd"] / MARGIN * 100,
            "gap_longest": u["gap_longest"], "gap_mean": u["gap_mean"], "tail": u["tail"],
            "rr": float(w.mean() / abs(l.mean())) if len(w) and len(l) else float("nan")}


# ───────────────────────────── AUC всех признаков сразу ─────────────────────────────

def rank_matrix(X):
    """Средние ранги значений столбца среди не-NaN; NaN → 0. Возвращает (R, V): R float64 n×F, V — маска не-NaN."""
    X = np.asarray(X, float)
    n, F = X.shape
    R = np.zeros((n, F))
    V = ~np.isnan(X)
    for j in range(F):
        v = V[:, j]
        x = X[v, j]
        if len(x) == 0:
            continue
        order = np.argsort(x, kind="stable")
        xs = x[order]
        r = np.empty(len(x))
        # средние ранги по группам равных значений
        starts = np.concatenate([[0], np.nonzero(xs[1:] != xs[:-1])[0] + 1, [len(xs)]])
        rs = np.empty(len(xs))
        for a, b in zip(starts[:-1], starts[1:]):
            rs[a:b] = (a + 1 + b) / 2.0
        r[order] = rs
        R[v, j] = r
    return R, V


def auc_from_ranks(Y, R, V):
    """AUC (P(значение у метки 1 больше), ничьи пополам) для каждой строки меток Y (B×n, 0/1) и каждого признака: B×F, NaN при пустой стороне."""
    Y = np.atleast_2d(np.asarray(Y, float))
    S = Y @ R
    n1 = Y @ V.astype(float)
    nv = V.sum(0).astype(float)
    n0 = nv[None, :] - n1
    with np.errstate(invalid="ignore", divide="ignore"):
        a = (S - n1 * (n1 + 1) / 2.0) / (n1 * n0)
    a[(n1 == 0) | (n0 == 0)] = np.nan
    return a


def auc_all(X, y):
    R, V = rank_matrix(X)
    return auc_from_ranks(np.asarray(y, float)[None, :], R, V)[0]


def null_labels(y, B, kind, rng, min_shift=None):
    """B строк перемешанных меток. N1 — перестановка; N2 — циклический сдвиг ряда (порядок строк = порядок по времени) на k, |k| ≥ max(10, 5 % n)."""
    y = np.asarray(y, float)
    n = len(y)
    if kind == "N1":
        return np.stack([y[rng.permutation(n)] for _ in range(B)])
    ms = max(10, int(math.ceil(0.05 * n))) if min_shift is None else min_shift
    if n < 2 * ms + 1:
        raise ValueError("мало сделок для сдвига: n=%d, min_shift=%d" % (n, ms))
    ks = rng.integers(ms, n - ms + 1, size=B)
    return np.stack([np.roll(y, int(k)) for k in ks])


def null_auc(R, V, y, B, kind, rng, chunk=500):
    """AUC* (B×F) при перемешанных метках; куски по `chunk` строк."""
    out = []
    for a in range(0, B, chunk):
        Y = null_labels(y, min(chunk, B - a), kind, rng)
        out.append(auc_from_ranks(Y, R, V))
    return np.concatenate(out)


def family_threshold(A, alpha):
    """Семейный порог (Westfall–Young): квантиль 1 − alpha по перестановкам от max_f |AUC*_f − 0,5| (A — B×F)."""
    dev = np.abs(A - 0.5)
    mx = np.nanmax(np.where(np.isnan(dev), -np.inf, dev), axis=1)
    return float(np.quantile(mx, 1.0 - alpha))


def norm_ppf(q):
    """Φ⁻¹(q) без scipy (Acklam, ошибка < 1.2e-9)."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    if not 0 < q < 1:
        raise ValueError(q)
    if q < 0.02425:
        s = math.sqrt(-2 * math.log(q))
        return (((((c[0] * s + c[1]) * s + c[2]) * s + c[3]) * s + c[4]) * s + c[5]) / ((((d[0] * s + d[1]) * s + d[2]) * s + d[3]) * s + 1)
    if q > 1 - 0.02425:
        return -norm_ppf(1 - q)
    s = q - 0.5
    r = s * s
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * s / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def eligible_columns(X, min_nonnan=0.5, min_distinct=3):
    """Пригодность (по половине 1, без меток): доля не-NaN ≥ 50 %, ≥ 3 различных значений; одинаковые столбцы — одна копия (первая). Возвращает индексы."""
    X = np.asarray(X, float)
    keep, seen = [], set()
    for j in range(X.shape[1]):
        v = X[:, j]
        ok = ~np.isnan(v)
        if ok.mean() < min_nonnan or len(np.unique(v[ok])) < min_distinct:
            continue
        key = hash(np.where(ok, v, np.inf).tobytes())
        if key in seen:
            continue
        seen.add(key)
        keep.append(j)
    return keep


# ───────────────────────────── дерево глубины ≤ 3 (CART на numpy) ─────────────────────────────

def _gini(pos, n):
    p = pos / n
    return 2.0 * p * (1.0 - p)


def cart_fit(X, y, max_depth=3, min_leaf=20, nq=19):
    """Критерий Джини, пороги — nq квантилей (шаг 5 %) значений в узле, ничья — первый по порядку признаков и порогов. X без NaN. Дерево — список узлов."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n_all, F = X.shape
    qs = np.linspace(0, 1, nq + 2)[1:-1]
    nodes = []

    def build(idx, depth):
        nid = len(nodes)
        n = len(idx)
        pos = float(y[idx].sum())
        node = {"n": n, "pos": pos, "p": pos / n if n else float("nan"), "leaf": True}
        nodes.append(node)
        if depth >= max_depth or n < 2 * min_leaf or pos == 0 or pos == n:
            return nid
        base = _gini(pos, n)
        best = None
        yi = y[idx]
        for j in range(F):
            xs = X[idx, j]
            order = np.argsort(xs, kind="stable")
            xs_s, ys = xs[order], yi[order]
            cum = np.cumsum(ys)
            for thr in np.unique(np.quantile(xs, qs)):
                nl = int(np.searchsorted(xs_s, thr, side="right"))
                nr = n - nl
                if nl < min_leaf or nr < min_leaf:
                    continue
                pl = float(cum[nl - 1])
                gain = base - (nl / n) * _gini(pl, nl) - (nr / n) * _gini(pos - pl, nr)
                if best is None or gain > best[0] + 1e-15:
                    best = (gain, j, float(thr))
        if best is None or best[0] <= 1e-12:
            return nid
        _, j, thr = best
        left = idx[X[idx, j] <= thr]
        right = idx[X[idx, j] > thr]
        node.update(leaf=False, feat=j, thr=thr)
        node["l"] = build(left, depth + 1)
        node["r"] = build(right, depth + 1)
        return nid

    build(np.arange(n_all), 0)
    return nodes


def cart_leaf(nodes, X):
    """Номер листа для каждой строки X."""
    X = np.asarray(X, float)
    out = np.empty(len(X), np.int64)
    for i in range(len(X)):
        k = 0
        while not nodes[k]["leaf"]:
            k = nodes[k]["l"] if X[i, nodes[k]["feat"]] <= nodes[k]["thr"] else nodes[k]["r"]
        out[i] = k
    return out


def cart_score(nodes, X):
    """Оценка строки = доля меток 1 в её листе на обучении."""
    leaf = cart_leaf(nodes, X)
    p = np.array([nd["p"] for nd in nodes])
    return p[leaf]


def cart_depth(nodes, k=0):
    return 0 if nodes[k]["leaf"] else 1 + max(cart_depth(nodes, nodes[k]["l"]), cart_depth(nodes, nodes[k]["r"]))


def auc_scores(score, y):
    """AUC одной оценки против меток (ничьи пополам)."""
    return float(auc_all(np.asarray(score, float)[:, None], y)[0])


# ───────────────────────────── присоединение признаков ─────────────────────────────

def join_unique(keys, rows_key, rows_feat):
    """Каждому ключу сделки — вектор признаков строки матрицы с тем же ключом. Дубли ключа: векторы совпадают → первая строка, иначе не присоединена.
    keys: список кортежей-ключей сделок; rows_key: список ключей строк матрицы; rows_feat: массив строк×признаков. Возвращает (индекс строки или −1, причина):
    0 — присоединена, 1 — ключа нет в матрице, 2 — дубль с разными векторами."""
    rows_feat = np.asarray(rows_feat, float)
    where = {}
    for i, k in enumerate(rows_key):
        where.setdefault(k, []).append(i)
    ix = np.full(len(keys), -1, np.int64)
    why = np.zeros(len(keys), np.int8)
    for a, k in enumerate(keys):
        ids = where.get(k)
        if not ids:
            why[a] = 1
            continue
        if len(ids) > 1:
            m = rows_feat[ids]
            same = np.all((m == m[0]) | (np.isnan(m) & np.isnan(m[0])))
            if not same:
                why[a] = 2
                continue
        ix[a] = ids[0]
    return ix, why
