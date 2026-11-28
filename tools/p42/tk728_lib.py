#!/usr/bin/env python3
"""П-42 этап A, шаг 4 (TK-728, §13.78.4 = PS-1057 + поправки §13.78.7 = PS-1060): общие функции чистки признаков и меток. Только numpy / scipy — тесты идут и на ПК.

  Константы протокола (одно зерно этапа — `SEED`, порог копий — `RHO_THR`, выборка 4б — `N_SAMPLE`, 2 000 строк теста на заглядывание — `N_CAUSAL`);
  4б: `rank_columns`, `spearman_pairwise`, `cluster_average`, `pick_rep`, `window_minutes`;
  4в: `block_idx`, `ratio_boot`, `terciles`, `kappa`;
  4г: `mh_or`, `mh_or_ci`, `mh_perm_p`, `overdispersion`, `mom_beta_binomial`, `shrunk_share`;
  4а: `causal_rank` (причинный ранг строки — среди строк с более ранним сигналом, не меньше `MIN_CAUSAL` строк).
"""
from __future__ import annotations

import math
import re

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from scipy.stats import rankdata

SEED = 20261127                 # §13.78 п. 11: одно зерно на весь этап A
RHO_THR = 0.7                   # 4б: |ρ| ≥ 0,7 — копия (до счёта)
N_SAMPLE = 200_000              # 4б: строк таблицы подходов
N_CAUSAL = 2_000                # 4а: строк теста на заглядывание
N_PERM = 10_000                 # 4г: перестановок внутри слоя
N_BOOT = 20_000                 # блочный бутстреп (общие правила, п. 1)
MIN_CAUSAL = 500                # 4а: причинный ранг — не меньше 500 более ранних строк
MIN_PAIRS = 1_000               # 4б: пара признаков с меньшим числом общих строк — расстояние 1 (не копия)


# ───────────────────────────── список признаков ─────────────────────────────

# 4е: признаки времени посчитаны `tk671_time.py`, в список не внесены — вносятся (имя, что значит ≤ 12 слов); номера — следующие за последним
TIME_FEATURES = [
    ("t_hour", "Час суток по UTC в момент сигнала, 0–23"),
    ("t_wday", "День недели по UTC в момент сигнала, 0 — понедельник"),
    ("t_to_fund_min", "Минут до ближайшего следующего фандинга, до 480"),
    ("t_sess_asia", "Сигнал в азиатскую сессию, 00–08 по UTC, 0 / 1"),
    ("t_sess_eu", "Сигнал в европейскую сессию, 07–16 по UTC, 0 / 1"),
    ("t_sess_us", "Сигнал в американскую сессию, 13–21 по UTC, 0 / 1"),
]
ALIASES = {"AGE": "age_d", "CLS": "cls"}      # признак списка → столбец таблицы с другим именем (одно значение, `tk671_coin.py`)
HISTORY17 = ["bk20", "bk30", "bk40", "bk60", "ok10_d3", "ok10_d5", "ok10_d7", "ok10_d14", "ok10_d30", "ok05_d14",
             "xok10_d3", "xok10_d5", "xok10_d7", "xok10_d14", "xok10_d30", "hist_own", "hist_cross"]
OI_LS10 = ["oi_c1", "oi_c4", "oi_c24", "loi_turn", "ls_tc", "ls_ts", "ls_a", "dls_tc", "dls_ts", "dls_a"]
LOOKAHEAD_4A = ["IMP", "FLOW", "COUP_15m", "COUP_1h", "COUP_4h", "COUP_24h"]
BK_LABEL = ["bk20", "bk40", "bk60", "bk30"]


def feature_table(path):
    """Признаки `ml_features.json` по порядку номеров + признаки времени (4е) с номерами после последнего. Возвращает список dict: n, name, set, what, new."""
    import json
    d = json.load(open(path, encoding="utf-8"))
    rows = []
    for st in d["sets"]:
        for g in st["groups"]:
            for f in g["features"]:
                rows.append(dict(n=f["n"], name=f["name"], set=st["id"], what=f["what"], new=False))
    have = {r["name"] for r in rows}
    top = max(r["n"] for r in rows)
    for i, (name, what) in enumerate(TIME_FEATURES, 1):
        if name not in have:
            rows.append(dict(n=top + i, name=name, set="time", what=what, new=True))
    return sorted(rows, key=lambda r: r["n"])


