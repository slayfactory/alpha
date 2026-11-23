#!/usr/bin/env python3
"""П-42 TK-701 (единички, набор `t701`; поправка к PS-960): три столбца для ВСЕХ сигналов базы h775 окна и стороны — `tfv60`, `pre_n5`, `oi_c1`.
Ось выбрана после просмотра P&L TK-701 на этих данных (период 01.07–13.11 2026 тот же, что у TK-701): отбор по просмотренному результату, всё прочитанное по набору — «найдено на этих данных».

Определения (s = +1 лонг, −1 шорт; «+» = по ходу сделки; sig — минута и мс сигнальной сделки; только тики с t < sig_ms, G-causal):
  tfv60  = s · (2 · V_покупок_тейкера − V) / V по обороту цена·кол-во за 60 с строго перед sig (TK-594 `flow_features`, тот же столбец, что `tfv60` таблицы TK-671); NaN — нет оборота;
  pre_n5 = s · (P_посл / P_5с − 1) / NATR, P_посл — цена последней сделки строго раньше sig, P_5с — цена последней сделки не позже sig − 5 с (в пределах 35 мин назад), NATR — 30-минутный
           средний TR / close закрытой минуты перед минутой сигнала (`tk698_path.natr_at`, то же определение, что у TK-701 / TK-698); NaN — нет сделок / NATR ≤ 0 / нет кэша минуток;
           это знаковый ход к цене СИГНАЛА (у TK-701 столбец `pre_n5` считался к цене входа — другая величина, пороги новые);
  oi_c1  = (OI(T*) / OI(T* − 12) − 1) · 1e4 (TK-594 `deriv_features`: строка metrics строго до сигнала, T* = ⌊(sig − 10 мин) / 5 мин⌋; метрики — с 18.07, раньше NaN) — знак стороны не применяется
           (рост открытого интереса за час); окно 60 мин.
Охват: лента 01.07–13.11 (сутки < 01.07 и ≥ 14.11 не открываются, В-170); первые 35 мин суток 01.07 и сутки без файла ленты — NaN (пустое значение фильтр пропускает, PS-993 (д)).
Строки — все сигналы базы окна и стороны (таблица TK-671 плюс сигналы без её строки; `ext_rows`, как `tk697_ext`), значения зависят от монеты и минуты сигнала; монеты вне c539 (TradFi) — NaN.

  python3 tk701_sig.py --win wb --side long [--out /data/p42-tk701s/fam/c701/wb-long.parquet]
Результат: `<out>` (индекс row_id < 0; sym, sig_ms, tfv60, pre_n5, oi_c1), `<out>.info.json` (строки, доли определённых, G-eq с таблицей TK-671 для `tfv60` и `oi_c1`)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk594_feat as F594  # noqa: E402
import tk701_tape as T  # noqa: E402

FEAT34 = "/data/p42-tk634/feat"
PROD = "/data/p42-tk671/prod"
DATA671 = "/data/p42-tk671/data"
CACHE = "/data/p42-tk621/%s/cache/%s.b42"
COINS_FILE = "/data/p42-tk621/universe/symbols-crypto.txt"
OUT = "/data/p42-tk701s/fam/c701/%s-%s.parquet"
SIDE_L = {"long": "L", "short": "S"}
NAMES = ["tfv60", "pre_n5", "oi_c1"]
BACK_MS = 5000


def ext_rows(eps, tab, win, side):
    """Все сигналы базы окна и стороны: sym, sig_ms (мс от T0 окна), abs_ms, row_id (< 0). Правило `tk697_ext.ext_rows(..., which="all")` (одно и то же, копия без зависимости от ветки TK-697)."""
    import pandas as pd
    off = np.unique((tab["abs_ms"] - tab["sig_ms"]).to_numpy(np.int64))
    if len(off) != 1:
        raise SystemExit("abs_ms − sig_ms не постоянна в таблице TK-671: %s" % off[:5])
    tb = tab[tab["base_pass"] > 0]
    key = tb[["sym", "sig_ms"]].drop_duplicates()
    m = eps[["sym", "sig_ms"]].merge(key, on=["sym", "sig_ms"], how="left", indicator=True)
    m = m.drop(columns="_merge").drop_duplicates(["sym", "sig_ms"]).sort_values("sig_ms", kind="stable").reset_index(drop=True)
    m["abs_ms"] = m["sig_ms"].astype(np.int64) + int(off[0])
    m["row_id"] = -(np.arange(len(m), dtype=np.int64) + 1)
    return m[["row_id", "sym", "sig_ms", "abs_ms"]]


def sig_vals(Tm, P, V, B, sig, s, natr):
    """(tfv60, pre_n5) по тикам Tm / P / V / B (любая длина; читаются только t < sig — свойство, которое проверяет ворота G-causal); NaN, если нечего считать."""
    nan = float("nan")
    i1 = int(np.searchsorted(Tm, sig, "left")) - 1                    # последняя сделка строго раньше sig
    if i1 < 0 or Tm[i1] < sig - T.LOOK_MS:
        return nan, nan
    f = F594.flow_features(Tm[:i1 + 1], V[:i1 + 1], B[:i1 + 1], sig, s, None, False)
    tfv = float(f["tfv60"])
    i0 = int(np.searchsorted(Tm, sig - BACK_MS, "right")) - 1        # последняя сделка не позже sig − 5 с
    if i0 < 0 or Tm[i0] < sig - T.LOOK_MS or not (natr > 0):
        return tfv, nan
    return tfv, s * (float(P[i1]) / float(P[i0]) - 1.0) / natr


def compute(win, side, procs=1):
    """(res, info). res — sym, sig_ms, tfv60, pre_n5, oi_c1 (индекс row_id < 0)."""
    import pandas as pd
    from tk698_path import natr_at
    sl, s = SIDE_L[side], (1 if side == "long" else -1)
    eps = pd.read_parquet(os.path.join(FEAT34, "h775", "%s-%s" % (win, sl), "eps-%s.parquet" % sl), columns=["sym", "sig_ms"])
    tab = pd.read_parquet(os.path.join(PROD, "lvl-%s-%s" % (win, side), "rows.parquet"), columns=["row_id", "sym", "sig_ms", "abs_ms", "base_pass"])
    rows = ext_rows(eps, tab, win, side)
    coins = set(x.strip() for x in open(COINS_FILE, encoding="utf-8") if x.strip())
    n = len(rows)
    sym = rows["sym"].astype(str).to_numpy()
    sig_rel = rows["sig_ms"].to_numpy(np.int64)
    sig = rows["abs_ms"].to_numpy(np.int64)
    out = {c: np.full(n, np.nan) for c in NAMES}
    t0 = time.time()
    cnt = dict(n_store=0, n_zip=0, n_missing=0)
    for sy in sorted(set(sym) & coins):
        g = np.flatnonzero(sym == sy)
        g = g[np.argsort(sig[g], kind="stable")]
        cp = CACHE % (win, sy)
        natr = natr_at(cp, sig_rel[g] // 60000 - 1) if os.path.exists(cp) else np.full(len(g), np.nan)
        tp = T.StoreTape(win, sy)
        for j, k in enumerate(g):
            sg = int(sig[k])
            a = sg - T.LOOK_MS
            if a < T.FIRST_MS or sg >= T.LAST_MS:
                continue
            Tm, P, V, B, miss = tp.span(a, sg - 1)
            if any(d for d in miss if T.day_of(a) <= d <= T.day_of(sg)) or not len(Tm):
                continue
            out["tfv60"][k], out["pre_n5"][k] = sig_vals(Tm, P, V, B, sg, s, float(natr[j]))
        for c in cnt:
            cnt[c] += getattr(tp, c)
        d0, dmax = T.FIRST_MS // T.DAY_MS, int((sig[g] // T.DAY_MS).max())      # сетка metrics с 01.07 (файлов раньше 18.07 нет): первая строка монеты не зависит от того, какие сигналы есть
        M, first_row = F594.load_metrics(sy, d0, dmax)
        ft, fr = F594.load_funding(sy)
        keep = ft >= T.FIRST_MS
        ft, fr = ft[keep], fr[keep]
        prem = F594.load_premium(sy, d0, dmax)
        for k in g:
            if int(sig[k]) >= T.FIRST_MS:
                out["oi_c1"][k] = F594.deriv_features(int(sig[k]), float("nan"), M, first_row, d0, ft, fr, prem)["oi_c1"]
    res = rows[["row_id", "sym", "sig_ms"]].set_index("row_id")
    for c in NAMES:
        res[c] = out[c]
    info = dict(win=win, side=side, eps=int(len(eps)), rows=int(n), rows_c539=int(np.isin(sym, list(coins)).sum()), seconds=round(time.time() - t0, 1), tape=cnt,
                non_nan_share={c: round(float(np.isfinite(res[c]).mean()), 4) if n else 0.0 for c in NAMES},
                non_nan_share_c539={c: round(float(np.isfinite(res[c].to_numpy()[np.isin(sym, list(coins))]).mean()), 4) if n else 0.0 for c in NAMES})
    return res, info


def check(res, win, side):
    """G-eq (без P&L): у сигналов, которые есть в таблице TK-671, `tfv60` и `oi_c1` совпадают со столбцами таблицы (float32): рисунок NaN и значения; считаются строки таблицы с base_pass > 0 и монетой из c539."""
    import pandas as pd
    coins = set(x.strip() for x in open(COINS_FILE, encoding="utf-8") if x.strip())
    tab = pd.read_parquet(os.path.join(PROD, "lvl-%s-%s" % (win, side), "rows.parquet"), columns=["row_id", "sym", "sig_ms", "base_pass"])
    tab = tab[(tab["base_pass"] > 0) & tab["sym"].isin(coins)]
    f = pd.read_parquet(os.path.join(DATA671, "%s.parquet" % side), columns=["win", "row_id", "tfv60", "oi_c1"])
    f = f[f["win"].astype(str) == win].set_index("row_id")
    j = tab.merge(res.reset_index(drop=True), on=["sym", "sig_ms"], how="inner", suffixes=("", "_b"))
    j = j.join(f, on="row_id", rsuffix="_t")
    out = dict(matched=int(len(j)), bad={}, max_abs={}, nan_pattern_bad={}, nan_pattern_detail={})
    for c in ("tfv60", "oi_c1"):
        u, v = j[c].to_numpy(np.float64), j[c + "_t"].to_numpy(np.float64)
        pat = np.isnan(u) != np.isnan(v)
        both = ~np.isnan(u) & ~np.isnan(v)
        d = np.abs(u - v)[both]
        tol = 1e-5 * (1 + np.abs(v[both]))
        out["nan_pattern_bad"][c] = int(pat.sum())
        out["nan_pattern_detail"][c] = dict(mine_nan_table_val=int((np.isnan(u) & ~np.isnan(v)).sum()), mine_val_table_nan=int((~np.isnan(u) & np.isnan(v)).sum()))
        out["max_abs"][c] = float(d.max()) if len(d) else 0.0
        out["bad"][c] = int((d > tol).sum())
    out["ok"] = bool(out["matched"] > 0 and not any(out["bad"].values()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out")
    a = ap.parse_args()
    out = a.out or OUT % (a.win, a.side)
    res, info = compute(a.win, a.side)
    info["g_eq"] = check(res, a.win, a.side)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    res.to_parquet(out)
    with open(out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
