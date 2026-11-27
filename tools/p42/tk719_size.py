#!/usr/bin/env python3
"""TK-719 (PS-1040 + PS-1041): размер сделки базы B шорта (9 членов TK-718) по фандингу (кросс-секционный ранг) и 1/σ монеты; решают SEL и N1, размер заморожен на FJ.

  python3 tk719_size.py features --groups h1[,sel,n1,f]      # u (ранг фандинга среди c539 на момент сигнала), N_t, σ̂ (1 440 1-мин доходностей) на уникальных (монета, сигнал) → /data/p42-tk719/feat/<группа>.npz
  python3 tk719_size.py freeze   --code-commit SHA          # FJ: c₁, κ, m₃ (среднее s = 1), таблицы Jan / A / M / FJ, MDE → файл заморозки + БД. Отказ, если сделки SEL / N1 уже есть
  python3 tk719_size.py test     [--freeze …]               # SEL / N1: ряды, Δ_plain / Δ_exp, бутстреп, Холм m = 4, K1–K6, вердикт, таблицы п. 35, критерий остановки → БД
  python3 tk719_size.py gate     --groups h1,sel,n1         # сделки движка = ряды `tk718_ser` членов (допуск 1e-6)

Размер s — функция (монета, sig_ms), одинакова у всех членов; P&L копии = s × (gross − fees) / 9 (простой P&L, фандинг за удержание не входит). Сутки — по дате входа (как `mass_day`).
Результаты — `/data/p42-tk719/tk719.sqlite`: `tk719_row(test, side, series, win, stat, value, txt)`, `tk719_trade` (сутки размеров по уникальным сигналам)."""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import io
import json
import math
import multiprocessing as mp
import os
import sqlite3
import sys
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, "/data/p42-tk692/code", "/data/p42-tk718/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import tk692_walk as W  # noqa: E402
import tk718_tests as T  # noqa: E402

ROOT = "/data/p42-tk719"
DB = ROOT + "/tk719.sqlite"
FEAT = ROOT + "/feat"
FREEZE = ROOT + "/freeze/tk719-frozen.json"
TK718_DB = "/data/p42-tk718/tk718.sqlite"
KL_ROOT = "/data/p42-tk621"
C539 = KL_ROOT + "/universe/symbols-crypto.txt"
FUND_DIRS = ("/data/p42-tk700/dl/fund-all", ROOT + "/fund-dl")
FUND_REST = ROOT + "/fund-dl/rest"

SEED, NBOOT, BLOCK = 719, 10000, 21
ALPHA, M_HOLM = 0.025, 4                                          # PS-1041 п. 1: Холм по 4 (V1, V2, V3, B^σ — p = 1, последняя)
CAP, FLOOR = 2.0, 0.25
MIN_COV, NT_MIN, SIGMA_BARS = 0.7, 100, 1440
FUND_AGE_MS = 9 * 3600 * 1000
NMEMB = 9
MEMBERS = tuple(range(7180015, 7180024))                          # BS-l{3,7,14}-tf{30,60,240}
DAY_MS, D0_MS = T.DAY_MS, T.D0_MS
WINS = {"Jan": (0, 31), "A": (31, 120), "M": (120, 181), "FJ": (31, 181), "SEL": (181, 317), "SEL1": (181, 249), "SEL2": (249, 317), "N1": (317, 328)}
F0 = 328                                                          # 25.11.2026
VARIANTS = ("V1", "V2", "V3")
KILL_N = 60
CREDIT = {                                                         # группа → {окно: (сутки с, сутки по включительно)} по дате входа
    "h1": {m: (W.MONTH_START[i], W.MONTH_START[i + 1] - 1) for i, m in enumerate(W.MONTHS)},
    "sel": {"wa": (181, 236), "wb": (237, 274), "wc": (275, 316)},
    "n1": {"n1": (317, 327)},
}
RUNS = {"h1": ("/data/p42-tk718/trades", "tk718c-h1-A"), "sel": (ROOT + "/trades", "tk719s-sel-A"), "n1": (ROOT + "/trades", "tk719n-n1-A")}
TREES = {"h1": ["m01", "m02", "m03", "m04", "m05", "m06"], "sel": ["wa", "wb", "wc"], "n1": ["n1"]}
TABLES = """
CREATE TABLE IF NOT EXISTS tk719_row(test TEXT, side TEXT, series TEXT, win TEXT, stat TEXT, value REAL, txt TEXT, PRIMARY KEY(test, side, series, win, stat));
CREATE TABLE IF NOT EXISTS tk719_trade(grp TEXT, sym TEXT, sig_ms INTEGER, u REAL, nt INTEGER, sigma REAL, s1 REAL, s2 REAL, s3 REAL, PRIMARY KEY(grp, sym, sig_ms));
CREATE TABLE IF NOT EXISTS tk719_run(stage TEXT PRIMARY KEY, params TEXT, code TEXT, created TEXT);
"""


# ───────────────────────────── сделки ─────────────────────────────

