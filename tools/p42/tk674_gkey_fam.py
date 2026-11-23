#!/usr/bin/env python3
"""П-42 TK-674: делит ли G-key (no_key > 0) семейства клеток целиком или по частям. По плану шага (`plan_side`) берёт клетки-фильтры с ключами потока (nf, ev, ea, el, eh) и считает
по каждой паре (поток × столбец фильтра) клеток с no_key = 0 и > 0 (по `res/gkey674<суф>-<сторона>.json`, `tk674_gkey_scan.py`); печатает потоки, где есть и те и другие. Без движка и без P&L.
Запуск на srv в среде шага: `python3 tk674_gkey_fam.py` → `res/gkeyfam674<суф>-<сторона>.json`."""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_run as run  # noqa: E402

STREAM_KEYS = ("nf", "natr_min", "ev", "ea", "el", "eh")


def main():
    store = run.chron_store(ro=True)
    for side in run.SIDES:
        bad = {b["cid"] for b in json.load(open(run.t.q("res", "gkey674%s-%s.json" % (run.SUF, side)), encoding="utf-8"))["bad"]}
        cells = run.plan_side(side, store)[0]
        fam = collections.defaultdict(lambda: [0, 0])
        for c in cells.values():
            p = c["patch"]
            if not c["filt"] or not ({"nf", "ev", "ea", "el", "eh"} & set(p)):
                continue
            stream = tuple((k, p[k]) for k in STREAM_KEYS if k in p)
            for col in sorted({f[0] for f in c["filt"]}):
                fam[(stream, col)][c["cid"] in bad] += 1
        by_stream = collections.defaultdict(lambda: [0, 0])
        for (stream, _), (ok, nb) in fam.items():
            by_stream[stream][0] += ok
            by_stream[stream][1] += nb
        n_ok = sum(v[0] for v in fam.values())
        n_bad = sum(v[1] for v in fam.values())
        split = [(s, v) for s, v in by_stream.items() if v[0] and v[1]]
        split_cols = [(k, v) for k, v in fam.items() if v[0] and v[1]]
        print("== %s: пар (поток × столбец) %d; клеток с ключом потока и фильтром: no_key = 0 — %d, > 0 — %d; потоков с обеими долями %d из %d; пар поток × столбец с обеими долями %d" % (
            side, len(fam), n_ok, n_bad, len(split), len(by_stream), len(split_cols)))
        for s, v in split[:12]:
            print("   поток %s: no_key = 0 — %d, > 0 — %d" % (",".join("%s=%s" % kv for kv in s), v[0], v[1]))
        json.dump(dict(side=side, no_key_zero=n_ok, no_key_pos=n_bad, streams=len(by_stream), pairs=len(fam),
                       split_streams=[[list(map(list, s)), v] for s, v in split], split_pairs=[[list(map(list, k[0])), k[1], v] for k, v in split_cols]),
                  open(run.t.q("res", "gkeyfam674%s-%s.json" % (run.SUF, side)), "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
