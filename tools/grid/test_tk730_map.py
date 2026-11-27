"""TK-730 §6.4: синтетические проверки журнала лотов ядра и срезов карты денег (час, день недели, фондинг, возраст, трети, перестановка) — до чтения результатов."""
from __future__ import annotations

import datetime
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402
import tk730_map as M  # noqa: E402
from test_tk729 import MK, fl, mk, run  # noqa: E402
from test_tk730_pm import _decline_path, _P  # noqa: E402


def _led_run(S, nd, P, **kw):
    out, led, tot = L.run_coin(S, dict(rv60=np.full(nd * 1440, 0.01), tick=np.full(nd, 0.001)), P, ledger=5000, **kw)
    return out, led, tot


def test_ledger_does_not_change_results_and_sums_match_rows():
    nd = 2
    t = np.arange(nd * 1440)
    c = np.round(100 + 3.5 * np.sin(2 * np.pi * t / 720), 3)
    S = mk(c, nd)
    P = L.make_params(g0=0.01, day0=0, t_start=0)
    base = run(S, nd, P)
    out, led, tot = _led_run(S, nd, P)
    assert np.array_equal(out, base) and tot == len(led)
    assert led[:, 2].sum() == pytest.approx(out[L.R_CYC].sum() + out[L.R_EXIT].sum(), abs=1e-9)
    assert int((led[:, 4] == 0).sum()) == int(out[L.R_NCYC].sum()) and set(np.unique(led[:, 3])) == {-1.0, 1.0}
    assert (led[:, 1] > led[:, 0]).all()


def test_ledger_forced_exits_edge_timex_and_pm4_sum_to_rows():
    nd = 2
    c = fl(np.round(100 - 0.02 * np.arange(502), 3), nd * 1440)  # тренд вниз: выход за край
    S = mk(c, nd)
    out, led, _ = _led_run(S, nd, L.make_params(g0=0.01, day0=0, t_start=0))
    assert out[L.R_NEDGE].sum() > 0 and (led[:, 4] == 1).sum() >= 1
    assert led[:, 2].sum() == pytest.approx(out[L.R_CYC].sum() + out[L.R_EXIT].sum(), abs=1e-9)
    # выход по времени
    t = np.arange(nd * 1440)
    c2 = np.round(100 + 2.0 * np.sin(2 * np.pi * t / 5000), 3)
    S2 = mk(c2, nd)
    out2, led2, _ = _led_run(S2, nd, L.make_params(g0=0.01, day0=0, t_start=0, timex=60))
    assert out2[L.R_NTIME].sum() > 0 and led2[:, 2].sum() == pytest.approx(out2[L.R_CYC].sum() + out2[L.R_EXIT].sum(), abs=1e-9)
    # лимит убытка
    c3 = _decline_path(nd, [(100, 400, 96.0)])
    S3 = mk(c3, nd)
    out3, led3, _ = _led_run(S3, nd, _P(0.5))
    assert out3[L.R_NREG].sum() == 1 and led3[:, 2].sum() == pytest.approx(out3[L.R_CYC].sum() + out3[L.R_EXIT].sum(), abs=1e-9)


def test_ledger_known_lot_minutes_and_pnl():
    nd = 1
    n = nd * 1440
    c = np.full(n, 100.0)
    c[100:200] = 98.9
    c[200:] = 100.1
    S = mk(np.round(c, 3), nd)
    out, led, _ = _led_run(S, nd, L.make_params(g0=0.01, day0=0, t_start=0))
    assert len(led) == 1
    o, cl, pnl, side, kind = led[0]
    assert (o, cl, side, kind) == (100.0, 200.0, 1.0, 0.0)
    assert pnl == pytest.approx(25 * (100.0 / 99.0 - 1) - 25 * MK - 25 * (100.0 / 99.0) * MK, abs=1e-9)


def test_ledger_overflow_counts_but_does_not_crash():
    nd = 2
    t = np.arange(nd * 1440)
    S = mk(np.round(100 + 3.5 * np.sin(2 * np.pi * t / 720), 3), nd)
    out, led, tot = L.run_coin(S, dict(rv60=np.full(nd * 1440, 0.01), tick=np.full(nd, 0.001)), L.make_params(g0=0.01, day0=0, t_start=0), ledger=3)
    assert len(led) == 3 and tot > 3


# ───────────── срезы ─────────────

def test_hour_weekday_match_calendar():
    rng = np.random.default_rng(3)
    ts = rng.integers(0, 348 * 1440, size=200)
    for t in ts:
        dt = datetime.datetime(2025, 12, 12) + datetime.timedelta(minutes=int(t))
        assert M.weekday_of(np.array([t]))[0] == dt.weekday() and M.hour_of(np.array([t]))[0] == dt.hour


def test_in_funding_window():
    m = np.array([0, 29, 30, 31, 1409, 1410, 449, 450, 510, 511, 949, 960, 990, 991, 700])
    exp = [1, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1, 1, 0, 0]
    assert in_list(M.in_funding(m)) == exp
    assert in_list(M.in_funding(m + 1440 * 5)) == exp  # не зависит от суток


def in_list(a):
    return [int(x) for x in a]


def test_age_bin():
    f = 5000
    t = np.array([f, f + 89 * 1440, f + 90 * 1440, f + 364 * 1440, f + 365 * 1440])
    assert M.age_bin(t, f).tolist() == [1, 1, 2, 2, 3]
    assert M.age_bin(np.array([10, 90000]), 0).tolist() == [0, 0]  # первая свеча в первые сутки кэша → «до кэша»


def test_half_of_boundaries():
    def mn(d):
        return (L.DAY0 + d) * 1440 + 5
    h = M.half_of(np.array([mn(19), mn(20), mn(180), mn(181), mn(327), mn(328)]))
    assert h.tolist() == [-1, 0, 0, 1, 1, -1]


def test_terciles_and_slice_table():
    x = np.array([1.0, 2, 3, 4, 5, 6, 7, 8, 9, np.nan])
    assert M.terciles(x).tolist() == [0, 0, 0, 1, 1, 1, 2, 2, 2, -1]
    led = np.array([[100, 110, 2.0, 1, 0], [100 + 1440 * 300, 0, -1.0, 1, 0], [100 + 1440 * 100, 0, 5.0, 1, 0]])
    led[:, 0] += 1440 * L.DAY0
    half = M.half_of(led[:, 0])
    assert half.tolist() == [-1, 1, 0] or half.tolist() == [-1, 1, 0]
    tb = M.slice_table(led, np.array([0, 0, 1]), 2, half)
    assert tb[1, 0].tolist() == [-1.0, 1, 0] and tb[0, 1].tolist() == [5.0, 1, 1] and tb[0, 0].tolist() == [0, 0, 0]


def test_perm_both_positive_null_and_signal():
    rng = np.random.default_rng(5)
    a = rng.normal(size=300)
    indep = M.perm_both_positive(a, rng.normal(size=300), nperm=2000)
    assert indep["p"] > 0.05 and indep["null_lo"] <= indep["obs"] <= indep["null_hi"] + 5
    corr = M.perm_both_positive(a, a + rng.normal(size=300) * 0.3, nperm=2000)
    assert corr["p"] < 0.01 and corr["obs"] > corr["null_hi"]
    z = M.perm_both_positive(np.array([1.0, 0.0, 2.0, -1.0]), np.array([1.0, 3.0, 0.0, -2.0]), nperm=200)
    assert z["n"] == 2 and z["obs"] == 1  # монеты без торговли в какой-то половине не входят
