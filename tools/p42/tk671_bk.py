#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): семейство «пробиваемость монеты» bk (TK-604/654) на строках набора: `bk_n` — число закрытых независимых эпизодов монеты той же стороны до t, `bk_k` — из них с меткой y = 1,
`bk20/bk40/bk60` = k / n при n ≥ K (K = 20/40/60), иначе NaN. Код TK-604 не переписан: `tk604_feat.episode_history` / `features`; эпизод закрыт при `sig + 65 мин ≤ t`.
Эпизоды — честная вселенная h775 (`tk671_bkep.py`, метка y как `tk604_episodes`, сделки — склад; на старых монетах равна таблице TK-604 построчно), окна w0 (17–30.06), wa, wb, wc склеены по зачётным диапазонам.
CLI: python3 tk671_bk.py --rows <rows.parquet> --win wa --side long --out <out.parquet> [--procs N]  (векторно, один процесс)
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
from tk700_win import win_choices  # noqa: E402
import tk604_feat as tf  # noqa: E402
import tk604_stats as st  # noqa: E402

EP_DIR = os.environ.get("TK671_BKEP_DIR", "/data/p42-tk671/fam/bkep")
COLUMNS = ["bk_n", "bk_k"] + ["bk%d" % K for K in tf.KS]
_C: dict = {}


def load(ep_dir=EP_DIR):
    """Эпизоды w0, wa, wb, wc → склейка (кэш процесса)."""
    if ep_dir not in _C:
        per = {w.name: tf.window_rows_to_arrays(tf.read_episodes_csv(os.path.join(ep_dir, w.name + ".csv")), w) for w in st.HIST_WINDOWS}
        _C[ep_dir] = tf.splice_episodes(per)
    return _C[ep_dir]


def compute(rows: pd.DataFrame, win: str, side: str, procs: int = 3, **kw) -> pd.DataFrame:
    """rows: row_id (индекс или столбец), sym, abs_ms. Возвращает DataFrame (индекс row_id) со столбцами COLUMNS; строки не выбрасываются."""
    r = rows.set_index("row_id") if "row_id" in rows.columns else rows
    sym = r["sym"].to_numpy(dtype=object)
    t = r["abs_ms"].to_numpy(dtype=np.int64)
    st.check_abs_ms(t, "время сигнала строк %s %s" % (win, side))
    hist = tf.episode_history(load(**kw), side)
    n, k, bk = tf.features(hist, sym, t)
    out = {"bk_n": n.astype(float), "bk_k": k.astype(float)}
    for K in tf.KS:
        out["bk%d" % K] = bk[K]
    return pd.DataFrame({c: out[c] for c in COLUMNS}, index=r.index)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=win_choices())
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
