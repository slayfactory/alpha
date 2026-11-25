#!/usr/bin/env python3
"""П-42 §13.73.3 (TK-674): остаток уровня за потолком — размер без P&L и без записи в res/ счёта. Тройки (четвёрки), которые `tk674_level.py select <n> <cap>` не взял,
в том же порядке (PS-956 разд. 2 п. 4), клетки остатка (канонические ключи, без уже взятых в отборе) → «есть в БД / считаем», проходы сигналов, классы, партии.
  python3 tk674_rest.py <n> [--out <файл.json>]          окружение — как у `tk674_level.py select` (TK674_ROOT, TK674_UNI, TK674_REBASE, TK674_SPLIT*, TK674_X680*)
Читает `res/level674<SUF2><n>-<сторона>.json` (взятые) и карты пар; пишет только `--out` (по умолчанию в `log/`)."""
from __future__ import annotations

import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_axes as ax  # noqa: E402
import tk674_level as lv  # noqa: E402
import tk674_run as run  # noqa: E402


def rest_side(side, n, store, bins):
    thr = lv.load_thr(side)
    act, n_sub = lv.active_subs(n, side)
    cand = []
    for names in lv.axis_combos(side, n):
        g = [act[frozenset(c)] for c in itertools.combinations(names, n - 1) if frozenset(c) in act]
        if len(g) >= lv.SUB_MIN:
            cand.append((-sum(g), names, len(g)))
    cand.sort()
    taken = json.load(open(os.path.join(lv.ROOT, "res", "level674%s%d-%s.json" % (ax.SUF2, n, side)), encoding="utf-8"))
    nt = len(taken["taken"])
    if [list(c[1]) for c in cand[:nt]] != [x["names"] for x in taken["taken"]]:
        raise SystemExit("%s: порядок отбора не совпал с файлом уровня" % side)
    cells, rest, n_new, n_have = dict(taken["cells"]), [], 0, 0
    for neg, names, na in cand[nt:]:
        cc = lv.combo_cells(side, names, thr, {})
        fresh = {k: c for k, c in cc.items() if k not in cells}
        found = store.cells_lookup(lv.SIDENAME[side], lv.UNI, lv.WIN, list(fresh), bins=bins)
        new = [k for k in fresh if k not in found]
        for k, c in fresh.items():
            cells[k] = dict(cid=c["cid"], patch=c["patch"], filt=c["filt"], sig=c["sig"])
        n_new += len(new)
        n_have += len(fresh) - len(new)
        rest.append(dict(names=list(names), score=-neg, n_act=na, cells=len(cc), fresh=len(fresh), new=len(new)))
    new_cells = {k: c for k, c in cells.items() if k not in taken["cells"]}
    sigs = {tuple(map(tuple, c["sig"])) if not isinstance(c["sig"], tuple) else c["sig"] for c in new_cells.values() if c["sig"]}
    return dict(side=side, n=n, taken=nt, rest_triples=len(rest), rest_cells_fresh=n_have + n_new, in_db=n_have, new=n_new, sigs_all=len(sigs),
                filt_cells=sum(1 for c in new_cells.values() if c["filt"]), rest=rest)


def main():
    n = int(sys.argv[1])
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(lv.ROOT, "log", "rest%d.json" % n)
    store = run.chron_store(ro=True)
    bins = run.eq_bins()
    res = {}
    for side in ax.SIDES:
        r = rest_side(side, n, store, bins)
        res[side] = r
        print("== %s n=%d: взято %d, остаток %d троек, клеток %d (в БД %d, считаем %d), проходов сигналов %d, клеток с фильтром %d" % (
            side, n, r["taken"], r["rest_triples"], r["rest_cells_fresh"], r["in_db"], r["new"], r["sigs_all"], r["filt_cells"]), flush=True)
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
