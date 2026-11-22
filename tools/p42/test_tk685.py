#!/usr/bin/env python3
"""Тесты TK-685 (§13.76) на синтетике: группы, путь по ленте, AUC против прямого пересчёта, шумы, порог, дерево, присоединение, загрузка набора (G-ref).
Запуск: python3 -m unittest tools/p42/test_tk685.py (нужны numpy, pandas — на srv `/data/tk592/venv/bin/python`)."""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk685_lib as lib  # noqa: E402


def brute_auc(x, y):
    """P(x у метки 1 > x у метки 0) + 0,5 · P(равны), только не-NaN."""
    ok = ~np.isnan(x)
    a, b = x[ok & (y == 1)], x[ok & (y == 0)]
    if len(a) == 0 or len(b) == 0:
        return float("nan")
    gt = (a[:, None] > b[None, :]).sum()
    eq = (a[:, None] == b[None, :]).sum()
    return float((gt + 0.5 * eq) / (len(a) * len(b)))


class Groups(unittest.TestCase):
    def test_net_band_symmetric(self):
        net = np.array([-1.0, -0.31, -0.30, 0.0, 0.30, 0.31, 2.0])
        fees = np.full(7, 0.30)
        g = lib.groups(net, fees)
        self.assertEqual(list(g), [lib.LOSS, lib.LOSS, lib.ZERO, lib.ZERO, lib.ZERO, lib.PROFIT, lib.PROFIT])

    def test_gross_rule_is_asymmetric(self):
        gross = np.array([-0.5, -0.2, 0.2, 0.35, 0.5])
        fees = np.full(5, 0.30)           # net = gross − fees = −0.8, −0.5, −0.1, +0.05, +0.2
        self.assertEqual(list(lib.groups_gross(gross, fees)), [lib.LOSS, lib.ZERO, lib.ZERO, lib.PROFIT, lib.PROFIT])
        self.assertEqual(list(lib.groups(gross - fees, fees)), [lib.LOSS, lib.LOSS, lib.ZERO, lib.ZERO, lib.ZERO])

    def test_halves(self):
        self.assertEqual(list(lib.halves([0, 67, 68, 135])), [1, 1, 2, 2])


