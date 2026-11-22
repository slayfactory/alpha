#!/usr/bin/env python3
"""П-42 TK-674: сверка столбцов FAST стороны S (sac10, sac60, accdv1_300) таблицы фильтров шага 3 с `fast.csv` TK-681 — по ключу (монета, sig_ms), независимо от row_id,
которым ходит `tk680_singles.fast_join`; плюс число отброшенных строк на порогах `thr680t.json` против `filter_[cands,dropped,no_key,no_value]` клеток допгейта TK-683.
Запуск на srv в среде шага 3 (TK674_X680 …): `python3 tk674_fast_check.py`. Без движка и без P&L."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk654_run as r54  # noqa: E402
import tk674_run as run  # noqa: E402
import tk674_x680 as x6  # noqa: E402
import tk680_axes as a80  # noqa: E402

COLS = ("sac10", "sac60", "accdv1_300")
CHECK = (("sac10", "q20"), ("accdv1_300", "q80"))                     # клетки допгейта: sac10.le@t_q20 [105, 54, 0, 0], accdv1_300.le@t_q80 [105, 40, 0, 0] (wa)


def main():
    st, r5, _, _ = run.t._srv()
    tabs = run.feat_tabs("S", st, r5.Uni(run.UNI), r54.episodes_h775())
    thr = x6.thresholds("S")                                           # пороги осей столбцов — как в плане шага 3
    for w in st.TRADE_WINDOWS:
        tab = tabs[w.name]
        fc = pd.read_csv(a80.T681_FASTDIR % (w.name, "short") + "/fast.csv")
        key = {}
        for i, (s, m) in enumerate(zip(fc["sym"], fc["sig_ms"])):
            key.setdefault((str(s), int(m)), i)
        rows = [key.get((str(s), int(m))) for s, m in zip(tab["sym"], tab["ms"])]
        miss = sum(r is None for r in rows)
        out = ["%s: строк %d, нет в fast.csv %d" % (w.name, len(rows), miss)]
        for c in COLS:
            ref = np.array([fc[c].iat[r] if r is not None else np.nan for r in rows], dtype=np.float64)
            mine = np.asarray(tab[c], dtype=np.float64)
            out.append("%s %s: равно %s (пустых %d)" % (c, "строк %d" % len(ref), "да" if np.array_equal(mine, ref, equal_nan=True) else "НЕТ", int(np.isnan(mine).sum())))
        for c, q in CHECK:
            th = thr["%s.le@t" % c][q][2]
            v = np.asarray(tab[c], dtype=np.float64)
            out.append("%s.le@t_%s порог %g: отброшено %d из %d (пустых %d)" % (c, q, th, int((v > th).sum()), len(v), int(np.isnan(v).sum())))
        print(" | ".join(out))


if __name__ == "__main__":
    main()
