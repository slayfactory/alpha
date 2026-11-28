"""Тесты TK-735: пространство правил, причинность признаков, копия rv_of, процедура поиска, нуль А, разложение Δ, k-means, метрики."""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk727_lib as L  # noqa: E402
import tk735_gates as GT  # noqa: E402
import tk735_lib as T  # noqa: E402
import tk735_search as SR  # noqa: E402


def synth(n=4000, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(rng.normal(0, 3e-4, n)))
    o = np.concatenate([[start], c[:-1]])
    h = np.maximum(o, c) * (1 + rng.uniform(0, 2e-4, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 2e-4, n))
    c, o, h, l = (np.round(x, 4) for x in (c, o, h, l))
    qv = rng.uniform(1e3, 1e5, n)
    cnt = rng.integers(10, 500, n).astype(float)
    tbq = qv * rng.uniform(0.2, 0.8, n)
    pres = rng.random(n) < 0.97
    for a in (o, h, l, c):
        a[~pres] = np.nan
    qv[~pres] = 0
    cnt[~pres] = 0
    tbq[~pres] = 0
    return dict(o=o, h=h, l=l, c=c, qv=qv, cnt=cnt, tbq=tbq, pres=pres)


def test_rule_space_size():
    rules = T.rule_space()
    assert len(rules) == T.EXPECT_RULES == 13007
    assert T.NCOND == 46
    assert all(len({int(T.COND_FEAT[c]) for c in r}) == len(r) for r in rules)     # условия по разным признакам
    assert len(set(rules)) == len(rules)


def test_rule_matrix_and_masks():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(300, T.NPHI))
    X[:, T.HOUR_J] = rng.integers(0, 6, 300)
    X[::7, 3] = np.nan
    th = T.thresholds(X)
    M = T.cond_masks(X, th)
    assert M.shape == (46, 300)
    assert not M[[ci for ci, (j, k) in enumerate(T.CONDS) if j == 3]][:, ::7].any()           # пропуск → условие не выполнено
    assert M[T.CONDS.index((0, 0))].mean() > M[T.CONDS.index((0, 3))].mean()                # ≥ p50 шире, чем ≥ p90
    rules = [(), (0,), (0, 7), (0, 7, 12)]
    R = T.rule_matrix(M, rules)
    assert R[0].all() and (R[2] == (M[0] & M[7])).all() and (R[3] == (M[0] & M[7] & M[12])).all()


def test_feat_at_causal_and_columns():
    S = synth()
    tc = np.array([2000, 2500, 3999 - 100])
    full = T.feat_at(S, tc)
    for t in tc:
        Sg = {k: v.copy() for k, v in S.items()}
        rng = np.random.default_rng(5)
        n = len(Sg["c"])
        for k in ("o", "h", "l", "c"):
            Sg[k][t + 1:] = rng.uniform(1, 500, n - t - 1)
        for k in ("qv", "cnt", "tbq"):
            Sg[k][t + 1:] = rng.uniform(0, 1e7, n - t - 1)
        Sg["pres"][t + 1:] = rng.random(n - t - 1) < 0.5
        g = T.feat_at(Sg, [t])
        tr = T.feat_at({k: v[:t + 1] for k, v in S.items()}, [t])
        i = list(tc).index(t)
        for k in full:
            a = full[k][i]
            assert (np.isnan(a) and np.isnan(g[k][0]) and np.isnan(tr[k][0])) or a == g[k][0] == tr[k][0], k


def test_feat_formulas():
    S = synth(3000, seed=3)
    t = 2500
    f = T.feat_at(S, [t])
    q = S["qv"]
    assert S["pres"][t]
    assert math.isclose(f["TURN1"][0], math.log10(q[t - 59:t + 1].sum()), rel_tol=1e-12)
    assert math.isclose(f["T24"][0], math.log10(q[t - 1439:t + 1].sum()), rel_tol=1e-12)
    assert math.isclose(f["SURGE"][0], q[t - 59:t + 1].sum() / (q[t - 1499:t - 59].sum() / 24.0), rel_tol=1e-12)
    assert math.isclose(f["SIZE"][0], q[t - 59:t + 1].sum() / S["cnt"][t - 59:t + 1].sum(), rel_tol=1e-12)
    assert math.isclose(f["SELL"][0], 1 - S["tbq"][t - 14:t + 1].sum() / q[t - 14:t + 1].sum(), rel_tol=1e-12)
    assert math.isclose(f["NTR"][0], math.log10(S["cnt"][t - 59:t + 1].sum()), rel_tol=1e-12)
    hh = S["h"][t - 59:t + 1]
    ll = S["l"][t - 59:t + 1]
    rng_bp = (np.nanmax(hh) - np.nanmin(ll)) / S["c"][t] * 1e4
    assert math.isclose(f["CALM"][0], f["V"][0] / rng_bp, rel_tol=1e-12)
    c = S["c"][t]
    g = 10.0 ** (math.floor(math.log10(c)) - 1)
    assert math.isclose(f["ROUND"][0], -1e4 * min(math.fmod(c, g), g - math.fmod(c, g)) / c, rel_tol=1e-9)
    assert math.isclose(T.r60_at(S, [t])[0], f["r60"][0], rel_tol=1e-12)
    assert np.isnan(T.feat_at(S, [100])["T24"][0])                                           # нет 1 440 минут → признака нет


