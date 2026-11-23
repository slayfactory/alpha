#!/usr/bin/env python3
"""Тесты tk674_xb (PS-995): нужные сутки архива, разбор столбцов с bd, пересборка bd-столбцов."""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_xb as xb  # noqa: E402


def D(s):
    return (dt.date.fromisoformat(s) - xb.EPOCH).days


class Need(unittest.TestCase):
    def test_two_days_holes_and_omega(self):
        base = D("2026-10-09") * 86400.0
        s = [base + 600.0, base + 7200.0, D("2026-10-08") * 86400.0 + 5000.0, D("2026-06-30") * 86400.0 + 10.0]
        sym = ["AAA", "AAA", "BBB", "CCC"]
        got = xb.need_pairs(sym, s, D("2026-07-01"), D("2026-11-13"), (D("2026-10-08"),))
        # 00:10 09.10 → σ − 1 ч = 08.10; 02:00 → только 09.10; строка 08.10 (дыра) и строка до ω — не читаются
        self.assertEqual(got, {("AAA", "2026-10-09"), ("AAA", "2026-10-08")})

    def test_coverage_ok_not_overwritten_by_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.csv")
            open(p, "w", encoding="utf-8").write("sym,day,status,bytes\nA,2026-07-01,ok,5\nA,2026-07-01,err:URLError,0\nB,2026-07-01,404,0\n")
            st = xb.coverage_status([p])
        self.assertEqual(st[("A", "2026-07-01")], "ok")
        self.assertEqual(st[("B", "2026-07-01")], "404")


class Gap(unittest.TestCase):
    def test_snapshot_in_hour(self):
        ts = [0.0, 60.0, 9990.0]                                      # файл обрывается на 9990 с
        self.assertTrue(xb.has_snapshot_in_hour(ts, 10000.0))
        self.assertTrue(xb.has_snapshot_in_hour(ts, 13590.0))         # ровно σ − 3600 = 9990: снимок в окне
        self.assertFalse(xb.has_snapshot_in_hour(ts, 13591.0))        # обрыв данных биржи: за час до σ снимков нет
        self.assertFalse(xb.has_snapshot_in_hour([], 5.0))


class Columns(unittest.TestCase):
    def test_split_and_plan(self):
        cols = {"bd_imb02>=-0.03": "f000", "x<=1": "f001", "x<=1&bd_imb02>=-0.03": "f002", "bd_thin5<=2&x<=1&bd_imb02>=-0.03": "f003", "bd_thin5<=2": "f004"}
        self.assertEqual(xb.split_sem("x<=1&bd_imb02>=-0.03"), ("x<=1", ["bd_imb02>=-0.03"]))
        self.assertEqual(xb.split_sem("bd_thin5<=2"), ("", ["bd_thin5<=2"]))
        plan = xb.plan_bd_columns(cols)
        self.assertEqual(plan, {"f000": (None, ["f000"]), "f002": ("f001", ["f000"]), "f003": ("f001", ["f004", "f000"]), "f004": (None, ["f004"])})
        with self.assertRaises(SystemExit):
            xb.plan_bd_columns({"x<=1&bd_a>=1": "f0"})                  # единички bd_a нет в таблице

    def test_rebuild_residual_and_singles(self):
        cols = {"x<=1": "f001", "bd_a>=1": "f000", "x<=1&bd_a>=1": "f002"}
        xs = pd.DataFrame(dict(sym=["A", "B", "C"], sig_ms=["1", "2", "3"], f000=["1.0", "1.0", "1.0"], f001=["1.0", "0.0", "1.0"], f002=["1.0", "0.0", "1.0"]))
        allb = pd.DataFrame(dict(sym=["C", "A", "B"], sig_ms=["3", "1", "2"], f000=["0.0", "1.0", "1.0"], f001=["9.9", "9.9", "9.9"], f002=["9.9", "9.9", "9.9"]))
        out, _ = xb.rebuild_bd(xs, allb, cols)
        self.assertEqual(list(out["f001"]), list(xs["f001"]))           # столбец без bd — байты xs
        self.assertEqual(list(out["f000"]), ["1.0", "1.0", "0.0"])      # единичка — из -all-b по ключу (порядок строк иной)
        self.assertEqual(list(out["f002"]), ["1.0", "0.0", "0.0"])      # остаток xs И единичка -all-b
        self.assertEqual(list(xs["f002"]), ["1.0", "0.0", "1.0"])       # вход не тронут

    def test_rebuild_missing_key_stops(self):
        xs = pd.DataFrame(dict(sym=["A"], sig_ms=["1"], f000=["1.0"]))
        allb = pd.DataFrame(dict(sym=["B"], sig_ms=["1"], f000=["1.0"]))
        with self.assertRaises(SystemExit):
            xb.rebuild_bd(xs, allb, {"bd_a>=1": "f000"})


if __name__ == "__main__":
    unittest.main()
