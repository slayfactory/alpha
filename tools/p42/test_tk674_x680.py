#!/usr/bin/env python3
"""Тесты режима TK674_X680 (пары с активными осями единичек TK-680, §13.73.7): без флага всё как было (эталон снят с кода до правки), с флагом — загрузка активных осей,
запреты совместимости, порядок и потолок пар, ключи клеток. Без srv, без движка, без БД (поиск в БД — подставной)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_axes as ax  # noqa: E402

# эталон до правки (код после слияния TK-680, без TK674_X680): подпись осей / сеток пар / списка пропущенных пар и ключ базы стороны
GOLD = {
    ("plain", "L"): dict(axes="9fa536424685844e", n_axes=34, pairs=525, skipped=36, skip_hash="86f3e9811921d26c", cells=8620, grid_hash="14a9e4b6adb09732",
                         base="ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 t24=12"),
    ("plain", "S"): dict(axes="b1556bd4d14ac9bd", n_axes=34, pairs=525, skipped=36, skip_hash="86f3e9811921d26c", cells=8868, grid_hash="8384bbacd014fa90",
                         base="bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 t24=12 tb=0.1 tt=20"),
    ("rebase", "L"): dict(axes="e75ed52c7ed68f13", n_axes=34, pairs=525, skipped=36, skip_hash="86f3e9811921d26c", cells=8620, grid_hash="4c76b453e73b78f2",
                          base="ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12"),
    ("rebase", "S"): dict(axes="510b808ddfd99335", n_axes=34, pairs=525, skipped=36, skip_hash="86f3e9811921d26c", cells=8868, grid_hash="bdd53d8e73affaf3",
                          base="bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20"),
    ("split", "L"): dict(axes="d7ed6a187ca45d75", n_axes=35, pairs=558, skipped=37, skip_hash="7f0219f56207f7db", cells=9970, grid_hash="f9cc807e920ddf93",
                         base="ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12"),
    ("split", "S"): dict(axes="9043ac4bc55b5a58", n_axes=35, pairs=558, skipped=37, skip_hash="7f0219f56207f7db", cells=10228, grid_hash="b38453153c276df6",
                         base="bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20"),
}
# клетки пар (стороны L / S), ключи сняты с кода до правки при REBASE = 1: (назначение осей, заплатка, фильтр, канонический ключ)
CELLS = {
    "L": [({"s": 3, "rf": 0.1}, {"s": 3, "rf": 0.1}, [], "ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.1 rs=20 s=3 t24=12"),
          ({"tt": 10, "k": 3}, {"s": 3, "tt": 10, "k": 3}, [], "ew=5 fh=4 fm=50 k=3 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12 tt=10"),
          ({"TPM60": "q60", "s": 2}, {"s": 2}, [("TPM60", "q60")], "ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=2 t24=12")],
    "S": [({"s": 3, "rf": 0.1}, {"s": 3, "rf": 0.1}, [], "bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.1 rs=2 s=3 t24=12 tb=0.1 tt=20"),
          ({"e": 0.5, "t24": 25}, {"s": 4, "e": 0.5, "t24": 25}, [], "bm=1 e=0.5 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=25 tb=0.1 tt=20"),
          ({"TPM60": "q60", "s": 2}, {"s": 2}, [("TPM60", "q60")], "bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=2 t24=12 tb=0.1 tt=20")],
}


def signature(side):
    A = ax.axes(side)
    ah = hashlib.sha256(repr([(a["name"], a["cls"], a["key"], a["vals"], a["base"]) for a in A]).encode()).hexdigest()[:16]
    g, sk = ax.pair_grids(side)
    h = hashlib.sha256()
    for k in sorted(g):
        h.update(repr((k, g[k]["vals"])).encode())
        for pos in sorted(g[k]["cells"]):
            p, f, ok, why = g[k]["cells"][pos]
            h.update(repr((pos, sorted(p.items()), f, ok, why)).encode())
    return dict(axes=ah, n_axes=len(A), pairs=len(g), skipped=len(sk), skip_hash=hashlib.sha256(repr(sk).encode()).hexdigest()[:16],
                cells=sum(len(x["cells"]) for x in g.values()), grid_hash=h.hexdigest()[:16], base=ax.base_ckey(side))


class Flags:
    """Подмена флагов модуля осей на время теста (они читаются при вызове функций)."""
    def __init__(self, **kw):
        self.kw, self.old = kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(ax, k)
            setattr(ax, k, v)

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(ax, k, v)


@unittest.skipIf(os.environ.get("TK674_X680") == "1", "эталон снят без TK674_X680")
class UnchangedWithoutFlag(unittest.TestCase):
    """Без TK674_X680 оси, сетки пар, ключи клеток и суффиксы файлов — как до правки."""

    def test_x680_off_by_default(self):
        self.assertFalse(getattr(ax, "X680", False))

    def test_axes_and_pair_grids(self):
        for mode, kw in (("plain", dict(REBASE=False, SPLIT=False)), ("rebase", dict(REBASE=True, SPLIT=False)), ("split", dict(REBASE=True, SPLIT=True))):
            with Flags(**kw):
                for side in "LS":
                    self.assertEqual(signature(side), GOLD[(mode, side)], "%s %s" % (mode, side))

    def test_base_ckeys(self):
        with Flags(REBASE=True, SPLIT=False):
            self.assertEqual(ax.base_ckey("L"), GOLD[("rebase", "L")]["base"])
            self.assertEqual(ax.base_ckey("S"), GOLD[("rebase", "S")]["base"])

    def test_cells_keys(self):
        with Flags(REBASE=True, SPLIT=False):
            for side, items in CELLS.items():
                for assign, patch, filt, ck in items:
                    p, f, ok, why = ax.cell_assign(side, assign)
                    self.assertEqual((p, f, ok, why), (patch, filt, True, ""))
                    self.assertEqual(ax.canon(ax.BASE_LINE(side) + (" " + ax.patch_str(p) if p else ""), []), ck)

    def test_axis_dicts_have_no_x680_fields(self):
        for side in "LS":
            for a in ax.axes(side):
                self.assertNotIn("patches", a)
                self.assertNotIn("x680", a)

    def test_file_suffixes(self):
        """SUF / FSUF `tk674_run` без флага: прежние имена файлов (plan674…, map674…, ftab674…)."""
        want = {(): ("", ""), ("TK674_REBASE=1",): ("b", ""), ("TK674_REBASE=1", "TK674_LEVEL=3"): ("b3", "3"), ("TK674_REBASE=1", "TK674_SPLIT=1"): ("bs", ""),
                ("TK674_REBASE=1", "TK674_LEVEL=1"): ("b1", "1")}
        for kv, (suf, fsuf) in want.items():
            env = {k: v for k, v in os.environ.items() if not k.startswith("TK674_") and k != "PYTHONPATH"}
            env.update(dict(x.split("=") for x in kv))
            env["PYTHONUTF8"] = "1"
            out = subprocess.run([sys.executable, "-c", "import sys, json; sys.path.insert(0, %r); import tk674_run as r; print(json.dumps([r.SUF, r.FSUF]))" % HERE],
                                 env=env, capture_output=True, text=True, cwd=tempfile.gettempdir())
            self.assertEqual(out.returncode, 0, out.stderr[-500:])
            self.assertEqual(json.loads(out.stdout.strip().splitlines()[-1]), [suf, fsuf], kv)

import tk680_axes as a80  # noqa: E402
import tk674_x680 as x6  # noqa: E402


# ---- подставной корень единичек TK-680 (карты map674b1-*.json + thr680*.json) -----------------------------------------------------------
def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w", encoding="utf-8"))


def _key_axis(side, sset, name, labels, winner, gain, active=True, invalid=()):
    eng = {n: (v, b) for n, v, b in a80.engine_axes(side, sset)}
    pm, base = dict(eng[name][0]), eng[name][1]
    cells = []
    for lab in labels:
        c = dict(label=lab, ckey=a80.make_cell(side, name, lab, pm[lab])["ckey"], is_base=lab == base)
        if lab in invalid:
            c["invalid"] = "движок отверг"
        cells.append(c)
    w = next(c for c in cells if c["label"] == winner)
    return dict(axis=name, kind="key", base=base, cells=cells, active=active, gain=gain, winner=dict(ckey=w["ckey"]))


def _col_axis(side, col, d, thr, gain, active=True):
    """thr — [(метка, порог)] в порядке файла порогов; «off» — рядом с самым мягким порогом (le — в конце, ge — в начале), как у `tk680_singles.select`."""
    op, name = {"le": "<=", "ge": ">="}[d], "%s.%s" % (col, d)
    cells = [dict(label=lab, ckey=a80.make_cell(side, name, lab, {}, (col, op, x))["ckey"], thr=x, cut=10) for lab, x in thr]
    off = dict(label="off", ckey=ax.base_ckey(side), is_base=True)
    cells = cells + [off] if d == "le" else [off] + cells
    return dict(axis=name, kind="col", base="off", cells=cells, active=active, gain=gain, winner=dict(ckey=next(c["ckey"] for c in cells if c["label"] != "off")))


def build_roots(tmp):
    """Четыре корня: основной (ключи + столбцы), «сетки шире» (ось карты natr_min / t24, ось fh, ось ew с ew = 0), «история пробоев», «контекст»."""
    out = {}
    for name, sset, tf in (("tk680", "", "thr680.json"), ("tk680g", "grid", "thr680g.json"), ("tk680h", "hist", "thr680h.json"), ("tk680c", "ctx", "thr680c.json")):
        root = os.path.join(tmp, name)
        thr, maps = {}, {}
        for side in "LS":
            if sset == "":
                thr[side] = {"mv15": dict(n=9, n_all=9, le=[["q20", 1.5], ["q40", 3.0]], ge=[]), "ok10_d7": dict(n=9, n_all=9, le=[], ge=[["q20", 1.0], ["q60", 4.0]]),
                             "btc_mv5": dict(n=9, n_all=9, le=[["q20", 5.0], ["q40", 10.0]], ge=[])}
                axes = [_key_axis(side, "", "ls", ["0", "0.5", "1"], "0.5", 0.5), _key_axis(side, "", "sn", ["0", "1", "1.5", "2", "3"], "1", 0.1, invalid=("3",)),
                        _key_axis(side, "", "sg", ["off", "sb0.5", "sb1", "sb2", "sr1.25", "sr1.5", "sr2", "sd0.25", "sd0.5", "sd1"], "sb1", 0.2),
                        _key_axis(side, "", "cw", ["off", "w15m0.25", "w30m0.5", "w60m1", "w60lo5.6", "w60lo7", "w60hi10.5", "w60hi12.7"], "w30m0.5", 0.3),
                        _key_axis(side, "", "tpp", ["off", "0.5-2.5", "1-5", "2-8"], "1-5", 0.35), _key_axis(side, "", "trp", ["off", "rr0.5", "rr1", "rr2"], "rr1", 0.15),
                        _key_axis(side, "", "xb", ["0", "0.2", "0.4", "0.8"], "0.4", 0.9), _key_axis(side, "", "fh", ["0.5", "1", "2", "4", "8"], "2", 0.4),
                        _key_axis(side, "", "nx", ["0", "1", "2", "3.5", "5"], "1", 0.05, active=False),
                        _col_axis(side, "mv15", "le", thr[side]["mv15"]["le"], 0.7), _col_axis(side, "ok10_d7", "ge", thr[side]["ok10_d7"]["ge"], 0.3),
                        _col_axis(side, "btc_mv5", "le", thr[side]["btc_mv5"]["le"], 0.25)]
            elif sset == "grid":
                thr[side] = {}
                axes = [_key_axis(side, "grid", "natr_min", ["0.2", "0.4", "0.6", "0.8", "1", "1.2"], "1", 0.45), _key_axis(side, "grid", "t24", ["6", "12", "25", "50", "100"], "6", 0.2),
                        _key_axis(side, "grid", "fh", ["0", "0.5", "1", "1.5", "2", "3", "4", "6", "8"], "6", 0.6), _key_axis(side, "grid", "ew", ["0", "5", "20", "100"], "20", 0.1),
                        _key_axis(side, "grid", "e", ["0.1", "0.15", "0.2", "0.3", "0.5"] if side == "L" else ["0.05", "0.1", "0.15", "0.2", "0.3", "0.5"], "0.3", 0.0, active=False)]
            elif sset == "hist":
                thr[side] = {"ok10_d3": dict(n=9, n_all=9, le=[["le0", 0.0], ["le1", 1.0]], ge=[]), "ok10_d7": dict(n=9, n_all=9, le=[], ge=[["ge1", 1.0], ["ge2", 2.0]])}
                axes = [_col_axis(side, "ok10_d3", "le", thr[side]["ok10_d3"]["le"], 0.5), _col_axis(side, "ok10_d7", "ge", thr[side]["ok10_d7"]["ge"], 0.2)]
            else:
                thr[side] = {"cosig15": dict(n=9, n_all=9, le=[["lt2", 1.0], ["lt3", 2.0]], ge=[]), "btc_mv5": dict(n=9, n_all=9, le=[["le0.1", 10.0], ["le0.2", 20.0]], ge=[])}
                axes = [_col_axis(side, "cosig15", "le", thr[side]["cosig15"]["le"], 0.55), _col_axis(side, "btc_mv5", "le", thr[side]["btc_mv5"]["le"], 0.35)]
            maps[side] = axes
        _write(os.path.join(root, "res", tf), thr)
        sha = hashlib.sha256(open(os.path.join(root, "res", tf), "rb").read()).hexdigest()
        for side in "LS":
            _write(os.path.join(root, "res", "map674b1-%s.json" % side), dict(side=side, level=1, axes=maps[side], thr_sha256=sha))
        out[name] = root
    return out


def old_thr(side):
    """Пороги осей признаков карты TK-674 (подставные числа) в виде `res/thr674.json`."""
    t = {}
    for name, feat, op, labels in ax.FILTERS[side]:
        t[name] = {lab: (feat, op, 1.0 + i) for i, lab in enumerate(labels)}
    return t


class X680Case(unittest.TestCase):
    """Режим TK674_X680=1 на подставных корнях; флаги модуля осей подменяются (читаются при вызове), ключи БД — подставные."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.old_flags = dict(REBASE=ax.REBASE, X680=ax.X680, SPLIT=ax.SPLIT)
        ax.REBASE, ax.X680, ax.SPLIT = True, False, False              # корни строим без адаптера (ключи клеток — как у `tk680_singles`, независимо от него)
        cls.roots = build_roots(cls.tmp)
        cls.old_env = os.environ.get("TK674_X680_ROOTS")
        os.environ["TK674_X680_ROOTS"] = ",".join(cls.roots[k] for k in ("tk680", "tk680h", "tk680g", "tk680c")) + "," + os.path.join(cls.tmp, "нет-такого")
        ax.X680 = True
        x6.reset()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.old_flags.items():
            setattr(ax, k, v)
        if cls.old_env is None:
            os.environ.pop("TK674_X680_ROOTS", None)
        else:
            os.environ["TK674_X680_ROOTS"] = cls.old_env
        x6.reset()

    def names(self, side="L"):
        """Новые оси единичек (ось карты, расширенная значением, — не новая)."""
        return [a["name"] for a in ax.axes(side) if a.get("x680") and a.get("kind") != "ext"]

    def axis(self, side, name):
        return ax.axis_map(side)[name]


