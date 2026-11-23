#!/usr/bin/env python3
"""TK-698, пункт 1: зависимость линий от рынка (BTC, ETH, равновзвешенный индекс c539) — суточный P&L по направлению дня, месяцам, дням ±2 %, 7-суточным блокам; корреляции; сделки по ходу рынка за 60 мин до входа.
Описательно, найдено на этих данных; движок не запускается. Ряды рынка — `tk698_mkt.py` (mkt.npz), сделки — `tk698_build.py`. Корзины хода за 60 мин — квантили ряда рынка по всем минутам окна (q20…q80), записаны до чтения P&L.
Запуск на srv: python3 tk698_item1.py <корень /data/p42-tk698> <run_key>"""
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

import tk698_db as D
import tk698_lib as L

MARKETS = ("btc", "eth", "idx")
BLOCK = 7
NPERM = 5000
SEED = 698


def block_ret(r):
    nb = len(r) // BLOCK
    out = np.full(len(r), np.nan)
    for k in range(nb):
        out[k * BLOCK:(k + 1) * BLOCK] = np.prod(1.0 + r[k * BLOCK:(k + 1) * BLOCK]) - 1.0
    return out


def row(item, line, dim, market, bin_, nd, n, usd, extra=None):
    return (item, line, dim, market, bin_, int(nd), int(n), float(usd), (float(usd) / n) if n else None, (float(usd) / nd) if nd else None, None, extra)


def day_split(item, line, dim, market, labels, daily, ntr, order):
    out = []
    for b in order:
        m = labels == b
        out.append(row(item, line, dim, market, b, m.sum(), ntr[m].sum(), daily[m].sum()))
    return out


