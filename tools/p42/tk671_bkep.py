#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): метка `y` (`o_x1_t60_f01`) эпизодов честной вселенной h775 для признака bk (TK-604/654). Таблица TK-604 (`/data/p42-tk604/ep`) — только 566 старых монет; здесь метка
считается для всех независимых эпизодов h775 (строки `/data/p42-tk625/ev-h775/<окно>.csv`, `indep = 1`; сами эпизоды и их границы — те же, что у TK-604, код эпизодов TK-625 скопирован без изменения).
Метка — как `tk604_episodes.process`: сделки (сигнальная, sig + 60 мин], `L.outcome(P, R, ⌈|R|·1 %⌉, ⌊|R|·0,1 %⌋)[0] == O3`; `ok_t60` — окно метки в сетке и все сутки сделок есть. Сделки — `tk671_ticks.StoreTicks`
(склад Rust; равенство с zip побитово — `tk671_ticks_gate.py`). Выход — CSV в столбцах `tk604_feat.EP_COLS` (`in_pool` = 0 — в историю не входит) + `<out>.stats.json`.
Использование (srv): SIGFEAT_BASE_DATE=<T0> SIGFEAT_DAYS=80 python3 tk671_bkep.py --win wa --out <csv> [--procs 7]
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
import sigfeat_lib as L  # noqa: E402
import tk604_feat as tf  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk671_ticks as T  # noqa: E402

EV_DIR = "/data/p42-tk625/ev-h775"
ENG = "/data/p42-tk625/h775/eng"
SIDES = {"long": 1, "short": -1}
WINS = {w.name: w for w in st.HIST_WINDOWS}
WDIR = {"w0": "/data/p42-tk625/h775/w0", "wa": "/data/p42-tk621/wa", "wb": "/data/p42-tk621/wb", "wc": "/data/p42-tk621/wc"}
G = {}


def ok_t60(tk, sig_ms):
    """Как `tk604_episodes.ok_t60`."""
    if sig_ms + 60 * L.MIN_MS > L.END_MS - 1:
        return False
    return all(tk.day(d) is not None for d in range(sig_ms // L.DAY_MS, (sig_ms + 60 * L.MIN_MS) // L.DAY_MS + 1))


def label(tk, sig_ms, s, lpx, dist):
    """(locate_fail, ok_t60, y) эпизода: строка `tk604_episodes.process` дословно, сделки со склада."""
    R = s * lpx
    tt, pp, _ = tk.span(sig_ms, sig_ms + 60 * L.MIN_MS)
    P2 = s * pp
    k2 = L.find_signal_tick(tt, P2, sig_ms, R, dist)
    if k2 is None:
        return 1, 0, ""
    if not ok_t60(tk, sig_ms):
        return 0, 0, ""
    absR = abs(R)
    f_floor = absR * 1000 // 1_000_000
    return 0, 1, int(L.outcome(P2[k2 + 1:], R, L.ceil_mul(absR, 10000, 1_000_000), f_floor)[0] == L.O3)


def process(sym):
    cfg = G
    out, n_loc, n_not = [], 0, 0
    tk = T.StoreTicks(cfg["trades"], sym, keep=4)
    sub = cfg["ev"].get(sym)
    if sub is None:
        return out, dict(locate_fail=0, not_ok=0, store=0, zip=0)
    for side, s in SIDES.items():
        part = sub[sub.side == side].sort_values(["sig_ms", "level"])
        dist = cfg["dist"][side]
        for r in part.itertuples(index=False):
            d = dist.get((sym, int(r.level), int(r.sig_ms)))
            if d is None:
                raise RuntimeError("нет дампа b0p0: %s %s %d %d" % (side, sym, r.level, r.sig_ms))
            lf, ok, y = label(tk, int(r.sig_ms), s, int(r.lp_e8), d)
            n_loc += lf
            n_not += int(lf == 0 and ok == 0)
            out.append(dict(side=side, sym=sym, level=int(r.level), sig_ms=int(r.sig_ms), indep=1, ok_t60=ok, y=y, y_n="", natr_e8=int(r.natr_e8), lp_e8=int(r.lp_e8), in_pool=0, locate_fail=lf))
    return out, dict(locate_fail=n_loc, not_ok=n_not, store=tk.n_store, zip=tk.n_zip)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--win", required=True, choices=sorted(WINS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--syms", default="")
    a = ap.parse_args()
    w = WINS[a.win]
    if L.BASE_DATE != w.t0 or L.DAYS_TOTAL != 80:
        sys.exit("SIGFEAT_BASE_DATE=%s SIGFEAT_DAYS=%d ≠ окно %s (%s) / 80" % (L.BASE_DATE, L.DAYS_TOTAL, a.win, w.t0))
    st.check_dates(w.t0, w.hi)
    t = time.time()
    ev = pd.read_csv(os.path.join(EV_DIR, a.win + ".csv"), usecols=["side", "sym", "level", "sig_ms", "indep", "natr_e8", "lp_e8"])
    ev = ev[ev.indep == 1]
    dist = {}
    for side in SIDES:
        df = L.read_sigsx(f"{ENG}/{a.win}/b0p0/{side}-sigsx.csv", usecols=["sym", "level", "sig_ms", "dist_e8"])
        dist[side] = {(r.sym, int(r.level), int(r.sig_ms)): int(r.dist_e8) for r in df.itertuples(index=False)}
    syms = sorted(ev.sym.unique())
    if a.syms:
        syms = [x for x in syms if x in a.syms.split(",")]
    G.update(trades=WDIR[a.win] + "/aggtrades", ev={s: g for s, g in ev.groupby("sym")}, dist=dist)
    print("окно %s: независимых эпизодов %d, монет %d, загрузка %.0f с" % (a.win, len(ev), len(syms), time.time() - t), flush=True)
    rows, tot = [], dict(locate_fail=0, not_ok=0, store=0, zip=0)
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
        f.write(",".join(tf.EP_COLS) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in tf.EP_COLS) + "\n")
    os.replace(tmp, a.out)
    tot.update(rows=len(rows), labelled=sum(1 for r in rows if r["y"] != ""), seconds=round(time.time() - t))
    with open(a.out + ".stats.json", "w", encoding="utf-8") as f:
        json.dump(tot, f, ensure_ascii=False, indent=1)
    print(json.dumps(tot, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
