"""TK-728 (шаг 4 этапа A): тесты общих функций `tk728_lib` — корреляции и кластеры, представитель, окна, блочный бутстреп, Мантель–Хензель (РБГ, перестановки),
сверхдисперсия и усадка, причинный ранг. Только numpy / scipy."""
import math
import os
import sys

import numpy as np
import pytest
from scipy.stats import spearmanr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk728_lib as L  # noqa: E402


# ── 4б ──────────────────────────────────────────────────────────────────────────────────────────

def test_spearman_equals_scipy_without_nan():
    rng = np.random.default_rng(1)
    z = rng.normal(size=3000)
    X = np.column_stack([z, z + rng.normal(0, 0.3, 3000), rng.normal(size=3000), np.round(z * 2)])
    rho, n = L.spearman_pairwise(X, min_pairs=10)
    ref = spearmanr(X).statistic
    assert np.allclose(rho, ref, atol=1e-10)
    assert (n == 3000).all()


def test_spearman_pairwise_nan_matches_ranks_of_columns():
    rng = np.random.default_rng(2)
    x = rng.normal(size=4000)
    y = x + rng.normal(0, 0.5, 4000)
    X = np.column_stack([x, y])
    X[rng.random(4000) < 0.3, 1] = np.nan
    rho, n = L.spearman_pairwise(X, min_pairs=10)
    R = L.rank_columns(X)
    ok = np.isfinite(R[:, 0]) & np.isfinite(R[:, 1])
    assert n[0, 1] == ok.sum()
    assert rho[0, 1] == pytest.approx(np.corrcoef(R[ok, 0], R[ok, 1])[0, 1], abs=1e-10)
    assert rho[0, 1] == pytest.approx(rho[1, 0])


def test_spearman_constant_and_short_pairs_nan():
    X = np.column_stack([np.ones(2000), np.arange(2000.0), np.where(np.arange(2000) < 500, np.arange(2000.0), np.nan)])
    rho, n = L.spearman_pairwise(X, min_pairs=1000)
    assert np.isnan(rho[0, 1]) and np.isnan(rho[0, 0])      # константа
    assert np.isnan(rho[1, 2])                              # общих строк 500 < 1000
    assert rho[1, 1] == pytest.approx(1.0)


def test_cluster_average_two_groups_and_threshold():
    rho = np.full((5, 5), 0.1)
    for g in ((0, 1, 2), (3, 4)):
        for i in g:
            for j in g:
                rho[i, j] = 0.9
    np.fill_diagonal(rho, 1.0)
    lab = L.cluster_average(rho)
    assert lab[0] == lab[1] == lab[2] and lab[3] == lab[4] and lab[0] != lab[3]
    # ровно на пороге |ρ| = 0,7 — ещё копия; 0,69 — уже нет
    r2 = np.array([[1.0, 0.7], [0.7, 1.0]])
    assert len(set(L.cluster_average(r2))) == 1
    r3 = np.array([[1.0, -0.69], [-0.69, 1.0]])
    assert len(set(L.cluster_average(r3))) == 2
    # знак не важен
    assert len(set(L.cluster_average(np.array([[1.0, -0.8], [-0.8, 1.0]])))) == 1
    # NaN — не копия
    assert len(set(L.cluster_average(np.array([[1.0, np.nan], [np.nan, 1.0]])))) == 2


def test_cluster_average_uses_mean_linkage_not_single():
    # цепочка a–b 0,9, b–c 0,9, a–c 0,1: средняя связь при слиянии {a,b}+c = (0,9+0,1)/2 = 0,5 < 0,7 → c не входит
    rho = np.array([[1, 0.9, 0.1], [0.9, 1, 0.9], [0.1, 0.9, 1.0]])
    lab = L.cluster_average(rho)
    assert len(set(lab)) == 2


def test_pick_rep_tiebreaks():
    cnt, win, num = {0: 90, 1: 100, 2: 100}, {0: 1, 1: 60, 2: 5}, {0: 10, 1: 20, 2: 30}
    assert L.pick_rep([0, 1, 2], cnt, win, num) == (2, "окно")
    assert L.pick_rep([0, 1], cnt, win, num) == (1, "охват")
    win2 = {0: 1, 1: 5, 2: 5}
    assert L.pick_rep([1, 2], cnt, win2, num) == (1, "номер")
    assert L.pick_rep([0], cnt, win, num) == (0, "один")


