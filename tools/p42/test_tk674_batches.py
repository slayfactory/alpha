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


class BigCalls(unittest.TestCase):
    """TK-683 (PS-990): вызовы крупнее — TK674_VMAX / TK674_SIGMAX; набор клеток не меняется, меняется состав партий."""

    def test_big_calls(self):
        todo = [cell(i, ("s", i // 10), filt=True) for i in range(6000)]       # 600 проходов по 10 вариантов с фильтром
        old = (run.VMAX, run.SIGMAX)
        try:
            run.VMAX, run.SIGMAX = 2000, 104
            bl = run.batches(todo)
        finally:
            run.VMAX, run.SIGMAX = old
        self.assertEqual(sorted(c["cid"] for _, cur in bl for c in cur), sorted(c["cid"] for c in todo))
        for _, cur in bl:
            self.assertLessEqual(len({c["sig"] for c in cur}), 104)
            self.assertLessEqual(len(cur), 2000 + 10)                            # VMAX + хвост последнего прохода
        self.assertLess(len(bl), len(run.batches(todo)))                        # крупнее — меньше вызовов, чем при 400 / 26


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

    def test_first_batch_with_sources_carries_canary(self):
        """r10: у первых партий нет клеток-источников — канарейки уходят в первую партию с ними, остальные идут после; нет ни у одной — порядок прежний."""
        old = run.CANARY_N
        run.CANARY_N = 6
        try:
            nosrc = [cell(i, (("natr_p", "9"),)) for i in range(500)]
            src = [cell(i, (("natr_p", str(i % 3)),)) for i in range(500)]
            small = [cell(i, (("natr_p", "0"),)) for i in range(100)]
            bl, k = run.canary_first("S", [("p", nosrc), ("p", small), ("p", src)])
            self.assertEqual((k, [len(c) for _, c in bl]), (2, [500, 500, 100]))
            self.assertIs(bl[0][1], src)
            bl, k = run.canary_first("S", [("p", src), ("p", nosrc)])                       # первая и так несёт — порядок не меняется
            self.assertEqual((k, bl[0][1] is src), (0, True))
            bl, k = run.canary_first("S", [("p", nosrc), ("f", src)])                       # источников нет ни у одной «p» — как есть (стоп в cmd_cells)
            self.assertEqual((k, bl[0][1] is nosrc), (0, True))
        finally:
            run.CANARY_N = old

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
    """PS-990: остаток уровня партиями — упаковка комбинаций и объединение файлов уровня."""

    def test_stage2_filter(self):
        import tk674_level as lv
        cand = [(-5, ("a.ge", "b.le", "c.ge", "d.le"), 3), (-4, ("a.ge", "bd_thin5.le", "c.ge", "d.le"), 3), (-3, ("a.ge", "f12x.le", "c.ge", "d.ge"), 3),
                (-2, ("a.ge", "b.le", "c.ge", "e.le"), 3), (-1, ("a.ge", "b.le", "c.ge", "g.le"), 3)]
        exn = {frozenset(("a.ge", "b.le", "c.ge", "e.le"))}
        names = lambda r: [c[1][1] for c in r]
        self.assertEqual(names(lv.stage2_filter(cand, exn, {"f12x"}, ("bd_",))), ["b.le", "b.le"])           # ступень 1, bd_ и f12 убраны, порядок ранга цел
        self.assertEqual(names(lv.stage2_filter(cand, exn, set(), ())), ["b.le", "bd_thin5.le", "f12x.le", "b.le"])
        self.assertEqual(names(lv.stage2_filter(cand, set(), {"f12x"}, ())), ["b.le", "bd_thin5.le", "b.le", "b.le"])

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

    def test_union_skips_side_without_level_file(self):
        """PS-991: четвёрки только лонга — у шорта нет файла уровня; объединение не падает и не создаёт файлов шорта."""
        import tk674_axes as ax
        import tk674_level as lv
        if "L" not in ax.SIDES or "S" not in ax.SIDES:
            self.skipTest("нужны обе стороны")
        put(lv.level_file(4, "L", ""), dict(side="L", n=4, cap=None, eligible=1, taken=[dict(names=["q"])], left_out=0, n_cells=1, skip=0,
                                            cells={"k1": dict(cid="k1", patch={}, filt=[], sig=[])}))
        lv.cmd_union(4, [])
        self.assertTrue(os.path.exists(lv.level_file(4, "L", "u")))
        self.assertFalse(os.path.exists(lv.level_file(4, "S", "u")))


class Recount(unittest.TestCase):
    """Пересчёт клеток TK-690 (PS-994): TK674_ONLY сужает клетки, TK674_INVALID_DROP снимает недействительность, без переменных — как раньше."""

    def setUp(self):
        self.f_only, self.f_drop = os.path.join(TMP, "only.json"), os.path.join(TMP, "drop.json")
        json.dump(["c00001", "c00003"], open(self.f_only, "w"))
        json.dump(["c00002"], open(self.f_drop, "w"))
        json.dump({"c00002": "no_key", "c00004": "no_key"}, open(run.invalid_path("S"), "w"))
        self.cells = {"k%05d" % i: cell(i, ("s", 0)) for i in range(5)}

    def tearDown(self):
        for v in ("TK674_ONLY", "TK674_INVALID_DROP"):
            os.environ.pop(v, None)

    def test_only(self):
        self.assertEqual(len(run._only(self.cells)), 5)                       # без переменной — все
        os.environ["TK674_ONLY"] = self.f_only
        self.assertEqual(sorted(c["cid"] for c in run._only(self.cells).values()), ["c00001", "c00003"])

    def test_invalid_drop(self):
        self.assertEqual(sorted(run.load_invalid("S")), ["c00002", "c00004"])
        os.environ["TK674_INVALID_DROP"] = self.f_drop
        self.assertEqual(sorted(run.load_invalid("S")), ["c00004"])


class FakeStore:
    """cells_lookup / cells_count по меткам окна: прежние строки под основной меткой, пересчёт — под отдельной."""

    def __init__(self, data):
        self.data = data                                                       # {win: {ckey: row}}

    def cells_lookup(self, side, uni, win, keys, bins=None):
        return {k: self.data.get(win, {})[k] for k in keys if k in self.data.get(win, {})}

    def cells_count(self, bins=None):
        return [dict(side="short", uni=run.UNI, win=w, bin="b", n=len(d)) for w, d in self.data.items()]


class Overlay(unittest.TestCase):
    """Пересчёт PS-994: клетка в карте одна (строка заменяется), m по основной метке окна не растёт."""

    def test_overlay_replaces_once_and_m_unchanged(self):
        main = {"k1": dict(sharpe=1.0), "k2": dict(sharpe=2.0), "k3": dict(sharpe=3.0)}
        new = {"k2": dict(sharpe=9.0), "k3": dict(sharpe=8.0), "kz": dict(sharpe=7.0)}      # kz — контроль вне плана
        store = FakeStore({run.WIN: main, "01.07–13.11 TK-690": new})
        plan = dict(cells={"k1": dict(cid="a"), "k2": dict(cid="b"), "k3": dict(cid="c")})
        rows = store.cells_lookup("short", run.UNI, run.WIN, list(plan["cells"]))
        n = run.overlay_rows(store, "S", plan, rows, None, "01.07–13.11 TK-690", {"b"})
        self.assertEqual(n, 1)
        self.assertEqual({k: r["sharpe"] for k, r in rows.items()}, {"k1": 1.0, "k2": 9.0, "k3": 3.0})      # k2 заменена, k3 не в списке — прежняя
        self.assertEqual(len(rows), 3)                                                                   # клеток столько же
        self.assertEqual(run.m_trials(store, "short"), (3, 3))                                          # m — только по основной метке (без пересчёта и без контроля)


class RecountCmp(unittest.TestCase):
    def test_cmp_rows(self):
        import tk674_recount as rc
        r = lambda t, u, s: dict(trades=t, usd=u, sharpe=s)                                              # noqa: E731
        store = FakeStore({"A": {"k1": r(10, 5.0, 1.0), "k2": r(10, 5.0, 1.0), "k3": r(3, 1.0, 0.1)}, "B": {"k1": r(10, 5.0, 1.0), "k2": r(11, 6.0, 1.2)}})
        plan = dict(cells={"k1": dict(cid="a"), "k2": dict(cid="b"), "k3": dict(cid="c")})
        n, same, diff, no_a, no_b = rc.cmp_rows(store, run.UNI, "short", plan, "A", "B", ["a", "b", "c"])
        self.assertEqual((n, same, len(diff), no_a, no_b), (3, 1, 1, [], ["k3"]))


class MkPlan(unittest.TestCase):
    def test_mkplan_inserts_cells_at_old_positions(self):
        import tk674_recount as rc
        cur = dict(cells={"k1": dict(cid="a")}, grids={"g": dict(vals=[[0, 1], [0]], m=[[None, "k1"]])}, n_cells=1)
        old = dict(cells={"k1": dict(cid="a"), "k2": dict(cid="b"), "k3": dict(cid="c")}, grids={"g": dict(vals=[[0, 1], [0]], m=[["k2", "k1"]])})
        out, n = rc.mkplan(cur, old, ["b"])
        self.assertEqual((n, sorted(out["cells"]), out["grids"]["g"]["m"]), (1, ["k1", "k2"], [["k2", "k1"]]))
        self.assertEqual(cur["grids"]["g"]["m"], [[None, "k1"]])                                         # исходный план не изменён

    def test_only_plan_bypasses_build_cells(self):
        pf = os.path.join(TMP, "oplan.json")
        json.dump(dict(cells={"k9": dict(cid="z", patch={"s": 4}, filt=[["tpm60.ge", 1]], sig=[["e", 1]]), "k8": dict(cid="y", patch={}, filt=[], sig=[])}), open(pf, "w"))
        fo = os.path.join(TMP, "oonly.json")
        json.dump(["z"], open(fo, "w"))
        os.environ.update(TK674_ONLY_PLAN=pf, TK674_ONLY=fo)
        try:
            cells, grids, sk, bad, found = run.plan_side("S", None)
        finally:
            os.environ.pop("TK674_ONLY_PLAN"); os.environ.pop("TK674_ONLY")
        self.assertEqual(list(cells), ["k9"])
        self.assertEqual(cells["k9"]["filt"], [("tpm60.ge", 1)])
        self.assertEqual(cells["k9"]["sig"], (("e", 1),))


if __name__ == "__main__":
    unittest.main()