class Path(unittest.TestCase):
    def setUp(self):
        self.p0 = 100_00000000
        self.t_in = 1_000_000

    def ticks(self, rel_ms, px):
        return np.array([self.t_in + m for m in rel_ms], np.int64), np.array([int(x * 1e8) for x in px], np.int64)

    def test_long_up_then_down(self):
        t, p = self.ticks([1000, 60_000, 180_000, 300_000], [101.0, 102.0, 99.0, 98.0])     # +1 %, +2 %, −1 %, −2 % от входа
        r = lib.path_one(t, p, self.t_in, self.t_in + 300_000, self.p0, +1, 0.3)
        self.assertAlmostEqual(r["mfe"], 10.0)                    # 2 % · 500
        self.assertAlmostEqual(r["mae"], 10.0)
        self.assertAlmostEqual(r["t_mfe"], 1.0)
        self.assertAlmostEqual(r["t_mae"], 5.0)
        self.assertTrue(r["green"] and r["green_first"])
        self.assertAlmostEqual(r["exit_move"], -10.0)
        self.assertAlmostEqual(r["giveback"], 20.0)
        self.assertAlmostEqual(r["giveback_share"], 2.0)
        self.assertAlmostEqual(r["mae_to_exit_min"], 0.0)
        self.assertEqual(r["mfe_k"][CHK(1)], 10.0)               # на 1-й минуте уже +2 %
        self.assertEqual(r["mae_k"][CHK(5)], 10.0)

    def test_checkpoint_mae(self):
        t, p = self.ticks([1000, 60_000, 180_000, 300_000], [101.0, 102.0, 99.0, 98.0])
        r = lib.path_one(t, p, self.t_in, self.t_in + 300_000, self.p0, +1, 0.3)
        self.assertEqual(r["mae_k"][CHK(1)], 0.0)
        self.assertEqual(r["mae_k"][CHK(2)], 0.0)
        self.assertEqual(r["mae_k"][CHK(3)], 5.0)                # 180 с: 99 → −1 % → 5 $
        self.assertEqual(r["mae_k"][CHK(60)], 10.0)              # заморожено после выхода

    def test_short_sign_and_red_first(self):
        t, p = self.ticks([1000, 2000, 9000], [100.1, 99.0, 98.0])    # шорт: сначала против (−0,1 % = 0,5 $), потом в пользу
        r = lib.path_one(t, p, self.t_in, self.t_in + 9000, self.p0, -1, 0.3)
        self.assertAlmostEqual(r["mae"], 0.5)
        self.assertAlmostEqual(r["mfe"], 10.0)
        self.assertTrue(r["green"])
        self.assertFalse(r["green_first"])                         # −fav ≥ 0,3 раньше, чем fav ≥ 0,3

    def test_never_green(self):
        t, p = self.ticks([500, 800], [99.9, 99.8])                # лонг только вниз
        r = lib.path_one(t, p, self.t_in, self.t_in + 800, self.p0, +1, 0.3)
        self.assertEqual(r["mfe"], 0.0)
        self.assertFalse(r["green"] or r["green_first"])
        self.assertTrue(np.isnan(r["giveback_share"]))
        self.assertAlmostEqual(r["mae"], 1.0)

    def test_empty_tape(self):
        r = lib.path_one([], [], self.t_in, self.t_in + 1000, self.p0, +1, 0.3)
        self.assertEqual(r["ticks"], 0)
        self.assertTrue(np.isnan(r["mfe"]))

    def test_entry_in_range(self):
        t = np.array([self.t_in - 500, self.t_in + 100, self.t_in + 5000], np.int64)
        p = np.array([int(99.9e8), int(100.2e8), int(150e8)], np.int64)
        self.assertTrue(lib.entry_in_range(t, p, self.t_in, self.p0))
        self.assertFalse(lib.entry_in_range(t, p, self.t_in, int(101e8)))
        self.assertFalse(lib.entry_in_range(t, p, self.t_in + 100_000, self.p0))

    def test_entry_dist_bp(self):
        t = np.array([self.t_in - 500, self.t_in + 100], np.int64)
        p = np.array([int(99.9e8), int(100.2e8)], np.int64)
        self.assertEqual(lib.entry_dist_bp(t, p, self.t_in, self.p0), 0.0)
        self.assertAlmostEqual(lib.entry_dist_bp(t, p, self.t_in, int(100.3e8)), (100.3 - 100.2) / 100.3 * 1e4, places=6)   # выше диапазона
        self.assertAlmostEqual(lib.entry_dist_bp(t, p, self.t_in, int(99.8e8)), (99.9 - 99.8) / 99.8 * 1e4, places=6)       # ниже диапазона
        self.assertTrue(np.isnan(lib.entry_dist_bp(t, p, self.t_in + 100_000, self.p0)))


def CHK(m):
    return lib.CHECK_MIN.index(m)


class Metrics(unittest.TestCase):
    def test_uw_and_set_metrics(self):
        r = lib.uw([1, 1, -1, -1, 1, 1, 3, -2, 0, 0])
        self.assertEqual((r["highs"], r["gap_longest"], r["tail"]), (3, 5, 3))
        self.assertAlmostEqual(r["maxdd"], 2.0)
        pnl = [10.0, -5.0, 5.0, -5.0]
        m = lib.set_metrics([0, 1, 2, 3], pnl, nd=4)
        self.assertEqual(m["n"], 4)
        self.assertAlmostEqual(m["sum"], 5.0)
        self.assertAlmostEqual(m["rr"], 7.5 / 5.0)
        self.assertAlmostEqual(m["maxdd"], 5.0)
        self.assertAlmostEqual(m["maxdd_pct"], 2.0)

    def test_day_out_clipped(self):
        m = lib.set_metrics([0, 136, 200], [1.0, 1.0, 1.0], nd=136)
        self.assertAlmostEqual(m["sum"], 3.0)


class Auc(unittest.TestCase):
    def test_matches_brute_force_with_nan_and_ties(self):
        rng = np.random.default_rng(1)
        n, F = 60, 5
        X = rng.integers(0, 6, size=(n, F)).astype(float)       # много ничьих
        X[rng.random((n, F)) < 0.2] = np.nan
        y = (rng.random(n) < 0.4).astype(float)
        got = lib.auc_all(X, y)
        for j in range(F):
            self.assertAlmostEqual(got[j], brute_auc(X[:, j], y), places=12)

    def test_perfect_and_inverse(self):
        x = np.arange(10, dtype=float)[:, None]
        y = np.array([0] * 5 + [1] * 5, float)
        self.assertAlmostEqual(lib.auc_all(x, y)[0], 1.0)
        self.assertAlmostEqual(lib.auc_all(-x, y)[0], 0.0)

    def test_empty_side_is_nan(self):
        self.assertTrue(np.isnan(lib.auc_all(np.arange(4.0)[:, None], np.zeros(4))[0]))

    def test_batch_equals_single(self):
        rng = np.random.default_rng(3)
        X = rng.normal(size=(40, 4))
        Y = (rng.random((6, 40)) < 0.5).astype(float)
        R, V = lib.rank_matrix(X)
        A = lib.auc_from_ranks(Y, R, V)
        for b in range(6):
            for j in range(4):
                self.assertAlmostEqual(A[b, j], brute_auc(X[:, j], Y[b]), places=12)


