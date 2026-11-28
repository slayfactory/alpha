#!/usr/bin/env python3
"""TK-741: чтение сделок V0 / V0nt и сигналов с ценой уровня (дамп `P42_SIGSX`) — только srv. Формулы метрик — `tk736_lib.py` (В-169), чтение окон — `tk736_an.py`."""
from __future__ import annotations

import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK736_ROOT", "/data/p42-tk741")
import tk726_seam as sm  # noqa: E402
import tk736_an as A  # noqa: E402
import tk736_lib as L  # noqa: E402

ROOT = os.environ.get("TK741_ROOT", "/data/p42-tk741")
TRADES = ROOT + "/trades"
RES = ROOT + "/res"
CID = {"v0": 7411001, "nt": 7412001, "pr": 7413001}
FJ, SEL = ("m02", "m03", "m04", "m05", "m06"), ("wa", "wb", "wc")
ALLW = ("m01",) + FJ + SEL + ("n1",)
# область → (окна, первые, последние сутки от 01.01.2026); FJ и SEL — судимые, январь и N1 — описательно
SCOPES = {"FJ": (FJ, 31, 180), "SEL": (SEL, 181, 316), "POOL": (FJ + SEL, 31, 316), "JAN": (("m01",), 0, 30), "N1": (("n1",), 317, 327)}
STAKE_MARGIN = L.MARGIN


def run_dir(stage, win):
    part = "h1" if win.startswith("m") else "n1" if win == "n1" else "sel"
    return "%s/tk741-%s-%s" % (TRADES, stage, part)


def read_win(stage, win, d=None, cid=None):
    """Сделки клетки этапа в зачётных сутках окна + цена уровня и данные уровня из `*-sigsx.csv` (ключ — монета, номер уровня, сигнал).
    `d`, `cid` (TK-745) — каталог прогона и номер клетки другого прогона (`review_loop.sh`); по умолчанию — этапы TK-741."""
    d = d or run_dir(stage, win)
    cid = cid or CID[stage]
    p = os.path.join(d, win, "%d-trades.csv" % cid)
    t0 = json.load(open(os.path.join(d, "t0.json"), encoding="utf-8"))[win]
    a = A.read_cell(p, t0, win)
    sx = {}
    with open(os.path.join(d, win, "%d-sigsx.csv" % cid), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            sx[(r["sym"], int(r["level"]), int(r["sig_ms"]))] = r
    n = len(a["pnl"])
    lpx, born, first_bar, natr = np.zeros(n), np.zeros(n, np.int64), np.zeros(n, np.int64), np.zeros(n)
    for i in range(n):
        r = sx[(a["sym"][i], int(a["level"][i]), int(a["sig_ms"][i]) - int(t0))]
        lpx[i], born[i], first_bar[i], natr[i] = abs(int(r["level_px_e8"])) / 1e8, int(r["born"]), int(r["first_bar"]), int(r["natr_e8"]) / 1e8
    a.update(lvl=lpx, born=born, first_bar=first_bar, natr=natr, t0=np.full(n, int(t0), np.int64))
    return a


def load(stage, wins, with_sigsx=True):
    parts = [read_win(stage, w) if with_sigsx else A.read_cell(os.path.join(run_dir(stage, w), w, "%d-trades.csv" % CID[stage]), json.load(open(os.path.join(run_dir(stage, w), "t0.json"), encoding="utf-8"))[w], w) for w in wins]
    return A.concat(parts)


def metrics(a, lo, hi):
    """Метрики В-169 по суточному ряду (сутки входа) и по сделкам."""
    p, n = A.day_series(a, lo, hi)
    u = L.uw_stats(p)
    r = a["pnl"]
    st = a["kind"] == "stop"
    return dict(sum=float(p.sum()), trades=int(n.sum()), per_trade=float(p.sum() / n.sum()) if n.sum() else None, sharpe=L.sharpe(p), dd=u["dd"], dd_pct=u["dd"] / STAKE_MARGIN * 100.0,
                gap_longest=u["gap_longest"], gap_mean=u["gap_mean"], gap_p90=u["gap_p90"], tail=u["tail"], rr=L.rr(r), stops=int(st.sum()), loss_per_stop=float(r[st].mean()) if st.any() else None,
                worst=float(r.min()) if len(r) else None, pos_days=float((p > 0).mean()), days=int(hi - lo + 1)), p, n
