#!/usr/bin/env python3
"""TK-698, пункт 3: сколько разных сделок в семействе «похожих». Семейство — шорт-клетки c539 окна 01.07–13.11 с токенами `e20` и `n_bounce1` (охрана n ≥ 81, PS-956); наборы: топ 200 по Σ $, топ 200 по Шарпу, все (отбор по результату — «найдено на этих данных»).
Сделки клеток — из архивов `cells-arch` (`tk698_lib.load_cell`, окно 01.07–13.11, P&L сделки = gross − fees, ставка 500 $); пересечение — по (sym, entry_ms) и по (sym, sig_ms). Ядро — сделка есть в ≥ 90 % клеток набора и во всех (100 %); Σ ядра против остатка — по каждой клетке (её собственный P&L сделки).
Запуск на srv: python3 tk698_item3.py <корень /data/p42-tk698> <run_key>"""
import json
import os
import sys
import time

import numpy as np

import tk698_db as D
import tk698_lib as L

WIN = "01.07–13.11"
NMIN = 81
TOP = 200
BUCKETS = (("100%", 1.0, 1.01), ("[90,100)%", 0.9, 1.0), ("[50,90)%", 0.5, 0.9), ("[10,50)%", 0.1, 0.5), ("<10%", 0.0, 0.1))


def select(db):
    best = {}
    for cid, cell, n, usd, sh in db.execute("select id, cell, trades, usd, sharpe from metric where side = 'short' and uni = 'c539' and win = ? and trades >= ?", (WIN, NMIN)):
        t = cell.split("+")
        if "e20" in t and "n_bounce1" in t and (cell not in best or cid > best[cell][0]):
            best[cell] = (cid, n, usd, sh)
    cells = sorted(best, key=lambda c: -best[c][2])
    by_sum = cells[:TOP]
    by_sh = sorted(best, key=lambda c: -best[c][3])[:TOP]
    return best, {"top200_usd": by_sum, "top200_sharpe": by_sh, "all": cells}


def row_by_id(db, cid, cell):
    """Строка metric по первичному ключу (`L.metric_row` ищет по (cell, side, uni, win) полным просмотром — сотни клеток слишком долго)."""
    r = db.execute("select id, trades, usd, sharpe, extra, entry from metric where id = ?", (cid,)).fetchone()
    return dict(id=r[0], n=r[1], usd=r[2], sharpe=r[3], ex=json.loads(r[4] or "{}"), entry=r[5], cell=cell, side="short")


def prefetch(rows):
    g = {}
    for r in rows:
        for w in L.WINDOWS:
            p = r["ex"]["dirs"][w]
            if os.path.isdir(p) or os.path.isfile(os.path.join(L.STAGE, p.split("/cells/", 1)[1], "meta.json")):
                continue
            wside, batch, cell = p.split("/cells/", 1)[1].split("/", 2)
            g.setdefault((wside, batch), []).append(cell)
    for (ws, b), cs in g.items():
        L.extract_batch(ws, b, cs)


def key_ids(tr, kind):
    sym = tr["sym"].astype(str).to_numpy()
    return sym, tr[kind].to_numpy(np.int64)


OWNER = "e20+ea0.14+ev8+n_bounce1+s4"
NEAR_J = (0.5, 0.7, 0.9)


