#!/usr/bin/env python3
"""TK-698, пункт 5б: по каждому столбцу таблицы TK-671 (306 признаков, `/data/p42-tk671/data/{long,short}.parquet`) — корзины q20 (границы по всем строкам обеих сторон, без P&L, записаны до чтения меток),
$ на строку и Σ $ по корзине на каждой стороне; расхождение сторон = размах разности сторон «шорт − лонг» ($ на строку) по корзинам; линия шума — то же расхождение при перемешанных метках стороны (целые сутки меняют сторону, 100 перестановок, seed 698).
Метка строки — `y_net` TK-671 (сделка на сигнале строки, каждая строка независимо, базовые выходы стороны, 500 $, net = gross − fees + funding; строки без сделки — без метки и не считаются) — это эталонная конструкция TK-671, не клетка владельца.
Корзины: у столбца ≤ 6 различных значений — по значению; иначе квантили q20/q40/q60/q80 (одинаковые границы для обеих сторон); корзина читается, если на каждой стороне ≥ MINN размеченных строк. Описательно, найдено на этих данных.
Запуск на srv: python3 tk698_item5b.py <run_key>"""
import json
import sys
import time

import numpy as np
import pyarrow.parquet as pq

import tk698_db as D
import tk698_lib as L

PQ = {"long": "/data/p42-tk671/data/long.parquet", "short": "/data/p42-tk671/data/short.parquet"}
NPERM = 100
SEED = 698
MINN = 500
META = {"win", "row_id", "sym"}


def feature_names():
    names = [pq.ParquetFile(PQ["short"]).schema_arrow.names, pq.ParquetFile(PQ["long"]).schema_arrow.names]
    both = [c for c in names[0] if c in names[1]]
    return [c for c in both if not (c in META or c.startswith(("meta_", "y_")))]


def fam_map(db_path="/data/chron/chron.sqlite"):
    import sqlite3
    c = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    ver = c.execute("select ver from infl_feat order by ver desc limit 1").fetchone()
    if not ver:
        return {}
    return {n: (cs or cat) for n, cat, cs in c.execute("select name, cat, cat_short from infl_feat where ver = ?", (ver[0],))}


def stats(bins, y, side, nb):
    """n и Σy по (сторона × корзина), массивы (2, nb)."""
    idx = side * nb + bins
    n = np.bincount(idx, minlength=2 * nb).reshape(2, nb)
    s = np.bincount(idx, weights=y, minlength=2 * nb).reshape(2, nb)
    return n, s


def gap_range(n, s):
    ok = (n[0] >= MINN) & (n[1] >= MINN)
    if ok.sum() < 2:
        return np.nan, ok
    m = s / np.maximum(n, 1)
    g = m[1] - m[0]                      # шорт − лонг (side 0 = long, 1 = short)
    return float(g[ok].max() - g[ok].min()), ok


