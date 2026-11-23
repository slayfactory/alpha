#!/usr/bin/env python3
"""Тесты `tk674_run.batches` (лёгкие партии без фильтра — до VMAX_LIGHT вариантов, остальные — VMAX; набор клеток не меняется) и канареек шага 4 (`canary_pick`, `canary_check`)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
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

    def test_filter_cells_stay_separate_and_capped(self):
        todo = [cell(i, ("s", 0), filt=bool(i % 2)) for i in range(100)]
        self.assertEqual({k for k, _ in run.batches(todo)}, {"p", "f"})
        todo = [cell(i, ("s", i // 100), filt=True) for i in range(2000)]      # партии с фильтром — не больше VMAX
        self.assertLessEqual(max(len(c) for _, c in run.batches(todo)), run.VMAX)


def put(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w", encoding="utf-8"))


class Canary(unittest.TestCase):
    def setUp(self):
        res = os.path.join(TMP, "res")
        cells, index = {}, {}
        for b in range(2):                                     # две готовые партии шага 3: по 6 клеток, проходы сигналов 0 / 1 / 2
            nm = "pb%d" % b
            for j in range(6):
                ck, cid = "ck%d_%d" % (b, j), "cid%d_%d" % (b, j)
                cells[ck] = dict(cid=cid, patch={"s": 4}, filt=[], sig=[["natr_p", str(j % 3)]])
                index.setdefault(nm, []).append(ck)
                os.makedirs(os.path.join(TMP, "cells", "wa-S", nm, cid), exist_ok=True)
        cells["ckf"] = dict(cid="cidf", patch={}, filt=[["x", "1"]], sig=[["natr_p", "0"]])      # клетка с фильтром канарейкой не бывает
        index["pb0"].append("ckf")
        put(os.path.join(res, "plan674bsx-S.json"), dict(cells=cells))
        put(os.path.join(res, "batches674bsx-S.json"), index)

    def test_pick_round_robin_over_passes(self):
        cur = [cell(i, (("natr_p", str(i % 3)),)) for i in range(30)]
        got = run.canary_pick("S", cur, 6)
        self.assertEqual(len(got), 6)
        self.assertEqual(len({tuple(map(tuple, c["sig"])) for _, c in got}), 3)       # по кругу по проходам
        self.assertTrue(all(not c["filt"] for _, c in got))
        self.assertEqual(len({c["cid"] for _, c in got}), 6)
        self.assertEqual(run.canary_pick("S", [cell(0, (("natr_p", "9"),))], 6), [])       # другой проход — канареек нет

    def test_check_byte_equal_and_diff(self):
        w = types.SimpleNamespace(name="wa")
        out = os.path.join(TMP, "outcanary")
        got = run.canary_pick("S", [cell(i, (("natr_p", str(i % 3)),)) for i in range(30)], 2)
        for nm, c in got:
            old = os.path.join(TMP, "cells", "wa-S", nm, c["cid"])
            new = os.path.join(out, "cn-" + c["cid"])
            os.makedirs(new, exist_ok=True)
            for f in run.CMP_FILES:
                for d in (old, new):
                    with open(os.path.join(d, "short-" + f), "w", newline="") as fh:
                        fh.write("a,b\n1,2\n")
        self.assertEqual(run.canary_check("S", w, out, got), [])
        self.assertFalse(any(os.path.exists(os.path.join(out, "cn-" + c["cid"])) for _, c in got))       # каталоги канареек убраны
        nm, c = got[0]
        new = os.path.join(out, "cn-" + c["cid"])
        os.makedirs(new, exist_ok=True)
        for f in run.CMP_FILES:
            with open(os.path.join(new, "short-" + f), "w", newline="") as fh:
                fh.write("a,b\n1,3\n")
        bad = run.canary_check("S", w, out, got[:1])
        self.assertEqual(len(bad), len(run.CMP_FILES))


class SplitUnion(unittest.TestCase):
    """PS-988: остаток уровня партиями — упаковка комбинаций и объединение файлов уровня."""

    def test_fits(self):
        import tk674_level as lv
        self.assertTrue(lv.fits(dict(taken=[], n_new=0), 70000, 40000))                  # пустая партия берёт любую комбинацию (крупнее cap — своя партия)
        self.assertTrue(lv.fits(dict(taken=[1], n_new=39990), 10, 40000))
        self.assertFalse(lv.fits(dict(taken=[1], n_new=39990), 11, 40000))

    def test_union_in_order_and_gap_stops(self):
        import tk674_axes as ax
        import tk674_level as lv
        res = os.path.join(TMP, "res")

        def lvl(side, tg, names, skip, cells):
            put(lv.level_file(3, side, tg), dict(side=side, n=3, cap=20000, eligible=5, taken=[dict(names=[x]) for x in names], left_out=0, n_cells=len(cells), skip=skip,
                                                 cells={k: dict(cid=k, patch={}, filt=[], sig=[]) for k in cells}))
        for side in ax.SIDES:
            lvl(side, "", ["a", "b"], 0, ["k1", "k2"])
            lvl(side, "r01", ["c"], 2, ["k3"])
            lvl(side, "r02", ["d", "e"], 3, ["k4"])
        put(os.path.join(res, "invalid674%s3r01-%s.json" % (ax.SUF2, ax.SIDES[0])), {"cidX": "no_key"})
        lv.cmd_union(3, ["r01", "r02"])
        for side in ax.SIDES:
            u = json.load(open(lv.level_file(3, side, "u"), encoding="utf-8"))
            self.assertEqual([x["names"][0] for x in u["taken"]], list("abcde"))           # порядок отбора сохранён
            self.assertEqual(sorted(u["cells"]), ["k1", "k2", "k3", "k4"])
            self.assertEqual(u["left_out"], 0)
        inv = json.load(open(os.path.join(res, "invalid674%s3u-%s.json" % (ax.SUF2, ax.SIDES[0])), encoding="utf-8"))
        self.assertEqual(inv, {"cidX": "no_key"})
        for side in ax.SIDES:
            lvl(side, "r03", ["z"], 7, ["k9"])                                              # партии не подряд (skip 7, набрано 5) — стоп
        with self.assertRaises(SystemExit):
            lv.cmd_union(3, ["r01", "r02", "r03"])


if __name__ == "__main__":
    unittest.main()
