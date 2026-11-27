"""TK-729 (П-TK729 §1 PS-1061, §2 PS-1062): тестер грида на минутках — загрузка, признаки, ядро исполнения, блочный бутстреп.

Загрузка минуток, формула rv60/NATR, шаг цены, бутстреп — копия `tools/p42/tk727_lib.py` ветки TK-727 (не сливается). Сетка минут: от G0 = 12.12.2025 00:00 UTC, сутки 0 отчёта — 01.01.2026 (DAY0 = 20).

Ядро `grid_run` — один счёт монеты: нейтральная сетка из 10 уровней на сторону, исполнение на минутках по §1 (строго на шаг цены; встречный ордер и повторный вход — не раньше следующей минуты),
издержки по тарифу (мейкер 0,014 % − 10 % возврата, тейкер 0,035 %, проскальзывание 0,01 % на рыночных). Центр, фильтр режима и наклон — подключаемые массивы (`cen`, `allow`, `skw`): их
способ счёта задаётся снаружи (§2 может меняться поправкой), ядро их не знает.
"""
from __future__ import annotations

import math
import os

import numpy as np

try:
    from numba import njit
except Exception:  # без numba — чистый python (тесты на малых рядах)
    def njit(*a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda f: f

SEED = 20261128
NBOOT = 20000
KROOT = "/data/p42-tk621"
WINS = ("m01", "m02", "m03", "m04", "m05", "m06", "wa20", "wa", "wb", "wc", "n1")
SYMS = "/data/p42-tk621/universe/symbols-crypto.txt"  # c539 (как tk727_s3 / tk727_s5)
G0_MS = 1765497600000
MIN_MS = 60_000
DAY0 = 20  # сутки G0 → 01.01.2026
NDAYS = 348  # 12.12.2025 … 24.11.2026
NOUT = NDAYS - DAY0  # 328 суток отчёта (01.01 … 24.11)
PERIODS = {
    "jan": (0, 31), "feb": (31, 59), "mar": (59, 90), "apr": (90, 120), "may": (120, 151), "jun": (151, 181),
    "jul": (181, 212), "aug": (212, 243), "sep": (243, 273), "oct": (273, 304), "nov": (304, 328),
}
BURN = 30  # разгон фильтров: сутки отчёта 0–29 (01.01–30.01)
HALVES = {"H1": (BURN, 181), "H2": (181, NOUT)}  # 31.01–30.06 и 01.07–24.11 (сутки отчёта, [от, до))

# тариф В-47 / приказ 12: мейкер 1,4 bps − 10 % возврата; тейкер 3,5 bps (протокол §1: возврат только у мейкера), проскальзывание 1 bp на рыночных
MAKER = 0.00014 * 0.9
TAKER = 0.00035
SLIP = 0.0001
SIDE_NOTIONAL = 250.0  # сумма 10 уровней на сторону, $
NL = 10

# строки выхода ядра (по суткам отчёта)
R_PNL, R_CYC, R_EXIT, R_NCYC, R_NEDGE, R_NTIME, R_ACT, R_NOPEN, R_NMKT = range(9)
NR = 9


def read_syms(path: str = SYMS) -> list[str]:
    return sorted(x.strip() for x in open(path, encoding="utf-8").read().strip().split(",") if x.strip())


def date_str(di: int) -> str:
    import datetime

    return (datetime.date(2025, 12, 12) + datetime.timedelta(days=int(di))).isoformat()


# ───────────────────────────── загрузка (копия tk727_lib) ─────────────────────────────

def csv_path(sym: str, di: int, kroot: str = KROOT, wins=WINS):
    fn = date_str(di) + ".csv"
    for w in wins:
        p = os.path.join(kroot, w, "klines", sym, fn)
        if os.path.exists(p):
            return p
    return None


def load_coin(sym: str, kroot: str = KROOT, wins=WINS, ndays: int = NDAYS):
    """Минутки монеты на сетку G0: o, h, l, c, qv (float64, NaN — минуты нет), pres (bool)."""
    n = ndays * 1440
    o, h, l, c = (np.full(n, np.nan) for _ in range(4))
    qv = np.zeros(n)
    pres = np.zeros(n, bool)
    cols = (0, 1, 2, 3, 4, 7)
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
        pres[k] = True
    return dict(o=o, h=h, l=l, c=c, qv=qv, pres=pres)


# ───────────────────────────── признаки ─────────────────────────────

def _wsum(a: np.ndarray, p: int) -> np.ndarray:
    cs = np.concatenate([[0.0], np.cumsum(a, dtype=np.float64)])
    out = np.full(len(a), np.nan)
    out[p - 1:] = cs[p:] - cs[:-p]
    return out


def returns(S: dict):
    c, pres = S["c"], S["pres"]
    r = np.zeros(len(c))
    rv = np.zeros(len(c), bool)
    rv[1:] = pres[1:] & pres[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        r[1:] = np.where(rv[1:], (c[1:] - c[:-1]) / c[:-1], 0.0)
    return r, rv


def rv_sqrt_sum(S: dict, p: int = 60, rr=None) -> np.ndarray:
    """σ_p: √Σ r² по p минутам (в долях цены на горизонт p минут); есть при минуте t, окне t ≥ p и ≥ ⌈p/2⌉ доходностях (код 5–8 движка, победитель шага 3 П-42 при H = 60)."""
    r, rv = rr if rr is not None else returns(S)
    s2 = _wsum(r * r, p)
    k = _wsum(rv.astype(np.float64), p)
    ok = S["pres"] & (k * 2 >= p)
    ok[:p] = False
    return np.where(ok, np.sqrt(s2), np.nan)


def true_range(S: dict) -> np.ndarray:
    h, l, c, pres = S["h"], S["l"], S["c"], S["pres"]
    n = len(c)
    idx = np.where(pres, np.arange(n), -1)
    last = np.maximum.accumulate(idx)
    prev_idx = np.concatenate([[-1], last[:-1]])
    prevc = np.where(prev_idx >= 0, c[np.maximum(prev_idx, 0)], np.nan)
    with np.errstate(invalid="ignore"):
        tr = np.fmax(np.fmax(h - l, np.abs(h - prevc)), np.abs(l - prevc))
    return np.where(pres, tr, 0.0)


def natr(S: dict, p: int = 30, tr=None) -> np.ndarray:
    tr = true_range(S) if tr is None else tr
    pres = S["pres"]
    k = _wsum(pres.astype(np.float64), p)
    s = _wsum(tr, p)
    with np.errstate(invalid="ignore", divide="ignore"):
        v = s / (k * S["c"])
    return np.where(pres & (k * 2 >= p), v, np.nan)


def er_kaufman(S: dict, p: int = 60) -> np.ndarray:
    """ER_p = |c_t − c_{t−p}| / Σ|Δc| (коэффициент эффективности Кауфмана); NaN без минут t, t−p, при < ½ окна приращений или нулевом знаменателе."""
    c, pres = S["c"], S["pres"]
    n = len(c)
    rv = np.zeros(n, bool)
    rv[1:] = pres[1:] & pres[:-1]
    d = np.zeros(n)
    with np.errstate(invalid="ignore"):
        d[1:] = np.where(rv[1:], np.abs(c[1:] - c[:-1]), 0.0)
    den = _wsum(d, p)
    k = _wsum(rv.astype(np.float64), p)
    num = np.full(n, np.nan)
    num[p:] = np.abs(c[p:] - c[:-p])
    ok = pres & (k * 2 >= p) & (den > 0)
    ok[:p] = False
    with np.errstate(invalid="ignore", divide="ignore"):
        er = num / den
    return np.where(ok & np.isfinite(er), er, np.nan)


def tick_days(S: dict, ndays: int = NDAYS, win: int = 7) -> np.ndarray:
    """Шаг цены на сутки d: НОД цен (×1e8, целые) за предыдущие `win` суток; нет — НОД самих суток; 0 — свечей нет."""
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
        w = day_g[max(0, d - win):d]
        w = w[w > 0]
        out[d] = int(np.gcd.reduce(w)) if len(w) else day_g[d]
    return out / 1e8


def er_thresholds(er: np.ndarray, qs, ndays: int = NDAYS, win_days: int = 30, min_n: int = 1440) -> np.ndarray:
    """Порог θ[d, i] = квантиль qs[i] распределения ER за прошлые `win_days` суток [d − win, d); NaN, если значений < min_n."""
    th = np.full((ndays, len(qs)), np.nan)
    for d in range(win_days, ndays):
        v = er[(d - win_days) * 1440: d * 1440]
        v = v[np.isfinite(v)]
        if len(v) >= min_n:
            th[d] = np.quantile(v, qs)
    return th


def autocorr5_days(S: dict, ndays: int = NDAYS, win_days: int = 30, min_pairs: int = 2000) -> np.ndarray:
    """ρ1[d] — автокорреляция (лаг 1) 5-минутных доходностей за прошлые `win_days` суток [d − win, d); NaN, если пар < min_pairs.
    5-минутная свеча — закрытие минуты с индексом ≡ 4 (mod 5); пара — две подряд идущие 5-минутные доходности, обе присутствуют."""
    c = np.where(S["pres"], S["c"], np.nan)[4::5]  # 288 баров в сутки
    with np.errstate(invalid="ignore", divide="ignore"):
        r = c[1:] / c[:-1] - 1.0  # r[j] — доходность бара j+1
    x, y = r[1:], r[:-1]  # x — текущая, y — предыдущая; бар текущей доходности j+2
    ok = np.isfinite(x) & np.isfinite(y)
    bar = np.arange(2, 2 + len(x))
    day = bar // 288
    keep = ok & (day < ndays)
    day, x, y = day[keep], x[keep], y[keep]
    cnt = np.bincount(day, minlength=ndays).astype(np.float64)
    sx = np.bincount(day, x, minlength=ndays)
    sy = np.bincount(day, y, minlength=ndays)
    sxx = np.bincount(day, x * x, minlength=ndays)
    syy = np.bincount(day, y * y, minlength=ndays)
    sxy = np.bincount(day, x * y, minlength=ndays)
    out = np.full(ndays, np.nan)

    def roll(a, d):
        return a[d - win_days:d].sum()
    for d in range(win_days, ndays):
        n_ = roll(cnt, d)
        if n_ < min_pairs:
            continue
        mx, my = roll(sx, d) / n_, roll(sy, d) / n_
        vx = roll(sxx, d) / n_ - mx * mx
        vy = roll(syy, d) / n_ - my * my
        cv = roll(sxy, d) / n_ - mx * my
        if vx > 0 and vy > 0:
            out[d] = cv / math.sqrt(vx * vy)
    return out


@njit(cache=True)
def ema_series(c, pres, lam):
    """EMA закрытий по присутствующим минутам (λ = 2^(−1/полураспад)); значение на минуте t — после обновления закрытием t; до первой свечи NaN, в дырах переносится."""
    n = c.shape[0]
    out = np.full(n, np.nan)
    e = np.nan
    for t in range(n):
        if pres[t]:
            e = c[t] if np.isnan(e) else lam * e + (1.0 - lam) * c[t]
        out[t] = e
    return out


# ───────────────────────────── подключаемые центр / фильтр / отбор ─────────────────────────────

def center_ema(S: dict, halflife_min: float = 60.0) -> np.ndarray:
    """Центр сетки: EMA цены (полураспад `halflife_min` минут)."""
    return ema_series(S["c"], S["pres"], 0.5 ** (1.0 / halflife_min))


def allow_mask(er60: np.ndarray, thr_day: np.ndarray | None, sel_day: np.ndarray | None, ndays: int = NDAYS) -> np.ndarray:
    """Разрешение новых уровней по минутам (uint8): фильтр ER60 < θ[d] (если thr_day задан) И монета выбрана на сутки d (если sel_day задан). Нет значения → запрет."""
    n = ndays * 1440
    ok = np.ones(n, bool)
    if thr_day is not None:
        ok &= er60 < np.repeat(thr_day, 1440)
    if sel_day is not None:
        ok &= np.repeat(sel_day.astype(bool), 1440)
    return ok.astype(np.uint8)


def sel_days(rho: np.ndarray) -> np.ndarray:
    """Отбор монет на сутки: автокорреляция 5-минуток за прошлые 30 суток < 0."""
    with np.errstate(invalid="ignore"):
        return (rho < 0).astype(np.uint8)


# ───────────────────────────── ядро ─────────────────────────────

def make_params(*, g0=0.0, a=0.0, use_cen=0, b=0.0, timex=0, pause=0, t_start=DAY0 * 1440, maker=MAKER, taker=TAKER, slip=SLIP,
                side_not=SIDE_NOTIONAL, skew_coef=0.5, skew_mode=0, day0=DAY0, gmin_ticks=2.0) -> np.ndarray:
    """Вектор параметров ядра. g0 — шаг доли цены запуска (a = 0); a — множитель σ60 (g = a·σ60·центр); use_cen — центр из массива `cen` и наклон; b — наращивание объёма;
    timex — минут до закрытия лота по рынку (0 — нет); pause — пауза монеты после выхода за край, минут; skew_coef — наклон q·skew_coef·g (skew_mode 0) или q·skw[t] (1)."""
    return np.array([g0, a, float(use_cen), b, float(timex), float(pause), float(t_start), maker, taker, slip, side_not, skew_coef, float(skew_mode), float(day0), gmin_ticks], np.float64)


@njit(cache=True)
def _rt(x, tick):
    return math.floor(x / tick + 0.5) * tick


@njit(cache=True)
def _ri(x, tick):
    return math.floor(x / tick + 0.5)


@njit(cache=True)
def grid_run(o, h, l, c, pres, tickd, rv60, cen, allow, skw, P, out):
    """Счёт одной монеты. out — массив (NR, NOUT), обнуляется снаружи. Подробности исполнения — в docstring модуля и в протоколе §1/§2."""
    n = c.shape[0]
    g0 = P[0]
    a = P[1]
    use_cen = P[2] > 0.5
    b = P[3]
    timex = int(P[4])
    pause = int(P[5])
    t_start = int(P[6])
    maker = P[7]
    taker = P[8]
    slip = P[9]
    side_not = P[10]
    skew_coef = P[11]
    skew_mode = int(P[12])
    day0 = int(P[13])
    gmin = P[14]
    nlv = 10
    nk = np.zeros(nlv + 1)
    ssum = 0.0
    for k in range(1, nlv + 1):
        ssum += 1.0 + b * (k - 1)
    for k in range(1, nlv + 1):
        nk[k] = side_not * (1.0 + b * (k - 1)) / ssum
    # лоты: слот k = 1..10; long — вход-покупка на уровне k, выход-продажа на уровне k−1; short — зеркально
    lst = np.zeros(nlv + 1, np.int8)
    lent = np.zeros(nlv + 1)
    lq = np.zeros(nlv + 1)
    lop = np.full(nlv + 1, -1, np.int64)
    lfe = np.zeros(nlv + 1)
    lfr = np.full(nlv + 1, -1, np.int64)
    sst = np.zeros(nlv + 1, np.int8)
    sent = np.zeros(nlv + 1)
    sq = np.zeros(nlv + 1)
    sop = np.full(nlv + 1, -1, np.int64)
    sfe = np.zeros(nlv + 1)
    sfr = np.full(nlv + 1, -1, np.int64)
    qlb = np.zeros(nlv + 1)
    qle = np.zeros(nlv + 1)
    qsb = np.zeros(nlv + 1)
    qse = np.zeros(nlv + 1)
    qon = False
    have_q = False
    qclose = 0.0
    zq = 0.0
    gq = 0.0
    p0 = 0.0
    launched = False
    prev_on = False
    gabs = 0.0
    g_ok = False
    g_t = -1000000000
    pause_until = -1
    lastc = np.nan
    unreal_prev = 0.0
    nl = 0
    ns = 0
    for t in range(n):
        d = t // 1440
        do = d - day0
        ok = pres[t]
        if ok:
            lastc = c[t]
        if t < t_start or do < 0:
            continue
        tick = tickd[d]
        if ok and tick > 0.0:
            # ── A. исполнение котировок, выставленных на закрытии прошлой минуты
            if have_q and (qon or nl + ns > 0):
                lo_i = _ri(l[t], tick)
                hi_i = _ri(h[t], tick)
                qc_i = _ri(qclose, tick)
                for k in range(1, nlv + 1):
                    # long: вход
                    if lst[k] == 0 and qon and lfr[k] < t:
                        pi = _ri(qlb[k], tick)
                        if lo_i <= pi - 1:
                            p = qlb[k]
                            rate = maker
                            if pi - qc_i >= 1:  # выставлена выше рынка → тейкер по открытию
                                p = min(p, o[t] * (1.0 + slip))
                                rate = taker
                                out[8, do] += 1.0
                            qty = nk[k] / p
                            lst[k] = 1
                            lent[k] = p
                            lq[k] = qty
                            lop[k] = t
                            lfe[k] = qty * p * rate
                            nl += 1
                            out[0, do] -= lfe[k]
                            out[7, do] += 1.0
                    # long: выход
                    if lst[k] == 1 and lop[k] < t:
                        pi = _ri(qle[k], tick)
                        if hi_i >= pi + 1:
                            p = qle[k]
                            rate = maker
                            if qc_i - pi >= 1:
                                p = max(p, o[t] * (1.0 - slip))
                                rate = taker
                                out[8, do] += 1.0
                            gross = lq[k] * (p - lent[k])
                            fc = lq[k] * p * rate
                            out[0, do] += gross - fc
                            out[1, do] += gross - fc - lfe[k]
                            out[3, do] += 1.0
                            lst[k] = 0
                            lfr[k] = t
                            nl -= 1
                    # short: вход
                    if sst[k] == 0 and qon and sfr[k] < t:
                        pi = _ri(qsb[k], tick)
                        if hi_i >= pi + 1:
                            p = qsb[k]
                            rate = maker
                            if qc_i - pi >= 1:
                                p = max(p, o[t] * (1.0 - slip))
                                rate = taker
                                out[8, do] += 1.0
                            qty = nk[k] / p
                            sst[k] = 1
                            sent[k] = p
                            sq[k] = qty
                            sop[k] = t
                            sfe[k] = qty * p * rate
                            ns += 1
                            out[0, do] -= sfe[k]
                            out[7, do] += 1.0
                    # short: выход
                    if sst[k] == 1 and sop[k] < t:
                        pi = _ri(qse[k], tick)
                        if lo_i <= pi - 1:
                            p = qse[k]
                            rate = maker
                            if pi - qc_i >= 1:
                                p = min(p, o[t] * (1.0 + slip))
                                rate = taker
                                out[8, do] += 1.0
                            gross = sq[k] * (sent[k] - p)
                            fc = sq[k] * p * rate
                            out[0, do] += gross - fc
                            out[1, do] += gross - fc - sfe[k]
                            out[3, do] += 1.0
                            sst[k] = 0
                            sfr[k] = t
                            ns -= 1
            # ── B. закрытие минуты: выходы, центр, шаг, новая лестница
            cl = c[t]
            if have_q and nl + ns > 0 and (cl < zq - nlv * gq or cl > zq + nlv * gq):
                px_l = cl * (1.0 - slip)
                px_s = cl * (1.0 + slip)
                for k in range(1, nlv + 1):
                    if lst[k] == 1:
                        gross = lq[k] * (px_l - lent[k])
                        fc = lq[k] * px_l * taker
                        out[0, do] += gross - fc
                        out[2, do] += gross - fc - lfe[k]
                        lst[k] = 0
                        lfr[k] = t
                    if sst[k] == 1:
                        gross = sq[k] * (sent[k] - px_s)
                        fc = sq[k] * px_s * taker
                        out[0, do] += gross - fc
                        out[2, do] += gross - fc - sfe[k]
                        sst[k] = 0
                        sfr[k] = t
                nl = 0
                ns = 0
                out[4, do] += 1.0
                pause_until = t + pause
                if not use_cen:
                    p0 = cl
                    launched = True
                    g_ok = False
            if timex > 0 and nl + ns > 0:
                px_l = cl * (1.0 - slip)
                px_s = cl * (1.0 + slip)
                for k in range(1, nlv + 1):
                    if lst[k] == 1 and t - lop[k] >= timex:
                        gross = lq[k] * (px_l - lent[k])
                        fc = lq[k] * px_l * taker
                        out[0, do] += gross - fc
                        out[2, do] += gross - fc - lfe[k]
                        out[5, do] += 1.0
                        lst[k] = 0
                        lfr[k] = t
                        nl -= 1
                    if sst[k] == 1 and t - sop[k] >= timex:
                        gross = sq[k] * (sent[k] - px_s)
                        fc = sq[k] * px_s * taker
                        out[0, do] += gross - fc
                        out[2, do] += gross - fc - sfe[k]
                        out[5, do] += 1.0
                        sst[k] = 0
                        sfr[k] = t
                        ns -= 1
            on_raw = allow[t] != 0 and t >= pause_until
            center = np.nan
            if use_cen:
                center = cen[t]
            else:
                if (not launched) or (on_raw and (not prev_on) and nl + ns == 0):
                    p0 = cl
                    launched = True
                    g_ok = False
                center = p0
            if on_raw and np.isfinite(center) and (not g_ok or t - g_t >= 60):
                if a > 0.0:
                    s = rv60[t]
                    if np.isfinite(s) and s > 0.0:
                        gabs = max(a * s * center, gmin * tick)
                        g_ok = True
                        g_t = t
                else:
                    gabs = max(g0 * center, gmin * tick)
                    g_ok = True
                    g_t = t
            prev_on = on_raw
            if g_ok and np.isfinite(center):
                q = nl - ns
                z = center
                if use_cen:
                    if skew_mode == 0:
                        z = center - q * skew_coef * gabs
                    else:
                        z = center - q * skw[t]
                on = on_raw
                if z - nlv * gabs <= 2.0 * tick:
                    on = False
                if on:
                    out[6, do] = 1.0
                for k in range(1, nlv + 1):
                    qlb[k] = _rt(z - k * gabs, tick)
                    qle[k] = _rt(z - (k - 1) * gabs, tick)
                    qsb[k] = _rt(z + k * gabs, tick)
                    qse[k] = _rt(z + (k - 1) * gabs, tick)
                qon = on
                zq = z
                gq = gabs
                qclose = cl
                have_q = True
            else:
                qon = False
        # ── сутки закрыты: оценка открытого по последнему закрытию
        if t % 1440 == 1439:
            u = 0.0
            if nl + ns > 0 and np.isfinite(lastc):
                for k in range(1, nlv + 1):
                    if lst[k] == 1:
                        u += lq[k] * (lastc - lent[k])
                    if sst[k] == 1:
                        u += sq[k] * (sent[k] - lastc)
            out[0, do] += u - unreal_prev
            unreal_prev = u
    return out


def run_coin(S: dict, F: dict, P: np.ndarray, cen=None, allow=None, skw=None) -> np.ndarray:
    """Обёртка: массивы признаков F (rv60, tick) → вывод ядра (NR, NOUT)."""
    n = len(S["c"])
    out = np.zeros((NR, n // 1440 - int(P[13])))
    zeros = np.zeros(n)
    grid_run(S["o"], S["h"], S["l"], S["c"], S["pres"], F["tick"], F["rv60"],
             zeros if cen is None else cen, np.ones(n, np.uint8) if allow is None else allow, zeros if skw is None else skw, P, out)
    return out


# ───────────────────────────── статистика ─────────────────────────────

def block_means(x: np.ndarray, block: int, nboot: int | None = None, seed: int = SEED) -> np.ndarray:
    nboot = NBOOT if nboot is None else nboot
    rng = np.random.default_rng(seed)
    n = len(x)
    nb = math.ceil(n / block)
    starts = rng.integers(0, n, size=(nboot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    idx = idx.reshape(nboot, nb * block)[:, :n]
    return x[idx].mean(axis=1)


def stat_block(x, block: int, nboot: int | None = None, seed: int = SEED) -> dict:
    """Среднее суточного ряда и 95 % перцентильный интервал кругового блочного бутстрепа (блок в сутках)."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    m = float(x.mean())
    bm = block_means(x, block, nboot, seed)
    lo, hi = np.percentile(bm, [2.5, 97.5])
    return dict(mean=m, se=float(bm.std(ddof=1)), lo=float(lo), hi=float(hi), n=len(x), block=block)


def sharpe_ann(x) -> float:
    """Годовой Шарп суточного ряда: среднее / σ(ddof=1) · √365 (крипто — 365 суток)."""
    x = np.asarray(x, float)
    s = x.std(ddof=1)
    return float(x.mean() / s * math.sqrt(365.0)) if s > 0 else float("nan")


def drawdown(x) -> dict:
    """Просадка и перехай по накопленному суточному Σ (старт 0). dd — макс. (пик − накоплено); перехай — сутки между новыми максимумами (строго выше): самый длинный, средний, p90 (с открытым последним интервалом)."""
    x = np.asarray(x, float)
    cum = np.cumsum(x)
    peak = 0.0
    dd = 0.0
    dd_end = -1
    last = -1  # индекс последнего максимума (−1 — старт)
    gaps = []
    for i, v in enumerate(cum):
        if v > peak:
            gaps.append(i - last)
            last = i
            peak = v
        if peak - v > dd:
            dd = peak - v
            dd_end = i
    gaps.append(len(cum) - 1 - last) if len(cum) - 1 > last else None
    g = np.array(gaps if gaps else [0], float)
    return dict(dd=float(dd), dd_end=int(dd_end), hw_max=float(g.max()), hw_mean=float(g.mean()), hw_p90=float(np.percentile(g, 90)), n_high=len(gaps))
