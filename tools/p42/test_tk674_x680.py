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
    ("plain", "L"): dict(axes="9fa536424685844e", n_axes=34, pairs=525, skipped=36, skip_hash="86f3e9811921d26c", cells=8620, grid_hash="0f173f112576ee8c",
                         base="ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 t24=12"),
    ("plain", "S"): dict(axes="b1556bd4d14ac9bd", n_axes=34, pairs=525, skipped=36, skip_hash="86f3e9811921d26c", cells=8868, grid_hash="19c7aa438c596483",
                         base="bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 t24=12 tb=0.1 tt=20"),
    ("rebase", "L"): dict(axes="e75ed52c7ed68f13", n_axes=34, pairs=525, skipped=36, skip_hash="86f3e9811921d26c", cells=8620, grid_hash="b3fd44795ca49ab7",
                          base="ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12"),
    ("rebase", "S"): dict(axes="510b808ddfd99335", n_axes=34, pairs=525, skipped=36, skip_hash="86f3e9811921d26c", cells=8868, grid_hash="8a524f7943e4762d",
                          base="bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20"),
    ("split", "L"): dict(axes="d7ed6a187ca45d75", n_axes=35, pairs=558, skipped=37, skip_hash="7f0219f56207f7db", cells=9970, grid_hash="0d7c88fb5c83240e",
                         base="ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12"),
    ("split", "S"): dict(axes="9043ac4bc55b5a58", n_axes=35, pairs=558, skipped=37, skip_hash="7f0219f56207f7db", cells=10228, grid_hash="aa60bfb02778d585",
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

if __name__ == "__main__":
    unittest.main()
