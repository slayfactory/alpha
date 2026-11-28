"""TK-735, §13.79.2–§13.79.3: процедура поиска правил, нули, PBO, разложение Δ_A + Δ_D, перекрёстный двойник, бутстреп, правило решения. Чистые функции (numpy), без чтения диска.

Данные режима `RD` (dict): r (результат сделки, $), day (сутки режима от 0), coin (номер монеты 0…), typ (тип монеты сделки, −1 — нет), M (46 × n булева матрица условий, пороги — по типу сделки монеты;
для сделок монет другого типа маска считается по порогам типа, к которому применяется правило — см. `masks_for_type`), nd (сутки режима), sigma_t (σ̂ по (режим, тип)).
"""
from __future__ import annotations

import math

import numpy as np

import tk735_lib as T


# ───────────────────────────── процедура поиска ─────────────────────────────

def admissible(n, ndays, n_ref, setting):
    """Пол настройки: LF — ≥ N_FLOOR сделок за ≥ 5 разных суток; L40 — ≥ 40 % сделок выборки поиска (n_ref)."""
    if setting == "LF":
        return n >= T.N_FLOOR and ndays >= T.MIN_DAYS_LF
    return n >= T.FRAC_L40 * n_ref and n > 0


def j_value(m, n, sigma_t):
    """J = m − σ̂_t / √n (§13.79.2 п. 2)."""
    return m - sigma_t / math.sqrt(n) if n > 0 else -math.inf


def search(M, r, day, sub, sigma_t, setting):
    """Процедура §13.79.2 п. 1–3 на сделках `sub` (индексы в M, r, day): ≤ 3 шага, кандидаты — ещё не использованные признаки × порог / корзина, допустимые по полу, выбор по наибольшему J,
    принятие при росте J против предыдущего. Ничья — меньший номер условия. → кортеж условий (пустой — «правила нет»)."""
    sub = np.asarray(sub, np.int64)
    n_ref = len(sub)
    if n_ref == 0:
        return ()
    Ms = M[:, sub]
    rs = r[sub]
    ds = day[sub]
    cur = np.ones(n_ref, bool)
    j_prev = j_value(rs.mean(), n_ref, sigma_t)
    used_feat, rule = set(), []
    for _ in range(3):
        cand = Ms & cur[None, :]
        cnt = cand.sum(1)
        sm = cand @ rs
        best, bj = None, -math.inf
        for ci in range(T.NCOND):
            if T.COND_FEAT[ci] in used_feat or cnt[ci] == 0:
                continue
            nd_ = None
            if setting == "LF":
                if cnt[ci] < T.N_FLOOR:
                    continue
                nd_ = len(np.unique(ds[cand[ci]]))
            if not admissible(int(cnt[ci]), nd_, n_ref, setting):
                continue
            jv = j_value(sm[ci] / cnt[ci], int(cnt[ci]), sigma_t)
            if jv > bj + 1e-15:
                best, bj = ci, jv
        if best is None or not bj > j_prev + 1e-15:
            break
        rule.append(best)
        used_feat.add(int(T.COND_FEAT[best]))
        cur = cand[best]
        j_prev = bj
    return tuple(rule)


def rule_mask(M, rule):
    m = np.ones(M.shape[1], bool)
    for ci in rule:
        m &= M[ci]
    return m


# ───────────────────────────── суточные агрегаты ─────────────────────────────

def agg_days(day, r, mask, nd):
    """(число сделок, Σ r) по суткам для сделок под маской."""
    d = day[mask].astype(np.int64)
    return np.bincount(d, minlength=nd)[:nd].astype(np.float64), np.bincount(d, weights=r[mask], minlength=nd)[:nd]


