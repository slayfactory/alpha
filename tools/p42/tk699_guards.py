#!/usr/bin/env python3
"""TK-699 шаг 3: охраны стадии `tables` TK-674 по ВСЕМ клеткам пересчёта (стадия печатает только первые 30 нарушений) — без записи в БД.

Те же `_row_job` / `eff_flags` / `variant_line`, что в `tk674_run.cmd_tables`; результат: сколько нарушений по видам (G7, G-off, G-tape, G-key, P&L ≠ ряд), у каких клеток (по ключам патча).
G-key `no_key > 0`: кандидаты клетки без строки таблицы признаков (потоки со сдвигом входа `ea` / `ev` — часть кандидатов не из базовых сигналов) проходят фильтр молча — свойство клетки, как
у прежних строк (в архиве прежних клеток те же счётчики: ea0.391+ev4+s4+accdv1_300.le@t_q20 — [114, 41, 59, 0]).

  tk699_guards.py --side S --list changed|k3 --out guards-S.json     (среда — env-rest-l.sh + TK674_ROOT=/data/p42-tk699/r674 TK674_LEVEL=3 TK674_BATCH=t699 TK674_SIDES=S)
"""
from __future__ import annotations

import argparse
import collections
import json
import multiprocessing as mp
import os
import sys

sys.path.insert(0, os.environ.get("TK699_CODE674", "/data/p42-tk674c/code-bd"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--side", required=True, choices=("S", "L"))
    ap.add_argument("--list", required=True, choices=("changed", "k3"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import tk674_run as R
    st, r5, _, _ = R.t._srv()
    u = r5.Uni(R.UNI)
    lists = json.load(open("%s/res/recount-lists-%s.json" % (R.ROOT, a.side), encoding="utf-8"))
    keep = set(lists[a.list])
    raw = json.load(open(R.level_path(a.side), encoding="utf-8"))["cells"]
    index = json.load(open(os.path.join(R.ROOT, "res", "batches674%s-%s.json" % (R.SUF, a.side)), encoding="utf-8"))
    where = {ck: nm for nm, cks in index.items() for ck in cks}
    jobs = []
    for k, c in raw.items():
        if c["cid"] not in keep or k not in where:
            continue
        filt = [tuple(x) for x in c["filt"]]
        jobs.append((a.side, k, c["cid"], where[k], R.t.variant_line(r5, u, a.side, c["patch"]), c["patch"], filt, R.eff_flags(a.side, c["patch"])))
    kinds, cells = collections.Counter(), collections.defaultdict(set)
    errors = []
    with mp.Pool(3, initializer=R._winit) as pool:
        for res in pool.imap_unordered(R._row_job, [j for j in jobs], chunksize=8):
            if "error" in res:
                errors.append(res["error"])
                continue
            for e in res["errs"]:
                kinds[e.split(":")[0] + (":" + e.split(": ", 1)[1].split(" ")[0] if e.startswith("G-key") else "")] += 1
                cells[e.split(":")[0]].add(res["ckey"])
    patch_sig = collections.Counter()
    for k in cells.get("G-key", set()):
        patch_sig[tuple(sorted(raw[k]["patch"]))] += 1
    out = dict(side=a.side, list=a.list, cells=len(jobs), errors_row_job=errors[:20], n_errors_row_job=len(errors), violations=dict(kinds),
               cells_by_guard={g: len(v) for g, v in cells.items()}, gkey_patch_keys={"+".join(k): n for k, n in patch_sig.most_common()},
               gkey_cells=sorted(cells.get("G-key", set())))
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("guards %s %s: клеток %d, ошибок row_job %d, нарушения %s, клеток по охранам %s; G-key по ключам патча %s" % (
        a.side, a.list, len(jobs), len(errors), dict(kinds), out["cells_by_guard"], out["gkey_patch_keys"]), flush=True)


if __name__ == "__main__":
    main()
