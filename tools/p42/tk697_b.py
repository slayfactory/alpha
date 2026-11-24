#!/usr/bin/env python3
"""П-42 TK-697 (вторая поправка к PS-960, набор `t697b`): два столбца рынка для ВСЕХ сигналов базы — `btc_mv15`, `mk_btc_s60`. Ось выбрана после просмотра P&L TK-698 на этих данных.

  btc_mv15   = s · (cf[t − 1] / cf[t − 16] − 1) · 1e4, бп — f12 `btc_mv15` («ход BTC за 15 минут перед сигналом, к пробою»); = 1e4 · `mk_btc_s15` семейства mkt TK-671;
  mk_btc_s60 = s · (cf[t − 1] / cf[t − 61] − 1), доля — столбец `mk_btc_s60` таблицы TK-671 (семейство mkt);
  `t = sig_ms // 60000` — минута сигнальной сделки, `cf` — закрытия BTCUSDT по минуткам кэша (`ffill_close`, как `tk668_feat`), `s` = +1 лонг, −1 шорт; > 0 — BTC шёл в сторону сделки, < 0 — против.
  Читаются только закрытые минуты ≤ t − 1. Пусто (NaN) — нет кэша BTC, `t − 1 − h < 0`, любое из двух закрытий не определено. Пустое значение фильтр пропускает (PS-993 (д)), доля пустых печатается.

Строки — все сигналы базы h775 окна и стороны (`tk697_ext.ext_rows(..., which="all")`: таблица TK-671 плюс сигналы без её строки), значения считает `tk671_mkt.compute` (проверен воротами TK-671: G-brute, G-causal, G-file);
значения зависят только от монеты и минуты сигнала, поэтому файл ключуется по (`sym`, `sig_ms`). TradFi вне c539: столбцы BTC определены (нужен только ряд BTCUSDT), в зачётный состав c539 они не входят.

  python3 tk697_b.py --win wb --side long [--out /data/p42-tk697/fam/b697/wb-long.parquet] [--procs 2] [--gate-dir <каталог для rows.parquet / got.parquet ворот mkt>]

Результат: `<out>` (индекс row_id < 0; sym, sig_ms, btc_mv15, mk_btc_s60), `<out>.info.json` (строки, доли определённых, G-eq с таблицей TK-671: `btc_mv15` f12 и `mk_btc_s60` на строках, которые в ней есть),
при `--gate-dir` — `rows-<окно>-<сторона>.parquet` (вход `tk671_mkt.compute`) для ворот `tk671_gate_mkt.py --rows … --win … --side …` (G-brute, G-causal, G-file; ворота пересчитывают столбцы сами)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FEAT34 = "/data/p42-tk634/feat"
PROD = "/data/p42-tk671/prod"
DATA671 = "/data/p42-tk671/data"
SIDE_L = {"long": "L", "short": "S"}
NAMES = ["btc_mv15", "mk_btc_s60"]
BP = 1e4


def compute(win, side, procs=2):
    """(res, rows, got, info): res — sym, sig_ms, btc_mv15, mk_btc_s60 (индекс row_id < 0); rows / got — вход и выход `tk671_mkt.compute` (27 столбцов)."""
    import pandas as pd
    import tk671_mkt as mkt
    import tk697_ext as ext
    sl = SIDE_L[side]
    eps = pd.read_parquet(os.path.join(FEAT34, "h775", "%s-%s" % (win, sl), "eps-%s.parquet" % sl), columns=["sym", "sig_ms", "level", "lp_e8"])
    tab = pd.read_parquet(os.path.join(PROD, "lvl-%s-%s" % (win, side), "rows.parquet"), columns=["row_id", "src", "sym", "level", "sig_ms", "level_px_e8", "abs_ms", "base_pass"])
    rows = ext.ext_rows(eps, tab, win, side, "all")
    t0 = time.time()
    got = mkt.compute(rows, win, side, procs=procs)
    res = rows[["row_id", "sym", "sig_ms"]].set_index("row_id")
    res["btc_mv15"] = got["mk_btc_s15"].to_numpy(np.float64) * BP
    res["mk_btc_s60"] = got["mk_btc_s60"].to_numpy(np.float64)
    info = dict(win=win, side=side, eps=int(len(eps)), rows=int(len(rows)), seconds=round(time.time() - t0, 1),
                non_nan_share={c: round(float(np.isfinite(res[c]).mean()), 4) if len(res) else 0.0 for c in NAMES})
    return res, rows, got, info


def check(res, win, side):
    """G-eq (без P&L): у сигналов, которые есть в таблице TK-671, `btc_mv15` = f12 `btc_mv15` и `mk_btc_s60` = столбец таблицы (float32, как хранит таблица): одинаковый рисунок NaN, |Δ| по столбцу."""
    import pandas as pd
    tab = pd.read_parquet(os.path.join(PROD, "lvl-%s-%s" % (win, side), "rows.parquet"), columns=["row_id", "sym", "sig_ms", "base_pass"])
    tab = tab[tab["base_pass"] > 0]
    f = pd.read_parquet(os.path.join(DATA671, "%s.parquet" % side), columns=["win", "row_id"] + NAMES)
    f = f[f["win"].astype(str) == win].set_index("row_id")
    j = tab.merge(res.reset_index(drop=True), on=["sym", "sig_ms"], how="inner", suffixes=("", "_b"))
    j = j.join(f, on="row_id", rsuffix="_t")
    out = dict(matched=int(len(j)), bad={}, max_abs={}, nan_pattern_bad={})
    for c in NAMES:
        u, v = j[c].to_numpy(np.float64), j[c + "_t"].to_numpy(np.float64)
        pat = np.isnan(u) != np.isnan(v)
        both = ~np.isnan(u) & ~np.isnan(v)
        d = np.abs(u - v)[both]
        tol = (1e-6 if c == "mk_btc_s60" else 1e-5) * (1 + np.abs(v[both]))      # таблица хранит float32 (бп — до ~1e-5 относительной)
        out["nan_pattern_bad"][c] = int(pat.sum())
        out["max_abs"][c] = float(d.max()) if len(d) else 0.0
        out["bad"][c] = int((d > tol).sum())
    out["ok"] = bool(out["matched"] > 0 and not any(out["bad"].values()) and not any(out["nan_pattern_bad"].values()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out")
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--gate-dir")
    a = ap.parse_args()
    out = a.out or "/data/p42-tk697/fam/b697/%s-%s.parquet" % (a.win, a.side)
    res, rows, got, info = compute(a.win, a.side, a.procs)
    info["g_eq"] = check(res, a.win, a.side)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    res.to_parquet(out)
    with open(out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    if a.gate_dir:
        os.makedirs(a.gate_dir, exist_ok=True)
        rows.to_parquet(os.path.join(a.gate_dir, "rows-%s-%s.parquet" % (a.win, a.side)))
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