class Loading(X680Case):
    def test_active_only(self):
        for side in "LS":
            n = self.names(side)
            self.assertNotIn("nx", n)                                   # ось не активна
            self.assertNotIn("e", n)                                    # то же, в наборе «сетки шире»
            self.assertEqual(sorted(n), sorted(["ls", "sn", "sg", "cw", "tpp", "trp", "xb", "fh", "ew", "mv15.le", "ok10_d7.ge", "btc_mv5.le",
                                                "ok10_d3.le@h", "ok10_d7.ge@h", "cosig15.le@c", "btc_mv5.le@c"]))

    def test_old_axes_untouched_and_first(self):
        old = [a["name"] for a in ax._old_axes("L")]
        self.assertEqual([a["name"] for a in ax.axes("L")][:len(old)], old)
        self.assertFalse(any(a.get("x680") for a in ax.axes("L")[:len(old)] if a["name"] not in ("natr_min",)))

    def test_missing_root_noted(self):
        self.assertTrue(any("нет-такого" in n and "пропущен" in n for n in x6.load("L")["notes"]))

    def test_compound_patches(self):
        a = self.axis("S", "xb")
        self.assertEqual(a["patches"]["0.4"], {"xb": 0.4, "e2": 0})                # у шорта вторая часть входа выключена
        self.assertEqual(a["patches"]["0"], {})
        self.assertEqual(self.axis("L", "xb")["patches"]["0.4"], {"xb": 0.4})
        self.assertEqual(self.axis("L", "sg")["patches"]["sb1"], {"sn": 2, "sg": 1, "sb": 1})
        self.assertEqual(self.axis("L", "tpp")["patches"]["1-5"], {"tpp": 1, "t_low": 1, "t_high": 5})
        self.assertEqual(self.axis("L", "cw")["patches"]["w60lo5.6"], {"cw": 60, "cl": 5.6, "ch": 1000, "cm": 0.5})
        self.assertEqual(self.axis("L", "sg")["base"], "off")

    def test_engine_rejected_value_dropped(self):
        self.assertNotIn("3", self.axis("L", "sn")["vals"])
        self.assertTrue(any(n == "sn" and "движок отверг" in w for n, w in x6.load("L")["dropped"]))

    def test_ew0_not_a_candidate(self):
        a = self.axis("L", "ew")
        self.assertEqual(a["vals"], ["5", "20", "100"])
        self.assertEqual(a["base"], "5")
        self.assertTrue(any(n == "ew" and "не кандидат" in w for n, w in x6.load("S")["dropped"]))
        self.assertEqual(self.axis("S", "ew")["patches"]["20"], {"ew": 20})            # у шорта без e2 = 0: значение ew = 0 снято

    def test_same_axis_two_sets_union(self):
        a = self.axis("L", "fh")
        self.assertEqual(a["vals"], ["0", "0.5", "1", "1.5", "2", "3", "4", "6", "8"])  # основной набор ⊂ «сетки шире»; числовые метки — по возрастанию
        self.assertAlmostEqual(a["gain"], 0.6)                                            # больший из 0,4 и 0,6
        self.assertEqual(a["set"], ["", "grid"])
        self.assertEqual(sum(1 for n in self.names("L") if n == "fh"), 1)

    def test_map_axis_key_extended_not_duplicated(self):
        a = self.axis("L", "natr_min")
        self.assertEqual(a["vals"], [0.2, 0.4, 0.6, 0.8, 1.0])                            # победитель набора «сетки шире» вне прежних значений
        self.assertTrue(a["x680"] and a["ext"] == ["1"])
        self.assertEqual(self.names("L").count("natr_min"), 0)                           # новой осью не стала
        self.assertEqual([x["name"] for x in ax.axes("L")].count("natr_min"), 1)
        t = self.axis("L", "t24")                                                         # победитель 6 — уже значение оси карты: расширять нечем
        self.assertEqual(t["vals"], [6, 12, 25, 50])
        self.assertFalse(t.get("x680"))
        self.assertTrue(any(n == "t24" and "расширять нечем" in w for n, w in x6.load("L")["dropped"]))

    def test_extended_axis_cells(self):
        patch, filt, ok, why = ax.cell_assign("L", {"natr_min": 1.0})
        self.assertEqual((patch, ok), ({"natr_min": 1.0, "s": 3}, True))

    def test_column_axes(self):
        a = self.axis("L", "mv15.le")
        self.assertEqual((a["cls"], a["op"], a["key"], a["base"], a["vals"]), ("filter", "<=", "mv15", "off", ["q20", "q40", "off"]))
        self.assertEqual(a["thr"], {"q20": ("mv15", "<=", 1.5), "q40": ("mv15", "<=", 3.0)})
        self.assertEqual(self.axis("L", "ok10_d7.ge")["vals"], ["off", "q20", "q60"])
        self.assertEqual(self.axis("L", "ok10_d3.le@h")["thr"]["le1"], ("ok10_d3", "<=", 1.0))
        self.assertEqual(x6.thresholds("L")["cosig15.le@c"]["lt2"], ("cosig15", "<=", 1.0))

    def test_column_specs(self):
        sp = x6.column_specs("L")
        self.assertEqual({c[0]: c[1] for c in sp["cl"]}, {"mv15": "mv15", "ok10_d7": "ok10_d7", "btc_mv5": "btc_mv5", "ok10_d3": "ok10_d3", "ok10_d7": "ok10_d7", "cosig15": "cosig15"})
        self.assertTrue(sp["ctx"] and sp["hist"])

    def test_key_check_against_map(self):
        """Ключ клетки «ось, значение» при остальном базе = ключ клетки карты единичек: у каждой оси и значения."""
        for side in "LS":
            for name in ("ls", "xb", "fh", "ew", "sg"):
                a = self.axis(side, name)
                for lab in a["vals"]:
                    patch, filt, ok, why = ax.cell_assign(side, {name: lab})
                    self.assertTrue(ok, (name, lab, why))
                    self.assertEqual(ax.cell_ckey(side, patch, filt, {}), a80.make_cell(side, name, lab, a["patches"][lab])["ckey"])

    def test_sha_mismatch_is_error(self):
        f = os.path.join(self.roots["tk680h"], "res", "thr680h.json")
        raw = open(f, "rb").read()
        try:
            open(f, "wb").write(raw + b" ")
            x6.reset()
            with self.assertRaises(SystemExit) as cm:
                x6.load("L")
            self.assertIn("sha256", str(cm.exception))
        finally:
            open(f, "wb").write(raw)
            x6.reset()

    def test_key_mismatch_is_error(self):
        f = os.path.join(self.roots["tk680"], "res", "map674b1-L.json")
        raw = open(f, "rb").read()
        m = json.loads(raw.decode("utf-8"))
        next(a for a in m["axes"] if a["axis"] == "ls")["cells"][1]["ckey"] = "ew=5 xx=1"
        try:
            open(f, "w", encoding="utf-8").write(json.dumps(m))
            x6.reset()
            with self.assertRaises(SystemExit) as cm:
                x6.load("L")
            self.assertIn("ключ клетки не совпал", str(cm.exception))
        finally:
            open(f, "wb").write(raw)
            x6.reset()


