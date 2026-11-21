#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): входы сборщиков признаков из `rows.parquet` (шаг lvl).

  python3 tk671_prep.py --rows <rows.parquet> --out <каталог> [--coin-mod N --coin-rem R]
Пишет: `sigsx.csv` (формат дампа движка для `alpha-feat f12`, одна строка — один эпизод: sym,level,sig_ms,dist_e8,level_px_e8,born,end,first_bar,natr_e8,members),
`eps-ts.csv` (sym,sig_ms — tcount / tsize / tox), `eps-key.csv` (row_id,sym,sig_ms,level_px_e8), `rows-slice.parquet` (строки среза целиком, `row_id` — из `rows.parquet`). `--coin-mod/--coin-rem` — срез монет для замеров (каждая N-я монета по порядку имён)."""
import argparse
import os

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--coin-mod", type=int, default=1)
    ap.add_argument("--coin-rem", type=int, default=0)
    a = ap.parse_args()
    r = pd.read_parquet(a.rows)
    if a.coin_mod > 1:
        coins = sorted(r["sym"].unique())
        keep = set(coins[a.coin_rem::a.coin_mod])
        r = r[r["sym"].isin(keep)]
    r = r.sort_values(["sym", "sig_ms", "level"], kind="stable").reset_index(drop=True)
    os.makedirs(a.out, exist_ok=True)
    # D / W / P (`lx`): у уровня источника журнала членов нет — уровень = одна цена, сформированная на баре `first_bar` (n_piv = 1, разброс 0): член `first_bar:level_px_e8`
    empty = r["members"].fillna("") == ""
    r["members"] = r["members"].where(~empty, r["first_bar"].astype("int64").astype(str) + ":" + r["level_px_e8"].astype("int64").astype(str))
    r[["sym", "level", "sig_ms", "dist_e8", "level_px_e8", "born", "end", "first_bar", "natr_e8", "members"]].to_csv(os.path.join(a.out, "sigsx.csv"), index=False)
    r[["sym", "sig_ms"]].to_csv(os.path.join(a.out, "eps-ts.csv"), index=False)
    r[["row_id", "sym", "sig_ms", "level_px_e8"]].to_csv(os.path.join(a.out, "eps-key.csv"), index=False)
    r.to_parquet(os.path.join(a.out, "rows-slice.parquet"), index=False)
    print(len(r), r["sym"].nunique())


if __name__ == "__main__":
    main()
