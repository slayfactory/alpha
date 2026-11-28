#!/usr/bin/env python3
"""TK-735, §13.79.3: этап `outcomes` — Холм 4 / 16, условия (i), (ii), (v), (vii), исходы O1–O7, R*, метрики В-169, якорная клетка, чувствительность к краю окна, DSR.
Реализации мест, где протокол не задаёт формулу, записаны до запуска — летопись L-531. Запуск: `tk735_run.py outcomes` (юнит на srv; читает res/search-*.npy, res/decide-*.json)."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"))     # selection_bias.py (на srv лежит рядом с кодом)
import tk727_s5 as S5  # noqa: E402
import tk735_lib as T  # noqa: E402
import tk735_search as SR  # noqa: E402

KEYS = ("A", "N", "Bn", "C", "E")
ALPHA = 0.05
NBOOT = 20000
TRIALS = dict(distinct=104000, evals=662008, evals_sel_plus_prior=662008 + 2_100_000)    # §13 п. 7: различные (режим, тип, правило); оценки процедуры; SEL + прежние 2,1 млн клеток (план §8)
SENS_WIN = {"FJ": ("m02", "m03", "m04", "m05", "m06"), "SEL": ("wb", "wc")}                # окна склейки, у которых отбрасываются первые 2 суток (список протокола)


# ───────────────────────────── чистые функции ─────────────────────────────

def finite_p(p) -> float:
    return 1.0 if p is None or not np.isfinite(p) else float(p)


def holm_by_block(tests: dict) -> dict:
    """tests: {ключ: {блок: p}} → {ключ: {блок: p по Холму}}; Холм считается отдельно для каждого блока по всем ключам семейства."""
    keys = list(tests)
    blks = sorted({b for k in keys for b in tests[k]}, key=int)
    out = {k: {} for k in keys}
    for b in blks:
        adj = SR.holm_adj([finite_p(tests[k][b]) for k in keys])
        for k, a in zip(keys, adj):
            out[k][b] = a
    return out


def top2_share(daily: np.ndarray) -> float:
    """Доля двух лучших суток в Σ $ (Σ ≤ 0 → NaN: условие (v) не выполнено)."""
    tot = float(np.sum(daily))
    if not tot > 0:
        return float("nan")
    return float(np.sort(daily)[-2:].sum() / tot)


def classify(c: dict) -> str:
    """Таблица исходов §13.79.3, первый сработавший по порядку. Вход: i, pbo, v, vii, dA, dB, ub_a (список верхних границ по блокам), dDX_ok (нижняя граница > 0 после Холма, оба блока), dDX_pt."""
    pbo = c["pbo"]
    ii = bool(pbo <= 0.2)
    i, v, vii = bool(c["i"]), bool(c["v"]), bool(c["vii"])
    if i and ii and v and vii:
        if c["dB"] >= c["dA"]:
            return "O1"
        if c["dA"] - c["dB"] > 0:
            return "O2"
    if i and ii and v and not vii:
        return "O3"
    if (not i or pbo > 0.5) and c["dDX_ok"]:
        return "O4"
    if (all(u <= 0 for u in c["ub_a"]) or pbo > 0.5) and not (c["dDX_pt"] > 0):
        return "O5"
    if all(u < 0.5 for u in c["ub_a"]):
        return "O6"
    return "O7"


def meanf(vals):
    a = np.array(vals, float)
    a = a[np.isfinite(a)]
    return float(a.mean()) if len(a) else float("nan")


def metrics_mean(lst: list) -> dict:
    """Среднее по сплитам числовых метрик run_metrics (nan пропускаются)."""
    if not lst:
        return {}
    out = {k: meanf([m[k] for m in lst]) for k in lst[0] if k != "n_small"}
    out["n_splits"] = len(lst)
    return out


def m_rep(Wt, A, N) -> np.ndarray:
    """M по повторам бутстрепа: среднее по сплитам с отбором Σ A / Σ N."""
    wa, wn = Wt @ A.T, Wt @ N.T
    with np.errstate(invalid="ignore", divide="ignore"):
        m = wa / wn
    ok = wn > 0
    cnt = ok.sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cnt > 0, np.where(ok, m, 0).sum(1) / cnt, np.nan)


def sharpe_daily(daily: np.ndarray) -> tuple:
    """Суточный Шарп и SE по Ло: √((1 + ½ SR²)/nd)."""
    sd = float(np.std(daily, ddof=1))
    sr = float(daily.mean() / sd) if sd > 0 else float("nan")
    return sr, math.sqrt((1 + 0.5 * sr * sr) / len(daily)) if np.isfinite(sr) else float("nan")


# ───────────────────────────── данные режима ─────────────────────────────

class Ctx:
    def __init__(self, reg: str, fz: dict, g: dict):
        z = np.load(os.path.join(T.ROOT, "res", "base-%s.npz" % reg), allow_pickle=False)
        self.reg = reg
        self.r, self.day, self.typ, self.coin, self.X = z["r"], z["day"], z["typ"], z["coin"], z["X"]
        self.kind, self.sym, self.sig_ms = z["kind"], z["sym"], z["sig_ms"]
        self.in_anchor = z["in_anchor"].astype(bool)
        self.nd = int(z["nd"])
        self.sp = np.load(os.path.join(T.ROOT, "res", "splits-%s.npz" % reg), allow_pickle=False)
        info = fz["regimes"][reg]
        self.K = int(info["K"])
        self.th, self.M, self.sigma_t = {}, {}, {}
        for t in range(self.K):
            th = np.array([[np.nan if v is None else v for v in row] for row in info["thresholds"][str(t)]])
            self.th[t] = th
            self.M[t] = T.cond_masks(self.X, th)
            self.sigma_t[t] = g["sigma"][reg]["type_%d" % t]["sigma_t"]
        self.sens_days = []
        for w in SENS_WIN[reg]:
            d0 = S5.day_of(S5.WINDOWS[w][0]) - T.REG[reg][0]
            self.sens_days += [d for d in (d0, d0 + 1) if 0 <= d < self.nd]


def split_items(ctx: Ctx, res: dict) -> list:
    """Сплиты с правилом: (тип, сплит, индексы сделок W_s, маска отбора, правило)."""
    items = []
    for t in range(ctx.K):
        for s in range(T.NSPLIT):
            rule = tuple(res["t%d" % t]["rules"][s][0])
            if not rule:
                continue
            W = np.where(np.isin(ctx.coin, ctx.sp["t%d_W" % t][s]))[0]
            items.append(dict(t=t, s=s, W=W, sel=SR.rule_mask(ctx.M[t][:, W], rule), rule=rule))
    return items


def scope_arrays(ctx: Ctx, items: list, types: list, exclude_coin=None, anchor=False) -> dict:
    """Суточные массивы (сплиты × сутки) отбора на W_s по набору типов; опционально без сделок одной монеты или только клетка якоря."""
    from tk735_run import day_arrays

    out = {k: np.zeros((T.NSPLIT, ctx.nd)) for k in KEYS}
    for it in items:
        if it["t"] not in types:
            continue
        W = it["W"]
        keep = np.ones(len(W), bool)
        if exclude_coin is not None:
            keep &= ctx.coin[W] != exclude_coin
        if anchor:
            keep &= ctx.in_anchor[W]
        d = day_arrays(ctx.M[it["t"]], ctx.r, ctx.day, ctx.nd, W[keep], it["rule"])
        for k in KEYS:
            out[k][it["s"]] += d[k]
    return out


def best_coin(ctx: Ctx, items: list, types: list) -> tuple:
    """Монета с наибольшей Σ $ отобранных сделок W_s по всем сплитам набора; → (номер монеты, Σ $)."""
    tot = np.zeros(int(ctx.coin.max()) + 1)
    for it in items:
        if it["t"] in types:
            W = it["W"][it["sel"]]
            tot += np.bincount(ctx.coin[W], weights=ctx.r[W], minlength=len(tot))
    c = int(np.argmax(tot))
    return c, float(tot[c])


def point_dA(arr: dict) -> float:
    one = np.ones((1, arr["A"].shape[1]), np.float32)
    return float(SR.delta_rep(one, arr["A"], arr["N"], arr["Bn"], arr["C"], arr["E"])[0][0])


def boot_dA(Wt, arr: dict) -> dict:
    rep_a, _ = SR.delta_rep(Wt, arr["A"], arr["N"], arr["Bn"], arr["C"], arr["E"])
    pt = point_dA(arr)
    return dict(SR.interval(rep_a, pt), p=SR.p_one_sided(rep_a, pt))


def metrics_scope(ctx: Ctx, items: list, types: list) -> dict:
    per = {"all": [], "sel": [], "rest": []}
    by = {}
    for it in items:
        if it["t"] in types:
            by.setdefault(it["s"], []).append(it)
    for s, its in by.items():
        W = np.concatenate([it["W"] for it in its])
        sel = np.concatenate([it["sel"] for it in its])
        for name, idx in (("all", W), ("sel", W[sel]), ("rest", W[~sel])):
            per[name].append(T.run_metrics(ctx.day[idx], ctx.r[idx], ctx.kind[idx], ctx.nd))
    return {k: metrics_mean(v) for k, v in per.items()}


def union_stats(ctx: Ctx, items: list, types: list) -> dict:
    ids = np.concatenate([it["W"][it["sel"]] for it in items if it["t"] in types] or [np.zeros(0, np.int64)])
    u = np.unique(ids)
    sig = {(str(ctx.sym[i]), int(ctx.sig_ms[i])) for i in u}
    return dict(unique_trades=int(len(u)), unique_signals=len(sig))


def rstar(ctx: Ctx, t: int) -> dict:
    """R*: процедура L40 один раз на всех сделках монет типа по всем суткам (§13.79.2); условия вместе с абсолютными порогами."""
    idx = np.where(ctx.typ == t)[0]
    rule = SR.search(ctx.M[t], ctx.r, ctx.day, idx, ctx.sigma_t[t], "L40")
    conds = []
    for ci in rule:
        j, k = T.CONDS[ci]
        conds.append(dict(cond=int(ci), feature=T.PHI[j], op="=" if j == T.HOUR_J else ">=", value=int(k) if j == T.HOUR_J else float(ctx.th[t][j, k]), pct=None if j == T.HOUR_J else T.PCTS[k]))
    m = SR.rule_mask(ctx.M[t][:, idx], rule) if rule else np.ones(len(idx), bool)
    return dict(n_type_trades=int(len(idx)), rule=conds, n_selected=int(m.sum()), mean_r_selected_insample=float(ctx.r[idx][m].mean()) if m.any() else float("nan"))


# ───────────────────────────── один (режим, настройка) ─────────────────────────────

def extras(ctx: Ctx, res: dict, setting: str) -> dict:
    """Всё, что требует пересчёта на W_s: (v), клетка якоря, чувствительность, метрики, объединение, n_eff, DSR (пул), R*."""
    items = split_items(ctx, res)
    types = list(range(ctx.K))
    Wt = {blk: SR.day_weights(ctx.nd, blk, NBOOT, T.SEED) for blk in RN_BLOCKS(ctx.nd)}
    out = {}
    scopes = [("pool", types)] + [("t%d" % t, [t]) for t in types]
    for name, tys in scopes:
        arr = scope_arrays(ctx, items, tys)
        # самопроверка: пересчёт совпадает с массивами этапа search
        ref = {k: sum(res["t%d" % t]["same_%s" % k] for t in tys) for k in KEYS}
        assert all(np.allclose(arr[k], ref[k], atol=1e-9) for k in KEYS), (ctx.reg, setting, name)
        daily = arr["A"].sum(0)
        share = top2_share(daily)
        bc, bc_sum = best_coin(ctx, items, tys)
        arr_nb = scope_arrays(ctx, items, tys, exclude_coin=bc)
        e = dict(sum_selected=float(daily.sum()), top2_share=share, best_coin=ctx.sym[ctx.coin == bc][0] if (ctx.coin == bc).any() else None, best_coin_sum=bc_sum,
                 dA_no_best_coin=point_dA(arr_nb), dA=point_dA(arr))
        e["v"] = bool(np.isfinite(share) and share <= 0.5 and e["dA_no_best_coin"] > 0)
        e["metrics"] = metrics_scope(ctx, items, tys)
        e["union"] = union_stats(ctx, items, tys)
        nsel = arr["N"].sum(1)
        e["n_bar"] = float(nsel[nsel > 0].mean()) if (nsel > 0).any() else float("nan")
        if name == "pool":
            arr_an = scope_arrays(ctx, items, tys, anchor=True)
            arr_se = {k: v.copy() for k, v in arr.items()}
            for k in KEYS:
                arr_se[k][:, ctx.sens_days] = 0.0
            e["anchor"] = {str(b): boot_dA(w, arr_an) for b, w in Wt.items()}
            e["anchor"]["n_sel"] = int(arr_an["N"].sum(1).mean())
            e["sens_no_first2"] = {str(b): dict(boot_dA(w, arr_se), M=meanf(m_rep(np.ones((1, ctx.nd), np.float32), arr_se["A"], arr_se["N"]))) for b, w in Wt.items()}
            e["sens_days"] = [int(d) for d in ctx.sens_days]
            first = list(Wt)[0]
            mr = m_rep(Wt[first], arr["A"], arr["N"])
            sem = float(np.nanstd(mr, ddof=1))
            sig_all = float(ctx.r.std(ddof=1))
            e["se_M_boot"] = sem
            e["n_eff"] = float((sig_all / sem) ** 2) if sem > 0 else float("nan")
            ok = arr["N"].sum(1) > 0
            ds = arr["A"][ok].mean(0)
            sr, se = sharpe_daily(ds)
            e["dsr"] = {}
            for nm, n_tr in TRIALS.items():
                if nm == "evals_sel_plus_prior" and ctx.reg != "SEL":
                    continue
                import selection_bias as SB

                e["dsr"][nm] = SB.significance(sr, se, n_tr)
        out[name] = e
    out["rstar"] = {str(t): rstar(ctx, t) for t in types} if setting == "L40" else None
    return out


def RN_BLOCKS(nd):
    return (math.ceil(nd ** (1 / 3)), 21)


# ───────────────────────────── исходы ─────────────────────────────

def scope_inputs(dec: dict, reg: str, scope: str):
    """Значения бутстрепа этапа decide по (режим, охват): {блок: {…}}, PBO."""
    r = dec[reg]
    K = r["K"]
    blks = sorted(r["blocks"], key=int)
    if scope == "pool":
        return {b: r["blocks"][b]["pool"] for b in blks}, r["pbo"]["pool"]["share"]
    t = scope[1:]
    return {b: r["blocks"][b]["types"][t] for b in blks}, r["pbo"]["types"][t]["share"]


def outcomes(dec: dict, ex: dict, setting: str) -> dict:
    """Холм (только L40) и исходы по (режим, охват). ex — extras по режимам."""
    regs = [r for r in ("FJ", "SEL") if r in dec]
    descriptive = setting != "L40"
    fam_pool, fam_type = {}, {}
    for reg in regs:
        K = dec[reg]["K"]
        for scope in ["pool"] + ["t%d" % t for t in range(K)]:
            vals, _ = scope_inputs(dec, reg, scope)
            fam = fam_pool if scope == "pool" else fam_type
            for meas in ("dA", "dDX"):
                fam[(reg, scope, meas)] = {b: vals[b][meas]["p"] for b in vals}
    holm_pool = holm_by_block({k: v for k, v in fam_pool.items()})
    holm_type = holm_by_block({k: v for k, v in fam_type.items()})
    res = {}
    for reg in regs:
        K = dec[reg]["K"]
        for scope in ["pool"] + ["t%d" % t for t in range(K)]:
            vals, pbo = scope_inputs(dec, reg, scope)
            fam, holm = (fam_pool, holm_pool) if scope == "pool" else (fam_type, holm_type)
            adj_a = {b: (fam[(reg, scope, "dA")][b] if descriptive else holm[(reg, scope, "dA")][b]) for b in vals}
            adj_d = {b: (fam[(reg, scope, "dDX")][b] if descriptive else holm[(reg, scope, "dDX")][b]) for b in vals}
            pt = vals[next(iter(vals))]
            ubk = "ub_bonf4" if scope == "pool" else "ub_bonf16"
            e = ex[reg][scope]
            dX, dA, dB = pt["dX"]["point"], pt["dA"]["point"], pt["dB"]
            c = dict(
                i=all(finite_p(adj_a[b]) <= ALPHA for b in vals) and dA > 0,
                pbo=pbo if pbo == pbo else float("nan"),
                v=e["v"],
                vii=bool(np.isfinite(dX) and dX > 0 and dX >= 0.5 * dA),
                dA=dA, dB=dB, ub_a=[vals[b]["dA"][ubk] for b in vals],
                dDX_ok=all(finite_p(adj_d[b]) <= ALPHA for b in vals) and pt["dDX"]["point"] > 0,
                dDX_pt=pt["dDX"]["point"],
            )
            res["%s/%s" % (reg, scope)] = dict(outcome=classify(c), descriptive=descriptive, inputs=c, holm_dA=adj_a, holm_dDX=adj_d, M=pt["M"], dD=pt["dD"]["point"], dX=dX,
                                               dDX=pt["dDX"]["point"], dA_lo={b: vals[b]["dA"]["lo"] for b in vals}, dA_hi={b: vals[b]["dA"]["hi"] for b in vals},
                                               dDX_lo={b: vals[b]["dDX"]["lo"] for b in vals}, pbo=pbo)
    return res


def run():
    fz = json.load(open(os.path.join(T.ROOT, "freeze", "tk735-frozen.json"), encoding="utf-8"))
    g = json.load(open(os.path.join(T.ROOT, "res", "g_prep.json"), encoding="utf-8"))
    ctxs = {reg: Ctx(reg, fz, g) for reg in ("FJ", "SEL") if "stop" not in fz["regimes"][reg]}
    full = {}
    for setting in ("L40", "LF"):
        dec = json.load(open(os.path.join(T.ROOT, "res", "decide-%s.json" % setting), encoding="utf-8"))
        ex = {}
        for reg, ctx in ctxs.items():
            t0 = time.time()
            res = np.load(os.path.join(T.ROOT, "res", "search-%s-%s.npy" % (reg, setting)), allow_pickle=True)[0]
            ex[reg] = extras(ctx, res, setting)
            print(reg, setting, "extras за %.0f с" % (time.time() - t0), flush=True)
        out = outcomes(dec, ex, setting)
        full[setting] = dict(outcomes=out, extras=ex)
        for k, v in out.items():
            print(setting, k, v["outcome"], "M=%.3f dA=%.3f dB=%.3f dX=%.3f dDX=%.3f pbo=%.2f" % (v["M"], v["inputs"]["dA"], v["inputs"]["dB"], v["dX"], v["dDX"], v["pbo"]), flush=True)
    path = os.path.join(T.ROOT, "res", "outcomes.json")
    json.dump(full, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=lambda o: bool(o) if isinstance(o, np.bool_) else float(o))
    write_db(full)
    print("outcomes записано:", path)


def write_db(full: dict):
    con = sqlite3.connect(T.DB)
    con.execute("CREATE TABLE IF NOT EXISTS t735_outcome (regime TEXT, setting TEXT, scope TEXT, outcome TEXT, descriptive INTEGER, M REAL, dA REAL, dD REAL, dX REAL, dDX REAL, dB REAL, pbo REAL, top2_share REAL, dA_no_best_coin REAL, detail TEXT, PRIMARY KEY (regime, setting, scope))")
    for setting, d in full.items():
        for key, v in d["outcomes"].items():
            reg, scope = key.split("/")
            e = d["extras"][reg][scope]
            con.execute("INSERT OR REPLACE INTO t735_outcome VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (reg, setting, scope, v["outcome"], int(v["descriptive"]), v["M"], v["inputs"]["dA"], v["dD"], v["dX"], v["dDX"], v["inputs"]["dB"], v["pbo"], e["top2_share"], e["dA_no_best_coin"],
                         json.dumps(dict(outcome=v, extras=e), ensure_ascii=False, default=lambda o: bool(o) if isinstance(o, np.bool_) else float(o))))
    con.commit()
    con.close()
