#!/usr/bin/env python3
"""TK-698: сделки линий (клетки c539 из БД летописи, архивы `cells-arch`) → `<out>/trades/<id>.parquet` с рыночными столбцами `mk_*` на минуте сигнала сделки (`tk671_mkt.compute` — готовая функция TK-671, ворота G-brute / G-causal).
Таблица TK-671 привязана к началу эпизода подхода (сделка в среднем на 592 мин позже строки), поэтому для столбцов на сделке функция вызывается по (sym, sig_ms) самой сделки; охраны — Σ, n и суточный ряд = строке БД.
Запуск на srv: python3 tk698_build.py <out> [main|mirror]"""
import json
import os
import sys

import numpy as np
import pandas as pd

import tk698_lib as L
import tk671_mkt as mk

# id линии: (клетка, сторона)
LINES = {
    "S_owner": ("e20+ea0.14+ev8+n_bounce1+s4", "short"),
    "S_384a": ("e20+e2t2+n_bounce1+natr_min0.6+nf1+s4", "short"),
    "S_384b": ("e20+e2t2+n_bounce1+natr_p60+s4", "short"),
    "L_anchor": ("s3", "long"),
    "S_anchor": ("s4", "short"),
    "L_best1": ("s3+big1800.le_q80+ok10_d5.le@h_le0+vpin1h.ge_q40", "long"),
    "L_best2": ("s3+big1800.le_q80+ok10_d7.le@h_le1+vpin1h.ge_q40", "long"),
    "L_best3": ("s3+CN60_q50+big1800.le_q80+vpin1h.ge_q20", "long"),
}


# зеркальные конструкции пункта 5: одни и те же ключи на обеих сторонах (имена клеток БД) и якоря наоборот
MIRROR = {
    "L_nb1": ("n_bounce1", "long"), "S_nb1": ("n_bounce1", "short"),
    "L_nb1s3": ("n_bounce1+s3", "long"), "S_nb1s3": ("n_bounce1+s3", "short"),
    "L_nb1s4": ("n_bounce1+s4", "long"), "S_nb1s4": ("n_bounce1+s4", "short"),
    "L_nb1nm": ("n_bounce1+natr_min0.2+s3", "long"), "S_nb1nm": ("n_bounce1+natr_min0.2+s4", "short"),
    "S_e20nb1": ("e20+n_bounce1+s4", "short"),
    "L_anchor_s4": ("s4", "long"), "S_anchor_s3": ("s3", "short"),
    "L_lx1": ("lx1", "long"), "S_lx1": ("lx1", "short"), "L_lx3": ("lx3", "long"), "S_lx3": ("lx3", "short"),
}
SETS = {"main": LINES, "mirror": MIRROR}


def add_mk(tr, side):
    parts = []
    for w in L.WINDOWS:
        a = tr[tr["win"] == w].copy()
        a["row_id"] = np.arange(len(a), dtype=np.int64)
        rows = pd.DataFrame(dict(row_id=a["row_id"].to_numpy(), sym=a["sym"].astype(str).to_numpy(), sig_ms=(a["sig_ms"].to_numpy(np.int64) - L.t0_ms(w))))
        df = mk.compute(rows, w, side, procs=1)
        a = a.reset_index(drop=True)
        for c in df.columns:
            a[c] = df[c].to_numpy()
        parts.append(a)
    return pd.concat(parts, ignore_index=True)


def build(line_id, cell, side, out, db):
    r = L.metric_row(db, cell, side)
    assert r is not None, (cell, side)
    tr = L.load_cell(r, lots=True)
    d = L.daily_pnl(tr)
    assert len(tr) == r["n"], (cell, len(tr), r["n"])
    assert abs(tr["val"].sum() - r["usd"]) < 1e-5 and np.abs(d - np.array(r["ex"]["daily"])).max() < 1e-6, ("охрана Σ / суточный ряд", cell)
    tr = add_mk(tr, side)
    tr.insert(0, "line", line_id)
    tr.to_parquet(os.path.join(out, "trades", line_id + ".parquet"), index=False)
    m = L.metrics(d)
    return dict(line=line_id, cell=cell, side=side, n=len(tr), usd=m["usd"], sharpe=m["sharpe"], dd=m["dd"], dd_pct=m["dd_pct"], gap_longest=m["gap_longest"], gap_mean=m["gap_mean"], gap_tail=m["gap_tail"], rr=L.rr(tr["val"]), db_row=r["id"], db_entry=r["entry"])


def main(out, which="main"):
    os.makedirs(os.path.join(out, "trades"), exist_ok=True)
    db = L.chron_ro()
    res = []
    for k, (cell, side) in SETS[which].items():
        res.append(build(k, cell, side, out, db))
        print(json.dumps(res[-1], ensure_ascii=False), flush=True)
    json.dump(res, open(os.path.join(out, "lines.json" if which == "main" else "lines-%s.json" % which), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], *(sys.argv[2:3]))
