#!/usr/bin/env python3
"""П-42 TK-674: откуда берутся кандидаты без строки таблицы фильтров (G-key, no_key > 0). По клеткам из `res/gkey674<суф>-<сторона>.json` (`tk674_gkey_scan.py`)
берёт сделки клетки (`short-trades.csv` / `long-trades.csv`: (sym, sig_ms) кандидатов, прошедших мимо фильтра) и считает: сколько ключей нет в `ftab674<суф>/<окно>-<сторона>.csv`
и сколько из них есть в дампе базы h775 (до обрезки до c539, `tk654_run.FEAT34`) и в широкой таблице TK-671 (`/data/p42-tk671/data/<сторона>.parquet`, `meta_sig_ms`). Без движка и без P&L: проверка «строки нет в таблицах-источниках» против «потеряна нашей сборкой».
Запуск на srv в среде шага: `python3 tk674_gkey_cause.py [шаг выборки клеток, по умолчанию 20]` → `res/gkeycause674<суф>-<сторона>.json`. НЕ на упакованных партиях (`tk674_pack`: каталоги клеток убраны в `cells-arch/`) — сначала `tk674_pack.py extract` или читать архив; на упакованных молча пропустит."""
import csv
import glob
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_run as run  # noqa: E402


def main():
    step = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    st, _, _, _ = run.t._srv()
    for side in run.SIDES:
        f = run.t.q("res", "gkey674%s-%s.json" % (run.SUF, side))
        bad = json.load(open(f, encoding="utf-8"))["bad"]
        if not bad:
            print("== %s: клеток с no_key нет" % side)
            continue
        name = run.SIDENAME[side]
        pq = pd.read_parquet("/data/p42-tk671/data/%s.parquet" % name, columns=["win", "sym", "meta_sig_ms"])
        in_671 = {w: set(zip(g["sym"], g["meta_sig_ms"])) for w, g in pq.groupby(pq["win"].astype(str))}
        import tk654_run as r54
        base = {}
        for w in st.TRADE_WINDOWS:
            sym, ms, _ = r54.rfx.read_ftab(os.path.join(r54.FEAT34, "h775", "%s-%s.csv" % (w.name, side)))
            base[w.name] = set(zip(map(str, sym), map(int, ms)))
        tab = {w.name: set(map(tuple, pd.read_csv(run.t.q("ftab674" + run.FSUF, "%s-%s.csv" % (w.name, side)), usecols=["sym", "sig_ms"]).values.tolist())) for w in st.TRADE_WINDOWS}
        fams = {"ev/ea": lambda b: "ev" in b["patch"], "nf": lambda b: "nf" in b["patch"]}
        out = {}
        for fam, sel in fams.items():
            cs = [b for b in bad if sel(b)][::step]
            n_tr = n_miss = n_671 = n_base = 0
            for b in cs:
                for w in st.TRADE_WINDOWS:
                    d = glob.glob(os.path.join(run.ROOT, "cells", "%s-%s" % (w.name, side), "*", b["cid"]))[0]
                    for r in csv.DictReader(open(os.path.join(d, "%s-trades.csv" % name), encoding="utf-8")):
                        k = (r["sym"], int(r["sig_ms"]))
                        n_tr += 1
                        if k not in tab[w.name]:
                            n_miss += 1
                            n_671 += k in in_671[w.name]
                            n_base += k in base[w.name]
            out[fam] = dict(cells=len(cs), of_cells=sum(1 for b in bad if sel(b)), trades=n_tr, no_ftab_row=n_miss, of_them_in_tk671_table=n_671, of_them_in_base_dump_h775=n_base)
            print("== %s %s: клеток %d (из %d), сделок %d, без строки ftab %d, из них есть в таблице TK-671 %d, в дампе базы h775 %d" % (side, fam, len(cs), out[fam]["of_cells"], n_tr, n_miss, n_671, n_base))
        json.dump(out, open(run.t.q("res", "gkeycause674%s-%s.json" % (run.SUF, side)), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