@pytest.mark.parametrize("name,exp", [
    ("cb_15m", 15), ("cb_4h", 240), ("cb_7d", 10080), ("c5_btc_7d", 10080), ("c1_btc_6h", 360), ("ok10_d7", 10080), ("xok10_d30", 43200),
    ("mk_btc_s60", 60), ("mk_rel_s1440", 1440), ("mk_rv5_btc_60", 60), ("mk_corr5_240", 240), ("n60", 60), ("n24", 1440), ("m24", 1440),
    ("n1440", 1440), ("tfv300", 5), ("tfn10", 10 / 60), ("big1800", 30), ("bigimb300", 5), ("sac10", 10 / 60), ("vpin300", 5), ("vpin1h", 60), ("vpin6h", 360),
    ("kyle30", 30), ("amih240", 240), ("kyle1m", 1), ("amih5m", 5), ("mk10_300", 5), ("mk1_10", 10 / 60), ("accn1_60", 1), ("accdv30_300", 5),
    ("mv15", 15), ("btc_mv5", 5), ("tpm60", 60), ("cons120", 120), ("size1440", 1440), ("sw_a30", 30), ("pm_p24", 1440), ("turn1h", 60), ("zv6h", 360),
    ("natr_bp", 30), ("lturn24", 1440),
])
def test_window_minutes(name, exp):
    assert L.window_minutes(name) == pytest.approx(exp)


def test_window_minutes_unknown_is_inf():
    assert math.isinf(L.window_minutes("spread_bp"))
    assert math.isinf(L.window_minutes("dist_bp"))


# ── 4в ──────────────────────────────────────────────────────────────────────────────────────────

def test_block_len_rule():
    assert L.block_len(150) == 6 and L.block_len(136) == 6 and L.block_len(286) == 7 and L.block_len(125) == 5 and L.block_len(1) == 1


def test_block_idx_same_scheme_as_lib():
    try:
        import lib
    except Exception:                                    # на ПК pandas может быть сломан — тот же тест идёт на srv
        pytest.skip("lib.py не импортируется (pandas)")
    x = np.random.default_rng(3).normal(size=50)
    idx = L.block_idx(50, np.random.default_rng(7), 200, 4)
    ref = lib.block_boot_means(x, np.random.default_rng(7), nboot=200, block=4)
    assert np.allclose(x[idx].mean(axis=1), ref)


def test_ratio_boot_constant_ratio_degenerate():
    num = np.array([2.0, 4, 6, 8, 10, 12, 14, 16])
    den = num * 2
    est, lo, hi = L.ratio_boot(num, den, seed=1, block=3, nboot=500)
    assert est == pytest.approx(0.5) and lo == pytest.approx(0.5) and hi == pytest.approx(0.5)


def test_ratio_boot_covers_truth():
    rng = np.random.default_rng(5)
    den = rng.integers(20, 40, 136).astype(float)
    num = rng.binomial(den.astype(int), 0.3).astype(float)
    est, lo, hi = L.ratio_boot(num, den, seed=L.SEED, block=6, nboot=2000)
    assert lo < 0.3 < hi and lo < est < hi


def test_terciles_and_kappa():
    v = np.arange(300.0)
    (q1, q2), t = L.terciles(v)
    assert np.bincount(t).tolist() == [100, 100, 100] and q1 < q2
    a = np.array([1, 1, 0, 0, 1, 0, 1, 0])
    assert L.kappa(a, a) == pytest.approx(1.0)
    assert L.kappa(a, 1 - a) == pytest.approx(-1.0)


# ── 4г ──────────────────────────────────────────────────────────────────────────────────────────

def test_mh_or_single_stratum_equals_cross_ratio_and_woolf_ci():
    a, b, c, d = (np.array([x], dtype=float) for x in (30, 20, 10, 40))
    assert L.mh_or(a, b, c, d) == pytest.approx(30 * 40 / (20 * 10))
    orr, lo, hi, se = L.mh_or_ci(a, b, c, d)
    assert orr == pytest.approx(6.0)
    assert se == pytest.approx(math.sqrt(1 / 30 + 1 / 20 + 1 / 10 + 1 / 40))
    assert lo == pytest.approx(math.exp(math.log(6) - 1.959963984540054 * se))


def test_mh_or_two_strata_hand_value():
    a, b, c, d = (np.array(x, dtype=float) for x in ([10, 5], [4, 6], [3, 2], [8, 9]))
    n = a + b + c + d
    ref = (a * d / n).sum() / (b * c / n).sum()
    assert L.mh_or(a, b, c, d) == pytest.approx(ref)


