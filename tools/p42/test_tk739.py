"""Тесты tk739 (TK-739): группы / разбиения / пути CPCV, процедура Π на синтетике, правило S, составные метки G-cov. Синтетика, без данных srv."""
import itertools
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk739_proc as P  # noqa: E402
import tk739_ingest as I  # noqa: E402
import tk739_cov as cv  # noqa: E402


def test_groups_and_splits():
    G = P.groups()
    assert len(G) == 10 and sum(len(g) for g in G) == 286
    assert np.array_equal(np.concatenate(G), np.arange(286))
    spl = P.splits()
    assert len(spl) == 45
    for i, j, tr, te in spl:
        assert len(set(tr) & set(te)) == 0
        for g in (G[i], G[j]):                                    # purge 2 суток с каждой стороны тестовой группы
            lo, hi = g[0], g[-1]
            assert not ({d for d in range(max(0, lo - 2), lo)} & set(tr))
            assert not ({d for d in range(hi + 1, min(286, hi + 3))} & set(tr))


def test_cpcv_paths_cover_each_group_once():
    ps = P.paths()
    assert len(ps) == 9
    seen = set()
    for p in ps:
        assert sorted(g for pair in p for g in pair) == list(range(10))
        seen |= set(p)
    assert seen == set(itertools.combinations(range(10), 2))


def test_cprime_positive_for_strong_series_and_negative_for_noise():
    rng = np.random.default_rng(1)
    strong = 1.0 + 0.5 * rng.standard_normal(286)
    assert P.cprime(strong, 7, B=2000)[0] > 0
    zero = rng.standard_normal(286) * 3
    assert P.cprime(zero - zero.mean(), 7, B=2000)[0] < 0


def _toy(n=300, p=8, signal=True, seed=3):
    rng = np.random.default_rng(seed)
    F = rng.uniform(-1, 1, size=(n, p))
    F[:, -1] = 1.0                                                  # константный столбец — отбрасывается
    mu = (2.0 * F[:, 0] + 1.0) if signal else np.zeros(n)         # линейная поверхность, максимум у F0 = 1
    X = mu[:, None] + 3.0 * rng.standard_normal((n, 286))
    cnt = np.full((n, 286), 2.0)
    base = np.zeros(286)
    NB = np.zeros((n, 4, p), np.float32)
    msk = np.ones((n, 4), bool)
    for q, d in enumerate((-0.1, 0.1, -0.05, 0.05)):
        NB[:, q] = F
        NB[:, q, 0] = F[:, 0] + d
    return X, cnt, base, F, NB, msk


def test_select_finds_edge_of_planted_surface():
    X, cnt, base, F, NB, msk = _toy(signal=True)
    cand_ok = np.ones(len(X), bool)
    r = P.select(np.arange(286), X, cnt, base, F, NB, msk, nboot=100, cand_ok=cand_ok)
    assert r["c"] is not None and F[r["c"], 0] > 0.7                  # выбирает точку у максимума линейной поверхности


def test_select_refuses_when_trades_missing():
    X, cnt, base, F, NB, msk = _toy()
    cnt[:] = 0.0
    assert P.select(np.arange(286), X, cnt, base, F, NB, msk, cand_ok=np.ones(len(X), bool))["c"] is None


def test_nested_pbo_range_and_refusal_zero_path():
    X, cnt, base, F, NB, msk = _toy(n=120, signal=False)
    r = P.nested(X, cnt, base, np.full(286, 2.0), F, NB, msk, np.ones(len(X), bool), 100, nboot=30)
    assert 0.0 <= r["pbo"] <= 1.0 and r["test_delta"].shape == (45, 286)
    for s, c in enumerate(r["choices"]):
        if c is None:
            assert not r["test_delta"][s].any()                        # отказ → Δ пути 0


def test_rule_s_ladder():
    # c_pt так, чтобы S = 2048 влезало, 4096 — нет
    t_max = I.T_MAX_S
    c_pt = t_max / (2048 + I.EXTRA) * 0.99
    got = next((s for s in I.S_LADDER if (s + I.EXTRA) * c_pt <= t_max), None)
    assert got == 2048
    assert next((s for s in I.S_LADDER if (s + I.EXTRA) * (t_max / 100.0) <= t_max), None) is None     # даже 512 не влезает → O6-цена


def test_expand_composite_axis_labels():
    assert cv.expand_axes({"nf": "natr_min=0.19,nf=1"}) == {"natr_min": "0.19", "nf": "1"}
    assert cv.expand_axes({"k": "3", "F:sharp": ""}) == {"k": "3", "F:sharp": ""}
    assert cv.expand_axes({"x": "a=1,b"}) is None
