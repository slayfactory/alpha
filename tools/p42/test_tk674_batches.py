#!/usr/bin/env python3
"""Тест `tk674_run.batches`: лёгкие партии (< 20 проходов сигналов) — до VMAX_LIGHT вариантов, тяжёлые — VMAX; набор клеток не меняется."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.makedirs(os.path.join(TMP, "res"))
os.environ.update(TK674_ROOT=TMP, TK674_UNI="c539", TK674_VMAX_LIGHT="800")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_run as run  # noqa: E402


def cell(i, sig, filt=False):
    return dict(cid="c%05d" % i, ckey="k%05d" % i, sig=sig, filt=filt)


class Batches(unittest.TestCase):
    def test_light_batches_take_vmax_light(self):
        todo = [cell(i, ("s", i // 100)) for i in range(2000)]               # 20 проходов по 100 вариантов
        sizes = [len(c) for _, c in run.batches(todo)]
        self.assertEqual(sum(sizes), 2000)
        self.assertEqual(sizes[0], 800)                                      # до 8 проходов — лёгкая партия

    def test_heavy_batches_keep_vmax(self):
        todo = [cell(i, ("s", i // 10)) for i in range(2000)]                # 200 проходов по 10 вариантов
        bl = run.batches(todo)
        self.assertEqual(sum(len(c) for _, c in bl), 2000)
        for _, cur in bl:
            self.assertLessEqual(len({c["sig"] for c in cur}), run.SIGMAX)
            if len({c["sig"] for c in cur}) >= run.LIGHT_SIGS:
                self.assertLessEqual(len(cur), 410)                          # VMAX + хвост последнего прохода

    def test_filter_cells_stay_separate(self):
        todo = [cell(i, ("s", 0), filt=bool(i % 2)) for i in range(100)]
        kinds = {k for k, _ in run.batches(todo)}
        self.assertEqual(kinds, {"p", "f"})


if __name__ == "__main__":
    unittest.main()
