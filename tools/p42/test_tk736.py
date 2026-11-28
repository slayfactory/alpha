"""Тесты tk736_lib (TK-736): ход против позиции, k, реплей, G_max, C1 / C2 / C3, исходы. Синтетика, без данных srv."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk736_lib as L  # noqa: E402


def test_adverse_short_excludes_exit_candle_except_stop():
    o = np.array([1, 1, 1, 1], np.int64)
    h = np.array([100, 103, 110, 150], np.int64)
    l = np.array([90, 95, 80, 60], np.int64)
    assert abs(L.adverse_move("short", h, l, o, 0, 2, False, 100) - 0.03) < 1e-12      # 110 (свеча выхода) не входит
    assert abs(L.adverse_move("short", h, l, o, 0, 2, True, 100) - 0.10) < 1e-12       # stop: входит
    assert abs(L.adverse_move("long", h, l, o, 0, 3, False, 100) - 0.20) < 1e-12       # лонг: низ 80, свеча 3 не входит
    assert L.adverse_move("short", h, l, o, 2, 2, False, 100) == 0.0                   # пусто → 0


def test_estimate_k_and_undefined():
    x = np.linspace(0.1, 5.0, 101)
    assert L.estimate_k(x) == round(float(np.quantile(x, 0.95)), 2)
    assert L.estimate_k(x[:19]) is None


def test_replay_and_gmax():
    r = np.array([5.0, -12.0, -3.0])
    fees = np.array([0.3, 0.3, 0.3])
    a = np.array([0.001, 0.03, 0.001])
    V = np.array([0.004, 0.004, -1.0])
    out, cut = L.replay(r, fees, a, V, k=3.0, s=0.04)             # d = 0.012; сделка 1: a ≥ d → срез
    assert cut.tolist() == [False, True, False]
    assert abs(out[1] - (-500 * (0.012 + 0.0001) - 0.3)) < 1e-9 and out[0] == 5.0 and out[2] == -3.0   # V = −1 → d = s, нет среза
    g = L.gmax(r, fees)                                            # R = 500·0,0051 + 0,3 = 2,85
    assert abs(g - ((12.0 - 2.85) + (3.0 - 2.85))) < 1e-9


def test_c1_pass_fail_unclear():
    rng = np.random.default_rng(1)
    up = rng.normal(2.0, 1.0, 140)
    assert L.c1(up, sigma0=100.0, nboot=2000)["verdict"] == "pass"
    down = rng.normal(-3.0, 1.0, 140)
    assert L.c1(down, sigma0=100.0, nboot=2000)["verdict"] == "fail"
    assert L.mde_c1(rng.normal(0, 1.0, 140), 100.0, nboot=2000) > -10.0


def test_c2_lower_sigma_gives_small_p():
    rng = np.random.default_rng(2)
    nd, nper = 150, 5
    lab = np.repeat(np.arange(nper), nd // nper)
    n = np.full(nd, 4.0)
    base = rng.normal(0, 1, nd) + np.repeat(np.array([-12.0, 6.0, 15.0, -9.0, 3.0]), nd // nper)     # большой разброс между месяцами
    flat = rng.normal(0, 1, nd) + 2.0
    res = L.c2_p(flat, n, base, n, lab, nper, nboot=2000)
    assert res["p"] < 0.05 and res["sigma2"] < res["sigma0"]
    assert L.c2_p(base, n, flat, n, lab, nper, nboot=2000)["p"] > 0.5


def test_c3_pairs():
    k0, k2 = [("c", "A", 1, 1), ("c", "B", 2, 1)], [("c", "A", 1, 1), ("c", "Z", 9, 1)]
    ok = L.c3([-8.0, -9.0], [-7.0, -50.0], k0, k2, ["stop", "x"], [True, False], ["stop", "x"])
    assert ok["pairs"] == 1 and ok["a_ok"] and ok["ok"]                      # Z — только в V2′: в проверку не входит
    bad = L.c3([-8.0, -9.0], [-9.0, -1.0], k0, k2, ["stop", "x"], [True, False], ["stop", "x"])
    assert not bad["a_ok"] and not bad["ok"]


def test_outcomes():
    a = dict(c1="pass", c2p=0.01)
    assert L.outcome(1.0, 1.1, a, a, 0.02, True) == "O1"
    assert L.outcome(1.0, 2.0, a, a, 0.02, True) == "O3"                    # k не переносится
    assert L.outcome(1.0, 1.1, dict(c1="fail", c2p=0.5), a, 0.02, True) == "O2"
    assert L.outcome(1.0, 1.1, a, a, 0.02, False) == "O5"
    assert L.outcome(None, 1.1, a, a, 0.02, True) == "O4"
    assert L.outcome(1.0, 1.1, a, a, 0.02, True, powered=False) == "O6"


def test_dedup_first():
    m = L.dedup_first(["A", "A", "B"], [1, 1, 1], [2, 2, 2], [3, 3, 3], [1.5, 1.5, 1.5])
    assert m.tolist() == [True, False, True]
