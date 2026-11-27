#!/usr/bin/env python3
"""TK-718 (PS-1037 §4 T3 / T4, PS-1038): по пулу A — переотбор без эпизода (LOEO) и PBO через CSCV с числом испытаний. numpy, без движка.

  python3 tk718_pool.py pack-short --out /data/p42-tk718/pool-short-full      # шорт 0–327: пул TK-717 (0–180) + `h2-chron-A-short` (181–316) + `h2n-A-short` (317–327), присутствие по `mass_cw`
  python3 tk718_pool.py t3 [--db /data/p42-tk718/tk718.sqlite]                # переотбор: шорт основная (0–118 ∪ 182–327) и строгая (0–118), лонг строгая → M (120–180)
  python3 tk718_pool.py t4 [--db …] [--only short181|short328|long181]        # CSCV S = 16, 12 870 разбиений, без охраны и с охраной на IS; число испытаний, кластеры, MinBTL

Правило отбора — PS-1007 §3 (`tk692_walk`): балл — Шарп окна (ddof = 1, × √365), охрана n ≥ ⌈r · W⌉ (r = 81/136 шорт, 105/136 лонг), максимум балла, ничья — по Σ, затем меньший cid; клетка должна быть посчитана во всех нужных месяцах.
Результаты — `tk718_row` (test T3 / T4) в БД на srv (В-179)."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import itertools
import json
import math
import os
import pickle
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, "/data/p42-tk692/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("TK674_REBASE", "1")
import tk692_walk as W  # noqa: E402
import tk718_tests as T  # noqa: E402

SEED = 718
NRAND = 2000
S_BLOCKS = 16
POOL = {"short": "/data/p42-tk717/pool-short", "long": "/data/p42-tk717/pool-long"}
FULL = "/data/p42-tk718/pool-short-full"
MASS = "/data/p42-tk700/mass/mass.sqlite"
M_DAYS = (120, 181)                                               # M = май–июнь, сутки 120–180
CUT = (119, 182)                                                  # вырезка с отступом E = 1: сутки 119 и 181 — вне окна отбора (и вне M)
M_TRIALS = {"short": 1_765_704, "long": 202_010}                  # сырой счёт отбора линий `tk674-frozen.json`
SR_MINBTL = 1.0


# ───────────────────────────── набор ─────────────────────────────

class Pool:
    """Клетки стороны: X[c, d] P&L, N[c, d] сделок, P[c, m] — посчитана ли клетка в месяце m01…m06 (+ h2, n1 у полного шорта)."""

    def __init__(self, cids, X, N, P, ckeys):
        self.cids, self.X, self.N, self.P, self.ckeys = np.asarray(cids, np.int64), X, N, P, ckeys
        self.nd = X.shape[1]


def load_pool(path):
    ckeys, _meta = pickle.load(open(os.path.join(path, "meta.pkl"), "rb"))
    return Pool(np.load(os.path.join(path, "cids.npy")), np.load(os.path.join(path, "X.npy"), mmap_mode="r"), np.load(os.path.join(path, "N.npy"), mmap_mode="r"), np.load(os.path.join(path, "P.npy")), ckeys)


def pack_short(out, mass_db=MASS, base=POOL["short"]):
    """Шорт 0–327: пул TK-717 (0–180) + mass_day `h2-chron-A-short` (181–316; сутки уже абсолютные от 01.01) + `h2n-A-short` (317–327). P — 6 месяцев + h2 + n1 по строкам `mass_cw`."""
    b = load_pool(base)
    C = len(b.cids)
    X = np.zeros((C, 328))
    N = np.zeros((C, 328), np.int32)
    X[:, :181], N[:, :181] = b.X, b.N
    P = np.zeros((C, 8), bool)
    P[:, :6] = b.P
    con = sqlite3.connect("file:%s?mode=ro" % mass_db, uri=True)
    for j, (run, lo, hi) in enumerate((("h2-chron-A-short", 181, 317), ("h2n-A-short", 317, 328))):
        have = np.array([c for (c,) in con.execute("SELECT cid FROM mass_cw WHERE run_id=?", (run,))], np.int64)
        ii = np.searchsorted(b.cids, have)
        ok = (ii < C) & (b.cids[np.minimum(ii, C - 1)] == have)
        P[ii[ok], 6 + j] = True
        cur = con.execute("SELECT cid, day, pnl, n FROM mass_day WHERE run_id=?", (run,))
        rows = 0
        while True:
            part = cur.fetchmany(1_000_000)
            if not part:
                break
            a = np.array(part, float)
            c = a[:, 0].astype(np.int64)
            ii = np.searchsorted(b.cids, c)
            ok = (ii < C) & (b.cids[np.minimum(ii, C - 1)] == c)
            d = a[:, 1].astype(np.int64)
            if (d[ok] < lo).any() or (d[ok] >= hi).any():
                raise SystemExit("%s: сутки вне %d…%d" % (run, lo, hi - 1))
            X[ii[ok], d[ok]] += a[ok, 2]
            N[ii[ok], d[ok]] += a[ok, 3].astype(np.int32)
            rows += int(ok.sum())
        print("%s: строк mass_day %d, клеток с рядом %d из %d" % (run, rows, int(P[:, 6 + j].sum()), C), flush=True)
    con.close()
    os.makedirs(out, exist_ok=True)
    for nm, arr in (("X", X), ("N", N), ("P", P), ("cids", b.cids)):
        np.save(os.path.join(out, nm + ".npy"), arr)
    pickle.dump((list(b.ckeys), {}), open(os.path.join(out, "meta.pkl"), "wb"), protocol=4)
    json.dump(dict(side="short", cells=C, nd=328, base=base, runs=["h2-chron-A-short", "h2n-A-short"], created=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")), open(os.path.join(out, "info.json"), "w"))
    print("pack-short: %d клеток × 328 суток → %s" % (C, out))


# ───────────────────────────── T3: переотбор без эпизода ─────────────────────────────

def window_scores(X, N, mask, side, chunk=40000):
    """Балл (Шарп окна, ddof = 1, × √365), Σ $, сделки и маска охраны (σ > 1e-9 и n ≥ ⌈r · W⌉) для всех клеток; окно — булева маска суток."""
    C = X.shape[0]
    w = int(mask.sum())
    need = W.n_min(side, w)
    tot, nw = np.empty(C), np.empty(C, np.int64)
    score = np.full(C, -np.inf)
    ok = np.zeros(C, bool)
    for a in range(0, C, chunk):
        xs = np.asarray(X[a:a + chunk])[:, mask]
        t = xs.sum(axis=1)
        sq = (xs * xs).sum(axis=1)
        n = np.asarray(N[a:a + chunk])[:, mask].sum(axis=1)
        mean = t / w
        sd = np.sqrt(np.maximum((sq - w * mean * mean) / (w - 1), 0.0))
        o = (sd > 1e-9) & (n >= need)
        with np.errstate(divide="ignore", invalid="ignore"):
            score[a:a + chunk] = np.where(o, mean / sd * math.sqrt(W.YEAR_DAYS), -np.inf)
        tot[a:a + chunk], nw[a:a + chunk], ok[a:a + chunk] = t, n, o
    return score, tot, nw, ok, w, need


def select_cell(X, N, cids, elig, mask, side):
    """Выбор §3: максимум балла среди клеток elig, прошедших охрану; ничья — большая Σ окна, затем меньший cid. None — нет кандидатов."""
    score, tot, nw, ok, w, need = window_scores(X, N, mask, side)
    cand = np.nonzero(elig & ok)[0]
    if len(cand) == 0:
        return None
    mx = float(score[cand].max())
    top = cand[score[cand] >= mx]
    best = int(top[np.lexsort((cids[top], -tot[top]))[0]])
    return dict(idx=best, cid=int(cids[best]), score=float(score[best]), sum_w=float(tot[best]), n_w=int(nw[best]), m=len(cand), cand=cand, w=w, need=need)


def ckey_diff(a, b):
    """Какие ключи клеток различаются (включая пороги фильтров): список строк «ключ: a → b»."""
    import tk718_cells as cl
    ka, fa = cl.split_ckey(a)
    kb, fb = cl.split_ckey(b)
    out = []
    for k in sorted(set(ka) | set(kb)):
        if ka.get(k) != kb.get(k):
            out.append("%s: %s → %s" % (k, ka.get(k), kb.get(k)))
    da, db = {f: (op, x) for f, op, x in fa}, {f: (op, x) for f, op, x in fb}
    for f in sorted(set(da) | set(db)):
        if da.get(f) != db.get(f):
            out.append("F %s: %s → %s" % (f, da.get(f), db.get(f)))
    return out


def day_mask(nd, parts):
    m = np.zeros(nd, bool)
    for lo, hi in parts:
        m[lo:hi] = True
    return m


def t3_config(pool, side, name, sel_parts, elig, rows, rng):
    """Одна конфигурация переотбора: выбранная клетка применяется к M; 2 000 случайных из прошедших охрану; сравнение с выбором по всей истории."""
    nd = pool.nd
    mask = day_mask(nd, sel_parts)
    pk = select_cell(pool.X, pool.N, pool.cids, elig, mask, side)
    if pk is None:
        rows.append(("T3", side, name, "M", "nodata", float("nan"), "нет клеток, прошедших охрану"))
        return None
    i = pk["idx"]
    xm, nm = np.asarray(pool.X[i, M_DAYS[0]:M_DAYS[1]]), np.asarray(pool.N[i, M_DAYS[0]:M_DAYS[1]])
    st = W.series_stats(xm, nm)
    cand = pk["cand"]
    draw = cand[rng.integers(0, len(cand), NRAND)]
    sm = np.asarray(pool.X[:, M_DAYS[0]:M_DAYS[1]])[draw].sum(axis=1)
    share = W.share_ge(sm, st["sum"])
    T.add_stats(rows, "T3", side, name, "M", st, dict(rand_mean=float(sm.mean()), rand_p10=float(np.percentile(sm, 10)), rand_p90=float(np.percentile(sm, 90)), share=share,
                                                      sel_score=pk["score"], sel_sum=pk["sum_w"], sel_n=float(pk["n_w"]), m=float(pk["m"]), win_days=float(pk["w"]), need=float(pk["need"]), cid=float(pk["cid"])))
    rows.append(("T3", side, name, "M", "ckey", float("nan"), pool.ckeys[i]))
    full = day_mask(nd, [(0, nd)])
    allk = select_cell(pool.X, pool.N, pool.cids, elig, full, side)
    if allk is not None:
        j = allk["idx"]
        sj = W.series_stats(np.asarray(pool.X[j, M_DAYS[0]:M_DAYS[1]]), np.asarray(pool.N[j, M_DAYS[0]:M_DAYS[1]]))
        diff = ckey_diff(pool.ckeys[i], pool.ckeys[j]) if i != j else []
        rows += [("T3", side, name + "_all", "M", "cid", float(allk["cid"]), pool.ckeys[j]), ("T3", side, name + "_all", "M", "sum_M", sj["sum"], "выбор по всей истории (в выборке)"),
                 ("T3", side, name + "_all", "M", "sharpe_M", sj["sharpe"], None), ("T3", side, name, "M", "axes_changed", float(len(diff)), "; ".join(diff))]
    return dict(sum=st["sum"], share=share, cid=pk["cid"])


def t3_run(db, only=None):
    rows = []
    rng = np.random.default_rng(SEED)
    out = {}
    sh = load_pool(FULL)
    ok_months = sh.P[:, :6].all(axis=1)
    ok_full = ok_months & sh.P[:, 6] & sh.P[:, 7]
    main = t3_config(sh, "short", "main", [(0, CUT[0]), (CUT[1], 328)], ok_full, rows, rng)
    strict = t3_config(sh, "short", "strict", [(0, CUT[0])], ok_months, rows, rng)
    lg = load_pool(POOL["long"])
    lstrict = t3_config(lg, "long", "strict", [(0, CUT[0])], lg.P.all(axis=1), rows, rng)
    for side, cfg, nm in (("short", main, "main"), ("long", lstrict, "strict")):
        if cfg is None:
            rows.append(("T3", side, "verdict", "M", "c3b", float("nan"), "нет данных"))
            continue
        ok = bool(cfg["sum"] > 0 and cfg["share"] <= 0.10)
        rows.append(("T3", side, "verdict", "M", "c3b", float(ok), "%s: Σ_M = %.2f $, доля не хуже выбранной %.4f (≤ 0,10)" % (nm, cfg["sum"], cfg["share"])))
        out[side] = ok
    save(db, rows, {"T3"})
    print("T3: %s" % out)
    return out


# ───────────────────────────── T4: PBO через CSCV ─────────────────────────────

def dedupe(X, N):
    """Индексы различных рядов (совпадают X и N целиком) и число клеток на ряд."""
    seen, keep, mult = {}, [], []
    for i in range(X.shape[0]):
        h = hashlib.md5(np.ascontiguousarray(X[i]).tobytes() + np.ascontiguousarray(N[i]).tobytes()).digest()
        j = seen.get(h)
        if j is None:
            seen[h] = len(keep)
            keep.append(i)
            mult.append(1)
        else:
            mult[j] += 1
    return np.array(keep, np.int64), np.array(mult, np.int64)


def block_aggs(X, N, S=S_BLOCKS):
    """Блоки суток (array_split): Σ, Σ квадратов, сделки по блокам (S, C) и длины блоков."""
    nd = X.shape[1]
    parts = np.array_split(np.arange(nd), S)
    s1 = np.stack([X[:, p].sum(axis=1) for p in parts])
    s2 = np.stack([(X[:, p] ** 2).sum(axis=1) for p in parts])
    sn = np.stack([N[:, p].sum(axis=1) for p in parts]).astype(np.float64)
    return s1, s2, sn, np.array([len(p) for p in parts], float)


def sharpe_from(t, q, d):
    """Годовой Шарп из Σ, Σ квадратов и числа суток (ddof = 1); σ = 0 → 0."""
    mean = t / d
    sd = np.sqrt(np.maximum((q - d * mean * mean) / (d - 1), 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 1e-9, mean / sd * math.sqrt(W.YEAR_DAYS), 0.0)


def cscv(X, N, side, chunk=48, S=S_BLOCKS, guard=False):
    """CSCV (Bailey–Borwein–López de Prado–Zhu): все C(S, S/2) разбиений; IS-лучшая клетка по Шарпу (с охраной §3 на IS при guard), её средний относительный ранг ω = ранг / (N + 1) по Шарпу OOS среди клеток (при guard — прошедших охрану на IS).
    Возвращает dict: pbo (доля ω ≤ 0,5), share_neg (доля разбиений с Σ_OOS < 0), slope / corr (OOS Шарп на IS Шарп лучшей), средние IS / OOS Шарпа лучшей, число разбиений."""
    s1, s2, sn, dl = block_aggs(X, N, S)
    C = X.shape[0]
    combos = np.array(list(itertools.combinations(range(S), S // 2)), np.int64)
    nc = len(combos)
    ind = np.zeros((nc, S))
    ind[np.arange(nc)[:, None], combos] = 1.0
    om = 1.0 - ind
    ndays = X.shape[1]
    omega, sis, sos, sum_os = np.empty(nc), np.empty(nc), np.empty(nc), np.empty(nc)
    for a in range(0, nc, chunk):
        I, O = ind[a:a + chunk], om[a:a + chunk]
        d_is, d_os = I @ dl, O @ dl
        t_is, q_is, t_os, q_os = I @ s1, I @ s2, O @ s1, O @ s2
        sh_is = sharpe_from(t_is, q_is, d_is[:, None])
        sh_os = sharpe_from(t_os, q_os, d_os[:, None])
        if guard:
            need = np.ceil(W.RATE[side] * d_is - 1e-9)
            pas = (I @ sn) >= need[:, None]
            sh_sel = np.where(pas, sh_is, -np.inf)
        else:
            pas = np.ones_like(sh_is, bool)
            sh_sel = sh_is
        best = np.argmax(sh_sel, axis=1)
        r = np.arange(len(best))
        bo = sh_os[r, best]
        less = ((sh_os < bo[:, None]) & pas).sum(axis=1)
        eq = ((sh_os == bo[:, None]) & pas).sum(axis=1)
        nv = pas.sum(axis=1)
        omega[a:a + chunk] = (less + (eq + 1) / 2.0) / (nv + 1.0)
        sis[a:a + chunk], sos[a:a + chunk], sum_os[a:a + chunk] = sh_is[r, best], bo, t_os[r, best]
    lam = np.log(omega / (1.0 - omega))
    slope = float(np.polyfit(sis, sos, 1)[0]) if np.std(sis) > 0 else float("nan")
    corr = float(np.corrcoef(sis, sos)[0, 1]) if np.std(sis) > 0 and np.std(sos) > 0 else float("nan")
    return dict(pbo=float((omega <= 0.5).mean()), pbo_logit=float((lam <= 0).mean()), share_neg=float((sum_os < 0).mean()), slope=slope, corr=corr, is_sharpe=float(sis.mean()), os_sharpe=float(sos.mean()),
                splits=nc, days=ndays, omega_mean=float(omega.mean()))


def clusters(Xd, thresholds=(0.5, 0.7, 0.9), seed=SEED, nmax=6000):
    """Число кластеров (связные компоненты графа ρ ≥ порог) в случайной подвыборке до 6 000 различных рядов (зерно 718): {порог: (число компонент, размер подвыборки)}."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components
    rng = np.random.default_rng(seed)
    n = Xd.shape[0]
    idx = np.sort(rng.choice(n, size=min(nmax, n), replace=False))
    Z = Xd[idx].astype(float)
    Z = Z - Z.mean(axis=1, keepdims=True)
    nrm = np.sqrt((Z * Z).sum(axis=1))
    live = nrm > 1e-12
    Zl = Z[live] / nrm[live][:, None]
    R = Zl @ Zl.T
    out = {}
    for th in thresholds:
        k, _ = connected_components(csr_matrix(R >= th), directed=False)
        out[th] = (int(k) + int((~live).sum()), len(idx))
    return out