class Bans(X680Case):
    def ok(self, a, b, side="L"):
        return ax.compat(side, self.axis(side, a), self.axis(side, b))

    def test_registered_bans(self):
        for side in "LS":
            self.assertFalse(self.ok("mv15.le", "k", side)[0])                   # столбец × ось уровня
            self.assertFalse(self.ok("ok10_d7.ge", "tf", side)[0])
            self.assertFalse(self.ok("mv15.le", "e", side)[0])                   # столбец × e
            self.assertFalse(self.ok("TPM60", "e", side)[0])
            for o in ("e2", "mk", "ek"):
                self.assertFalse(self.ok("xb", o, side)[0], o)                  # xb × e2 / mk / ek
            self.assertFalse(self.ok("tpp", "tj", side)[0])
            self.assertFalse(self.ok("ls", "sn", side)[0])                       # ось формы стопа × другая
            self.assertFalse(self.ok("cw", "sg", side)[0])
            self.assertFalse(self.ok("ls", "cw", side)[0])
            self.assertTrue(self.ok("ls", "s", side)[0])                         # форма × фиксированный стоп — можно (блок A TK-643)
            self.assertTrue(self.ok("xb", "tpp", side)[0])

    def test_xb_split(self):
        old = ax.SPLIT
        ax.SPLIT = True
        try:
            self.assertFalse(ax.compat("L", self.axis("L", "xb"), self.axis("L", "split"))[0])
            self.assertTrue(ax.compat("L", self.axis("L", "ls"), self.axis("L", "split"))[0])
        finally:
            ax.SPLIT = old

    def test_dup_keys(self):
        self.assertFalse(self.ok("tpp", "t_low")[0])
        self.assertFalse(self.ok("tpp", "t_high")[0])
        self.assertFalse(self.ok("trp", "rr")[0])
        self.assertFalse(self.ok("sg", "sn")[0])
        self.assertTrue(self.ok("trp", "t_low")[0])

    def test_column_x_signal_keys(self):
        for c in ("mv15.le", "ok10_d3.le@h", "cosig15.le@c"):
            self.assertFalse(self.ok(c, "fh")[0], c)                             # новый ключ сигнала TK-680
            self.assertTrue(self.ok(c, "ls")[0], c)
            self.assertTrue(self.ok(c, "xb")[0], c)
        self.assertFalse(self.ok("TPM60", "fh")[0])                              # ось признаков карты × ключ сигнала
        self.assertTrue(self.ok("TPM60", "ls")[0])
        self.assertTrue(self.ok("mv15.le", "natr_min")[0])                       # пул — допустим (ослабленный пул отсекается по клетке)

    def test_column_x_stream_keys_tk681(self):
        """§13.73.26: оси потока TK-681 (nf / ev / ea / el / eh) не совместимы со столбцом таблицы признаков — ключи (монета, sig_ms) потока не совпадают с таблицей; natr_min и sm — совместимы."""
        for side in "LS":
            f = self.axis(side, "TPM60")
            for keys in (("nf", "natr_min"), ("ev", "ea"), ("ev", "ea", "el"), ("ev", "ea", "eh")):
                a = dict(name="A", cls="x680key", keys=keys, x680=True, key="A", gain=0.1)
                self.assertEqual(ax.compat(side, f, a), (False, x6.R_COLKEY), keys)
                self.assertEqual(ax.compat(side, a, f), (False, x6.R_COLKEY), keys)
            for keys in (("natr_min",), ("sm",), ("s",)):
                a = dict(name="A", cls="x680key", keys=keys, x680=True, key="A", gain=0.1)
                self.assertTrue(ax.compat(side, f, a)[0], keys)

    def test_same_column_same_sign(self):
        self.assertFalse(self.ok("btc_mv5.le", "btc_mv5.le@c")[0])               # один столбец и знак в двух наборах
        self.assertFalse(self.ok("ok10_d7.ge", "ok10_d7.ge@h")[0])               # ok10_d7 в основном = ok10_d7 истории пробоев (TK-680: столбец переименован в таблицах, COL_MAP его больше не сводит)
        self.assertTrue(self.ok("mv15.le", "ok10_d7.ge")[0])
        self.assertTrue(self.ok("ok10_d3.le@h", "ok10_d7.ge@h")[0])

    def test_compound_common_key(self):
        a = dict(name="A", cls="x680key", keys=("e2", "xb"), x680=True, key="A", gain=0.1)
        b = dict(name="B", cls="x680key", keys=("e2", "ew"), x680=True, key="B", gain=0.1)
        c = dict(name="C", cls="x680key", keys=("ew",), x680=True, key="C", gain=0.1)
        self.assertEqual(x6.compat("S", a, b), (False, x6.R_COMPOUND))
        self.assertIsNone(x6.compat("S", a, c))

    def test_banned_reasons_counted(self):
        rule, banned = x6.pairs_by_rule("L")
        by = {}
        for _, _, w in banned:
            by[w] = by.get(w, 0) + 1
        for r in (x6.R_XB, x6.R_TPP, x6.R_DUP, x6.R_SHAPE, x6.R_COLKEY, x6.R_SAMECOL):
            self.assertIn(r, by)
        self.assertEqual(len(rule) + len(banned), len(rule) + sum(by.values()))

    def test_old_x_old_pairs_not_planned(self):
        g, sk = ax.pair_grids("L")
        new = set(self.names("L")) | {"natr_min"}
        for a, b in g:
            self.assertTrue(a in new or b in new, (a, b))
        self.assertNotIn(("s", "rf"), g)
        rule, banned = x6.pairs_by_rule("L")
        self.assertEqual(len(g), len(rule))
        self.assertEqual(len(sk), len(banned))


