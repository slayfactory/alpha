"""TK-735 (П-42 шаг 9в, §13.79): общие функции — сделки базы, минутки c539 с оборотом / числом сделок, признаки φ1…φ11, свойства P1…P3, k-means, пространство правил.

Протокол — `chron.py protocol show P-42`, §13.79, §13.79.1–§13.79.6 (PS-1073…1079); читаемая копия — docs/research/p42-step9v-protocol-draft-2026-11-28.md.
Время: закрытая минута tc = m − 1 (m — минута сигнала на сетке G0, как `natr[sig_ms // 60000 − 1]` движка); все признаки — только по минутам ≤ tc.
Сутки — от 01.01.2026 (`eday`); режимы — FJ [31, 181), SEL [181, 317), январь [0, 31), N1 [317, 328).
"""
from __future__ import annotations

import csv
import datetime
import itertools
import json
import math
import os

import numpy as np

import tk727_lib as L
import tk727_s5 as S5

ROOT = "/data/p42-tk735"
DB = ROOT + "/tk735.sqlite"
SEED = 20261127
SEED_NULL = 20262127
SEED_PLACEBO = 20264127
NSPLIT = 100
N_FLOOR = 10
MIN_DAYS_LF = 5
FRAC_L40 = 0.4
FAM = S5.FAM
ANCHOR = 7180023
REG = {"FJ": (31, 181), "SEL": (181, 317)}          # [от, до) сутки от 01.01.2026
FORM_DAY = {"FJ": 31, "SEL": 181}                    # день формирования свойств — первый день режима (00:00 UTC)
TARGETS = {"jan": (0, 31), "n1": (317, 328)}
KCOLS = (0, 1, 2, 3, 4, 7, 8, 10)                    # open_time, o, h, l, c, quote_volume, count, taker_buy_quote_volume
KL_HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"
PCTS = (50, 67, 80, 90)
# признаки списка (φ1…φ11), §13.79.2; φ3 — категория (6 четырёхчасовых корзин UTC)
PHI = ("V", "T24", "HOUR", "SURGE", "SIZE", "SELL", "WEAK", "CALM", "ROUND", "NTR", "TURN1")
NPHI = len(PHI)
HOUR_J = PHI.index("HOUR")
# условия: (признак, индекс порога / корзина); непрерывные — 4 порога (≥), φ3 — 6 значений (=)
CONDS = []
for _j in range(NPHI):
    for _k in range(6 if _j == HOUR_J else 4):
        CONDS.append((_j, _k))
NCOND = len(CONDS)                                    # 46
COND_FEAT = np.array([c[0] for c in CONDS], np.int64)
EXPECT_RULES = 13007


def day_of(iso: str) -> int:
    return (datetime.date.fromisoformat(iso) - datetime.date(2026, 1, 1)).days


def day_str(d: int) -> str:
    return (datetime.date(2026, 1, 1) + datetime.timedelta(days=int(d))).isoformat()


def read_syms() -> list:
    return S5.read_syms()


# ───────────────────────────── сделки ─────────────────────────────

def read_trades_file(path: str, t0_ms: int) -> dict:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    a = dict(sym=np.array([r["sym"] for r in rows], dtype="U24"), kind=np.array([r["kind"] for r in rows], dtype="U12"))
    for k in ("gross", "fees", "funding"):
        a[k] = np.array([float(r[k]) for r in rows], np.float64)
    for k in ("entry_px_e8", "sig_ms", "entry_ms", "exit_ms"):
        a[k] = np.array([int(r[k]) for r in rows], np.int64)
    for k in ("sig_ms", "entry_ms", "exit_ms"):
        a[k] = a[k] + int(t0_ms)
    a["pnl"] = a["gross"] - a["fees"]                      # r = gross − fees (§13.79, L-477)
    a["eday"] = (a["entry_ms"] - S5.EPOCH0) // L.DAY_MS
    return a


