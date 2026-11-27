"""TK-727 (П-42 этап A, §13.78.3 шаг 3, §13.78.5 шаг 5, §13.78.7): общие функции — загрузка минуток, оценщики волатильности, шаг цены, блочный бутстреп.

Формула NATR — копия `src/breakout/data.rs:346-382` (истинный размах по закрытию предыдущей присутствующей минуты, среднее по присутствующим, деление на закрытие минуты `t`;
значение есть только у присутствующей минуты `t` и при k·2 ≥ p). Реализованная волатильность — `rv_of` (`data.rs:387-410`). Индекс `t` — закрытая минута, моментом считается
следующая минута `m = t + 1` (в движке `natr[sig_ms // 60000 − 1]`).

Сетка: минуты от `G0_MS` (12.12.2025 00:00 UTC, первая свеча m01); сутки номер 0 — 01.01.2026 (`DAY0 = 20` суток от G0), как в `tk718_tests`.
"""
from __future__ import annotations

import math
import os

import numpy as np

SEED = 20261127  # §13.78 п. 11: одно зерно на этап
NBOOT = 20000
KROOT = "/data/p42-tk621"
WINS = ("m01", "m02", "m03", "m04", "m05", "m06", "wa20", "wa", "wb", "wc", "n1")  # порядок: первая найденная копия дня
G0_MS = 1765497600000  # 2025-12-12 00:00 UTC
DAY_MS = 86_400_000
MIN_MS = 60_000
DAY0 = 20  # сутки G0 → 01.01.2026
NDAYS = 348  # 12.12.2025 … 24.11.2026
NMIN = NDAYS * 1440
LAST_DAY = 327  # 24.11.2026 (сутки от 01.01.2026)
# границы периодов в сутках от 01.01.2026 (FJ 31–180, SEL 181–316, N1 317–327), tk718_tests
PERIODS = {
    "jan": (0, 31), "feb": (31, 59), "mar": (59, 90), "apr": (90, 120), "may": (120, 151), "jun": (151, 181),
    "jul": (181, 212), "aug": (212, 243), "sep": (243, 273), "oct": (273, 304), "nov1": (304, 317), "n1": (317, 328),
}
HALVES = {"FJ": (31, 181), "SEL": (181, 317)}
BURN = 30  # разгон: сутки 0–29 только для масштаба и первой подгонки HAR (§13.78.7 п. 8)
HL = 30.0  # полураспад EWTR, минут
EW_WIN = 600  # окно усечения EWTR (20 полураспадов)
NATR_P = (5, 15, 30, 60)
RV_P = (5, 15, 30, 60)
EST_NAMES = ["natr5", "natr15", "natr30", "natr60", "rv5", "rv15", "rv30", "rv60", "ewtr", "gk60", "bv60", "har"]
TICK_DAYS = 7  # `MK_TICK_DAYS` движка


def day_of_minute(m):
    """Сутки от 01.01.2026 для минуты сетки m."""
    return m // 1440 - DAY0


def date_str(di: int) -> str:
    import datetime

    return (datetime.date(2025, 12, 12) + datetime.timedelta(days=int(di))).isoformat()


# ───────────────────────────── загрузка ─────────────────────────────

def csv_path(sym: str, di: int, kroot: str = KROOT, wins=WINS):
    fn = date_str(di) + ".csv"
    for w in wins:
        p = os.path.join(kroot, w, "klines", sym, fn)
        if os.path.exists(p):
            return p
    return None


