"""TK-730 (П-TK729 §6 PS-1066): корзина монет под грид — недели, признаки режима, ранговая корреляция, бутстреп, метрики корзины.

Недели: пн–вс UTC, сутки отчёта 0 = 01.01.2026 (чт); первая неделя — пн 26.01 (сутки 25), последняя полная — пн 16.11 (сутки 319); 23–24.11 — неполная неделя, в счёт не входит.
Половины по дате начала недели (§2 делит 30.06 / 01.07): H1 — недели с началом 26.01…29.06 (23), H2 — 06.07…16.11 (20).
Все признаки недели k считаются по минуткам строго до её начала (последние 7 суток, минуты [m0 − 10 080, m0)), m0 — полночь UTC понедельника.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402

WEEK0 = 25  # сутки отчёта первой недели (пн 26.01.2026)
NW = 43  # недели 26.01 … 16.11 (начала)
H1W = list(range(0, 23))  # начала 26.01 … 29.06
H2W = list(range(23, 43))  # начала 06.07 … 16.11
WIN = 7 * 1440  # окно признаков, минут
BLOCK_W = 3  # блок бутстрепа по неделям
FEATS = ("pnl_prev", "chop", "vr", "hl", "cover", "turn", "beta", "trend")
FEAT_RU = {"pnl_prev": "P&L прошлой недели", "chop": "пилообразность", "vr": "VR 60 мин", "hl": "полураспад OU", "cover": "σ_eq / шаг 0,5 %",
           "turn": "оборот", "beta": "бета к BTC", "trend": "сила тренда"}
STEP = 0.005  # шаг сетки C0 / охват §6.2 п. 5
MIN_COINS = 10  # меньше монет в неделе — ρ недели не считается


def week_start_day(k: int) -> int:
    """Сутки отчёта начала недели k."""
    return WEEK0 + 7 * k


def week_start_min(k: int) -> int:
    """Минута сетки G0 полуночи UTC понедельника недели k (признаки — строго до неё)."""
    return (L.DAY0 + week_start_day(k)) * 1440


def week_date(k: int) -> str:
    return L.date_str(L.DAY0 + week_start_day(k))


def weekly(daily: np.ndarray) -> np.ndarray:
    """(ncoin, 328) суточных → (ncoin, NW) недельных сумм."""
    out = np.zeros((daily.shape[0], NW))
    for k in range(NW):
        a = week_start_day(k)
        out[:, k] = daily[:, a:a + 7].sum(axis=1)
    return out


# ───────────────────────────── ранги ─────────────────────────────

def rank_avg(x: np.ndarray) -> np.ndarray:
    """Средние ранги (1…n), связки — средним."""
    x = np.asarray(x, float)
    n = len(x)
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    flag = np.concatenate([[True], xs[1:] != xs[:-1], [True]])
    idx = np.flatnonzero(flag)
    cnt = np.diff(idx)
    avg = (idx[:-1] + 1 + idx[1:]) / 2.0
    r = np.empty(n)
    r[order] = np.repeat(avg, cnt)
    return r


def spearman(x, y, min_n: int = 3) -> float:
    """Ранговая корреляция Спирмена по парам без NaN; NaN, если пар меньше min_n или ряд постоянен."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < min_n:
        return float("nan")
    rx, ry = rank_avg(x[m]), rank_avg(y[m])
    rx -= rx.mean()
    ry -= ry.mean()
    d = math.sqrt(float((rx * rx).sum() * (ry * ry).sum()))
    return float((rx * ry).sum() / d) if d > 0 else float("nan")


# ───────────────────────────── бутстреп и Холм ─────────────────────────────

