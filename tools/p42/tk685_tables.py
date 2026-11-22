#!/usr/bin/env python3
"""П-42 §13.76 (TK-685): таблицы описания по группам исхода — путь сделки (группа × MFE / MAE / зелёные / отдача / удержание) и метрики набора по половинам.
Средние и доли, медиан нет. Путь считается только у сделок с лентой (`tape_ok`, исключённые печатаются отдельно)."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk685_lib as lib  # noqa: E402
import tk685_run as R  # noqa: E402


def path_frame(name):
    return pd.read_csv(R.p("path", name + ".csv"))


def path_table(pa, half=None):
    """Группа × (сделок с путём, доля зелёных, доля «в плюсе до минуса», средние MFE / MAE в $ и % маржи, время до MFE и MAE, отдача, доля отдачи, удержание), строки — группы."""
    sub = pa[pa["tape_ok"]] if half is None else pa[pa["tape_ok"] & (pa["half"] == half)]
    rows = []
    for g in (lib.LOSS, lib.ZERO, lib.PROFIT):
        s = sub[sub["group"] == g]
        gr = s[s["green"]]
        rows.append({"группа": lib.GROUP_NAMES[g], "n": int(len(s)), "зелёных, %": 100 * float(s["green"].mean()) if len(s) else np.nan,
                     "в плюсе до минуса, %": 100 * float(s["green_first"].mean()) if len(s) else np.nan,
                     "MFE $": float(s["mfe"].mean()) if len(s) else np.nan, "MFE % маржи": float(s["mfe"].mean()) / lib.MARGIN * 100 if len(s) else np.nan,
                     "MAE $": float(s["mae"].mean()) if len(s) else np.nan, "MAE % маржи": float(s["mae"].mean()) / lib.MARGIN * 100 if len(s) else np.nan,
                     "до MFE, мин": float(s["t_mfe"].mean()) if len(s) else np.nan, "до MAE, мин": float(s["t_mae"].mean()) if len(s) else np.nan,
                     "отдача $": float(s["giveback"].mean()) if len(s) else np.nan, "отдача, доля MFE (зелёные)": float(gr["giveback_share"].mean()) if len(gr) else np.nan,
                     "удержание, мин": float(s["hold_min"].mean()) if len(s) else np.nan})
    return pd.DataFrame(rows)


def fmt(df, digits=2):
    return df.to_string(index=False, float_format=lambda x: ("%." + str(digits) + "f") % x)
