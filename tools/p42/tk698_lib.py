#!/usr/bin/env python3
"""TK-698 (П-42, описательно): общие функции — клетки c539 из БД летописи, сделки из архивов `cells-arch`, склейка окон wa / wb / wc как в `tk604_stats.splice`.
Движок не запускается; июнь и январь–май не читаются (сделки до 01.07 отбрасываются при загрузке без подсчёта; гейт В-170).
Время: `*_ms` окна — мс от T0 окна, абсолютные = + T0; сутки — UTC от 2026-07-01 (0…135). Ставка 500 $, P&L сделки `val` = gross − fees (без фандинга, как метрика БД и `tk604_stats.splice`)."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sqlite3
import subprocess

import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk663_shape as sh  # noqa: E402
import tk684_metrics as mt  # noqa: E402

ROOT = "/data/p42-tk698"
ARCH = "/data/p42-tk674c/cells-arch"
CHRON = "/data/chron/chron.sqlite"
STAGE = ROOT + "/stage"
DAY_MS = 86_400_000
D0 = dt.date(2026, 7, 1)
NDAYS = 136
LAST_MS = int(dt.datetime(2026, 11, 14, tzinfo=dt.timezone.utc).timestamp() * 1000)
FIRST_MS = int(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
# окна как tk604_stats: (T0, первый зачётный день, последний)
WINDOWS = {"wa": ("2026-06-17", "2026-07-01", "2026-08-25"), "wb": ("2026-07-25", "2026-08-26", "2026-10-02"), "wc": ("2026-09-01", "2026-10-03", "2026-11-13")}
ENG = {"long": "L", "short": "S"}
MONTHS = ("jul", "aug", "sep", "oct", "nov")
MONTH_LEN = (31, 31, 30, 31, 13)
MONTH_OF_DAY = np.repeat(np.arange(5), MONTH_LEN)
STAKE = 500.0
MARGIN = 250.0


def d_(s):
    return dt.date.fromisoformat(s)


def t0_ms(w):
    d = d_(WINDOWS[w][0])
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def day_idx(w, rel_ms):
    return (d_(WINDOWS[w][0]) - D0).days + np.asarray(rel_ms, dtype=np.int64) // DAY_MS


def chron_ro():
    return sqlite3.connect("file:%s?mode=ro" % CHRON, uri=True)


def metric_row(db, cell, side, uni="c539", win="01.07–13.11"):
    """Последняя строка metric клетки (id, extra-словарь); None, если нет."""
    r = db.execute("select id, trades, usd, sharpe, extra, entry from metric where cell = ? and side = ? and uni = ? and win = ? order by id desc limit 1", (cell, side, uni, win)).fetchone()
    if r is None:
        return None
    ex = json.loads(r[4] or "{}")
    return dict(id=r[0], n=r[1], usd=r[2], sharpe=r[3], ex=ex, entry=r[5], cell=cell, side=side)


def _cell_dir(win, side, path):
    """Каталог клетки окна: готовый, иначе достаётся из cells-arch одним членом архива в STAGE."""
    sub = path.split("/cells/", 1)[1]                    # <окно>-<сторона>/<пакет>/<клетка>
    dest = os.path.join(STAGE, sub)
    if os.path.isfile(os.path.join(dest, "meta.json")):
        return dest
    if os.path.isdir(path):
        return path
    wside, batch, cell = sub.split("/", 2)
    os.makedirs(os.path.join(STAGE, wside), exist_ok=True)
    subprocess.run(["tar", "--zstd", "-xf", "%s/%s/%s.tar.zst" % (ARCH, wside, batch), "-C", os.path.join(STAGE, wside), "%s/%s" % (batch, cell)], check=True)
    assert os.path.isfile(os.path.join(dest, "meta.json")), dest
    return dest


def extract_batch(wside, batch, cells):
    """Достать несколько клеток одного пакета одним вызовом tar (item 3, сотни клеток)."""
    os.makedirs(os.path.join(STAGE, wside), exist_ok=True)
    todo = [c for c in cells if not os.path.isfile(os.path.join(STAGE, wside, batch, c, "meta.json"))]
    if not todo:
        return
    subprocess.run(["tar", "--zstd", "-xf", "%s/%s/%s.tar.zst" % (ARCH, wside, batch), "-C", os.path.join(STAGE, wside)] + ["%s/%s" % (batch, c) for c in todo], check=True)


TRADE_COLS = ["sym", "level", "sig_min", "entry_min", "exit_min", "entry_px_e8", "kind", "takes", "gross", "fees", "funding", "net", "entry_day", "sig_ms", "entry_ms", "exit_ms"]


def load_cell(row, lots=False):
    """Сделки клетки по трём окнам, только зачётные дни 01.07–13.11 (как splice). row — результат metric_row. Столбцы: win, sym, sig_ms / entry_ms / exit_ms (абсолют), day, kind, takes, gross, fees, funding, net, entry_px_e8 (+ lot2, net1, net2 при lots)."""
    side = row["side"]
    parts = []
    for w in WINDOWS:
        d = _cell_dir(w, side, row["ex"]["dirs"][w])
        f = os.path.join(d, "%s-trades.csv" % side)
        df = pd.read_csv(f)
        if lots and os.path.isfile(os.path.join(d, "%s-lots.csv" % side)):
            lt = pd.read_csv(os.path.join(d, "%s-lots.csv" % side))
            assert len(lt) == len(df), (row["cell"], w, len(lt), len(df))
            for c in lt.columns:
                df["lot_" + c] = lt[c].to_numpy()
        day = day_idx(w, df["entry_ms"].to_numpy())
        lo = (d_(WINDOWS[w][1]) - D0).days
        hi = (d_(WINDOWS[w][2]) - D0).days
        df = df[(day >= lo) & (day <= hi)].copy()
        df["day"] = day[(day >= lo) & (day <= hi)]
        t0 = t0_ms(w)
        for c in ("sig_ms", "entry_ms", "exit_ms"):
            df[c] = df[c].astype(np.int64) + t0
        df["win"] = w
        df["val"] = df["gross"] - df["fees"]
        parts.append(df)
    out = pd.concat(parts, ignore_index=True)
    assert out["entry_ms"].min() >= FIRST_MS and out["entry_ms"].max() < LAST_MS, "изоляция"
    return out


def daily_pnl(tr):
    return np.bincount(tr["day"].to_numpy(), weights=tr["val"].to_numpy(), minlength=NDAYS)


def metrics(daily):
    """Метрики суточного ряда (В-169, как строки `metric` БД): Σ $, Шарп годовой, просадка $ и % маржи, перехай (самый долгий / средний / хвост, сутки), доля под водой — `tk684_metrics` + `tk663_shape.uw`."""
    d = [float(x) for x in daily]
    u = sh.uw(d)
    return dict(usd=float(sum(d)), sharpe=mt.sharpe(d), dd=u["maxdd"], dd_pct=100.0 * u["maxdd"] / MARGIN, gap_longest=u["gap_longest"], gap_mean=u["gap_mean"], gap_tail=u["tail"], under=u["under_share"], highs=u["highs"])


def rr(net):
    return mt.rr([float(x) for x in net])
