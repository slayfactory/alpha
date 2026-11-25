#!/usr/bin/env python3
"""TK-709 (протокол P-TK709 §8, ворота до чтения результата): G1 — независимый пересчёт цен входа/выхода по ленте для случайных сделок `main` (медленный цикл по сделкам, другая реализация);
G3 — независимый пересчёт событий (pandas, другая логика серий) по нескольким монетам против таблицы `events`. G2 (порядок времени, цены/количества > 0) считается в `tk709_run.py` (счётчики g2_*).
Запуск (srv): python3 tk709_gates.py <БД> <run_id сделок> <run_id событий> [n сделок=20] [монет G3=3]. Печатает PASS/FAIL, ничего про P&L."""
from __future__ import annotations

import os
import random
import sqlite3
import sys
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk709_tape as tp  # noqa: E402

PREM = "/data/p42-premium"
MIN = 60_000


def naive_entry(t, p, q, buy, t0):
    need, cum_q, p1 = 500.0, 0.0, None
    spent = 0.0
    for i in range(len(t)):
        if not buy[i] or t[i] < t0 or t[i] > t0 + 60_000:
            continue
        if p1 is None:
            p1 = p[i]
        tr = p[i] * q[i]
        if spent + tr >= need:
            take = need - spent
            cum_q += take / p[i]
            return max(p1, 500.0 / cum_q)
        spent += tr
        cum_q += q[i]
    return None


def naive_exit(t, p, q, buy, t1, qty):
    got, spent, p1 = 0.0, 0.0, None
    for i in range(len(t)):
        if buy[i] or t[i] < t1 or t[i] > t1 + 600_000:
            continue
        if p1 is None:
            p1 = p[i]
        take = min(q[i], qty - got)
        spent += take * p[i]
        got += take
        if got >= qty - 1e-15:
            return min(p1, spent / qty)
    return None if p1 is None else min(p1, spent / got)


def g1(con, run, n):
    rows = con.execute("select sym, t_event, x, h, t0, t1, e0, e1, delay_ms, exit_status from trades where run_id=? and variant='main' and exit_status in (0,1)", (run,)).fetchall()
    random.Random(709).shuffle(rows)
    bad, k = 0, 0
    for sym, te, x, h, t0, t1, e0, e1, delay, st in rows[:n]:
        tape = tp.Tape(sym)
        t, p, q, b = tape.span(t0 - 1, t1 + 600_000)
        n0 = naive_entry(t, p, q, b, t0)
        n1 = naive_exit(t, p, q, b, t1, 500.0 / e0)
        ok = n0 is not None and n1 is not None and abs(n0 / e0 - 1) < 1e-9 and abs(n1 / e1 - 1) < 1e-9
        k += 1
        if not ok:
            bad += 1
            print("G1 расхождение", sym, te, x, h, e0, n0, e1, n1)
    print("G1", "PASS" if bad == 0 else "FAIL", k, "сделок, расхождений", bad)
    return bad == 0


def ev_pandas(sym, x):
    frames = []
    d = os.path.join(PREM, sym)
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".zip"):
            continue
        with zipfile.ZipFile(os.path.join(d, fn)) as z:
            frames.append(pd.read_csv(z.open(z.namelist()[0]), usecols=["open_time", "low"]))
    df = pd.concat(frames).drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df["open_time"] = np.where(df["open_time"] > 10 ** 14, df["open_time"] // 1000, df["open_time"])
    beyond = df["low"] <= -x
    gap = df["open_time"].diff() != MIN
    run_id = ((beyond != beyond.shift(fill_value=False)) | gap).cumsum()
    first = beyond & (run_id != run_id.shift(fill_value=-1))
    return sorted(df.loc[first, "open_time"].astype(int).tolist())


def g3(con, evrun, nsym):
    syms = [r[0] for r in con.execute("select sym from events where run_id=? group by sym order by count(*) desc", (evrun,))]
    pick = [syms[0], syms[len(syms) // 2], syms[-1]][:nsym]
    ok = True
    for s in pick:
        for x in (0.05, 0.10, 0.20):
            a = ev_pandas(s, x)
            b = sorted(r[0] for r in con.execute("select t_ms from events where run_id=? and sym=? and x=?", (evrun, s, x)))
            same = a == b
            ok &= same
            print("G3", s, x, len(a), len(b), "ok" if same else "РАСХОЖДЕНИЕ")
    print("G3", "PASS" if ok else "FAIL")
    return ok


def main():
    db, run, evrun = sys.argv[1], sys.argv[2], sys.argv[3]
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 20
    nsym = int(sys.argv[5]) if len(sys.argv) > 5 else 3
    con = sqlite3.connect(db)
    a, b = g1(con, run, n), g3(con, evrun, nsym)
    sys.exit(0 if (a and b) else 1)


if __name__ == "__main__":
    main()