def main(root, run_key, which="all"):
    t0 = time.time()
    db = D.connect()
    D.new_run(db, run_key, "item3", "tools/p42/tk698_item3.py", "семейство e20 + n_bounce1 (шорт, c539): число разных сделок, ядро, Σ ядра против остатка; отбор по результату — найдено на этих данных")
    chron = L.chron_ro()
    best, sets = select(chron)
    D.put(db, "res_text", run_key, [("sets", json.dumps({k: v for k, v in sets.items()}, ensure_ascii=False)),
                                     ("guard", json.dumps({"n_min": NMIN, "cells_family": len(best), "top": TOP, "win": WIN}, ensure_ascii=False))], ["key", "value"])
    rows = {c: row_by_id(chron, best[c][0], c) for c in best}
    prefetch(list(rows.values()))
    print("клеток %d, распаковано, %.0f с" % (len(rows), time.time() - t0), file=sys.stderr, flush=True)
    data = {}
    for c, r in rows.items():
        tr = L.load_cell(r)
        assert len(tr) == r["n"] and abs(tr["val"].sum() - r["usd"]) < 1e-5, ("охрана n / Σ", c)
        data[c] = tr
    print("сделки клеток загружены, %.0f с" % (time.time() - t0), file=sys.stderr, flush=True)
    if which == "near":
        ok = {}
        for c, tr in data.items():
            ok[c] = set(zip(tr["sym"].astype(str), tr["entry_ms"].astype(np.int64)))
        own = ok[OWNER]
        jac_own = {c: len(own & k) / len(own | k) for c, k in ok.items()}
        sets = {"near_owner_j%d" % round(100 * t): [c for c in jac_own if jac_own[c] >= t] for t in NEAR_J}
        sets = {k: v for k, v in sets.items() if len(v) >= 5}
        D.put(db, "res_text", run_key, [("near_sets", json.dumps({k: len(v) for k, v in sets.items()})), ("near_cells", json.dumps(sets, ensure_ascii=False)),
                                         ("jaccard_owner", json.dumps(sorted(jac_own.values(), reverse=True)[:60]))], ["key", "value"])
    syms = sorted({s for tr in data.values() for s in tr["sym"].astype(str).unique()})
    sc = {s: i for i, s in enumerate(syms)}
    split, linerows, notes = [], [], {}
    for kname in ("entry_ms", "sig_ms"):
        keys = {}
        for c, tr in data.items():
            k = np.array([sc[s] for s in tr["sym"].astype(str)], np.int64) * (1 << 42) + tr[kname].to_numpy(np.int64)
            assert len(np.unique(k)) == len(k), ("ключ неуникален", c, kname)
            keys[c] = k
        for sname, cells in sets.items():
            allk = np.unique(np.concatenate([keys[c] for c in cells]))
            C, K = len(cells), len(allk)
            M = np.zeros((C, K), np.bool_)
            V = np.zeros((C, K), np.float32)
            day = np.zeros(K, np.int16)
            for i, c in enumerate(cells):
                j = np.searchsorted(allk, keys[c])
                M[i, j] = True
                V[i, j] = data[c]["val"].to_numpy(np.float32)
                day[j] = data[c]["day"].to_numpy(np.int16)
            cover = M.mean(0)
            nper = M.sum(1)
            usd_cell = V.sum(1)
            Mf = M.astype(np.float32)
            inter = Mf @ Mf.T
            union = nper[:, None] + nper[None, :] - inter
            iu = np.triu_indices(C, 1)
            jac = inter[iu] / union[iu]
            tag = "%s/%s" % (sname, kname)
            split.append(("item3", tag, "distinct", "all", "keys", 0, int(K), float(usd_cell.mean()), None, None, None,
                          dict(cells=C, n_per_cell_mean=float(nper.mean()), n_per_cell_min=int(nper.min()), n_per_cell_max=int(nper.max()), usd_cell_mean=float(usd_cell.mean()), usd_cell_min=float(usd_cell.min()), usd_cell_max=float(usd_cell.max()),
                               jaccard_mean=float(jac.mean()), jaccard_min=float(jac.min()), jaccard_max=float(jac.max()))))
            for bn, lo, hi in BUCKETS:
                m = (cover >= lo) & (cover < hi)
                if not m.any():
                    split.append(("item3", tag, "cover", "all", bn, 0, 0, 0.0, None, None, 0.0, None))
                    continue
                us = V[:, m].sum(1)
                nn = M[:, m].sum(1)
                split.append(("item3", tag, "cover", "all", bn, 0, int(m.sum()), float(us.mean()), float(us.sum() / max(nn.sum(), 1)), None, float(nn.mean() / nper.mean()),
                              dict(n_cell_mean=float(nn.mean()), usd_cell_min=float(us.min()), usd_cell_max=float(us.max()))))
            for thr, cn in ((1.0, "core100"), (0.9, "core90")):
                core = cover >= thr - 1e-9
                us_c, us_r = V[:, core].sum(1), V[:, ~core].sum(1)
                n_c = M[:, core].sum(1)
                dc, dr = np.zeros(L.NDAYS), np.zeros(L.NDAYS)
                np.add.at(dc, day[core], V[:, core].mean(0))
                np.add.at(dr, day[~core], V[:, ~core].mean(0))
                cov = np.cov(usd_cell, us_c)[0, 1] / max(np.var(usd_cell, ddof=1), 1e-12) if C > 2 else None
                split.append(("item3", tag, cn, "all", "core", 0, int(core.sum()), float(us_c.mean()), float(us_c.sum() / max(n_c.sum(), 1)), None, float(us_c.mean() / usd_cell.mean()) if usd_cell.mean() else None,
                              dict(n_core_cell_mean=float(n_c.mean()), n_rest_cell_mean=float((nper - n_c).mean()), usd_core_mean=float(us_c.mean()), usd_core_min=float(us_c.min()), usd_core_max=float(us_c.max()),
                                   usd_rest_mean=float(us_r.mean()), usd_rest_min=float(us_r.min()), usd_rest_max=float(us_r.max()), var_share_core=None if cov is None else float(cov),
                                   corr_cell_core=float(np.corrcoef(usd_cell, us_c)[0, 1]) if C > 2 and us_c.std() > 0 else None, corr_cell_rest=float(np.corrcoef(usd_cell, us_r)[0, 1]) if C > 2 and us_r.std() > 0 else None)))
                for nm, d_, nn_, uu in (("core", dc, float(n_c.mean()), us_c), ("rest", dr, float((nper - n_c).mean()), us_r)):
                    if kname != "entry_ms":
                        continue
                    m = L.metrics(d_)
                    linerows.append(("I3|%s|%s|%s" % (sname, cn, nm), "e20+n_bounce1 (%s)" % sname, "short", int(round(nn_)), m["usd"], m["usd"] / max(nn_, 1e-9), m["sharpe"], m["dd"], m["dd_pct"], m["gap_longest"], m["gap_mean"], m["gap_tail"], None,
                                     dict(kind="mean over cells of the set", cells=C, keys=int(core.sum() if nm == "core" else (~core).sum()))))
            if kname == "entry_ms":
                notes[sname] = dict(cells=C, keys=int(K))
    D.put(db, "res_split", run_key, split, ["item", "line", "dim", "market", "bin", "n_days", "n", "usd", "per_trade", "per_day", "share", "extra"])
    D.put(db, "res_line", run_key, linerows, ["line", "cell", "side", "n", "usd", "per_trade", "sharpe", "dd", "dd_pct", "gap_longest", "gap_mean", "gap_tail", "rr", "extra"])
    print("срезов %d, линий %d, %.0f с" % (len(split), len(linerows), time.time() - t0))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], *(sys.argv[3:4]))
