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
        a, b = dump([("AAA", 100, 100, 5)]), dump([("AAA", 100, 100, 5)])
        b["natr_e8"] = 501
        with self.assertRaises(SystemExit):
            t.build_rows({"n00": a, "n01": b}, "wa", 0)

    def test_dist_conflict_takes_first_stream_by_priority(self):
        per = {"n00": dump([("AAA", 100, 100, 5)]), "n01": dump([("AAA", 100, 100, 9)]), "n02": dump([("AAA", 100, 100, 5), ("BBB", 200, 100, 1)])}
        self.assertEqual(len(t.dist_conflicts(per)), 1)
        r = t.build_rows(per, "wa", 0, ["n01", "n00", "n02"])
        self.assertEqual(sorted(zip(r["sym"], r["dist_e8"])), [("AAA", 9), ("BBB", 1)])
        r = t.build_rows(per, "wa", 0, ["n00", "n01", "n02"])
        self.assertEqual(sorted(zip(r["sym"], r["dist_e8"])), [("AAA", 5), ("BBB", 1)])

    def test_alt_rows_are_other_distance_of_conflicting_keys_only(self):
        per = {"n00": dump([("AAA", 100, 100, 5)]), "n01": dump([("AAA", 100, 100, 9), ("BBB", 200, 100, 1)])}
        r = t.build_rows(per, "wa", 0, ["n00", "n01"], alt=True)
        self.assertEqual(list(zip(r["sym"], r["dist_e8"])), [("AAA", 9)])

    def test_stream_prio_nf_first(self):
        ss = [dict(vid="n00", patch=dict(ev=8, ea=0.07)), dict(vid="n09", patch=dict(nf=2, natr_min=0.4)), dict(vid="n10", patch=dict(nf=1, natr_min=0.6))]
        self.assertEqual(t.stream_prio(ss), ["n09", "n10", "n00"])

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


class Guard(unittest.TestCase):
    """Охрана «пусто проходит»: значение, которое считать нечем, не должно молча пропустить кандидата."""

    def test_cc_unavail_when_cluster_mate_has_no_cache(self):
        coins = ["A", "B", "Z", "C"]
        lab = {11: np.array([0, 0, 0, 1]), 10: np.array([-1, -1, -1, -1])}
        got = t.cc_unavail(coins, lab, np.array([11, 11, 10, 11, 11]), ["A", "Z", "A", "C", "Q"], {"A", "Z", "C"})      # у B кэша нет
        self.assertEqual(got.tolist(), [True, True, False, False, False])      # кластер {A, B, Z}: A, Z — да; месяц без кластера, монета одна в кластере, монеты нет во вселенной — нет

    def test_cc_unavail_false_when_all_mates_cached(self):
        coins = ["A", "B", "Z"]
        got = t.cc_unavail(coins, {11: np.array([0, 0, 0])}, np.array([11, 11]), ["A", "Z"], {"A", "B", "Z"})
        self.assertEqual(got.tolist(), [False, False])

    def test_parse_feats(self):
        self.assertEqual(t.parse_feats("bm=1 e=0.15 F=cb_1h>=0.16"), ["cb_1h"])
        self.assertEqual(t.parse_feats("a=1 F=cc_1h>=0.2&n24>=3&sharp>=0.1"), ["cc_1h", "n24", "sharp"])

    def tab(self):
        nan = np.nan
        return dict(cc_1h=np.array([0.5, nan, nan, 0.2, nan, 0.1]), cc_unavail=np.array([0, 1, 0, 0, 1, 0], dtype=bool), tpm60=np.array([1, 2, nan, 4, 5, 6.0]),
                    cons60=np.array([0, 0, 0, nan, 0, 0.0]), mv5=np.array([1, 1, 1, 1, 1, 1.0]), n24=np.array([3, 3, 3, 3, nan, 3.0]), m24=np.array([0, 0, 1, 0, 0, 0.0]))

    def test_unavailable_new_candidate_invalidates_cell(self):
        new = np.array([0, 0, 1, 1, 1, 0], dtype=bool)
        r = t.cell_counts(self.tab(), ["cc_1h"], np.arange(6), new)
        self.assertEqual((r["cands"], r["nan"], r["nan_new"], r["unavail_new"]), (6, 3, 2, 1))     # строка 1: «нечем», но в прежней таблице (значение есть) — не считается; строка 4: новая — считается; строка 2: NaN «не определено»

    def test_unavailable_in_old_rows_only_does_not_invalidate(self):
        new = np.array([0, 0, 1, 1, 0, 0], dtype=bool)
        self.assertEqual(t.cell_counts(self.tab(), ["cc_1h"], np.arange(6), new)["unavail_new"], 0)

    def test_cell_without_cc_is_not_invalidated_by_cc_flag(self):
        new = np.ones(6, dtype=bool)
        r = t.cell_counts(self.tab(), ["tpm60"], np.arange(6), new)
        self.assertEqual((r["unavail_new"], r["nan"]), (0, 1))

    def test_candidates_only_and_composite_features(self):
        new = np.ones(6, dtype=bool)
        r = t.cell_counts(self.tab(), ["sharp", "n24"], np.array([0, 3, 4]), new)
        self.assertEqual((r["cands"], r["nan"]), (3, 2))        # sharp NaN на строке 3 (cons60), n24 NaN на строке 4; строка 2 (m24 = 1) вне кандидатов
        r = t.cell_counts(self.tab(), ["n24"], np.array([2]), new)
        self.assertEqual(r["nan"], 1)                           # n24 при m24 = 1 не определён


if __name__ == "__main__":
    unittest.main()
