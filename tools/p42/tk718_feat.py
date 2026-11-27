#!/usr/bin/env python3
"""TK-718 (PS-1037 §4 T6, PS-1038 п. 1): режим по драйверу — признаки сделки по данным ДО момента сигнала, терцили по сделкам 11–31.01.2026, перестановки (20 000, зерно 718), Холм.

  python3 tk718_feat.py features [--out /data/p42-tk718/feat718.npz] [--procs 4]   # стадия 1–2: признаки уникальных сделок B / S1 / S2 / L401 / B лонга / L1 / L2 (1-мин свечи, фандинг, ОИ)
  python3 tk718_feat.py test     [--db …] [--feat …] [--nperm 20000]                # стадия 3: терцили, охват, перестановки, Холм → `tk718_row` (test T6, T6t, T6m, T6cov)

Время сигнала t_sig (мс): окна признаков заканчиваются на последней полной минуте до t_sig (закрытые бары < t_sig // 60000); рыночные значения — по часовой сетке (час, начавшийся не позже сигнала; окна — до конца предыдущего часа).
Свечи — `/data/p42-tk621/m01…m06/klines/<символ>/<дата>.csv` (объединение дат; m01 с 12.12.2025). Пропущенные минуты: закрытие переносится (доходность 0), охват окна ≥ 0,7 минут иначе признак «нет».
Признаки (19): монета — volratio, jag, vr15, vr60, succ30, spread*, depth*, turnover, funding, doi, beta, corr; рынок — btc_volratio, mkt_volratio, btc_vr60, btc_ret24_side, btc_ret7d_side, breadth_side, mkt_corr (* — «нет данных» на январь–июнь, PS-1038)."""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sqlite3
import sys
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, "/data/p42-tk692/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import tk692_walk as W  # noqa: E402
import tk718_tests as T  # noqa: E402

SEED = 718
NPERM = 20000
KL_ROOT = "/data/p42-tk621"
KL_WINS = ("m01", "m02", "m03", "m04", "m05", "m06")
C539 = KL_ROOT + "/universe/symbols-crypto.txt"
FUND = "/data/p42-tk700/dl/fund-all"
OIDIR = "/data/p42-tk700/dl/metrics-all"
FEAT = T.ROOT + "/feat718.npz"
G0_MS = T.D0_MS - 20 * T.DAY_MS                                   # 12.12.2025 00:00 UTC — первая свеча m01
NDAYS_G = 201                                                     # 12.12.2025 … 30.06.2026
NMIN, NH, N5 = NDAYS_G * 1440, NDAYS_G * 24, NDAYS_G * 288
MIN_COV = 0.7                                                     # охват окна минутами; гейт «доля сделок с признаком» — тот же порог
JAN_SIG = (10, 31)                                                # сутки сигнала 11.01–31.01 (21 сутки): границы терцилей
FJ_SIG = (31, 181)
MIN_JAN, MIN_TERC = 90, 30
STOP_PCT = {"short": 4.0, "long": 3.0}                            # `s` ключей членов базы и линий: стоп, % цены (S1 / S2 / L384 / L401 — s=4; L1 / L2, якорь лонга — s=3)
SIGN = {"short": -1.0, "long": 1.0}
COIN_FEATS = ("volratio", "jag", "vr15", "vr60", "succ30", "spread", "depth", "turnover", "funding", "doi", "beta", "corr")
MKT_FEATS = ("btc_volratio", "mkt_volratio", "btc_vr60", "btc_ret24_side", "btc_ret7d_side", "breadth_side", "mkt_corr")
FEATS = COIN_FEATS + MKT_FEATS
NODATA = {"spread": "нет данных на январь–июнь (спред — только с докачки TK-700 вне окна)", "depth": "нет данных: глубина стакана только 14.09–13.11 (PS-1038 п. 1)"}
SERIES = {"short": ("B", "S1", "S2", "L401"), "long": ("B", "L1", "L2")}


# ───────────────────────────── свечи ─────────────────────────────