def load_coin(sym: str, kroot: str = KROOT, wins=WINS, ndays: int = NDAYS, with_vol: bool = False):
    """Минутки монеты на сетку G0: o, h, l, c, qv (float64, NaN — минуты нет), pres (bool); with_vol — ещё `vol` (базовый объём; шаг 5, VWAP минуты)."""
    n = ndays * 1440
    o, h, l, c = (np.full(n, np.nan) for _ in range(4))
    qv = np.zeros(n)
    vol = np.zeros(n) if with_vol else None
    pres = np.zeros(n, bool)
    cols = (0, 1, 2, 3, 4, 5, 7) if with_vol else (0, 1, 2, 3, 4, 7)
    for di in range(ndays):
        p = csv_path(sym, di, kroot, wins)
        if p is None:
            continue
        a = np.loadtxt(p, delimiter=",", skiprows=1, usecols=cols, ndmin=2)
        if a.size == 0:
            continue
        k = ((a[:, 0] - G0_MS) // MIN_MS).astype(np.int64)
        ok = (k >= 0) & (k < n)
        k = k[ok]
        o[k], h[k], l[k], c[k], qv[k] = a[ok, 1], a[ok, 2], a[ok, 3], a[ok, 4], a[ok, -1]
        if with_vol:
            vol[k] = a[ok, 5]
        pres[k] = True
    out = dict(o=o, h=h, l=l, c=c, qv=qv, pres=pres)
    if with_vol:
        out["vol"] = vol
    return out


# ───────────────────────────── оценщики ─────────────────────────────

def _wsum(a: np.ndarray, p: int) -> np.ndarray:
    """Скользящая сумма последних p элементов, конец окна — сам индекс (NaN, пока окна нет)."""
    cs = np.concatenate([[0.0], np.cumsum(a, dtype=np.float64)])
    out = np.full(len(a), np.nan)
    out[p - 1:] = cs[p:] - cs[:-p]
    return out


def true_range(S: dict) -> np.ndarray:
    """Истинный размах как в `natr_of`: закрытие предыдущей ПРИСУТСТВУЮЩЕЙ минуты (где угодно раньше), у первой присутствующей — h − l; у отсутствующей — 0."""
    h, l, c, pres = S["h"], S["l"], S["c"], S["pres"]
    n = len(c)
    idx = np.where(pres, np.arange(n), -1)
    last = np.maximum.accumulate(idx)
    prev_idx = np.concatenate([[-1], last[:-1]])
    prevc = np.where(prev_idx >= 0, c[np.maximum(prev_idx, 0)], np.nan)
    with np.errstate(invalid="ignore"):
        tr = np.fmax(np.fmax(h - l, np.abs(h - prevc)), np.abs(l - prevc))
    return np.where(pres, tr, 0.0)


def natr(S: dict, p: int, tr: np.ndarray | None = None) -> np.ndarray:
    """NATR_p[t] = Σ TR / (k · c[t]) (доля цены); NaN, если минуты t нет, k·2 < p или окна нет."""
    tr = true_range(S) if tr is None else tr
    pres = S["pres"]
    k = _wsum(pres.astype(np.float64), p)
    s = _wsum(tr, p)
    c = S["c"]
    with np.errstate(invalid="ignore", divide="ignore"):
        v = s / (k * c)
    ok = pres & (k * 2 >= p)
    return np.where(ok, v, np.nan)


def returns(S: dict):
    """r[j] = (c[j] − c[j−1]) / c[j−1] при присутствии обеих минут; rv — признак доходности."""
    c, pres = S["c"], S["pres"]
    r = np.zeros(len(c))
    rv = np.zeros(len(c), bool)
    rv[1:] = pres[1:] & pres[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        r[1:] = np.where(rv[1:], (c[1:] - c[:-1]) / c[:-1], 0.0)
    return r, rv


def rv_sqrt_sum(S: dict, p: int, rr=None) -> np.ndarray:
    """Реализованная волатильность кода 5–8: √Σ r² по p минутам; нужна минута t, окно с t ≥ p, ≥ ⌈p/2⌉ доходностей."""
    r, rv = rr if rr is not None else returns(S)
    s2 = _wsum(r * r, p)
    k = _wsum(rv.astype(np.float64), p)
    ok = S["pres"] & (k * 2 >= p)
    ok[:p] = False
    return np.where(ok, np.sqrt(s2), np.nan)


def rv_mean(S: dict, p: int, rr=None) -> np.ndarray:
    """√(Σ r² / k) — волатильность одной минуты по окну p (вход HAR); те же правила наличия."""
    r, rv = rr if rr is not None else returns(S)
    s2 = _wsum(r * r, p)
    k = _wsum(rv.astype(np.float64), p)
    ok = S["pres"] & (k * 2 >= p)
    ok[:p] = False
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ok, np.sqrt(s2 / k), np.nan)


def ewtr(S: dict, tr: np.ndarray | None = None, hl: float = HL, win: int = EW_WIN) -> np.ndarray:
    """№ 9: сглаженный истинный размах — взвешенное среднее TR / c по присутствующим минутам, вес λ^возраст, λ = 2^(−1/hl); окно усечено `win` минутами, значение есть,
    если набрано ≥ ½ полного веса окна; минуты t нет → NaN."""
    tr = true_range(S) if tr is None else tr
    pres = S["pres"]
    lam = 0.5 ** (1.0 / hl)
    w = lam ** np.arange(win)
    with np.errstate(invalid="ignore", divide="ignore"):
        x = np.where(pres, tr / S["c"], 0.0)
    num = np.convolve(x, w)[: len(x)]
    den = np.convolve(pres.astype(np.float64), w)[: len(x)]
    full = w.sum()
    ok = pres & (den >= 0.5 * full)
    ok[: win // 2] = False
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ok, num / den, np.nan)


def gk60(S: dict, p: int = 60) -> np.ndarray:
    """№ 10: Garman–Klass: √ среднего по присутствующим минутам окна p от 0,5·ln(h/l)² − (2ln2 − 1)·ln(c/o)²; минута t обязана быть; ≥ ½ окна."""
    o, h, l, c, pres = S["o"], S["h"], S["l"], S["c"], S["pres"]
    with np.errstate(invalid="ignore", divide="ignore"):
        g = 0.5 * np.log(h / l) ** 2 - (2 * math.log(2) - 1) * np.log(c / o) ** 2
    g = np.where(pres, g, 0.0)
    s = _wsum(g, p)
    k = _wsum(pres.astype(np.float64), p)
    ok = pres & (k * 2 >= p)
    with np.errstate(invalid="ignore", divide="ignore"):
        v = s / k
    return np.where(ok, np.sqrt(np.maximum(v, 0.0)), np.nan)


def bipower_mean(S: dict, p: int = 60, rr=None) -> np.ndarray:
    """Среднее (π/2)·|r_j||r_{j−1}| по окну p (пары подряд идущих доходностей); NaN при нехватке (< ½ окна пар) или без минуты t."""
    r, rv = rr if rr is not None else returns(S)
    a = np.abs(r)
    pair = np.zeros(len(a))
    pv = np.zeros(len(a), bool)
    pair[1:] = a[1:] * a[:-1]
    pv[1:] = rv[1:] & rv[:-1]
    pair = np.where(pv, pair, 0.0)
    s = _wsum(pair, p)
    k = _wsum(pv.astype(np.float64), p)
    ok = S["pres"] & (k * 2 >= p)
    ok[:p] = False
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ok, (math.pi / 2) * s / k, np.nan)


