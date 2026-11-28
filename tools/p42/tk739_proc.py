#!/usr/bin/env python3
"""TK-739 (П-42 шаг 8, §13.81.2–§13.81.3): процедура выбора Π (`step8_select`), вложенная проверка (45 разбиений), PBO, пути CPCV, нижняя граница C′. Чистые функции (numpy), без ввода-вывода и без P&L-чтения:
одна и та же `select` работает на обучающих сутках разбиений, на полной выборке и в синтетических мирах G-pow8 (`tk739_pow.py`).

Детали, которых нет в протоколе (amend L-548 и L-553, записаны до G-pow8): ридж — (1/n)‖y − Xβ‖² + λ‖β‖², столбцы стандартизуются на допущенных точках, константные отбрасываются, λ ∈ 10^(−3…3) (13 значений), 5-кратная перекрёстная проверка
по точкам отдельно для целей Σ и SR (`seed`); блочный бутстреп — круговые блоки по порядку суток обучающей части; отказ процедуры в разбиении → разность пути 0 (торгуется B*), в PBO — «не выше середины»; ранг PBO — среди точек R по SR теста.
"""
from __future__ import annotations

import itertools
import math

import numpy as np

SEED = 20261127
LAMS = 10.0 ** np.arange(-3.0, 3.01, 0.5)
ADMIT = 0.596
YEAR = math.sqrt(365.0)
NB_BOOT = 200
PLATEAU_MIN = 2.0 / 3.0
GROUPS_LEN = (29,) * 6 + (28,) * 4                                   # 286 суток, 10 групп подряд


def groups(n_days=286):
    out, s = [], 0
    for L in GROUPS_LEN:
        out.append(np.arange(s, s + L))
        s += L
    assert s == n_days
    return out


def splits(purge=2):
    """45 разбиений (пары групп в тест): [(i, j, train_days, test_days)]; из обучения убираются `purge` суток с каждой стороны каждой тестовой группы (очистка 1 + запрет 1)."""
    G = groups()
    out = []
    for i, j in itertools.combinations(range(10), 2):
        test = np.concatenate([G[i], G[j]])
        bad = np.zeros(286, bool)
        bad[test] = True
        for g in (G[i], G[j]):
            lo, hi = g[0], g[-1]
            bad[max(0, lo - purge):lo] = True
            bad[hi + 1:min(286, hi + 1 + purge)] = True
        out.append((i, j, np.nonzero(~bad)[0], test))
    return out