class Selection(X680Case):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.thr = {}
        for side in "LS":
            cls.thr[side] = dict(old_thr(side), **x6.thresholds(side))
        cls.big = {s: x6.select(s, lambda keys: {}, 10 ** 9, cls.thr[s]) for s in "LS"}

    def test_order_by_gain_then_name(self):
        rule, _ = x6.pairs_by_rule("L")
        self.assertEqual([p["pair"] for p in rule], [p["pair"] for p in sorted(rule, key=lambda p: (-p["gain"], p["pair"]))])
        g = {a["name"]: a["gain"] for a in ax.axes("L") if a.get("x680")}
        for p in rule:
            self.assertAlmostEqual(p["gain"], max(g.get(p["a"], -1), g.get(p["b"], -1)))      # у пары двух новых — больший
        self.assertAlmostEqual(rule[0]["gain"], 0.9)                                          # xb — наибольший прирост
        pairs = [p["pair"] for p in rule]
        self.assertLess(pairs.index("ls|mv15.le"), pairs.index("s|ls"))                       # пара двух новых с прирастом 0,7 раньше пары «ls × стоп» с 0,5
        tie = [p["pair"] for p in rule if abs(p["gain"] - 0.9) < 1e-9]
        self.assertEqual(tie, sorted(tie))

    def test_all_pairs_taken_without_cap(self):
        for side in "LS":
            sel = self.big[side]
            self.assertEqual(len(sel["taken"]), sel["n_rule"])
            self.assertEqual(sel["left_out"], [])

    def test_cap_cuts_whole_pairs(self):
        side = "L"
        big = self.big[side]["taken"]
        cum, tot = [], 0
        for p in big:
            tot += p["new"]
            cum.append(tot)
        for cap in (0, 1, 50, 137, cum[40], cum[40] - 1, cum[-1], cum[-1] + 5):
            sel = x6.select(side, lambda keys: {}, cap, self.thr[side])
            k = next((i for i, c in enumerate(cum) if c > cap), len(cum))     # префикс по порядку: первая не поместившаяся пара — стоп
            self.assertEqual([p["pair"] for p in sel["taken"]], [p["pair"] for p in big[:k]], cap)
            self.assertLessEqual(sel["n_new"], cap)
            self.assertEqual(sel["n_new"], cum[k - 1] if k else 0)
            self.assertEqual([p["pair"] for p in sel["left_out"]][:3], [p["pair"] for p in big[k:k + 3]])
            self.assertEqual(len(sel["left_out"]), len(big) - k)
        sel = x6.select(side, lambda keys: {}, cum[40] - 1, self.thr[side])
        self.assertEqual(len(sel["taken"]), next(i for i, c in enumerate(cum) if c > cum[40] - 1))
        self.assertGreater(sel["n_new"] + big[len(sel["taken"])]["new"], cum[40] - 1)         # первая оставшаяся пара не помещается целиком

    def test_cells_in_db_do_not_count(self):
        side = "L"
        rule, _ = x6.pairs_by_rule(side)
        first = ax.pair_grid(side, self.axis(side, rule[0]["a"]), self.axis(side, rule[0]["b"]))
        have = {ax.cell_ckey(side, p, f, self.thr[side]): {} for p, f, ok, w in first["cells"].values() if ok}
        sel = x6.select(side, lambda keys: {k: have[k] for k in keys if k in have}, 10 ** 9, self.thr[side])
        self.assertEqual(sel["taken"][0]["new"], 0)
        self.assertEqual(sel["n_have"], self.big[side]["n_have"] + len(have))
        self.assertEqual(sel["n_new"], self.big[side]["n_new"] - len(have))                  # найденные в БД клетки потолок не тратят

    def test_new_x_old_cell_keys(self):
        for side in "LS":
            thr = self.thr[side]
            # новая простая × прежняя простая: ls = 0,5 × s = 2 → строка базы + s=2 ls=0.5
            patch, filt, ok, why = ax.cell_assign(side, {"s": 2, "ls": "0.5"})
            self.assertEqual((patch, filt, ok), ({"s": 2, "ls": 0.5}, [], True))
            self.assertEqual(ax.cell_ckey(side, patch, filt, thr), ax.canon(ax.BASE_LINE(side) + " s=2 ls=0.5"))
            # составная новая × прежняя: xb = 0,4 × rf = 0,1 (у шорта xb несёт e2 = 0)
            patch, filt, ok, why = ax.cell_assign(side, {"rf": 0.1, "xb": "0.4"})
            want = {"rf": 0.1, "xb": 0.4, "s": ax.BASE_STOP[side]}
            if side == "S":
                want["e2"] = 0
            self.assertEqual((patch, ok), (want, True))
            self.assertEqual(ax.cell_ckey(side, patch, filt, thr), ax.canon(ax.BASE_LINE(side) + " " + ax.patch_str(want)))
            # новая × прежняя признаков: fh = 2 нельзя; ls = 1 × TPM60 = q60 — фильтр по смыслу
            patch, filt, ok, why = ax.cell_assign(side, {"TPM60": "q60", "ls": "1"})
            self.assertEqual((filt, ok), ([("TPM60", "q60")], True))
            self.assertEqual(ax.cell_ckey(side, patch, filt, thr), ax.canon(ax.BASE_LINE(side) + " ls=1 s=%d" % ax.BASE_STOP[side], [thr["TPM60"]["q60"]]))
            # новая столбца × прежняя: mv15 ≤ q20 × s = 1
            patch, filt, ok, why = ax.cell_assign(side, {"mv15.le": "q20", "s": 1})
            self.assertEqual((patch, filt, ok), ({"s": 1}, [("mv15.le", "q20")], True))
            self.assertEqual(ax.cell_ckey(side, patch, filt, thr), ax.canon(ax.BASE_LINE(side) + " s=1", [("mv15", "<=", 1.5)]))
            self.assertTrue(ax.cell_ckey(side, patch, filt, thr).endswith("F=mv15<=1.5"))

    def test_new_x_new_column_pair_two_filters(self):
        for side in "LS":
            g = ax.pair_grid(side, self.axis(side, "mv15.le"), self.axis(side, "ok10_d7.ge"))
            a, b = self.axis(side, "mv15.le"), self.axis(side, "ok10_d7.ge")
            i, j = a["vals"].index("q40"), b["vals"].index("q60")
            patch, filt, ok, why = g["cells"][(i, j)]
            self.assertEqual((patch, ok, sorted(filt)), ({"s": ax.BASE_STOP[side]}, True, [("mv15.le", "q40"), ("ok10_d7.ge", "q60")]))
            ck = ax.cell_ckey(side, patch, filt, self.thr[side])
            self.assertEqual(ck, ax.canon(ax.BASE_LINE(side) + " s=%d" % ax.BASE_STOP[side], [("mv15", "<=", 3.0), ("ok10_d7", ">=", 4.0)]))
            self.assertTrue(ck.endswith("F=mv15<=3&ok10_d7>=4"))
            self.assertEqual(len([t for t in ck.split("F=")[1].split("&")]), 2)

    def test_conflicting_keys_cell_invalid(self):
        side = "S"
        am = dict(self.axis_map_side(side))
        fake = dict(name="A", cls="x680key", key="A", keys=("e2",), vals=["a", "b"], base="b", patches={"a": {"e2": 0}, "b": {}}, x680=True, kind="key", gain=0.1)
        am["A"] = fake
        self.assertNotEqual(x6.cell_check(side, am, {"A": "a", "e2": 0.05}), "")          # ось A требует e2 = 0, ось e2 — 0,05
        self.assertEqual(x6.cell_check(side, am, {"A": "a", "e2": 0}), "")
        self.assertEqual(x6.cell_check(side, am, {"A": "b", "e2": 0.05}), "")            # база оси A при базе шорта e2 = 0,05 — согласована
        self.assertNotEqual(x6.cell_check(side, am, {"A": "b", "e2": 0.1}), "")

    def axis_map_side(self, side):
        return ax.axis_map(side)

    def test_pool_weakened_with_column_invalid(self):
        patch, filt, ok, why = ax.cell_assign("L", {"mv15.le": "q20", "natr_min": 0.2})
        self.assertFalse(ok)                                                                # ослабленный пул не покрыт строками таблицы признаков
        patch, filt, ok, why = ax.cell_assign("L", {"mv15.le": "q20", "natr_min": 1.0})
        self.assertTrue(ok)                                                                 # расширенное значение оси карты — пул уже, чем база

    def test_fingerprint_changes_with_axes(self):
        a = x6.fingerprint("L")
        self.assertEqual(a, x6.fingerprint("L"))
        self.assertNotEqual(a, x6.fingerprint("S"))

    def test_neighbours_inside_form(self):
        sg = self.axis("L", "sg")["vals"]
        self.assertEqual([sg[j] for j in ax.nb_idx("sg", sg, sg.index("sb1"))], ["sb0.5", "sb2"])
        self.assertEqual(ax.nb_idx("sg", sg, sg.index("off")), [])
        self.assertEqual([sg[j] for j in ax.nb_idx("sg", sg, sg.index("sb2"))], ["sb1"])                # sr1.25 — другая форма
        ls = self.axis("L", "ls")["vals"]
        self.assertEqual(ax.nb_idx("ls", ls, 1), [0, 2])
        self.assertEqual(ax.nb_idx("mv15.le", ["q20", "q40", "off"], 1), [0, 2])                       # столбцы — на шаг, как всегда