def read_trades_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        rd = list(csv.DictReader(f))
    return dict(sym=np.array([r["sym"] for r in rd], dtype=object), sig=np.array([int(r["sig_ms"]) for r in rd], np.int64), ent=np.array([int(r["entry_ms"]) for r in rd], np.int64),
                ext=np.array([int(r["exit_ms"]) for r in rd], np.int64), val=np.array([float(r["gross"]) - float(r["fees"]) for r in rd], float),
                funding=np.array([float(r["funding"]) for r in rd], float))


def load_group(grp, root=None, run=None, credit=None, cids=MEMBERS):
    """Копии сделок 9 членов группы (по числу членов: каждая сделка члена — копия): массивы cid, sym, sig, ent, ext [мс эпохи], day, val (gross − fees), funding. Нет файла — ошибка."""
    root = root or RUNS[grp][0]
    run = run or RUNS[grp][1]
    credit = credit or CREDIT[grp]
    t0 = json.load(open(os.path.join(root, run, "t0.json"), encoding="utf-8"))
    parts = []
    for win, (lo, hi) in credit.items():
        for cid in cids:
            t = read_trades_csv(os.path.join(root, run, win, "%d-trades.csv" % cid))
            for k in ("sig", "ent", "ext"):
                t[k] = t[k] + t0[win]
            t["day"] = (t["ent"] - D0_MS) // DAY_MS
            keep = (t["day"] >= lo) & (t["day"] <= hi)
            part = {k: v[keep] for k, v in t.items()}
            part["cid"] = np.full(int(keep.sum()), cid, np.int64)
            parts.append(part)
    out = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    o = np.lexsort((out["cid"], out["ent"], out["day"]))
    return {k: v[o] for k, v in out.items()}


def unique_keys(c):
    """Уникальные (монета, сигнал) копий: (sym[u], sig[u], inv[копия → u])."""
    ix, usym, usig, inv = {}, [], [], np.empty(len(c["sym"]), np.int64)
    for j, (s, g) in enumerate(zip(c["sym"], c["sig"])):
        k = (s, int(g))
        if k not in ix:
            ix[k] = len(usym)
            usym.append(s)
            usig.append(int(g))
        inv[j] = ix[k]
    return np.array(usym, dtype=object), np.array(usig, np.int64), inv


# ───────────────────────────── признаки ─────────────────────────────

def parse_funding(text):
    out = {}
    for line in text.splitlines()[1:]:
        p = line.split(",")
        if len(p) >= 3 and p[0].isdigit():
            out[int(p[0])] = float(p[2])
    return out


def load_funding_coin(sym, dirs=FUND_DIRS, rest=FUND_REST):
    """(время мс, ставка) монеты из архивов Binance (zip) и REST-файла `<rest>/<монета>.csv`; по времени, без повторов."""
    d = {}
    for base in dirs:
        dd = os.path.join(base, sym)
        if os.path.isdir(dd):
            for fn in sorted(os.listdir(dd)):
                if fn.endswith(".zip"):
                    z = zipfile.ZipFile(os.path.join(dd, fn))
                    for nm in z.namelist():
                        d.update(parse_funding(z.read(nm).decode()))
    rp = os.path.join(rest, sym + ".csv")
    if os.path.exists(rp):
        d.update(parse_funding(open(rp, encoding="utf-8").read()))
    ts = np.array(sorted(d), np.int64)
    return ts, np.array([d[t] for t in ts], float)


def funding_matrix(coins, sigs, loader=load_funding_coin):
    """M[сигнал, монета] — последняя ставка со временем ≤ сигнала не старше 9 ч, иначе NaN."""
    M = np.full((len(sigs), len(coins)), np.nan)
    for j, s in enumerate(coins):
        ts, rt = loader(s)
        if not len(ts):
            continue
        k = np.searchsorted(ts, sigs, side="right") - 1
        kk = np.maximum(k, 0)
        ok = (k >= 0) & (sigs - ts[kk] <= FUND_AGE_MS)
        M[:, j] = np.where(ok, rt[kk], np.nan)
    return M


def cross_rank(M, col):
    """u = (монет строго ниже + 0,5 × равных, включая саму) / N_t; N_t < 100 или нет ставки у монеты — NaN. col[i] — столбец монеты сделки i (−1 — нет)."""
    n = len(M)
    u, nt = np.full(n, np.nan), np.isfinite(M).sum(axis=1)
    for i in range(n):
        j = col[i]
        if j < 0 or not np.isfinite(M[i, j]) or nt[i] < NT_MIN:
            continue
        r = M[i, j]
        u[i] = (np.count_nonzero(M[i] < r) + 0.5 * np.count_nonzero(M[i] == r)) / nt[i]
    return u, nt


