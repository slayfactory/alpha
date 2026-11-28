#!/usr/bin/env python3
"""TK-739 (П-42 шаг 8, §13.81.4 п. 2): парный G-pow8 — синтетика, движок не нужен; только srv (R = 100 миров на тип × 45 разбиений × Π). Также `Design` — таблица точек R ∪ OAT (столбцы поверхности, соседи ±δ) для реального счёта.

  python3 tk739_pow.py design --S 4096          # таблица точек и столбцов → res/design-S<S>.npz (+ .pkl с координатами)
  python3 tk739_pow.py timing --S 4096          # время одной проходки Π (первое действие G-pow8)
  python3 tk739_pow.py run --S 4096 --worlds 100 [--kinds n0,nB,A] [--wmult 1,2,4] [--procs 6]   # → res/gpow8-S<S>.json

Модель мира (§13.81.4 п. 2; допущения названы «модель»): x_p(t) = μ_p + σ·(√a·F(t) + √(1 − a)·G_p(t)); G — гауссово поле по координатам (ядро exp(−d), анизотропное, ℓ по осям из G-cov) × сутки: источники — стандартизованные ряды клеток карты (банк остатков),
сутки перемешаны блоками по 7 (хвосты и автокорреляция сохраняются); F — общий суточный фактор (a — из G-cov); σ — σ суток якоря (G-src); база — строка OAT «base» (μ = m_B*); счёт сделок = сделки B* (все точки допускаются).
Миры: н0 (μ_p = 0, база m_B*), нB (μ_p = m_B*), A (μ_p = m_B* + A·exp(−‖ξ_p − ξ*‖²/2w²), ξ* — случайная точка R, w = δ·wmult, δ = 0,2 в нормированных координатах [−1; 1] — 0,10 длины интервала).
Синтетическая коробка: 270 соседей кандидата — условное гауссово продолжение поля в точки соседей (та же модель), μ — той же формулой. Исход мира — правило §13.81.3 п. 4 (A: PBO ≤ 0,2; B: плато коробки ⅔ на FJ и ½ на SEL; C′ > 0 при блоках 7 и 21; центр не у границы).
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
import argparse
import json
import multiprocessing as mp
import pickle
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")
import tk739_design as dz  # noqa: E402
import tk739_proc as P  # noqa: E402

ROOT = os.environ.get("TK739_ROOT", "/data/p42-tk739")
RES = ROOT + "/res"
NLIN = len(dz.LIN_NAMES)
DELTA_XI = 0.2
MPCTX = "fork" if "fork" in mp.get_all_start_methods() else "spawn"   # srv — fork; ПК (Windows) — spawn, только отладка
FJ_DAYS = 150


class Design:
    def __init__(self, F, NB, NBm, coords, ckeys, roles, Rn, base_idx, cand_ok, labels):
        self.F, self.NB, self.NBm, self.coords, self.ckeys, self.roles = F, NB, NBm, coords, ckeys, roles
        self.Rn, self.base_idx, self.cand_ok, self.labels = Rn, base_idx, cand_ok, labels


_GM = None


def _init_gm(path):
    global _GM
    import tk739_gates as g
    _GM = g.load_gmap()


def _nb_one(c):
    nb = [d2 for _lab, d2 in dz.box_axial(c, _GM)] + dz.box_sobol(c, _GM)
    Q = 14 + 256
    out = np.zeros((Q, dz.n_columns()), np.float32)
    for k, d2 in enumerate(nb[:Q]):
        out[k] = dz.features(d2)
    m = np.zeros(Q, bool)
    m[:min(len(nb), Q)] = True
    return out, m


def build_design(S, procs=6, gm=None):
    import tk739_cells as cells
    import tk739_gates as g
    gm = gm or g.load_gmap()
    pts = [p for p in cells.stage_points("design", gm, S) if p[0] in ("R", "OAT")]
    coords = [p[3] for p in pts]
    F = np.array([dz.features(c) for c in coords])
    with mp.get_context(MPCTX).Pool(procs, initializer=_init_gm, initargs=("",)) as pool:
        res = pool.map(_nb_one, coords, chunksize=16)
    NB = np.stack([r[0] for r in res])
    NBm = np.stack([r[1] for r in res])
    ckeys = [p[5]["ckey"] for p in pts]
    base_ck = dz.anchor_ckey()
    cand_ok = np.array([ck != base_ck for ck in ckeys])
    base_idx = [i for i, p in enumerate(pts) if p[0] == "OAT" and p[2] == "base"][0]
    Rn = sum(p[0] == "R" for p in pts)
    return Design(F, NB, NBm, coords, ckeys, [p[0] for p in pts], Rn, base_idx, cand_ok, [p[2] for p in pts])


def save_design(d, S):
    os.makedirs(RES, exist_ok=True)
    np.savez(RES + "/design-S%d.npz" % S, F=d.F, NB=d.NB, NBm=d.NBm, cand_ok=d.cand_ok)
    with open(RES + "/design-S%d.pkl" % S, "wb") as f:
        pickle.dump(dict(coords=d.coords, ckeys=d.ckeys, roles=d.roles, Rn=d.Rn, base_idx=d.base_idx, labels=d.labels), f)


def load_design(S):
    z = np.load(RES + "/design-S%d.npz" % S)
    with open(RES + "/design-S%d.pkl" % S, "rb") as f:
        m = pickle.load(f)
    return Design(z["F"], z["NB"], z["NBm"], m["coords"], m["ckeys"], m["roles"], m["Rn"], m["base_idx"], z["cand_ok"], m["labels"])


# ───────────────────────────── мир ─────────────────────────────

class Ctx:
    pass


def make_ctx(des, gcov, gsrc, bank, base_n):
    from scipy.linalg import cholesky
    c = Ctx()
    c.des, c.bank, c.base_n = des, bank, base_n
    li = {n: i for i, n in enumerate(dz.LIN_NAMES)}
    import tk739_cov as cv
    ell = np.ones(NLIN)
    for g_, cols in cv.group_cols().items():
        ell[cols] = gcov["ell_by_group"][g_]["ell"]
    c.ell = ell
    c.xi = des.F[:, :NLIN]
    sc = c.xi / ell
    from scipy.spatial.distance import cdist
    D = cdist(sc, sc)
    K = np.exp(-D) + 1e-6 * np.eye(len(sc))
    c.L = cholesky(K, lower=True)
    c.a = float(gcov["fit_iso"]["a"])
    c.sigma = float(gsrc["sigma_day_usd"])
    c.m_B = float(gsrc["m_bstar_usd_per_day"])
    c.cand_ok = des.cand_ok
    return c


def _day_map(rng, nbank=136, n=286, block=7):
    nb = int(np.ceil(n / block))
    st = rng.integers(0, nbank, size=nb)
    return ((st[:, None] + np.arange(block)[None, :]) % nbank).ravel()[:n]


def world_mu(ctx, kind, A, w, rng):
    d = ctx.des
    n = len(d.F)
    if kind == "n0":
        mu = np.zeros(n)
        mu[d.base_idx] = ctx.m_B
        return mu, None
    if kind == "nB":
        return np.full(n, ctx.m_B), None
    star = int(rng.integers(0, d.Rn))
    dist2 = ((ctx.xi - ctx.xi[star][None]) ** 2).sum(1)
    mu = ctx.m_B + A * np.exp(-dist2 / (2 * w * w))
    mu[d.base_idx] = ctx.m_B
    return mu, star


def simulate(ctx, kind, A, wmult, seed):
    rng = np.random.default_rng(seed)
    d = ctx.des
    n = len(d.F)
    w = DELTA_XI * wmult
    didx = _day_map(rng)
    rows = rng.integers(0, len(ctx.bank), size=n + 1)
    Zs = ctx.bank[rows][:, didx].astype(np.float64)
    Fct, Z = Zs[n], Zs[:n]
    G = ctx.L @ Z
    e = ctx.sigma * (np.sqrt(ctx.a) * Fct[None] + np.sqrt(1 - ctx.a) * G)
    mu, star = world_mu(ctx, kind, A, w, rng)
    X = mu[:, None] + e
    return X, mu, star, (Z, Fct, w, rng)


def synth_box(ctx, c, mu_fn, Z, Fct, rng):
    """Серии 270 соседей кандидата c — условное продолжение поля G; Σ$ нужен только по половинам (FJ / SEL). Возвращает (серии (Qm × 286), mask)."""
    from scipy.linalg import solve_triangular
    from scipy.spatial.distance import cdist
    d = ctx.des
    msk = d.NBm[c]
    xq = d.NB[c][:, :NLIN].astype(np.float64)[msk] / ctx.ell
    xo = ctx.xi / ctx.ell
    Kbo = np.exp(-cdist(xq, xo))
    Kbb = np.exp(-cdist(xq, xq))
    V = solve_triangular(ctx.L, Kbo.T, lower=True)                      # L⁻¹ K_ob   (n × q)
    cond = Kbb - V.T @ V + 1e-6 * np.eye(len(xq))
    Lc = np.linalg.cholesky(cond + 1e-9 * np.eye(len(xq)))
    mean_part = V.T @ Z                                                   # K_bo K_oo⁻¹ e_o = (L⁻¹K_ob)ᵀ Z
    z2 = rng.standard_normal((len(xq), Z.shape[1]))
    Gq = mean_part + Lc @ z2
    e = ctx.sigma * (np.sqrt(ctx.a) * Fct[None] + np.sqrt(1 - ctx.a) * Gq)
    return e + mu_fn(xq * ctx.ell)[:, None]


def judge(ctx, X, kind, A, mu_fn, Zinfo, nboot=P.NB_BOOT, nested_boot=P.NB_BOOT, seed=1):
    """Правило O1–O7 в синтетическом мире (A: PBO ≤ 0,2; B: плато коробки; C′ при блоках 7 и 21; граница диапазона)."""
    d = ctx.des
    cnt = np.tile(ctx.base_n[None], (len(X), 1))
    base = X[d.base_idx]
    full = P.select(np.arange(286), X, cnt, base, d.F, d.NB, d.NBm, seed=seed, nboot=nboot, cand_ok=ctx.cand_ok)
    nst = P.nested(X, cnt, base, ctx.base_n, d.F, d.NB, d.NBm, ctx.cand_ok, d.Rn, seed=seed, nboot=nested_boot)
    _, ser = P.path_series(nst["test_delta"], nst["split_ij"])
    c7, _ = P.cprime(ser, 7, B=4000)
    c21, _ = P.cprime(ser, 21, B=4000)
    out = dict(refused=full["c"] is None, pbo=nst["pbo"], c7=c7, c21=c21, ref45=nst["refusals"], invalid=nst["invalid"])
    cond_a = nst["pbo"] <= 0.2
    cond_c = (c7 > 0) and (c21 > 0)
    cond_b, border = False, False
    if full["c"] is not None:
        c = full["c"]
        border = dz.near_border(d.coords[c], None)[0]
        Z, Fct, w, rng = Zinfo
        Xq = synth_box(ctx, c, mu_fn, Z, Fct, rng)
        fj, se = Xq[:, :FJ_DAYS].sum(1) > 0, Xq[:, FJ_DAYS:].sum(1) > 0
        cond_b = bool(fj.mean() >= 2.0 / 3.0 and se.mean() >= 0.5)
        out["c"] = int(c)
    out.update(A=bool(cond_a), B=bool(cond_b), C=bool(cond_c), border=bool(border))
    out["O1"] = bool((not out["refused"]) and cond_a and cond_b and cond_c and not border)
    return out


def one_world(args):
    ctx, kind, A, wmult, seed = args
    t0 = time.time()
    X, mu, star, zi = simulate(ctx, kind, A, wmult, seed)
    muq = None
    if kind == "n0":
        mu_fn = lambda xi: np.zeros(len(xi))                              # noqa: E731
    elif kind == "nB":
        mu_fn = lambda xi: np.full(len(xi), ctx.m_B)                       # noqa: E731
    else:
        xs = ctx.xi[star]
        w = DELTA_XI * wmult
        mu_fn = lambda xi: ctx.m_B + A * np.exp(-((xi - xs[None]) ** 2).sum(1) / (2 * w * w))   # noqa: E731
    r = judge(ctx, X, kind, A, mu_fn, zi, seed=seed)
    r.update(kind=kind, Amp=A, wmult=wmult, seed=seed, sec=time.time() - t0)
    return r


_CTX = None


def _w_init(path_args):
    global _CTX
    S = path_args
    des = load_design(S)
    gcov = json.load(open(RES + "/gcov.json", encoding="utf-8"))
    gsrc = json.load(open(RES + "/gsrc.json", encoding="utf-8"))
    bank = np.load(RES + "/cov-bank.npy")
    base_n = np.load(RES + "/bstar_n.npy").astype(float)
    _CTX = make_ctx(des, gcov, gsrc, bank, base_n)


def _w_run(a):
    kind, A, wmult, seed = a
    return one_world((_CTX, kind, A, wmult, seed))


def cmd_design(a):
    t = time.time()
    d = build_design(a.S, a.procs)
    save_design(d, a.S)
    print("design S=%d: точек %d (R %d), столбцов %d, соседей до %d, %.0f с" % (a.S, len(d.F), d.Rn, d.F.shape[1], d.NB.shape[1], time.time() - t))
    return 0


def cmd_timing(a):
    _w_init(a.S)
    ctx = _CTX
    X, mu, star, zi = simulate(ctx, "nB", 0.0, 2.0, 7)
    cnt = np.tile(ctx.base_n[None], (len(X), 1))
    base = X[ctx.des.base_idx]
    spl = P.splits()
    t0 = time.time()
    r = P.select(spl[0][2], X, cnt, base, ctx.des.F, ctx.des.NB, ctx.des.NBm, cand_ok=ctx.cand_ok)
    t1 = time.time() - t0
    print("t_Π (одно разбиение, один процесс) = %.2f с; допущено %s; кандидат %s" % (t1, r["n_adm"], r["c"]))
    json.dump(dict(t_pi_sec=t1, n_pts=int(len(X)), S=a.S), open(RES + "/gpow8-timing.json", "w"))
    return 0


def cmd_run(a):
    kinds = a.kinds.split(",")
    jobs = []
    for k in kinds:
        if k == "A":
            for wm in [float(x) for x in a.wmult.split(",")]:
                for Af in [float(x) for x in a.amps.split(",")]:
                    for r in range(a.worlds):
                        jobs.append(("A", Af, wm, 1000 * r + int(Af * 100) + int(wm * 7)))
        else:
            for r in range(a.worlds):
                jobs.append((k, 0.0, 2.0, 5000 + r))
    gs = json.load(open(RES + "/gsrc.json", encoding="utf-8"))
    mB = gs["m_bstar_usd_per_day"]
    jobs = [(k, A * mB, wm, s) for k, A, wm, s in jobs]
    out = []
    t0 = time.time()
    with mp.get_context(MPCTX).Pool(a.procs, initializer=_w_init, initargs=(a.S,)) as pool:
        for i, r in enumerate(pool.imap_unordered(_w_run, jobs)):
            out.append(r)
            if (i + 1) % 10 == 0:
                print("%d / %d миров, %.0f с" % (i + 1, len(jobs), time.time() - t0), flush=True)
                json.dump(out, open(RES + "/gpow8-S%d.partial.json" % a.S, "w"))
    json.dump(out, open(RES + "/gpow8-S%d.json" % a.S, "w"))
    summ = {}
    for r in out:
        key = (r["kind"], round(r["Amp"] / mB, 2), r["wmult"])
        v = summ.setdefault(key, dict(n=0, o1=0, pbo=0.0, ref=0))
        v["n"] += 1
        v["o1"] += r["O1"]
        v["pbo"] += r["pbo"]
        v["ref"] += r["refused"]
    for k, v in sorted(summ.items()):
        print(k, "O1 доля %.3f (n=%d), PBO среднее %.3f, отказ полной процедуры %.2f" % (v["o1"] / v["n"], v["n"], v["pbo"] / v["n"], v["ref"] / v["n"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("design", cmd_design), ("timing", cmd_timing), ("run", cmd_run)):
        s = sp.add_parser(name)
        s.add_argument("--S", type=int, required=True)
        s.add_argument("--procs", type=int, default=6)
        s.add_argument("--worlds", type=int, default=100)
        s.add_argument("--kinds", default="n0,nB,A")
        s.add_argument("--wmult", default="2")
        s.add_argument("--amps", default="1")
        s.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
