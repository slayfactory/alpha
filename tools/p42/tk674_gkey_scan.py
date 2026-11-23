#!/usr/bin/env python3
"""П-42 TK-674: полный список клеток-фильтров, у которых движок не нашёл строку таблицы фильтров для части кандидатов (`filter_[cands, dropped, no_key, no_value]` в meta.json клетки, G-key: no_key > 0).
`tables` печатает только первые 30 нарушений; здесь — все клетки, разбор по заплатке клетки (оси без фильтра) и по окну → `res/gkey674<суф>-<сторона>.json`. Без движка и без P&L.
Запуск на srv в среде шага: `python3 tk674_gkey_scan.py`."""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk604_run as r4  # noqa: E402
import tk674_run as run  # noqa: E402


def main():
    st, r5, _, _ = run.t._srv()
    store = run.chron_store(ro=True)
    for side in run.SIDES:
        cells, _, _, _, found = run.plan_side(side, store)
        index = run.batch_index(side, cells, found, st)
        where = {ck: nm for nm, cks in index.items() for ck in cks}
        bad, n_f = [], 0
        for ck, c in cells.items():
            if not c["filt"] or ck not in where:
                continue
            n_f += 1
            fc_w = {}
            for w in st.TRADE_WINDOWS:
                meta = json.load(open(os.path.join(run.ROOT, "cells", "%s-%s" % (w.name, side), where[ck], c["cid"], "meta.json"), encoding="utf-8"))[run.SIDENAME[side]]
                fc_w[w.name] = r4.filter_counters(meta)
            if any(fc is None or fc["no_key"] or fc["no_value"] for fc in fc_w.values()):
                bad.append(dict(cid=c["cid"], ckey=ck, patch=c["patch"], filt=[list(x) for x in c["filt"]], fc=fc_w))
        by_patch = collections.Counter(tuple(sorted(b["patch"])) for b in bad)
        by_key = collections.Counter(k for b in bad for k in b["patch"])
        by_filt = collections.Counter(f[0] for b in bad for f in b["filt"])
        json.dump(dict(side=side, n_filter_cells=n_f, n_bad=len(bad), bad=bad), open(run.t.q("res", "gkey674%s-%s.json" % (run.SUF, side)), "w", encoding="utf-8"), ensure_ascii=False)
        print("== %s: клеток-фильтров %d, с no_key / no_value > 0: %d" % (side, n_f, len(bad)))
        print("   по ключам заплатки: %s" % ", ".join("%s %d" % kv for kv in by_key.most_common(12)))
        print("   по осям фильтра: %s" % ", ".join("%s %d" % kv for kv in by_filt.most_common(12)))
        print("   по наборам ключей заплатки: %s" % "; ".join("%s %d" % ("+".join(k), n) for k, n in by_patch.most_common(8)))


if __name__ == "__main__":
    main()