def test_hour_bucket():
    m = (np.array([1765497600000, 1765497600000 + 4 * 3600_000, 1765497600000 + 23 * 3600_000]) - L.G0_MS) // L.MIN_MS
    assert list(T.hour_bucket(m)) == [0.0, 1.0, 5.0]                                          # 12.12.2025 00:00 UTC


def test_rv_engine_copy_matches_python():
    S = synth(3000, seed=4)
    S["pres"][:] = True
    S["c"] = np.round(np.abs(S["c"]), 4)
    for k in ("o", "h", "l"):
        S[k] = np.nan_to_num(S[k], nan=1.0)
    S["c"] = np.nan_to_num(S["c"], nan=1.0)
    rv = L.rv_sqrt_sum(S, 60)
    for t in (100, 500, 1500, 2900):
        eng, k = GT.rv_engine_int(S, t)
        assert eng >= 0 and abs(rv[t] * 1e8 - eng) <= math.sqrt(k) + 1.0


def test_search_finds_planted_edge_and_respects_floor():
    rng = np.random.default_rng(7)
    n = 400
    X = rng.normal(size=(n, T.NPHI))
    X[:, T.HOUR_J] = rng.integers(0, 6, n)
    th = T.thresholds(X)
    M = T.cond_masks(X, th)
    day = rng.integers(0, 60, n)
    r = rng.normal(0, 1, n)
    r += 3.0 * M[T.CONDS.index((1, 3))]                  # правило «φ2 ≥ p90» даёт +3 $
    sigma = float(r.std())
    sub = np.arange(n)
    rule = SR.search(M, r, day, sub, sigma, "L40")
    assert T.CONDS.index((1, 0)) in rule                    # L40 допускает только ≥ p50 (≥ 40 % сделок); более узкие пороги — вне пола
    msk = SR.rule_mask(M, rule)
    assert msk.sum() >= 0.4 * n
    # LF: пол 10 сделок и 5 суток
    rule_lf = SR.search(M, r, day, sub, sigma, "LF")
    mk = SR.rule_mask(M, rule_lf)
    assert mk.sum() >= T.N_FLOOR and len(np.unique(day[mk])) >= T.MIN_DAYS_LF
    assert T.CONDS.index((1, 3)) in rule_lf or T.CONDS.index((1, 2)) in rule_lf    # LF допускает узкий порог с заложенным краем
    # шум: без края правила нет или J растёт; правило не превышает 3 условий и условия по разным признакам
    r0 = rng.normal(0, 1, n)
    rl = SR.search(M, r0, day, sub, float(r0.std()), "L40")
    assert len(rl) <= 3 and len({int(T.COND_FEAT[c]) for c in rl}) == len(rl)


def test_null_a_matches_expectation():
    rng = np.random.default_rng(3)
    nall = rng.integers(1, 6, 20).astype(float)
    r = [rng.normal(0, 1, int(k)) for k in nall]
    sall = np.array([x.sum() for x in r])
    nsel = np.array([min(1, int(k)) for k in nall], float)
    nsel[::3] = 0
    exp = 0.0
    for d in range(20):
        exp += nsel[d] * (sall[d] / nall[d])
    exp /= nsel.sum()
    assert math.isclose(SR.null_a(nsel, nall, sall), exp, rel_tol=1e-12)
    # Δ_A + Δ_D = m − m^все
    A = np.array([[(r[d][: int(nsel[d])]).sum() for d in range(20)]])
    N = nsel[None, :]
    mall = sall / nall
    B = (nsel * mall)[None, :]
    C, E = sall[None, :], nall[None, :]
    W = np.ones((1, 20), np.float32)
    da, dd = SR.delta_rep(W, A, N, B, C, E)
    m = A.sum() / N.sum()
    assert math.isclose(da[0] + dd[0], m - C.sum() / E.sum(), rel_tol=1e-9)


def test_day_weights_sum():
    W = SR.day_weights(150, 6, 200, T.SEED)
    assert W.shape == (200, 150) and (W.sum(1) == 150).all()


def test_pbo_rank_and_holm():
    rng = np.random.default_rng(9)
    RM = rng.random((50, 120)) < 0.5
    r = rng.normal(0, 1, 120)
    day = rng.integers(0, 30, 120)
    rk, ok = SR.pbo_rank(RM, r, day, 30, 1.0, "L40", 120, 3)
    assert ok and 0 < rk <= 1
    adj = SR.holm_adj([0.01, 0.04, 0.03])
    assert adj[0] == 0.03 and adj[2] == adj[1] == 0.06


def test_kmeans_separates():
    rng = np.random.default_rng(1)
    X = np.vstack([rng.normal(0, 0.05, (30, 2)), rng.normal(3, 0.05, (30, 2)), rng.normal(-3, 0.05, (30, 2))])
    cen, lab, ine = T.kmeans(X, 3, n_init=10)
    assert len(set(lab[:30])) == 1 and len(set(lab[30:60])) == 1 and len(set(lab[60:])) == 1 and len(set(lab)) == 3


