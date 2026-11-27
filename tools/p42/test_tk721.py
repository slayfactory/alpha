"""TK-721: тесты чистых функций стресса B (tk721_stress.py, tk721_cells.py)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk721_cells as cl  # noqa: E402
import tk721_fwd as LF  # noqa: E402
import tk721_stress as S  # noqa: E402


def test_month_bounds_cover_feb_nov():
    mb = S.month_bounds()
    assert len(mb) == 10 and mb[0][1] == 31 and mb[-1][2] == 328
    assert all(mb[i][2] == mb[i + 1][1] for i in range(9))
    assert sum(b - a for _, a, b in mb) == S.ND == 297
    assert dict((n, b - a) for n, a, b in mb)["2026-11"] == 24


def test_windows():
    assert S.SEL == (150, 286) and S.SEL[1] - S.SEL[0] == 136 and S.FEBNOV == (0, 297)


def test_daily_by_entry_day():
    day = np.array([31, 31, 33, 327, 30, 328])                  # 30 и 328 — вне окна
    val = np.array([9.0, 9.0, 18.0, 9.0, 99.0, 99.0])
    p, n = S.daily(day, val)
    assert p[0] == 2.0 and p[2] == 2.0 and p[296] == 1.0 and p.sum() == 5.0
    assert n[0] == 2 and n.sum() == 4


def test_holm_and_binom():
    adj = S.holm([0.001, 0.04, 0.03, 0.5, 0.2, 0.01])
    assert abs(adj[0] - 0.006) < 1e-12 and adj[5] == max(0.006, 5 * 0.01) and abs(adj[3] - 0.5) < 1e-12
    assert all(0 <= a <= 1 for a in adj)
    assert abs(S.binom_p(7) - 176 / 1024) < 1e-12 and S.binom_p(0) == 1.0 and S.binom_p(10) == 1 / 1024


def test_split_coins_reproducible_and_disjoint():
    coins = ["C%03d" % i for i in range(101)]
    a1, b1 = S.split_coins(coins)
    a2, b2 = S.split_coins(list(reversed(coins)))
    assert a1 == a2 and b1 == b2 and not (a1 & b1) and len(a1) == 50 and len(b1) == 51 and a1 | b1 == set(coins)


def test_uw_extras():
    x = np.array([1.0, -2.0, -1.0, 4.0, -1.0])
    u = S.uw(x)
    assert u["maxdd"] == 3.0 and u["gap_longest"] == 3 and abs(u["under"] - 3 / 5) < 1e-12
    assert u["gap_p90"] >= 1


def test_stats_zero_copies():
    s = S.stats(np.zeros(3), np.zeros(3, int))
    assert s["sum"] == 0 and s["copies"] == 0 and np.isnan(s["per_copy"])


def test_posthoc_funding_strict_and_sign():
    ts = np.array([1000, 2000, 3000], np.int64)
    rate = np.array([0.001, 0.002, -0.004])
    # вход строго раньше выплаты, выход строго позже: (500, 2500) ловит 1000 и 2000; (1000, 2000) — ни одной
    f = S.posthoc_funding([500, 1000, 3500], [2500, 2000, 4000], ts, rate, notional=500.0)
    assert abs(f[0] - 0.003 * 500) < 1e-9 and f[1] == 0.0 and f[2] == 0.0
    assert S.posthoc_funding([0], [10], np.array([], np.int64), np.array([]))[0] == 0.0


def test_boot_idx_paired_and_p():
    i1, i2 = S.boot_idx(136), S.boot_idx(136)
    assert (i1 == i2).all() and i1.shape == (S.NBOOT, 136)
    assert S.p_le0(np.array([-1.0, 0.0, 1.0, 2.0])) == (2 + 1) / (4 + 1)


def test_cells_with_slip_and_rows():
    line = "lat=4 latx=4 pk=1 s=4 tf=30"
    ck = "bm=1 lat=4 latx=4 s=4 t24=12 tf=30"
    l2, c2 = cl.with_slip(line, ck, 2)
    assert l2.endswith(" slip=2") and "s=4 slip=2 t24=12" in c2
    src = [("tk719b", 7180015 + k, "short", ck, line, "[]", "[]", "", 0, 0.0, None, 0, "bin") for k in range(9)]
    rows = cl.rows_from(src)
    assert len(rows) == 27 and all(len(r) == 13 for r in rows)
    assert {r[0] for r in rows} == {"tk721g", "tk721s"} and {r[1] for r in rows if r[0] == "tk721s"} == {7210100 + k for k in range(9)} | {7210200 + k for k in range(9)}


def test_long_cells_rows():
    src = [("tk718core", 7180006 + k, "long", "ck%d" % k, "line%d" % k, "[]", "[]", "", 0, 0.0, None, 0, "bin") for k in range(9)]
    rows = cl.rows_long(src)
    assert len(rows) == 9 and all(len(r) == 13 for r in rows)
    assert [r[1] for r in rows] == [7210300 + k for k in range(9)] and {r[0] for r in rows} == {"tk721l"}
    assert [r[4] for r in rows] == ["line%d" % k for k in range(9)] and all(r[2] == "long" for r in rows)


def test_long_ids_consistent():
    assert LF.LCIDS == tuple(range(7180006, 7180015)) and LF.MCIDS == tuple(range(7210300, 7210309))
    assert len(LF.LCIDS) == len(LF.MCIDS) == 9


def test_daily_member_window_and_counts():
    day = np.array([181, 181, 183, 316, 180, 317])               # 180 и 317 — вне окна 181…316
    val = np.array([1.0, 2.0, 4.0, 8.0, 99.0, 99.0])
    p, n = LF.daily_member(day, val, 181, 317)
    assert len(p) == len(n) == 136 and p[0] == 3.0 and p[2] == 4.0 and p[135] == 8.0 and p.sum() == 15.0
    assert n[0] == 2 and n.sum() == 4


def test_gate_series_tolerance():
    a = np.array([1.0, 2.0, 3.0])
    assert LF.gate_series(a, a + 1e-12, [1, 0, 2], [1, 0, 2])[0]
    assert not LF.gate_series(a, a + 1e-6, [1, 0, 2], [1, 0, 2])[0]
    assert not LF.gate_series(a, a, [1, 0, 2], [1, 1, 2])[0]


def test_compare_by_k():
    t = lambda sym, g: dict(sym=sym, entry_ms="10", exit_ms="20", gross=str(g), fees="0.1")   # noqa: E731
    ok, on, orf = LF.compare_by_k([[t("A", 1.0)], [t("B", 2.0)]], [[t("A", 1.0)], [t("B", 2.0)]])
    assert ok and not on and not orf
    ok, on, orf = LF.compare_by_k([[t("A", 1.0)], [t("B", 2.0)]], [[t("A", 1.0)], [t("B", 2.5)]])
    assert not ok and len(on) == 1 and len(orf) == 1 and on[0][0] == 1


def test_window_stats_keys_and_values():
    p = np.zeros(328)
    p[181:317] = 1.0
    n = np.full(328, 2, np.int64)
    st = LF.window_stats(p, n, LF.WINDOWS)
    assert st["SEL"]["days"] == 136 and abs(st["SEL"]["sum"] - 136.0) < 1e-9 and st["SEL"]["trades"] == 272 and abs(st["SEL"]["per_day"] - 1.0) < 1e-12
    assert st["A"]["sum"] == 0.0 and st["FJ"]["days"] == 150
    p[100] = np.nan
    try:
        LF.window_stats(p, n, LF.WINDOWS)
    except AssertionError:
        pass
    else:
        raise AssertionError("NaN в окне должен падать")
