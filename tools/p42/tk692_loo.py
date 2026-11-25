#!/usr/bin/env python3
"""TK-692: описательная проверка вердикта PS-1007 §8 (критик thr_m27kv64m9h, L-436) — без новых выборов и без смены правила.

  (a) ряд вне выборки со стороны без клетки `--drop-cid` в наборе (следующий по правилу выбор встаёт на её место на каждой границе);
  (b) ряды (основной и без клетки) отдельно на сутках `--split` (по умолчанию 120 = начало мая): до и после;
  (c) доля главной клетки и главного месяца в Σ основного ряда.

  python3 tk692_loo.py --db /data/p42-tk700/mass/mass.sqlite --out /data/p42-tk692/tk692.sqlite --rp /data/p42-tk692/replay.sqlite \\
      --rid A-m06 --side short --cellset all1 --runs <run-id месяцев A шорт, через запятую> --drop-cid 788465

Результат — печать и таблица `tk692_loo` в --out (rid, side, variant, step, period, …). Основной ряд сверяется с `tk692_sum` (E = 1) до 1e-6 $."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import os

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk692_walk as W  # noqa: E402

SCHEMA = """CREATE TABLE IF NOT EXISTS tk692_loo(rid TEXT, side TEXT, variant TEXT, step TEXT, period TEXT, days INT, n INT, sum REAL, sharpe REAL, dd REAL, extra TEXT,
  PRIMARY KEY(rid, side, variant, step, period))"""
MON_RU = ("янв", "фев", "мар", "апр", "май", "июн")


def drop_cid(pool, cid):
    keep = pool.cids != cid
    return W.Pool(pool.cids[keep], pool.X[keep], pool.N[keep], pool.P[keep], [k for k, f in zip(pool.ckeys, keep) if f], pool.meta)


def run_walk(pool, side, step):
    picks, days, pnl, nn = W.walk(pool, side, step, 1)
    cid_of = {}
    for r in picks:
        if r["pick"] is not None and r["e"] > r["b"]:
            for d in range(r["b"], r["e"]):
                cid_of[d] = r["pick"]["cid"]
    return picks, days, pnl, nn, np.array([cid_of[int(d)] for d in days], np.int64)


def stat(pnl, nn):
    st = W.series_stats(pnl, nn)
    return dict(days=st["days"], n=st["n"], sum=st["sum"], sharpe=st["sharpe"], dd=st["dd"])


def fmt(st):
    return "%+.2f | %.2f | %.2f (%.1f %%) | %d" % (st["sum"], st["sharpe"], st["dd"], st["dd"] / W.MARGIN * 100, st["n"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rp", required=True)
    ap.add_argument("--rid", required=True)
    ap.add_argument("--side", required=True)
    ap.add_argument("--cellset", default="all1")
    ap.add_argument("--runs", required=True)
    ap.add_argument("--drop-cid", type=int, required=True)
    ap.add_argument("--split", type=int, default=120)
    a = ap.parse_args()
    con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    out = sqlite3.connect(a.out)
    out.execute(SCHEMA)
    pool = W.load_pool(con, a.runs.split(","), a.cellset, a.side)
    con.close()
    assert pool.nd == W.TOTAL, pool.nd
    loo = drop_cid(pool, a.drop_cid)
    ref = {r[0]: r[1] for r in out.execute("SELECT step, sum FROM tk692_sum WHERE rid=? AND side=? AND emb=1 AND series='select'", (a.rid, a.side))}
    ckeys = {label: (ck, n, usd) for label, ck, n, usd in W.BASES[a.side]}
    refs = {}
    res = {}
    for step in W.STEPS:
        res[("main", step)] = run_walk(pool, a.side, step)
        res[("loo", step)] = run_walk(loo, a.side, step)
        got = float(res[("main", step)][2].sum())
        if abs(got - ref[step]) > 1e-6:
            raise SystemExit("основной ряд %s не сходится с tk692_sum: %.6f против %.6f" % (step, got, ref[step]))
    days_all = res[("main", "month")][1]
    for label, (ck, n0, usd0) in ckeys.items():
        idx, how, fam = pool.resolve_base(ck, n0, usd0)
        xb = W.extra_base(a.rp, a.side, label, pool.nd) if idx is None else None
        refs[label] = (xb[0], xb[1]) if xb is not None else ((pool.X[idx], pool.N[idx]) if idx is not None else None)

    def put(variant, step, period, st, extra=None):
        out.execute("INSERT OR REPLACE INTO tk692_loo VALUES(?,?,?,?,?,?,?,?,?,?,?)", (a.rid, a.side, variant, step, period, st["days"], st["n"], st["sum"], st["sharpe"], st["dd"], json.dumps(extra) if extra else None))

    print("### (a) ряд вне выборки целиком, без клетки %d в наборе (E = 1; ставка 500 $; сторона %s)\n" % (a.drop_cid, a.side))
    print("| шаг | ряд | Σ $ | Шарп | просадка $ (%% маржи) | сд. | выборов клетки %d | разных клеток |\n|---|---|---|---|---|---|---|---|" % a.drop_cid)
    for step in W.STEPS:
        for variant in ("main", "loo"):
            picks, days, pnl, nn, cids = res[(variant, step)]
            st = stat(pnl, nn)
            tr = [r["pick"]["cid"] for r in picks if r["pick"] is not None and r["e"] > r["b"]]
            put(variant, step, "oos", st, dict(picks_of_cell=sum(1 for c in tr if c == a.drop_cid), steps=len(tr), distinct=len(set(tr))))
            print("| %s | %s | %+.2f | %.2f | %.2f (%.1f %%) | %d | %d из %d | %d |" % (step, "основной" if variant == "main" else "без %d" % a.drop_cid, st["sum"], st["sharpe"], st["dd"], st["dd"] / W.MARGIN * 100,
                                                                                    st["n"], sum(1 for c in tr if c == a.drop_cid), len(tr), len(set(tr))))
        for label, rr in refs.items():
            if rr is None:
                continue
            pnl, nn = rr[0][days_all], rr[1][days_all]
            st = stat(pnl, nn)
            put(label, step, "oos", st)
        # справка: базы за те же сутки (одни и те же на всех шагах только при совпадении суток)
    print("\nБазы за те же сутки (по шагу): ")
    print("| шаг | база | Σ $ | Шарп | просадка $ (% маржи) |\n|---|---|---|---|---|")
    for step in W.STEPS:
        days = res[("main", step)][1]
        for label, rr in refs.items():
            if rr is None:
                continue
            st = stat(rr[0][days], rr[1][days])
            print("| %s | %s | %+.2f | %.2f | %.2f (%.1f %%) |" % (step, label, st["sum"], st["sharpe"], st["dd"], st["dd"] / W.MARGIN * 100))

    print("\n### (b) до / после суток %d (до — 01.01+%d сут.; Σ $ | Шарп | просадка $ (%% маржи) | сд.)\n" % (a.split, a.split))
    print("| шаг | ряд | суток до | до | суток после | после |\n|---|---|---|---|---|---|")
    for step in W.STEPS:
        for variant in ("main", "loo"):
            picks, days, pnl, nn, cids = res[(variant, step)]
            m0, m1 = days < a.split, days >= a.split
            s0, s1 = stat(pnl[m0], nn[m0]), stat(pnl[m1], nn[m1])
            put(variant, step, "before", s0)
            put(variant, step, "after", s1)
            print("| %s | %s | %d | %s | %d | %s |" % (step, "основной" if variant == "main" else "без %d" % a.drop_cid, s0["days"], fmt(s0), s1["days"], fmt(s1)))
        days = res[("main", step)][1]
        for label, rr in refs.items():
            if rr is None:
                continue
            m0, m1 = days < a.split, days >= a.split
            s0, s1 = stat(rr[0][days][m0], rr[1][days][m0]), stat(rr[0][days][m1], rr[1][days][m1])
            put(label, step, "before", s0)
            put(label, step, "after", s1)
            print("| %s | %s | %d | %s | %d | %s |" % (step, label, s0["days"], fmt(s0), s1["days"], fmt(s1)))

    print("\n### (c) вклад клеток и месяцев в Σ основного ряда (E = 1)\n")
    print("| шаг | Σ $ | главная клетка | её Σ $ (доля) | вторая клетка | её Σ $ (доля) | Σ $ по месяцам (фев … июн) | главный месяц (доля) |\n|---|---|---|---|---|---|---|---|")
    for step in W.STEPS:
        for variant in ("main", "loo"):
            picks, days, pnl, nn, cids = res[(variant, step)]
            tot = float(pnl.sum())
            by = {}
            for c, p in zip(cids, pnl):
                by[int(c)] = by.get(int(c), 0.0) + float(p)
            top = sorted(by.items(), key=lambda kv: -kv[1])[:2]
            mon = [float(pnl[(days >= W.MONTH_START[m]) & (days < W.MONTH_START[m + 1])].sum()) for m in range(1, 6)]
            tm = int(np.argmax(mon))
            extra = dict(top_cells=top, months=mon, top_month=W.MONTHS[tm + 1], top_month_share=mon[tm] / tot if tot else None, top_cell_share=top[0][1] / tot if tot else None)
            put(variant, step, "share", dict(days=len(days), n=int(nn.sum()), sum=tot, sharpe=float("nan"), dd=0.0), extra)
            t1, t2 = top[0], top[1] if len(top) > 1 else (0, 0.0)
            print("| %s %s | %+.2f | %d | %+.2f (%.0f %%) | %d | %+.2f (%.0f %%) | %s | %s (%.0f %%) |" % (
                step, "" if variant == "main" else "(без %d)" % a.drop_cid, tot, t1[0], t1[1], 100 * t1[1] / tot if tot else 0, t2[0], t2[1], 100 * t2[1] / tot if tot else 0,
                " / ".join("%+.1f" % x for x in mon), MON_RU[tm + 1], 100 * mon[tm] / tot if tot else 0))
    out.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