def boot_week(x, block: int = BLOCK_W) -> dict:
    """Среднее ряда по неделям, 95 % интервал и двусторонний p кругового блочного бутстрепа (20 000, зерно 20261128)."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return dict(mean=float("nan"), lo=float("nan"), hi=float("nan"), p=float("nan"), n=len(x))
    bm = L.block_means(x, block, L.NBOOT, L.SEED)
    lo, hi = np.percentile(bm, [2.5, 97.5])
    nb = len(bm)
    p = 2.0 * min((np.sum(bm <= 0.0) + 1) / (nb + 1), (np.sum(bm >= 0.0) + 1) / (nb + 1))
    return dict(mean=float(x.mean()), lo=float(lo), hi=float(hi), p=float(min(p, 1.0)), n=len(x))


def holm(ps) -> list[float]:
    """Скорректированные p-значения Холма (монотонные)."""
    ps = np.asarray(ps, float)
    m = len(ps)
    order = np.argsort(ps)
    adj = np.empty(m)
    run = 0.0
    for i, j in enumerate(order):
        run = max(run, (m - i) * ps[j])
        adj[j] = min(run, 1.0)
    return adj.tolist()


# ───────────────────────────── 6.1: постоянство ─────────────────────────────

def persistence(pnl_w: np.ndarray, traded_w: np.ndarray) -> dict:
    """§6.1. pnl_w, traded_w — (ncoin, NW). ρ недели k: Спирмен P&L недели k и k + 1 по монетам с торговлей в обе недели; рядом — верхний дециль недели k (по монетам с торговлей в k)
    против всех таких монет по P&L недели k + 1 (средний на монету, $; монета без торговли на k + 1 — ноль)."""
    npair = pnl_w.shape[1] - 1
    rho = np.full(npair, np.nan)
    ncoin = np.zeros(npair, int)
    top_m = np.full(npair, np.nan)
    all_m = np.full(npair, np.nan)
    top_sum = np.full(npair, np.nan)
    ntop = np.zeros(npair, int)
    for k in range(npair):
        both = traded_w[:, k] & traded_w[:, k + 1]
        ncoin[k] = int(both.sum())
        if both.sum() >= MIN_COINS:
            rho[k] = spearman(pnl_w[both, k], pnl_w[both, k + 1])
        u = np.flatnonzero(traded_w[:, k])
        if len(u) >= MIN_COINS:
            nt = int(math.ceil(0.1 * len(u)))
            order = u[np.argsort(-pnl_w[u, k], kind="mergesort")]
            top = order[:nt]
            ntop[k] = nt
            top_m[k] = float(pnl_w[top, k + 1].mean())
            all_m[k] = float(pnl_w[u, k + 1].mean())
            top_sum[k] = float(pnl_w[top, k + 1].sum())
    diff = top_m - all_m
    return dict(rho=rho, ncoin=ncoin, top_m=top_m, all_m=all_m, diff=diff, top_sum=top_sum, ntop=ntop,
                rho_b=boot_week(rho), top_b=boot_week(top_m), all_b=boot_week(all_m), diff_b=boot_week(diff))


# ───────────────────────────── признаки режима §6.2 (по прошлым 7 суткам) ─────────────────────────────

def chop_ratio(p_h: np.ndarray) -> float:
    """Пилообразность: Σ|часовых сдвигов| / |сдвиг за всё окно| по часовым ln-ценам (обратный коэффициент эффективности; прямая = 1, нулевой сдвиг → +∞)."""
    p_h = np.asarray(p_h, float)
    if len(p_h) < 3 or not np.isfinite(p_h).all():
        return float("nan")
    den = abs(p_h[-1] - p_h[0])
    num = float(np.abs(np.diff(p_h)).sum())
    return num / den if den > 0 else float("inf")


def variance_ratio(lnc: np.ndarray, q: int = 60) -> float:
    """VR = var(q-минутных доходностей с шагом 1 мин) / (q · var(1-минутных)) по ряду ln-цен окна (случайное блуждание → 1)."""
    lnc = np.asarray(lnc, float)
    if len(lnc) < 3 * q or not np.isfinite(lnc).all():
        return float("nan")
    r1 = np.diff(lnc)
    rq = lnc[q:] - lnc[:-q]
    v1 = float(r1.var())
    return float(rq.var() / (q * v1)) if v1 > 0 else float("nan")


def beta_to(ri: np.ndarray, rb: np.ndarray) -> float:
    """Бета: cov(ri, rb) / var(rb) по доходностям окна."""
    ri, rb = np.asarray(ri, float), np.asarray(rb, float)
    if len(ri) != len(rb) or len(ri) < 10 or not (np.isfinite(ri).all() and np.isfinite(rb).all()):
        return float("nan")
    vb = float(rb.var())
    return float(((ri - ri.mean()) * (rb - rb.mean())).mean() / vb) if vb > 0 else float("nan")


def trend_strength(lnc: np.ndarray) -> float:
    """|ln-доходность за окно| / σ дневная, σ дневная = √(Σ r²_1мин / число суток окна) (реализованная по минуткам)."""
    lnc = np.asarray(lnc, float)
    if len(lnc) < 1440 or not np.isfinite(lnc).all():
        return float("nan")
    r1 = np.diff(lnc)
    nd = len(r1) / 1440.0
    s = math.sqrt(float((r1 * r1).sum()) / nd)
    return abs(float(lnc[-1] - lnc[0])) / s if s > 0 else float("nan")


def med_finite(a: np.ndarray) -> float:
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return float(np.median(a)) if len(a) else float("nan")


def ln_ffill(S: dict) -> np.ndarray:
    """ln закрытия с протяжкой вперёд по пропущенным минутам; до первой свечи — NaN."""
    with np.errstate(invalid="ignore", divide="ignore"):
        x = np.log(np.where(S["pres"], S["c"], np.nan))
    return L.ffill(x)


def week_features(S: dict, ou: dict, lnb_h: np.ndarray, k: int, lnc: np.ndarray | None = None) -> np.ndarray:
    """Семь признаков §6.2 (кроме P&L) монеты на неделю k, строго по минутам [m0 − 10 080, m0): chop, vr, hl, cover, turn, beta, trend.
    ou — результат `L.ou_rolling` (по минутам, причинный); lnb_h — ln BTC на минутной сетке (протянутый), для беты — часовые доходности. Нет ≥ ½ минут окна → все NaN."""
    m0 = week_start_min(k)
    a = m0 - WIN
    out = np.full(7, np.nan)
    if a < 0:
        return out
    pres = S["pres"][a:m0]
    if pres.sum() * 2 < WIN:
        return out
    if lnc is None:
        lnc = ln_ffill(S)
    w = lnc[a:m0]
    if not np.isfinite(w).all():
        return out
    ph = w[59::60]  # ln закрытия последней минуты каждого часа окна: 168 точек, последняя — минута m0 − 1 (до начала недели)
    out[0] = chop_ratio(ph)
    out[1] = variance_ratio(w, 60)
    samp = slice(a, m0, 15)
    out[2] = med_finite(ou["hl"][samp])
    out[3] = med_finite(ou["sig_eq"][samp]) / STEP
    out[4] = float(S["qv"][a:m0].sum())
    wb = lnb_h[a:m0]
    pb = wb[59::60]
    out[5] = beta_to(np.diff(ph), np.diff(pb))
    out[6] = trend_strength(w)
    return out


# ───────────────────────────── IC и корзина (§6.2–§6.3) ─────────────────────────────

def ic_weekly(feat_w: np.ndarray, pnl_w: np.ndarray, traded_w: np.ndarray) -> np.ndarray:
    """IC недели k: Спирмен признака, известного до начала недели k (feat_w[:, k]), с P&L недели k по монетам с признаком и торговлей на неделе k. (NW,)"""
    ic = np.full(feat_w.shape[1], np.nan)
    for k in range(feat_w.shape[1]):
        m = np.isfinite(feat_w[:, k]) & traded_w[:, k]
        if m.sum() >= MIN_COINS:
            ic[k] = spearman(feat_w[m, k], pnl_w[m, k])
    return ic


def pick_basket(feat_w: np.ndarray, k: int, sign: int, kk: int, elig: np.ndarray | None = None) -> np.ndarray:
    """Индексы верхних kk монет недели k по признаку × знак (по монетам с конечным признаком; связки — по индексу монеты)."""
    f = feat_w[:, k] * sign
    ok = np.isfinite(f)
    if elig is not None:
        ok &= elig[:, k]
    idx = np.flatnonzero(ok)
    if len(idx) == 0:
        return idx
    order = idx[np.argsort(-f[idx], kind="mergesort")]
    return order[:kk]
