#!/usr/bin/env python3
"""TK-735, §13.79.1: свойства монет P1…P4, ворота G-prop, типы (k-means), деления «корзина / двойник», случайные монеты (нуль Б). P&L не читается.

  tk735_props.py extract          # по каждой монете c539: суточные ряды → res/daily.npz; признаки φ на моментах сделок базы → res/trade_feat.npz (P&L не читается)
  tk735_props.py props            # P1–P3, G-prop
  tk735_props.py p4               # P4 (метки y_n в RV60, L-526 / L-527): усадка, G-prop → res/props_p4.npz
  tk735_props.py types            # типы по годным свойствам, сплиты → БД, заморозка
  tk735_props.py join             # признаки φ1…φ11 на моментах сделок базы (после заморозки корзин)

Свойства — на первый день режима 00:00 UTC по 30 суткам до него (FJ: 02.01–31.01, SEL: 01.06–30.06); окна G-prop — §13.79.1.
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk727_lib as L  # noqa: E402
import tk728_lib as K8  # noqa: E402  (mom_beta_binomial, shrunk_share: усадка P4)
import tk735_lib as T  # noqa: E402

RES = os.path.join(T.ROOT, "res")
ND = L.NDAYS


def dday(m):  # индекс суток сетки G0 от 12.12.2025 → сутки от 01.01.2026 (минус DAY0)
    return m // 1440 - L.DAY0


# ───────────────────────────── суточные ряды и признаки на моментах сделок ─────────────────────────────

G = {}


def extract_task(sym):
    """Суточные ряды монеты по суткам сетки G0 (348): сумма RV60 (bp) и число определённых минут, оборот, число минут, закрытие последней минуты, сумма закрытий, НОД цен ×1e8;
    плюс признаки φ на закрытых минутах сделок этой монеты и r60 на всех минутах U (для медианы по рынку, φ7). P&L не используется."""
    S = T.load_kl(sym)
    rv = L.rv_sqrt_sum(S, 60) * 1e4
    fin = np.isfinite(rv)
    n = ND
    rv_sum, rv_n, qv, pn, csum = (np.zeros(n) for _ in range(5))
    cl = np.full(n, np.nan)
    gcd = np.zeros(n, np.int64)
    for d in range(n):
        a, b = d * 1440, (d + 1) * 1440
        p = S["pres"][a:b]
        f = fin[a:b]
        rv_sum[d] = float(rv[a:b][f].sum())
        rv_n[d] = float(f.sum())
        qv[d] = float(S["qv"][a:b].sum())
        pn[d] = float(p.sum())
        if p.any():
            cl[d] = S["c"][a:b][p][-1]
            csum[d] = float(S["c"][a:b][p].sum())
            v = np.concatenate([S[k][a:b][p] for k in ("o", "h", "l", "c")])
            iv = np.rint(v * 1e8).astype(np.int64)
            iv = iv[iv > 0]
            if len(iv):
                gcd[d] = int(np.gcd.reduce(iv))
    idx, tc = G["by"].get(sym, (np.zeros(0, np.int64), np.zeros(0, np.int64)))
    f = T.feat_at(S, tc) if len(tc) else {}
    r60u = T.r60_at(S, G["U"])
    return sym, dict(rv_sum=rv_sum, rv_n=rv_n, qv=qv, pn=pn, cl=cl, csum=csum, gcd=gcd), idx, f, r60u


def cmd_extract(a):
    syms = T.read_syms()
    u = T.unique_trades()
    n = len(u["sym"])
    by = {}
    for i in range(n):
        by.setdefault(str(u["sym"][i]), []).append(i)
    G["by"] = {s: (np.array(v, np.int64), u["tc"][np.array(v)]) for s, v in by.items()}
    G["U"] = np.unique(u["tc"])
    print("уникальных сделок %d (все окна), монет с сделками %d, моментов U %d" % (n, len(by), len(G["U"])), flush=True)
    t0 = time.time()
    D, F, R = {}, {}, {}
    keys = ("V", "T24", "SURGE", "SIZE", "SELL", "CALM", "ROUND", "NTR", "TURN1", "r60")
    X = {k: np.full(n, np.nan) for k in keys}
    with mp.Pool(a.procs) as pool:
        for i, (s, d, idx, f, r60u) in enumerate(pool.imap_unordered(extract_task, syms, chunksize=2), 1):
            D[s] = d
            R[s] = r60u
            for k in keys:
                if len(idx):
                    X[k][idx] = f[k]
            if i % 50 == 0:
                print("[%d/%d] %.0f с" % (i, len(syms), time.time() - t0), flush=True)
    np.savez_compressed(os.path.join(RES, "daily.npz"), syms=np.array(syms), **{k: np.array([D[s][k] for s in syms]) for k in ("rv_sum", "rv_n", "qv", "pn", "cl", "csum", "gcd")})
    Rm = np.array([R[s] for s in syms])                                 # (монет, U)
    ncoin = np.isfinite(Rm).sum(0)
    with np.errstate(all="ignore"):
        med = np.where(ncoin >= 100, np.nanmedian(Rm, axis=0), np.nan)
    pos = np.searchsorted(G["U"], u["tc"])
    weak = -(X["r60"] - med[pos])
    X["WEAK"] = weak
    X["HOUR"] = T.hour_bucket(u["m"])
    X["n_mkt"] = ncoin[pos].astype(np.float64)
    meta = {k: u[k] for k in ("sym", "sig_ms", "eday", "regime", "in_anchor", "tc", "m")}
    np.savez_compressed(os.path.join(RES, "trade_feat.npz"), **meta, **X)
    cov = {k: round(float(np.isfinite(X[k]).mean()), 4) for k in ("V", "T24", "SURGE", "SIZE", "SELL", "CALM", "ROUND", "NTR", "TURN1", "WEAK")}
    print("extract готово: %d монет за %.0f с; охват признаков %s" % (len(syms), time.time() - t0, json.dumps(cov)))


# ───────────────────────────── свойства ─────────────────────────────

def window_props(D, d0, d1, min_rv_min=14400):
    """P1, P2, P3 по суткам [d0, d1) от 01.01.2026: P1 — среднее RV60 (bp) по минутам; P2 — log10 среднего суточного оборота по суткам с минутами; P3 — шаг цены / P1 (оба в bp). → dict массивов по монетам (NaN — свойства нет)."""
    g0, g1 = d0 + L.DAY0, d1 + L.DAY0
    rv_sum, rv_n = D["rv_sum"][:, g0:g1].sum(1), D["rv_n"][:, g0:g1].sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        p1 = np.where(rv_n >= min_rv_min, rv_sum / rv_n, np.nan)
        days = (D["pn"][:, g0:g1] > 0)
        qv = np.where(days, D["qv"][:, g0:g1], np.nan)
        qm = np.nanmean(np.where(days, qv, np.nan), axis=1)
        p2 = np.where(np.isfinite(qm) & (qm > 0) & np.isfinite(p1), np.log10(qm), np.nan)
        csum, pn = D["csum"][:, g0:g1].sum(1), D["pn"][:, g0:g1].sum(1)
        price = csum / pn
        gg = D["gcd"][:, g0:g1]
        tick = np.zeros(len(gg), np.int64)
        for i in range(len(gg)):
            v = gg[i][gg[i] > 0]
            tick[i] = int(np.gcd.reduce(v)) if len(v) else 0
        tick_bp = np.where(tick > 0, tick * 1e-8 / price * 1e4, np.nan)
        p3 = np.where(np.isfinite(p1) & (p1 > 0), tick_bp / p1, np.nan)
    return dict(P1=p1, P2=p2, P3=p3, tick_bp=tick_bp)


def sb(r):
    """Поправка Спирмена–Брауна для корреляции половин."""
    return 2 * r / (1 + r)


def spearman(x, y):
    from scipy.stats import spearmanr

    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 5:
        return float("nan"), int(ok.sum())
    return float(spearmanr(x[ok], y[ok]).statistic), int(ok.sum())


def prop_windows(regime):
    """Окно формирования и окна G-prop: SEL — [151, 181) против [121, 151); FJ — формирование [1, 31) и половины [1, 16) / [16, 31) (Спирмен–Браун)."""
    if regime == "SEL":
        return dict(form=(151, 181), gprop=[("prev30", (121, 151), None)], note="SEL: 01.06–30.06 против 02.05–31.05")
    return dict(form=(1, 31), gprop=[("halves", (1, 16), (16, 31))], note="FJ: половины 02–16.01 / 17–31.01 с поправкой Спирмена–Брауна")


def cmd_props(a):
    z = np.load(os.path.join(RES, "daily.npz"), allow_pickle=False)
    syms = [str(s) for s in z["syms"]]
    D = {k: z[k] for k in ("rv_sum", "rv_n", "qv", "pn", "cl", "csum", "gcd")}
    out = {}
    gp = {}
    for reg in ("FJ", "SEL"):
        w = prop_windows(reg)
        f0, f1 = w["form"]
        P = window_props(D, f0, f1)
        info = dict(window=w["note"], n_valid={k: int(np.isfinite(P[k]).sum()) for k in ("P1", "P2", "P3")})
        gate = {}
        for name, wa_, wb_ in w["gprop"]:
            if reg == "SEL":
                Q = window_props(D, *wa_)
                for k in ("P1", "P2", "P3"):
                    r, n = spearman(P[k], Q[k])
                    gate[k] = dict(rho=r, n=n, passed=bool(r >= 0.5))
            else:
                A, B = window_props(D, *wa_), window_props(D, *wb_)
                for k in ("P1", "P2", "P3"):
                    r, n = spearman(A[k], B[k])
                    rsb = sb(r)
                    gate[k] = dict(rho_half=r, rho_sb=rsb, n=n, passed=bool(rsb >= 0.5))
        info["gprop"] = gate
        out[reg] = dict(P=P, info=info)
        gp[reg] = info
    json.dump(gp, open(os.path.join(RES, "g_prop_p123.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for reg in gp:
        print(reg, json.dumps(gp[reg], ensure_ascii=False))
    np.savez(os.path.join(RES, "props_p123.npz"), syms=np.array(syms), **{"%s_%s" % (reg, k): out[reg]["P"][k] for reg in out for k in ("P1", "P2", "P3", "tick_bp")})


# ───────────────────────────── P4: доля пробоев с усадкой (поправки L-526, L-527) ─────────────────────────────

DAY_MS = 86_400_000
P4_WIN = {"SEL": ("lab-rv60-w0.csv", 1781654400000), "FJ": ("lab-rv60-m01.csv", 1767312000000)}   # файл меток, начало окна (SEL 17.06, FJ 02.01 00:00 UTC)


def p4_counts(lab, syms, lo_ms):
    """k, n по монетам (порядок `syms`) за всё окно, нечётные и чётные сутки окна (первые сутки = 1, дата UTC `sig_abs`). Берутся подходы с `ok_t60 = 1` и непустой меткой."""
    ok = (lab["ok_t60"].to_numpy() == 1) & np.isfinite(lab["y_n_rv60"].to_numpy(dtype=np.float64))
    sym = lab["sym"].to_numpy()[ok]
    y = lab["y_n_rv60"].to_numpy(dtype=np.float64)[ok].astype(np.int64)
    day = (lab["sig_abs_ms"].to_numpy(dtype=np.int64)[ok] - lo_ms) // DAY_MS + 1
    pos = {s_: i for i, s_ in enumerate(syms)}
    out = {}
    for name, sel in (("all", np.ones(len(y), bool)), ("odd", day % 2 == 1), ("even", day % 2 == 0)):
        k, n = np.zeros(len(syms), np.int64), np.zeros(len(syms), np.int64)
        for s_, yy in zip(sym[sel], y[sel]):
            i = pos.get(str(s_))
            if i is not None:
                n[i] += 1
                k[i] += int(yy)
        out[name] = (k, n)
    return out


def p4_shrink(k, n, adm):
    """p̂ = (k + m·p₀)/(n + m) по допущенным монетам; p₀, m — метод моментов по монетам с n ≥ 1 (L-527 п. 3). Нет сверхдисперсии (ρ ≤ 0 или не определено) → `defined = False`, p̂ = nan."""
    use = adm & (n >= 1)
    info = dict(n_coins_adm=int(adm.sum()), n_coins_n1=int(use.sum()), k_sum=int(k[use].sum()), n_sum=int(n[use].sum()))
    if use.sum() < 3 or info["k_sum"] == 0 or info["k_sum"] == info["n_sum"]:
        return np.full(len(k), np.nan), dict(info, defined=False, why="мало монет или все доли 0 / 1")
    mm = K8.mom_beta_binomial(k[use], n[use])
    info.update(p0=mm["p0"], rho=mm["rho"], m=mm["m"])
    if not (np.isfinite(mm["rho"]) and mm["rho"] > 0):
        return np.full(len(k), np.nan), dict(info, defined=False, why="нет сверхдисперсии (rho <= 0)")
    ph = np.where(adm, K8.shrunk_share(k, n, mm["p0"], mm["m"]), np.nan)
    return ph, dict(info, defined=True)


def cmd_p4(a):
    """P4 по окнам формирования: p̂ за окно, ворота G-prop (нечётные / чётные сутки, Спирмен–Браун ≥ 0,5). Метки — res/lab-rv60-{w0,m01}.csv; P&L не читается."""
    import pandas as pd

    z = np.load(os.path.join(RES, "props_p123.npz"), allow_pickle=False)
    syms = [str(x) for x in z["syms"]]
    gp, arr = {}, {"syms": np.array(syms)}
    for reg, (fn, lo_ms) in P4_WIN.items():
        lab = pd.read_csv(os.path.join(RES, fn), dtype={"sym": str})
        adm = np.isfinite(z["%s_P1" % reg])
        C = p4_counts(lab, syms, lo_ms)
        ph, info = p4_shrink(*C["all"], adm)
        po, io = p4_shrink(*C["odd"], adm)
        pe, ie = p4_shrink(*C["even"], adm)
        g = dict(window=info, odd=io, even=ie, n_labelled=int(C["all"][1].sum()), n_approaches=int(len(lab)))
        if io["defined"] and ie["defined"]:
            r, n_ = spearman(po[adm], pe[adm])
            g.update(rho_half=r, rho_sb=sb(r), n=n_, passed=bool(info["defined"] and np.isfinite(r) and sb(r) >= 0.5))
        else:
            g.update(rho_half=float("nan"), rho_sb=float("nan"), n=0, passed=False)
        gp[reg] = g
        arr[reg + "_P4"] = ph if g["passed"] else np.full(len(syms), np.nan)
        arr[reg + "_P4_all"] = ph
        arr[reg + "_k"], arr[reg + "_n"] = C["all"]
        print(reg, json.dumps(dict(window=info, rho_half=g["rho_half"], rho_sb=g["rho_sb"], passed=g["passed"], n_labelled=g["n_labelled"], n_approaches=g["n_approaches"]), ensure_ascii=False))
    np.savez(os.path.join(RES, "props_p4.npz"), **arr)
    json.dump(gp, open(os.path.join(RES, "g_prop_p4.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ───────────────────────────── типы, сплиты, пороги ─────────────────────────────

FREEZE =os.path.join(T.ROOT, "freeze", "tk735-frozen.json")
KSIZE_MIN = 60


def sha256_file(path):
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def cmd_types(a):
    """Типы k-means по рангам годных свойств (G-prop), деления на «корзину / двойник», случайные наборы (нуль Б). Без сделок и без P&L."""
    from scipy.stats import ks_2samp

    z = np.load(os.path.join(RES, "props_p123.npz"), allow_pickle=False)
    syms = [str(x) for x in z["syms"]]
    gp = json.load(open(os.path.join(RES, "g_prop_p123.json"), encoding="utf-8"))
    z4 = np.load(os.path.join(RES, "props_p4.npz"), allow_pickle=False)
    g4 = json.load(open(os.path.join(RES, "g_prop_p4.json"), encoding="utf-8"))
    if os.path.exists(T.DB):
        os.remove(T.DB)
    db = sqlite3.connect(T.DB)
    db.executescript("""CREATE TABLE t735_prop (regime TEXT, sym TEXT, p1 REAL, p2 REAL, p3 REAL, p4 REAL, r1 REAL, r2 REAL, r3 REAL, r4 REAL, typ INTEGER);
        CREATE TABLE t735_centroid (regime TEXT, typ INTEGER, props TEXT, centroid TEXT, n_coins INTEGER);
        CREATE TABLE t735_basket (regime TEXT, typ INTEGER, n_coins INTEGER, p1_mean REAL, p2_mean REAL, p3_mean REAL, p4_mean REAL);
        CREATE TABLE t735_split (regime TEXT, typ INTEGER, s INTEGER, n_b INTEGER, n_w INTEGER, n_c INTEGER, bal_maxabs REAL, bal_ks REAL)""")
    frozen = dict(version="TK-735 v1", seed=T.SEED, regimes={})
    out_files = []
    for reg in ("FJ", "SEL"):
        gate_all = dict(gp[reg]["gprop"])
        gate_all["P4"] = dict(rho_half=g4[reg]["rho_half"], rho_sb=g4[reg]["rho_sb"], n=g4[reg]["n"], passed=g4[reg]["passed"])
        good = [k for k in ("P1", "P2", "P3", "P4") if gate_all[k]["passed"]]
        info = dict(passed_props=good, gprop=gate_all, p4=g4[reg],
                    note="P4 - метки y_n в RV60 (поправки L-526, L-527): SEL - окно 17-30.06 (w0), FJ - 02-31.01 (m01); k-means по рангам годных свойств")
        if len(good) < 2:
            info["stop"] = "годных свойств < 2: корзины не определены (§13.79.1 п. 2)"
            frozen["regimes"][reg] = info
            print(reg, "СТОП:", info["stop"])
            continue
        P = {k: z["%s_%s" % (reg, k)] for k in ("P1", "P2", "P3")}
        P["P4"] = z4["%s_P4_all" % reg]
        ok = np.ones(len(syms), bool)
        for k in good:
            ok &= np.isfinite(P[k])
        idx = np.where(ok)[0]
        pos = {int(c): j for j, c in enumerate(idx)}
        R = np.column_stack([T.pct_rank(P[k][idx]) for k in good])
        K = 4
        while True:
            cen, lab, ine = T.kmeans(R, K, T.SEED, 100)
            sizes = np.bincount(lab, minlength=K)
            if sizes.min() >= KSIZE_MIN or K == 2:
                break
            K -= 1
        order = np.argsort(cen[:, 0])                       # типы нумеруются по P1 центроида (по первому годному свойству)
        newid = np.empty(K, np.int64)
        newid[order] = np.arange(K)
        typ = np.full(len(syms), -1, np.int64)
        typ[idx] = newid[lab]
        info.update(K=K, inertia=float(ine), sizes=[int((typ == t).sum()) for t in range(K)], n_eligible=int(ok.sum()),
                    no_type=[syms[i] for i in range(len(syms)) if not ok[i]], centroids=[[float(x) for x in cen[order[t]]] for t in range(K)], props=good)
        for i, sy in enumerate(syms):
            rr = [float(R[pos[i], good.index(k)]) if (i in pos and k in good) else float("nan") for k in ("P1", "P2", "P3", "P4")]
            db.execute("INSERT INTO t735_prop VALUES (?,?,?,?,?,?,?,?,?,?,?)", (reg, sy, float(P["P1"][i]), float(P["P2"][i]), float(P["P3"][i]), float(P["P4"][i]), *rr, int(typ[i])))
        for t in range(K):
            db.execute("INSERT INTO t735_centroid VALUES (?,?,?,?,?)", (reg, t, ",".join(good), json.dumps(info["centroids"][t]), info["sizes"][t]))
            m = typ == t
            db.execute("INSERT INTO t735_basket VALUES (?,?,?,?,?,?,?)", (reg, t, int(m.sum()), float(np.nanmean(P["P1"][m])), float(np.nanmean(P["P2"][m])), float(np.nanmean(P["P3"][m])),
                                                                         float(np.nanmean(P["P4"][m])) if np.isfinite(P["P4"][m]).any() else float("nan")))
        # сплиты: B_s / W_s — случайные половины монет типа; C_s — случайные монеты c539 вне B_s ∪ W_s того же размера, что W_s
        sp = {}
        ncoin = len(syms)
        for t in range(K):
            coins = np.array([i for i in range(ncoin) if typ[i] == t], np.int64)       # по имени (syms отсортированы)
            h = len(coins) // 2
            Bm, Wm, Cm = [], [], []
            for s in range(1, T.NSPLIT + 1):
                perm = np.random.default_rng(T.SEED + s).permutation(coins)
                Bs, Ws = np.sort(perm[:h]), np.sort(perm[h:2 * h])
                rest = np.setdiff1d(np.arange(ncoin), np.concatenate([Bs, Ws]))
                Cs = np.sort(np.random.default_rng(T.SEED_NULL + s).choice(rest, min(len(Ws), len(rest)), replace=False))
                Bm.append(Bs)
                Wm.append(Ws)
                Cm.append(Cs)
                bal, ks = [], []
                for j in range(len(good)):                  # баланс свойств B_s / W_s: стандартизованная разность и KS по рангам
                    rb, rw = R[[pos[int(c)] for c in Bs], j], R[[pos[int(c)] for c in Ws], j]
                    sd = math.sqrt((rb.var(ddof=1) + rw.var(ddof=1)) / 2) or 1.0
                    bal.append(abs(rb.mean() - rw.mean()) / sd)
                    ks.append(ks_2samp(rb, rw).statistic)
                db.execute("INSERT INTO t735_split VALUES (?,?,?,?,?,?,?,?)", (reg, t, s, len(Bs), len(Ws), len(Cs), float(max(bal)), float(max(ks))))
            sp["t%d_B" % t], sp["t%d_W" % t], sp["t%d_C" % t] = np.array(Bm), np.array(Wm), np.array(Cm)
        path = os.path.join(RES, "splits-%s.npz" % reg)
        np.savez(path, typ=typ, syms=np.array(syms), **sp)
        out_files.append(path)
        frozen["regimes"][reg] = info
        print(reg, "K=%d размеры %s, годные свойства %s" % (K, info["sizes"], good))
    db.commit()
    out_files += [os.path.join(RES, f) for f in ("props_p123.npz", "g_prop_p123.json", "props_p4.npz", "g_prop_p4.json")]
    frozen["files"] = {os.path.basename(p): sha256_file(p) for p in out_files}
    os.makedirs(os.path.dirname(FREEZE), exist_ok=True)
    json.dump(frozen, open(FREEZE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("заморожено:", FREEZE, json.dumps(frozen["files"]))


def cmd_thresholds(a):
    """Абсолютные пороги условий на (режим, тип): перцентили 50 / 67 / 80 / 90 признаков всех уникальных сделок монет типа в сутках режима (без $). Дописывает в файл заморозки."""
    fz = json.load(open(FREEZE, encoding="utf-8"))
    tf = np.load(os.path.join(RES, "trade_feat.npz"), allow_pickle=False)
    X = np.column_stack([tf[k] for k in T.PHI])
    sym = tf["sym"]
    reg = tf["regime"]
    for r in ("FJ", "SEL"):
        info = fz["regimes"][r]
        if "stop" in info:
            continue
        sp = np.load(os.path.join(RES, "splits-%s.npz" % r), allow_pickle=False)
        syms = [str(x) for x in sp["syms"]]
        typ = sp["typ"]
        sym_typ = {syms[i]: int(typ[i]) for i in range(len(syms))}
        tt = np.array([sym_typ.get(str(s), -1) for s in sym])
        info["thresholds"] = {}
        info["n_trades_type"] = {}
        for t in range(info["K"]):
            m = (reg == r) & (tt == t)
            th = T.thresholds(X[m])
            info["thresholds"][str(t)] = [[None if not np.isfinite(v) else float(v) for v in row] for row in th]
            info["n_trades_type"][str(t)] = int(m.sum())
        print(r, "сделок по типам:", info["n_trades_type"])
    fz["files"]["trade_feat.npz"] = sha256_file(os.path.join(RES, "trade_feat.npz"))
    json.dump(fz, open(FREEZE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("пороги записаны в", FREEZE)


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("extract", "props", "p4", "types", "thresholds"))
    ap.add_argument("--procs", type=int, default=6)
    a = ap.parse_args()
    {"extract": cmd_extract, "props": cmd_props, "p4": cmd_p4, "types": cmd_types, "thresholds": cmd_thresholds}[a.cmd](a)


if __name__ == "__main__":
    main()