def test_run_metrics():
    day = np.array([0, 0, 1, 3, 3, 3])
    r = np.array([1.0, -2.0, 3.0, 1.0, 1.0, -1.0])
    m = T.run_metrics(day, r, np.array(["stop", "x", "x", "x", "x", "stop"]), 5)
    assert m["n"] == 6 and math.isclose(m["sum"], 3.0) and m["n_stop"] == 2 and m["n_small"]
    assert m["dd"] == 1.0 and m["gap_longest"] >= 1


def test_p4_counts_and_shrink():
    import pandas as pd

    import tk735_props as PR

    lo = 1781654400000
    day = PR.DAY_MS
    rows = []
    for sym, per_day in (("AAA", [(1, 1), (1, 0), (2, 1), (3, 1)]), ("BBB", [(1, 0), (2, 0), (2, 0)])):
        for d, y in per_day:
            rows.append(dict(sym=sym, level=1, sig_abs_ms=lo + (d - 1) * day + 1000, ok_t60=1, y_n_rv60=y, rv_e8=1, lp_e8=1))
    rows.append(dict(sym="AAA", level=1, sig_abs_ms=lo + 1000, ok_t60=0, y_n_rv60=np.nan, rv_e8=1, lp_e8=1))   # без метки — не считается
    rows.append(dict(sym="ZZZ", level=1, sig_abs_ms=lo + 1000, ok_t60=1, y_n_rv60=1, rv_e8=1, lp_e8=1))        # монеты нет в списке — игнор
    C = PR.p4_counts(pd.DataFrame(rows), ["AAA", "BBB", "CCC"], lo)
    assert C["all"][1].tolist() == [4, 3, 0] and C["all"][0].tolist() == [3, 0, 0]
    assert C["odd"][1].tolist() == [3, 1, 0] and C["even"][1].tolist() == [1, 2, 0]      # сутки 1, 3 — нечётные; 2 — чётные
    assert C["odd"][0].tolist() == [2, 0, 0] and C["even"][0].tolist() == [1, 0, 0]


def test_p4_shrink_pulls_to_market_and_undefined():
    import tk735_props as PR

    rng = np.random.default_rng(3)
    true = rng.beta(2, 5, 200)                       # реальная разнородность монет → сверхдисперсия
    n = rng.integers(5, 40, 200)
    k = rng.binomial(n, true)
    adm = np.ones(200, bool)
    ph, info = PR.p4_shrink(k, n, adm)
    assert info["defined"] and info["m"] > 0 and 0 < info["p0"] < 1
    raw = k / n
    assert ph.std() < raw.std()                      # усадка сжимает разброс
    ph0, i0 = PR.p4_shrink(np.zeros(200, np.int64), n, adm)
    assert not i0["defined"] and np.isnan(ph0).all()
    nb = np.full(200, 20)
    kb = rng.binomial(nb, 0.3)                       # нет разнородности → rho <= 0 в части выборок; не падает
    PR.p4_shrink(kb, nb, adm)
    adm2 = adm.copy()
    adm2[:50] = False
    ph2, _ = PR.p4_shrink(k, n, adm2)
    assert np.isnan(ph2[:50]).all() and np.isfinite(ph2[50:]).all()


def test_eval_split_planted_edge():
    import tk735_run as RN

    rng = np.random.default_rng(5)
    n, nd = 3000, 60
    day = rng.integers(0, nd, n)
    coin = rng.integers(0, 40, n)
    X = rng.normal(0, 1, (n, T.NPHI))
    X[:, T.HOUR_J] = rng.integers(0, 6, n)
    r = rng.normal(0, 3, n) + 3.0 * (X[:, 0] >= 0.5)                        # правдивый край: V (φ1) ≥ порога
    th = T.thresholds(X)
    M = T.cond_masks(X, th)
    rules = T.rule_space()
    pos = {ru: i for i, ru in enumerate(rules)}
    B = np.where(coin < 10)[0]
    W = np.where((coin >= 10) & (coin < 20))[0]
    C = np.where(coin >= 20)[0]
    o = RN.eval_split(M, r, day, nd, B, W, C, 3.0, "L40", rules, pos)
    assert o["rule"] and int(T.COND_FEAT[o["rule"][0]]) == 0                  # первым выбран признак V
    same = o["same"]
    assert abs(same["A"].sum() - r[W][SR.rule_mask(M[:, W], o["rule"])].sum()) < 1e-9
    assert same["N"].sum() == SR.rule_mask(M[:, W], o["rule"]).sum() and same["E"].sum() == len(W)
    assert abs(same["C"].sum() - r[W].sum()) < 1e-9
    assert o["pbo_adm"] and 0 < o["pbo_rank"] <= 1
    assert set(o["cross"]) <= {0, 1} and RN.canon(o["rule"]) in pos
    # нуль А: если правило выбирает случайную половину суток, Σ n_отб · m̄ ≈ Σ r отобранных в среднем — формула не падает на пустых сутках
    assert np.isfinite(same["Bn"]).all()
