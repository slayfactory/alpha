#!/usr/bin/env python3
"""TK-700: Python-семейства признаков на строках БАЗЫ окна (m01…m06 — отложенная выборка; wa / wb / wc — ворота) — обёртка над tk671_coin / tk671_bd с подстановкой окон H1.

  python3 tk700_fam.py coin --rows <rows.parquet> --win m01 --side long --out <fam/coin/m01-long.parquet> [--procs 2] [--labels-dir <каталог кэша меток>] [--universe <файл монет>]
  python3 tk700_fam.py bd   --rows <rows.parquet> --win m01 --side long --out <fam/bd/m01-long.parquet> [--procs 2] [--zip /data/p42-tk700/dl/book/zip]

coin — cb / ce / bt / cc × 15m, 1h, 4h, 24h, 7d (корреляции и бета к BTC / ETH / кластеру; кластеры месяца — по 14 суткам до 1-го числа, `REFRESH_WINDOW` месяц → окно m0N), age_d, cls, turn24h, natr_bp.
  События PEER (peer5 / 15 / 60, peerall15) не строятся (нужны события пробоев всех эпизодов окна; сводный ранг PEER осью не берётся — `COL_SKIP` tk680_axes): столбцы пустые.
  Вселенная кластеров — `--universe` (H1: c539 `symbols-crypto.txt`, без TradFi; ворота wa — прежние 775, чтобы сверить код; кластерные столбцы cc_* на H1 считаются без TradFi по построению).
bd — глубина книги `bookDepth`: сутки архива вне 14.09–13.11 для H1 разрешены (`OMEGA` = 01.12.2025–30.06.2026, дыр нет — нет файла = пусто); остальное — как tk671_bd (снимок строго до сигнала).
Метки и P&L не читаются."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

H1_WINS = ("m01", "m02", "m03", "m04", "m05", "m06")
H1_UNIVERSE = "/data/p42-tk621/universe/symbols-crypto.txt"
H1_LABELS = "/data/p42-tk700/fam/coin/clusters"
H1_ZIP = "/data/p42-tk700/dl/book/zip"
_EPOCH = dt.date(1970, 1, 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fam", choices=("coin", "bd"))
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True)
    ap.add_argument("--side", required=True, choices=("long", "short"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--labels-dir", default=None)
    ap.add_argument("--universe", default=None)
    ap.add_argument("--zip", default=None)
    a = ap.parse_args(argv)
    h1 = a.win in H1_WINS
    if h1:
        os.environ["TK700_WINSET"] = "h1"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import numpy as np
    import pandas as pd
    rows = pd.read_parquet(a.rows)
    info = {}
    if a.fam == "coin":
        import tk636_feat as f6
        import tk671_coin as c
        if h1:
            f6.REFRESH_WINDOW.update({i + 1: n for i, n in enumerate(H1_WINS)})
        coins = c.universe_coins(a.universe or (H1_UNIVERSE if h1 else c.SYMS_FILE))
        labels_dir = a.labels_dir or (H1_LABELS if h1 else c.LABELS_DIR)
        os.makedirs(labels_dir, exist_ok=True)
        ev = (np.array([], dtype=object), np.array([], dtype=np.int64)) if h1 else None
        df = c.compute(rows, a.win, a.side, a.procs, labels_dir=labels_dir, coins=coins, info=info, events=ev)
        if h1:
            df[[x for x in df.columns if x.startswith("peer")]] = np.nan
    else:
        import tk671_bd as bd
        if h1:
            day = lambda s: (dt.date.fromisoformat(s) - _EPOCH).days   # noqa: E731
            bd.OMEGA_LO_D, bd.OMEGA_HI_D, bd.HOLES_D, bd.LAST_OK_D = day("2025-12-01"), day("2026-06-30"), (), day("2026-06-30")
        df = bd.compute(rows, a.win, a.side, a.procs, zdir=a.zip or (H1_ZIP if h1 else bd.ZIP), stats=info)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    df.to_parquet(a.out)
    with open(a.out + ".info.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, ensure_ascii=False, indent=1, default=str)
    print(a.fam, a.win, a.side, "rows", len(df), "cols", df.shape[1], "nonnan", {k: round(float(df[k].notna().mean()), 3) for k in list(df.columns)[:6]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
