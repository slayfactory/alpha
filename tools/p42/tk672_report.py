#!/usr/bin/env python3
"""П-42 §13.72 (TK-672): выбор «топ-K в сутки», реплика портфеля, сравнение с базой, шум, вердикт (`prereg-13.72.md` §5–§9). Параметры и пороги протокола не меняются.

  python3 tk672_report.py --data-dir /data/p42-tk672/data --fit-dir /data/p42-tk672/fit --out <каталог> [--threads 2] [--nboot 20000] [--nnull 2000]

Реплика, метрики, бутстреп, Холм, MDE, DSR, базы на двойниках — из `tk671_report` (импорт). Клетки вердикта (m = 6): CatBoost лонг K₁, K₂, шорт K₁, K₂; LightGBM лонг K₁, шорт K₁ — против базы стороны (`L_base` / `S_base`);
справка вне Холма — против `L_n24` / `D_S`. K₁ = `R_base(m)`, K₂ = 2·K₁, `q = K / R_all(m)` по строкам `[01.07, начало m − 24 ч)`; выбор — `tk672_sel.causal_select` по оценкам месяца (с сутками разогрева).
Нулевая модель (6 клеток): в каждые сутки UTC сигнала случайные строки из всех строк суток в том же числе, что выбрала модель. Плацебо — 4 клетки CatBoost (окт, 01–13.11). Выход: `tables.json`, `report.md`."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk671_pf as pf  # noqa: E402
import tk671_report as r1  # noqa: E402
import tk672_fit as fit  # noqa: E402
import tk672_sel as sel  # noqa: E402

sh = r1.sh
START_MS, END_MS, NDAYS, DAY_MS = r1.START_MS, r1.END_MS, r1.NDAYS, r1.DAY_MS
SIDES, SIDE_RU = r1.SIDES, r1.SIDE_RU
MONTH_NAMES, MONTH_OF_DAY, MONTH_START_MS = r1.MONTH_NAMES, r1.MONTH_OF_DAY, r1.MONTH_START_MS
MONTH_LABELS = fit.MONTHS                              # метки моделей: 2026-08 … 2026-11
T_JUL1 = fit.f1.T_JUL1
SEED, NBOOT, BLOCK, NNULL = 672, 20_000, 5, 2_000
PRIOR_TRIALS, N_CELLS = 34, 6
TRIALS = PRIOR_TRIALS + N_CELLS                        # 40
# клетки вердикта: (библиотека, сторона, кратность K)
CELLS = (("cb", "long", 1), ("cb", "long", 2), ("cb", "short", 1), ("cb", "short", 2), ("lgb", "long", 1), ("lgb", "short", 1))
LIB_RU = {"cb": "CatBoost", "lgb": "LightGBM"}
PRIMARY_BASE, REF_BASE = r1.PRIMARY_BASE, r1.REF_BASE
V_ADD, V_NO, V_UNCLEAR = r1.V_ADD, r1.V_NO, r1.V_UNCLEAR
DATA_COLS = r1.DATA_COLS + ["rl_base", "rl_ref"]
PLC_MONTH_IDX = (2, 3)


def cid(lib, side, mult):
    return "%s|%s|K%d" % (lib, side, mult)


def cell_title(lib, side, mult):
    return "%s %s K%s" % (LIB_RU[lib], SIDE_RU[side], "₁" if mult == 1 else "₂")


# ───────────────────────────── загрузка ─────────────────────────────

def read_side(data_dir, side):
    path = os.path.join(data_dir, "%s.parquet" % side)
    have = set(pq.read_schema(path).names)
    df = pd.read_parquet(path, columns=[c for c in DATA_COLS if c in have])
    if "n24" not in df.columns:
        df["n24"] = np.nan
    df["sym"] = df["sym"].astype(str)
    df["win"] = df["win"].astype(str)
    return df


def read_pred(fit_dir, lib, side):
    df = pd.read_parquet(os.path.join(fit_dir, lib, side, "pred.parquet"))
    df["win"] = df["win"].astype(str)
    return df


def month_k(abs_ms_all, base_all, label):
    """K и q месяца m: строки `[01.07, начало m − 24 ч)` в сутки, метки не участвуют."""
    S = fit.f1.month_start_ms(label)
    rb, ra = sel.rates(abs_ms_all, base_all, T_JUL1, S - DAY_MS)
    return rb, ra


def select_side(d, side_, preds, log=print):
    """Для строк стороны `d` (ВСЕ строки с 01.07, с меткой и без) считает булевы столбцы выбора по клеткам стороны; `preds` — {lib: pred}.
    Возвращает (столбцы выбора, плацебо-столбцы, таблица K по месяцам)."""
    n = len(d)
    ab = d["meta_abs_ms"].to_numpy(np.int64)
    base = d["rl_base"].to_numpy(float)
    ix_df = d[["win", "row_id"]].copy()
    ix_df["_ix"] = np.arange(n)
    cols, plc, kinfo = {}, {}, []
    for lib, side, mult in CELLS:
        if side != side_:
            continue
        cols[cid(lib, side, mult)] = np.zeros(n, dtype=bool)
        if lib == "cb":
            plc[cid(lib, side, mult)] = np.zeros(n, dtype=bool)
    for lib, side, mult in CELLS:
        if side != side_:
            continue
        pred = preds[lib]
        for mi, label in enumerate(MONTH_LABELS):
            S = fit.f1.month_start_ms(label)
            rb, ra = month_k(ab, base, label)
            K = mult * rb
            q = K / ra
            pm = pred[pred["month"] == label].merge(ix_df, on=["win", "row_id"], how="left", validate="many_to_one")
            assert not pm["_ix"].isna().any(), "оценки строк, которых нет в наборе"
            ix = pm["_ix"].to_numpy(np.int64)
            ms = ab[ix]
            assert int(ms.min()) >= S - DAY_MS and int(ms.max()) < min(fit.month_end_ms(label), END_MS), "окно оценок вне [начало − 24 ч, конец месяца)"
            s = sel.causal_select(ms, pm["score"].to_numpy(float), q, S)
            cols[cid(lib, side, mult)][ix[s]] = True
            if lib == "cb" and mi in PLC_MONTH_IDX:
                sp = sel.causal_select(ms, pm["score_plc"].to_numpy(float), q, S)
                plc[cid(lib, side, mult)][ix[sp]] = True
            inm = (ab >= S) & (ab < min(fit.month_end_ms(label), END_MS))
            days = (min(fit.month_end_ms(label), END_MS) - S) / DAY_MS
            kinfo.append(dict(cell=cid(lib, side, mult), month=MONTH_NAMES[mi], r_base=rb, r_all=ra, K=K, q=q, days=days, rows=int(inm.sum()), selected_rows=int((cols[cid(lib, side, mult)] & inm).sum()),
                              selected_per_day=float((cols[cid(lib, side, mult)] & inm).sum() / days), base_rows_month=int((inm & (base > 0.5)).sum())))
    return cols, plc, kinfo


# ───────────────────────────── нулевая модель по суткам ─────────────────────────────

class DayPool:
    """Пул строк суток UTC сигнала: число строк суток (все) и позиции строк с меткой в `Cand` (в порядке движка `ms` по возрастанию)."""

    def __init__(self, c, all_ms):
        self.n_all = np.bincount(np.floor_divide(np.asarray(all_ms, dtype=np.int64) - START_MS, DAY_MS), minlength=NDAYS)
        dc = np.floor_divide(c.ms - START_MS, DAY_MS)
        assert (np.diff(dc) >= 0).all()
        self.lo = np.searchsorted(dc, np.arange(NDAYS), side="left")
        self.n_lab = np.searchsorted(dc, np.arange(NDAYS), side="right") - self.lo
        assert (self.n_lab <= self.n_all).all()


def null_cell_day(c, pool, counts_day, nnull, seed, model_sum, month_counts):
    """2 000 случайных наборов: в каждые сутки d выбирается counts_day[d] строк из всех строк суток (метка — у тех, у кого есть); та же реплика; Σ по сделкам, закрытым внутри периода."""
    rng = np.random.default_rng(seed)
    days = np.flatnonzero(counts_day > 0)
    sums = np.empty(nnull)
    for b in range(nnull):
        parts = []
        for d in days:
            k = int(min(counts_day[d], pool.n_all[d]))
            idx = rng.choice(int(pool.n_all[d]), size=k, replace=False)
            idx = idx[idx < pool.n_lab[d]]
            if len(idx):
                parts.append(pool.lo[d] + idx)
        sel_pos = np.sort(np.concatenate(parts)).astype(np.int64) if parts else np.zeros(0, dtype=np.int64)
        acc = c.replay(sel_pos)
        sums[b] = float(c.net_in[sel_pos][acc].sum())
    below = float((sums < model_sum).mean() + 0.5 * (sums == model_sum).mean())
    return dict(n=int(nnull), mean=float(sums.mean()), sd=float(sums.std(ddof=1)) if nnull > 1 else float("nan"), p5=float(np.percentile(sums, 5)), p95=float(np.percentile(sums, 95)), max=float(sums.max()),
                model_sum=float(model_sum), model_percentile=below * 100, p_ge=float((1 + (sums >= model_sum).sum()) / (1 + nnull)), counts=[int(x) for x in month_counts])


# ───────────────────────────── плацебо ─────────────────────────────

def placebo_cell(c, key_model, key_plc):
    out = {}
    for name, key in (("plc", key_plc), ("model", key_model)):
        pos = np.nonzero(c.x[key] & (c.month >= 2) & (c.month <= 3))[0]
        tr = r1.run_line(c, pos)
        res = {"n_trades": int(len(tr["net"])), "sum": float(tr["net"].sum()), "months": []}
        for m in PLC_MONTH_IDX:
            tm = MONTH_OF_DAY[tr["day"]] == m
            res["months"].append(dict(month=MONTH_NAMES[m], selected_rows_labeled=int((c.x[key] & (c.month == m)).sum()), n_trades=int(tm.sum()), sum=float(tr["net"][tm].sum())))
        out[name] = res
    return out


# ───────────────────────────── перекрытие модель / база ─────────────────────────────

def _key(c, pos):
    return c.ms[pos].astype(np.int64) * 4096 + c.sym[pos].astype(np.int64)


def overlap(cm, tr_m, cb, tr_b):
    """Принятые сделки модели против принятых сделок базы по ключу `(sym, sig_ms абсолютный)`; Σ и $/сделку: только модель / только база / общие."""
    km, kb = _key(cm, tr_m["pos"]), _key(cb, tr_b["pos"])
    com_m, com_b = np.isin(km, kb), np.isin(kb, km)
    nm, nb = tr_m["net"], tr_b["net"]
    def agg(x):
        return dict(n=int(len(x)), sum=float(x.sum()), mean=float(x.mean()) if len(x) else float("nan"))
    return dict(model_trades=int(len(km)), base_trades=int(len(kb)), common=int(com_m.sum()), share_base_in_model=float(com_b.mean()) if len(kb) else float("nan"), only_model=agg(nm[~com_m]),
                only_base=agg(nb[~com_b]), common_model_side=agg(nm[com_m]), common_base_side=agg(nb[com_b]), dup_keys_model=int(len(km) - len(np.unique(km))), dup_keys_base=int(len(kb) - len(np.unique(kb))))


# ───────────────────────────── основной счёт ─────────────────────────────

def compute(a, log=print):
    t_start = time.time()
    sb = r1.load_sb(a.sb_dir)
    res = {"meta": {}, "lines": {}, "cells": {}, "null": {}, "placebo": {}, "counter": {}, "n24_join": {}, "inputs": {}, "k": [], "overlap": {}, "base_share": {}, "auc": {}, "ypos_share": {}, "importance": {}, "tk671_check": {}}
    data = {s: read_side(a.data_dir, s) for s in SIDES}
    preds = {s: {lib: read_pred(a.fit_dir, lib, s) for lib in ("cb", "lgb")} for s in SIDES}
    twins = {b: r1.load_twin(a.gate_dir, tw, side) for b, (tw, side, _) in r1.BASES.items()}
    names = set()
    for s in SIDES:
        names.update(data[s].loc[data[s]["meta_abs_ms"] >= START_MS, "sym"].unique().tolist())
    for t in twins.values():
        names.update(t["sym"].unique().tolist())
    names_sorted = sorted(names)
    sym_id = {n: i for i, n in enumerate(names_sorted)}
    ncoins = len(sym_id)
    coin_arr = {}
    idx = r1.boot_index(a.nboot, seed=SEED)
    r1._replay_kernel(np.zeros(1, np.int64), np.zeros(1, np.int64), np.zeros(1, np.int64), np.zeros(1, np.int64), np.zeros(1, np.bool_), 1, 1, *r1._cfg_tuple())

    # ── базы (реплика на двойниках), как TK-671
    base_tr = {}
    for b, (tw, side, tpl) in r1.BASES.items():
        t = twins[b]
        if b == "L_n24":
            ok, j = r1.n24_pass(t, data["long"][data["long"]["meta_abs_ms"] >= START_MS])
            res["n24_join"] = j
            t = t[ok].reset_index(drop=True)
        c = r1.base_cand(t, sym_id)
        pos = np.arange(len(c))
        acc = c.replay(pos)
        tr = r1.accepted_trades(c, pos, acc)
        base_tr[b] = (c, tr)
        ser, daily = r1.line_series(tr["day"], tr["net"], tr["stop"])
        ser.update(side=side, kind="base", daily=daily.tolist(), n_closed_after=tr["n_after"], sum_closed_after=tr["sum_after"], twin=tw)
        coin_arr[b] = r1.coin_sums(tr, ncoins)
        ser["coin_sums"] = r1.coin_dict(coin_arr[b], names_sorted)
        res["lines"][b] = ser
        log("база %s: сделок %d, Σ %+.2f $, Шарп %.2f (%.0f с)" % (b, ser["total"]["n"], ser["total"]["sum"], ser["total"]["sharpe"], time.time() - t_start))
    t671 = os.path.join(a.tk671_tables)
    if os.path.exists(t671):
        old = json.load(open(t671, encoding="utf-8"))["lines"]
        for b in r1.BASES:
            if b in old:
                o, n_ = old[b]["total"], res["lines"][b]["total"]
                res["tk671_check"][b] = dict(n_old=o["n"], n_new=n_["n"], sum_old=o["sum"], sum_new=n_["sum"], equal=bool(o["n"] == n_["n"] and abs(o["sum"] - n_["sum"]) < 1e-6))

    # ── выбор по клеткам и кандидаты модели
    cands, selcols, plccols, pools = {}, {}, {}, {}
    for s in SIDES:
        d = data[s]
        cols, plc, kinfo = select_side(d, s, preds[s], log)
        res["k"].extend(kinfo)
        ab = d["meta_abs_ms"].to_numpy(np.int64)
        inr = (ab >= START_MS) & (ab < END_MS)
        has = d["y_has"].fillna(False).astype(bool).to_numpy()
        lab = has & np.isfinite(d["y_net"].to_numpy(float)) & (d["y_exit_ms"].to_numpy(float) >= 0)
        take = inr & lab
        res["inputs"][s] = dict(rows_all=int(len(d)), rows_in_period=int(inr.sum()), labeled_rows=int(take.sum()), unlabeled_rows=int((inr & ~lab).sum()), labeled_inconsistent=int((inr & has & ~lab).sum()))
        dd = d[take]
        extra = {k: v[take] for k, v in cols.items()}
        extra.update({"plc|" + k: v[take] for k, v in plc.items()})
        extra["rl_base"] = (d["rl_base"].to_numpy(float) > 0.5)[take]
        c = r1.Cand(dd["meta_abs_ms"].to_numpy(np.int64), dd["meta_dist_e8"].to_numpy(np.int64), dd["sym"].map(sym_id).to_numpy(np.int64), dd["meta_level_px_e8"].to_numpy(np.int64),
                    dd["y_exit_ms"].to_numpy(np.int64), dd["y_stop"].to_numpy(float) == 1.0, dd["y_net"].to_numpy(float), extra=extra)
        cands[s] = c
        selcols[s] = {k: v[inr] for k, v in cols.items()}
        plccols[s] = plc
        pools[s] = (DayPool(c, ab[inr]), np.floor_divide(ab[inr] - START_MS, DAY_MS), ab[inr], (d["rl_base"].to_numpy(float) > 0.5)[inr])
    res["inputs"]["note"] = "строки без метки (вход не состоялся) в портфеле места не занимают; в выборе и пороге участвуют"

    # ── клетки: реплика
    model_lines = {}
    for lib, side, mult in CELLS:
        k = cid(lib, side, mult)
        c = cands[side]
        pos = np.nonzero(c.x[k])[0]
        tr = r1.run_line(c, pos)
        ser, daily = r1.line_series(tr["day"], tr["net"], tr["stop"])
        sel_all = selcols[side][k]
        ser.update(side=side, kind="model", lib=lib, mult=mult, daily=daily.tolist(), selected_rows_labeled=int(len(pos)), selected_rows_all=int(sel_all.sum()), n_closed_after=tr["n_after"], sum_closed_after=tr["sum_after"])
        coin_arr[k] = r1.coin_sums(tr, ncoins)
        ser["coin_sums"] = r1.coin_dict(coin_arr[k], names_sorted)
        res["lines"][k] = ser
        model_lines[k] = (tr, daily, ser)
        log("клетка %s: выбрано строк %d (с меткой %d), сделок %d, Σ %+.2f $ (%.0f с)" % (k, sel_all.sum(), len(pos), ser["total"]["n"], ser["total"]["sum"], time.time() - t_start))

    # ── (а) доля строк базы в выборе; (б) перекрытие
    for lib, side, mult in CELLS:
        k = cid(lib, side, mult)
        pool, day_in, ab_in, base_in = pools[side]
        sel_in = selcols[side][k]
        mi = r1.month_of_ms(ab_in)
        res["base_share"][k] = dict(base_rows=int(base_in.sum()), selected_of_base=int((sel_in & base_in).sum()), share=float((sel_in & base_in).sum() / max(base_in.sum(), 1)),
                                    months=[dict(month=MONTH_NAMES[m], base_rows=int((base_in & (mi == m)).sum()), selected_of_base=int((sel_in & base_in & (mi == m)).sum())) for m in range(4)])
        for kind, b in (("primary", PRIMARY_BASE[side]), ("ref", REF_BASE[side])):
            res["overlap"]["%s|%s" % (k, b)] = overlap(cands[side], model_lines[k][0], *base_tr[b])

    # ── вывод по клеткам
    cells = {}
    for lib, side, mult in CELLS:
        k = cid(lib, side, mult)
        tr, daily, ser = model_lines[k]
        for kind, b in (("primary", PRIMARY_BASE[side]), ("ref", REF_BASE[side])):
            bd = np.asarray(res["lines"][b]["daily"])
            d_ = r1.delta_stats(daily, bd, coin_arr[k], coin_arr[b], idx)
            cells[(k, kind)] = dict(cell=k, lib=lib, side=side, mult=mult, kind=kind, base=b, **d_)
    prim = [cid(*c) for c in CELLS]
    hl = r1.holm([cells[(k, "primary")]["p"] for k in prim])
    for k, h in zip(prim, hl):
        cells[(k, "primary")].update(holm_rank=h["rank"], holm_step=h["step"], holm_pass=h["passed"], mde=r1.mde(cells[(k, "primary")]["sd_boot"], h["step"]))
    strict = r1.ALPHA / N_CELLS
    for k in prim:
        r = cells[(k, "ref")]
        r.update(holm_rank=None, holm_step=strict, holm_pass=bool(r["p"] <= strict), mde=r1.mde(r["sd_boot"], strict))
    for (k, kind), cl in cells.items():
        ser, bl = res["lines"][k], res["lines"][cl["base"]]
        v, conds = r1.verdict(cl["holm_pass"], ser["total"]["sum"], bl["total"]["sum"], ser["total"]["sharpe"], bl["total"]["sharpe"], cl["month_dsum"], [m["n"] for m in ser["months"]],
                              cl["dsum"], cl["dsum_no2days"], cl["dsum_no2coins"])
        cl.update(verdict=v, conditions=conds, model_months_n=[m["n"] for m in ser["months"]], model_share_days=ser["total"]["share_days_trades"], sum_model=ser["total"]["sum"], sum_base=bl["total"]["sum"],
                  sharpe_model=ser["total"]["sharpe"], sharpe_base=bl["total"]["sharpe"], months_dsum_positive=int(sum(1 for x in cl["month_dsum"] if x > 0)),
                  months_n_ok=int(sum(1 for m in ser["months"] if m["n"] >= r1.MIN_TRADES_MONTH)))
        cl["dsr"] = r1.dsr_cell(sb, ser["total"]["sharpe"], trials=TRIALS) if kind == "primary" else None
    res["cells"] = {"%s|%s" % key: v for key, v in cells.items()}

    # ── нулевая модель (6 клеток), плацебо (4 клетки CatBoost)
    def job(ci):
        lib, side, mult = CELLS[ci]
        k = cid(lib, side, mult)
        pool, day_in, ab_in, base_in = pools[side]
        sel_in = selcols[side][k]
        counts_day = np.bincount(day_in[sel_in], minlength=NDAYS)
        mi = r1.month_of_ms(ab_in)
        mcounts = [int((sel_in & (mi == m)).sum()) for m in range(4)]
        return k, null_cell_day(cands[side], pool, counts_day, a.nnull, [SEED, ci], model_lines[k][2]["total"]["sum"] + 0.0, mcounts)
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=max(1, a.threads)) as ex:
        for k, r in ex.map(job, range(len(CELLS))):
            res["null"][k] = r
    log("нулевая модель: %d клеток × %d наборов, %.0f с" % (len(CELLS), a.nnull, time.time() - t0))
    for lib, side, mult in CELLS:
        if lib == "cb":
            k = cid(lib, side, mult)
            res["placebo"][k] = placebo_cell(cands[side], k, "plc|" + k)

    # ── (в) AUC по месяцам и доля y_pos у флага; (г) важность
    for s in SIDES:
        d = data[s]
        key = d[["win", "row_id", "y_has", "y_net", "rl_base"]].copy()
        for lib in ("cb", "lgb"):
            pr = preds[s][lib]
            pr = pr[~pr["warm"]].merge(key, on=["win", "row_id"], how="left", validate="many_to_one")
            for label, mname in zip(MONTH_LABELS, MONTH_NAMES):
                m = pr[(pr["month"] == label) & pr["y_has"].fillna(False).astype(bool)]
                y = (m["y_net"].to_numpy(float) > 0).astype(float)
                fl = m["rl_base"].to_numpy(float) > 0.5
                res["auc"]["%s|%s|%s" % (lib, s, mname)] = dict(rows=int(len(m)), auc=fit.auc(y, m["score"].to_numpy(float)), auc_flag_rows=fit.auc(y[fl], m["score"].to_numpy(float)[fl]) if fl.sum() > 30 else None, flag_rows=int(fl.sum()))
        lab = key[key["y_has"].fillna(False).astype(bool)]
        ab = d.loc[lab.index, "meta_abs_ms"].to_numpy(np.int64)
        mi = r1.month_of_ms(ab)
        yp = (lab["y_net"].to_numpy(float) > 0)
        fl = lab["rl_base"].to_numpy(float) > 0.5
        res["ypos_share"][s] = [dict(month=MONTH_NAMES[m], n_flag=int((fl & (mi == m)).sum()), share_flag=float(yp[fl & (mi == m)].mean()) if (fl & (mi == m)).any() else None, n_other=int((~fl & (mi == m)).sum()),
                                     share_other=float(yp[~fl & (mi == m)].mean())) for m in range(4)]
        for lib in ("cb", "lgb"):
            for label, mname in zip(MONTH_LABELS, MONTH_NAMES):
                p = os.path.join(a.fit_dir, lib, s, "importance-%s.json" % label)
                if os.path.exists(p):
                    imp = json.load(open(p, encoding="utf-8"))["clf"]
                    res["importance"]["%s|%s|%s" % (lib, s, mname)] = dict(families=dict(list(imp["families"].items())[:8]), rl=float(imp["families"].get("rl", 0.0)), rules=float(imp["families"].get("rules", 0.0)),
                                                                          top=[(k, round(v, 2)) for k, v in list(imp["features"].items())[:5]])

    nstar = {}
    for lib in ("cb", "lgb"):
        for s in SIDES:
            p = os.path.join(a.fit_dir, lib, s, "nstar.json")
            if os.path.exists(p):
                j = json.load(open(p, encoding="utf-8"))
                nstar["%s|%s" % (lib, s)] = dict(nstar=j["nstar"], auc=j["auc"], n_train=j["n_train"], n_val=j["n_val"], n_features=j["n_features"])
    res["nstar"] = nstar
    res["counter"] = dict(prior=PRIOR_TRIALS, cells=N_CELLS, trials=TRIALS, text="CatBoost/f12 trials: %d + %d = %d" % (PRIOR_TRIALS, N_CELLS, TRIALS),
                          call="sb.significance(S, se, %d), se = sqrt(365/%d·(1 + S²/730)), S — годовой Шарп клетки за авг–ноя" % (TRIALS, NDAYS), source=os.path.abspath(sb.__file__),
                          note="все семейства признаков, `rl_*` и сами правила базы найдены на этих данных (01.07–13.11); вывод — только кандидат вперёд")
    res["meta"] = dict(start="2026-08-01", end="2026-11-13", days=int(NDAYS), nboot=a.nboot, block=BLOCK, seed=SEED, nnull=a.nnull, alpha=r1.ALPHA, power=r1.POWER, margin=r1.MARGIN, cfg=dict(pf.BASE_CFG),
                       numba=bool(hasattr(r1._replay_kernel, "py_func")), elapsed_s=round(time.time() - t_start, 1), clarifications=CLARIFICATIONS)
    return res


CLARIFICATIONS = [
    "p = доля бутстреп-сумм Δ ≤ 0 (число ÷ число повторов, без поправки +1); Холм m = 6 по 6 клеткам вердикта; справочные ячейки (L_n24, D_S) — тест вне Холма на самом строгом шаге 0,05/6, вердикт справочный.",
    "«≥ 30 сделок минимум в 3 месяцах» — принятые репликой сделки по месяцу закрытия (выбранные строки печатаются рядом).",
    "«Без 2 лучших суток / монет»: из ΔΣ вычитаются два наибольших ПОЛОЖИТЕЛЬНЫХ суточных (по-монетных) Δ (соглашение tk668_post.line_tests).",
    "Базы — реплика на сделках двойников (как TK-671); L_base / L_n24 / S_base / D_S совпадают с TK-671 (строка «сверка» ниже).",
    "Ключ «общих» сделок — `(sym, sig_ms абсолютный)`: `level` у двойника и `meta_level` набора — разные нумерации; дублей ключей нет (печатается).",
    "Месяц сделки — месяц закрытия (как сутки P&L); месяц выбранных строк (R:R у K, «выбрано в сутки») — месяц сигнала; «выбрано в сутки» считает все строки (с меткой и без), принятые сделки — только строки с меткой после реплики.",
    "Нулевая модель: в каждые сутки UTC сигнала случайные строки из ВСЕХ строк суток в том же числе, что выбрала модель; у кого есть метка — те идут в ту же реплику.",
    "Плацебо: окт и 01–13.11 одной репликой со стартом 01.10 с пустым состоянием; модель на тех же месяцах и тем же протоколом — рядом. Плацебо только у CatBoost.",
    "Годовой Шарп при нулевой дисперсии суточного ряда = 0 (как tk663_shape.sharpe).",
]


# ───────────────────────────── отчёт ─────────────────────────────

def _f(x, nd=2, sign=False):
    return sh.num(None if x is None else float(x), nd, sign)


def _strip(o):
    return r1._strip(o)


def _line_title(k, ser):
    if ser["kind"] == "model":
        return cell_title(ser["lib"], ser["side"], ser["mult"])
    return k


def build_report(res):
    L, P, cells = res["lines"], [], res["cells"]
    mk = lambda side: [cid(*c) for c in CELLS if c[1] == side] + [PRIMARY_BASE[side], REF_BASE[side]]
    P.append("# TK-672 (§13.72): классификатор «сделка в плюс» + «топ-K в сутки», правила — числами. Реплика портфеля, сравнение с базой, 01.08–13.11 (105 суток), ставка 500 $, плечо 2\n")
    for side in SIDES:
        bits = []
        for k in mk(side):
            m = L[k]["total"]
            bits.append("%s %s (стоп %s $ = %s %% маржи 250 $, макс. %s $)" % (_line_title(k, L[k]), _f(m["rr"]), _f(m["stop_mean"], 1, True), _f(m["stop_mean_pct"], 1, True), _f(m["stop_max"], 1, True)))
        P.append("- **R:R %s** (средний выигрыш ÷ |средний убыток|, авг–ноя): %s." % (SIDE_RU[side], "; ".join(bits)))
    groups = {V_ADD: [], V_NO: [], V_UNCLEAR: []}
    for c in CELLS:
        k = cid(*c)
        x = cells["%s|primary" % k]
        groups[x["verdict"]].append("%s (p %s, шаг Холма %s)" % (cell_title(*c), _f(x["p"], 4), _f(x["holm_step"], 4)))
    P.append("- **Вердикты (6 клеток, база стороны, Холм m = 6):** " + "; ".join("%s — %s" % (v, ", ".join(x) if x else "нет") for v, x in groups.items()) + ".")
    for side in SIDES:
        b = L[PRIMARY_BASE[side]]["total"]
        bits = ["%s %s $ (Шарп %s; n %d; просадка %s $; перехай самый длинный %d сут.)" % (cell_title(*c), _f(L[cid(*c)]["total"]["sum"], 0, True), _f(L[cid(*c)]["total"]["sharpe"]), L[cid(*c)]["total"]["n"], _f(L[cid(*c)]["total"]["maxdd"], 0),
                                                                                     L[cid(*c)]["total"]["gap_longest"]) for c in CELLS if c[1] == side]
        P.append("- **Σ $ за авг–ноя, %s:** база %s %s $ (Шарп %s; n %d; просадка %s $; перехай %d сут.); %s." % (SIDE_RU[side], PRIMARY_BASE[side], _f(b["sum"], 0, True), _f(b["sharpe"]), b["n"], _f(b["maxdd"], 0), b["gap_longest"], "; ".join(bits)))
    P.append("- **Счётчик:** %s; всё найдено на этих данных (01.07–13.11), результат — только кандидат вперёд (В-165)." % res["counter"]["text"])
    P.append("")

    P.append("## 1. R:R и убыток стопа (авг–ноя; 250 $ маржи)\n")
    for side in SIDES:
        rows = []
        for k in mk(side):
            m = L[k]["total"]
            rows.append([_line_title(k, L[k]), m["n"], _f(m["mean_win"], 2, True), _f(m["mean_loss"], 2, True), _f(m["rr"]), m["stop_n"], "%s (%s %%)" % (_f(m["stop_mean"], 2, True), _f(m["stop_mean_pct"], 1, True)),
                         "%s (%s %%)" % (_f(m["stop_max"], 2, True), _f(m["stop_max_pct"], 1, True))])
        P.append("**%s**\n" % SIDE_RU[side])
        P.append("\n".join(sh.table(["линия", "сделок", "средний выигрыш, $", "средний убыток, $", "R:R", "стопов", "убыток стопа средний, $ (% маржи)", "убыток стопа макс., $ (% маржи)"], rows)))

    P.append("## 2. Итоги за авг–ноя (ряд на все 105 суток; сутки — закрытие сделки, UTC)\n")
    for side in SIDES:
        rows = []
        for k in mk(side):
            m = L[k]["total"]
            rows.append([_line_title(k, L[k]), _f(m["sum"], 2, True), _f(m["mean"], 3, True), m["n"], _f(m["sharpe"]), "%s (%s %%)" % (_f(m["maxdd"]), _f(m["maxdd_pct"], 1)), sh.pct(m["share_days_trades"])])
        P.append("**%s**\n" % SIDE_RU[side])
        P.append("\n".join(sh.table(["линия", "Σ, $", "на сделку, $", "сделок", "годовой Шарп", "макс. просадка, $ (% маржи)", "доля суток со сделками"], rows)))

    P.append("## 3. По месяцам (месяц закрытия): Σ $ · $/сделку · n · Шарп\n")
    for side in SIDES:
        rows = []
        for k in mk(side):
            row = [_line_title(k, L[k])]
            for mm in L[k]["months"]:
                row.append("%s · %s · %d · %s" % (_f(mm["sum"], 1, True), _f(mm["mean"], 2, True), mm["n"], _f(mm["sharpe"], 1)))
            rows.append(row)
        P.append("**%s**\n" % SIDE_RU[side])
        P.append("\n".join(sh.table(["линия"] + list(MONTH_NAMES), rows)))

    P.append("## 4. Перехай P&L (сутки между новыми максимумами; авг–ноя)\n")
    P.append("Зазор — календарных суток между соседними новыми максимумами конца суток (старт — 0); «самый длинный» включает хвост до конца периода; средний и p90 — по закрытым зазорам; доля под водой — доля суток ниже пика.\n")
    for side in SIDES:
        rows = []
        for k in mk(side):
            m = L[k]["total"]
            rows.append([_line_title(k, L[k]), m["highs"], m["gap_longest"], _f(m["gap_mean"], 1), _f(m["gap_p90"], 1), m["tail"], sh.pct(m["under_share"]), m["under_longest"]])
        P.append("**%s**\n" % SIDE_RU[side])
        P.append("\n".join(sh.table(["линия", "новых максимумов", "самый длинный, сут.", "средний, сут.", "p90, сут.", "хвост, сут.", "доля суток под водой", "самый долгий эпизод под водой, сут."], rows)))

    P.append("## 5. Шум и вердикт: парная суточная Δ (клетка − база), блочный бутстреп (блок %d, %d, seed %d)\n" % (BLOCK, res["meta"]["nboot"], SEED))
    P.append("Основные ячейки — база стороны, Холм m = 6; ячейки `справка` (L_n24, D_S) — тот же тест вне Холма (шаг 0,05/6). MDE — что вообще видно при 80 % мощности. Δ по месяцам — месяц закрытия.\n")
    rows = []
    for key, c in cells.items():
        k, kind = key.rsplit("|", 1)
        rows.append(["%s vs %s%s" % (cell_title(c["lib"], c["side"], c["mult"]), c["base"], "" if kind == "primary" else " (справка)"), _f(c["sum_model"], 1, True), _f(c["sum_base"], 1, True), _f(c["dsum"], 1, True), _f(c["p"], 4),
                     ("%d · %s" % (c["holm_rank"], _f(c["holm_step"], 4)) if c["holm_rank"] else "— · %s" % _f(c["holm_step"], 4)) + (" · да" if c["holm_pass"] else " · нет"),
                     _f(c["sd_boot"], 1), _f(c["mde"], 1), " / ".join(_f(x, 1, True) for x in c["month_dsum"]), "%d из 4" % c["months_dsum_positive"], " / ".join(str(x) for x in c["model_months_n"]),
                     _f(c["dsum_no2days"], 1, True), _f(c["dsum_no2coins"], 1, True), sh.pct(c["model_share_days"]), c["verdict"]])
    P.append("\n".join(sh.table(["ячейка", "Σ клетки, $", "Σ базы, $", "ΔΣ, $", "p (односторонний)", "ранг · шаг Холма · прошла", "SD бутстрепа ΔΣ, $", "MDE, $", "ΔΣ по месяцам, $", "месяцев Δ>0", "сделок по месяцам", "ΔΣ без 2 лучших суток, $",
                                 "ΔΣ без 2 лучших монет, $", "доля суток со сделками", "вердикт"], rows)))
    P.append("### 5.1. Условия вердикта (основные ячейки)\n")
    rows = []
    yn = lambda x: "да" if x else "нет"
    for c in CELLS:
        x = cells["%s|primary" % cid(*c)]
        cd = x["conditions"]
        rows.append([cell_title(*c), yn(cd["holm"]), yn(cd["sum_ge"]), yn(cd["sharpe_ge"]), yn(cd["months_pos"]), yn(cd["months_n"]), yn(cd["no2days"]), yn(cd["no2coins"]), x["verdict"]])
    P.append("\n".join(sh.table(["ячейка", "p ≤ шага Холма", "Σ клетки ≥ базы", "Шарп клетки ≥ базы", "Δ>0 в ≥ 3 из 4 месяцев", "≥ 30 сделок в ≥ 3 месяцах", "ΔΣ>0 без 2 лучших суток", "ΔΣ>0 без 2 лучших монет", "вердикт"], rows)))

    P.append("## 6. Состав выбора: K, выбрано в сутки, доля строк базы, перекрытие с базой\n")
    rows = []
    for x in res["k"]:
        rows.append([x["cell"], x["month"], _f(x["r_base"], 2), _f(x["K"], 2), "%.5f" % x["q"], x["rows"], x["selected_rows"], _f(x["selected_per_day"], 2), x["base_rows_month"]])
    P.append("\n".join(sh.table(["клетка", "месяц", "R_base, строк/сут (до месяца)", "K, строк/сут", "q = K/R_all", "строк месяца", "выбрано строк (все)", "выбрано в сутки", "строк базы в месяце"], rows)))
    P.append("")
    rows = []
    for c in CELLS:
        k = cid(*c)
        b = res["base_share"][k]
        rows.append([cell_title(*c), b["base_rows"], b["selected_of_base"], sh.pct(b["share"], 1), " / ".join("%d из %d" % (m["selected_of_base"], m["base_rows"]) for m in b["months"])])
    P.append("Доля строк базы (`rl_base = 1`, 01.08–13.11, все строки с меткой и без), попавших в выбор клетки:\n")
    P.append("\n".join(sh.table(["клетка", "строк базы", "выбрано", "доля", "по месяцам (авг / сен / окт / ноя)"], rows)))
    P.append("")
    rows = []
    for key, o in res["overlap"].items():
        k, b = key.rsplit("|", 1)
        lib, side, mk_ = k.split("|")
        rows.append([k + " vs " + b, o["model_trades"], o["base_trades"], o["common"], sh.pct(o["share_base_in_model"], 1), "%d · %s $ · %s $/сд." % (o["only_model"]["n"], _f(o["only_model"]["sum"], 1, True), _f(o["only_model"]["mean"], 2, True)),
                     "%d · %s $ · %s $/сд." % (o["only_base"]["n"], _f(o["only_base"]["sum"], 1, True), _f(o["only_base"]["mean"], 2, True)),
                     "%d · %s $ · %s $/сд." % (o["common_model_side"]["n"], _f(o["common_model_side"]["sum"], 1, True), _f(o["common_model_side"]["mean"], 2, True)), o["dup_keys_model"] + o["dup_keys_base"]])
    P.append("Принятые сделки клетки против принятых сделок базы (ключ `(sym, sig_ms)`):\n")
    P.append("\n".join(sh.table(["клетка vs база", "сделок клетки", "сделок базы", "общих", "доля сделок базы в клетке", "только модель (n · Σ · на сделку)", "только база (n · Σ · на сделку)", "общие (n · Σ · на сделку, по клетке)", "дубли ключей"], rows)))

    P.append("## 7. Нулевая модель: %d случайных наборов (те же числа по суткам), та же реплика\n" % res["meta"]["nnull"])
    rows = []
    for k, r in res["null"].items():
        lib, side, mk_ = k.split("|")
        rows.append([cell_title(lib, side, int(mk_[1:])), "/".join(str(x) for x in r["counts"]), _f(r["mean"], 1, True), _f(r["sd"], 1), "%s … %s" % (_f(r["p5"], 1, True), _f(r["p95"], 1, True)), _f(r["model_sum"], 1, True),
                     _f(r["model_percentile"], 1) + " %", _f(r["p_ge"], 4)])
    P.append("\n".join(sh.table(["клетка", "выбрано строк по месяцам", "Σ набора, среднее, $", "SD, $", "5 … 95 %, $", "Σ клетки, $", "процентиль клетки", "доля наборов с Σ ≥ клетки"], rows)))
    P.append("## 8. Плацебо CatBoost (`y_pos` переставлен внутри суток; окт и 01–13.11, метки настоящие; старт реплики 01.10)\n")
    rows = []
    for k, r in res["placebo"].items():
        lib, side, mk_ = k.split("|")
        pm, mm = r["plc"], r["model"]
        rows.append([cell_title(lib, side, int(mk_[1:])), " / ".join("%s %d" % (x["month"], x["selected_rows_labeled"]) for x in pm["months"]), pm["n_trades"], _f(pm["sum"], 1, True),
                     " / ".join("%s %d" % (x["month"], x["selected_rows_labeled"]) for x in mm["months"]), mm["n_trades"], _f(mm["sum"], 1, True)])
    P.append("\n".join(sh.table(["клетка", "выбрано строк с меткой (плацебо)", "сделок", "Σ плацебо, $", "выбрано строк с меткой (модель)", "сделок", "Σ модели на тех же месяцах, $"], rows)))

    P.append("## 9. AUC по месяцам (все строки с меткой; описательно), доля «в плюс» у строк базы, важность семейств\n")
    rows = []
    for key, x in res["auc"].items():
        lib, side, mname = key.split("|")
        rows.append([LIB_RU[lib], SIDE_RU[side], mname, x["rows"], _f(x["auc"], 4), "—" if x["auc_flag_rows"] is None else _f(x["auc_flag_rows"], 3), x["flag_rows"]])
    P.append("\n".join(sh.table(["библиотека", "сторона", "месяц", "строк с меткой", "AUC", "AUC на строках базы", "строк базы с меткой"], rows)))
    P.append("")
    rows = []
    for s in SIDES:
        for x in res["ypos_share"][s]:
            rows.append([SIDE_RU[s], x["month"], x["n_flag"], sh.pct(x["share_flag"], 1) if x["share_flag"] is not None else "—", x["n_other"], sh.pct(x["share_other"], 1)])
    P.append("Доля сделок «в плюс» (`y_net > 0`): строки базы против прочих.\n")
    P.append("\n".join(sh.table(["сторона", "месяц", "строк базы", "доля в плюс", "прочих строк", "доля в плюс"], rows)))
    P.append("")
    rows = []
    for key, x in res["importance"].items():
        lib, side, mname = key.split("|")
        rows.append([LIB_RU[lib], SIDE_RU[side], mname, _f(x["rl"], 2) + " %", _f(x["rules"], 2) + " %", ", ".join("%s %s" % (a_, _f(b_, 1)) for a_, b_ in list(x["families"].items())[:5]), ", ".join("%s %s" % (a_, _f(b_, 1)) for a_, b_ in x["top"][:3])])
    P.append("Важность (CatBoost — PredictionValuesChange, LightGBM — gain, % от суммы):\n")
    P.append("\n".join(sh.table(["библиотека", "сторона", "месяц", "семейство `rl_*`", "семейство `rules_*`", "топ-5 семейств", "топ-3 признака"], rows)))

    P.append("## 10. Счётчик и DSR\n")
    P.append("**%s** (прежний CatBoost / f12 = %d + эти 6 клеток); справочные ячейки в счётчик не входят. Все семейства признаков и `rl_*` — «найдено на этих данных». DSR: `%s`, источник `%s`.\n" % (res["counter"]["text"], PRIOR_TRIALS, res["counter"]["call"], res["counter"]["source"]))
    rows = []
    for c in CELLS:
        d = cells["%s|primary" % cid(*c)]["dsr"]
        rows.append([cell_title(*c), _f(d["sharpe"]), _f(d["se"], 3), _f(d["noise_bar"]), _f(d["deflated"]), "да" if d["survives"] else "нет"])
    P.append("\n".join(sh.table(["клетка", "Шарп клетки", "SE", "планка шума (n = 40)", "дефлированный Шарп", "значим"], rows)))

    P.append("## 11. Справка и сверка\n")
    for b, x in res["tk671_check"].items():
        P.append("- база %s: n %d (TK-671 %d), Σ %s $ (TK-671 %s $) — %s." % (b, x["n_new"], x["n_old"], _f(x["sum_new"], 2, True), _f(x["sum_old"], 2, True), "равна" if x["equal"] else "НЕ равна"))
    j = res["n24_join"]
    P.append("- L_n24: сделок двойника %d; без строки данных %d; n24 пустой %d; строки одного ключа не согласны по порогу %d (считаются НЕ прошедшими); прошло n24 ≥ 220 000: %d." % (j["twin_trades"], j["no_data_row"], j["n24_nan"], j["ambiguous_rows_treated_as_fail"], j["passed"]))
    for s in SIDES:
        i = res["inputs"][s]
        P.append("- Данные %s: строк в наборе %d, в периоде %d, с меткой %d, без метки %d (не торгуют), несогласованных меток %d." % (SIDE_RU[s], i["rows_all"], i["rows_in_period"], i["labeled_rows"], i["unlabeled_rows"], i["labeled_inconsistent"]))
    for k, ser in L.items():
        if ser["n_closed_after"]:
            P.append("- Закрыто после 13.11 и не вошло в ряд: %s — %d сделок, Σ %s $." % (_line_title(k, ser), ser["n_closed_after"], _f(ser["sum_closed_after"], 2, True)))
    P.append("- N* (AUC июльской проверки): " + "; ".join("%s %s: %d" % (LIB_RU[k.split("|")[0]], SIDE_RU[k.split("|")[1]], v["nstar"]) for k, v in res["nstar"].items()) + ".")
    P.append("\n## 12. Уточнения прочтения протокола\n")
    P.extend("- " + c for c in res["meta"]["clarifications"])
    P.append("")
    return "\n".join(P)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default="/data/p42-tk672/data")
    ap.add_argument("--fit-dir", default="/data/p42-tk672/fit")
    ap.add_argument("--out", default="/data/p42-tk672/report")
    ap.add_argument("--gate-dir", default="/data/p42-tk671/gate-pf")
    ap.add_argument("--tk671-tables", default="/data/p42-tk671/report/tables.json")
    ap.add_argument("--sb-dir", default=None)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--nboot", type=int, default=NBOOT)
    ap.add_argument("--nnull", type=int, default=NNULL)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    res = compute(a)
    with open(os.path.join(a.out, "tables.json"), "w", encoding="utf-8") as f:
        json.dump(_strip(res), f, ensure_ascii=False, indent=1)
    with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(build_report(res))
    print("готово:", os.path.join(a.out, "report.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