# ───────────────────────────── 4б: корреляции и кластеры ─────────────────────────────

def rank_columns(X: np.ndarray) -> np.ndarray:
    """Средние ранги по столбцам среди непустых значений; пустые остаются NaN."""
    X = np.asarray(X, dtype=np.float64)
    R = np.full(X.shape, np.nan)
    for j in range(X.shape[1]):
        ok = np.isfinite(X[:, j])
        if ok.any():
            R[ok, j] = rankdata(X[ok, j], method="average")
    return R


def spearman_pairwise(X: np.ndarray, min_pairs: int = MIN_PAIRS):
    """Спирмен: Пирсон по рангам столбцов (ранги — среди непустых значений столбца), попарно по общим непустым строкам. Возвращает (ρ, число общих строк).
    Столбец-константа или пара с меньше `min_pairs` общих строк — ρ = NaN. Диагональ — 1 у столбцов с разбросом."""
    R = rank_columns(X)
    M = np.isfinite(R)
    mean = np.where(M.any(axis=0), np.nanmean(np.where(M, R, np.nan), axis=0), 0.0)
    Z = np.where(M, R - mean[None, :], 0.0)
    Mf = M.astype(np.float64)
    n = Mf.T @ Mf                                   # общих строк у пары (i, j)
    S = Z.T @ Mf                                    # S[i, j] = Σ z_i по строкам, где непусты и i, и j
    Q = (Z * Z).T @ Mf                              # Σ z_i² по тем же строкам
    C = Z.T @ Z                                     # Σ z_i z_j
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = C / n - (S / n) * (S.T / n)
        vi = Q / n - (S / n) ** 2
        vj = vi.T
        rho = cov / np.sqrt(vi * vj)
    rho = np.where((n >= min_pairs) & np.isfinite(rho), np.clip(rho, -1.0, 1.0), np.nan)
    return rho, n.astype(np.int64)


def cluster_average(rho: np.ndarray, thr: float = RHO_THR) -> np.ndarray:
    """Иерархическая кластеризация, средняя связь, расстояние 1 − |ρ| (NaN → 1); разрез на расстоянии 1 − thr: внутри кластера средняя |ρ| связи при слиянии ≥ thr.
    Возвращает номера кластеров 1…K по порядку первого члена."""
    k = rho.shape[0]
    if k == 1:
        return np.array([1])
    D = 1.0 - np.abs(np.where(np.isfinite(rho), rho, 0.0))
    D = (D + D.T) / 2.0
    np.fill_diagonal(D, 0.0)
    Z = linkage(squareform(D, checks=False), method="average")
    lab = fcluster(Z, t=1.0 - thr + 1e-12, criterion="distance")
    # перенумеровать по порядку первого появления — устойчивые номера
    order, out = {}, np.empty(k, dtype=np.int64)
    for i, c in enumerate(lab):
        if c not in order:
            order[c] = len(order) + 1
        out[i] = order[c]
    return out


def pick_rep(members, cnt, win, num):
    """Представитель кластера: наибольший охват (число непустых строк); при равенстве — самое короткое окно; затем меньший номер признака.
    Возвращает (индекс члена, уровень решения: 'охват' | 'окно' | 'номер' | 'один')."""
    members = list(members)
    if len(members) == 1:
        return members[0], "один"
    best = max(cnt[i] for i in members)
    c1 = [i for i in members if cnt[i] == best]
    if len(c1) == 1:
        return c1[0], "охват"
    wmin = min(win[i] for i in c1)
    c2 = [i for i in c1 if win[i] == wmin]
    if len(c2) == 1:
        return c2[0], "окно"
    return min(c2, key=lambda i: num[i]), "номер"


_UNITS = [(r"_(\d+)m$", 1.0), (r"_(\d+)h$", 60.0), (r"_(\d+)d$", 1440.0), (r"_d(\d+)$", 1440.0)]
_SECONDS_PREFIX = ("tfv", "tfn", "tfp", "big", "bigimb", "sac", "lmsz", "vpin", "accn", "accv", "accdn", "accdv")


