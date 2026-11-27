#!/usr/bin/env python3
"""TK-726, П-42 этап A, шаг 1, 1г: описательная диагностика края окон (после чтения 1г, в счётчик испытаний; решений не двигает).

  python3 tk726_edge.py run [--report edge-report.txt]

Что печатает, по трём линиям S1 / S2 / L401 и по 9 членам B + якорю (суммарно по 10 окнам):
  1. профиль по суткам окна: сделок, $/сделку, доля `stop` — сутки 0…6 и 7…13 (затухание = разгон; провал только на 0–1 = край).
  2. плацебо: та же разность «2 суток с номера k против остальных» для k = 0…30 — место k = 0 среди 31 значений (если края нет, k = 0 — обычное значение).
  3. окна по отдельности — какие дают край, и сколько даёт край без n1 и без m01.
Только numpy; сделки — с диска (`trades/…`), код чтения — из tk726_seam.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk726_seam as sm  # noqa: E402

LINES = ("S1", "S2", "L401")
WINS = tuple(sm.WINDOWS)


def window_rows(a, win):
    """Сделки окна: (номер суток от начала окна, pnl, is_stop)."""
    lo, hi = sm.day_of(sm.WINDOWS[win][0]), sm.day_of(sm.WINDOWS[win][1])
    m = sm.in_days(a, lo, hi)
    return (a["eday"][m] - lo).astype(np.int64), a["pnl"][m], (a["kind"][m] == "stop")


def pooled(cells_tr, names, wins):
    d, p, s = [], [], []
    for n in names:
        for w in wins:
            a = window_rows(cells_tr[n][w], w)
            d.append(a[0]); p.append(a[1]); s.append(a[2])
    return np.concatenate(d), np.concatenate(p), np.concatenate(s)


def block_delta(d, p, k, width=2):
    """$/сделку(сутки k…k+width−1) − $/сделку(остальные сутки окна)."""
    inb = (d >= k) & (d < k + width)
    a, b = p[inb], p[~inb]
    return (a.mean() if len(a) else float("nan")) - (b.mean() if len(b) else float("nan")), len(a)


def profile(d, p, s):
    out = []
    for lo, hi in ((0, 0), (1, 1), (2, 2), (3, 3), (4, 6), (7, 13), (14, 10000)):
        m = (d >= lo) & (d <= hi)
        n = int(m.sum())
        out.append((lo, hi, n, float(p[m].mean()) if n else float("nan"), float(s[m].mean()) if n else float("nan")))
    return out


def fmt(x, dg=2):
    return "—" if x != x else ("%.*f" % (dg, x))


def cmd_run(a):
    out = sqlite3.connect(a.db, timeout=60)
    cells = {name: cid for cid, name in out.execute("SELECT cid, name FROM tk726_cell")}
    tr = {name: {w: sm.load_window_trades(w, cid) for w in WINS} for name, cid in cells.items()}
    bs = [n for n in cells if n.startswith("BS-")]
    groups = [(n, [n]) for n in LINES] + [("B9", bs), ("anchor BS-l7-tf60", ["BS-l7-tf60"])]
    lines = []
    say = lines.append
    for gname, names in groups:
        d, p, s = pooled(tr, names, WINS)
        say("== %s (%d клеток, 10 окон, %d сделок)" % (gname, len(names), len(p)))
        say("профиль по суткам окна: сутки | сделок | $/сделку | доля stop")
        for lo, hi, n, u, ss in profile(d, p, s):
            say("  %s | %d | %s | %s" % ("%d" % lo if lo == hi else "%d–%s" % (lo, hi if hi < 1000 else "…"), n, fmt(u), fmt(ss, 3)))
        pl = [block_delta(d, p, k)[0] for k in range(31)]
        rank = 1 + sum(1 for v in pl[1:] if v <= pl[0])
        say("плацебо Δ(2 суток с k) для k = 0…30: k=0 %s; k=1…30 мин %s, среднее %s, макс %s; место k=0 снизу среди 31: %d" % (fmt(pl[0], 3), fmt(min(pl[1:]), 3), fmt(float(np.mean(pl[1:])), 3), fmt(max(pl[1:]), 3), rank))
        per = []
        for w in WINS:
            dw, pw, _ = pooled(tr, names, (w,))
            dl, n2 = block_delta(dw, pw, 0)
            per.append("%s %s (%d)" % (w, fmt(dl, 2), n2))
        say("Δ по окнам (2 суток − остальные; сделок в первых 2 сутках): " + "; ".join(per))
        no = tuple(w for w in WINS if w not in ("n1", "m01"))
        d2, p2, s2 = pooled(tr, names, no)
        say("без n1 и m01: Δ(k=0) %s; плацебо k=1…30 среднее %s, мин %s, макс %s" % (
            fmt(block_delta(d2, p2, 0)[0], 3), fmt(float(np.mean([block_delta(d2, p2, k)[0] for k in range(1, 31)])), 3),
            fmt(min(block_delta(d2, p2, k)[0] for k in range(1, 31)), 3), fmt(max(block_delta(d2, p2, k)[0] for k in range(1, 31)), 3)))
        say("")
    # 4. начало окна: движок и зачёт. Сигналы движка идут с суток 14 от T0; зачёт: m01…m06 и n1 — с суток 20 (на 6 суток позже), wa — с суток 14 (первые сутки движка), wb / wc — вырезка с суток 32 (середина прогона).
    say("== начало окна против начала месяца (зачётные первые 2 суток периода против остальных; в скобках — сделок в первых 2 сутках)")
    say("период | где первые сутки зачёта в прогоне движка | S1 Δ | S2 Δ | L401 Δ | B9 Δ")
    import tk726_exits as ex_mod
    asm = {n: ex_mod.assemble(cid) for n, cid in cells.items() if n in LINES or n in bs}
    where = {"Feb": "сутки 6 от начала сигналов", "Mar": "сутки 6", "Apr": "сутки 6", "May": "сутки 6", "Jun": "сутки 6", "Jul": "сутки 0 (первые сутки движка, wa)",
             "Aug": "середина wa", "Sep": "середина wb", "Oct": "середина wb", "Nov1-13": "середина wc"}
    for pi, (pname, plo, phi) in enumerate(ex_mod.PERIODS):
        if pname not in where:
            continue
        lo = sm.day_of(plo)
        cols = []
        for names in [[n] for n in LINES] + [bs]:
            pn, nn, pr, nr = 0.0, 0, 0.0, 0
            for n in names:
                ar = asm[n]
                m = ar["pidx"] == pi
                first = m & (ar["eday"] < lo + 2)
                rest = m & (ar["eday"] >= lo + 2)
                pn += float(ar["pnl"][first].sum()); nn += int(first.sum()); pr += float(ar["pnl"][rest].sum()); nr += int(rest.sum())
            cols.append("%s (%d)" % (fmt((pn / nn if nn else float("nan")) - (pr / nr if nr else float("nan"))), nn))
        say("%s | %s | %s" % (pname, where[pname], " | ".join(cols)))
    text = "\n".join(lines)
    print(text)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--db", default=sm.OUT_DB)
    r.add_argument("--report", default="")
    r.set_defaults(fn=cmd_run)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
