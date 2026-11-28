#!/usr/bin/env python3
"""П-42 этап A, шаг 4в (TK-728, §13.78.4): метки эпизодов SEL c539 — `y` (ход 1 % цены, `o_x1_t60_f01`) и `y_n` (ход 1 NATR сигнала, X = ⌈|R|·natr_e8 / SCALE⌉) в одной проходке по сделкам.
Код метки — `tk671_bkep.label` и `tk604_episodes.process` дословно (`sigfeat_lib.outcome`, возврат — 0,1 % уровня, окно 60 мин от сигнальной сделки); меняется только ширина пробоя X у `y_n`.
Эпизоды — независимые (`indep = 1`) строки `/data/p42-tk625/ev-h775/<окно>.csv`, монеты c539. Сделки — склад тиков (`tk671_ticks.StoreTicks`, только чтение).
  SIGFEAT_BASE_DATE=<T0 окна> SIGFEAT_DAYS=80 python3 tk728_labels.py --win wa --out /data/p42-tk728/res/lab-wa.csv [--procs 2]
Выход — CSV: side, sym, level, sig_ms, ok_t60, y, y_n, natr_e8, lp_e8, locate_fail (+ `<out>.stats.json`). Метка пуста, если окно не в сетке / не хватает суток сделок (`ok_t60 = 0`) или natr_e8 ≤ 0 (`y_n`)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sigfeat_lib as L  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk671_bkep as B  # noqa: E402
import tk671_ticks as T  # noqa: E402

COLS = ["side", "sym", "level", "sig_ms", "ok_t60", "y", "y_n", "natr_e8", "lp_e8", "locate_fail"]
CRYPTO = "/data/p42-tk621/universe/symbols-crypto.txt"
G = {}


def label2(tk, sig_ms, s, lpx, dist, natr_e8):
    """(locate_fail, ok_t60, y, y_n): как `tk671_bkep.label`, плюс исход при ширине пробоя 1 NATR (X = ⌈|R|·natr_e8 / SCALE⌉ — формула `tk604_episodes.process`)."""
    R = s * lpx
    tt, pp, _ = tk.span(sig_ms, sig_ms + 60 * L.MIN_MS)
    P2 = s * pp
    k2 = L.find_signal_tick(tt, P2, sig_ms, R, dist)
    if k2 is None:
        return 1, 0, "", ""
    if not B.ok_t60(tk, sig_ms):
        return 0, 0, "", ""
    absR = abs(R)
    f_floor = absR * 1000 // 1_000_000
    Pw = P2[k2 + 1:]
    y = int(L.outcome(Pw, R, L.ceil_mul(absR, 10000, 1_000_000), f_floor)[0] == L.O3)
    y_n = int(L.outcome(Pw, R, -(-(absR * natr_e8) // L.SCALE), f_floor)[0] == L.O3) if natr_e8 > 0 else ""
    return 0, 1, y, y_n


def process(sym):
    out, n_loc, n_not = [], 0, 0
    tk = T.StoreTicks(G["trades"], sym, keep=4)
    sub = G["ev"].get(sym)
    if sub is None:
        return out, dict(locate_fail=0, not_ok=0)
    for side, s in B.SIDES.items():
        part = sub[sub.side == side].sort_values(["sig_ms", "level"])
        dist = G["dist"][side]
        for r in part.itertuples(index=False):
            d = dist.get((sym, int(r.level), int(r.sig_ms)))
            if d is None:
                raise RuntimeError("нет дампа b0p0: %s %s %d %d" % (side, sym, r.level, r.sig_ms))
            lf, ok, y, y_n = label2(tk, int(r.sig_ms), s, int(r.lp_e8), d, int(r.natr_e8))
            n_loc += lf
            n_not += int(lf == 0 and ok == 0)
            out.append(dict(side=side, sym=sym, level=int(r.level), sig_ms=int(r.sig_ms), ok_t60=ok, y=y, y_n=y_n, natr_e8=int(r.natr_e8), lp_e8=int(r.lp_e8), locate_fail=lf))
    return out, dict(locate_fail=n_loc, not_ok=n_not)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--win", required=True, choices=("wa", "wb", "wc"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--syms", default="")
    a = ap.parse_args()
    w = B.WINS[a.win]
    if L.BASE_DATE != w.t0 or L.DAYS_TOTAL != 80:
        sys.exit("SIGFEAT_BASE_DATE=%s SIGFEAT_DAYS=%d ≠ окно %s (%s) / 80" % (L.BASE_DATE, L.DAYS_TOTAL, a.win, w.t0))
    st.check_dates(w.t0, w.hi)
    t = time.time()
    coins = set(x for x in open(CRYPTO, encoding="utf-8").read().replace("\n", ",").split(",") if x)
    ev = pd.read_csv(os.path.join(B.EV_DIR, a.win + ".csv"), usecols=["side", "sym", "level", "sig_ms", "indep", "natr_e8", "lp_e8"])
    ev = ev[(ev.indep == 1) & ev.sym.isin(coins)]
    dist = {}
    for side in B.SIDES:
        df = L.read_sigsx(f"{B.ENG}/{a.win}/b0p0/{side}-sigsx.csv", usecols=["sym", "level", "sig_ms", "dist_e8"])
        dist[side] = {(r.sym, int(r.level), int(r.sig_ms)): int(r.dist_e8) for r in df.itertuples(index=False)}
    syms = sorted(ev.sym.unique())
    if a.syms:
        syms = [x for x in syms if x in a.syms.split(",")]
    G.update(trades=B.WDIR[a.win] + "/aggtrades", ev={s: g for s, g in ev.groupby("sym")}, dist=dist)
    print("окно %s: независимых эпизодов c539 %d, монет %d, загрузка %.0f с" % (a.win, len(ev), len(syms), time.time() - t), flush=True)
    rows, tot = [], dict(locate_fail=0, not_ok=0)
    with Pool(a.procs) as pool:
        for i, (r, s_) in enumerate(pool.imap_unordered(process, syms, chunksize=1), 1):
            rows += r
            for k in tot:
                tot[k] += s_[k]
            if i % 100 == 0:
                print("  %d/%d монет, %.0f с" % (i, len(syms), time.time() - t), flush=True)
    rows.sort(key=lambda r: (r["side"], r["sym"], r["sig_ms"], r["level"]))
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(",".join(COLS) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in COLS) + "\n")
    os.replace(tmp, a.out)
    tot.update(rows=len(rows), labelled_y=sum(1 for r in rows if r["y"] != ""), labelled_yn=sum(1 for r in rows if r["y_n"] != ""), seconds=round(time.time() - t))
    json.dump(tot, open(a.out + ".stats.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(tot, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
