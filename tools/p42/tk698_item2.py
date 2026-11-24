#!/usr/bin/env python3
"""TK-698, пункт 2: «мимика» — сделки по ходу лидера (BTC / ETH) за 5 / 15 / 60 мин до сигнала × силе монеты против BTC (`mk_rel_s60`); столбцы `mk_*` — функция TK-671 на минуте сигнала сделки (`tk698_build.py`), знак стороны внутри (положительно = ход в сторону сделки).
Корзины — терцили (q33 / q67) того же столбца по всем строкам таблицы TK-671 той же стороны, границы записаны в `res_text` до чтения P&L. Таблицы: по одной оси (ход лидера) и 3 × 3 (ход лидера × сила монеты): сделок, Σ $, $ на сделку, доля стопов.
Описательно, найдено на этих данных; окно 01.07–13.11, ставка 500 $. Запуск на srv: python3 tk698_item2.py <корень /data/p42-tk698> <run_key>"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import tk698_db as D

PQ = {"long": "/data/p42-tk671/data/long.parquet", "short": "/data/p42-tk671/data/short.parquet"}
LINES = ["L_anchor", "L_best1", "L_best2", "L_best3", "L_nb1s3", "L_nb1s4", "S_anchor", "S_owner", "S_384a", "S_384b", "S_nb1s3", "S_nb1s4"]
LEAD = {"btc": ("mk_btc_s5", "mk_btc_s15", "mk_btc_s60"), "eth": ("mk_eth_s5", "mk_eth_s15", "mk_eth_s60")}
REL = "mk_rel_s60"
OWN = "mk_own_s60"
COLS = [c for v in LEAD.values() for c in v] + [REL, OWN]


def tercile(x, e):
    return np.where(np.isfinite(x), np.digitize(x, e), -1)


def main(root, run_key):
    db = D.connect()
    D.new_run(db, run_key, "item2", "tools/p42/tk698_item2.py", "мимика: ход лидера × сила монеты; терцили по строкам TK-671 стороны, до чтения P&L; описательно, найдено на этих данных")
    edges = {}
    for sd in ("long", "short"):
        t = pq.read_table(PQ[sd], columns=COLS).to_pandas()
        edges[sd] = {c: [float(q) for q in np.nanquantile(t[c].to_numpy(np.float64), [1 / 3, 2 / 3])] for c in COLS}
    D.put(db, "res_text", run_key, [("edges_terciles", json.dumps(edges, ensure_ascii=False))], ["key", "value"])
    split = []
    for lid in LINES:
        p = os.path.join(root, "trades", lid + ".parquet")
        if not os.path.exists(p):
            continue
        tr = pd.read_parquet(p)
        side = "long" if lid.startswith("L_") else "short"
        v, kind = tr["val"].to_numpy(), tr["kind"].to_numpy()
        e = edges[side]
        rel_b = tercile(tr[REL].to_numpy(), e[REL])
        for mk, cols in LEAD.items():
            for c in cols:
                b1 = tercile(tr[c].to_numpy(), e[c])
                for i in range(3):
                    m = b1 == i
                    split.append(("item2", lid, "lead_" + c, mk, "t%d" % (i + 1), 0, int(m.sum()), float(v[m].sum()), float(v[m].mean()) if m.any() else None, None, float(m.mean()), dict(stop_share=float((kind[m] == "stop").mean()) if m.any() else None, lo=e[c][i - 1] if i > 0 else None, hi=e[c][i] if i < 2 else None)))
                m = b1 < 0
                split.append(("item2", lid, "lead_" + c, mk, "nan", 0, int(m.sum()), float(v[m].sum()), float(v[m].mean()) if m.any() else None, None, float(m.mean()), None))
                if c.endswith(("s15", "s60")):
                    for i in range(3):
                        for j in range(3):
                            m = (b1 == i) & (rel_b == j)
                            split.append(("item2", lid, "grid_" + c, mk, "l%d_r%d" % (i + 1, j + 1), 0, int(m.sum()), float(v[m].sum()), float(v[m].mean()) if m.any() else None, None, float(m.mean()),
                                          dict(stop_share=float((kind[m] == "stop").mean()) if m.any() else None)))
        for j in range(3):
            m = rel_b == j
            split.append(("item2", lid, "rel_s60", "own-btc", "t%d" % (j + 1), 0, int(m.sum()), float(v[m].sum()), float(v[m].mean()) if m.any() else None, None, float(m.mean()), dict(stop_share=float((kind[m] == "stop").mean()) if m.any() else None)))
        own_b = tercile(tr[OWN].to_numpy(), e[OWN])
        for j in range(3):
            m = own_b == j
            split.append(("item2", lid, "own_s60", "own", "t%d" % (j + 1), 0, int(m.sum()), float(v[m].sum()), float(v[m].mean()) if m.any() else None, None, float(m.mean()), dict(stop_share=float((kind[m] == "stop").mean()) if m.any() else None)))
        split.append(("item2", lid, "n", "all", "all", 0, len(tr), float(v.sum()), float(v.mean()), None, 1.0, dict(stop_share=float((kind == "stop").mean()))))
    D.put(db, "res_split", run_key, split, ["item", "line", "dim", "market", "bin", "n_days", "n", "usd", "per_trade", "per_day", "share", "extra"])
    print("срезов", len(split))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