def bv60(S: dict, p: int = 60, rr=None) -> np.ndarray:
    """№ 11: оценка без скачков — √ bipower_mean."""
    return np.sqrt(bipower_mean(S, p, rr))


def jump_share(S: dict, p: int = 60, rr=None) -> np.ndarray:
    """F6: доля скачков 1 − BV / RV² за p минут (обе в виде средних за минуту), обрезана снизу нулём; NaN при нехватке."""
    r, rv = rr if rr is not None else returns(S)
    bvm = bipower_mean(S, p, (r, rv))
    rvm = rv_mean(S, p, (r, rv)) ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        j = 1.0 - bvm / rvm
    return np.where(np.isfinite(j), np.maximum(j, 0.0), np.nan)


def tick_by_day(S: dict, ndays: int = NDAYS) -> np.ndarray:
    """Шаг цены на сутки d: НОД цен (×1e8, целые) за предыдущие TICK_DAYS суток (как `tick_at`, сделки заменены свечами); 0 — нет данных."""
    day_g = np.zeros(ndays, np.int64)
    for d in range(ndays):
        a, b = d * 1440, (d + 1) * 1440
        pr = S["pres"][a:b]
        if not pr.any():
            continue
        v = np.concatenate([S[k][a:b][pr] for k in ("o", "h", "l", "c")])
        iv = np.rint(v * 1e8).astype(np.int64)
        iv = iv[iv > 0]
        if len(iv):
            day_g[d] = int(np.gcd.reduce(iv))
    out = np.zeros(ndays, np.int64)
    for d in range(ndays):
        w = day_g[max(0, d - TICK_DAYS):d]
        w = w[w > 0]
        out[d] = int(np.gcd.reduce(w)) if len(w) else 0
    return out


