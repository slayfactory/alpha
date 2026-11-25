#!/usr/bin/env python3
"""Тесты `tk674_select` (отбор кандидатов вперёд по предрегистрации): разбор ключа, исключения осей, дубли, пол, правило ничьей, второй кандидат по Жаккару."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_select as sel  # noqa: E402

BASE = "ew=5 s=4 t24=12 tt=20"


def cell(cid, ckey, sharpe, usd=200.0, gap=10, dd=20.0, tsig=None):
    return dict(ckey=ckey, cid=cid, sharpe=sharpe, usd=usd, n=300, maxdd=dd, gap_longest=gap, gap_mean=2.0, rr=1.8, lstop=20.4, worst=21.0, mean_usd=0.7, nb_mean=5.0, tsig=tsig or cid)


def amap(winners, side="S"):
    return dict(side=side, base=dict(ckey=BASE, sharpe=3.0, usd=100.0, n=170, maxdd=25.0, rr=1.8, kpi=dict(gap_longest=40)), sharpe_sd=1.5, m=1000000, pairs=[dict(pair="p%d" % i, winner=w) for i, w in enumerate(winners)])


class Select(unittest.TestCase):
    def test_parse_and_axes(self):
        self.assertEqual(sel.parse_ckey("ew=5 F=a<=0.3&b>=2 q=100"), {"ew": "5", "F:a<=": "0.3", "F:b>=": "2", "q": "100"})
        ax = sel.axes_of("ew=5 s=4 t24=12 tt=20 q=100 F=bd_thin5<=0.2", BASE)
        self.assertEqual(ax, {"q", "F:bd_thin5<="})
        self.assertEqual(sel.axes_of("ew=5 s=3 t24=12", BASE), {"s", "tt"})          # ключ базы, которого нет в клетке, — тоже отличие
        self.assertEqual(sel.axis_name("F:bd_thin5<="), "bd_thin5")

    def test_exclusions(self):
        self.assertTrue(sel.is_excluded(cell("s4+bd_thin5.le_q60", BASE + " F=bd_thin5<=0.2", 5), BASE))
        self.assertTrue(sel.is_excluded(cell("s4+cc_1h.le_q20", BASE + " F=cc_1h<=1", 5), BASE))
        self.assertTrue(sel.is_excluded(cell("s4+touch_ep.le_q50", BASE + " F=touch_ep<=3", 5), BASE))
        self.assertTrue(sel.is_excluded(cell("s4+age_h.ge_q50", BASE, 5), BASE))      # ось только в cid
        self.assertFalse(sel.is_excluded(cell("s4+q100+ok10_d5.le@h_le0", BASE + " q=100 F=ok10_d5<=0", 5), BASE))

    def test_exclusion_by_key_and_by_cid_separately(self):
        key_only = cell("s4+x", BASE + " F=pierce_max_bp<=5", 5)                       # ось только в ключе клетки
        cid_only = cell("s4+depth_mean_bp.le_q30", BASE, 5)                            # ось только в токенах cid
        for w in (key_only, cid_only):
            self.assertTrue(sel.is_excluded(w, BASE))
        self.assertEqual(sel.excluded_tokens(key_only, sel.axes_of(key_only["ckey"], BASE)), {"key:pierce_max_bp"})
        self.assertEqual(sel.excluded_tokens(cid_only, sel.axes_of(cid_only["ckey"], BASE)), {"cid:depth_mean_bp"})
        both = cell("s4+touch_ep.le_q50", BASE + " F=touch_ep<=3", 5)
        self.assertEqual(sel.excluded_tokens(both, sel.axes_of(both["ckey"], BASE)), {"key:touch_ep", "cid:touch_ep"})
        for name in sel.F12_AXES:                                                      # каждая f12-ось из PS-999 п. 3 снимается и по ключу, и по cid
            self.assertTrue(sel.is_excluded(cell("s4+" + name + ".le_q50", BASE + " F=" + name + "<=3", 5), BASE), name)
            self.assertTrue(sel.is_excluded(cell("s4+" + name + ".le_q50", BASE, 5), BASE), name)
            self.assertTrue(sel.is_excluded(cell("s4+x", BASE + " F=" + name + ">=3", 5), BASE), name)

    def test_forward(self):
        srcs = {"q": {"engine"}, "tpm60": {"trades"}, "ok10_d5": {"history"}, "bd_thin5": {"book"}, "oi_c1": {"metrics"}, "mix": {"candles", "funding"}}
        ok = lambda ck: sel.forward_ok(sel.axes_of(ck, BASE), srcs)
        self.assertTrue(ok(BASE + " q=100 F=tpm60<=0.3&ok10_d5<=0"))
        self.assertFalse(ok(BASE + " F=bd_thin5<=0.2"))
        self.assertFalse(ok(BASE + " F=oi_c1>=1"))
        self.assertFalse(ok(BASE + " F=mix>=1"))                                       # один из источников не вперёд
        self.assertFalse(ok(BASE + " zzz=1"))                                          # неизвестная ось — не вперёд
        real = sel.feature_sources()
        for name in ("tpm60", "vpin1h", "ok10_d5", "q", "rr"):
            self.assertIn(name, real, name)
        self.assertEqual(real["bd_thin5"], {"book"})

    def test_jaccard_and_pick(self):
        self.assertEqual(sel.jaccard(frozenset("ab"), frozenset("bc")), 1 / 3)
        mk = lambda s, gap, dd: dict(w=dict(sharpe=s, gap_longest=gap, maxdd=dd))
        self.assertEqual(sel.pick([mk(8.0, 20, 5), mk(7.95, 10, 9), mk(7.5, 1, 1)])["w"]["gap_longest"], 10)   # 7,95 в пределах 0,1 от 8,0 → меньший перехай
        self.assertEqual(sel.pick([mk(8.0, 10, 5), mk(7.95, 10, 4)])["w"]["maxdd"], 4)                       # перехай равен → меньшая просадка
        self.assertEqual(sel.pick([mk(8.0, 20, 5), mk(7.8, 1, 1)])["w"]["sharpe"], 8.0)                       # 7,8 вне 0,1
        self.assertIsNone(sel.pick([]))

    def test_build(self):
        d = tempfile.mkdtemp()
        w1 = cell("a+b", BASE + " q=100 tf=30", 9.0, tsig="t1")
        w2 = cell("a+c", BASE + " q=100 rr=1.5", 8.9, tsig="t2")                           # Жаккар к w1 = 1/3 → второй кандидат
        w3 = cell("a+b2", BASE + " q=100 tf=30 rr=1", 8.95, tsig="t3")                     # Жаккар к w1 = 2/3 → не второй
        w4 = cell("x+bd_thin5.le_q60", BASE + " F=bd_thin5<=0.2", 12.0, tsig="t4")         # исключён по оси
        w5 = cell("a+b", BASE + " q=100 tf=30", 9.0, tsig="t1")                            # дубль w1
        w6 = cell("low", BASE + " q=5", 2.0, tsig="t6")                                    # ниже пола по Шарпу
        w7 = cell("lowusd", BASE + " q=6", 8.0, usd=50.0, tsig="t7")                       # ниже пола по Σ
        for name, ws in zip(sel.MAPS["S"], ([w1, w4], [w2, w3, w5], [w6, w7, None])):
            json.dump(amap(ws), open(os.path.join(d, name + ".json"), "w"))
        r = sel.build("S", d)
        st = r["stats"]
        self.assertEqual((st["winners"], st["excluded_axes"], st["duplicates"], st["distinct"], st["above_floor"]), (7, 1, 1, 5, 3))
        self.assertEqual(r["first"]["w"]["cid"], "a+b")
        self.assertEqual(r["second"]["w"]["cid"], "a+c")
        self.assertEqual(st["second_pool"], 1)

    def test_no_second(self):
        d = tempfile.mkdtemp()
        w1 = cell("a", BASE + " q=100", 9.0, tsig="t1")
        w2 = cell("b", BASE + " q=100 rr=1", 8.0, tsig="t2")                              # Жаккар 1/2 ≤ 0,5 → допустим
        w3 = cell("c", BASE + " q=100", 7.0, tsig="t1")                                   # те же сделки → не второй
        for name, ws in zip(sel.MAPS["S"], ([w1], [w2], [w3])):
            json.dump(amap(ws), open(os.path.join(d, name + ".json"), "w"))
        self.assertEqual(sel.build("S", d)["second"]["w"]["cid"], "b")
        for name, ws in zip(sel.MAPS["S"], ([w1], [w3], [])):
            json.dump(amap(ws), open(os.path.join(d, name + ".json"), "w"))
        self.assertIsNone(sel.build("S", d)["second"])


if __name__ == "__main__":
    unittest.main()
