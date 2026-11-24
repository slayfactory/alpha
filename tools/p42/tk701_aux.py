#!/usr/bin/env python3
"""TK-701 (П-42, описательно): вспомогательные признаки сделок линии — рынок после входа, тренд BTC, оборот, производные биржи, стакан. Движок не запускается, сутки < 01.07 и ≥ 14.11 не читаются (NaN, не подстановка).
Столбцы (знак s = +1 лонг, −1 шорт; «+» = по ходу сделки):
  aft_btc15 / aft_btc60 / aft_eth15 / aft_eth60 — s · ход BTC / ETH от последней закрытой минуты до входа на +15 / +60 мин (`mkt.npz` TK-698; исход, не ось);
  btc_trend7 — ход BTC за 7 полных суток до суток сигнала (произведение суточных ходов `d_btc`, сутки 0…6 — NaN); turn24 — Σ qv минуток за 1 440 минут до сигнала (`.b42`; единицы не важны, нужен ранг), NaN, если окно начинается до 01.07;
  производные `tk594_feat.deriv_features` (oi_c1 / c4 / c24, ls_tc / ls_ts / ls_a, dls_*, fund_last, fund_chg24, fund_int, basis): строка metrics строго до сигнала (T* = ⌊(sig − 10 мин)/5 мин⌋), метрики — с 18.07;
  стакан `tk671_bd.compute` (bd_imb02 / 1 / 2 уже со знаком «позади − впереди», bd_thin5 / 30, bd_depth_turn): сутки 14.09–13.11 без 08.10.
Запуск на srv: python3 tk701_aux.py <линия> <окно> [<выход>]   — `<выход>/<линия>-<окно>.parquet`; PYTHONPATH — каталог кода (tools/p42)."""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk594_feat as F594  # noqa: E402
import tk701_tape as T  # noqa: E402

CACHE = "/data/p42-tk621/%s/cache/%s.b42"
MKT = "/data/p42-tk698/mkt/mkt.npz"
OUT = "/data/p42-tk701/aux"
FIRST_MIN = T.FIRST_MS // 60000
NMIN = 136 * 1440
MINDAY0 = T.FIRST_MS // T.DAY_MS


def t0_ms(win):
    import datetime as dt
    d0 = {"wa": "2026-06-17", "wb": "2026-07-25", "wc": "2026-09-01"}[win]
    return int(dt.datetime.fromisoformat(d0).replace(tzinfo=T.UTC).timestamp() * 1000)


def qv_series(path):
    with open(path, "rb") as f:
        head = f.read(8)
    n = int.from_bytes(head[4:8], "little")
    return np.memmap(path, dtype="<i8", mode="r", offset=8, shape=(5, n))[4], n


def run(line, win, out_dir, limit=0):
    t_start = time.time()
    tr = T.load_trades(line, win)
    if limit:
        tr = tr.iloc[:limit].copy()
    n = len(tr)
    s = tr["s"].to_numpy(np.int64)
    sig = tr["sig_ms"].to_numpy(np.int64)
    ent = tr["entry_ms"].to_numpy(np.int64)
    day = tr["day"].to_numpy(np.int64)
    out = pd.DataFrame(dict(sym=tr["sym"].to_numpy(), entry_ms=ent))
    mk = np.load(MKT)
    b0 = sig // 60000 - 1
    for nm, key, h in (("aft_btc15", "btc15", 15), ("aft_btc60", "btc60", 60), ("aft_eth15", "eth15", 15), ("aft_eth60", "eth60", 60)):
        i = b0 + h - FIRST_MIN
        ok = (i >= 0) & (i < NMIN)
        v = np.full(n, np.nan)
        v[ok] = mk[key][i[ok]].astype(np.float64)
        out[nm] = s * v
    d_btc = mk["d_btc"].astype(np.float64)
    tr7 = np.full(n, np.nan)
    for k in range(n):
        d = int(day[k])
        if d >= 7:
            w = d_btc[d - 7:d]
            if not np.isnan(w).any():
                tr7[k] = float(np.prod(1.0 + w) - 1.0)
    out["btc_trend7"] = tr7
    # оборот за сутки
    turn = np.full(n, np.nan)
    t0m = t0_ms(win) // 60000
    for sym, g in tr.groupby("sym").groups.items():
        p = CACHE % (win, sym)
        if not os.path.exists(p):
            continue
        qv, nn = qv_series(p)
        for k in g:
            m = int(sig[k] // 60000) - t0m
            if m - 1440 >= FIRST_MIN - t0m and m <= nn:
                turn[k] = float(np.sum(qv[m - 1440:m])) / 1e5
    out["turn24"] = turn
    # производные биржи
    cols = F594.DERIV
    dv = {c: np.full(n, np.nan) for c in cols}
    cov = dict(metrics_days=0, funding_syms=0)
    for sym, g in tr.groupby("sym").groups.items():
        g = np.array(sorted(g))
        days = sig[g] // T.DAY_MS
        dmin, dmax = int(days.min()), int(days.max())
        d0 = max(dmin - 2, MINDAY0)
        M, first_row = F594.load_metrics(sym, d0, dmax)
        ft, fr = F594.load_funding(sym)
        keep = ft >= T.FIRST_MS
        ft, fr = ft[keep], fr[keep]
        prem = F594.load_premium(sym, d0, dmax)
        for k in g:
            r = F594.deriv_features(int(sig[k]), turn[k], M, first_row, d0, ft, fr, prem)
            for c in cols:
                dv[c][k] = r[c]
    for c in cols:
        out[c] = dv[c]
    # стакан
    import tk671_bd as BD
    side = "long" if line.startswith("L_") else "short"
    rows = pd.DataFrame(dict(row_id=np.arange(n, dtype=np.int64), sym=tr["sym"].astype(str).to_numpy(), sig_ms=(sig - t0_ms(win))))
    bd = BD.compute(rows, win, side, procs=1)
    for c in bd.columns:
        out[c] = bd[c].to_numpy()
    os.makedirs(out_dir, exist_ok=True)
    out.to_parquet(os.path.join(out_dir, "%s-%s%s.parquet" % (line, win, "-sample" if limit else "")), index=False)
    print(line, win, "trades", n, "sec", round(time.time() - t_start, 1), "nonNaN:", {c: round(float(out[c].notna().mean()), 3) for c in ["aft_btc15", "aft_btc60", "btc_trend7", "turn24", "oi_c1", "ls_a", "fund_last", "basis", "bd_imb1", "bd_thin5"]}, flush=True)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else OUT, int(sys.argv[4]) if len(sys.argv) > 4 else 0)
