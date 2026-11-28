#!/usr/bin/env python3
"""TK-735, §13.79.3 «Печатается» и §13.79.1 (FIX проверяющего, п. 1, 3): печати, которые протокол требует и которых не было в отчёте. Считаются из готовых `res/search-*.npy`, `res/base-*.npz`,
`res/decide-*.json`, `res/outcomes.json`, `splits-*.npz` и `t735_split` — движок и поиск заново не запускаются. → `res/prints.json`.

  корреляции суточных рядов Σ $ «корзина B_s — двойник W_s — случайные монеты C_s» (отобранные сделки правилом R_s и все сделки), среднее и разброс по сплитам, доля суток с обеими сторонами;
  частота выбора правил по сплитам; число сплит-типов с «правила нет» (R_s, R^чёт, R^неч → Δ_X); таблица по месяцам режима (описательно); баланс B_s / W_s (стандартизованная разность, KS);
  PBO без недопустимых R_s и исходы при нём (L-525 п. 2: недопустимое на W_s правило считается подгонкой — доля 5–26 %)."""
from __future__ import annotations

import collections
import datetime
import json
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk735_lib as T  # noqa: E402
import tk735_out as OU  # noqa: E402
import tk735_search as SR  # noqa: E402

RES = os.path.join(T.ROOT, "res")
PAIRS = (("B", "W"), ("B", "C"), ("W", "C"))


def series_for(ctx, t: int, coins, rule) -> dict:
    """Суточные ряды набора монет: A, N — Σ r и число сделок под правилом; C, E — все сделки."""
    idx = np.where(np.isin(ctx.coin, coins))[0]
    mask = SR.rule_mask(ctx.M[t][:, idx], rule)
    E, C = SR.agg_days(ctx.day[idx], ctx.r[idx], np.ones(len(idx), bool), ctx.nd)
    N, A = SR.agg_days(ctx.day[idx], ctx.r[idx], mask, ctx.nd)
    return dict(A=A, N=N, C=C, E=E)


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def rule_name(ci) -> str:
    j, k = T.CONDS[ci]
    return "%s=%d" % (T.PHI[j], k) if j == T.HOUR_J else "%s≥p%d" % (T.PHI[j], T.PCTS[k])


def stats(v: list) -> dict:
    a = np.array(v, float)
    a = a[np.isfinite(a)]
    return dict(mean=float(a.mean()) if len(a) else float("nan"), sd=float(a.std(ddof=1)) if len(a) > 1 else float("nan"), n=int(len(a)))


def corr_prints(ctx, res: dict) -> dict:
    """По сплитам: ряды трёх наборов, сложенные по типам (пул) и по каждому типу."""
    K = ctx.K
    per = {sc: {p: {"sel": [], "all": []} for p in PAIRS} for sc in ["pool"] + ["t%d" % t for t in range(K)]}
    both = {sc: {p: {"sel": [], "all": []} for p in PAIRS} for sc in per}
    for s in range(T.NSPLIT):
        by_t = {}
        for t in range(K):
            rule = tuple(res["t%d" % t]["rules"][s][0])
            if not rule:
                continue
            by_t[t] = {nm: series_for(ctx, t, ctx.sp["t%d_%s" % (t, nm)][s], rule) for nm in ("B", "W", "C")}
        if not by_t:
            continue
        scopes = [("pool", list(by_t))] + [("t%d" % t, [t]) for t in by_t]
        for sc, tys in scopes:
            ser = {nm: {k: sum(by_t[t][nm][k] for t in tys) for k in ("A", "N", "C", "E")} for nm in ("B", "W", "C")}
            for p in PAIRS:
                x, y = p
                per[sc][p]["sel"].append(pearson(ser[x]["A"], ser[y]["A"]))
                per[sc][p]["all"].append(pearson(ser[x]["C"], ser[y]["C"]))
                both[sc][p]["sel"].append(float(((ser[x]["N"] > 0) & (ser[y]["N"] > 0)).mean()))
                both[sc][p]["all"].append(float(((ser[x]["E"] > 0) & (ser[y]["E"] > 0)).mean()))
    return {sc: {"%s-%s" % p: dict(corr_sel=stats(per[sc][p]["sel"]), corr_all=stats(per[sc][p]["all"]), days_both_sel=stats(both[sc][p]["sel"]), days_both_all=stats(both[sc][p]["all"])) for p in PAIRS} for sc in per}


def freq_prints(ctx, res: dict) -> dict:
    """Частота наборов правил R_s по сплитам и число «правила нет» (R_s, R^чёт, R^неч)."""
    out = {}
    for t in range(ctx.K):
        rr = res["t%d" % t]["rules"]
        cnt = collections.Counter(tuple(sorted(r[0], key=lambda ci: (int(T.COND_FEAT[ci]), ci))) for r in rr if r[0])
        out["t%d" % t] = dict(
            n_splits=len(rr), no_rule=sum(1 for r in rr if not r[0]), no_even=sum(1 for r in rr if not r[1]), no_odd=sum(1 for r in rr if not r[2]),
            n_distinct=len(cnt), top=[dict(rule=" ∧ ".join(rule_name(c) for c in k) or "(пусто)", n=v) for k, v in cnt.most_common(4)],
            feat_freq=dict(collections.Counter(T.PHI[int(T.COND_FEAT[c])] for r in rr for c in r[0]).most_common()))
    return out


