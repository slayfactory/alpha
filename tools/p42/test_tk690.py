#!/usr/bin/env python3
"""Тесты `tk690_run`: потоки по клеткам с no_key > 0, строки потоков без дублей и со стопом на конфликте, слияние таблиц фильтров (прежние строки дословно)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk690_run as t  # noqa: E402


def sigkey(patch):
    return tuple(sorted((k, str(patch[k])) for k in ("nf", "ev", "ea", "natr_min") if k in patch))


def bad_cell(patch, cands, no_key):
    return dict(patch=patch, fc={w: dict(cands=cands[i], no_key=no_key[i]) for i, w in enumerate(("wa", "wb", "wc"))})


class Streams(unittest.TestCase):
    def test_cells_of_one_stream_are_one_stream(self):
        b = [bad_cell(dict(nf=1, natr_min=0.6, s=4), (72, 91, 92), (16, 33, 31)), bad_cell(dict(nf=1, natr_min=0.6, s=4, ew=1), (72, 91, 92), (16, 33, 31)),
             bad_cell(dict(ev=4, ea=0.391, s=4), (114, 124, 134), (59, 58, 74))]
        ss = t.streams_from_bad(b, sigkey)
        self.assertEqual([s["vid"] for s in ss], ["n00", "n01"])
        self.assertEqual([s["cells"] for s in ss], [1 + 1, 1] if ss[0]["patch"].get("nf") else [1, 2])
        self.assertTrue(all("s" not in s["patch"] and "ew" not in s["patch"] for s in ss))        # в заплатке потока — только ключи прохода сигналов

    def test_counters_must_agree_within_stream(self):
        b = [bad_cell(dict(nf=1, natr_min=0.6), (72, 91, 92), (16, 33, 31)), bad_cell(dict(nf=1, natr_min=0.6, s=4), (72, 91, 93), (16, 33, 31))]
        with self.assertRaises(SystemExit):
            t.streams_from_bad(b, sigkey)


def dump(rows):
    """rows: (sym, sig_ms, level_px_e8, dist_e8) → дамп sigsx с одинаковыми прочими полями."""
    return pd.DataFrame([dict(sym=s, level=1, sig_ms=m, dist_e8=d, level_px_e8=px, natr_e8=500, born=1, end=9, first_bar=2, members="1:%d" % px) for s, m, px, d in rows])


class Rows(unittest.TestCase):
    def test_union_without_duplicates_ordered_like_lvl(self):
        per = {"n00": dump([("AAA", 300, 100, 5), ("BBB", 100, 200, 7)]), "n01": dump([("BBB", 100, 200, 7), ("CCC", 100, 300, 3)])}
        r = t.build_rows(per, "wa", 1_000)
        self.assertEqual(len(r), 3)
        self.assertEqual(list(r["sym"]), ["CCC", "BBB", "AAA"])                         # по (sig_ms, dist_e8, sym, level)
        self.assertEqual(list(r["row_id"]), [0, 1, 2])
        self.assertEqual(list(r["abs_ms"]), [1_100, 1_100, 1_300])
        self.assertEqual(set(r["win"]), {"wa"})

    def test_conflicting_attributes_stop(self):
        a, b = dump([("AAA", 100, 100, 5)]), dump([("AAA", 100, 100, 6)])
        with self.assertRaises(SystemExit):
            t.build_rows({"n00": a, "n01": b}, "wa", 0)

    def test_two_levels_one_key_stop(self):
        with self.assertRaises(SystemExit):
            t.build_rows({"n00": dump([("AAA", 100, 100, 5)]), "n01": dump([("AAA", 100, 101, 5)])}, "wa", 0)


class Merge(unittest.TestCase):
    def write(self, name, lines):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("sym,sig_ms,f000,f001\n" + "".join(l + "\n" for l in lines))
        return p

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_old_rows_verbatim_new_appended(self):
        old = self.write("old.csv", ["AAA,100,1.0,0.0", "BBB,200,0.0,0.0"])
        new = self.write("new.csv", ["BBB,200,0.0,0.0", "CCC,300,1.0,1.0", "AAA,100,1.0,0.0"])
        out = os.path.join(self.d, "out.csv")
        n_common, n_add, diff = t.merge_tables(old, new, out)
        self.assertEqual((n_common, n_add, diff), (2, 1, []))
        self.assertEqual(open(out, encoding="utf-8").read(), open(old, encoding="utf-8").read() + "CCC,300,1.0,1.0\n")

    def test_difference_on_common_key_is_reported(self):
        old = self.write("old.csv", ["AAA,100,1.0,0.0"])
        new = self.write("new.csv", ["AAA,100,1.0,1.0"])
        _, _, diff = t.merge_tables(old, new, os.path.join(self.d, "out.csv"))
        self.assertEqual(diff, [("AAA", 100)])

    def test_header_mismatch_stops(self):
        old = self.write("old.csv", ["AAA,100,1.0,0.0"])
        p = os.path.join(self.d, "new.csv")
        open(p, "w", encoding="utf-8").write("sym,sig_ms,f000,f002\nAAA,100,1.0,0.0\n")
        with self.assertRaises(SystemExit):
            t.merge_tables(old, p, os.path.join(self.d, "out.csv"))


if __name__ == "__main__":
    unittest.main()
