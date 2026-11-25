#!/usr/bin/env python3
"""TK-709 (протокол P-TK709 §4–§5, §8 ступень 3, поправка PS-1019): исход событий по ленте — 9 клеток (X × h) × варианты main / R1 / R3 / R4_1s / R4_5s → БД.
  main  — вход проходом по ленте на $500 (E0 = max(первая сделка, VWAP)), выход зеркально; T0 = закрытие минуты события + 4 мс (§5)
  R1    — вход по первой сделке покупателя-тейкера, выход по первой сделке продавца-тейкера (правило П-42)
  R3    — минутная модель: вход по первой сделке минуты t+1 (+1 бп), выход по последней сделке минуты t+h (−1 бп)
  R4_1s / R4_5s — main с T0 через 1 с / 5 с после закрытия минуты (задержка живого сигнала); выход по часам прежний
Запуск (srv, юнит): python3 tk709_run.py <БД> <run_id> <run_id событий> [символ,символ…]
Пишет `trades(...)`, `counts(run_id, variant, x, h, reason, n)`; повтор с тем же run_id — отказ. Один открытый вход на монету в варианте × клетке."""
from __future__ import annotations

import collections
import datetime as dt
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk709_tape as tp  # noqa: E402

MIN = 60_000
LAT = 4
NOTIONAL = 500.0
FEE = 0.000315                                       # тейкер 3,5 бп минус 10 % возврата, на ногу
EXTRA_CAP = 600_000
XS = (0.05, 0.10, 0.20)
HS = (5, 15, 60)
END_MS = int(dt.datetime(2026, 11, 23, tzinfo=dt.timezone.utc).timestamp() * 1000)    # тиков за 23.11 нет
SPLIT_DAY = (dt.date(2026, 7, 1) - tp.EPOCH).days
VARIANTS = ("main", "R1", "R3", "R4_1s", "R4_5s")
R4 = {"R4_1s": 1000, "R4_5s": 5000}
COLS = "run_id, variant, x, h, sym, t_event, t0, t1, e0, e1, p1, vwap, delay_ms, exit_status, mae, pnl, gross, period, day, thin_main"


def pnl_of(e0, e1):
    g = e1 / e0 - 1.0
    return NOTIONAL * g - NOTIONAL * FEE - NOTIONAL * (1.0 + g) * FEE, g


class Win:
    """Слайс ленты [m, hi] одного события с подокнами по времени."""

    def __init__(self, t, p, q, b):
        self.t, self.p, self.q, self.b = t, p, q, b

    def cut(self, a, z):
        i0, i1 = int(np.searchsorted(self.t, a, "left")), int(np.searchsorted(self.t, z, "right"))
        return self.t[i0:i1], self.p[i0:i1], self.q[i0:i1], self.b[i0:i1]

    def last_px(self, z):
        i = int(np.searchsorted(self.t, z, "right")) - 1
        return float(self.p[i]) if i >= 0 else float("nan")


def first_where(t, p, mask):
    k = np.flatnonzero(mask)
    return float(p[k[0]]) if len(k) else None


def mae_of(w: Win, a, z, e0):
    t, p, _, _ = w.cut(a, z)
    return min(0.0, float(p.min()) / e0 - 1.0) if len(p) else 0.0


def sim_event(w: Win, m, h, variant, last_fallback):
    """→ ('ok', rec) | ('thin'|'noprice', None). rec: (t0, t1, e0, e1, p1, vwap, delay, exit_status, mae). Окно `w` — [m, m+h·мин+LAT+600 с]."""
    t1 = m + h * MIN + LAT
    if variant == "R3":
        t0 = m
        ta, pa, _, _ = w.cut(m, m + MIN - 1)
        tb, pb, _, _ = w.cut(m + (h - 1) * MIN, m + h * MIN - 1)
        if not len(pa) or not len(pb):
            return "noprice", None
        e0, e1 = float(pa[0]) * 1.0001, float(pb[-1]) * 0.9999
        return "ok", (t0, m + h * MIN, e0, e1, float(pa[0]), float(pb[-1]), int(ta[0] - m), tp.ST_OK, mae_of(w, t0, m + h * MIN, e0))
    t0 = m + R4.get(variant, LAT)
    tt, ttp, tq, tb = w.cut(t0, t0 + 60_000)
    if variant == "R1":
        e0 = first_where(tt, ttp, tb)
        if e0 is None:
            return "thin", None
        ts, ps, qs, bs = w.cut(t1, t1 + EXTRA_CAP)
        e1 = first_where(ts, ps, ~bs)
        st = tp.ST_OK
        if e1 is None:
            e1 = last_fallback
            st = tp.ST_LAST
            if np.isnan(e1):
                return "noprice", None
        return "ok", (t0, t1, e0, e1, e0, e1, int(tt[np.flatnonzero(tb)[0]] - t0), st, mae_of(w, t0, t1, e0))
    res = tp.entry_walk(tt, ttp, tq, tb, t0)
    if res is None:
        return "thin", None
    e0, p1, vw, delay = res
    ts, ps, qs, bs = w.cut(t1, t1 + EXTRA_CAP)
    xr = tp.exit_walk(ts, ps, qs, bs, t1, NOTIONAL / e0, last_fallback)
    if xr is None:
        return "noprice", None
    e1, _, _, st = xr
    return "ok", (t0, t1, e0, e1, p1, vw, delay, st, mae_of(w, t0, t1, e0))