def load_trades_all(cids=FAM) -> dict:
    """Сделки клеток во всех окнах (m01…m06, wa, wb, wc, n1), зачётные сутки окон как у TK-726 / TK-727; ключи массивов — см. `keys`."""
    parts = []
    for wi, w in enumerate(S5.WIN_NAMES):
        lo, hi, root = S5.WINDOWS[w]
        t0 = json.load(open(os.path.join(root, "t0.json"), encoding="utf-8"))[w]
        for cid in cids:
            p = os.path.join(root, w, "%d-trades.csv" % cid)
            if not os.path.exists(p):
                continue
            a = read_trades_file(p, t0)
            keep = (a["eday"] >= S5.day_of(lo)) & (a["eday"] <= S5.day_of(hi))
            for k in list(a):
                a[k] = a[k][keep]
            n = int(keep.sum())
            a["cid"] = np.full(n, cid, np.int64)
            a["win"] = np.full(n, wi, np.int8)
            parts.append(a)
    keys = ("cid", "win", "sym", "kind", "sig_ms", "entry_ms", "exit_ms", "entry_px_e8", "pnl", "gross", "fees", "funding", "eday")
    return {k: np.concatenate([p[k] for p in parts]) for k in keys}


def dedupe(tr: dict, cids=FAM):
    """Уникальные сделки семейства (L-477, `tk727_s5_an.dedupe_family`): индексы первых вхождений (монета, сигнал, вход, выход, результат)."""
    idx = np.where(np.isin(tr["cid"], cids))[0]
    seen, keep = set(), []
    for i in idx:
        k = (str(tr["sym"][i]), int(tr["sig_ms"][i]), int(tr["entry_ms"][i]), int(tr["exit_ms"][i]), round(float(tr["pnl"][i]), 6))
        if k in seen:
            continue
        seen.add(k)
        keep.append(i)
    return np.array(keep, np.int64), int(len(idx)), int(len(idx) - len(keep))


def unique_trades(cids=FAM) -> dict:
    """Уникальные сделки базы (все окна): массивы строк-первых вхождений + `regime` (FJ / SEL / jan / n1), `in_anchor` — сделка есть и в клетке якоря s4 (7180023), `tc` — закрытая минута перед сигналом на сетке G0, `m` — минута сигнала."""
    tr = load_trades_all(cids)
    idx, n_raw, n_dup = dedupe(tr, cids)
    u = {k: v[idx] for k, v in tr.items()}
    anchor = set()
    for i in np.where(tr["cid"] == ANCHOR)[0]:
        anchor.add((str(tr["sym"][i]), int(tr["sig_ms"][i]), int(tr["entry_ms"][i]), int(tr["exit_ms"][i]), round(float(tr["pnl"][i]), 6)))
    u["in_anchor"] = np.array([(str(u["sym"][i]), int(u["sig_ms"][i]), int(u["entry_ms"][i]), int(u["exit_ms"][i]), round(float(u["pnl"][i]), 6)) in anchor for i in range(len(idx))], bool)
    u["regime"] = regime_of_day(u["eday"])
    u["m"] = (u["sig_ms"] - L.G0_MS) // L.MIN_MS
    u["tc"] = u["m"] - 1
    u["n_raw"], u["n_dup"] = n_raw, n_dup
    return u


def regime_of_day(eday):
    """FJ / SEL / jan / n1 / '' по суткам входа."""
    out = np.full(len(eday), "", dtype="U4")
    for name, (lo, hi) in list(REG.items()) + list(TARGETS.items()):
        out[(eday >= lo) & (eday < hi)] = name
    return out


# ───────────────────────────── минутки ─────────────────────────────

