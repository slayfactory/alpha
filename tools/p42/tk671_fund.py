#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), семейство `fund`: признаки TK-666 (PS-936) — фандинг F, премия P, круглое число R, слияние уровня с 240-минутными C — на строках `rows.parquet` (12 источников уровней).

  python3 tk671_fund.py --rows <rows.parquet> --win wa --side long --out <out.parquet> [--procs 3]
Модуль: `compute(rows, win, side, procs=3) -> DataFrame` (индекс `row_id`, float64; NaN — не определено; строки не выбрасываются). Пишет `<out>.info.json` (доли не-NaN, охват данных) и `<out>.need.json` (чего не хватает на диске).

Столбцы (10) и определения — `tk666_feat.py` без изменений (формулы импортируются оттуда для эталона; здесь они векторизованы по монете и сверены с оригиналом на реальных строках, `tk671_gate_fund.py`):
  f8, f_age_h       ставка последней выплаты с calc_time ≤ σ, приведённая к 8 ч; возраст выплаты, ч (старше 2 интервалов → f8 NaN, возраст остаётся); нет данных → NaN, NaN
  prem, prem_age_s  close последней закрытой минутной свечи `premiumIndexKlines` (open + 60 с ≤ σ), возраст, с; старше 5 мин или нет файла → NaN
  rho, mant         ρ = расстояние цены уровня до круглого числа в долях NATR·p; мантисса p / 10^⌊log10 p⌋ (`round_feat`)
  lvl_px            |level_px_e8|
  conf01 conf02 conf04   0/1 при τ = 0,1 / 0,2 / 0,4 %: у монеты есть живой на минуте σ (born ≤ m < end, m = sig_ms // 60000) уровень `tf = 240` ДРУГОЙ, чем уровень строки, чья версия цены на m
                    (последняя с временем ≤ m) в пределах τ·p от цены уровня строки; нет таких → 0; нет дампа уровней окна и стороны → NaN
Опорный набор уровней «240» (решение TK-671 «минимальное честное определение»): ровно тот же, что у TK-666 — дампы движка `/data/p42-tk666/levels/<окно>/<L|S>_t240/<long|short>-levels.csv` (база стороны + `tf=240`, n_bounce базы, `pk=1`;
`P42_LEVELS`), окна wa / wb / wc, обе стороны — ГОТОВЫ, новый прогон движка не нужен. У 12 источников строки признак тот же: «уровень строки слит с живым 240-минутным уровнем в пределах τ» (для строк с `tf = 30 / 60`, `lx = D / W / P` — дословно
определение TK-666, оригинальная `confluence` даёт те же значения). «Другой уровень»: если маска источников строки (`mask`) содержит любой из `p240n1/2/3`, сама строка — 240-минутный уровень, и из опорного набора исключаются уровни
с тем же `first_bar` (тот же кластер пивотов) или с точно той же ценой на m (то же значение, найденное другим источником); без этого conf = 1 тривиально. Для остальных строк исключений нет. Нет `mask` / `first_bar` в `rows` — исключений нет.
Причинность: версия цены берётся со временем ≤ m; живость — born ≤ m < end (`end` — минута закрытия 240-минутного бара, добивший уровень; уровни движка строятся только по закрытым барам). Ворота — `tk671_gate_fund.py`.
Данные: фандинг — месячные архивы 06–10 (`/data/p42-metrics/funding`, `/data/p42-tk666/funding`, `/data/p42-tk600/fund-all`) + REST ноября `/data/p42-tk666/funding/<СИМВОЛ>/rest-2026-11.json` (только монеты, скачанные TK-666);
премия — суточные архивы `/data/p42-premium`, `/data/p42-tk666/premium`, `/data/p42-tk600/premium-all`. Нет файла → NaN, строка не падает. Сутки ≥ 14.11 не читаются (охрана `check_ms`)."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk604_stats as st  # noqa: E402
import tk666_feat as ft  # noqa: E402
import tk671_lvl as lvl  # noqa: E402

COLS = list(ft.FEAT_COLS)                                  # f8 f_age_h prem prem_age_s conf01 conf02 conf04 rho mant lvl_px
FUND_ROOTS = (ft.FUND_OLD, ft.FUND_NEW, "/data/p42-tk600/fund-all")
PREM_ROOTS = (ft.PREM_OLD, ft.PREM_NEW, "/data/p42-tk600/premium-all")
FUND_MONTHS = ("2026-06",) + tuple(ft.MONTHS)              # июнь — для строк первых часов 01.07 (последняя выплата 30.06)
SIDE_LV = {"long": "L", "short": "S"}
P240_BITS = sum(1 << lvl.SOURCES.index(s) for s in ("p240n1", "p240n2", "p240n3"))
DAY_MS = 86_400_000
EPOCH = dt.date(1970, 1, 1)


def day_str(ms):
    return (EPOCH + dt.timedelta(int(ms // DAY_MS))).isoformat()


# ---- F: фандинг (по монете) ------------------------------------------------------------------------------------------------------------------

def fund_path(sym, month):
    for root in FUND_ROOTS:
        p = os.path.join(root, sym, "%s-fundingRate-%s.zip" % (sym, month))
        if os.path.exists(p):
            return p
    return None


def load_funding(sym):
    """Как `tk666_feat.load_funding`, но месяцы 06–10 и три корня; (t, rate8, inter) — как у оригинала."""
    rows = []
    for m in FUND_MONTHS:
        p = fund_path(sym, m)
        if p:
            rows += list(ft._read_funding_zip(p))
    rp = os.path.join(ft.FUND_NEW, sym, "rest-2026-11.json")
    if os.path.exists(rp):
        rest = sorted(json.load(open(rp, encoding="utf-8")), key=lambda x: int(x["fundingTime"]))
        for x in rest:
            t = int(x["fundingTime"])
            ft.check_ms(t)
            rows.append((t, None, float(x["fundingRate"])))
    rows.sort(key=lambda r: r[0])
    t = np.array([r[0] for r in rows], dtype=np.int64)
    inter = np.zeros(len(rows))
    for i, (ti, h, _) in enumerate(rows):
        if h is not None:
            inter[i] = h
        elif i > 0:
            inter[i] = ft.interval_round((ti - rows[i - 1][0]) / 3.6e6)
        else:
            inter[i] = 8.0
    rate8 = np.array([r[2] for r in rows]) * 8.0 / np.where(inter > 0, inter, 8.0)
    return t, rate8, inter


def funding_vec(t, rate8, inter, ms):
    """`tk666_feat.funding_at` для массива `ms`."""
    n = len(ms)
    f8, age = np.full(n, np.nan), np.full(n, np.nan)
    if len(t) == 0:
        return f8, age
    i = np.searchsorted(t, ms, side="right") - 1
    ok = i >= 0
    ic = np.maximum(i, 0)
    a = (ms - t[ic]) / 3.6e6
    age = np.where(ok, a, np.nan)
    fresh = ok & ~(a > ft.FUND_GAP_X * inter[ic])
    f8 = np.where(fresh, rate8[ic], np.nan)
    return f8, age


# ---- P: премия (по монете) -------------------------------------------------------------------------------------------------------------------

def prem_path(sym, day):
    for root in PREM_ROOTS:
        p = os.path.join(root, sym, "%s-1m-%s.zip" % (sym, day))
        if os.path.exists(p):
            return p
    return None


def load_prem(sym, days):
    """(open_ms по возрастанию, close) из суточных архивов `days`; свеча берётся только из файла СВОИХ суток и только на целой минуте (как словарь `Prem.day` + поиск `o in d`)."""
    ops, cls, missing = [], [], []
    for d in days:
        p = prem_path(sym, d)
        if p is None:
            missing.append(d)
            continue
        dd = ft.read_prem_zip(p)
        if not dd:
            continue
        o = np.fromiter(dd.keys(), dtype=np.int64, count=len(dd))
        c = np.fromiter(dd.values(), dtype=np.float64, count=len(dd))
        keep = (o % 60_000 == 0) & (np.floor_divide(o, DAY_MS) == (dt.date.fromisoformat(d) - EPOCH).days)
        ops.append(o[keep])
        cls.append(c[keep])
    if not ops:
        return np.zeros(0, dtype=np.int64), np.zeros(0), missing
    o, c = np.concatenate(ops), np.concatenate(cls)
    srt = np.argsort(o, kind="stable")
    return o[srt], c[srt], missing


def prem_vec(opens, closes, ms):
    """`Prem.at` для массива `ms`: ближайшая свеча с open ≤ (ms − 60 с) // 60 с · 60 с, возраст (ms − (open + 60 с)) / 1000 ≤ 300 с."""
    n = len(ms)
    p, a = np.full(n, np.nan), np.full(n, np.nan)
    if len(opens) == 0:
        return p, a
    target = (ms - 60_000) // 60_000 * 60_000
    j = np.searchsorted(opens, target, side="right") - 1
    jc = np.maximum(j, 0)
    age = (ms - (opens[jc] + 60_000)) / 1000.0
    ok = (j >= 0) & ~(age > ft.PREM_MAX_AGE_S)
    return np.where(ok, closes[jc], np.nan), np.where(ok, age, np.nan)


# ---- R: круглое число (по всем строкам) --------------------------------------------------------------------------------------------------------

def round_vec(px_e8, natr_e8):
    """`tk666_feat.round_feat` для массивов (целая арифметика; цифры — по порогам степеней 10)."""
    p = np.abs(np.asarray(px_e8, dtype=np.int64))
    n = np.asarray(natr_e8, dtype=np.int64)
    pow10 = np.array([10 ** k for k in range(19)], dtype=np.int64)
    digits = (p[:, None] >= pow10[None, :]).sum(axis=1)                  # число десятичных цифр p (0 для p ≤ 0)
    ok = (p > 0) & (digits >= 2) & (n > 0)
    d = np.where(ok, digits, 2)
    pp = np.where(ok, p, 10)
    nn = np.where(ok, n, 1)
    step = pow10[d - 2]
    k = (pp + step // 2) // step
    dist = np.abs(pp - k * step)
    rho = dist.astype(np.float64) * 1e8 / (nn.astype(np.float64) * pp.astype(np.float64))
    mant = pp.astype(np.float64) / pow10[d - 1].astype(np.float64)
    return np.where(ok, rho, np.nan), np.where(ok, mant, np.nan)


# ---- C: слияние с 240-минутными уровнями (по монете) ----------------------------------------------------------------------------------------------

def levels_file(win, side):
    return ft.levels_path("t240", win, SIDE_LV[side])


def read_levels_fb(path):
    """{монета: [(born, end, mins[], pxs[], first_bar)]} — как `tk666_feat.read_levels` плюс `first_bar`."""
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            segs = [x.split(":") for x in r["segs"].split("|") if x]
            out.setdefault(r["sym"], []).append((int(r["born"]), int(r["end"]), np.array([int(a) for a, _ in segs], dtype=np.int64),
                                                np.array([abs(int(b)) for _, b in segs], dtype=np.int64), int(r["first_bar"])))
    return out


def conf_vec(levels, m, p, first_bar, own240):
    """(3, n) conf_τ (0/1). `levels` — уровни монеты; m — минута сигнала; p — |цена уровня строки| (int64); own240 — bool: у строки есть источник `tf = 240` (исключить свой уровень)."""
    n = len(m)
    out = np.zeros((len(ft.TAUS), n), dtype=bool)
    for born, end, mins, pxs, fb in levels:
        alive = (m >= born) & (m < end)
        if not alive.any():
            continue
        i = np.searchsorted(mins, m, side="right") - 1
        have = alive & (i >= 0)
        px = pxs[np.maximum(i, 0)]
        if own240 is not None:
            have &= ~(own240 & ((first_bar == fb) | (px == p)))
        diff = np.abs(px - p)
        for j, tau in enumerate(ft.TAUS):
            out[j] |= have & (diff <= tau * p)
    return out.astype(np.float64)


# ---- задание по монете --------------------------------------------------------------------------------------------------------------------------

def _coin_job(job):
    sym, idx, ms, m, p, fb, own, levels = job
    t, rate8, inter = load_funding(sym)
    f8, fage = funding_vec(t, rate8, inter, ms)
    ud = np.unique(np.concatenate([ms, ms - 360_000]) // DAY_MS)
    days = [(EPOCH + dt.timedelta(int(d))).isoformat() for d in ud]
    o, c, miss_days = load_prem(sym, days)
    prem, page = prem_vec(o, c, ms)
    conf = conf_vec(levels, m, p, fb, own) if levels is not None else np.full((len(ft.TAUS), len(ms)), np.nan)
    months = sorted({(EPOCH + dt.timedelta(int(d))).isoformat()[:7] for d in np.unique(ms // DAY_MS)})
    miss_months = [mo for mo in months if mo in ft.MONTHS and fund_path(sym, mo) is None]
    nov = any(mo == "2026-11" for mo in months)
    nov_missing = nov and not os.path.exists(os.path.join(ft.FUND_NEW, sym, "rest-2026-11.json"))
    return idx, dict(f8=f8, f_age_h=fage, prem=prem, prem_age_s=page, conf01=conf[0], conf02=conf[1], conf04=conf[2]), dict(
        sym=sym, funding_missing=miss_months, nov_rest_missing=bool(nov_missing), prem_days_missing=miss_days, fund_n=int(len(t)))


def compute(rows, win, side, procs=3, stats=None):
    import pandas as pd
    for col, want in (("win", win), ("side", side)):
        if col in rows.columns and not (rows[col] == want).all():
            raise ValueError("rows.%s ≠ %s" % (col, want))
    if "row_id" not in rows.columns or not rows["row_id"].is_unique:
        raise ValueError("rows.row_id отсутствует или не уникален")
    n = len(rows)
    ms = rows["abs_ms"].to_numpy(dtype=np.int64)
    if n and int(ms.max()) >= ft.LAST_MS:
        raise SystemExit("охрана: abs_ms ≥ 14.11.2026 — не читается (проверка вперёд)")
    px = np.abs(rows["level_px_e8"].to_numpy(dtype=np.int64))
    natr = rows["natr_e8"].to_numpy(dtype=np.int64)
    rho, mant = round_vec(px, natr)
    sig_ms = rows["sig_ms"].to_numpy(dtype=np.int64) if "sig_ms" in rows.columns else ms - {x.name: x for x in st.TRADE_WINDOWS}[win].t0_ms
    m = sig_ms // 60_000
    fb = rows["first_bar"].to_numpy(dtype=np.int64) if "first_bar" in rows.columns else None
    own = (rows["mask"].to_numpy(dtype=np.int64) & P240_BITS) != 0 if "mask" in rows.columns and fb is not None else None
    lpath = levels_file(win, side)
    levels = read_levels_fb(lpath) if os.path.exists(lpath) else None
    vals = {k: np.full(n, np.nan) for k in COLS}
    vals["rho"], vals["mant"], vals["lvl_px"] = rho, mant, px.astype(np.float64)
    sym = rows["sym"].to_numpy()
    order = np.argsort(sym, kind="stable")
    sy = sym[order]
    cut = np.flatnonzero(sy[1:] != sy[:-1]) + 1
    jobs = []
    for g in np.split(order, cut) if n else []:
        s_ = str(sym[g[0]])
        lv = None if levels is None else levels.get(s_, [])
        jobs.append((s_, g, ms[g], m[g], px[g], None if fb is None else fb[g], None if own is None else own[g], lv))
    jobs.sort(key=lambda j: -len(j[1]))
    if procs > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(procs) as ex:
            results = list(ex.map(_coin_job, jobs, chunksize=2))
    else:
        results = [_coin_job(j) for j in jobs]
    need = dict(funding_coin_months=[], nov_rest_coins=[], prem_coin_days=[])
    for idx, f, info in results:
        for k, v in f.items():
            vals[k][idx] = v
        need["funding_coin_months"] += [[info["sym"], mo] for mo in info["funding_missing"]]
        if info["nov_rest_missing"]:
            need["nov_rest_coins"].append(info["sym"])
        need["prem_coin_days"] += [[info["sym"], d] for d in info["prem_days_missing"]]
    if stats is not None:
        stats.update(coins=len(jobs), levels_file=lpath, levels_file_exists=levels is not None,
                     need_counts={k: len(v) for k, v in need.items()}, need=need)
    return pd.DataFrame(vals, index=pd.Index(rows["row_id"].to_numpy(), name="row_id"))[COLS]


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=3)
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows)
    t = time.time()
    stats = {}
    df = compute(rows, a.win, a.side, a.procs, stats)
    sec = time.time() - t
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    df.to_parquet(a.out)
    need = stats.pop("need")
    info = dict(rows=len(df), seconds=round(sec, 3), ms_per_row=round(1000.0 * sec / max(len(df), 1), 5), procs=a.procs,
                nonnan={c: round(float(df[c].notna().mean()), 6) for c in df.columns}, **stats)
    with open(a.out + ".info.json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=1)
    with open(a.out + ".need.json", "w", encoding="utf-8") as fh:
        json.dump(need, fh, ensure_ascii=False)
    print(json.dumps({k: info[k] for k in ("rows", "seconds", "ms_per_row", "need_counts")}))


if __name__ == "__main__":
    main()
