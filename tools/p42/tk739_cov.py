#!/usr/bin/env python3
"""TK-739 (П-42 шаг 8, §13.81.4 п. 1.5–1.6): G-cov (ковариация для G-pow8) и G-count + ONC (планка DSR) — только srv, чтение готовых суточных рядов карты (`metric_cur` / `metric.extra.daily`, SEL 01.07–13.11, T = 136). P&L точек шага 8 не читается.

  python3 tk739_cov.py gcov     # σ одной линии, корреляция как функция расстояния, ℓ по осям, банк остатков → res/gcov.json, res/cov-bank.npy
  python3 tk739_cov.py gcount   # число клеток (chron cells-count), ONC (k-средние, качество t = mean/std силуэта) на ряду карты, σ испытаний, планка DSR → res/gcount.json

Допущения (amend L-552: детали, которых нет в протоколе; от P&L точек не зависят):
  • расстояние между клетками — в нормированных координатах поверхности (`tk739_design.features`, линейная часть: [−1; 1], one-hot, индикаторы «выкл.»), анизотропное: d = √Σ(Δξ_a / ℓ_a)²;
  • корреляция суточных рядов ρ(d) = a + (1 − a)·exp(−d): `a` — общий суточный фактор (далёкие клетки), ℓ_a — по осям карты (n_bounce, k, natr_min, nf, LS, F:sharp, F:n60); для новых осей — медиана ℓ, метка «модель»;
  • ONC: число кластеров по критерию LdP (качество = mean(силуэт) / std(силуэт)) на случайной выборке клеток карты (зерно 20261127), размеры выборки 1 000 / 2 000 / 4 000 — рост K печатается; решающее число — K на наибольшей выборке (нижняя граница полного N_eff: планка DSR не занижена).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, "/data/p42-tk689/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("TK674_REBASE", "1")

ROOT = os.environ.get("TK739_ROOT", "/data/p42-tk739")
RES = ROOT + "/res"
CHRON = "/data/chron/chron.sqlite"
SEED = 20261127
T_MAP = 136


def jwrite(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)


def family():
    """Якорь и клетки уровней 1–2 карты (шорт, c539): [(id, axes{ось: метка}, daily[136])]; якорь — первым."""
    import tk689_influence as T
    import tk726_cond as C
    S = C.load_side(CHRON, "short", T, print)
    low = S["low"]
    ids = {v[0]: dict(axes) for axes, v in low.items()}
    daily = C.fetch_daily(S["con"], list(ids) + [S["anchor"][0]], T)
    out = [(S["anchor"][0], {}, daily[S["anchor"][0]])]
    for cid, ax in ids.items():
        if cid in daily and cid != S["anchor"][0]:
            out.append((cid, ax, daily[cid]))
    return out


def expand_axes(axes):
    """Метка оси может быть составной («k=1,nf=1» — правило RULES свело несколько ключей в одну ось): раскрыть в {ключ: значение}; None — если не разобрать."""
    out = {}
    for name, val in axes.items():
        if name.startswith("F:") or "=" not in str(val):
            out[name] = val
            continue
        for part in str(val).split(","):
            k, sep, v = part.partition("=")
            if not sep:
                return None
            out[k] = v
    return out


def axis_xi(axes, gm):
    """Координаты клетки карты в нормированном пространстве поверхности (линейная часть `features`) или None, если у клетки есть ось вне пространства шага 8."""
    import tk739_design as dz
    ok = {"n_bounce", "k", "natr_min", "nf", "lx", "tf", "F:sharp", "F:n60"}
    axes = expand_axes(axes)                                        # составные метки «natr_min=0.19,nf=1» → отдельные ключи
    if axes is None or set(axes) - ok:
        return None
    c = dict(LS=(int(float(axes.get("lx", 0))), int(float(axes.get("tf", 60)))), n_bounce=int(float(axes.get("n_bounce", 2))), k=int(float(axes.get("k", 2))), nf=int(float(axes.get("nf", 0))),
             u_lo=0.0, q_hi=None, n60=None, sharp=("F:sharp" in axes), phi=None, w=None)
    if c["LS"] not in dz.LS_VALUES or c["nf"] not in dz.NF_VALUES:
        return None
    nm = float(axes.get("natr_min", 0.4))
    if nm < dz.BASE_NATR - 1e-9:
        return None                                                 # слабее базы — вне пространства U1
    c["u_lo"] = float(min(max((gm.natr30 < nm).mean(), 0.0), dz.U_LO[1]))
    if "F:n60" in axes:
        try:
            x = float(str(axes["F:n60"]).lstrip("<>="))
        except ValueError:
            return None
        c["n60"] = float(min(max((gm.n60 < x).mean(), dz.N60_Q[0]), dz.N60_Q[1]))
    return c


GROUPS = {"n_bounce": ("n_bounce",), "k": ("k",), "u_lo": ("u_lo",), "q_hi": ("q_hi", "q_off"), "n60": ("n60", "n60_off"), "w": ("w", "phi_s", "phi_c", "w_off"),
          "LS": ("LS0", "LS1", "LS2", "LS3", "LS4"), "nf": ("nf8",), "sharp": ("sharp",)}


def group_cols():
    import tk739_design as dz
    li = {n: i for i, n in enumerate(dz.LIN_NAMES)}
    return {g: [li[n] for n in ns] for g, ns in GROUPS.items()}


def cmd_gcov(a):
    import tk739_design as dz
    import tk739_gates as g
    from scipy.optimize import curve_fit
    gm = g.load_gmap()
    fam = family()
    rep = dict(n_family=len(fam), T=T_MAP)
    D = np.array([f[2] for f in fam], float)
    n_nz = (D != 0).sum(axis=1)
    sd = D.std(axis=1, ddof=1)
    live = (sd > 0) & (n_nz >= 20)
    rep["sigma_anchor"] = float(sd[0])
    rep["sigma_line"] = dict(n=int(live.sum()), mean=float(sd[live].mean()), p10=float(np.percentile(sd[live], 10)), p90=float(np.percentile(sd[live], 90)), note="σ суточного $ одной клетки семейства (уровни 1–2), T = 136; не медиана доходов стратегии — параметр шума")
    # банк остатков: стандартизованные ряды клеток (среднее 0, σ 1)
    bank = (D[live] - D[live].mean(axis=1, keepdims=True)) / sd[live][:, None]
    os.makedirs(RES, exist_ok=True)
    np.save(RES + "/cov-bank.npy", bank.astype(np.float32))
    rep["bank_shape"] = list(bank.shape)
    # координаты клеток в пространстве поверхности
    cols = group_cols()
    xi, idx = [], []
    for i, (cid, ax, d) in enumerate(fam):
        c = axis_xi(ax, gm)
        if c is None or not live[i]:
            continue
        xi.append(dz.features(c)[:len(dz.LIN_NAMES)])
        idx.append(i)
    xi = np.array(xi)
    rep["n_in_space"] = len(idx)
    Z = (D[idx] - D[idx].mean(axis=1, keepdims=True)) / sd[idx][:, None]
    R = (Z @ Z.T) / (T_MAP - 1)
    # расстояние (изотропное, по группам с единичной длиной) → ρ(d)
    diff = xi[:, None, :] - xi[None, :, :]
    d2 = (diff ** 2).sum(axis=2)
    iu = np.triu_indices(len(idx), 1)
    dist = np.sqrt(d2[iu])
    rho = R[iu]
    rng = np.random.default_rng(SEED)
    bins = [0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 10.0]
    tab = []
    xs, ys = [], []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = np.nonzero((dist >= lo) & (dist < hi))[0]
        if len(m) > 20000:
            m = rng.choice(m, 20000, replace=False)
        if len(m):
            tab.append(dict(d_lo=lo, d_hi=hi, pairs=int(len(m)), rho_mean=float(rho[m].mean())))
            xs += dist[m].tolist()
            ys += rho[m].tolist()
    rep["rho_by_distance"] = tab
    f = lambda d, aa, ll: aa + (1 - aa) * np.exp(-d / ll)       # noqa: E731
    try:
        (a_f, l_f), _ = curve_fit(f, np.array(xs), np.array(ys), p0=(0.5, 1.0), bounds=([0.0, 0.05], [0.999, 20.0]))
    except Exception as e:                                          # noqa: BLE001
        a_f, l_f = 0.5, 1.0
        rep["fit_error"] = str(e)
    rep["fit_iso"] = dict(a=float(a_f), ell=float(l_f))
    # ℓ по осям: пары «якорь — клетка уровня 1 по одной оси»
    ell = {}
    base_xi = dz.features(dict(LS=dz.BASE_LS, n_bounce=dz.BASE_NB, k=dz.BASE_K, nf=dz.BASE_NF, u_lo=0.0, q_hi=None, n60=None, sharp=False, phi=None, w=None))[:len(dz.LIN_NAMES)]
    pts = {gname: [] for gname in cols}
    for j, i in enumerate(idx):
        ax = fam[i][1]
        if len(ax) != 1 or "=" in str(next(iter(ax.values()))):
            continue                                                # составная ось (несколько ключей сразу) в оценку ℓ по одной оси не идёт
        name = next(iter(ax))
        gname = {"n_bounce": "n_bounce", "k": "k", "natr_min": "u_lo", "nf": "nf", "lx": "LS", "tf": "LS", "F:sharp": "sharp", "F:n60": "n60"}.get(name)
        if gname is None:
            continue
        dd = float(np.sqrt(((xi[j] - base_xi)[cols[gname]] ** 2).sum()))
        r = float(np.corrcoef(D[0], D[i])[0, 1]) if D[i].std() > 0 else float("nan")
        if dd > 0 and np.isfinite(r):
            pts[gname].append((dd, r))
    for gname, v in pts.items():
        if len(v) >= 2:
            dd = np.array([x for x, _ in v])
            rr = np.array([y for _, y in v])
            rat = np.clip((rr - a_f) / (1 - a_f), 1e-3, 0.999)
            ell[gname] = dict(ell=float(np.clip(-dd.mean() / np.log(rat.mean()), 0.05, 20.0)), n=len(v), source="карта")
    known = [v["ell"] for v in ell.values()]
    med = float(np.median(known)) if known else float(l_f)
    for gname in cols:
        if gname not in ell:
            ell[gname] = dict(ell=med, n=0, source="модель (медиана ℓ известных осей)")
    rep["ell_by_group"] = ell
    rep["ell_median_known"] = med
    jwrite(RES + "/gcov.json", rep)
    print(json.dumps(rep, ensure_ascii=False, indent=1)[:5000])
    return 0


# ───────────────────────────── ONC ─────────────────────────────

def _kmeans(X, k, rng, iters=30):
    """k-средние (Ллойд, k-means++); X — n × p."""
    n = len(X)
    c = X[rng.integers(n)][None]
    d2 = ((X - c[0]) ** 2).sum(1)
    for _ in range(1, k):
        pr = d2 / d2.sum() if d2.sum() > 0 else np.full(n, 1.0 / n)
        c = np.vstack([c, X[rng.choice(n, p=pr)]])
        d2 = np.minimum(d2, ((X - c[-1]) ** 2).sum(1))
    xx = (X ** 2).sum(1)[:, None]
    lab = None
    for _ in range(iters):
        dm = xx - 2 * X @ c.T + (c ** 2).sum(1)[None]
        nl = dm.argmin(1)
        if lab is not None and (nl == lab).all():
            break
        lab = nl
        for j in range(k):
            m = lab == j
            if m.any():
                c[j] = X[m].mean(0)
    return lab


def _silhouette(Dm, lab):
    """Силуэт каждой точки по матрице расстояний Dm (n × n) и меткам."""
    k = int(lab.max()) + 1
    oh = np.zeros((len(lab), k))
    oh[np.arange(len(lab)), lab] = 1.0
    cnt = oh.sum(0)
    s = Dm @ oh                                                       # сумма расстояний до каждого кластера
    own = s[np.arange(len(lab)), lab] / np.maximum(cnt[lab] - 1, 1)
    other = s / np.maximum(cnt[None], 1)
    other[np.arange(len(lab)), lab] = np.inf
    b = other.min(1)
    out = (b - own) / np.maximum(np.maximum(own, b), 1e-12)
    out[cnt[lab] <= 1] = 0.0
    return out


def onc_base(corr, kmax, n_init, rng):
    """LdP ONC, базовый шаг: расстояние √((1 − ρ)/2), строки как признаки; лучший k по качеству mean/std силуэта. Возвращает (метки, качество каждой точки-силуэт, k)."""
    Dm = np.sqrt(np.clip((1 - np.nan_to_num(corr)) / 2.0, 0, None))
    best = (-np.inf, None, None)
    for k in range(2, kmax + 1):
        for _ in range(n_init):
            lab = _kmeans(Dm, k, rng)
            if len(np.unique(lab)) < 2:
                continue
            lab = np.unique(lab, return_inverse=True)[1]
            sil = _silhouette(Dm, lab)
            q = sil.mean() / max(sil.std(), 1e-12)
            if q > best[0]:
                best = (q, lab, sil)
    return best[1], best[2], int(best[1].max()) + 1


def onc(series, kmax=40, n_init=3, seed=SEED):
    """ONC: базовый шаг + одно улучшение (перекластеризация кластеров с качеством ниже среднего, принимается при росте среднего качества). Возвращает число кластеров."""
    rng = np.random.default_rng(seed)
    corr = np.corrcoef(series)
    lab, sil, k = onc_base(corr, min(kmax, len(series) // 4), n_init, rng)
    q = np.array([sil[lab == j].mean() / max(sil[lab == j].std(), 1e-12) if (lab == j).sum() > 1 else 0.0 for j in range(k)])
    weak = np.nonzero(q < q.mean())[0]
    if len(weak) >= 2:
        m = np.isin(lab, weak)
        sub = corr[np.ix_(m, m)]
        lab2, sil2, k2 = onc_base(sub, min(kmax, int(m.sum()) // 4), n_init, rng)
        if lab2 is not None:
            new = lab.copy()
            keep_ids = np.unique(lab[~m])
            new[m] = lab2 + int(lab.max()) + 1
            full = np.unique(new, return_inverse=True)[1]
            Dm = np.sqrt(np.clip((1 - np.nan_to_num(corr)) / 2.0, 0, None))
            s_new = _silhouette(Dm, full)
            if s_new.mean() / max(s_new.std(), 1e-12) > sil.mean() / max(sil.std(), 1e-12):
                return int(full.max()) + 1, int(k)
    return int(k), int(k)


def map_sample(n, seed):
    """n случайных клеток карты шорта (c539, SEL), у которых есть суточный ряд (T = 136), не стоп 5 % / ew0 (как карта влияния)."""
    import tk689_influence as T
    import tk726_cond as C
    con = sqlite3.connect("file:%s?mode=ro" % CHRON, uri=True, timeout=120)
    ids = [r[0] for r in con.execute("SELECT id FROM metric_cur WHERE ckey IS NOT NULL AND uni=? AND win=? AND side='short'", (T.UNI, T.WIN))]
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(ids), min(len(ids), int(n * 1.6)), replace=False)
    got = C.fetch_daily(con, [ids[i] for i in pick], T)
    rows = [v for v in got.values() if len(v) == T_MAP and v.std() > 0 and (v != 0).sum() >= 20]
    return np.array(rows[:n]), len(ids)


def cmd_gcount(a):
    sys.path.insert(0, "/data/p42-tk739/code")
    import selection_bias as sb
    rep = dict(note="G-count + ONC (§13.81.4 п. 1.6): число клеток, число кластеров ONC на рядах карты (T = 136), планка DSR")
    rep["cells_total_chron_cells_count"] = int(a.cells_total)
    sizes = [int(x) for x in a.sizes.split(",")]
    ks = {}
    sr_all = None
    n_total = None
    for m in sizes:
        X, n_total = map_sample(m, SEED + m)
        sr = X.mean(axis=1) / X.std(axis=1, ddof=1)
        k, k0 = onc(X, kmax=a.kmax, n_init=a.n_init)
        ks[m] = dict(n_series=int(len(X)), K_base=int(k0), K=int(k), sr_daily_std=float(sr.std(ddof=1)), sr_daily_mean=float(sr.mean()))
        print("ONC m=%d: K=%d (база %d), σ SR суточного = %.4f" % (m, k, k0, sr.std(ddof=1)), flush=True)
        sr_all = sr
    rep["onc"] = ks
    rep["map_cells_short_c539"] = int(n_total)
    big = ks[max(sizes)]
    sig_trials = big["sr_daily_std"]
    rep["sigma_trials_T136"] = float(sig_trials)
    rep["sigma_trials_T286"] = float(sig_trials * math.sqrt(136 / 286))
    rep["N_eff"] = int(big["K"])
    rep["bar_daily_T286_at_N_eff"] = float(sb.expected_max_sharpe(rep["sigma_trials_T286"], int(big["K"])))
    rep["bar_annual"] = float(rep["bar_daily_T286_at_N_eff"] * math.sqrt(365))
    jwrite(RES + "/gcount.json", rep)
    print(json.dumps({k: v for k, v in rep.items() if k != "cells_count_raw"}, ensure_ascii=False, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("gcov").set_defaults(fn=cmd_gcov)
    c = sp.add_parser("gcount")
    c.add_argument("--cells-total", type=int, default=2115238)
    c.add_argument("--sizes", default="1000,2000,4000")
    c.add_argument("--kmax", type=int, default=40)
    c.add_argument("--n-init", type=int, default=3)
    c.set_defaults(fn=cmd_gcount)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