def load_klines(sym, root=KL_ROOT, wins=KL_WINS):
    """Минутная сетка с G0: (open, high, low, close_ffill, quote_volume, присутствие). Нет данных → NaN; закрытие переносится вперёд от первой свечи монеты."""
    o, h, l, c = (np.full(NMIN, np.nan) for _ in range(4))
    qv = np.zeros(NMIN)
    pres = np.zeros(NMIN, bool)
    seen = set()
    for win in wins:
        d = os.path.join(root, win, "klines", sym)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".csv") or fn.startswith("funding") or fn[:10] in seen:
                continue
            seen.add(fn[:10])
            a = np.loadtxt(os.path.join(d, fn), delimiter=",", skiprows=1, usecols=(0, 1, 2, 3, 4, 7), ndmin=2)
            if a.size == 0:
                continue
            k = ((a[:, 0] - G0_MS) // 60000).astype(np.int64)
            ok = (k >= 0) & (k < NMIN)
            k = k[ok]
            o[k], h[k], l[k], c[k], qv[k] = a[ok, 1], a[ok, 2], a[ok, 3], a[ok, 4], a[ok, 5]
            pres[k] = True
    idx = np.where(pres, np.arange(NMIN), -1)
    idx = np.maximum.accumulate(idx)
    cf = np.where(idx >= 0, c[np.maximum(idx, 0)], np.nan)
    return o, h, l, cf, qv, pres


def csum(x):
    """Накопленные суммы с ведущим нулём: (Σ, число конечных) — окно [a, b) = cs[b] − cs[a]."""
    ok = np.isfinite(x)
    return np.concatenate([[0.0], np.cumsum(np.where(ok, x, 0.0))]), np.concatenate([[0], np.cumsum(ok)]).astype(np.int64)


def wsum(cs, a, b):
    return cs[0][b] - cs[0][a], cs[1][b] - cs[1][a]


def log_ret(c, q=1):
    r = np.full(len(c), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        r[q:] = np.log(c[q:] / c[:-q])
    return r


def var_ratio_at(rq_cs, rq2_cs, r1_cs, r12_cs, i, q, w=4320):
    """VR(q) по последним w минутам до минуты i (исключая i): Var(q-мин доходностей) ÷ (q · Var(1-мин доходностей)); None — охват < 0,7. i — массив индексов."""
    i = np.asarray(i)
    aq, bq = i - w + q, i                                         # доходности q-мин, закончившиеся в минутах [i − w + q, i)
    a1, b1 = i - w + 1, i
    sq, nq = wsum(rq_cs, aq, bq)
    sq2, _ = wsum(rq2_cs, aq, bq)
    s1, n1 = wsum(r1_cs, a1, b1)
    s12, _ = wsum(r12_cs, a1, b1)
    with np.errstate(divide="ignore", invalid="ignore"):
        vq = sq2 / nq - (sq / nq) ** 2
        v1 = s12 / n1 - (s1 / n1) ** 2
        vr = vq / (q * v1)
    ok = (i >= w) & (nq >= MIN_COV * (w - q)) & (n1 >= MIN_COV * (w - 1)) & (v1 > 0)
    return np.where(ok, vr, np.nan)


def trade_success(o, h, l, ei, xi, side, pct):
    """+1R раньше стопа: R = pct % цены входа (открытие минуты входа); минуты ei…xi; обе границы в одной (первой достигнутой) минуте — «не дошёл»; не достигнута +1R — «не дошёл»."""
    out = np.zeros(len(ei))
    for j, (e, x) in enumerate(zip(ei, xi)):
        e, x = int(e), int(x)
        if e < 0 or x >= len(h) or x < e or not np.isfinite(o[e]):
            out[j] = np.nan
            continue
        p0 = o[e]
        hh, ll = h[e:x + 1], l[e:x + 1]
        if side == "long":
            tgt, stp = np.nonzero(hh >= p0 * (1 + pct / 100.0))[0], np.nonzero(ll <= p0 * (1 - pct / 100.0))[0]
        else:
            tgt, stp = np.nonzero(ll <= p0 * (1 - pct / 100.0))[0], np.nonzero(hh >= p0 * (1 + pct / 100.0))[0]
        if len(tgt) == 0:
            out[j] = 0.0
        elif len(stp) == 0:
            out[j] = 1.0
        else:
            out[j] = 1.0 if tgt[0] < stp[0] else 0.0                 # одна минута (== ) → 0
    return out


# ───────────────────────────── фандинг / ОИ ─────────────────────────────

def load_funding(sym, root=FUND):
    """(время мс, ставка) по месячным архивам `<символ>-fundingRate-YYYY-MM.zip` 2025-12…2026-06; пусто → (пустые массивы)."""
    ts, rt = [], []
    d = os.path.join(root, sym)
    if not os.path.isdir(d):
        return np.zeros(0, np.int64), np.zeros(0)
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".zip"):
            continue
        with zipfile.ZipFile(os.path.join(d, fn)) as z:
            for nm in z.namelist():
                for line in z.read(nm).decode().splitlines()[1:]:
                    p = line.split(",")
                    if len(p) >= 3 and p[0].isdigit():
                        ts.append(int(p[0]))
                        rt.append(float(p[2]))
    o = np.argsort(ts)
    return np.array(ts, np.int64)[o], np.array(rt)[o]


def last_before(ts, vals, t, max_age_ms):
    """Последнее значение с временем ≤ t не старше max_age_ms; иначе NaN."""
    if len(ts) == 0:
        return np.full(len(t), np.nan)
    j = np.searchsorted(ts, t, side="right") - 1
    ok = (j >= 0) & (t - ts[np.maximum(j, 0)] <= max_age_ms)
    return np.where(ok, vals[np.maximum(j, 0)], np.nan)


def load_oi(sym, root=OIDIR):
    """(время мс, ОИ в $) из суточных архивов metrics (5-мин); нет каталога — пусто."""
    d = os.path.join(root, sym)
    ts, v = [], []
    if not os.path.isdir(d):
        return np.zeros(0, np.int64), np.zeros(0)
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".zip"):
            continue
        with zipfile.ZipFile(os.path.join(d, fn)) as z:
            for nm in z.namelist():
                for line in z.read(nm).decode().splitlines()[1:]:
                    p = line.split(",")
                    try:
                        t = int(datetime.datetime.strptime(p[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
                        ts.append(t)
                        v.append(float(p[3]))
                    except (ValueError, IndexError):
                        continue
    o = np.argsort(ts)
    return np.array(ts, np.int64)[o], np.array(v)[o]


# ───────────────────────────── стадия 1: по монете ─────────────────────────────

def coin_job(arg):
    """Признаки сделок одной монеты + часовые ряды рынка. arg = (символ, {сторона: (sig_ms, entry_ms, exit_ms)}, корни). Возвращает dict."""
    sym, trades, roots = arg
    o, h, l, c, qv, pres = load_klines(sym, roots.get("kl", KL_ROOT))
    r1 = log_ret(c)
    r1sq = r1 * r1
    cs1, cs12 = csum(r1), csum(r1sq)
    out = dict(sym=sym, have=bool(pres.any()), trades={})
    hh = np.arange(NH) * 60                                       # конец окна (исключая) для часа h: минута 60·h
    ok = hh >= 43200
    with np.errstate(divide="ignore", invalid="ignore"):
        n24, c24 = wsum(cs12, np.maximum(hh - 1440, 0), hh)
        n30, c30 = wsum(cs12, np.maximum(hh - 43200, 0), hh)
        vrat = np.sqrt((n24 / c24) / (n30 / c30))
        vrat = np.where(ok & (c24 >= MIN_COV * 1440) & (c30 >= MIN_COV * 43200) & (n30 > 0), vrat, np.nan)
    ret24, ret7 = np.full(NH, np.nan), np.full(NH, np.nan)
    for arr, lag in ((ret24, 1440), (ret7, 10080)):
        e = hh - 1
        s = hh - 1 - lag
        v = (e >= 0) & (s >= 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            arr[v] = c[e[v]] / c[s[v]] - 1.0
    out["vrat"], out["ret24"], out["ret7"] = vrat.astype(np.float32), ret24.astype(np.float32), ret7.astype(np.float32)
    out["c5"] = c[4::5][:N5].astype(np.float32)                    # закрытие 5-мин бара (последняя минута бара)
    if sym == "BTCUSDT":
        r15, r60 = log_ret(c, 15), log_ret(c, 60)
        vr60h = var_ratio_at(csum(r60), csum(r60 * r60), cs1, cs12, hh, 60)
        out["vr60_grid"] = vr60h.astype(np.float32)
    ft, fr = load_funding(sym, roots.get("fund", FUND))
    ot, ov = load_oi(sym, roots.get("oi", OIDIR))
    r15, r60 = log_ret(c, 15), log_ret(c, 60)
    cs15, cs152, cs60, cs602 = csum(r15), csum(r15 * r15), csum(r60), csum(r60 * r60)
    cq = np.concatenate([[0.0], np.cumsum(qv)])
    cp = np.concatenate([[0], np.cumsum(pres)])
    for side, (sig, ent, ext) in trades.items():
        i = (sig - G0_MS) // 60000
        e, x = (ent - G0_MS) // 60000, (ext - G0_MS) // 60000
        f = {}
        a60, a30 = i - 60, i - 43200
        okw = (a30 >= 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            n60, k60 = wsum(cs12, np.maximum(a60, 0), i)
            n30_, k30 = wsum(cs12, np.maximum(a30, 0), i)
            f["volratio"] = np.where(okw & (k60 >= MIN_COV * 60) & (k30 >= MIN_COV * 43200) & (n30_ > 0), np.sqrt((n60 / k60) / (n30_ / k30)), np.nan)
        jag = np.full(len(i), np.nan)
        for j, ii in enumerate(i):
            if ii >= 60:
                w = r1sq[ii - 60:ii]
                fin = np.isfinite(w)
                if fin.sum() >= MIN_COV * 60 and w[fin].sum() > 0:
                    jag[j] = w[fin].max() / w[fin].sum()
        f["jag"] = jag
        f["vr15"] = var_ratio_at(cs15, cs152, cs1, cs12, i, 15)
        f["vr60"] = var_ratio_at(cs60, cs602, cs1, cs12, i, 60)
        tv = cq[i] - cq[np.maximum(i - 1440, 0)]
        f["turnover"] = np.where((i >= 1440) & ((cp[i] - cp[np.maximum(i - 1440, 0)]) >= MIN_COV * 1440), tv, np.nan)
        f["funding"] = last_before(ft, fr, sig, 9 * 3600 * 1000)
        if len(ot):
            a, b = last_before(ot, ov, sig, 600_000), last_before(ot, ov, sig - 86_400_000, 600_000)
            with np.errstate(divide="ignore", invalid="ignore"):
                f["doi"] = b * 0 + (a / b - 1.0)
        else:
            f["doi"] = np.full(len(i), np.nan)
        out["trades"][side] = dict(f=f, succ=trade_success(o, h, l, e, x, side, STOP_PCT[side]))
    return out


# ───────────────────────────── стадия 2: рынок, бета, корреляция ─────────────────────────────

def mean_pairwise_corr(R, w0, w1, min_coins=20):
    """Средняя попарная корреляция столбцов R[w0:w1] (монеты с полным окном и σ > 0): (Σ_t s_t² / (T − 1) − n) / (n (n − 1)), s_t = Σ_i z_ti. nan, если монет < min_coins."""
    A = R[w0:w1].astype(np.float64)
    ok = np.isfinite(A).all(axis=0)
    A = A[:, ok]
    if A.shape[1] < min_coins:
        return float("nan"), A.shape[1]
    sd = A.std(axis=0, ddof=1)
    A = A[:, sd > 1e-12]
    n = A.shape[1]
    if n < min_coins:
        return float("nan"), n
    z = (A - A.mean(axis=0)) / A.std(axis=0, ddof=1)
    s = z.sum(axis=1)
    return float(((s * s).sum() / (A.shape[0] - 1) - n) / (n * (n - 1))), n


def corr_beta(rc, rb):
    """(бета, корреляция) доходностей монеты к BTC на общих конечных точках; охват < 0,7 — NaN."""
    ok = np.isfinite(rc) & np.isfinite(rb)
    if ok.sum() < MIN_COV * len(rc):
        return float("nan"), float("nan")
    x, y = rb[ok], rc[ok]
    vx, vy = x.var(), y.var()
    if vx <= 0 or vy <= 0:
        return float("nan"), float("nan")
    cv = ((x - x.mean()) * (y - y.mean())).mean()
    return float(cv / vx), float(cv / math.sqrt(vx * vy))


def market_grid(res, c539):
    """Часовые ряды рынка по результатам стадии 1: btc_volratio, mkt_volratio (среднее по c539), btc_vr60, btc_ret24, btc_ret7d, breadth_up, mkt_corr."""
    have = [r for r in res if r["have"] and r["sym"] in c539]
    V = np.stack([r["vrat"] for r in have]).astype(np.float64)
    R24 = np.stack([r["ret24"] for r in have]).astype(np.float64)
    with np.errstate(invalid="ignore"):
        mkt_vr = np.nanmean(V, axis=0)
        fin = np.isfinite(R24)
        breadth = np.where(fin.sum(axis=0) >= 20, ((R24 > 0) & fin).sum(axis=0) / np.maximum(fin.sum(axis=0), 1), np.nan)
    btc = next((r for r in res if r["sym"] == "BTCUSDT" and r["have"]), None)
    g = dict(mkt_volratio=mkt_vr, breadth_up=breadth)
    if btc is not None:
        g.update(btc_volratio=btc["vrat"].astype(np.float64), btc_vr60=btc.get("vr60_grid", np.full(NH, np.nan)).astype(np.float64),
                 btc_ret24=btc["ret24"].astype(np.float64), btc_ret7=btc["ret7"].astype(np.float64))
    C5 = np.stack([r["c5"] for r in have]).astype(np.float64).T          # (N5, монет)
    with np.errstate(divide="ignore", invalid="ignore"):
        R5 = np.log(C5[1:] / C5[:-1])
    R5 = np.vstack([np.full((1, R5.shape[1]), np.nan), R5])
    mc = np.full(NH, np.nan)
    for hr in range(72, NH):
        mc[hr] = mean_pairwise_corr(R5, 12 * hr - 864, 12 * hr)[0]
    g["mkt_corr"] = mc
    return g, have, R5


def run_features(trades_by_side, out_path, procs=4, roots=None, c539=None):
    """trades_by_side: {сторона: dict(sym, sig, ent, ext) массивы УНИКАЛЬНЫХ сделок}; пишет npz: признаки на каждую сделку."""
    roots = roots or {}
    c539 = c539 if c539 is not None else set(open(C539).read().strip().split(","))
    syms = sorted(set(c539))
    per = {s: {} for s in syms}
    for side, t in trades_by_side.items():
        for s in set(t["sym"]):
            m = t["sym"] == s
            per[s][side] = (t["sig"][m], t["ent"][m], t["ext"][m])
    jobs = [(s, per[s], roots) for s in syms]
    if procs > 1:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(procs) as pool:
            res = list(pool.imap_unordered(coin_job, jobs, chunksize=2))
    else:
        res = [coin_job(j) for j in jobs]
    res.sort(key=lambda r: r["sym"])
    g, have, R5 = market_grid(res, c539)
    ix = {r["sym"]: i for i, r in enumerate(have)}
    btc_i = ix.get("BTCUSDT")
    out = {}
    byname = {r["sym"]: r for r in res}
    for side, t in trades_by_side.items():
        n = len(t["sym"])
        F = {k: np.full(n, np.nan) for k in FEATS}
        succ = np.full(n, np.nan)
        for s in set(t["sym"]):
            m = np.nonzero(t["sym"] == s)[0]
            r = byname[s]["trades"].get(side)
            if r is None:
                continue
            for k, v in r["f"].items():
                F[k][m] = v
            succ[m] = r["succ"]
        sig = t["sig"]
        hidx = np.clip((sig - G0_MS) // 3_600_000, 0, NH - 1)
        sgn = SIGN[side]
        for k, gk in (("btc_volratio", "btc_volratio"), ("mkt_volratio", "mkt_volratio"), ("btc_vr60", "btc_vr60"), ("mkt_corr", "mkt_corr")):
            if gk in g:
                F[k] = g[gk][hidx]
        if "btc_ret24" in g:
            F["btc_ret24_side"], F["btc_ret7d_side"] = sgn * g["btc_ret24"][hidx], sgn * g["btc_ret7"][hidx]
        F["breadth_side"] = g["breadth_up"][hidx] if side == "long" else 1.0 - g["breadth_up"][hidx]
        if btc_i is not None:
            rb = R5[:, btc_i]
            for j in range(n):
                s = t["sym"][j]
                if s not in ix:
                    continue
                k5 = int((sig[j] - G0_MS) // 300_000)
                if k5 >= 864:
                    F["beta"][j], F["corr"][j] = corr_beta(R5[k5 - 864:k5, ix[s]], rb[k5 - 864:k5])
        out["%s__succ" % side] = succ
        for k in FEATS:
            out["%s__%s" % (side, k)] = F[k]
        for k in ("sym", "sig", "ent", "ext"):
            out["%s__t_%s" % (side, k)] = np.asarray(t[k])
    for k, v in g.items():
        out["grid__" + k] = np.asarray(v)
    np.savez_compressed(out_path, **out)
    return out


# ───────────────────────────── стадия 3: терцили, перестановки, Холм ─────────────────────────────

def terciles(v_jan):
    """Границы терцилей по январским значениям (33,3 и 66,7 процентиль)."""
    return np.percentile(v_jan, [100.0 / 3.0, 200.0 / 3.0])


def labels(v, q):
    """0 / 1 / 2 — терцили по границам q (v ≤ q1; ≤ q2; выше); 3 — признак отсутствует."""
    lab = np.digitize(v, q, right=True)
    return np.where(np.isfinite(v), lab, 3).astype(np.int64)


def tercile_stats(lab, val):
    """По терцилям: n, Σ, среднее; spread = max − min среднего (None, если терциль < 30 сделок)."""
    n = np.bincount(lab, minlength=4)[:3]
    s = np.bincount(lab, weights=val, minlength=4)[:3]
    if (n < MIN_TERC).any():
        return n, s, None
    m = s / n
    return n, s, float(m.max() - m.min())


def perm_spreads_coin(lab_mat, val, day, nperm, rng):
    """Нуль признаков монеты: метки терцилей перемешаны внутри суток (одна перестановка на итерацию — общая для всех признаков). lab_mat (F, n) → (nperm, F) разброс средних (терцили < 30 сделок не бывают: размеры сохраняются)."""
    F, n = lab_mat.shape
    out = np.empty((nperm, F))
    off = (np.arange(F) * 4)[:, None]
    w = np.tile(val, F)
    for it in range(nperm):
        pi = np.argsort(day + rng.random(n), kind="stable")
        idx = (lab_mat[:, pi] + off).ravel()
        s = np.bincount(idx, weights=w, minlength=4 * F).reshape(F, 4)[:, :3]
        c = np.bincount(idx, minlength=4 * F).reshape(F, 4)[:, :3]
        with np.errstate(divide="ignore", invalid="ignore"):
            m = np.where(c > 0, s / c, np.nan)
        out[it] = np.nanmax(m, axis=1) - np.nanmin(m, axis=1)
    return out


def perm_spreads_market(grid_lab, hidx, val, h_lo, h_hi, nperm, rng):
    """Нуль рыночных признаков: циклический сдвиг часовой сетки на случайное целое число суток (1…n_сут − 1 в FJ). Каждый сдвиг считается один раз; 20 000 розыгрышей — выбор из них. grid_lab (F, NH) метки сетки → (nperm, F)."""
    nd = (h_hi - h_lo) // 24
    F = grid_lab.shape[0]
    shifts = rng.integers(1, nd, nperm)
    cache = {}
    off = (np.arange(F) * 4)[:, None]
    w = np.tile(val, F)
    rel = hidx - h_lo
    for s in np.unique(shifts):
        hs = h_lo + (rel + 24 * int(s)) % (h_hi - h_lo)
        lab = grid_lab[:, hs]
        idx = (lab + off).ravel()
        sm = np.bincount(idx, weights=w, minlength=4 * F).reshape(F, 4)[:, :3]
        c = np.bincount(idx, minlength=4 * F).reshape(F, 4)[:, :3]
        with np.errstate(divide="ignore", invalid="ignore"):
            m = np.where(c >= MIN_TERC, sm / c, np.nan)
        cache[int(s)] = np.where(np.isfinite(m).sum(axis=1) >= 2, np.nanmax(m, axis=1) - np.nanmin(m, axis=1), 0.0)
    return np.stack([cache[int(s)] for s in shifts])


def episodes(ind, bridge=3, min_len=10):
    """Число независимых эпизодов верхнего терциля: серии подряд идущих суток, разрывы ≤ bridge склеиваются, берутся серии длиной ≥ min_len (включая склеенные разрывы)."""
    runs, start = [], None
    for i, v in enumerate(ind):
        if v and start is None:
            start = i
        if (not v) and start is not None:
            runs.append([start, i - 1])
            start = None
    if start is not None:
        runs.append([start, len(ind) - 1])
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] - 1 <= bridge:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return sum(1 for a, b in merged if b - a + 1 >= min_len)


def series_trades(S, side, name):
    """Сделки ряда: dict(sym, sig, ent, ext, val); B — сделки 9 членов с весом 1/9; нет файлов — None."""
    if name == "B":
        parts = []
        for nm in S.members(side):
            tr = T.cell_trades(S.plan[side, S.ckey(side, nm)]["cid"])
            if tr is None:
                return None
            parts.append(dict(tr, val=tr["val"] / 9.0))
        if not parts:
            return None
        return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    ck = S.ckey(side, name)
    return None if ck is None else T.cell_trades(S.plan[side, ck]["cid"])


def collect_trades(S):
    """Все ряды T6 и уникальные сделки по сторонам: ({(сторона, ряд): сделки}, {сторона: уникальные (sym, sig, ent, ext)})."""
    sers, uniq = {}, {}
    for side, names in SERIES.items():
        keys = {}
        for nm in names:
            tr = series_trades(S, side, nm)
            if tr is None:
                continue
            sers[side, nm] = tr
            for j in range(len(tr["sym"])):
                keys[(tr["sym"][j], int(tr["sig_ms"][j]), int(tr["entry_ms"][j]), int(tr["exit_ms"][j]))] = None
        if keys:
            ks = list(keys)
            uniq[side] = dict(sym=np.array([k[0] for k in ks], dtype=object), sig=np.array([k[1] for k in ks], np.int64), ent=np.array([k[2] for k in ks], np.int64), ext=np.array([k[3] for k in ks], np.int64))
    return sers, uniq


def attach(F, side, tr):
    """Признаки из npz на сделки ряда (по ключу sym, sig, ent, ext) → dict признак → массив; succ30 — в `series_succ`."""
    key = {(F["%s__t_sym" % side][j], int(F["%s__t_sig" % side][j]), int(F["%s__t_ent" % side][j]), int(F["%s__t_ext" % side][j])): j for j in range(len(F["%s__t_sym" % side]))}
    ix = np.array([key[(tr["sym"][j], int(tr["sig_ms"][j]), int(tr["entry_ms"][j]), int(tr["exit_ms"][j]))] for j in range(len(tr["sym"]))], np.int64)
    feats = {k: F["%s__%s" % (side, k)][ix] for k in FEATS}
    return feats, F["%s__succ" % side][ix]


def succ30(tr, succ):
    """Признак (4): доля пробоев той же монеты за 30 сут. до сигнала с выходом СТРОГО раньше сигнала, дошедших до +1R; < 3 таких сделок — NaN («без истории»)."""
    out = np.full(len(tr["sym"]), np.nan)
    for s in set(tr["sym"]):
        m = np.nonzero(tr["sym"] == s)[0]
        ex, sg, sc = tr["exit_ms"][m], tr["sig_ms"][m], succ[m]
        for a, j in enumerate(m):
            lo = sg[a] - 30 * T.DAY_MS
            sel = (ex < sg[a]) & (ex >= lo) & np.isfinite(sc)
            if sel.sum() >= 3:
                out[j] = sc[sel].mean()
    return out


def holm_rows(pairs):
    """Холм по всем (признак × ряд) с данными: список (ключ, p) → {ключ: (rank, alpha_j, прошла)}."""
    keys = [k for k, _ in pairs]
    ps = np.array([p for _, p in pairs])
    hm = W.holm(list(ps))
    order = np.argsort(ps, kind="stable")
    rank = {int(i): j for j, i in enumerate(order, start=1)}
    return {keys[i]: (rank[i], hm[i][1], hm[i][0]) for i in range(len(keys))}


def t6_test(S, F, rows, nperm=NPERM, seed=SEED):
    """Стадия 3: по каждому ряду и признаку — охват, терцили (январь), разброс средних на FJ, нуль, p; затем Холм."""
    sers, _uniq = collect_trades(S)
    pairs, detail = [], {}
    rng = np.random.default_rng([seed, 6])
    for (side, name), tr in sers.items():
        feats, succ = attach(F, side, tr)
        feats["succ30"] = succ30(tr, succ)
        sday = (tr["sig_ms"] - T.D0_MS) // T.DAY_MS
        jan = (sday >= JAN_SIG[0]) & (sday < JAN_SIG[1])
        fj = (sday >= FJ_SIG[0]) & (sday < FJ_SIG[1])
        label = "%s-%s" % (side, name)
        rows.append(("T6", side, name, "Jan", "n_trades", float(jan.sum()), "сигналы 11–31.01"))
        rows.append(("T6", side, name, "FJ", "n_trades", float(fj.sum()), None))
        if jan.sum() < MIN_JAN:
            rows.append(("T6", side, name, "ALL", "nodata", float("nan"), "в январе %d сделок < %d — все признаки «нет данных» (PS-1038)" % (jan.sum(), MIN_JAN)))
            continue
        val = tr["val"][fj]
        day = sday[fj].astype(np.float64)
        order = np.argsort(day, kind="stable")                                 # сутки по возрастанию: метки внутри суток перемешиваются на месте
        val_s, day_s = val[order], day[order]
        month = np.searchsorted(np.array(W.MONTH_START), sday[fj][order], side="right") - 1
        coin_set, coin_lab, coin_q, mkt_set, mkt_lab = [], [], {}, [], []
        for fk in FEATS:
            if fk in NODATA:
                rows.append(("T6cov", side, "%s:%s" % (name, fk), "ALL", "share", float("nan"), NODATA[fk]))
                continue
            v = feats[fk]
            vj = v[jan]
            if fk in MKT_FEATS or fk in ("breadth_side",):
                pass
            cov_m = []
            for mi in range(1, 6):
                mk = fj & (np.searchsorted(np.array(W.MONTH_START), sday, side="right") - 1 == mi)
                cov_m.append(float(np.isfinite(v[mk]).mean()) if mk.sum() else float("nan"))
                rows.append(("T6cov", side, "%s:%s" % (name, fk), W.MONTHS[mi], "share", cov_m[-1], None))
            cov_ok = all(c == c and c >= MIN_COV for c in cov_m)
            if (not cov_ok) or np.isfinite(vj).sum() < MIN_JAN:
                rows.append(("T6", side, "%s:%s" % (name, fk), "FJ", "nodata", float("nan"), "охват < %.1f (по месяцам %s) или январских значений < %d" % (MIN_COV, ["%.2f" % c for c in cov_m], MIN_JAN)))
                continue
            q = terciles(vj[np.isfinite(vj)])
            lab = labels(v[fj][order], q)
            n, s, spread = tercile_stats(lab, val_s)
            if spread is None:
                rows.append(("T6", side, "%s:%s" % (name, fk), "FJ", "nodata", float("nan"), "терциль < %d сделок: %s" % (MIN_TERC, list(map(int, n)))))
                continue
            detail[side, name, fk] = dict(n=n, s=s, spread=spread, q=q, lab=lab, v=v)
            if fk in MKT_FEATS:
                mkt_set.append(fk)
            else:
                coin_set.append(fk)
                coin_lab.append(lab)
        null = {}
        if coin_set:
            null.update({k: c for k, c in zip(coin_set, perm_spreads_coin(np.stack(coin_lab), val_s, day_s, nperm, rng).T)})
        if mkt_set:
            h_lo, h_hi = int(FJ_SIG[0] * 24 + 480), int(FJ_SIG[1] * 24 + 480)   # час-индекс от G0: 20 суток сдвига + сутки FJ
            gk = {"btc_volratio": "grid__btc_volratio", "mkt_volratio": "grid__mkt_volratio", "btc_vr60": "grid__btc_vr60", "mkt_corr": "grid__mkt_corr"}
            glabs = []
            for fk in mkt_set:
                q = detail[side, name, fk]["q"]
                if fk in gk:
                    grid = F[gk[fk]]
                elif fk == "btc_ret24_side":
                    grid = SIGN[side] * F["grid__btc_ret24"]
                elif fk == "btc_ret7d_side":
                    grid = SIGN[side] * F["grid__btc_ret7"]
                else:
                    grid = F["grid__breadth_up"] if side == "long" else 1.0 - F["grid__breadth_up"]
                detail[side, name, fk]["grid"] = grid
                glabs.append(labels(grid, q))
            hidx = np.clip((tr["sig_ms"][fj][order] - G0_MS) // 3_600_000, 0, NH - 1).astype(np.int64)
            sp = perm_spreads_market(np.stack(glabs), hidx, val_s, h_lo, h_hi, nperm, rng)
            null.update({k: c for k, c in zip(mkt_set, sp.T)})
        for fk, nl in null.items():
            d = detail[side, name, fk]
            p = float(((nl >= d["spread"] - 1e-12).sum() + 1) / (len(nl) + 1))
            d["p"] = p
            pairs.append(((side, name, fk), p))
    hm = holm_rows(pairs) if pairs else {}
    for (side, name, fk), d in detail.items():
        key = "%s:%s" % (name, fk)
        rk, aj, ok = hm.get((side, name, fk), (None, None, None))
        rows += [("T6", side, key, "FJ", "spread", d["spread"], None), ("T6", side, key, "FJ", "p", d["p"], "p-floor 1/(R+1) = %.2e" % (1.0 / (nperm + 1))),
                 ("T6", side, key, "FJ", "holm_rank", float(rk), None), ("T6", side, key, "FJ", "holm_alpha", float(aj), None), ("T6", side, key, "FJ", "holm_pass", float(ok), None),
                 ("T6", side, key, "FJ", "q1", float(d["q"][0]), None), ("T6", side, key, "FJ", "q2", float(d["q"][1]), None)]
        tot = float(d["s"].sum())
        for k in range(3):
            rows += [("T6t", side, key, "T%d" % (k + 1), "n", float(d["n"][k]), None), ("T6t", side, key, "T%d" % (k + 1), "sum", float(d["s"][k]), None),
                     ("T6t", side, key, "T%d" % (k + 1), "per_trade", float(d["s"][k] / d["n"][k]), None)]
        rows.append(("T6", side, key, "FJ", "top_share_of_sum", float(d["s"][2] / tot) if tot > 0 else float("nan"), "Σ верхнего терциля / Σ_FJ (при Σ_FJ > 0)"))
        if fk in MKT_FEATS:
            grid = d["grid"]
            days = np.arange(FJ_SIG[0], FJ_SIG[1])
            dm = np.array([np.nanmean(grid[(dd + 20) * 24:(dd + 21) * 24]) for dd in days])
            top = dm > d["q"][1]
            for mi in range(1, 6):
                mm = (days >= W.MONTH_START[mi]) & (days < W.MONTH_START[mi + 1])
                rows.append(("T6m", side, key, W.MONTHS[mi], "top_days_share", float(top[mm].mean()), None))
            rows.append(("T6m", side, key, "FJ", "episodes", float(episodes(top)), "серии подряд ≥ 10 суток, разрывы ≤ 3 склеены"))
    # «живёт в одном режиме» (описательно): верхний терциль несёт ≥ 0,6 Σ_FJ при p_Холм ≤ 0,05 у B и у линии стороны
    for side, names in SERIES.items():
        for fk in FEATS:
            def ok(nm):
                d = detail.get((side, nm, fk))
                if d is None or (side, nm, fk) not in hm:
                    return None
                tot = float(d["s"].sum())
                return bool(hm[side, nm, fk][2] and hm[side, nm, fk][1] and tot > 0 and d["s"][2] / tot >= 0.6)
            ob = ok("B")
            for nm in names[1:]:
                of = ok(nm)
                if ob is not None and of is not None:
                    rows.append(("T6", side, "%s:%s" % (nm, fk), "FJ", "one_regime", float(ob and of), "верхний терциль ≥ 0,6 Σ_FJ при p_Холм ≤ 0,05 и у B, и у %s" % nm))
    return detail, hm


# ───────────────────────────── CLI ─────────────────────────────

def cmd_features(a):
    S = T.Store(a.db)
    _sers, uniq = collect_trades(S)
    out = run_features(uniq, a.out, procs=a.procs)
    print("признаки: сделок %s → %s (%d массивов)" % ({s: len(u["sym"]) for s, u in uniq.items()}, a.out, len(out)))
    return 0


def cmd_test(a):
    S = T.Store(a.db)
    F = dict(np.load(a.feat, allow_pickle=True))
    rows = []
    detail, hm = t6_test(S, F, rows, a.nperm)
    for side in SERIES:
        n = sum(1 for k in hm if k[0] == side)
        sig = sum(1 for k, v in hm.items() if k[0] == side and v[2])
        print("T6 %s: испытаний с данными %d, проходят Холм %d" % (side, n, sig))
    con = sqlite3.connect(a.db, timeout=120)
    con.executescript(T.TABLES)
    for t in ("T6", "T6t", "T6m", "T6cov"):
        con.execute("DELETE FROM tk718_row WHERE test=?", (t,))
    con.executemany("INSERT OR REPLACE INTO tk718_row VALUES(?,?,?,?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO tk718_run VALUES('t6', ?, ?, ?)", (json.dumps(dict(seed=SEED, nperm=a.nperm)), T.git_head(), datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    con.close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("features")
    s.add_argument("--db", default=T.DB)
    s.add_argument("--out", default=FEAT)
    s.add_argument("--procs", type=int, default=4)
    s = sp.add_parser("test")
    s.add_argument("--db", default=T.DB)
    s.add_argument("--feat", default=FEAT)
    s.add_argument("--nperm", type=int, default=NPERM)
    a = ap.parse_args(argv)
    return {"features": cmd_features, "test": cmd_test}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
