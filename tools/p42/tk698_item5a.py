#!/usr/bin/env python3
"""TK-698, пункт 5а: анатомия линий по готовым сделкам (движок не запускается) — доля выигрышей, средний выигрыш / проигрыш, R:R, виды выхода с $ на вид, время в сделке, ступени `takes`, $ на сделку по месяцам, путь после входа (MFE / MAE в $ и в NATR по ленте, `tk698_path.py`),
доля исполнения второй части входа (`lot2` из `*-lots.csv`, только клетки с входом двумя частями). Ставка 500 $, P&L сделки = gross − fees. Описательно, найдено на этих данных.
Запуск на srv: python3 tk698_item5a.py <корень /data/p42-tk698> <run_key>"""
import json
import os
import sys

import numpy as np
import pandas as pd

import tk698_db as D
import tk698_lib as L


def row(line, dim, bin_, n, usd, nd=0, share=None, extra=None):
    return ("item5a", line, dim, "own", bin_, int(nd), int(n), float(usd), (float(usd) / n) if n else None, None, share, extra)


def load_path(root, line):
    parts = []
    for w in L.WINDOWS:
        p = os.path.join(root, "path", "%s-%s.parquet" % (line, w))
        if os.path.exists(p):
            parts.append(pd.read_parquet(p))
    return pd.concat(parts, ignore_index=True) if parts else None


def main(root, run_key):
    db = D.connect()
    D.new_run(db, run_key, "item5a", "tools/p42/tk698_item5a.py", "анатомия линий: выходы, время, путь, месяцы; описательно, найдено на этих данных; окно 01.07–13.11, ставка 500 $")
    lines = []
    for f in ("lines.json", "lines-mirror.json"):
        if os.path.exists(os.path.join(root, f)):
            lines += json.load(open(os.path.join(root, f), encoding="utf-8"))
    split, linerows = [], []
    for ln in lines:
        lid = ln["line"]
        tr = pd.read_parquet(os.path.join(root, "trades", lid + ".parquet"))
        v, kind = tr["val"].to_numpy(), tr["kind"].to_numpy()
        hold = (tr["exit_ms"].to_numpy() - tr["entry_ms"].to_numpy()) / 60000.0
        N = len(tr)
        win, loss = v > 0, v < 0
        extra = dict(win_share=float(win.mean()), loss_share=float(loss.mean()), mean_win=float(v[win].mean()) if win.any() else None, mean_loss=float(v[loss].mean()) if loss.any() else None,
                     hold_mean=float(hold.mean()), kinds=sorted(set(kind.tolist())), lots=bool("lot_lot2" in tr.columns))
        if "lot_lot2" in tr.columns:
            extra["fill_b"] = float(tr["lot_lot2"].mean())
        if "lot_kind2" in tr.columns:
            extra["lot_kinds"] = {str(k): int(c) for k, c in tr["lot_kind2"].value_counts().items()}
        linerows.append((lid, ln["cell"], ln["side"], ln["n"], ln["usd"], ln["usd"] / ln["n"], ln["sharpe"], ln["dd"], ln["dd_pct"], ln["gap_longest"], ln["gap_mean"], ln["gap_tail"], ln["rr"], extra))
        pth = load_path(root, lid)
        if pth is not None:
            tr = tr.merge(pth[["sym", "entry_ms", "tape_ok", "mfe", "mae", "t_mfe", "t_mae", "green", "green_first", "giveback", "natr", "mfe_natr", "mae_natr"]], on=["sym", "entry_ms"], how="left", validate="one_to_one")
        for k in sorted(set(kind.tolist())):
            m = kind == k
            split.append(row(lid, "kind", k, m.sum(), v[m].sum(), share=float(m.mean()), extra=dict(hold_mean=float(hold[m].mean()), mean_win=float(v[m & win].mean()) if (m & win).any() else None, mean_loss=float(v[m & loss].mean()) if (m & loss).any() else None)))
        tk = tr["takes"].to_numpy()
        for nm, m in (("0", tk == 0), ("1-3", (tk >= 1) & (tk <= 3)), ("4-7", (tk >= 4) & (tk <= 7)), ("8+", tk >= 8)):
            split.append(row(lid, "takes", nm, m.sum(), v[m].sum(), share=float(m.mean())))
        for nm, m in (("win", win), ("loss", loss), ("zero", ~win & ~loss)):
            ex = dict(hold_mean=float(hold[m].mean()) if m.any() else None, stop_share=float((kind[m] == "stop").mean()) if m.any() else None)
            if pth is not None:
                q = tr[m & tr["tape_ok"].fillna(False).to_numpy(bool)]
                ex.update(path_n=int(len(q)), mfe=float(q["mfe"].mean()) if len(q) else None, mae=float(q["mae"].mean()) if len(q) else None, mfe_natr=float(np.nanmean(q["mfe_natr"])) if len(q) else None,
                          mae_natr=float(np.nanmean(q["mae_natr"])) if len(q) else None, green_share=float(q["green"].mean()) if len(q) else None, t_mfe=float(q["t_mfe"].mean()) if len(q) else None, t_mae=float(q["t_mae"].mean()) if len(q) else None,
                          giveback=float(q["giveback"].mean()) if len(q) else None)
            split.append(row(lid, "wl", nm, m.sum(), v[m].sum(), share=float(m.mean()), extra=ex))
        if pth is not None:
            q = tr[tr["tape_ok"].fillna(False).to_numpy(bool)]
            split.append(row(lid, "path_all", "all", len(q), q["val"].sum(), share=float(len(q) / N), extra=dict(natr_mean=float(np.nanmean(q["natr"])), mfe=float(q["mfe"].mean()), mae=float(q["mae"].mean()), mfe_natr=float(np.nanmean(q["mfe_natr"])), mae_natr=float(np.nanmean(q["mae_natr"])),
                                                                         green_first=float(q["green_first"].mean()), tape_ok_share=float(tr["tape_ok"].fillna(False).mean()))))
        day = tr["day"].to_numpy()
        for mi, mn in enumerate(L.MONTHS):
            m = L.MONTH_OF_DAY[day] == mi
            split.append(row(lid, "month", mn, m.sum(), v[m].sum(), nd=int((L.MONTH_OF_DAY == mi).sum()), share=float(win[m].mean()) if m.any() else None, extra=dict(stop_share=float((kind[m] == "stop").mean()) if m.any() else None)))
    D.put(db, "res_line", run_key, linerows, ["line", "cell", "side", "n", "usd", "per_trade", "sharpe", "dd", "dd_pct", "gap_longest", "gap_mean", "gap_tail", "rr", "extra"])
    D.put(db, "res_split", run_key, split, ["item", "line", "dim", "market", "bin", "n_days", "n", "usd", "per_trade", "per_day", "share", "extra"])
    print("записано: линий %d, срезов %d" % (len(linerows), len(split)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