def month_prints(ctx, res: dict) -> list:
    """Таблица по месяцам режима (пул, описательно): отобранные и все сделки W_s, Δ_A, M — среднее по сплитам."""
    types = list(range(ctx.K))
    same = OU_pooled(res, types)
    lo = T.REG[ctx.reg][0]
    months = collections.OrderedDict()
    for d in range(ctx.nd):
        dt = datetime.date(2026, 1, 1) + datetime.timedelta(days=lo + d)
        months.setdefault("%04d-%02d" % (dt.year, dt.month), []).append(d)
    rows = []
    for mo, days in months.items():
        ind = np.zeros((1, ctx.nd), np.float32)
        ind[0, days] = 1.0
        da, dd = SR.delta_rep(ind, same["A"], same["N"], same["Bn"], same["C"], same["E"])
        m = OU.m_rep(ind, same["A"], same["N"])
        with np.errstate(invalid="ignore", divide="ignore"):
            allm = np.nanmean(same["C"][:, days].sum(1) / np.where(same["E"][:, days].sum(1) > 0, same["E"][:, days].sum(1), np.nan))
        rows.append(dict(month=mo, days=len(days), n_sel=float(same["N"][:, days].sum(1).mean()), sum_sel=float(same["A"][:, days].sum(1).mean()), n_all=float(same["E"][:, days].sum(1).mean()),
                         sum_all=float(same["C"][:, days].sum(1).mean()), M=float(m[0]), m_all=float(allm), dA=float(da[0]), dD=float(dd[0])))
    return rows


def OU_pooled(res: dict, types: list) -> dict:
    return {k: sum(res["t%d" % t]["same_%s" % k] for t in types) for k in OU.KEYS}


def pbo_prints(res: dict, K: int) -> dict:
    """PBO с недопустимыми R_s как подгонкой (как в отчёте) и без них; доля недопустимых."""
    def one(types):
        rows = [(rk, adm) for t in types for (_s, rk, adm) in res["t%d" % t]["pbo"]]
        n = len(rows)
        nadm = sum(1 for _r, a in rows if a)
        bad_incl = sum(1 for r, a in rows if (not a) or r > 0.5)
        bad_excl = sum(1 for r, a in rows if a and r > 0.5)
        return dict(n=n, n_inadmissible=n - nadm, share_inadmissible=(n - nadm) / n if n else float("nan"), pbo_incl=bad_incl / n if n else float("nan"), pbo_excl=bad_excl / nadm if nadm else float("nan"))
    return dict(pool=one(list(range(K))), types={str(t): one([t]) for t in range(K)})


def balance_prints() -> dict:
    con = sqlite3.connect("file:%s?mode=ro" % T.DB, uri=True)
    out = {}
    for reg, typ, n, mx_a, mean_a, mx_k, mean_k in con.execute("SELECT regime, typ, count(*), max(bal_maxabs), avg(bal_maxabs), max(bal_ks), avg(bal_ks) FROM t735_split GROUP BY regime, typ"):
        out["%s/t%d" % (reg, typ)] = dict(n_splits=n, std_diff_max=mx_a, std_diff_mean=mean_a, ks_max=mx_k, ks_mean=mean_k)
    return out


def main():
    fz = json.load(open(os.path.join(T.ROOT, "freeze", "tk735-frozen.json"), encoding="utf-8"))
    g = json.load(open(os.path.join(RES, "g_prep.json"), encoding="utf-8"))
    oc = json.load(open(os.path.join(RES, "outcomes.json"), encoding="utf-8"))
    out = dict(balance=balance_prints(), settings={})
    for setting in ("L40", "LF"):
        dec = json.load(open(os.path.join(RES, "decide-%s.json" % setting), encoding="utf-8"))
        ex = {reg: oc[setting]["extras"][reg] for reg in ("FJ", "SEL")}
        st = dict(corr={}, freq={}, months={}, pbo={})
        for reg in ("FJ", "SEL"):
            ctx = OU.Ctx(reg, fz, g)
            res = np.load(os.path.join(RES, "search-%s-%s.npy" % (reg, setting)), allow_pickle=True)[0]
            st["corr"][reg] = corr_prints(ctx, res)
            st["freq"][reg] = freq_prints(ctx, res)
            st["months"][reg] = month_prints(ctx, res)
            st["pbo"][reg] = pbo_prints(res, ctx.K)
            print(setting, reg, "готово", flush=True)
        dec2 = json.loads(json.dumps(dec))
        for reg in ("FJ", "SEL"):
            dec2[reg]["pbo"]["pool"]["share"] = st["pbo"][reg]["pool"]["pbo_excl"]
            for t in range(dec2[reg]["K"]):
                dec2[reg]["pbo"]["types"][str(t)]["share"] = st["pbo"][reg]["types"][str(t)]["pbo_excl"]
        o1 = OU.outcomes(dec, ex, setting)
        o2 = OU.outcomes(dec2, ex, setting)
        st["outcome_incl"] = {k: v["outcome"] for k, v in o1.items()}
        st["outcome_excl"] = {k: v["outcome"] for k, v in o2.items()}
        out["settings"][setting] = st
    json.dump(out, open(os.path.join(RES, "prints.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=lambda o: bool(o) if isinstance(o, np.bool_) else float(o))
    for s in ("L40", "LF"):
        a, b = out["settings"][s]["outcome_incl"], out["settings"][s]["outcome_excl"]
        print(s, "исходы при PBO без недопустимых:", {k: (a[k], b[k]) for k in a if a[k] != b[k]} or "не меняются")
    print("prints записано")


if __name__ == "__main__":
    main()