class Nulls(unittest.TestCase):
    def test_null_labels_keep_counts(self):
        rng = np.random.default_rng(5)
        y = (np.arange(100) % 4 == 0).astype(float)
        for kind in ("N1", "N2"):
            Y = lib.null_labels(y, 50, kind, rng)
            self.assertTrue(np.all(Y.sum(1) == y.sum()))

    def test_shift_respects_min_shift(self):
        rng = np.random.default_rng(6)
        y = np.arange(40, dtype=float)
        Y = lib.null_labels(y, 100, "N2", rng)
        k = (np.argmax(Y == 0, axis=1))                              # позиция нуля = величина сдвига
        self.assertTrue(np.all((k >= 10) & (k <= 30)))

    def test_family_threshold_bounds_noise(self):
        rng = np.random.default_rng(7)
        n, F = 300, 40
        X = rng.normal(size=(n, F))
        y = (rng.random(n) < 0.5).astype(float)
        R, V = lib.rank_matrix(X)
        A = lib.null_auc(R, V, y, 400, "N1", rng, chunk=100)
        T = lib.family_threshold(A, 0.05)
        self.assertTrue(0.03 < T < 0.2)
        self.assertGreater(lib.family_threshold(A, 0.01), T)          # строже α → выше порог
        fresh = lib.auc_all(X, y)
        self.assertLess(np.nanmax(np.abs(fresh - 0.5)), 0.2)

    def test_norm_ppf(self):
        self.assertAlmostEqual(lib.norm_ppf(0.975), 1.959963984540054, places=6)
        self.assertAlmostEqual(lib.norm_ppf(0.5), 0.0, places=9)
        self.assertAlmostEqual(lib.norm_ppf(0.00625), -2.4977, places=3)


class Eligible(unittest.TestCase):
    def test_rules(self):
        n = 20
        a = np.arange(n, dtype=float)                       # годен
        b = np.full(n, 1.0)                                 # константа
        c = np.where(np.arange(n) % 2 == 0, np.nan, 1.0)    # 50 % не-NaN, 1 значение → отброшен по числу значений
        d = a.copy()                                        # дубль a
        e = np.where(np.arange(n) < 12, np.nan, np.arange(n, dtype=float))   # 40 % не-NaN
        f = np.where(np.arange(n) < 4, np.nan, np.arange(n, dtype=float) % 3)   # 80 %, 3 значения
        X = np.stack([a, b, c, d, e, f], axis=1)
        self.assertEqual(lib.eligible_columns(X), [0, 5])

    def test_feature_columns(self):
        names = ["win", "row_id", "sym", "meta_x", "y_net", "mv5", "emp2h", "n1440", "ok10_d7", "bk_n", "rules_lx"]
        self.assertEqual(lib.feature_columns(names), ["mv5", "ok10_d7", "rules_lx"])
        self.assertEqual(len(lib.DROP_ALL), 83)


