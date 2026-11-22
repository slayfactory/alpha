#!/usr/bin/env python3
"""П-42 §13.73.1 (TK-674): уровни n = 3, 4 карты осей — клетки комбинаций из n осей, счёт «есть / считаем» по БД летописи. Без движка и без P&L.
  python3 tk674_level.py count <n> [--rule <имя>]   — комбинации осей, разные клетки (канонические ключи), есть в БД / считаем, проходы сигналов, оценка часов
Комбинация осей допустима, если все пары осей совместимы (`tk674_axes.compat`); клетка — произведение значений осей с теми же охранами `cell_assign`
(t_low < t_high, min-лот ≥ 5 $, lx ≠ 0 только при tf = 60, пул × признак, стоп ≤ 5 %). Канонический ключ — `tk674_axes.canon` (составной фильтр — «F=…» по смыслу)."""
from __future__ import annotations

import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_axes as ax  # noqa: E402

ROOT = os.environ.get("TK674_ROOT", "/data/p42-tk674")
SIDENAME = {"L": "long", "S": "short"}
UNI, WIN = "h775", "01.07–13.11"


def axis_combos(side, n):
    """Все комбинации из n осей стороны со всеми совместимыми парами."""
    axs = ax.axes(side)
    out = []
    for c in itertools.combinations(axs, n):
        if all(ax.compat(side, a, b)[0] for a, b in itertools.combinations(c, 2)):
            out.append(tuple(a["name"] for a in c))
    return out


def combo_cells(side, names, thr, cells=None, only_all_moved=False):
    """Клетки комбинации осей → {ckey: клетка}; thr — {ось: {метка: (признак, знак, порог)}} из res/thr674.json.
    only_all_moved — только позиции, где каждая ось сдвинута с базы (остальные позиции — клетки комбинаций меньшего уровня)."""
    amap = ax.axis_map(side)
    cells = {} if cells is None else cells
    base_line = ax.BASE_LINE(side)
    vals = [amap[n]["vals"] for n in names]
    for vs in itertools.product(*vals):
        if only_all_moved and any(v == amap[n]["base"] for n, v in zip(names, vs)):
            continue
        patch, filt, ok, why = ax.cell_assign(side, dict(zip(names, vs)))
        if not ok:
            continue
        ft = [(thr[n][lab][0], thr[n][lab][1], thr[n][lab][2]) for n, lab in filt] if filt else []
        ck = ax.canon(base_line + (" " + ax.patch_str(patch) if patch else ""), ft)
        c = cells.get(ck)
        if c is None:
            c = cells[ck] = dict(ckey=ck, cid=ax.cell_id(patch, filt), patch=patch, filt=filt, sig=ax.sigkey(patch), combos=[])
        c["combos"].append(names)
    return cells


def level_cells(side, n, thr, combos=None, only_all_moved=False):
    cells = {}
    for names in (combos if combos is not None else axis_combos(side, n)):
        combo_cells(side, names, thr, cells, only_all_moved)
    return cells


def load_thr(side):
    raw = json.load(open(os.path.join(ROOT, "res", "thr674.json"), encoding="utf-8"))["thr"][side]
    return {n: {lab: tuple(v) for lab, v in d.items()} for n, d in raw.items()}


def cmd_count(n):
    import tk674_run as run
    store = run.chron_store(ro=True)
    bins = run.eq_bins()
    for side in ax.SIDES:
        thr = load_thr(side)
        combos = axis_combos(side, n)
        cells = level_cells(side, n, thr, combos)
        found = store.cells_lookup(SIDENAME[side], UNI, WIN, list(cells), bins=bins)
        todo = [c for k, c in cells.items() if k not in found]
        cls = {}
        for k, c in cells.items():
            kind = "фильтр" if c["filt"] else ("сигналы" if c["sig"] else "выход")
            row = cls.setdefault(kind, [0, 0])
            row[0] += 1
            row[1] += k in found
        sigs = {c["sig"] for c in todo if c["sig"]}
        print("== %s n=%d: комбинаций осей %d, разных клеток %d, в БД есть %d, считаем %d; проходов сигналов %d; %s" % (
            side, n, len(combos), len(cells), len(found), len(todo), len(sigs),
            ", ".join("%s %d/%d" % (k, v[0] - v[1], v[0]) for k, v in sorted(cls.items()))))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "count":
        cmd_count(int(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
