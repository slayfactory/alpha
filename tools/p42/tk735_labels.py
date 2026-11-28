#!/usr/bin/env python3
"""TK-735, §13.79.1 P4: метки `y_n` подходов шорта SEL в единице RV60 (вместо NATR30), окно wa, эпизоды 17.06–30.06 с `sig_ms + 60 мин ≤ 01.07 00:00` (момент формирования).

Код метки — `tk728_labels.label2` дословно (`sigfeat_lib.outcome`, склад тиков, только чтение); меняется только ширина пробоя: X = ⌈|R|·RV60 / SCALE⌉, RV60 — на закрытой минуте перед сигналом (доля цены ×1e8).
  cd /data/p42-tk728/code && SIGFEAT_BASE_DATE=2026-06-17 SIGFEAT_DAYS=80 PYTHONPATH=/data/p42-tk735/code python3 /data/p42-tk735/code/tk735_labels.py --out /data/p42-tk735/res/lab-rv60-wa.csv [--procs 2]
Выход — CSV: sym, level, sig_abs_ms, ok_t60, y_n_rv60, rv_e8, lp_e8.
"""
from __future__ import annotations

import argparse
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

G = {}
FORM_MS = int(__import__("datetime").datetime(2026, 7, 1, tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)
T0_WA = 1781654400000


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
        abs_ms = T0_WA + int(r.sig_ms)
        if abs_ms + 3_600_000 > FORM_MS:
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
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=2)
    a = ap.parse_args()
    w = B.WINS["wa"]
    if SL.BASE_DATE != w.t0 or SL.DAYS_TOTAL != 80:
        sys.exit("SIGFEAT_BASE_DATE=%s SIGFEAT_DAYS=%d ≠ окно wa (%s) / 80" % (SL.BASE_DATE, SL.DAYS_TOTAL, w.t0))
    st.check_dates(w.t0, w.hi)
    t = time.time()
    coins = set(x for x in open(K.CRYPTO, encoding="utf-8").read().replace("\n", ",").split(",") if x)
    ev = pd.read_csv(os.path.join(B.EV_DIR, "wa.csv"), usecols=["side", "sym", "level", "sig_ms", "indep", "natr_e8", "lp_e8"])
    ev = ev[(ev.indep == 1) & ev.sym.isin(coins) & (ev.side == "short")]
    ev = ev[T0_WA + ev.sig_ms + 3_600_000 <= FORM_MS]
    df = SL.read_sigsx(f"{B.ENG}/wa/b0p0/short-sigsx.csv", usecols=["sym", "level", "sig_ms", "dist_e8"])
    dist = {(r.sym, int(r.level), int(r.sig_ms)): int(r.dist_e8) for r in df.itertuples(index=False)}
    syms = sorted(ev.sym.unique())
    G.update(trades=B.WDIR["wa"] + "/aggtrades", ev={s: g for s, g in ev.groupby("sym")}, dist=dist)
    print("wa: подходов шорта до 30.06 23:00: %d, монет %d, загрузка %.0f с" % (len(ev), len(syms), time.time() - t), flush=True)
    rows = []
    with Pool(a.procs) as pool:
        for i, r in enumerate(pool.imap_unordered(process, syms, chunksize=1), 1):
            rows += r
            if i % 100 == 0:
                print("  %d/%d монет, %.0f с" % (i, len(syms), time.time() - t), flush=True)
    rows.sort(key=lambda r: (r["sym"], r["sig_abs_ms"], r["level"]))
    cols = ["sym", "level", "sig_abs_ms", "ok_t60", "y_n_rv60", "rv_e8", "lp_e8"]
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(",".join(cols) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in cols) + "\n")
    os.replace(tmp, a.out)
    st_ = dict(rows=len(rows), ok=sum(1 for r in rows if r["ok_t60"] == 1), labelled=sum(1 for r in rows if r["y_n_rv60"] != ""), seconds=round(time.time() - t))
    json.dump(st_, open(a.out + ".stats.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(st_, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