class Cart(unittest.TestCase):
    def test_splits_on_separating_feature(self):
        rng = np.random.default_rng(11)
        n = 400
        X = rng.normal(size=(n, 6))
        y = (X[:, 3] > 0.2).astype(float)
        nodes = lib.cart_fit(X, y, max_depth=3, min_leaf=20)
        self.assertFalse(nodes[0]["leaf"])
        self.assertEqual(nodes[0]["feat"], 3)
        self.assertLessEqual(lib.cart_depth(nodes), 3)
        self.assertGreater(lib.auc_scores(lib.cart_score(nodes, X), y), 0.99)
        leaf = lib.cart_leaf(nodes, X)
        for k in set(leaf):
            self.assertGreaterEqual(int((leaf == k).sum()), 20)

    def test_pure_or_small_is_leaf(self):
        X = np.arange(10, dtype=float)[:, None]
        nodes = lib.cart_fit(X, np.ones(10), max_depth=3, min_leaf=2)
        self.assertEqual(len(nodes), 1)
        nodes = lib.cart_fit(X, (X[:, 0] > 4).astype(float), max_depth=3, min_leaf=6)
        self.assertEqual(len(nodes), 1)                              # 2·min_leaf > n

    def test_min_leaf_blocks_small_split(self):
        rng = np.random.default_rng(2)
        X = rng.normal(size=(100, 3))
        y = np.zeros(100)
        y[np.argsort(X[:, 0])[:3]] = 1                               # 3 положительных на краю: сплит с листом 3 < min_leaf 20 невозможен
        nodes = lib.cart_fit(X, y, max_depth=3, min_leaf=20)
        for nd in nodes:
            self.assertGreaterEqual(nd["n"], 20)


class Join(unittest.TestCase):
    def test_unique_dup_same_dup_diff_missing(self):
        feat = np.array([[1.0, np.nan], [2.0, 3.0], [2.0, 3.0], [5.0, 6.0], [7.0, 8.0]])
        rk = [("a", 1), ("b", 2), ("b", 2), ("c", 3), ("c", 3)]
        ix, why = lib.join_unique([("a", 1), ("b", 2), ("c", 3), ("d", 4)], rk, feat)
        self.assertEqual(list(ix), [0, 1, -1, -1])
        self.assertEqual(list(why), [0, 0, 2, 1])


class Trades(unittest.TestCase):
    def test_load_set_gate_and_selection(self):
        import pandas as pd
        import tk604_stats as st
        import tk685_trades as tr
        with tempfile.TemporaryDirectory() as d:
            sets = {"X": ("L", os.path.join(d, "{w}-L"), 3, 1.0)}
            for w in st.TRADE_WINDOWS:
                os.makedirs(os.path.join(d, w.name + "-L"))
            def day_ms(w, date, extra=0):
                return ((datetime.date.fromisoformat(date) - w.t0d).days * st.DAY_MS) + extra
            wa, wb, wc = st.TRADE_WINDOWS
            recs = {
                wa.name: [("AAAUSDT", day_ms(wa, "2026-07-01", 1000), 5.0, 0.3), ("OUTUSDT", day_ms(wa, "2026-06-20"), 1.0, 0.3)],     # вторая — до зачётного диапазона
                wb.name: [("BBBUSDT", day_ms(wb, "2026-08-26", 2000), 2.0, 0.3)],
                wc.name: [("CCCUSDT", day_ms(wc, "2026-11-13", 3000), -6.0 + 0.3, 0.3)],
            }
            for w in st.TRADE_WINDOWS:
                lines = ["sym,level,sig_min,entry_min,exit_min,entry_px_e8,kind,takes,gross,fees,funding,net,entry_day,sig_ms,entry_ms,exit_ms"]
                for sym, ems, gross, fees in recs[w.name]:
                    lines.append("%s,3,0,0,0,100000000,trail,2,%f,%f,0,%f,0,%d,%d,%d" % (sym, gross, fees, gross - fees, ems - 4, ems, ems + 60_000))
                open(os.path.join(d, w.name + "-L", "long-trades.csv"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
            expect_sum = (5.0 - 0.3) + (2.0 - 0.3) + (-5.7 - 0.3)
            sets["X"] = ("L", os.path.join(d, "{w}-L"), 3, expect_sum)
            df = tr.load_set("X", sets=sets)
            self.assertEqual(len(df), 3)
            self.assertNotIn("OUTUSDT", set(df["sym"]))
            self.assertEqual(list(df["win"]), ["wa", "wb", "wc"])
            self.assertEqual(list(df["half"]), [1, 1, 2])
            self.assertEqual(list(df["group"]), [lib.PROFIT, lib.PROFIT, lib.LOSS])
            self.assertEqual(int(df["day_in"].iloc[0]), 0)
            self.assertEqual(int(df["day_in"].iloc[2]), 135)
            self.assertTrue((df["sig_abs"] < df["t_in"]).all())
            bad = {"X": ("L", os.path.join(d, "{w}-L"), 4, expect_sum)}
            with self.assertRaises(AssertionError):
                tr.load_set("X", sets=bad)
            sz = tr.group_sizes(df)
            self.assertEqual(sz[("net", 0)], [1, 0, 2])


if __name__ == "__main__":
    unittest.main()