def load_kl(sym: str, ndays: int = L.NDAYS) -> dict:
    """Минутки монеты на сетке G0: o, h, l, c (NaN — минуты нет), qv (оборот, $), cnt (число сделок), tbq (оборот агрессивных покупок, $), pres. Источник дня — первое окно L.WINS, где он есть."""
    n = ndays * 1440
    o, h, l, c = (np.full(n, np.nan) for _ in range(4))
    qv, cnt, tbq = (np.zeros(n) for _ in range(3))
    pres = np.zeros(n, bool)
    for di in range(ndays):
        p = L.csv_path(sym, di)
        if p is None:
            continue
        a = np.loadtxt(p, delimiter=",", skiprows=1, usecols=KCOLS, ndmin=2)
        if a.size == 0:
            continue
        k = ((a[:, 0] - L.G0_MS) // L.MIN_MS).astype(np.int64)
        ok = (k >= 0) & (k < n)
        k, a = k[ok], a[ok]
        o[k], h[k], l[k], c[k], qv[k], cnt[k], tbq[k] = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], a[:, 6], a[:, 7]
        pres[k] = True
    return dict(o=o, h=h, l=l, c=c, qv=qv, cnt=cnt, tbq=tbq, pres=pres)


def _at(a: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """a[idx], NaN где idx вне массива."""
    idx = np.asarray(idx, np.int64)
    ok = (idx >= 0) & (idx < len(a))
    out = np.full(len(idx), np.nan)
    out[ok] = a[idx[ok]]
    return out


def feat_at(S: dict, tc) -> dict:
    """Признаки на закрытых минутах tc (индексы сетки G0), только по минутам ≤ tc (§13.79.2). NaN — признака нет → условие не выполнено (§13 п. 9 (6)).
    Ключи: V (RV60, bp), T24 (log10 оборота 24 ч), SURGE, SIZE ($), SELL, CALM (V / range60), ROUND (−d, bp), NTR (log10 числа сделок 60 мин), TURN1 (log10 оборота 60 мин), r60 (ln c[t]/c[t−60], bp; для WEAK)."""
    tc = np.asarray(tc, np.int64)
    n = len(S["c"])
    pres, c = S["pres"], S["c"]
    inb = (tc >= 0) & (tc < n)
    tt = np.clip(tc, 0, max(n - 1, 0))
    pr = np.where(inb, pres[tt], False)
    ct = np.where(pr, c[tt], np.nan)
    rv = L.rv_sqrt_sum(S, 60)
    V = np.where(inb, rv[tt], np.nan) * 1e4
    q60, q1440 = L._wsum(S["qv"], 60), L._wsum(S["qv"], 1440)
    n60 = L._wsum(S["cnt"], 60)
    t15 = L._wsum(S["tbq"], 15)
    q15 = L._wsum(S["qv"], 15)
    with np.errstate(invalid="ignore", divide="ignore"):
        Q1 = np.where(inb, _at(q60, tc), np.nan)
        Q24 = _at(q1440, tc)
        T24 = np.where(Q24 > 0, np.log10(Q24), np.nan)
        Qp = _at(q1440, tc - 60)                       # 1 440 закрытых минут перед последним часом
        SURGE = np.where(Qp > 0, Q1 / (Qp / 24.0), np.nan)
        N1 = _at(n60, tc)
        SIZE = np.where(N1 > 0, Q1 / N1, np.nan)
        Q15, TB15 = _at(q15, tc), _at(t15, tc)
        SELL = np.where(Q15 > 0, 1.0 - TB15 / Q15, np.nan)
        NTR = np.where(N1 > 0, np.log10(N1), np.nan)
        TURN1 = np.where(Q1 > 0, np.log10(Q1), np.nan)
    # размах 60 закрытых минут (≥ ½ окна присутствует)
    rng_bp = np.full(len(tc), np.nan)
    ok = pr & (tc >= 59)
    if ok.any():
        from numpy.lib.stride_tricks import sliding_window_view

        ii = tc[ok]
        hw = sliding_window_view(S["h"], 60)[ii - 59]
        lw = sliding_window_view(S["l"], 60)[ii - 59]
        cnt = np.isfinite(hw).sum(axis=1)
        with np.errstate(all="ignore"):
            hi, lo = np.nanmax(hw, axis=1), np.nanmin(lw, axis=1)
        r = (hi - lo) / ct[ok] * 1e4
        r = np.where(cnt * 2 >= 60, r, np.nan)
        rng_bp[ok] = r
    with np.errstate(invalid="ignore", divide="ignore"):
        CALM = np.where(rng_bp > 0, V / rng_bp, np.nan)
        g = 10.0 ** (np.floor(np.log10(ct)) - 1.0)
        m = np.fmod(ct, g)
        d = 1e4 * np.minimum(m, g - m) / ct
        ROUND = np.where(pr, -d, np.nan)
        c0 = _at(c, tc - 60)
        p0 = _at(pres.astype(np.float64), tc - 60) > 0
        r60 = np.where(pr & p0, np.log(ct / c0) * 1e4, np.nan)
    return dict(V=V, T24=T24, SURGE=SURGE, SIZE=SIZE, SELL=SELL, CALM=CALM, ROUND=ROUND, NTR=NTR, TURN1=TURN1, r60=r60)


def r60_at(S: dict, tc) -> np.ndarray:
    """ln(c[tc] / c[tc−60]) в bp; NaN, если нет любой из двух минут (та же формула, что `r60` в `feat_at`)."""
    tc = np.asarray(tc, np.int64)
    n = len(S["c"])
    ok = (tc >= 60) & (tc < n)
    tt = np.clip(tc, 0, n - 1)
    pr = ok & S["pres"][tt] & S["pres"][np.clip(tc - 60, 0, n - 1)]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(pr, np.log(S["c"][tt] / S["c"][np.clip(tc - 60, 0, n - 1)]) * 1e4, np.nan)


def hour_bucket(m) -> np.ndarray:
    """φ3: четырёхчасовая корзина часа UTC минуты сигнала m (индекс сетки G0)."""
    return ((np.asarray(m, np.int64) * L.MIN_MS + L.G0_MS) // 3_600_000 % 24 // 4).astype(np.float64)


def feature_matrix(f: dict, hour: np.ndarray, weak: np.ndarray) -> np.ndarray:
    """(n, 11) в порядке PHI: V, T24, HOUR, SURGE, SIZE, SELL, WEAK, CALM, ROUND, NTR, TURN1."""
    cols = dict(f)
    cols["HOUR"], cols["WEAK"] = hour, weak
    return np.column_stack([cols[k] for k in PHI])


# ───────────────────────────── условия и правила ─────────────────────────────

def thresholds(X: np.ndarray) -> np.ndarray:
    """Пороги (11, 4): перцентили 50 / 67 / 80 / 90 определённых значений признака (np.percentile, линейная интерполяция); у φ3 — NaN."""
    th = np.full((NPHI, len(PCTS)), np.nan)
    for j in range(NPHI):
        if j == HOUR_J:
            continue
        v = X[:, j]
        v = v[np.isfinite(v)]
        if len(v):
            th[j] = np.percentile(v, PCTS)
    return th


def cond_masks(X: np.ndarray, th: np.ndarray) -> np.ndarray:
    """(46, n) булевы маски условий: «признак ≥ порога» / «час-корзина = b»; пропуск → False."""
    n = len(X)
    M = np.zeros((NCOND, n), bool)
    for ci, (j, k) in enumerate(CONDS):
        x = X[:, j]
        with np.errstate(invalid="ignore"):
            M[ci] = (x == k) if j == HOUR_J else (x >= th[j, k])
    return M


def rule_space() -> list:
    """Все правила пространства процедуры: пустое, 46 одиночных, 960 пар, 12 000 троек (условия по разным признакам) — кортежи индексов условий (по возрастанию)."""
    by_feat = [[ci for ci in range(NCOND) if COND_FEAT[ci] == j] for j in range(NPHI)]
    rules = [()]
    for r in (1, 2, 3):
        for fs in itertools.combinations(range(NPHI), r):
            for cs in itertools.product(*[by_feat[j] for j in fs]):
                rules.append(tuple(cs))
    return rules


def rule_matrix(M: np.ndarray, rules: list) -> np.ndarray:
    """(правил, n) булева матрица попадания сделок под правило."""
    n = M.shape[1]
    R = np.ones((len(rules), n), bool)
    for i, r in enumerate(rules):
        for ci in r:
            R[i] &= M[ci]
    return R


# ───────────────────────────── k-means ─────────────────────────────

def kmeans(X: np.ndarray, K: int, seed: int = SEED, n_init: int = 100, iters: int = 100):
    """k-means++ с перезапусками (зерно + номер запуска), минимум инерции. → (центроиды, метки, инерция)."""
    n = len(X)
    best = None
    for run in range(n_init):
        rng = np.random.default_rng(seed + run)
        cen = [X[rng.integers(n)]]
        for _ in range(1, K):
            d2 = np.min(((X[:, None, :] - np.array(cen)[None, :, :]) ** 2).sum(-1), axis=1)
            s = d2.sum()
            cen.append(X[rng.choice(n, p=d2 / s)] if s > 0 else X[rng.integers(n)])
        cen = np.array(cen)
        lab = None
        for _ in range(iters):
            d = ((X[:, None, :] - cen[None, :, :]) ** 2).sum(-1)
            new = d.argmin(1)
            if lab is not None and (new == lab).all():
                break
            lab = new
            for k in range(K):
                if (lab == k).any():
                    cen[k] = X[lab == k].mean(0)
        d = ((X[:, None, :] - cen[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        inertia = float(d[np.arange(n), lab].sum())
        if best is None or inertia < best[2] - 1e-12:
            best = (cen.copy(), lab.copy(), inertia)
    return best


def pct_rank(x: np.ndarray) -> np.ndarray:
    """Процентный ранг среди монет формирования, равные — средний ранг: (ранг − 0,5) / n."""
    from scipy.stats import rankdata

    return (rankdata(x, method="average") - 0.5) / len(x)


# ───────────────────────────── метрики ─────────────────────────────

def day_series(day: np.ndarray, r: np.ndarray, nd: int):
    """Суточные Σ r и число сделок на nd суток (день — от 0)."""
    s = np.bincount(day.astype(np.int64), weights=r, minlength=nd)[:nd]
    k = np.bincount(day.astype(np.int64), minlength=nd)[:nd]
    return s, k


def run_metrics(day: np.ndarray, r: np.ndarray, kind: np.ndarray | None, nd: int, margin: float = 250.0) -> dict:
    """В-169: Σ $, сделок, $ на сделку, Шарп (годовой, суточный ряд с нулями), просадка $ и % маржи, перехай (самый длинный / средний / p90 / хвост), R:R, убыток на стоп, доля плюсовых суток, σ суточного."""
    s, k = day_series(day, r, nd)
    n = int(k.sum())
    cum = np.cumsum(s)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    prev = np.concatenate([[0.0], peak[:-1]])
    hi = np.nonzero(cum > prev + 1e-9)[0]
    pts = np.concatenate([[-1], hi]).astype(int)
    gaps = np.diff(pts)
    tail = nd - 1 - int(pts[-1])
    allg = np.array(list(gaps) + [tail], float)
    sd = float(np.std(s, ddof=1)) if nd > 1 else 0.0
    win = r[r > 0]
    los = r[r < 0]
    out = dict(n=n, sum=float(s.sum()), per_trade=float(s.sum() / n) if n else float("nan"),
               sharpe=float(s.mean() / sd * math.sqrt(365)) if sd > 0 else float("nan"),
               dd=float((peak - cum).max()), dd_pct=float((peak - cum).max() / margin * 100.0),
               gap_longest=int(allg.max()), gap_mean=float(allg.mean()), gap_p90=float(np.percentile(allg, 90)), gap_tail=tail,
               rr=float(win.mean() / -los.mean()) if len(win) and len(los) else float("nan"),
               pos_days=float((s > 0).sum() / max(1, (k > 0).sum())), sd_day=sd,
               n_small=n < 20)
    if kind is not None:
        st = r[kind == "stop"]
        out["stop_loss"] = float(st.mean()) if len(st) else float("nan")
        out["n_stop"] = int(len(st))
    return out