def all_estimators(S: dict) -> dict:
    """Ряды оценщиков кодов 1–11 по закрытой минуте t (код 12 — HAR — собирается в `tk727_s3` из входов `rvm60/rvm1440/rvm10080`)."""
    tr = true_range(S)
    rr = returns(S)
    E = {}
    for p in NATR_P:
        E[f"natr{p}"] = natr(S, p, tr)
    for p in RV_P:
        E[f"rv{p}"] = rv_sqrt_sum(S, p, rr)
    E["ewtr"] = ewtr(S, tr)
    E["gk60"] = gk60(S)
    E["bv60"] = bv60(S, 60, rr)
    E["rvm60"] = rv_mean(S, 60, rr)
    E["rvm1440"] = rv_mean(S, 1440, rr)
    E["rvm10080"] = rv_mean(S, 10080, rr)
    return E


# ───────────────────────────── размах ─────────────────────────────

def future_range(S: dict, m: np.ndarray, H: int) -> np.ndarray:
    """Размах следующих H минут [m, m+H): (max h − min l) / c[m−1]; NaN, если закрытие m−1 отсутствует, окно выходит за сетку или присутствует < ½ H минут.
    Нижняя граница — 1 шаг цены / цена — накладывается снаружи (нужен шаг)."""
    n = len(S["c"])
    out = np.full(len(m), np.nan)
    ok = (m >= 1) & (m + H <= n)
    mm = m[ok]
    from numpy.lib.stride_tricks import sliding_window_view

    hw = sliding_window_view(S["h"], H)[mm]
    lw = sliding_window_view(S["l"], H)[mm]
    pw = sliding_window_view(S["pres"].astype(np.int8), H)[mm]
    cnt = pw.sum(axis=1)
    with np.errstate(all="ignore"):
        hi = np.nanmax(hw, axis=1)
        lo = np.nanmin(lw, axis=1)
    c0 = S["c"][mm - 1]
    rr = (hi - lo) / c0
    good = S["pres"][mm - 1] & (cnt * 2 >= H)
    out[np.where(ok)[0][good]] = rr[good]
    return out


# ───────────────────────────── статистика ─────────────────────────────

def block_means(x: np.ndarray, block: int, nboot: int | None = None, seed: int = SEED) -> np.ndarray:
    """Средние повторов кругового бутстрепа блоками по суткам (зерно — константа этапа)."""
    nboot = NBOOT if nboot is None else nboot
    rng = np.random.default_rng(seed)
    n = len(x)
    nb = math.ceil(n / block)
    starts = rng.integers(0, n, size=(nboot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    idx = idx.reshape(nboot, nb * block)[:, :n]
    return x[idx].mean(axis=1)


def block_len(n: int) -> int:
    """⌈n^{1/3}⌉ суток (§13.78 п. 1)."""
    return int(math.ceil(round(n ** (1.0 / 3.0), 12)))


def stat_block(x, block: int, nboot: int | None = None, seed: int = SEED) -> dict:
    """Среднее суточного ряда, SE и 95 % перцентильный интервал блочного бутстрепа, двусторонний p по нормали (как `lib.stat`)."""
    from statistics import NormalDist

    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    m = float(x.mean())
    bm = block_means(x, block, nboot, seed)
    se = float(bm.std(ddof=1))
    lo, hi = np.percentile(bm, [2.5, 97.5])
    z = m / se if se > 0 else 0.0
    p = 2 * (1 - NormalDist().cdf(abs(z))) if se > 0 else 1.0
    return dict(mean=m, se=se, lo=float(lo), hi=float(hi), z=z, p=p, n=len(x), block=block)


def holm(pvals):
    """Холм: скорректированные p в исходном порядке (как `tools/p42/lib.holm`)."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * pvals[i]))
        adj[i] = run
    return adj


def qlike(rr: np.ndarray, f: np.ndarray) -> np.ndarray:
    """QLIKE: RR/F − ln(RR/F) − 1."""
    q = rr / f
    return q - np.log(q) - 1.0


def day_means(day: np.ndarray, x: np.ndarray, d0: int, d1: int) -> np.ndarray:
    """Суточные средние x по моментам; сутки [d0, d1) без моментов — NaN (отбрасываются снаружи)."""
    sel = (day >= d0) & (day < d1) & np.isfinite(x)
    n = d1 - d0
    s = np.bincount(day[sel] - d0, weights=x[sel], minlength=n)[:n]
    k = np.bincount(day[sel] - d0, minlength=n)[:n]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(k > 0, s / k, np.nan)