def paths():
    """9 путей CPCV (N = 10, k = 2): каждая группа в тесте ровно один раз — разложение пар на 9 совершенных паросочетаний (круговой метод). Возвращает [[(i, j) × 5] × 9]."""
    n = 10
    arr = list(range(n))
    out = []
    for _ in range(n - 1):
        out.append([tuple(sorted((arr[k], arr[n - 1 - k]))) for k in range(n // 2)])
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    return out


# ───────────────────────────── ридж ─────────────────────────────

class Ridge:
    """Ридж на допущенных точках: X (n × P) сырые столбцы → стандартизация, W = (XᵀX/n + λI)⁻¹Xᵀ/n фиксирован внутри разбиения."""

    def __init__(self, F, seed):
        self.mu = F.mean(0)
        sd = F.std(0)
        self.keep = sd > 1e-9
        self.sd = np.where(self.keep, sd, 1.0)
        self.X = ((F - self.mu) / self.sd)[:, self.keep]
        self.n, self.p = self.X.shape
        self.seed = seed
        self.W = {}

    def cv_lambda(self, y):
        rng = np.random.default_rng(self.seed)
        perm = rng.permutation(self.n)
        folds = np.array_split(perm, 5)
        err = np.zeros(len(LAMS))
        for f in folds:
            m = np.ones(self.n, bool)
            m[f] = False
            Xt, yt = self.X[m], y[m] - y[m].mean()
            xm = Xt.mean(0)
            Xc = Xt - xm
            A = Xc.T @ Xc / len(Xc)
            ev, V = np.linalg.eigh(A)
            b0 = V.T @ (Xc.T @ yt / len(Xc))
            Xv = (self.X[f] - xm) @ V
            for k, lam in enumerate(LAMS):
                beta = b0 / (ev + lam)
                err[k] += np.sum((y[f] - y[m].mean() - Xv @ beta) ** 2)
        return LAMS[int(np.argmin(err))]

    def fit_W(self, lam):
        Xc = self.X - self.X.mean(0)
        self.xm = self.X.mean(0)
        A = Xc.T @ Xc / self.n + lam * np.eye(self.p)
        self.W[lam] = np.linalg.solve(A, Xc.T) / self.n          # p × n
        return self.W[lam]


def _boot_counts(nT, B, rng):
    L = max(1, math.ceil(nT ** (1.0 / 3.0)))
    nb = math.ceil(nT / L)
    starts = rng.integers(0, nT, size=(B, nb))
    idx = (starts[:, :, None] + np.arange(L)[None, None, :]).reshape(B, -1)[:, :nT] % nT
    cnt = np.zeros((B, nT))
    np.add.at(cnt, (np.repeat(np.arange(B), nT), idx.ravel()), 1.0)
    return cnt


def select(days, X, cnt, base, F, NB, NBmask, seed=SEED, nboot=NB_BOOT, cand_ok=None):
    """Процедура Π на обучающих сутках `days`. X — суточные ряды точек (n × 286, $), cnt — число сделок (n × 286), base — ряд базы (286,), F — сырые столбцы поверхности (n × P),
    NB — сырые столбцы соседей ±δ каждой точки (n × Q × P, float32), NBmask — (n × Q) bool (сосед существует), cand_ok — (n,) bool: точка может быть кандидатом (не база и не дубль базы).
    Возвращает dict(c, lb, plateau, yhat, qmean, n_adm, lam_sigma, lam_sr) или c = None (отказ)."""
    days = np.asarray(days)
    nT = len(days)
    n = len(X)
    ok = cnt[:, days].sum(1) >= math.ceil(ADMIT * nT)
    adm = np.nonzero(ok)[0]
    res = dict(c=None, n_adm=int(len(adm)))
    if len(adm) < 40:
        return res
    XT = X[adm][:, days]
    DT = XT - base[days][None]
    m = DT.mean(1)
    sg = XT.std(1, ddof=1)
    sg = np.where(sg > 1e-9, sg, np.nan)
    sr = m / sg * YEAR
    good = np.isfinite(sr)
    adm, XT, DT, m, sr = adm[good], XT[good], DT[good], m[good], sr[good]
    if len(adm) < 40:
        return res
    R = Ridge(F[adm], seed)
    lam_s, lam_r = R.cv_lambda(m), R.cv_lambda(sr)
    Wm, Wr = R.fit_W(lam_s), R.fit_W(lam_r)
    bm = Wm @ (m - m.mean())
    br = Wr @ (sr - sr.mean())
    yhat_m = m.mean() + (R.X - R.xm) @ bm
    # плато: сосед улучшает базу (ŷΣ > 0); ŷ(q) = ȳ + ((F_q − μ)/σ)·β = F_q·(β/σ) + (ȳ − μ·(β/σ))
    keepF = R.keep
    coef = np.zeros(F.shape[1])
    coef[keepF] = bm / R.sd[keepF]
    off = m.mean() - R.mu @ coef - R.xm @ bm
    pred = (NB @ coef.astype(np.float32))[adm] + np.float32(off)
    msk = NBmask[adm]
    nq = msk.sum(1)
    pl = np.where(nq > 0, ((pred > 0) & msk).sum(1) / np.maximum(nq, 1), 0.0)
    qmean = np.where(nq > 0, (pred * msk).sum(1) / np.maximum(nq, 1), 0.0)
    cand = (yhat_m > 0) & (pl >= PLATEAU_MIN) & (cand_ok[adm] if cand_ok is not None else True)
    if not cand.any():
        return res
    ci = np.nonzero(cand)[0]
    rng = np.random.default_rng(seed + 1)
    cntb = _boot_counts(nT, nboot, rng)                                    # B × nT
    ones = cntb.sum(1, keepdims=True)
    mb_all = (cntb @ DT.T) / ones
    ex_all = (cntb @ XT.T) / ones
    ex2_all = (cntb @ (XT ** 2).T) / ones
    sd_all = np.sqrt(np.maximum((ex2_all - ex_all ** 2) * ones / (ones - 1), 1e-18))
    yb = mb_all / sd_all * YEAR                                            # B × n_adm
    ybc = yb - yb.mean(1, keepdims=True)
    beta_b = ybc @ Wr.T                                                    # B × p
    Xc = R.X[ci] - R.xm
    pred_b = yb.mean(1, keepdims=True) + beta_b @ Xc.T                     # B × nc
    lb = np.percentile(pred_b, 5, axis=0)
    j = int(np.argmax(lb))                                                 # ничья — меньший номер (argmax берёт первый; ci упорядочен по номеру точки)
    c = int(adm[ci[j]])
    return dict(c=c, lb=float(lb[j]), plateau=float(pl[ci[j]]), yhat=float(yhat_m[ci[j]]), qmean=float(qmean[ci[j]]), n_adm=int(len(adm)), lam_sigma=float(lam_s), lam_sr=float(lam_r))


# ───────────────────────────── вложенная проверка ─────────────────────────────

def nested(X, cnt, base, base_n, F, NB, NBmask, cand_ok, Rn, spl=None, seed=SEED, nboot=NB_BOOT):
    """45 запусков Π; Rn — число точек опорного набора R (строки 0…Rn−1 в X). Возвращает dict: choices (c*_s или None), omega, pbo, refusals, invalid, test_delta (45 × 286: Δ c*_s на тестовых сутках, 0 при отказе), sel."""
    spl = spl or splits()
    ch, om, below, invalid = [], [], 0, 0
    test_delta = np.zeros((len(spl), 286))
    sels = []
    for s, (i, j, tr, te) in enumerate(spl):
        if base_n[te].sum() == 0:                                     # недопустимое разбиение: у B* нет сделок в тесте (→ O5)
            invalid += 1
        sel = select(tr, X, cnt, base, F, NB, NBmask, seed=seed + s, nboot=nboot, cand_ok=cand_ok)
        sels.append(sel)
        c = sel["c"]
        ch.append(c)
        if c is None:
            below += 1
            om.append(float("nan"))
            continue
        D = X[:, te] - base[te][None]
        sdx = X[:Rn][:, te].std(1, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            sr = np.where(sdx > 1e-9, D[:Rn].mean(1) / sdx, -np.inf)
        sc = D[c].mean() / max(X[c, te].std(ddof=1), 1e-12) if cnt[c, te].sum() > 0 else -np.inf
        rank = 1 + int((sr < sc).sum())
        w = rank / (Rn + 1.0)
        om.append(w)
        test_delta[s, te] = D[c]
    om = np.array(om)
    hit = np.array([(c is None) or (not np.isfinite(w)) or (w <= 0.5) or (cnt[c, spl[s][3]].sum() == 0) for s, (c, w) in enumerate(zip(ch, om))])
    return dict(choices=ch, omega=om, pbo=float(hit.mean()), refusals=int(sum(c is None for c in ch)), invalid=int(invalid), test_delta=test_delta, sel=sels, split_ij=[(s[0], s[1]) for s in spl])


def path_series(test_delta, split_ij):
    """Девять путей CPCV: ряд разности пути — склейка тестовых рядов Δ c*_s пар пути; тестовый ряд правила — среднее по путям (286 суток)."""
    idx = {p: s for s, p in enumerate(split_ij)}
    out = []
    for pth in paths():
        row = np.zeros(286)
        for pair in pth:
            s = idx[tuple(sorted(pair))]
            G = groups()
            for g in pair:
                row[G[g]] = test_delta[s, G[g]]
        out.append(row)
    P = np.array(out)
    return P, P.mean(0)


def cprime(series, block, B=20000, seed=SEED):
    """Нижняя граница (5-й перцентиль) блочного бутстрепа среднего суточного ряда; круговые блоки длины `block`."""
    rng = np.random.default_rng(seed + block)
    n = len(series)
    nb = math.ceil(n / block)
    out = np.empty(B)
    ch = 2000
    for a in range(0, B, ch):
        k = min(ch, B - a)
        st = rng.integers(0, n, size=(k, nb))
        idx = (st[:, :, None] + np.arange(block)[None, None, :]).reshape(k, -1)[:, :n] % n
        out[a:a + k] = series[idx].mean(1)
    return float(np.percentile(out, 5)), float(out.mean())