def load_close_day(sym, date, kl_dirs):
    """(минуты от эпохи, close) файла `<дерево>/<монета>/<дата>.csv` первого дерева, где он есть; нет — None."""
    for d in kl_dirs:
        p = os.path.join(d, sym, "%s.csv" % date)
        if os.path.exists(p):
            a = np.loadtxt(p, delimiter=",", skiprows=1, usecols=(0, 4), ndmin=2)
            return (a[:, 0] // 60000).astype(np.int64), a[:, 1]
    return None


def sigma_hat(sym, sig_ms, kl_dirs, cache):
    """σ̂ = корень из среднего квадрата 1 440 1-мин лог-доходностей баров с номерами [i − 1 440, i − 1], i = sig_ms // 60000 (бары, закрытые до сигнала). Охват (баров со свечой) ≥ 0,7, пропуск = доходность 0.
    (σ̂, охват); охват < 0,7 — (NaN, охват)."""
    i = sig_ms // 60000
    lo, hi = i - SIGMA_BARS - 1, i - 1
    c = np.full(hi - lo + 1, np.nan)
    for day in range(lo // 1440, hi // 1440 + 1):
        key = (sym, day)
        if key not in cache:
            date = (datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(days=day)).strftime("%Y-%m-%d")
            cache[key] = load_close_day(sym, date, kl_dirs)
        r = cache[key]
        if r is None:
            continue
        m, cl = r
        ok = (m >= lo) & (m <= hi)
        c[m[ok] - lo] = cl[ok]
    pres = np.isfinite(c)
    cov = float(pres[1:].mean())
    if cov < MIN_COV:
        return float("nan"), cov
    idx = np.where(pres, np.arange(len(c)), -1)
    idx = np.maximum.accumulate(idx)
    cf = np.where(idx >= 0, c[np.maximum(idx, 0)], np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        r1 = np.log(cf[1:] / cf[:-1])
    r1 = np.where(np.isfinite(r1), r1, 0.0)
    return float(math.sqrt(float((r1 * r1).sum()) / SIGMA_BARS)), cov


def _sigma_job(arg):
    sym, sigs, kl_dirs = arg
    cache = {}
    return sym, [sigma_hat(sym, int(g), kl_dirs, cache) for g in sigs]


def compute_features(usym, usig, kl_dirs, coins, procs=4, loader=load_funding_coin):
    col_of = {s: j for j, s in enumerate(coins)}
    col = np.array([col_of.get(s, -1) for s in usym], np.int64)
    u, nt = cross_rank(funding_matrix(coins, usig, loader), col)
    by = {}
    for j, s in enumerate(usym):
        by.setdefault(s, []).append(j)
    jobs = [(s, usig[ix], kl_dirs) for s, ix in sorted(by.items())]
    sig, cov = np.full(len(usym), np.nan), np.zeros(len(usym))
    res = mp.get_context("fork").Pool(procs).imap_unordered(_sigma_job, jobs, chunksize=4) if procs > 1 else map(_sigma_job, jobs)
    for s, rr in res:
        for j, (sg, cv) in zip(by[s], rr):
            sig[j], cov[j] = sg, cv
    return dict(usym=usym, usig=usig, u=u, nt=nt, sigma=sig, scov=cov)


def feat_path(grp):
    return os.path.join(FEAT, grp + ".npz")


def kl_dirs_of(grp):
    if grp == "f":
        return [os.path.join(ROOT, "n2", "klines")] + [os.path.join(KL_ROOT, "n1", "klines")]
    return [os.path.join(KL_ROOT, w, "klines") for w in TREES[grp]]


# ───────────────────────────── размер ─────────────────────────────

def solve_scale(x, w, fixed, lo, hi):
    """c: взвешенное среднее s = where(fixed, 1, clip(c·x, lo, hi)) равно 1 (s не убывает по c — бисекция по логарифму)."""
    def f(c):
        s = np.where(fixed, 1.0, np.clip(c * x, lo, hi))
        return float((w * s).sum() / w.sum())
    a, b = 1e-12, 1e12
    if f(a) > 1.0 + 1e-12 or f(b) < 1.0 - 1e-12:
        raise ValueError("масштаб не находится: среднее при c → 0 = %.4f, при c → ∞ = %.4f" % (f(a), f(b)))
    for _ in range(300):
        m = math.sqrt(a * b)
        if f(m) < 1.0:
            a = m
        else:
            b = m
    return math.sqrt(a * b)


def sizes(u, sig, sc):
    """(s1, s2, s3) по кросс-секционному рангу u и σ̂; нет признака — s = 1. sc = {c1, kappa, m3}."""
    hu = np.isfinite(u)
    hs = np.isfinite(sig) & (sig > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.where(hs, np.clip(sc["kappa"] / np.where(hs, sig, 1.0), FLOOR, CAP), 1.0)
    s1 = np.where(hu, np.clip(sc["c1"] * 2.0 * np.where(hu, u, 0.0), 0.0, CAP), 1.0)
    s2 = g
    s3 = np.where(hu & hs, np.clip(2.0 * np.where(hu, u, 0.0) * g / sc["m3"], 0.0, CAP), 1.0)
    return s1, s2, s3


def fit_scales(u, sig, w):
    """Заморозка на FJ: масштабы c₁, κ, m₃ при среднем s = 1 (вес w — копии сделок)."""
    hu = np.isfinite(u)
    hs = np.isfinite(sig) & (sig > 0)
    c1 = solve_scale(2.0 * np.where(hu, u, 0.0), w, ~hu, 0.0, CAP)
    kappa = solve_scale(1.0 / np.where(hs, sig, 1.0), w, ~hs, FLOOR, CAP)
    g = np.where(hs, np.clip(kappa / np.where(hs, sig, 1.0), FLOOR, CAP), 1.0)
    cc = solve_scale(2.0 * np.where(hu, u, 0.0) * g, w, ~(hu & hs), 0.0, CAP)
    return dict(c1=c1, kappa=kappa, m3=1.0 / cc)


# ───────────────────────────── ряды и метрики ─────────────────────────────

def daily(day, w, nd):
    return np.bincount(day, weights=w, minlength=nd)[:nd]


def win_slice(x, win, nd):
    lo, hi = WINS[win]
    return x[lo:min(hi, nd)]


def peak_concurrent(ent, ext, w):
    """Пик одновременных ставок $ (500 $ × s / 9 на копию): события входа / выхода по времени."""
    t = np.concatenate([ent, ext])
    d = np.concatenate([w, -w])
    o = np.lexsort((d, t))                                            # при равном времени выход раньше входа
    return float(np.max(np.cumsum(d[o]))) if len(o) else 0.0


def stats_rows(rows, series, pnl, cnt, wins, nd, note=None):
    for win in wins:
        lo, hi = WINS[win]
        if hi > nd:
            continue
        st = W.series_stats(pnl[lo:hi], cnt[lo:hi])
        for k in T.STAT_KEYS:
            if k in st:
                rows.append(("T1", "short", series, win, k, float(st[k]), note))


def holm_m(p, m=M_HOLM, alpha=ALPHA):
    """Холм: p — считаемые гипотезы, остальные до m стоят последними с p = 1. [(прошла, порог)] в порядке входа."""
    order = np.argsort(p, kind="stable")
    res, alive = [None] * len(p), True
    for j, i in enumerate(order, start=1):
        aj = alpha / (m - j + 1)
        alive = alive and p[i] <= aj
        res[i] = (bool(alive), aj)
    return res


def boot_idx(n, rng_tag, B=NBOOT, L=BLOCK):
    return T.block_idx(n, L, B, np.random.default_rng([SEED, rng_tag]))


def kill_tables(dexp, dsized, idx, q):
    """Пути бутстрепа SEL (первые KILL_N суток): Q_q по n глубины падения накопленной Δ_exp и q-й процентиль макс. просадки ряда; доля путей, где (а) или (б) срабатывает хотя бы при одном n."""
    pe = np.cumsum(dexp[idx[:, :KILL_N]], axis=1)
    depth = -np.minimum(np.minimum.accumulate(pe, axis=1), 0.0)
    Q = np.percentile(depth, q, axis=0)
    cs = np.cumsum(dsized[idx[:, :KILL_N]], axis=1)
    peak = np.maximum(np.maximum.accumulate(cs, axis=1), 0.0)
    dd = np.maximum.accumulate(peak - cs, axis=1)
    DQ = np.percentile(dd, q, axis=0)
    fire = ((pe < -Q[None, :]) | (dd > DQ[None, :])).any(axis=1)
    return Q, DQ, float(fire.mean())


# ───────────────────────────── заморозка ─────────────────────────────

def con_open():
    con = sqlite3.connect(DB, timeout=120)
    con.executescript(TABLES)
    return con


def put_rows(con, rows, tests):
    for t in tests:
        con.execute("DELETE FROM tk719_row WHERE test=?", (t,))
    con.executemany("INSERT OR REPLACE INTO tk719_row VALUES(?,?,?,?,?,?,?)", rows)
    con.commit()


def tercile_rows(rows, test, tag, u, v, mask):
    ok = mask & np.isfinite(u)
    for k, (a, b) in enumerate(((0.0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.0 + 1e-9))):
        m = ok & (u >= a) & (u < b)
        rows.append((test, "short", "u-terc", tag, "T%d_n" % (k + 1), float(m.sum()), None))
        rows.append((test, "short", "u-terc", tag, "T%d_sum" % (k + 1), float(v[m].sum()), None))


def cmd_freeze(a):
    if os.path.isdir(ROOT + "/trades") and os.listdir(ROOT + "/trades"):
        raise SystemExit("СТОП: сделки SEL / N1 уже есть (%s/trades) — заморозка после них недопустима" % ROOT)
    c = load_group("h1")
    usym, usig, inv = unique_keys(c)
    F = np.load(feat_path("h1"), allow_pickle=True)
    ix = {(s, int(g)): j for j, (s, g) in enumerate(zip(F["usym"], F["usig"]))}
    fi = np.array([ix[(s, int(g))] for s, g in zip(usym, usig)], np.int64)
    u, nt, sg, cv = F["u"][fi], F["nt"][fi], F["sigma"][fi], F["scov"][fi]
    ucopy, sgcopy = u[inv], sg[inv]
    day = c["day"]
    fj = (day >= 31) & (day < 181)
    w = np.ones(int(fj.sum()))
    sc = fit_scales(ucopy[fj], sgcopy[fj], w)
    s1u, s2u, s3u = sizes(u, sg, sc)
    S = {"V1": s1u[inv], "V2": s2u[inv], "V3": s3u[inv]}
    v = c["val"] / NMEMB
    nd = int(day.max()) + 1
    rows = []
    rows += [("FJ", "short", "scale", "FJ", k, float(x), None) for k, x in sc.items()]
    cnt = np.bincount(day, minlength=nd)[:nd]
    Bd = daily(day, v, nd)
    ser = {"B": Bd}
    for k, s in S.items():
        ser[k] = daily(day, s * v, nd)
        sbar = {win: float(s[(day >= WINS[win][0]) & (day < WINS[win][1])].mean()) for win in ("Jan", "A", "M", "FJ")}
        for win, x in sbar.items():
            rows.append(("FJ", "short", k, win, "sbar", x, None))
            ser[k + "b_" + win] = x * Bd
        rows.append(("FJ", "short", k, "FJ", "share_at_cap", float((s[fj] >= CAP - 1e-9).mean()), None))
        rows.append(("FJ", "short", k, "FJ", "share_at_floor", float((s[fj] <= (FLOOR if k == "V2" else 0.0) + 1e-9).mean()), None))
        rows.append(("FJ", "short", k, "FJ", "share_missing", float(((~np.isfinite(ucopy)) if k == "V1" else (~np.isfinite(sgcopy)) if k == "V2" else ~(np.isfinite(ucopy) & np.isfinite(sgcopy)))[fj].mean()), None))
    for name in ("B", "V1", "V2", "V3"):
        stats_rows(rows, name, ser[name], cnt, ("Jan", "A", "M", "FJ"), nd, "найдено на этих данных" if name != "B" else None)
    for k in VARIANTS:
        for win in ("Jan", "A", "M", "FJ"):
            lo, hi = WINS[win]
            sb = np.full(nd, 0.0)
            sb[lo:hi] = ser[k + "b_" + win][lo:hi]
            stats_rows(rows, k + "b", sb, cnt, (win,), nd, "B × s̄ окна")
        rows.append(("FJ", "short", k, "FJ", "delta_plain", float(win_slice(ser[k] - ser["B"], "FJ", nd).sum()), "найдено на этих данных (Δ при s̄ = 1)"))
    # N_t и охват
    for nm, m in (("FJ", fj), ("Jan", day < 31), ("A", (day >= 31) & (day < 120)), ("M", (day >= 120) & (day < 181))):
        cu = np.unique(inv[m])
        rows += [("COV", "short", "N_t", nm, "mean", float(nt[cu].mean()), None), ("COV", "short", "N_t", nm, "min", float(nt[cu].min()), None),
                 ("COV", "short", "u", nm, "share", float(np.isfinite(u[cu]).mean()), None), ("COV", "short", "sigma", nm, "share", float(np.isfinite(sg[cu]).mean()), None)]
    tercile_rows(rows, "FJ", "FJ", ucopy, v, fj)
    # мощность (PS-1041 п. 5)
    rng = np.random.default_rng([SEED, 2])
    idx = T.block_idx(150, BLOCK, NBOOT, rng)
    note = {}
    for k in VARIANTS:
        d = (ser[k] - ser["B"])[31:181]
        se = float(np.std(d[idx].sum(axis=1), ddof=1))
        mde = (1.96 + 0.84) * se * math.sqrt(150 / 136)
        eff = float(d.sum())
        low = mde > 0.5 * eff
        note[k] = low
        rows += [("MDE", "short", k, "FJ", "se", se, "бутстреп, блок 21, 10 000"), ("MDE", "short", k, "FJ", "mde", mde, "(1,96 + 0,84) × SE × √(150/136)"), ("MDE", "short", k, "FJ", "half_effect", 0.5 * eff, "модель, на допущении усадки 50 %"),
                 ("MDE", "short", k, "FJ", "low_power", float(low), "мощность низкая: ожидаемый исход — не ясно" if low else "MDE ≤ половины эффекта FJ")]
    con = con_open()
    put_rows(con, rows, ("FJ", "COV", "MDE", "T1"))
    con.execute("INSERT OR REPLACE INTO tk719_run VALUES('freeze', ?, ?, ?)", (json.dumps(sc), a.code_commit, datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    code_sha = hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()
    out = dict(version="tk719-frozen-1", protocol="PS-1040 + PS-1041", code_commit=a.code_commit, code_sha256=code_sha, cap=CAP, floor=FLOOR, scales=sc, fj_copies=int(fj.sum()), fj_unique=int(len(np.unique(inv[fj]))),
               nt_fj_mean=float(nt[np.unique(inv[fj])].mean()), nt_fj_min=int(nt[np.unique(inv[fj])].min()), low_power=note,
               defs=dict(u="кросс-секционный ранг фандинга среди монет c539 со ставкой ≤ сигнала не старше 9 ч; (ниже + 0,5 × равных, включая саму) / N_t; N_t ≥ 100", sigma="корень среднего квадрата 1 440 1-мин лог-доходностей баров [i−1440, i−1], охват ≥ 0,7",
                         V1="clip(c1·2u; 0; 2)", V2="clip(kappa/sigma; 0,25; 2)", V3="clip(2u·g/m3; 0; 2), g = V2", missing="s = 1"))
    os.makedirs(os.path.dirname(FREEZE), exist_ok=True)
    json.dump(out, open(FREEZE, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    print("заморожено: %s  c1 %.6f  kappa %.6g  m3 %.6f  FJ копий %d  N_t FJ среднее %.1f, мин %d" % (FREEZE, sc["c1"], sc["kappa"], sc["m3"], out["fj_copies"], out["nt_fj_mean"], out["nt_fj_min"]))
    for k in VARIANTS:
        print("  %s: Δ_FJ %.2f $ (в выборке), MDE %.2f, ½ эффекта %.2f → %s" % (k, float((ser[k] - ser["B"])[31:181].sum()), [r for r in rows if r[0] == "MDE" and r[2] == k and r[4] == "mde"][0][5],
                                                                              [r for r in rows if r[0] == "MDE" and r[2] == k and r[4] == "half_effect"][0][5], "мощность низкая" if note[k] else "ok"))
    return 0


# ───────────────────────────── SEL / N1 ─────────────────────────────

def group_arrays(grp, sc):
    c = load_group(grp)
    usym, usig, inv = unique_keys(c)
    F = np.load(feat_path(grp), allow_pickle=True)
    ix = {(s, int(g)): j for j, (s, g) in enumerate(zip(F["usym"], F["usig"]))}
    fi = np.array([ix[(s, int(g))] for s, g in zip(usym, usig)], np.int64)
    u, nt, sg, cv = F["u"][fi], F["nt"][fi], F["sigma"][fi], F["scov"][fi]
    s1, s2, s3 = sizes(u, sg, sc)
    return dict(c=c, usym=usym, usig=usig, inv=inv, u=u, nt=nt, sigma=sg, scov=cv, s={"V1": s1[inv], "V2": s2[inv], "V3": s3[inv]}, su={"V1": s1, "V2": s2, "V3": s3})


def cmd_test(a):
    fz = json.load(open(a.freeze, encoding="utf-8"))
    sc = fz["scales"]
    sel, n1 = group_arrays("sel", sc), group_arrays("n1", sc)
    rows = []
    # покрытие и ворота N_t
    fj_nt = fz["nt_fj_mean"]
    gate_nt = {}
    for nm, g in (("SEL", sel), ("N1", n1)):
        cu = np.unique(g["inv"])
        rows += [("COV", "short", "N_t", nm, "mean", float(g["nt"][cu].mean()), None), ("COV", "short", "N_t", nm, "min", float(g["nt"][cu].min()), None),
                 ("COV", "short", "u", nm, "share", float(np.isfinite(g["u"][cu]).mean()), None), ("COV", "short", "sigma", nm, "share", float(np.isfinite(g["sigma"][cu]).mean()), None)]
        gate_nt[nm] = float(g["nt"][cu].mean()) >= 0.9 * fj_nt
        rows.append(("COV", "short", "N_t", nm, "gate_ge_0.9_FJ", float(gate_nt[nm]), "среднее N_t(%s) ≥ 0,9 × среднее N_t(FJ) = %.1f" % (nm, 0.9 * fj_nt)))
    if not gate_nt["SEL"]:
        con = con_open()
        put_rows(con, rows, ("COV",))
        raise SystemExit("СТОП: N_t(SEL) < 0,9 × N_t(FJ) — поправка до чтения (PS-1041 п. 2)")
    nd = 328
    c = {k: np.concatenate([sel["c"][k], n1["c"][k]]) for k in sel["c"]}
    S = {k: np.concatenate([sel["s"][k], n1["s"][k]]) for k in VARIANTS}
    day = c["day"]
    v = c["val"] / NMEMB
    cnt = np.bincount(day, minlength=nd)[:nd].astype(np.int64)
    Bd = daily(day, v, nd)
    ser = {"B": Bd}
    for k in VARIANTS:
        ser[k] = daily(day, S[k] * v, nd)
    # s̄ по окнам, Δ
    m_sel = (day >= 181) & (day < 317)
    sbar = {k: float(S[k][m_sel].mean()) for k in VARIANTS}
    dplain = {k: ser[k] - ser["B"] for k in VARIANTS}
    dexp = {k: ser[k] - sbar[k] * ser["B"] for k in VARIANTS}
    for k in VARIANTS:
        for win in ("SEL", "SEL1", "SEL2", "N1"):
            lo, hi = WINS[win]
            mk = (day >= lo) & (day < hi)
            rows += [("D", "short", k, win, "sbar", float(S[k][mk].mean()), None), ("D", "short", k, win, "delta_plain", float(dplain[k][lo:hi].sum()), None),
                     ("D", "short", k, win, "delta_exp", float(dexp[k][lo:hi].sum()), "s̄ = %.4f (SEL, фиксирован)" % sbar[k])]
        rows.append(("D", "short", k, "SEL", "peak_stake_usd", peak_concurrent(c["ent"][m_sel], c["ext"][m_sel], 500.0 * S[k][m_sel] / NMEMB), "модель, на допущении линейности по ставке"))
        rows.append(("D", "short", k, "SEL", "funding_sum", float((S[k] * c["funding"] / NMEMB)[m_sel].sum()), "не в P&L"))
        rows.append(("D", "short", k, "SEL", "share_at_cap", float((S[k][m_sel] >= CAP - 1e-9).mean()), None))
    rows.append(("D", "short", "B", "SEL", "peak_stake_usd", peak_concurrent(c["ent"][m_sel], c["ext"][m_sel], 500.0 / NMEMB * np.ones(int(m_sel.sum()))), None))
    rows.append(("D", "short", "B", "SEL", "funding_sum", float((c["funding"] / NMEMB)[m_sel].sum()), "не в P&L"))
    # бутстреп SEL
    idx = boot_idx(136, 1)
    p, lb, ub = {}, {}, {}
    for k in VARIANTS:
        x = dexp[k][181:317]
        sums = x[idx].sum(axis=1)
        p[k] = float((np.count_nonzero(sums <= 0) + 1) / (NBOOT + 1))
        lb[k], ub[k] = float(np.percentile(sums, 2.5)), float(np.percentile(sums, 97.5))
    hm = holm_m(np.array([p[k] for k in VARIANTS]))
    for (k, (ok, aj)) in zip(VARIANTS, hm):
        rows += [("D", "short", k, "SEL", "boot_p", p[k], "p-floor 1/10 001"), ("D", "short", k, "SEL", "boot_lo95", lb[k], None), ("D", "short", k, "SEL", "boot_hi95", ub[k], None),
                 ("D", "short", k, "SEL", "holm_alpha_j", aj, "Холм m = 4"), ("D", "short", k, "SEL", "holm_pass", float(ok), None)]
        sums = dexp[k][181:317][idx].sum(axis=1)
        rows.append(("D", "short", k, "SEL", "boot_lo_holm", float(np.percentile(sums, 100 * aj)), "нижняя граница на уровне α_j"))
    # q05 для K6
    q05 = {k: float(np.percentile(np.array([dplain[k][b:b + 11].sum() for b in range(181, 317 - 11 + 1)]), 5)) for k in VARIANTS}
    # K1–K6
    verdict_k, passed = {}, []
    sh_b = W.sharpe_of(ser["B"][181:317])
    for (k, (ok, aj)) in zip(VARIANTS, hm):
        k1 = bool(ok and float(dexp[k][181:317].sum()) > 0)
        k2 = float(dplain[k][181:317].sum()) > 0
        k3 = float(dexp[k][181:249].sum()) > 0 and float(dexp[k][249:317].sum()) > 0
        mk = m_sel
        contrib = {}
        for sym, cc in zip(c["sym"][mk], ((S[k] - sbar[k]) * v)[mk]):
            contrib[sym] = contrib.get(sym, 0.0) + float(cc)
        top_coin = max(contrib.values()) if contrib else 0.0
        top_sym = max(contrib, key=contrib.get) if contrib else ""
        dexp_tot = float(dexp[k][181:317].sum())
        top5 = float(np.sort(dexp[k][181:317])[-5:].sum())
        k4 = (dexp_tot - max(top_coin, 0.0) > 0) and (dexp_tot - top5 > 0)
        sh = W.sharpe_of(ser[k][181:317])
        k5 = sh >= sh_b
        thr = min(0.0, q05[k])
        k6 = float(dplain[k][317:328].sum()) >= thr
        allk = k1 and k2 and k3 and k4 and k5 and k6
        verdict_k[k] = dict(K1=k1, K2=k2, K3=k3, K4=k4, K5=k5, K6=k6, ALL=allk)
        rows += [("K", "short", k, "SEL", n, float(x), None) for n, x in verdict_k[k].items()]
        rows += [("K", "short", k, "SEL", "top_coin_contrib", top_coin, top_sym), ("K", "short", k, "SEL", "top5_days_delta_exp", top5, None), ("K", "short", k, "SEL", "sharpe", sh, "B: %.3f" % sh_b),
                 ("K", "short", k, "N1", "threshold_k6", thr, "min(0; q05 по 126 окнам из 11 суток) q05 = %.3f" % q05[k])]
        if allk:
            passed.append(k)
    cand = passed[0] if passed else None
    refuted = all(float(dexp[k][181:317].sum()) <= 0 for k in VARIANTS)
    verdict = "подтверждено" if cand else ("опровергнуто" if refuted else "не ясно")
    rows.append(("V", "short", "verdict", "SEL", "outcome", float(1 if cand else (-1 if refuted else 0)), "%s; кандидат %s; прошли %s" % (verdict, cand, passed)))
    # метрики п. 35 по окнам
    stats_rows(rows, "B", ser["B"], cnt, ("SEL", "SEL1", "SEL2", "N1"), nd)
    for k in VARIANTS:
        stats_rows(rows, k, ser[k], cnt, ("SEL", "SEL1", "SEL2", "N1"), nd, "идея видела SEL в форме B^σ" if k in ("V2", "V3") else None)
        for win in ("SEL", "SEL1", "SEL2", "N1"):
            lo, hi = WINS[win]
            sbw = float(S[k][(day >= lo) & (day < hi)].mean())
            sb = np.zeros(nd)
            sb[lo:hi] = sbw * ser["B"][lo:hi]
            stats_rows(rows, k + "b", sb, cnt, (win,), nd, "B × s̄ окна")
    tercile_rows(rows, "D", "SEL", np.concatenate([sel["u"][sel["inv"]], n1["u"][n1["inv"]]]), v, m_sel)
    # критерий остановки (PS-1040 §8 + PS-1041 п. 4)
    for k in VARIANTS:
        for q in (95, 99):
            Q, DQ, rate = kill_tables(dexp[k][181:317], ser[k][181:317], idx, q)
            rows.append(("KILL", "short", k, "q%d" % q, "false_stop_rate", rate, "доля путей SEL с остановкой при n ≤ %d" % KILL_N))
            for n in range(KILL_N):
                rows += [("KILL", "short", k, "q%d" % q, "Q_n%02d" % (n + 1), float(Q[n]), None), ("KILL", "short", k, "q%d" % q, "DD_n%02d" % (n + 1), float(DQ[n]), None)]
        r95 = [r for r in rows if r[0] == "KILL" and r[2] == k and r[3] == "q95" and r[4] == "false_stop_rate"][0][5]
        rows.append(("KILL", "short", k, "chosen", "level", 99.0 if r95 > 0.10 else 95.0, "доля ложных остановок на q95 = %.3f %s 0,10" % (r95, ">" if r95 > 0.10 else "≤")))
    con = con_open()
    put_rows(con, rows, ("COV", "D", "K", "V", "KILL"))
    con.execute("INSERT OR REPLACE INTO tk719_run VALUES('test', ?, ?, ?)", (json.dumps(dict(seed=SEED, nboot=NBOOT, block=BLOCK, freeze=a.freeze)), fz.get("code_commit", ""), datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    print("вердикт: %s (кандидат %s, прошли %s)" % (verdict, cand, passed))
    for k in VARIANTS:
        print("  %s: s̄ %.3f Δ_plain SEL %+.2f N1 %+.2f | Δ_exp SEL %+.2f [%.2f; %.2f] p %.4f Холм %s | K %s" % (k, sbar[k], dplain[k][181:317].sum(), dplain[k][317:328].sum(), dexp[k][181:317].sum(), lb[k], ub[k], p[k], hm[VARIANTS.index(k)][0],
                                                                                                         " ".join("%s=%d" % (n, x) for n, x in verdict_k[k].items())))
    return 0


# ───────────────────────────── ворота и признаки ─────────────────────────────

def cmd_gate(a):
    S = T.Store(TK718_DB)
    plan = {nm: S.ckey("short", nm) for nm in S.members("short")}
    cid_of = {S.plan["short", ck]["cid"]: nm for nm, ck in plan.items()}
    bad = 0
    for grp in a.groups.split(","):
        c = load_group(grp)
        nd = 328
        for cid in MEMBERS:
            nm = cid_of[cid]
            pnl, n = S.series_ck("short", plan[nm])
            m = c["cid"] == cid
            lo, hi = min(CREDIT[grp].values())[0], max(v[1] for v in CREDIT[grp].values()) + 1
            mine, cnt = daily(c["day"][m], c["val"][m], nd)[lo:hi], np.bincount(c["day"][m], minlength=nd)[:nd][lo:hi]
            ref, refn = pnl[lo:hi], n[lo:hi]
            d, dn = float(np.nanmax(np.abs(mine - ref))), int(np.abs(cnt - refn).max())
            print("ворота %s %s (%d): max|Δ P&L| %.2e, max|Δ n| %d, дней %d" % (grp, nm, cid, d, dn, hi - lo))
            if not (d <= 1e-6 and dn == 0):
                bad += 1
    print("ворота: расхождений %d" % bad)
    return 1 if bad else 0


def cmd_features(a):
    coins = [s for s in open(C539).read().strip().split(",") if s]
    cache = {}

    def loader(s):
        if s not in cache:
            cache[s] = load_funding_coin(s)
        return cache[s]
    os.makedirs(FEAT, exist_ok=True)
    for grp in a.groups.split(","):
        c = load_group(grp)
        usym, usig, _ = unique_keys(c)
        F = compute_features(usym, usig, kl_dirs_of(grp), coins, a.procs, loader)
        np.savez_compressed(feat_path(grp), **F)
        print("признаки %s: уникальных сигналов %d, u конечных %.3f, σ̂ конечных %.3f, N_t среднее %.1f" % (grp, len(usym), np.isfinite(F["u"]).mean(), np.isfinite(F["sigma"]).mean(), F["nt"].mean()))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("features")
    s.add_argument("--groups", default="h1")
    s.add_argument("--procs", type=int, default=4)
    s = sp.add_parser("freeze")
    s.add_argument("--code-commit", required=True)
    s = sp.add_parser("test")
    s.add_argument("--freeze", default=FREEZE)
    s = sp.add_parser("gate")
    s.add_argument("--groups", default="h1,sel,n1")
    a = ap.parse_args(argv)
    return {"features": cmd_features, "freeze": cmd_freeze, "test": cmd_test, "gate": cmd_gate}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
