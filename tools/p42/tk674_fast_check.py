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
    tot, tot_all = {}, 0
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
        sc = r54.in_scored_range(w, tab["ms"])                          # покрытие — как в `tk680_singles.cmd_select`: по строкам зачётного диапазона
        out.append("зачётный диапазон: %d строк; пустых " % int(sc.sum()) + ", ".join("%s %d" % (c, int(np.isnan(np.asarray(tab[c], dtype=np.float64)[sc]).sum())) for c in COLS))
        cov_n = {c: int(np.isfinite(np.asarray(tab[c], dtype=np.float64)[sc]).sum()) for c in COLS}
        tot = {c: tot.get(c, 0) + cov_n[c] for c in COLS}
        tot_all += int(sc.sum())
        print(" | ".join(out))
    print("покрытие в зачётном диапазоне (3 окна): " + ", ".join("%s %d / %d = %.1f %%" % (c, tot[c], tot_all, 100.0 * tot[c] / tot_all) for c in COLS) + "  (TK-680, thr680t.json h775: sac10 382 / 494 = 77,3 %, sac60 490 / 494, accdv1_300 491 / 494)")


if __name__ == "__main__":
    main()