def test_mh_removes_simpson_confound():
    # две монеты с разной базовой долей успеха и независимыми исходами: общая таблица даёт OR > 1, по слоям — около 1
    rng = np.random.default_rng(11)
    prev, cur, strat = [], [], []
    for s, p in enumerate((0.1, 0.9)):
        y = rng.random(4000) < p
        prev += list(y[:-1])
        cur += list(y[1:])
        strat += [s] * 3999
    prev, cur, strat = np.array(prev), np.array(cur), np.array(strat)
    a, b, c, d = L.mh_cells(prev, cur, np.zeros(len(strat), dtype=int), 1)
    assert L.mh_or(a, b, c, d) > 3
    a, b, c, d = L.mh_cells(prev, cur, strat, 2)
    orr, lo, hi, _ = L.mh_or_ci(a, b, c, d)
    assert lo < 1.0 < hi


def _events(n_strata, per, p, memory, seed):
    """События по слоям подряд; исход — марковская цепь: P(1 | prev=1) = p + memory, P(1 | prev=0) = p."""
    rng = np.random.default_rng(seed)
    lab, strat = [], []
    for s in range(n_strata):
        y, prev = [], 0
        for _ in range(per):
            pr = p + memory if prev else p
            prev = int(rng.random() < pr)
            y.append(prev)
        lab += y
        strat += [s] * per
    return np.array(lab, dtype=bool), np.array(strat)


def _pairs(strat):
    idx = np.arange(len(strat))
    ok = (idx > 0) & (strat == np.roll(strat, 1))
    cur = idx[ok]
    return cur - 1, cur


def test_mh_permutation_null_and_memory():
    for memory, expect_small in ((0.0, False), (0.25, True)):
        lab, strat = _events(30, 400, 0.4, memory, seed=21 + int(memory * 100))
        prev_idx, cur_idx = _pairs(strat)
        a, b, c, d = L.mh_cells(lab[prev_idx], lab[cur_idx], strat[cur_idx], 30)
        orr = L.mh_or(a, b, c, d)
        p1, p2, out = L.mh_perm_p(lab, prev_idx, cur_idx, strat[cur_idx], strat, 30, orr, 300, seed=3)
        assert len(out) == 300
        if expect_small:
            assert orr > 1.5 and p1 < 0.01
            _, lo, _, _ = L.mh_or_ci(a, b, c, d)
            assert lo > 1.0
        else:
            assert p1 > 0.02
            _, lo, hi, _ = L.mh_or_ci(a, b, c, d)
            assert lo < 1.0 < hi


def test_overdispersion_binomial_vs_heterogeneous():
    rng = np.random.default_rng(31)
    n = rng.integers(30, 80, 400)
    k_bin = rng.binomial(n, 0.4)
    od = L.overdispersion(k_bin, n)
    assert 0.85 < od["ratio"] < 1.15 and abs(od["rho_mom"]) < 0.01
    p_i = rng.beta(2.0, 3.0, 400)                        # ρ = 1/(a+b+1) = 1/6
    k_het = rng.binomial(n, p_i)
    od2 = L.overdispersion(k_het, n)
    assert od2["ratio"] > 3
    mm = L.mom_beta_binomial(k_het, n)
    assert mm["rho"] == pytest.approx(1 / 6, abs=0.04) and mm["m"] == pytest.approx(5.0, abs=1.5) and mm["p0"] == pytest.approx(0.4, abs=0.03)


def test_shrunk_share_limits():
    k, n = np.array([0, 3, 10]), np.array([0, 5, 10])
    assert L.shrunk_share(k, n, 0.4, 10.0)[0] == pytest.approx(0.4)
    assert L.shrunk_share(k, n, 0.4, 10.0)[2] == pytest.approx((10 + 4) / 20)
    assert (L.shrunk_share(k, n, 0.4, math.inf) == 0.4).all()
    assert L.shrunk_share(k, n, 0.4, 0.0)[1] == pytest.approx(0.6)


# ── 4а ──────────────────────────────────────────────────────────────────────────────────────────

def test_causal_rank_ignores_future_and_needs_min_prior():
    rng = np.random.default_rng(41)
    t = np.arange(2000)
    x = rng.normal(size=2000)
    r = L.causal_rank(t, x, min_prior=500)
    assert np.isnan(r[:500]).all() and np.isfinite(r[500:]).all()
    x2 = x.copy()
    x2[1500:] = rng.normal(5, 1, 500)                    # будущее изменено
    r2 = L.causal_rank(t, x2, min_prior=500)
    assert np.array_equal(r[:1500], r2[:1500], equal_nan=True)
    # значение ранга = доля более ранних, меньших по значению
    i = 1200
    assert r[i] == pytest.approx((x[:i] < x[i]).mean())


