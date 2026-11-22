#!/usr/bin/env python3
"""Тесты отбора троек в режиме TK674_X680 (шаг 4 на c539): карты пар обоих шагов объединяются, активные пары и планка нижних уровней берутся из объединения. Без srv, без движка, без БД."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.makedirs(os.path.join(TMP, "res"))
os.environ.update(TK674_ROOT=TMP, TK674_REBASE="1", TK674_SPLIT="1", TK674_X680="1", TK674_UNI="c539")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_level as lv  # noqa: E402


def pair(name, gain, usd, bnp=None):
    return dict(pair=name, both_moved=dict(gain_sub=gain, usd=usd) if gain is not None else None, best_no_plateau=dict(sharpe=bnp) if bnp is not None else None)


def put(name, pairs, best1, busd=100.0):
    json.dump(dict(base=dict(usd=busd), pairs=pairs, best1=best1), open(os.path.join(TMP, "res", name), "w", encoding="utf-8"))


class LevelX680(unittest.TestCase):
    def setUp(self):
        put("map674bs-S.json", [pair("a|b", 1.0, 120), pair("a|c", 0.5, 90), pair("b|c", None, 0)], dict(a=3.0, b=2.0))
        put("map674bsx-S.json", [pair("a|x", 2.0, 130, 4.0), pair("b|c", 0.7, 110, 3.5)], dict(a=3.5, x=2.5))

    def test_union_prefers_x_and_takes_max_best1(self):
        prs, b1, busd = lv.pair_maps("S")
        self.assertEqual(set(prs), {"a|b", "a|c", "b|c", "a|x"})
        self.assertEqual(prs["b|c"]["both_moved"]["gain_sub"], 0.7)            # запись карты x главнее
        self.assertEqual(b1, dict(a=3.5, b=2.0, x=2.5))
        self.assertEqual(busd, 100.0)

    def test_active_subs_include_new_axis_pairs(self):
        act, n_sub = lv.active_subs(3, "S")
        self.assertEqual(n_sub, 4)
        self.assertEqual(act, {frozenset("ab"): 1.0, frozenset("bc"): 0.7, frozenset("ax"): 2.0})   # a|c — Σ $ ниже базы (90 < 100): не активна

    def test_level4_reads_only_triples_map(self):
        put("map674bs3-S.json", [pair("a|b|c", 0.4, 150)], {})
        act, n_sub = lv.active_subs(4, "S")
        self.assertEqual((act, n_sub), ({frozenset("abc"): 0.4}, 1))


if __name__ == "__main__":
    unittest.main()