def null_a(nsel, nall, sall):
    """Нуль А: Σ_d n_sel_d · m̄_d(все) / Σ_d n_sel_d — математическое ожидание случайного подмножества с тем же числом сделок на каждые сутки (§13.79.3)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        mbar = np.where(nall > 0, sall / nall, 0.0)
    s = nsel.sum()
    return float((nsel * mbar).sum() / s) if s > 0 else float("nan")


# ───────────────────────────── PBO ─────────────────────────────

def pbo_rank(RM, r_w, day_w, ndays, sigma_t, setting, n_w, rule_idx):
    """Ранг правила `rule_idx` (индекс в пространстве) среди допустимых правил по J на W_s: → (доля «от лучшего» ранг/N_допустимых, допустимо ли само правило).
    RM — (правил × сделок W_s) булева матрица попаданий. Ничьи по J — средний ранг."""
    n = RM.sum(1).astype(np.float64)
    s = RM @ r_w
    with np.errstate(invalid="ignore", divide="ignore"):
        jv = np.where(n > 0, s / n - sigma_t / np.sqrt(n), -np.inf)
    if setting == "LF":
        oh = np.zeros((len(r_w), ndays), np.float32)
        oh[np.arange(len(r_w)), day_w] = 1.0
        dpres = (RM.astype(np.float32) @ oh) > 0
        nd_ = dpres.sum(1)
        adm = (n >= T.N_FLOOR) & (nd_ >= T.MIN_DAYS_LF)
    else:
        adm = (n >= T.FRAC_L40 * n_w) & (n > 0)
    if not adm[rule_idx]:
        return float("nan"), False
    jv_a = jv[adm]
    me = jv[rule_idx]
    better = (jv_a > me + 1e-15).sum()
    equal = (np.abs(jv_a - me) <= 1e-15).sum()
    rank = better + (equal + 1) / 2.0
    return float(rank / adm.sum()), True


# ───────────────────────────── бутстреп по суткам ─────────────────────────────

def day_weights(nd, block, nboot, seed):
    """Матрица (nboot × nd): сколько раз каждые сутки вошли в повтор блочного (круговой) бутстрепа по суткам, блок `block`."""
    rng = np.random.default_rng(seed)
    nb = math.ceil(nd / block)
    starts = rng.integers(0, nd, size=(nboot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % nd
    idx = idx.reshape(nboot, nb * block)[:, :nd]
    W = np.zeros((nboot, nd), np.float32)
    rows = np.repeat(np.arange(nboot), nd)
    np.add.at(W, (rows, idx.ravel()), 1.0)
    return W


def delta_rep(W, A, N, B, C, E):
    """Δ_A (и Δ_D) по повторам: A — Σ r отобранных по (сплит × сутки), N — число отобранных, B — Σ n_sel · m̄_all (нуль А), C — Σ r всех, E — число всех (все — (сплит × сутки), типы просуммированы).
    → (Δ_A_rep, Δ_D_rep), среднее по сплитам с непустым отбором. Δ_A = m − m^A; Δ_D = m^A − m^все."""
    wa, wn, wb, wc, we = W @ A.T, W @ N.T, W @ B.T, W @ C.T, W @ E.T
    with np.errstate(invalid="ignore", divide="ignore"):
        m = wa / wn
        ma = wb / wn
        mall = wc / we
        da = m - ma
        dd = ma - mall
    ok = (wn > 0) & (we > 0)
    cnt = ok.sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cnt > 0, np.where(ok, da, 0).sum(1) / cnt, np.nan), np.where(cnt > 0, np.where(ok, dd, 0).sum(1) / cnt, np.nan)


def interval(rep, point, alpha=0.05):
    """Односторонние 95 % границы по повторам бутстрепа (перцентили 5 / 95) и точка."""
    v = rep[np.isfinite(rep)]
    if len(v) == 0:
        return dict(point=float(point), lo=float("nan"), hi=float("nan"), se=float("nan"))
    return dict(point=float(point), lo=float(np.percentile(v, 100 * alpha)), hi=float(np.percentile(v, 100 * (1 - alpha))), se=float(v.std(ddof=1)))


def p_one_sided(rep, point):
    """Односторонний p для H0: Δ ≤ 0 по нормальной аппроксимации от точки и SE бутстрепа."""
    from statistics import NormalDist

    v = rep[np.isfinite(rep)]
    se = float(v.std(ddof=1)) if len(v) > 1 else 0.0
    if se <= 0:
        return 1.0 if point <= 0 else 0.0
    return float(1.0 - NormalDist().cdf(point / se))


def holm_adj(pvals):
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * pvals[i]))
        adj[i] = run
    return adj