def test_causal_rank_ties_same_time_and_unsorted_input():
    t = np.array([5, 1, 3, 3, 2, 4, 4, 6])
    x = np.array([1.0, 9, 2, 8, 5, 7, 3, 4])
    r = L.causal_rank(t, x, min_prior=2)
    # время 4: видят строки с t < 4 → x = {9 (t=1), 5 (t=2), 2, 8 (t=3)}
    assert r[5] == pytest.approx((np.sum(np.array([9, 5, 2, 8]) < 7)) / 4)
    assert r[6] == pytest.approx((np.sum(np.array([9, 5, 2, 8]) < 3)) / 4)
    assert np.isnan(r[1])                                # t = 1: никого раньше
    nan_x = x.copy()
    nan_x[2] = np.nan
    r3 = L.causal_rank(t, nan_x, min_prior=2)
    assert r3[5] == pytest.approx((np.sum(np.array([9, 5, 8]) < 7)) / 3)


# ── 4в: отчёт по меткам ─────────────────────────────────────────────────────────────────────────

def test_labels_report_cells_and_kappa():
    try:
        import pandas as pd
        import tk728_labels_report as R
    except ValueError:                                   # сломанный бинарный pandas на ПК (numpy) — тест идёт на srv
        pytest.skip("pandas не импортируется")
    cell = np.array([3, 3, 0, 1, 2, 0, 3, 0], dtype=np.int8)       # y,y_n: 11, 11, 00, 01, 10, 00, 11, 00
    day = np.array([0, 0, 1, 1, 2, 2, 3, 3])
    cd = R.day_counts(day, cell, 4)
    assert cd.sum(axis=0).tolist() == [3, 1, 1, 3]
    n, y, yn, agree, kap = R.stats_from_cells(cd.sum(axis=0))
    assert n == 8 and y == pytest.approx(4 / 8) and yn == pytest.approx(4 / 8) and agree == pytest.approx(6 / 8)
    a = np.array([1, 1, 0, 0, 1, 0, 1, 0]); b = np.array([1, 1, 0, 1, 0, 0, 1, 0])
    assert kap == pytest.approx(L.kappa(a, b))
    # сравнение с эталоном: общие эпизоды, расхождение и «только у одного»
    key = dict(side=["long"] * 3, sym=["A"] * 3, level=[1, 2, 3], sig_ms=[10, 20, 30])
    mine = pd.DataFrame(dict(key, y=[1.0, 0.0, 1.0]))
    ref = pd.DataFrame(dict(key, y=[1.0, 1.0, np.nan]))
    assert R.compare(mine, ref, "y") == (2, 1, 1, 0)


# ── 4е / статусы ────────────────────────────────────────────────────────────────────────────────

def test_status_priority_and_time_set(tmp_path):
    import json
    import tk728_status as S
    feats = lambda ns: [dict(n=i + 1, name=nm, what="что значит", src=["trades"], base="—", search="нет") for i, nm in enumerate(ns)]
    d = dict(updated="24.11.2026", intro="x", foot="x", sources={"trades": "сделки"},
             sets=[dict(id="a", title="A", short="A", task="TK-1", status="done", status_text="готово", about="x", numbers=[], reports=[], protocol="p", groups=[dict(title="", features=feats(["IMP", "x1", "x2", "bk20", "oi_c1"]))])])
    cl = dict(features=[dict(n=2, name="x1", is_rep=1, rep_n=2, cluster=1, rep_name="x1"), dict(n=3, name="x2", is_rep=0, rep_n=2, cluster=1, rep_name="x1"),
                        dict(n=1, name="IMP", is_rep=0, rep_n=2, cluster=1, rep_name="x1")])
    (tmp_path / "cluster.json").write_text(json.dumps(cl), encoding="utf-8")
    (tmp_path / "events.json").write_text(json.dumps(dict(rule_memory=False, main=dict(or_mh=0.945, or_lo=0.9, perm_or_mean=0.835))), encoding="utf-8")
    (tmp_path / "oi.json").write_text(json.dumps(dict(rule_triggered=[])), encoding="utf-8")
    (tmp_path / "causal.json").write_text(json.dumps(dict(lookahead={"IMP": "ранг по окну"})), encoding="utf-8")
    assert S.add_time_set(d) and not S.add_time_set(d)
    nums = [f["n"] for s in d["sets"] for g in s["groups"] for f in g["features"]]
    assert nums == list(range(1, 12)) and d["sets"][-1]["id"] == "time" and "signal" in d["sources"]
    out, summ = S.decide(d, str(tmp_path))
    assert out["IMP"][0] == "с заглядыванием"          # заглядывание сильнее «копии»
    assert out["x1"][0] == "в работе" and out["x2"][0] == "копия № 2"
    assert out["bk20"][0] == "в работе" and "L-521" in out["bk20"][1] and out["oi_c1"][0] == "в работе"     # поправка L-521: семейство истории не убирается
