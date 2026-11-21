#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): семейство «история пробоев монеты» (TK-625/633) на строках набора. Свои / чужие события: `ok<m>_d<D>` — число минимально успешных пробоев монеты той же стороны,
`xok<m>_d<D>` — той же монеты противоположной стороны (m 0,5/1/2 NATR × D 7/14/30 суток) — 18 столбцов float64, NaN = окно [t − D, t] вне накопленной истории.
Код TK-625 не переписан: таблицы событий h775 (`/data/p42-tk625/ev-h775/{w0,wa,wb,wc}.csv`, вкл. историю 17–30.06 — отдельный дамп w0 не нужен), `tk625_feat.History` / `features` с теми же
правилами (событие учтено при `t ≥ t_res + 5 мин`, дубли по (сторона, монета, пересечение), старт истории 24.06, возраст листинга, пропущенные сутки) — равенство с оригиналом по построению;
ворота — `tk671_gate_hist.py` (таблицы TK-625 `ftab-h775`, перебор, мусор после t). d7 определён с 01.07, d14 с 08.07, d30 с 24.07 (начало данных 17.06, чистые уровни с 24.06) — NaN раньше.
CLI: python3 tk671_hist.py --rows <rows.parquet> --win wa --side long --out <out.parquet> [--procs N]  (векторно, один процесс; --procs принят для единообразия)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_stats as st  # noqa: E402
import tk625_feat as f5  # noqa: E402

EV_DIR = os.environ.get("TK671_EV_DIR", "/data/p42-tk625/ev-h775")
COINDAYS = os.environ.get("TK671_COINDAYS", "/data/p42-tk625/res/coindays-h775.json")
UNIVERSE = os.environ.get("TK671_UNIVERSE", "/data/p42-tk621/universe/symbols-all.txt")
OWN = f5.COLS                                          # ok05_d7 … ok20_d30
FOREIGN = ["x" + c for c in f5.COLS]
COLUMNS = OWN + FOREIGN
OPP = {"long": "short", "short": "long"}
_C: dict = {}


def load(ev_dir=EV_DIR, coindays=COINDAYS, universe=UNIVERSE):
    """Склейка событий окон (w0, wa, wb, wc), пересечения без дублей, история по сторонам, суточные файлы монет — кэш процесса."""
    key = (ev_dir, coindays, universe)
    if key not in _C:
        per = {w.name: f5.read_events_csv(os.path.join(ev_dir, w.name + ".csv"), w.t0_ms) for w in st.HIST_WINDOWS}
        cr = f5.dedup_crossings(f5.splice_events(per))
        raw = json.load(open(coindays, encoding="utf-8"))
        cd = {s: np.array([c == "1" for c in v], dtype=bool) for s, v in raw.items()}
        names = sorted(x for x in open(universe, encoding="utf-8").read().replace("\n", ",").split(",") if x)
        names = sorted(set(names) | set(cr.sym.tolist()) | set(cd))
        hist = {s: f5.History(cr.take(cr.side == s), cd, names) for s in ("long", "short")}
        _C[key] = (cr, hist)
    return _C[key]


def features_by_coin(h, sym, t):
    """`ok_matrix(features(h, sym, t))` по группам одной монеты: те же функции TK-625, но маски `sym == s` считаются на малых массивах (на всём наборе — O(монет × строк))."""
    order = np.argsort(sym, kind="stable")
    ss = sym[order]
    bounds = np.concatenate([[0], np.flatnonzero(ss[1:] != ss[:-1]) + 1, [len(ss)]])
    out = {c: np.full(len(t), np.nan) for c in f5.COLS}
    for a, b in zip(bounds[:-1], bounds[1:]):
        idx = order[a:b]
        for k, v in f5.ok_matrix(f5.features(h, sym[idx], t[idx])).items():
            out[k][idx] = v
    return out


def compute(rows: pd.DataFrame, win: str, side: str, procs: int = 3, **kw) -> pd.DataFrame:
    """rows: row_id (индекс или столбец), sym, abs_ms. Возвращает DataFrame (индекс row_id) со столбцами COLUMNS; строки не выбрасываются."""
    if side not in OPP:
        raise ValueError("side: long|short")
    r = rows.set_index("row_id") if "row_id" in rows.columns else rows
    sym = r["sym"].to_numpy(dtype=object)
    t = r["abs_ms"].to_numpy(dtype=np.int64)
    st.check_abs_ms(t, "время сигнала строк %s %s" % (win, side))
    _, hist = load(**kw)
    out = {}
    for pref, s in (("", side), ("x", OPP[side])):
        for k, v in features_by_coin(hist[s], sym, t).items():
            out[pref + k] = v
    return pd.DataFrame({c: out[c] for c in COLUMNS}, index=r.index)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=("wa", "wb", "wc"))
    ap.add_argument("--side", required=True, choices=("long", "short"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=3)
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows)
    t0 = time.time()
    df = compute(rows, a.win, a.side, a.procs)
    sec = time.time() - t0
    df.to_parquet(a.out)
    info = dict(rows=len(df), seconds=round(sec, 3), ms_per_row=round(1000 * sec / max(len(df), 1), 5), procs=1, nonnan={c: round(float(df[c].notna().mean()), 4) for c in COLUMNS})
    with open(a.out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in info.items() if k != "nonnan"}))


if __name__ == "__main__":
    main()
