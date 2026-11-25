#!/usr/bin/env python3
"""Тесты `tk674_emptycols.filt_class` и `tk674_level.withheld` (правило критика 23.11, п. б–в: клетки с bd_* / cc_* карта не читает)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

os.environ.update(TK674_ROOT=tempfile.mkdtemp(), TK674_UNI="c539")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_emptycols as ec  # noqa: E402
import tk674_level as lv  # noqa: E402


class FiltClass(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(ec.filt_class([]), "0")
        self.assertEqual(ec.filt_class(None), "0")
        self.assertEqual(ec.filt_class([["tpm60.ge", "q80"]]), "A")
        self.assertEqual(ec.filt_class([["tpm60.ge", "q80"], ["big1800.le", "q80"]]), "B")
        self.assertEqual(ec.filt_class([["cc_1h.le", "q40"], ["big1800.le", "q80"]]), "cc")
        self.assertEqual(ec.filt_class([["bd_imb02.ge", "q20"]]), "bd")
        self.assertEqual(ec.filt_class([["cc_1h.le", "q40"], ["bd_thin5.ge", "q20"]]), "bd")        # bd сильнее cc: клетка недействительна


class Withheld(unittest.TestCase):
    cells = {"k1": dict(filt=[["cc_15m.le", "q60"]]), "k2": dict(filt=[["bd_thin30.ge", "q20"]]), "k3": dict(filt=[["big1800.le", "q80"]]), "k4": dict(filt=[])}

    def test_off_by_default(self):
        old, lv.WITHHOLD = lv.WITHHOLD, set()
        try:
            self.assertEqual(lv.withheld(self.cells), {})
        finally:
            lv.WITHHOLD = old

    def test_bd_cc(self):
        old, lv.WITHHOLD = lv.WITHHOLD, {"bd", "cc"}
        try:
            self.assertEqual(lv.withheld(self.cells), {"k1": "cc", "k2": "bd"})
        finally:
            lv.WITHHOLD = old

    def test_bd_only(self):
        old, lv.WITHHOLD = lv.WITHHOLD, {"bd"}
        try:
            self.assertEqual(lv.withheld(self.cells), {"k2": "bd"})
        finally:
            lv.WITHHOLD = old


if __name__ == "__main__":
    unittest.main()