def main(root, run_key):
    z = np.load(os.path.join(root, "mkt.npz")) if os.path.exists(os.path.join(root, "mkt.npz")) else np.load(os.path.join(root, "mkt", "mkt.npz"))
    ret = {"btc": z["d_btc"], "eth": z["d_eth"], "idx": z["d_idx"]}
    mv60 = {"btc": z["btc60"], "eth": z["eth60"], "idx": z["idx60"]}
    edges = {k: np.nanquantile(v, [0.2, 0.4, 0.6, 0.8]) for k, v in mv60.items()}   # до чтения P&L: ряд рынка по всем минутам окна
    db = D.connect()
    D.new_run(db, run_key, "item1", "tools/p42/tk698_item1.py", "зависимость от рынка; описательно, найдено на этих данных; окно 01.07–13.11, ставка 500 $")
    lines = json.load(open(os.path.join(root, "lines.json"), encoding="utf-8"))
    rng = np.random.default_rng(SEED)
    split, corr, linerows = [], [], []
    moon = L.MONTH_OF_DAY
    texts = {"edges_60m_q20_80": {k: [float(x) for x in v] for k, v in edges.items()}, "block_days": BLOCK, "days_in_blocks": (L.NDAYS // BLOCK) * BLOCK, "n_perm": NPERM,
             "market_days": {k: dict(up=int((v > 0).sum()), down=int((v < 0).sum()), ge2=int((v >= 0.02).sum()), le_m2=int((v <= -0.02).sum()), total=float(np.prod(1 + v) - 1)) for k, v in ret.items()}}
    for ln in lines:
        lid = ln["line"]
        tr = pd.read_parquet(os.path.join(root, "trades", lid + ".parquet"))
        daily = L.daily_pnl(tr)
        ntr = np.bincount(tr["day"].to_numpy(), minlength=L.NDAYS)
        s = 1.0 if ln["side"] == "long" else -1.0
        linerows.append((lid, ln["cell"], ln["side"], ln["n"], ln["usd"], ln["usd"] / ln["n"], ln["sharpe"], ln["dd"], ln["dd_pct"], ln["gap_longest"], ln["gap_mean"], ln["gap_tail"], ln["rr"], dict(db_row=ln["db_row"], db_entry=ln["db_entry"])))
        # месяцы
        for mi, mn in enumerate(L.MONTHS):
            m = moon == mi
            ex = {k: float(np.prod(1 + ret[k][m]) - 1) for k in MARKETS}
            ex.update({"up_days_" + k: int((ret[k][m] > 0).sum()) for k in MARKETS})
            split.append(row("item1", lid, "month", "all", mn, m.sum(), ntr[m].sum(), daily[m].sum(), ex))
        for mk_ in MARKETS:
            r = ret[mk_]
            lab = np.where(r > 0, "up", "down")
            split += day_split("item1", lid, "day_dir", mk_, lab, daily, ntr, ("up", "down"))
            lab2 = np.where(r >= 0.02, "ge+2%", np.where(r <= -0.02, "le-2%", "mid"))
            split += day_split("item1", lid, "band2", mk_, lab2, daily, ntr, ("ge+2%", "mid", "le-2%"))
            br = block_ret(r)
            lab3 = np.where(np.isnan(br), "excl", np.where(br > 0, "up", "down"))
            split += day_split("item1", lid, "trend7", mk_, lab3, daily, ntr, ("up", "down", "excl"))
            ok = np.isfinite(r)
            pe = float(np.corrcoef(daily[ok], r[ok])[0, 1])
            sp = float(stats.spearmanr(daily[ok], r[ok])[0])
            perm = np.array([np.corrcoef(daily[ok], rng.permutation(r[ok]))[0, 1] for _ in range(NPERM)])
            corr.append((lid, mk_, "pearson", pe, float(np.quantile(perm, 0.025)), float(np.quantile(perm, 0.975)), int(ok.sum())))
            corr.append((lid, mk_, "spearman", sp, None, None, int(ok.sum())))
            # ход рынка за 60 мин до сигнала (закрытая минута b = t − 1), корзины q20
            b = tr["sig_ms"].to_numpy(np.int64) // 60000 - 1 - L.FIRST_MS // 60000
            v = mv60[mk_][b]
            fin = np.isfinite(v)
            if mk_ == "btc":
                chk = tr["mk_btc_s60"].to_numpy() * s     # ворота: мой ряд = столбец TK-671 (mk_btc_s60 = s·r)
                both = fin & np.isfinite(chk)
                texts.setdefault("gate_btc60_maxabs", {})[lid] = float(np.abs(chk[both] - v[both]).max()) if both.any() else None
            binid = np.digitize(v, edges[mk_])
            val, kind = tr["val"].to_numpy(), tr["kind"].to_numpy()
            for bi in range(5):
                m = fin & (binid == bi)
                lo = float(edges[mk_][bi - 1]) if bi > 0 else None
                hi = float(edges[mk_][bi]) if bi < 4 else None
                split.append(row("item1", lid, "entry60_q", mk_, "q%d" % (bi + 1), 0, m.sum(), val[m].sum(), dict(lo=lo, hi=hi, stop_share=float((kind[m] == "stop").mean()) if m.any() else None)))
            for nm, m in (("up", fin & (v > 0)), ("down", fin & (v < 0))):
                split.append(row("item1", lid, "entry60_dir", mk_, nm, 0, m.sum(), val[m].sum(), dict(stop_share=float((kind[m] == "stop").mean()) if m.any() else None)))
            split.append(row("item1", lid, "entry60_dir", mk_, "no_value", 0, (~fin).sum(), val[~fin].sum()))
    D.put(db, "res_line", run_key, linerows, ["line", "cell", "side", "n", "usd", "per_trade", "sharpe", "dd", "dd_pct", "gap_longest", "gap_mean", "gap_tail", "rr", "extra"])
    D.put(db, "res_split", run_key, split, ["item", "line", "dim", "market", "bin", "n_days", "n", "usd", "per_trade", "per_day", "share", "extra"])
    D.put(db, "res_corr", run_key, corr, ["line", "market", "kind", "value", "lo", "hi", "n"])
    D.put(db, "res_text", run_key, [(k, json.dumps(v, ensure_ascii=False)) for k, v in texts.items()], ["key", "value"])
    print("записано: линий %d, срезов %d, корреляций %d" % (len(linerows), len(split), len(corr)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
