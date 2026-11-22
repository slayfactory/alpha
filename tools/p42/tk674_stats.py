#!/usr/bin/env python3
"""П-42 §13.73 (TK-674): строка метрик клетки (В-169) по трём окнам — одна функция для счёта клеток и для импорта готовых клеток в БД летописи.
Только чтение готовых CSV движка; без бутстрепа (интервалы — в отчёте, по суточным рядам из БД). Доллары — ставка 500 $, маржа 250 $, Sharpe — годовой по суточному ряду."""
from __future__ import annotations

import os

import numpy as np

import tk604_stats as st
import tk620_rr as rr
import tk625_stats as s5
import tk638_post as p8
import tk643_run as t
import tk663_shape as sh
import lib

MARGIN = rr.MARGIN
STAKE = 500.0
COST_USD = STAKE * (2 * 3.15 + 2 * 1.0) / 1e4        # издержки сделки, закрытой по стопу: комиссия 3,5 бп × 0,9 за ногу + проскальзывание 1 бп за ногу (§13.73 п. 5 (д))
FOLD = 34                                            # складки: 4 × 34 суток


def lmax(s_pct):
    return STAKE * s_pct / 100.0 + COST_USD


def load(u, side, dirs):
    """(ряд, виды выходов, P&L сделок) клетки; охрана G7 по окнам — `tk643_run.load_cell`."""
    return t.load_cell(st, p8, u, side, dirs)


def metrics(ser, kinds, pnl, s_pct=5.0):
    """Метрики клетки по ряду: n, Σ $, $/сделку, Sharpe, месяцы, складки, просадка, перехай (TK-663), R:R, стопы, Lmax и худшая сделка, суточные ряды."""
    Sd = np.bincount(ser.day, weights=ser.val, minlength=st.NDAYS)
    Nd = np.bincount(ser.day, minlength=st.NDAYS).astype(int)
    n = int(len(pnl))
    cum = np.concatenate([[0.0], np.cumsum(Sd)])
    maxdd = float((np.maximum.accumulate(cum) - cum).max())
    u_ = sh.uw(Sd)
    kp = lib.drawdown_kpi(Sd)
    mon = st.MONTH_OF_DAY[ser.day] if n else np.zeros(0, dtype=np.int64)
    months = [(int((mon == k).sum()), float(ser.val[mon == k].sum())) for k in range(len(st.MONTH_LEN))]
    folds = [(int(Nd[i * FOLD:(i + 1) * FOLD].sum()), float(Sd[i * FOLD:(i + 1) * FOLD].sum())) for i in range(st.NDAYS // FOLD)]
    r = rr.rr_stats([dict(pnl=p, kind=k) for p, k in zip(pnl, kinds)])
    kinds_n = {}
    for k in kinds:
        kinds_n[k] = kinds_n.get(k, 0) + 1
    lm = lmax(s_pct)
    hard = np.array([k == "stop" for k in kinds], dtype=bool)
    p = np.asarray(pnl, dtype=float)
    return dict(
        n=n, usd=float(p.sum()), mean_usd=float(p.sum() / n) if n else float("nan"), sharpe=s5.sharpe_year(Sd), months=months, folds=folds,
        maxdd=maxdd, maxdd_pct=maxdd / MARGIN * 100.0,
        kpi=dict(longest=int(kp["longest"]), p90=float(kp["p90"]), tail=int(kp["tail"]), gap_longest=int(u_["gap_longest"]), gap_mean=float(u_["gap_mean"]),
                 gap_tail=int(u_["tail"]), under_share=float(u_["under_share"]), highs=int(u_["highs"])),
        rr=dict(rr=r["rr"], mean_win=r["mean_win"], mean_loss=r["mean_loss"], n_win=r["n_win"], n_loss=r["n_loss"], stops=r["stops"]),
        kinds=kinds_n,
        lmax=dict(s=float(s_pct), usd=lm, pct_margin=lm / MARGIN * 100.0, worst=float(p.min()) if n else 0.0, worst_pct_margin=float(p.min()) / MARGIN * 100.0 if n else 0.0,
                  n_over=int((p < -lm - 1e-6).sum()), c_stop=float((-p[hard] - STAKE * s_pct / 100.0).mean()) if hard.any() else None, n_stop=int(hard.sum())),
        daily=[round(float(x), 6) for x in Sd], dayn=[int(x) for x in Nd])


def trade_keys(p8_mod, ser):
    return list(p8_mod.keys_of(ser))