def main(run_key):
    t0 = time.time()
    db = D.connect()
    D.new_run(db, run_key, "item5b", "tools/p42/tk698_item5b.py", "признаки TK-671 × стороны: корзины q20, разность сторон, шум по перестановкам суток; описательно, найдено на этих данных")
    feats = feature_names()
    fam = fam_map()
    meta = {}
    for sd in ("long", "short"):
        meta[sd] = pq.read_table(PQ[sd], columns=["meta_abs_ms", "y_has", "y_net", "y_stop"]).to_pandas()
    day = {sd: ((meta[sd]["meta_abs_ms"].to_numpy(np.int64) - L.FIRST_MS) // L.DAY_MS).astype(np.int16) for sd in meta}
    assert all(0 <= d.min() and d.max() < L.NDAYS for d in day.values()), "изоляция: сутки вне 01.07–13.11"
    has = {sd: meta[sd]["y_has"].to_numpy(bool) & np.isfinite(meta[sd]["y_net"].to_numpy()) for sd in meta}
    y = np.concatenate([meta["long"]["y_net"].to_numpy(np.float64)[has["long"]], meta["short"]["y_net"].to_numpy(np.float64)[has["short"]]])
    stp = np.concatenate([meta["long"]["y_stop"].to_numpy(np.float64)[has["long"]], meta["short"]["y_stop"].to_numpy(np.float64)[has["short"]]])
    side = np.concatenate([np.zeros(int(has["long"].sum()), np.int64), np.ones(int(has["short"].sum()), np.int64)])
    dd = np.concatenate([day["long"][has["long"]], day["short"][has["short"]]]).astype(np.int64)
    rng = np.random.default_rng(SEED)
    swaps = rng.integers(0, 2, size=(NPERM, L.NDAYS)).astype(np.int64)
    perm_side = [side ^ sw[dd] for sw in swaps]
    notes = {"rows_labeled": {"long": int(has["long"].sum()), "short": int(has["short"].sum())}, "rows_all": {sd: int(len(meta[sd])) for sd in meta},
             "nperm": NPERM, "minn": MINN, "sum_y": {"long": float(y[side == 0].sum()), "short": float(y[side == 1].sum())}}
    feat_rows, div_rows = [], []
    for k, c in enumerate(feats):
        a = {sd: pq.read_table(PQ[sd], columns=[c]).column(0).to_numpy(zero_copy_only=False).astype(np.float64) for sd in ("long", "short")}
        pooled = np.concatenate([a["long"], a["short"]])
        pf = pooled[np.isfinite(pooled)]
        if len(pf) == 0:
            continue
        u = np.unique(pf[: min(len(pf), 200000)])
        if len(u) <= 6:
            vals = np.unique(pf)
            edges = vals[1:] - 1e-12 if len(vals) > 1 else np.array([])
            cat = True
            lo_hi = [(float(v), float(v)) for v in vals]
        else:
            edges = np.unique(np.quantile(pf, [0.2, 0.4, 0.6, 0.8]))
            cat = False
            lo_hi = [(None if i == 0 else float(edges[i - 1]), None if i == len(edges) else float(edges[i])) for i in range(len(edges) + 1)]
        nb = len(edges) + 1
        x = np.concatenate([a["long"][has["long"]], a["short"][has["short"]]])
        fin = np.isfinite(x)
        bins = np.where(fin, np.digitize(x, edges), nb)             # NaN — своя корзина nb (читается отдельно)
        nbt = nb + 1
        n0, s0 = stats(bins, y, side, nbt)
        _, st0 = stats(bins, stp, side, nbt)
        rng_real, ok = gap_range(n0[:, :nb], s0[:, :nb])
        pr = []
        for ps in perm_side:
            n1, s1 = stats(bins, y, ps, nbt)
            r, _ = gap_range(n1[:, :nb], s1[:, :nb])
            if not np.isnan(r):
                pr.append(r)
        noise_hi = float(np.quantile(pr, 0.95)) if pr else None
        noise_max = float(max(pr)) if pr else None
        for sd, si in (("long", 0), ("short", 1)):
            for b in range(nbt):
                lo, hi = (lo_hi[b] if b < nb else (None, None))
                feat_rows.append((c, sd, b if b < nb else -1, lo, hi, int(n0[si, b]), float(s0[si, b]), float(s0[si, b] / n0[si, b]) if n0[si, b] else None, float(st0[si, b] / n0[si, b]) if n0[si, b] else None, {"cat": cat}))
        m = s0[:, :nb] / np.maximum(n0[:, :nb], 1)
        g = m[1] - m[0]
        okb = np.nonzero(ok)[0]
        ex = {}
        if len(okb) >= 2:
            ib, iw = int(okb[np.argmax(g[okb])]), int(okb[np.argmin(g[okb])])
            ex = {"bin_max": ib, "gap_max": float(g[ib]), "short_at_max": float(m[1][ib]), "long_at_max": float(m[0][ib]), "n_short_max": int(n0[1][ib]), "n_long_max": int(n0[0][ib]),
                  "bin_min": iw, "gap_min": float(g[iw]), "short_at_min": float(m[1][iw]), "long_at_min": float(m[0][iw]),
                  "split_bins": int(((m[1] > 0) & (m[0] < 0) & ok).sum()), "bins_read": int(ok.sum()), "edges": [float(e) for e in edges], "cat": cat,
                  "nan_rows": {"long": int(n0[0][nb]), "short": int(n0[1][nb])}, "noise_max": noise_max}
        div_rows.append((c, fam.get(c, "—"), None, None if np.isnan(rng_real) else float(rng_real), noise_hi, int((side == 1).sum()), int((side == 0).sum()), ex))
        if (k + 1) % 25 == 0:
            print("%d / %d столбцов, %.0f с" % (k + 1, len(feats), time.time() - t0), file=sys.stderr, flush=True)
    order = sorted(range(len(div_rows)), key=lambda i: -(div_rows[i][3] if div_rows[i][3] is not None else -1))
    rank = {i: r + 1 for r, i in enumerate(order)}
    div_rows = [(r[0], r[1], rank[i], r[3], r[4], r[5], r[6], r[7]) for i, r in enumerate(div_rows)]
    D.put(db, "res_feat", run_key, feat_rows, ["col", "side", "bin", "lo", "hi", "n", "usd", "per_trade", "stop_share", "extra"])
    D.put(db, "res_div", run_key, div_rows, ["col", "family", "rank", "div", "noise_hi", "n_short", "n_long", "extra"])
    D.put(db, "res_text", run_key, [("notes", json.dumps(notes, ensure_ascii=False))], ["key", "value"])
    print("столбцов %d, строк res_feat %d, %.0f с" % (len(div_rows), len(feat_rows), time.time() - t0))


if __name__ == "__main__":
    main(sys.argv[1])