def window_minutes(name: str) -> float:
    """Окно признака, минут, из имени (договорные разборы имён; неизвестное — inf, решает номер). Правила:
    `_<k>m|h|d` — как записано (последнее число: c5_btc_7d → 7 суток); `_d<k>` — суток; `mk_<btc|eth|own|rel>_s<k>`, `mk_rv5_*_<k>`, `mk_corr5_<k>`, `n/m/v<k>` — минут;
    `n24` / `m24` — 24 ч; `tfv|tfn|tfp|big|bigimb|sac|lmsz|vpin<k>`, `mk<a>_<k>`, `acc*<s>_<P>` — секунд (берётся последнее число — длинное окно); `kyle<k>`, `amih<k>` — минут;
    `vpin1h|6h`, `kyle1m|5m` — как записано; `mv|tpm|cons|flat|maxrng|size|sw_*<k>` — минут, `pm_*<k>` и `<имя><k>h` — часов, `natr_bp` — 30 мин, `lturn24` — 24 ч."""
    for pat, mult in _UNITS:
        m = re.search(pat, name)
        if m:
            return float(m.group(1)) * mult
    if name == "natr_bp":
        return 30.0                                      # V — NATR за 30 минут
    if name == "lturn24":
        return 1440.0
    m = re.fullmatch(r"(?:btc_|eth_)?mv(\d+)|tpm(\d+)|cons(\d+)|flat(\d+)|maxrng(\d+)|size(\d+)|sw_[da](\d+)", name)
    if m:
        return float(next(g for g in m.groups() if g))
    m = re.fullmatch(r"pm_[pn](\d+)", name)
    if m:
        return float(m.group(1)) * 60.0
    m = re.fullmatch(r"[a-z]+(\d+)h", name)
    if m and not name.startswith("vpin"):
        return float(m.group(1)) * 60.0
    m = re.fullmatch(r"mk_(?:btc|eth|own|rel)_s(\d+)", name)
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"mk_(?:rv5|corr5)_(?:btc_|own_)?(\d+)", name)
    if m:
        return float(m.group(1))
    if name in ("n24", "m24"):
        return 1440.0
    m = re.fullmatch(r"[nmv](\d+)", name)
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"(kyle|amih)(\d+)(m?)", name)
    if m:
        return float(m.group(2))
    m = re.fullmatch(r"vpin(\d+)h", name)
    if m:
        return float(m.group(1)) * 60.0
    m = re.fullmatch(r"mk\d+_(\d+)", name)
    if m:
        return float(m.group(1)) / 60.0
    m = re.fullmatch(r"(?:acc(?:n|v|dn|dv))\d+_(\d+)", name)
    if m:
        return float(m.group(1)) / 60.0
    m = re.fullmatch(r"([a-z]+)(\d+)", name)
    if m and m.group(1) in _SECONDS_PREFIX:
        return float(m.group(2)) / 60.0
    return math.inf


# ───────────────────────────── 4в: метки, блочный бутстреп ─────────────────────────────