class PassMasks(unittest.TestCase):
    """`tk674_run._pass`: оси карты TK-674 — прежняя логика; столбцы единичек (режим X680) — по знаку смысла, пустое значение проходит."""

    def setUp(self):
        try:
            import numpy as np
            import tk674_run as run
        except Exception as e:                                  # numpy / srv-модули нужны только этому тесту
            self.skipTest("нет tk674_run: %s" % e)
        self.np, self.run = np, run
        nan = float("nan")
        self.tab = dict(tpm60=np.array([1.0, 2.0, nan, 5.0]), cons60=np.array([0.0, 1.0, 2.0, nan]), mv5=np.array([3.0, 1.0, 1.0, 0.5]), n24=np.array([10.0, 90.0, 50.0, 70.0]),
                        m24=np.array([False, False, True, False]), mv15=np.array([1.0, 2.0, nan, 4.0]), ok10_d7=np.array([0.0, 1.0, 2.0, 3.0]))
        self.old = (ax.X680, run.LEVEL)

    def tearDown(self):
        ax.X680, self.run.LEVEL = self.old

    def test_default_unchanged(self):
        ax.X680, self.run.LEVEL = False, 2
        p = self.run._pass
        self.assertEqual(p("tpm60", 2.0, self.tab, "<=").tolist(), [True, True, True, False])
        self.assertEqual(p("n24", 60.0, self.tab, ">=").tolist(), [False, True, True, True])         # пустое (m24) проходит; ниже порога — нет
        self.assertEqual(p("sharp", 1.0, self.tab, "==").tolist(), [True, False, False, True])
        with self.assertRaises(SystemExit):
            p("mv15", 2.0, self.tab, "<=")                       # столбцов единичек без режима X680 нет

    def test_x680_columns_by_sign(self):
        ax.X680, self.run.LEVEL = True, 2
        p = self.run._pass
        self.assertEqual(p("mv15", 2.0, self.tab, "<=").tolist(), [True, True, True, False])        # nan проходит
        self.assertEqual(p("mv15", 2.0, self.tab, ">=").tolist(), [False, True, True, True])
        self.assertEqual(p("ok10_d7", 1.0, self.tab, ">=").tolist(), [False, True, True, True])
        self.assertEqual(p("tpm60", 2.0, self.tab, ">=").tolist(), [False, True, True, True])       # вторая сторона порога оси TPM60 — по знаку
        self.assertEqual(p("n24", 60.0, self.tab, "<=").tolist(), [True, False, True, False])        # n24 ≤: маска m24 → пусто → проходит; 70 > 60 — нет
        # оси карты TK-674 в режиме X680 — те же маски, что без него
        ax.X680 = False
        base = [self.run._pass(f, x, self.tab, o).tolist() for f, x, o in (("tpm60", 2.0, "<="), ("cons60", 1.0, "<="), ("n24", 60.0, ">="), ("sharp", 1.0, "=="))]
        ax.X680 = True
        self.assertEqual([self.run._pass(f, x, self.tab, o).tolist() for f, x, o in (("tpm60", 2.0, "<="), ("cons60", 1.0, "<="), ("n24", 60.0, ">="), ("sharp", 1.0, "=="))], base)

    def test_level1_unchanged(self):
        ax.X680, self.run.LEVEL = False, 1
        self.assertEqual(self.run._pass("mv15", 2.0, self.tab, "<=").tolist(), [True, True, True, False])
        with self.assertRaises(SystemExit):
            self.run._pass("mv15", 2.0, self.tab, "==")


