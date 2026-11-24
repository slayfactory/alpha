#!/usr/bin/env python3
"""TK-699 шаг 4: пороги единичек TK-680 / TK-697 по 10 столбцам f12 на исправленной шкале (c539) — квантили 20 / 40 / 60 / 80 % по кандидатам базы в зачётном диапазоне
(`tk680_singles._thresholds`: столбец с ≤ 4 разными значениями — сами значения) против замороженных (`thr680*.json` корней /data/p42-tk680n*, /data/p42-tk697*).

  tk699_thr.py --out thr-f12.json
      контроль: те же квантили на СТАРОЙ таблице (`.pre-f12` после подмены / прежний файл до неё) равны замороженным числам; затем новая шкала. Ничего не пишет в корни TK-674 / TK-680.
Среда — как у delta (env-rest.sh), TK699_AFTER_SWAP=1 после `swap`.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.environ.get("TK699_CODE674", "/data/p42-tk674c/code-bd"))
COLS10 = ["age_h", "approach_dur_min", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h", "depth_min_bp", "depth_mean_bp", "since_touch_prior_h",
          "touch_prior_none"]
if os.environ.get("TK699_AFTER_SWAP") == "1":
    OLDP, NEWP = "/data/p42-tk671/data/%s.parquet.pre-f12", "/data/p42-tk671/data/%s.parquet"
else:
    OLDP, NEWP = "/data/p42-tk671/data/%s.parquet", "/data/p42-tk671/data/%s.parquet.tk699-new"


def frozen():
    """{(корень, файл, сторона, столбец): {le, ge, n, n_all}} из замороженных порогов, где есть 10 столбцов."""
    out = {}
    for f in sorted(glob.glob("/data/p42-tk680n*/res/thr680*.json") + glob.glob("/data/p42-tk697*/res/thr680*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for side in ("L", "S"):
            for c in COLS10:
                v = d.get(side, {}).get(c) if isinstance(d, dict) else None
                if isinstance(v, dict):
                    out[(os.path.dirname(os.path.dirname(f)), os.path.basename(f), side, c)] = v
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import tk654_run as r54
    import tk674_run as R
    import tk680_axes as a80
    import tk680_singles as s80
    st, r5, _, _ = R.t._srv()
    u = r5.Uni(R.UNI)
    uh = r5.Uni("h775")                       # замороженные пороги TK-680 взяты на h775 (PS-974); на c539 они перенесены без пересчёта (§13.73.17) — контроль на h775 до исправления
    eps = r54.episodes_h775()
    fz = frozen()
    res = dict(columns=COLS10, sides={}, frozen_files=sorted({"%s/res/%s" % (k[0], k[1]) for k in fz}))
    for side in ("S", "L"):
        info = {}
        tabs = {}
        for tag, par, uni in (("h775_old", OLDP, uh), ("old", OLDP, u), ("new", NEWP, u)):
            a80.PARQUET = par
            tabs[tag] = R.feat_tabs(side, st, uni, eps)
            s80.add_columns(side, st, tabs[tag])
        sc = {tag: {w.name: r54.in_scored_range(w, tabs[tag][w.name]["ms"]) for w in st.TRADE_WINDOWS} for tag in tabs}
        for c in COLS10:
            row = {}
            for tag in ("h775_old", "old", "new"):
                allv = np.concatenate([np.asarray(tabs[tag][w.name][c], dtype=np.float64)[sc[tag][w.name]] for w in st.TRADE_WINDOWS])
                fin = allv[np.isfinite(allv)]
                le, ge = s80._thresholds(fin, c)
                row[tag] = dict(n=int(len(fin)), n_all=int(len(allv)), distinct=int(len(np.unique(fin))), le=[[l, float(x)] for l, x in le], ge=[[l, float(x)] for l, x in ge],
                                lo=float(fin.min()) if len(fin) else None, hi=float(fin.max()) if len(fin) else None)
            row["changed"] = row["old"]["le"] != row["new"]["le"] or row["old"]["ge"] != row["new"]["ge"]
            row["frozen"] = []
            for (root, fn, sd, cc), v in fz.items():
                if sd == side and cc == c:
                    same_old = [[l, float(x)] for l, x in v["le"]] == row["h775_old"]["le"] and [[l, float(x)] for l, x in v["ge"]] == row["h775_old"]["ge"]
                    row["frozen"].append(dict(file="%s/res/%s" % (root, fn), le=v["le"], ge=v["ge"], n=v.get("n"), equals_h775_old_recompute=same_old))
            info[c] = row
            print("%s %-20s c539 n %d->%d  distinct %d->%d  le old %s | new %s  [h775 frozen: files %d, recompute equal %d, n %s]" % (
                side, c, row["old"]["n"], row["new"]["n"], row["old"]["distinct"], row["new"]["distinct"], [x for _, x in row["old"]["le"]], [x for _, x in row["new"]["le"]],
                len(row["frozen"]), sum(1 for f in row["frozen"] if f["equals_h775_old_recompute"]), [f["n"] for f in row["frozen"]]), flush=True)
        res["sides"][side] = info
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