def block_idx(n: int, rng, nboot: int, block: int) -> np.ndarray:
    """Индексы кругового блочного пересэмплинга (та же схема, что `lib.block_boot_means`): (nboot, n)."""
    nb = math.ceil(n / block)
    starts = rng.integers(0, n, size=(nboot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return idx.reshape(nboot, nb * block)[:, :n]


def block_len(n_days: int) -> int:
    """⌈n^{1/3}⌉ суток (общие правила, п. 1)."""
    return max(1, math.ceil(round(n_days ** (1.0 / 3.0), 9)))


def ratio_boot(num: np.ndarray, den: np.ndarray, seed: int, block: int, nboot: int = N_BOOT):
    """Доля Σnum / Σden по суткам; интервал — блочный бутстреп по суткам (пересэмплинг суток целиком). Возвращает (оценка, нижняя, верхняя границы 95 %)."""
    num = np.asarray(num, dtype=np.float64)
    den = np.asarray(den, dtype=np.float64)
    rng = np.random.default_rng(seed)
    idx = block_idx(len(num), rng, nboot, block)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = num[idx].sum(axis=1) / den[idx].sum(axis=1)
    r = r[np.isfinite(r)]
    est = float(num.sum() / den.sum()) if den.sum() > 0 else float("nan")
    if len(r) == 0:
        return est, float("nan"), float("nan")
    lo, hi = np.percentile(r, [2.5, 97.5])
    return est, float(lo), float(hi)


def terciles(v: np.ndarray):
    """Границы третей (33⅓ и 66⅔ процентилей) и номер трети 0 / 1 / 2 каждого значения: нижняя — v ≤ q1, верхняя — v > q2."""
    v = np.asarray(v, dtype=np.float64)
    q1, q2 = np.nanpercentile(v, [100.0 / 3.0, 200.0 / 3.0])
    return (float(q1), float(q2)), np.where(v <= q1, 0, np.where(v <= q2, 1, 2)).astype(np.int8)


def kappa(a: np.ndarray, b: np.ndarray) -> float:
    """Каппа Коэна двух бинарных меток (0 / 1)."""
    a, b = np.asarray(a, dtype=bool), np.asarray(b, dtype=bool)
    n = len(a)
    po = float((a == b).mean())
    pe = float(a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean()))
    return (po - pe) / (1 - pe) if pe < 1 else float("nan")


# ───────────────────────────── 4г: память исходов ─────────────────────────────

def mh_cells(prev, cur, strat, nstrat):
    """Числа a, b, c, d по слоям: a = (prev=1, cur=1), b = (1, 0), c = (0, 1), d = (0, 0)."""
    prev = np.asarray(prev, dtype=bool)
    cur = np.asarray(cur, dtype=bool)
    a = np.bincount(strat[prev & cur], minlength=nstrat).astype(np.float64)
    b = np.bincount(strat[prev & ~cur], minlength=nstrat).astype(np.float64)
    c = np.bincount(strat[~prev & cur], minlength=nstrat).astype(np.float64)
    d = np.bincount(strat[~prev & ~cur], minlength=nstrat).astype(np.float64)
    return a, b, c, d


def mh_or(a, b, c, d) -> float:
    """Объединённое отношение шансов Мантеля–Хензеля: Σ a·d/n / Σ b·c/n."""
    n = a + b + c + d
    with np.errstate(invalid="ignore", divide="ignore"):
        num = np.where(n > 0, a * d / n, 0.0).sum()
        den = np.where(n > 0, b * c / n, 0.0).sum()
    return float(num / den) if den > 0 else (math.inf if num > 0 else float("nan"))


def mh_or_ci(a, b, c, d, z: float = 1.959963984540054):
    """Интервал ln OR по формуле Робинса–Бреслоу–Гринленда (PS-1060 п. 14). Возвращает (OR, нижняя, верхняя, SE ln OR)."""
    n = a + b + c + d
    ok = n > 0
    a, b, c, d, n = a[ok], b[ok], c[ok], d[ok], n[ok]
    R = (a * d / n).sum()
    S = (b * c / n).sum()
    if R <= 0 or S <= 0:
        return (float(R / S) if S > 0 else math.inf), float("nan"), float("nan"), float("nan")
    P = (a + d) / n
    Q = (b + c) / n
    PR = (P * a * d / n).sum()
    PS_QR = (P * b * c / n + Q * a * d / n).sum()
    QS = (Q * b * c / n).sum()
    var = PR / (2 * R * R) + PS_QR / (2 * R * S) + QS / (2 * S * S)
    se = math.sqrt(var)
    orr = R / S
    return float(orr), float(math.exp(math.log(orr) - z * se)), float(math.exp(math.log(orr) + z * se)), float(se)


def mh_perm_p(lab, prev_idx, cur_idx, strat_pair, strat_ev, nstrat, orr_obs, nperm, seed):
    """Перестановки внутри слоя. `lab` — исходы событий (0 / 1), события лежат непрерывно по слоям (`strat_ev` не убывает); пары (prev_idx[i] → cur_idx[i]) внутри одного слоя `strat_pair`.
    На каждом повторе исходы событий слоя перемешиваются, пары пересобираются с теми же индексами; статистика — OR Мантеля–Хензеля.
    Возвращает (p односторонний «память положительна»: OR* ≥ OR, p двусторонний по |ln OR|, массив OR*)."""
    rng = np.random.default_rng(seed)
    lab = np.asarray(lab, dtype=bool)
    n = len(lab)
    out = np.empty(nperm)
    for k in range(nperm):
        order = np.lexsort((rng.random(n), strat_ev))
        L = lab[order]
        a, b, c, d = mh_cells(L[prev_idx], L[cur_idx], strat_pair, nstrat)
        out[k] = mh_or(a, b, c, d)
    fin = np.isfinite(out)
    with np.errstate(invalid="ignore", divide="ignore"):
        ge = (out >= orr_obs - 1e-12) & fin
        two = (np.abs(np.log(out)) >= abs(math.log(orr_obs)) - 1e-12) & fin
    p1 = (1 + int(ge.sum())) / (1 + nperm)
    p2 = (1 + int(two.sum())) / (1 + nperm)
    return p1, p2, out


def overdispersion(k, n):
    """Разброс долей успеха между монетами против биномиального: χ² = Σ n_i (p_i − p̄)² / (p̄ (1 − p̄)) при k − 1 степенях свободы; отношение χ²/(k−1) и
    внутриклассовая корреляция бета-биномиала по методу моментов ρ = (χ²/(k−1) − 1) / (n̄ − 1) (≥ 0 — монеты различаются сильнее биномиального)."""
    k = np.asarray(k, dtype=np.float64)
    n = np.asarray(n, dtype=np.float64)
    ok = n > 0
    k, n = k[ok], n[ok]
    pbar = k.sum() / n.sum()
    p = k / n
    chi2 = float((n * (p - pbar) ** 2).sum() / (pbar * (1 - pbar)))
    df = len(k) - 1
    nbar = float(n.mean())
    ratio = chi2 / df if df > 0 else float("nan")
    rho = (ratio - 1.0) / (nbar - 1.0) if nbar > 1 else float("nan")
    return dict(chi2=chi2, df=df, ratio=float(ratio), rho_mom=float(rho), p_bar=float(pbar), n_coins=int(len(k)), n_bar=nbar)


def mom_beta_binomial(k, n):
    """Метод моментов бета-биномиала по (k_i успехов из n_i): p0 = Σk/Σn; ρ — из `overdispersion`; m = (1 − ρ)/ρ при ρ > 0, иначе inf (усадка до рыночной доли)."""
    od = overdispersion(k, n)
    r = od["rho_mom"]
    m = (1.0 - r) / r if r > 0 else math.inf
    return dict(p0=od["p_bar"], rho=r, m=float(m))


def shrunk_share(k, n, p0, m):
    """p̂ = (k + m·p0)/(n + m); n = k = 0 → p0."""
    k = np.asarray(k, dtype=np.float64)
    n = np.asarray(n, dtype=np.float64)
    if math.isinf(m):
        return np.full(np.broadcast(k, n).shape, float(p0))
    return (k + m * p0) / (n + m)


# ───────────────────────────── 4а: причинный ранг ─────────────────────────────

def causal_rank(t, x, min_prior: int = MIN_CAUSAL):
    """Причинный процентный ранг: у строки i — доля строк с более ранним сигналом (t_j < t_i), у которых x_j < x_i (+ ½ равных), среди определённых; строк меньше `min_prior` — NaN.
    Строки с одинаковым временем не видят друг друга. Не использует строки позже t_i. Счёт — дерево Фенвика по сжатым значениям."""
    t = np.asarray(t)
    x = np.asarray(x, dtype=np.float64)
    out = np.full(len(t), np.nan)
    fin = np.isfinite(x)
    if not fin.any():
        return out
    _, comp = np.unique(x[fin], return_inverse=True)
    rk = np.full(len(x), -1, dtype=np.int64)
    rk[fin] = comp
    m = int(comp.max()) + 1
    tree = [0] * (m + 1)
    cnt = [0] * m

    def add(i):
        cnt[i] += 1
        i += 1
        while i <= m:
            tree[i] += 1
            i += i & -i

    def prefix(i):                      # число значений с рангом < i
        s_ = 0
        while i > 0:
            s_ += tree[i]
            i -= i & -i
        return s_

    order = np.argsort(t, kind="stable")
    ts = t[order]
    n = len(ts)
    total = 0
    i = 0
    while i < n:
        j = i
        while j < n and ts[j] == ts[i]:
            j += 1
        for k in range(i, j):
            r = rk[order[k]]
            if r >= 0 and total >= min_prior:
                lo = prefix(r)
                out[order[k]] = (lo + 0.5 * cnt[r]) / total
        for k in range(i, j):
            r = rk[order[k]]
            if r >= 0:
                add(r)
                total += 1
        i = j
    return out
