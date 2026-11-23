#!/usr/bin/env python3
"""П-42 TK-674: охрана G-key по ВСЕМ посчитанным клеткам-фильтрам каталогов клеток (запись летописи, прочитанная раньше охраны, могла содержать клетки с `no_key` > 0):
в `meta.json` клетки счётчик `filter_[cands, dropped, no_key, no_value]` стороны; клетка-фильтр — та, где счётчик есть. Без движка и без P&L.
Запуск на srv юнитом (много мелких файлов): `python3 tk674_gkey_all.py <выход.json> <корень> [<корень> …]` (корень — каталог с `cells/<окно>-<сторона>/<партия>/<cid>/meta.json`)."""
import collections
import json
import os
import sys


def scan_root(root):
    res = collections.defaultdict(lambda: dict(filter_cells=0, no_key=0, no_value=0, bad_cids=set(), cids=set(), n_files=0))
    cells = os.path.join(root, "cells")
    for wd in sorted(os.listdir(cells)):
        if "-" not in wd:
            continue
        side = wd.split("-", 1)[1]
        sname = {"L": "long", "S": "short"}.get(side, side)
        r = res[side]
        for bd in os.scandir(os.path.join(cells, wd)):
            if not bd.is_dir():
                continue
            for cd in os.scandir(bd.path):
                mf = os.path.join(cd.path, "meta.json")
                if not os.path.exists(mf):
                    continue
                r["n_files"] += 1
                try:
                    fc = json.load(open(mf, encoding="utf-8")).get(sname, {}).get("filter_[cands,dropped,no_key,no_value]")
                except (OSError, ValueError):
                    fc = None
                if fc is None:
                    continue
                r["filter_cells"] += 1
                r["cids"].add(cd.name)
                if fc[2]:
                    r["no_key"] += 1
                    r["bad_cids"].add(cd.name)
                if fc[3]:
                    r["no_value"] += 1
    return {s: dict(n_files=v["n_files"], filter_cells_x_windows=v["filter_cells"], filter_cids=len(v["cids"]), no_key_cells_x_windows=v["no_key"], no_key_cids=len(v["bad_cids"]),
                    no_value_cells_x_windows=v["no_value"], bad_cids_sample=sorted(v["bad_cids"])[:12]) for s, v in res.items()}


def main():
    out, roots = sys.argv[1], sys.argv[2:]
    allr = {}
    for root in roots:
        allr[root] = scan_root(root)
        for s, v in allr[root].items():
            print("%-26s %s: файлов %d, клеток-фильтров (× окна) %d (cid %d), no_key > 0: %d (cid %d), no_value > 0: %d" % (
                root, s, v["n_files"], v["filter_cells_x_windows"], v["filter_cids"], v["no_key_cells_x_windows"], v["no_key_cids"], v["no_value_cells_x_windows"]), flush=True)
    json.dump(allr, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