class MapSupport(X680Case):
    """Карта (`cmd_map`, `tk674_mapmd`) для новых осей: индекс базы оси столбца — «off», оси ключа — её метка базы; текст отчёта. Сама стадия map (БД) здесь не гоняется."""

    def test_base_idx(self):
        try:
            import tk674_run as run
        except Exception as e:
            self.skipTest("нет tk674_run: %s" % e)
        a = self.axis("L", "mv15.le")
        self.assertEqual(run.base_idx("L", "mv15.le", a["vals"]), a["vals"].index("off"))
        self.assertEqual(run.base_idx("L", "ok10_d7.ge", self.axis("L", "ok10_d7.ge")["vals"]), 0)
        self.assertEqual(run.base_idx("L", "sg", self.axis("L", "sg")["vals"]), 0)
        self.assertEqual(run.base_idx("L", "ls", self.axis("L", "ls")["vals"]), 0)
        self.assertEqual(run.base_idx("L", "natr_min", self.axis("L", "natr_min")["vals"]), 1)
        self.assertEqual(run.base_idx("L", "ew", self.axis("L", "ew")["vals"]), 0)

    def test_mapmd_paragraph(self):
        import tk674_mapmd as md
        base = dict(s=3, rr=2.0, lstop=15.4, worst=16.0, n_over=1, usd=100.0, mean_usd=1.0, n=100, sharpe=1.5, maxdd=30.0, maxdd_pct=12.0, kpi=dict(gap_longest=20, gap_mean=5.0, gap_tail=1))
        d = dict(side="L", base=base, rebase=True, level=2, pairs=[], n_cells=10, m=100, sharpe_sd=1.0, noise_max=4.0, g_dup=dict(groups=0, cells=0, examples=[]),
                 x680=dict(cap=20000, n_new_axes=3, by_set={"основной": ["ls", "xb"], "контекст": ["cosig15.le@c"]}, ext={"natr_min": ["1"]}, n_rule=40, taken=30, left_out=10, banned=5, n_new=777, axes_sig="x"))
        txt = "\n".join(md.side_md(d))
        self.assertIn("Пары с активными осями единичек TK-680", txt)
        self.assertIn("взято 30", txt)
        self.assertIn("natr_min += 1", txt)
        d.pop("x680")
        self.assertNotIn("единичек TK-680", "\n".join(md.side_md(d)))