def run_symbol(sym, evs, run, cnt):
    """evs: [(x, t_event)] по возрастанию t в каждом x. → строки trades."""
    tape = tp.Tape(sym)
    rows = []
    thin_main = set()
    by_x = collections.defaultdict(list)
    for x, t in evs:
        by_x[x].append(t)
    for x in XS:
        ts = sorted(by_x.get(x, []))
        cnt[("all", x, 0, "events")] += len(ts)
        chain = {(v, h): -1 for v in VARIANTS for h in HS}
        for te in ts:
            m = te + MIN
            hi = m + 60 * MIN + LAT + EXTRA_CAP
            win = None
            for h in HS:
                t1 = m + h * MIN + LAT
                if t1 + EXTRA_CAP > END_MS:
                    cnt[("all", x, h, "beyond")] += 1
                    continue
                if not tape.have(m, t1 + EXTRA_CAP):
                    cnt[("all", x, h, "no_tape")] += 1
                    continue
                if win is None:
                    win = Win(*tape.span(m, hi))
                lastpx = win.last_px(t1 + EXTRA_CAP)
                if np.isnan(lastpx):
                    lastpx = tape.last_before(m)
                for v in VARIANTS:
                    t0n = m if v == "R3" else m + R4.get(v, LAT)
                    if t0n < chain[(v, h)]:
                        cnt[(v, x, h, "overlap")] += 1
                        continue
                    st, rec = sim_event(win, m, h, v, lastpx)
                    if st != "ok":
                        cnt[(v, x, h, st)] += 1
                        if v == "main" and st == "thin":
                            thin_main.add((x, h, te))
                        continue
                    t0, t1r, e0, e1, p1, vw, delay, xst, mae = rec
                    pnl, g = pnl_of(e0, e1)
                    day = int(t1r // tp.DAY_MS)
                    rows.append((run, v, x, h, sym, te, t0, t1r, e0, e1, p1, vw, delay, xst, mae, pnl, g, "A" if day < SPLIT_DAY else "B",
                                 tp.day_str(day), 1 if (x, h, te) in thin_main else 0))
                    chain[(v, h)] = t1r
                    cnt[(v, x, h, "traded")] += 1
    for k, n in tape.bad.items():
        if n:
            cnt[("all", 0, 0, "g2_" + k)] += n
    return rows


def main():
    db, run, evrun = sys.argv[1], sys.argv[2], sys.argv[3]
    only = set(sys.argv[4].split(",")) if len(sys.argv) > 4 else None
    con = sqlite3.connect(db)
    con.execute(f"create table if not exists trades(run_id text, variant text, x real, h integer, sym text, t_event integer, t0 integer, t1 integer, e0 real, e1 real, p1 real, vwap real, delay_ms integer, exit_status integer, mae real, pnl real, gross real, period text, day text, thin_main integer)")
    con.execute("create table if not exists counts(run_id text, variant text, x real, h integer, reason text, n integer)")
    con.execute("create index if not exists tr_run on trades(run_id, variant, x, h, period)")
    if con.execute("select count(*) from counts where run_id=?", (run,)).fetchone()[0]:
        raise SystemExit("run_id уже есть: " + run)
    syms = [r[0] for r in con.execute("select distinct sym from events where run_id=? order by sym", (evrun,))]
    if only:
        syms = [s for s in syms if s in only]
    cnt = collections.Counter()
    for i, sym in enumerate(syms, 1):
        evs = [(r[0], r[1]) for r in con.execute("select x, t_ms from events where run_id=? and sym=?", (evrun, sym))]
        rows = run_symbol(sym, evs, run, cnt)
        con.executemany(f"insert into trades({COLS}) values ({','.join('?' * 20)})", rows)
        if i % 20 == 0:
            con.commit()
            print(i, "/", len(syms), sym, len(rows), flush=True)
    con.executemany("insert into counts values (?,?,?,?,?,?)", [(run, v, x, h, r, n) for (v, x, h, r), n in cnt.items()])
    con.commit()
    print("done", len(syms), "symbols")


if __name__ == "__main__":
    main()