def min_btl_years(n_trials, sr=SR_MINBTL):
    """MinBTL ≈ 2 ln N / SR² (лет, годовой SR); N ≤ 1 → 0."""
    return 2.0 * math.log(n_trials) / (sr * sr) if n_trials > 1 else 0.0


def t4_config(side, name, X, N, ckeys, rows):
    """Одна конфигурация T4: PBO без охраны / с охраной, число испытаний (клеток, различных рядов, кластеров, m, множитель фильтров), MinBTL против длины данных."""
    nd = X.shape[1]
    keep, mult = dedupe(X, N)
    Xd, Nd = X[keep], N[keep]
    nfil = sum(1 for k in ckeys if " F=" in k or k.startswith("F="))
    rows += [("T4", side, name, "ALL", "cells", float(X.shape[0]), None), ("T4", side, name, "ALL", "distinct_series", float(len(keep)), None),
             ("T4", side, name, "ALL", "cells_with_filter", float(nfil), "без фильтра: %d; множитель испытаний «с фильтром / без» = %.2f" % (X.shape[0] - nfil, nfil / max(1, X.shape[0] - nfil))),
             ("T4", side, name, "ALL", "m_selection", float(M_TRIALS[side]), "сырой счёт отбора линий (tk674-frozen.json)")]
    for guard in (False, True):
        r = cscv(Xd, Nd, side, guard=guard)
        win = "guard" if guard else "noguard"
        for k, v in r.items():
            rows.append(("T4", side, name, win, k, float(v), None))
        print("T4 %s %s %s: PBO %.3f, доля OOS Σ<0 %.3f, наклон %.3f" % (side, name, win, r["pbo"], r["share_neg"], r["slope"]), flush=True)
    cl = clusters(Xd)
    for th, (k, n) in cl.items():
        rows.append(("T4", side, name, "ALL", "clusters_rho%.1f" % th, float(k), "в подвыборке %d различных рядов (нижняя оценка по всему пулу)" % n))
    yrs = nd / 365.0
    rows.append(("T4", side, name, "ALL", "data_years", yrs, None))
    for lab, n in (("distinct", len(keep)), ("clusters_0.7", cl[0.7][0]), ("m", M_TRIALS[side])):
        rows.append(("T4", side, name, "ALL", "minbtl_years_" + lab, min_btl_years(n), "N = %d, SR = 1; длина данных %.2f лет → %s" % (n, yrs, "длины хватает" if min_btl_years(n) <= yrs else "длины НЕ хватает")))


