#!/usr/bin/env python3
"""TK-735, §13.79.1 P4 (поправка L-526): метки `y_n` подходов шорта c539 в единице RV60 (вместо NATR30) на окне формирования режима.

Окна: `w0` — SEL, подходы 17.06 00:00 … 30.06 23:00 (`sig_abs + 60 мин ≤ 01.07`), готовые дамп `b0p0` и события TK-625; `m01` — FJ, подходы 02.01 00:00 … 31.01 23:00 (`sig_abs + 60 мин ≤ 01.02`),
дамп `b0p0` (`/data/p42-tk735/eng-m01`) и события `tk625_events.py` (`/data/p42-tk735/res/ev-m01.csv`) строятся этой же задачей.
Код метки — `tk728_labels.label2` дословно (`sigfeat_lib.outcome`, склад тиков, только чтение); меняется только ширина пробоя: X = ⌈|R|·RV60 / SCALE⌉, RV60 — на закрытой минуте перед сигналом (доля цены ×1e8).
  SIGFEAT_BASE_DATE=<T0 окна> SIGFEAT_DAYS=80 PYTHONPATH=/data/p42-tk735/code python3 /data/p42-tk735/code/tk735_labels.py --win w0|m01 --out /data/p42-tk735/res/lab-rv60-<окно>.csv [--procs 2]
Выход — CSV: sym, level, sig_abs_ms, ok_t60, y_n_rv60, rv_e8, lp_e8.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, "/data/p42-tk728/code")
import sigfeat_lib as SL  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk671_bkep as B  # noqa: E402
import tk671_ticks as TT  # noqa: E402
import tk727_lib as L  # noqa: E402
import tk728_labels as K  # noqa: E402
import tk735_lib as T  # noqa: E402


def _ms(y, m, d):
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp() * 1000)


# окно -> (T0, начало подходов, момент формирования, события, дамп движка, каталог сделок, дата зачёта hi для G6)
WINDOWS = {
    "w0": dict(t0="2026-06-03", t0_ms=_ms(2026, 6, 3), lo=_ms(2026, 6, 17), form=_ms(2026, 7, 1), ev="/data/p42-tk625/ev-h775/w0.csv",
               sigsx="/data/p42-tk625/h775/eng/w0/b0p0/short-sigsx.csv", trades="/data/p42-tk625/h775/w0/aggtrades", hi="2026-06-30"),
    "m01": dict(t0="2025-12-12", t0_ms=L.G0_MS, lo=_ms(2026, 1, 2), form=_ms(2026, 2, 1), ev="/data/p42-tk735/res/ev-m01.csv",
                sigsx="/data/p42-tk735/eng-m01/b0p0/short-sigsx.csv", trades="/data/p42-tk621/m01/aggtrades", hi="2026-01-31"),
}

G = {}


def process(sym):
    out = []
    sub = G["ev"].get(sym)
    if sub is None:
        return out
    S = T.load_kl(sym)
    rv = L.rv_sqrt_sum(S, 60)
    tk = TT.StoreTicks(G["trades"], sym, keep=4)
    s = B.SIDES["short"]
    part = sub[sub.side == "short"].sort_values(["sig_ms", "level"])
    dist = G["dist"]
    for r in part.itertuples(index=False):
        abs_ms = G["t0_ms"] + int(r.sig_ms)
        if abs_ms < G["lo"] or abs_ms + 3_600_000 > G["form"]:
            continue
        m = (abs_ms - L.G0_MS) // L.MIN_MS
        v = rv[m - 1] if 1 <= m <= len(rv) else np.nan
        rv_e8 = int(v * 1e8) if np.isfinite(v) else 0
        d = dist.get((sym, int(r.level), int(r.sig_ms)))
        if d is None:
            raise RuntimeError("нет дампа b0p0: %s %d %d" % (sym, r.level, r.sig_ms))
        lf, ok, y, y_n = K.label2(tk, int(r.sig_ms), s, int(r.lp_e8), d, rv_e8)
        out.append(dict(sym=sym, level=int(r.level), sig_abs_ms=abs_ms, ok_t60=ok, y_n_rv60=y_n, rv_e8=rv_e8, lp_e8=int(r.lp_e8)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--win", required=True, choices=sorted(WINDOWS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=2)
    a = ap.parse_args()
    w = WINDOWS[a.win]
    if SL.BASE_DATE != w["t0"] or SL.DAYS_TOTAL != 80:
        sys.exit("SIGFEAT_BASE_DATE=%s SIGFEAT_DAYS=%d ≠ окно %s (%s) / 80" % (SL.BASE_DATE, SL.DAYS_TOTAL, a.win, w["t0"]))
    st.check_dates(w["t0"], w["hi"])
    t = time.time()
    coins = set(x for x in open(K.CRYPTO, encoding="utf-8").read().replace(chr(10), ",").split(",") if x)
    ev = pd.read_csv(w["ev"], usecols=["side", "sym", "level", "sig_ms", "indep", "natr_e8", "lp_e8"])
    ev = ev[(ev.indep == 1) & ev.sym.isin(coins) & (ev.side == "short")]
    ab = w["t0_ms"] + ev.sig_ms
    ev = ev[(ab >= w["lo"]) & (ab + 3_600_000 <= w["form"])]
    df = SL.read_sigsx(w["sigsx"], usecols=["sym", "level", "sig_ms", "dist_e8"])
    dist = {(r.sym, int(r.level), int(r.sig_ms)): int(r.dist_e8) for r in df.itertuples(index=False)}
    syms = sorted(ev.sym.unique())
    G.update(trades=w["trades"], ev={s: g for s, g in ev.groupby("sym")}, dist=dist, t0_ms=w["t0_ms"], lo=w["lo"], form=w["form"])
    print("%s: подходов шорта в окне формирования: %d, монет %d, загрузка %.0f с" % (a.win, len(ev), len(syms), time.time() - t), flush=True)
    rows = []
    with Pool(a.procs) as pool:
        for i, r in enumerate(pool.imap_unordered(process, syms, chunksize=1), 1):
            rows += r
            if i % 100 == 0:
                print("  %d/%d монет, %.0f с" % (i, len(syms), time.time() - t), flush=True)
    rows.sort(key=lambda r: (r["sym"], r["sig_abs_ms"], r["level"]))
    cols = ["sym", "level", "sig_abs_ms", "ok_t60", "y_n_rv60", "rv_e8", "lp_e8"]
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline=chr(10)) as f:
        f.write(",".join(cols) + chr(10))
        for r in rows:
            f.write(",".join(str(r[c]) for c in cols) + chr(10))
    os.replace(tmp, a.out)
    st_ = dict(win=a.win, rows=len(rows), ok=sum(1 for r in rows if r["ok_t60"] == 1), labelled=sum(1 for r in rows if r["y_n_rv60"] != ""), seconds=round(time.time() - t))
    json.dump(st_, open(a.out + ".stats.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(st_, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