PLAN_CHILD = r'''
import json, os, sys
sys.path.insert(0, %(here)r)
import tk674_run as run
class Store:
    def __init__(self, have): self.have = have
    def cells_lookup(self, side, uni, win, keys, bins=None): return {k: {} for k in dict.fromkeys(keys) if k in self.have}
run.chron_store = lambda ro=False: Store(set())
class A: cap = %(cap)d
run.cmd_plan(A())
print(json.dumps(dict(suf=run.SUF, fsuf=run.FSUF)))
'''


class PlanEndToEnd(unittest.TestCase):
    """Стадия `plan` режима X680 целиком (подставная БД): файлы с суффиксом bx, таблица, замороженный отбор, прежние файлы не затронуты."""

    def run_plan(self, root, roots, cap, **env_extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("TK674_")}
        env.update(TK674_REBASE="1", TK674_X680="1", TK674_ROOT=root, TK674_X680_ROOTS=",".join(roots), TK674_SIDES="L", PYTHONUTF8="1")
        env.update(env_extra)
        return subprocess.run([sys.executable, "-c", PLAN_CHILD % dict(here=HERE, cap=cap)], env=env, capture_output=True, text=True, cwd=tempfile.gettempdir())

    def test_plan_freeze_and_files(self):
        tmp = tempfile.mkdtemp()
        old = (ax.REBASE, ax.X680, ax.SPLIT)
        ax.REBASE, ax.X680, ax.SPLIT = True, False, False
        try:
            roots = build_roots(tmp)
        finally:
            ax.REBASE, ax.X680, ax.SPLIT = old
        rr = [roots[k] for k in ("tk680", "tk680h", "tk680g", "tk680c")]
        work = os.path.join(tmp, "work")
        _write(os.path.join(work, "res", "thr674.json"), dict(thr={s: {n: {lab: list(v) for lab, v in d.items()} for n, d in old_thr(s).items()} for s in "LS"}))
        _write(os.path.join(work, "res", "eqbins674.json"), ["a" * 32])
        for f in ("plan674-L.json", "plan674b-L.json"):                                     # прежние файлы плана рядом — не трогаются
            _write(os.path.join(work, "res", f), {"keep": f})
        r = self.run_plan(work, rr, 300)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:] + r.stdout[-1500:])
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]), dict(suf="bx", fsuf="x"))
        self.assertIn("[X680]", r.stdout)
        self.assertIn("запрещённых пар", r.stdout)
        self.assertIn("за потолком 300 новых клеток", r.stdout)
        pf = os.path.join(work, "res", "plan674bx-L.json")
        plan = json.load(open(pf, encoding="utf-8"))
        sel = plan["x680"]
        self.assertGreater(len(sel["taken"]), 0)
        self.assertGreater(len(sel["left_out"]), 0)                                          # потолок 300 режет
        self.assertLessEqual(sel["n_new"], 300)
        self.assertEqual(plan["n_todo"], sel["n_new"])                                      # «считаем» плана = новые клетки отбора (в БД пусто)
        self.assertEqual(set(plan["by_grid"]), {p["pair"].replace("|", "×") for p in sel["taken"]})
        for f in ("plan674-L.json", "plan674b-L.json"):
            self.assertEqual(json.load(open(os.path.join(work, "res", f), encoding="utf-8")), {"keep": f})
        # повторный plan (после validate) — тот же отбор, даже с другим потолком
        r2 = self.run_plan(work, rr, 100000)
        self.assertEqual(r2.returncode, 0, r2.stderr[-1500:])
        self.assertIn("заморожен", r2.stdout)
        plan2 = json.load(open(pf, encoding="utf-8"))
        self.assertEqual([p["pair"] for p in plan2["x680"]["taken"]], [p["pair"] for p in sel["taken"]])
        # TK674_X680_REPLAN=1 — заново с новым потолком
        r3 = self.run_plan(work, rr, 100000, TK674_X680_REPLAN="1")
        self.assertEqual(r3.returncode, 0, r3.stderr[-1500:])
        self.assertEqual(len(json.load(open(pf, encoding="utf-8"))["x680"]["left_out"]), 0)
        # другой набор осей при замороженном отборе — отказ
        r4 = self.run_plan(work, rr[:1], 100000)
        self.assertNotEqual(r4.returncode, 0)
        self.assertIn("других осей", r4.stderr + r4.stdout)

    def test_x680_needs_rebase(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("TK674_")}
        env.update(TK674_X680="1", PYTHONUTF8="1")
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import tk674_axes" % HERE], env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("TK674_REBASE=1", r.stderr)


if __name__ == "__main__":
    unittest.main()
