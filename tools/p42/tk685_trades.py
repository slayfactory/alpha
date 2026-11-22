#!/usr/bin/env python3
"""П-42 §13.76 (TK-685): загрузка готовых сделок 4 наборов (L_A, S_A, L_W, S_W) — склейка окон wa / wb / wc по зачётным диапазонам (как `tk604_stats.splice`),
сверка G-ref (число сделок, Σ gross − fees) с числами летописи, группы исхода, половины. Движок не запускается (В-213).
Колонки: sym, win, level, sig_ms / entry_ms / exit_ms (мс от T0 окна), entry_px_e8, kind, takes, gross, fees, funding, net (= gross − fees),
t_in / t_out / sig_abs (мс эпохи), day_in / day_out (сутки от 01.07, выход — не позже 135), half (1 / 2), group (0 убыток / 1 около нуля / 2 прибыль), group_gross (прежнее правило, описание)."""
from __future__ import annotations

import datetime
import os

import numpy as np
import pandas as pd

import tk604_stats as st
import tk685_lib as lib

# имя набора: (сторона, шаблон каталога по окну, сделок, Σ $ по летописи)
SETS = {
    "L_A": ("L", "/data/p42-tk643/cells/{w}-L/A_s3_none", 428, 255.094208),
    "S_A": ("S", "/data/p42-tk643/cells/{w}-S/A_s4_none", 319, 170.409629),
    "L_W": ("L", "/data/p42-tk674/cells/{w}-L/pbc94ad4420/n_bounce1+s3", 3875, -16.520291),
    "S_W": ("S", "/data/p42-tk674/cells/{w}-S/p2d3b54abb3/n_bounce1+s4", 2912, 1103.462210),
}
SUM_TOL = 0.006
ENG = {"L": "long", "S": "short"}
COLS = ["sym", "level", "sig_ms", "entry_ms", "exit_ms", "entry_px_e8", "kind", "takes", "gross", "fees", "funding"]


def read_window(path, side, w):
    """Сделки окна `w` (st.Window) из `<side>-trades.csv` в зачётном диапазоне по дате входа."""
    df = pd.read_csv(os.path.join(path, ENG[side] + "-trades.csv"))
    lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
    d = st.abs_day(w, df["entry_ms"].to_numpy(np.int64))
    sel = (d >= lo_i) & (d <= hi_i)
    out = df.loc[sel, COLS].copy()
    out["win"] = w.name
    out["t0_ms"] = w.t0_ms
    out["day_in"] = d[sel]
    return out


def build(frames):
    """Склейка окон → таблица набора с производными колонками, порядок (t_in, sym)."""
    df = pd.concat(frames, ignore_index=True)
    df["net"] = df["gross"] - df["fees"]
    df["sig_abs"] = df["sig_ms"] + df["t0_ms"]
    df["t_in"] = df["entry_ms"] + df["t0_ms"]
    df["t_out"] = df["exit_ms"] + df["t0_ms"]
    d0_ms = int(datetime.datetime.combine(st.D0, datetime.time(), datetime.timezone.utc).timestamp() * 1000)
    df["day_out"] = np.minimum((df["t_out"] - d0_ms) // st.DAY_MS, st.NDAYS - 1).astype(np.int64)
    df["day_in"] = df["day_in"].astype(np.int64)
    df["half"] = lib.halves(df["day_in"].to_numpy())
    df["group"] = lib.groups(df["net"].to_numpy(), df["fees"].to_numpy())
    df["group_gross"] = lib.groups_gross(df["gross"].to_numpy(), df["fees"].to_numpy())
    st.check_abs_ms(df["sig_abs"].to_numpy(), "время сигнала сделки")
    return df.sort_values(["t_in", "sym"], kind="stable").reset_index(drop=True)


def load_set(name, sets=None, windows=None):
    """Набор → таблица; G-ref: n и Σ равны летописи (допуск 0,006 $), иначе AssertionError."""
    side, tmpl, n_ref, s_ref = (sets or SETS)[name]
    frames = [read_window(tmpl.format(w=w.name), side, w) for w in (windows or st.TRADE_WINDOWS)]
    df = build(frames)
    n, s = len(df), float(df["net"].sum())
    assert n == n_ref and abs(s - s_ref) <= SUM_TOL, ("G-ref", name, n, n_ref, s, s_ref)
    df.insert(0, "side", side)
    return df


def group_sizes(df):
    """Размеры групп по правилу net и по прежнему правилу gross, по половинам: {(правило, половина): [убыток, ноль, прибыль]}."""
    out = {}
    for rule, col in (("net", "group"), ("gross", "group_gross")):
        for h in (1, 2, 0):
            sub = df if h == 0 else df[df["half"] == h]
            out[(rule, h)] = [int((sub[col] == g).sum()) for g in (lib.LOSS, lib.ZERO, lib.PROFIT)]
    return out
