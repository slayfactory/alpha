#!/usr/bin/env python3
"""П-42 §13.73.8 (TK-680): число успешных пробоев монеты за 3 и 5 суток — `ok10_d3`, `ok10_d5` (своя сторона) и `xok10_d3`, `xok10_d5` (противоположная) на строках таблицы
TK-671. Тот же сборщик и то же определение, что у `ok10_d7 / d14 / d30` (`tk625_feat.History` / `features`, загрузка `tk671_hist.load`): успех — 1 NATR, событие учтено при
`t ≥ t_res + 5 мин`, пересечение в (t − D, t], NaN — окно вне накопленной истории. Регрессия в том же проходе: d7 / d14 / d30, посчитанные этим кодом, равны столбцам таблицы.
  python3 tk680_hist.py <сторона long|short> <выход.parquet>     — srv; строки в порядке таблицы TK-671 (win, row_id — для сверки ключа)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk625_feat as f5  # noqa: E402
import tk671_hist as h71  # noqa: E402
import tk680_axes as a80  # noqa: E402

NEW_DS = (3, 5)
M = "10"                                               # успех — 1 NATR


def compute(hist, side, sym, t, ds):
    """{столбец: массив} — свои `ok10_d<D>` и чужие `xok10_d<D>` по окнам ds."""
    out = {}
    for pref, s in (("", side), ("x", h71.OPP[side])):
        f = h71.features_by_coin(hist[s], sym, t, ds)
        for d in ds:
            out[pref + f5.col_name(M, d)] = f[f5.col_name(M, d)]
    return out


def main(side, out_path):
    old = [p + f5.col_name(M, d) for p in ("", "x") for d in f5.DS]
    df = pd.read_parquet(a80.PARQUET % side, columns=["win", "row_id", "sym", "meta_abs_ms"] + old)
    sym, t = df["sym"].astype(str).to_numpy(dtype=object), df["meta_abs_ms"].to_numpy(dtype=np.int64)
    _, hist = h71.load()
    got = compute(hist, side, sym, t, NEW_DS + f5.DS)
    for c in old:                                      # регрессия: прежние окна не изменились
        a, b = got[c], df[c].to_numpy(np.float64)
        bad = int((~((a == b) | (np.isnan(a) & np.isnan(b)))).sum())
        if bad:
            raise SystemExit("%s %s: %d строк из %d отличаются от таблицы TK-671" % (side, c, bad, len(df)))
    new = [p + f5.col_name(M, d) for p in ("", "x") for d in NEW_DS]
    res = df[["win", "row_id"]].copy()
    for c in new:
        res[c] = got[c]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    res.to_parquet(out_path)
    print("%s: строк %d; d7 / d14 / d30 равны таблице (%d столбцов); определено: %s" % (
        side, len(res), len(old), ", ".join("%s %.4f" % (c, float(np.isfinite(got[c]).mean())) for c in new)), flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in h71.OPP:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