def t4_run(db, only=None):
    rows = []
    cfgs = []
    if only in (None, "short181", "short328"):
        sh = load_pool(FULL)
        okm = sh.P[:, :6].all(axis=1)
        if only in (None, "short181"):
            cfgs.append(("short", "short0-180", np.asarray(sh.X[okm, :181]), np.asarray(sh.N[okm, :181]), [sh.ckeys[i] for i in np.nonzero(okm)[0]]))
        if only in (None, "short328"):
            okf = okm & sh.P[:, 6] & sh.P[:, 7]
            cfgs.append(("short", "short0-327", np.asarray(sh.X[okf]), np.asarray(sh.N[okf]), [sh.ckeys[i] for i in np.nonzero(okf)[0]]))
    if only in (None, "long181"):
        lg = load_pool(POOL["long"])
        ok = lg.P.all(axis=1)
        cfgs.append(("long", "long0-180", np.asarray(lg.X[ok]), np.asarray(lg.N[ok]), [lg.ckeys[i] for i in np.nonzero(ok)[0]]))
    for side, name, X, N, ck in cfgs:
        t4_config(side, name, X, N, ck, rows)
        save(db, rows, {"T4"}, replace_series=name)
    return rows


def save(db, rows, tests, replace_series=None):
    con = sqlite3.connect(db, timeout=120)
    con.executescript(T.TABLES)
    if replace_series:
        con.execute("DELETE FROM tk718_row WHERE test='T4' AND series=?", (replace_series,))
    else:
        for t in tests:
            con.execute("DELETE FROM tk718_row WHERE test=?", (t,))
    con.executemany("INSERT OR REPLACE INTO tk718_row VALUES(?,?,?,?,?,?,?)", [r for r in rows if r[0] in tests and (replace_series is None or r[2] == replace_series)])
    con.execute("INSERT OR REPLACE INTO tk718_run VALUES(?,?,?,?)", ("pool-" + "-".join(sorted(tests)) + (replace_series or ""), json.dumps(dict(seed=SEED, nrand=NRAND, S=S_BLOCKS)), T.git_head(),
                                                                datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("pack-short")
    s.add_argument("--out", default=FULL)
    s.add_argument("--mass", default=MASS)
    for n in ("t3", "t4"):
        s = sp.add_parser(n)
        s.add_argument("--db", default=T.DB)
        if n == "t4":
            s.add_argument("--only", default=None, choices=("short181", "short328", "long181"))
    a = ap.parse_args(argv)
    if a.cmd == "pack-short":
        pack_short(a.out, a.mass)
    elif a.cmd == "t3":
        t3_run(a.db)
    else:
        t4_run(a.db, a.only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
