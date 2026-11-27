"""TK-727, шаг 3, стадия 2: HAR, масштаб, QLIKE, суточные разности, бутстреп, Холм, правило решения (§13.78.3, §13.78.7 п. 8; правила краёв — `EDGE_RULES` в `tk727_s3.py`)."""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import time

import numpy as np

import tk727_lib as L

CODES = ["natr5", "natr15", "natr30", "natr60", "rv5", "rv15", "rv30", "rv60", "ewtr", "gk60", "bv60", "har"]
LABELS = {1: "NATR5", 2: "NATR15", 3: "NATR30", 4: "NATR60", 5: "RV5", 6: "RV15", 7: "RV30", 8: "RV60", 9: "EWTR", 10: "GK60", 11: "BV60", 12: "HAR"}
BASE = "natr30"
RAW11 = CODES[:11]
HAR_IN = ["rvm60", "rvm1440", "rvm10080"]
MONTH_STARTS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304]
BLOCKS = {"b": None, "b21": 21}  # None → ⌈n^{1/3}⌉


def load_all(out):
    files = sorted(glob.glob(os.path.join(out, "s3", "coin", "*.npz")))
    cols = None
    for f in files:
        z = np.load(f)
        if cols is None:
            cols = {k: [] for k in z.files}
        for k in z.files:
            cols[k].append(z[k])
    return {k: np.concatenate(v) for k, v in cols.items()}, len(files)


def har_design(D):
    fl = D["tickf"]
    X = [np.ones(len(fl))]
    for k in HAR_IN:
        X.append(np.log(np.maximum(D[k], fl)))
    hour = D["hour"].astype(np.int64)
    for hh in range(1, 24):
        X.append((hour == hh).astype(np.float64))
    return np.column_stack(X)


def month_of_day(day):
    """Начало месяца (сутки) для каждых суток; ноябрь (304+) — один пересчёт, 14–24.11 внутри него."""
    ms = np.array(MONTH_STARTS)
    return ms[np.searchsorted(ms, day, side="right") - 1]


def har_forecast(D, RR, ok_base):
    """HAR (№ 12) для всех моментов: коэффициенты — OLS по моментам (б) суток ≤ max(29, начало месяца − 2), пересчёт на начале месяца; возвращает F12 (NaN, если нет входов или фита)."""
    X = har_design(D)
    y = np.log(RR)
    inp = np.isfinite(X).all(axis=1) & ok_base
    mon = month_of_day(D["day"].astype(np.int64))
    F = np.full(len(y), np.nan)
    betas = {}
    for s in MONTH_STARTS:
        last = max(L.BURN - 1, s - 2)
        tr = inp & (D["set"] == 0) & (D["day"] <= last) & np.isfinite(y)
        if tr.sum() < 1000:
            continue
        beta, *_ = np.linalg.lstsq(X[tr], y[tr], rcond=None)
        betas[s] = dict(n=int(tr.sum()), last_day=int(last), beta=[float(b) for b in beta[:4]])
        te = inp & (mon == s)
        F[te] = np.exp(X[te] @ beta)
    return F, betas


def scale_factors(day, q, base_set):
    """k(d) = среднее q по моментам (б) суток ≤ d − 2 (растущее окно); NaN, пока нет."""
    nd = L.NDAYS - L.DAY0
    sel = base_set & np.isfinite(q)
    s = np.bincount(day[sel], weights=q[sel], minlength=nd)[:nd]
    n = np.bincount(day[sel], minlength=nd)[:nd]
    cs, cn = np.cumsum(s), np.cumsum(n)
    k = np.full(nd, np.nan)
    for d in range(2, nd):
        if cn[d - 2] > 0:
            k[d] = cs[d - 2] / cn[d - 2]
    return k


def decide(res, codes_new=(9, 10, 11, 12)):
    """Правило решения шага 3 по таблице разностей (H = 60, QLIKE, набор (б)).

    res[half][code] = dict(rel=относительное снижение к NATR30, pass_b=(среднее>0 и p_Холм<0,05 при блоке ⌈n^{1/3}⌉), pass_b21=то же при блоке 21, L=средняя потеря)
    res[half]['best18'] = (код, потеря) — лучший из кодов 1–8 в половине; res[half]['rel5'][код] = dict(rel, pass_b, pass_b21) — новый против лучшего из 1–8 (Холм по 4).
    Возвращает dict исхода.
    """
    passers = []
    unclear = []
    for c in range(1, 13):
        if c == 3:
            continue
        ok = all(res[h][c]["pass_b"] for h in ("FJ", "SEL"))
        ok21 = all(res[h][c]["pass_b21"] for h in ("FJ", "SEL"))
        if ok and not ok21:
            unclear.append(c)
        elif ok:
            passers.append(c)
    if not passers and not unclear:
        return dict(outcome="NATR30 остаётся, Г4 опровергнута", winner=None, passers=[], unclear=[])
    gain = {c: np.mean([res[h][c]["rel"] for h in ("FJ", "SEL")]) for c in passers}
    old = [c for c in passers if c <= 8]
    new = [c for c in passers if c >= 9]
    # новый берётся только по правилу 5 %
    new_ok = []
    for c in new:
        r5 = [res[h]["rel5"][c] for h in ("FJ", "SEL")]
        if all(x["rel"] >= 0.05 and x["pass_b"] for x in r5):
            new_ok.append(c)
    new_unclear5 = [c for c in new if c not in new_ok and all(res[h]["rel5"][c]["rel"] >= 0.05 and res[h]["rel5"][c]["pass_b"] for h in ("FJ", "SEL"))]
    if new_ok:
        w = max(new_ok, key=lambda c: gain[c])
        return dict(outcome="новый оценщик № %d берётся: задача на код источника с гейтом" % w, winner=w, passers=passers, unclear=unclear)
    if old:
        w = max(old, key=lambda c: gain[c])
        return dict(outcome="победитель — код № %d (есть в движке), нового кода нет" % w, winner=w, passers=passers, unclear=unclear)
    if unclear:
        return dict(outcome="не ясно: знак границы при блоках ⌈n^{1/3}⌉ и 21 разный (коды %s)" % unclear, winner=None, passers=passers, unclear=unclear)
    return dict(outcome="NATR30 остаётся: новые оценщики (№ %s) лучше NATR30, но не лучше лучшего из кодов 1–8 на ≥ 5 %% в обеих половинах" % new, winner=None, passers=passers, unclear=unclear)


def analyze(out, edge_rules=""):
    t0 = time.time()
    D, ncoins = load_all(out)
    n = len(D["m"])
    day = D["day"].astype(np.int64)
    base_set = D["set"] == 0
    print(f"загружено: монет {ncoins}, моментов {n} ((б) {int(base_set.sum())}, (а) {int((~base_set).sum())})", flush=True)
    fl = D["tickf"]
    base_ok = np.isfinite(fl)
    for k in RAW11 + HAR_IN:
        base_ok &= np.isfinite(D[k]) & (D[k] >= 0)
    db = sqlite3.connect(os.path.join(out, "tk727.sqlite"))
    for t in ("s3_loss", "s3_diff", "s3_desc", "s3_decision", "s3_counts", "s3_har"):
        db.execute(f"DROP TABLE IF EXISTS {t}")
    db.execute("CREATE TABLE s3_loss(h INT, loss TEXT, per TEXT, set_ TEXT, est TEXT, mean REAL, days INT, moments INT)")
    db.execute("CREATE TABLE s3_diff(h INT, loss TEXT, per TEXT, set_ TEXT, cand TEXT, block INT, mean REAL, se REAL, lo REAL, hi REAL, p REAL, p_holm REAL, days INT, rel REAL)")
    db.execute("CREATE TABLE s3_desc(item TEXT, per TEXT, key TEXT, val REAL, lo REAL, hi REAL, n INT, note TEXT)")
    db.execute("CREATE TABLE s3_decision(h INT, outcome TEXT, winner INT, passers TEXT, unclear TEXT, detail TEXT)")
    db.execute("CREATE TABLE s3_counts(h INT, per TEXT, set_ TEXT, total INT, valid INT)")
    db.execute("CREATE TABLE s3_har(h INT, month_start INT, n INT, last_day INT, beta TEXT)")
    result = dict(edge_rules=edge_rules)
    pers = {"FJ": L.HALVES["FJ"], "SEL": L.HALVES["SEL"], "jan": (0, 31), "n1": (317, 328)}
    for H, rrk in ((60, "rr60"), (20, "rr20")):
        RR = np.maximum(D[rrk], fl)
        ok = base_ok & np.isfinite(RR)
        F12, betas = har_forecast(D, RR, ok)
        for s, b in betas.items():
            db.execute("INSERT INTO s3_har VALUES(?,?,?,?,?)", (H, s, b["n"], b["last_day"], json.dumps(b["beta"])))
        ok = ok & np.isfinite(F12)
        # счётчики выбывших
        for per, (a, b) in pers.items():
            for tag, ms in (("b", base_set), ("a", ~base_set)):
                inr = ms & (day >= a) & (day < b)
                db.execute("INSERT INTO s3_counts VALUES(?,?,?,?,?)", (H, per, tag, int(inr.sum()), int((inr & ok).sum())))
        # масштаб и потери
        Fraw = {c: (F12 if c == "har" else D[c]) for c in CODES}
        nd = L.NDAYS - L.DAY0
        loss = {"qlike": {}, "sqlog": {}}
        kfac = {}
        for c in CODES:
            F = Fraw[c]
            q = np.where(ok, RR / F, np.nan)
            k = scale_factors(day, np.where(base_set, q, np.nan), base_set & ok)
            kfac[c] = k
            kd = k[np.minimum(day, nd - 1)]
            Fs = np.maximum(kd * F, fl)
            with np.errstate(all="ignore"):
                ql = L.qlike(RR, Fs)
                sl = np.log(RR / Fs) ** 2
            good = ok & np.isfinite(ql)
            loss["qlike"][c] = np.where(good, ql, np.nan)
            loss["sqlog"][c] = np.where(good, sl, np.nan)
        # общая выборка: момент входит, если у всех оценщиков потеря определена (масштаб есть)
        common = ok.copy()
        for c in CODES:
            common &= np.isfinite(loss["qlike"][c])
        for ln in loss:
            for c in CODES:
                loss[ln][c] = np.where(common, loss[ln][c], np.nan)
        result[f"H{H}"] = dict(common_b=int((common & base_set).sum()), common_a=int((common & ~base_set).sum()))
        # суточные ряды и таблицы потерь
        dm = {ln: {tag: {c: L.day_means(day, np.where(ms, loss[ln][c], np.nan), 0, nd) for c in CODES} for tag, ms in (("b", base_set), ("a", ~base_set))} for ln in loss}
        for ln in loss:
            for tag in ("b", "a"):
                for per, (a, b) in pers.items():
                    for c in CODES:
                        x = dm[ln][tag][c][a:b]
                        if np.isfinite(x).sum() == 0:
                            continue
                        mom = int((np.isfinite(loss[ln][c]) & (base_set if tag == "b" else ~base_set) & (day >= a) & (day < b)).sum())
                        db.execute("INSERT INTO s3_loss VALUES(?,?,?,?,?,?,?,?)", (H, ln, per, tag, c, float(np.nanmean(x)), int(np.isfinite(x).sum()), mom))
        # разности «NATR30 − кандидат» и Холм по 11
        resdec = {}
        for per in ("FJ", "SEL", "jan", "n1"):
            a, b = pers[per]
            for ln in ("qlike", "sqlog"):
                tag = "b"
                blk = {}
                for bname, bl in BLOCKS.items():
                    cands = [c for c in CODES if c != BASE]
                    st = {}
                    for c in cands:
                        d = dm[ln][tag][BASE][a:b] - dm[ln][tag][c][a:b]
                        d = d[np.isfinite(d)]
                        if len(d) < 5:
                            continue
                        st[c] = L.stat_block(d, bl or L.block_len(len(d)))
                    ph = L.holm([st[c]["p"] for c in st]) if st else []
                    for (c, s_), p_h in zip(st.items(), ph):
                        base_mean = float(np.nanmean(dm[ln][tag][BASE][a:b]))
                        rel = s_["mean"] / base_mean if base_mean else float("nan")
                        db.execute("INSERT INTO s3_diff VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (H, ln, per, "b", c, s_["block"], s_["mean"], s_["se"], s_["lo"], s_["hi"], s_["p"], p_h, s_["n"], rel))
                        blk.setdefault(bname, {})[c] = dict(mean=s_["mean"], p=s_["p"], p_holm=p_h, rel=rel)
                if ln == "qlike" and per in ("FJ", "SEL"):
                    resdec[per] = blk
        # набор (а) на SEL — описательно
        a, b = pers["SEL"]
        for ln in ("qlike", "sqlog"):
            cands = [c for c in CODES if c != BASE]
            st = {}
            for c in cands:
                d = dm[ln]["a"][BASE][a:b] - dm[ln]["a"][c][a:b]
                d = d[np.isfinite(d)]
                if len(d) >= 5:
                    st[c] = L.stat_block(d, L.block_len(len(d)))
            ph = L.holm([st[c]["p"] for c in st]) if st else []
            for (c, s_), p_h in zip(st.items(), ph):
                base_mean = float(np.nanmean(dm[ln]["a"][BASE][a:b]))
                db.execute("INSERT INTO s3_diff VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (H, ln, "SEL", "a", c, s_["block"], s_["mean"], s_["se"], s_["lo"], s_["hi"], s_["p"], p_h, s_["n"], s_["mean"] / base_mean))
        # правило решения (набор б, QLIKE)
        res = {}
        for per in ("FJ", "SEL"):
            a, b = pers[per]
            res[per] = {}
            meanL = {c: float(np.nanmean(dm["qlike"]["b"][c][a:b])) for c in CODES}
            best = min((c for c in CODES[:8]), key=lambda c: meanL[c])
            res[per]["best18"] = (best, meanL[best])
            for i, c in enumerate(CODES, start=1):
                if c == BASE:
                    continue
                bb = resdec[per]["b"][c]
                b21 = resdec[per]["b21"][c]
                res[per][i] = dict(rel=bb["rel"], pass_b=bool(bb["mean"] > 0 and bb["p_holm"] < 0.05), pass_b21=bool(b21["mean"] > 0 and b21["p_holm"] < 0.05), L=meanL[c])
            # новый против лучшего из 1–8: Холм по 4
            res[per]["rel5"] = {}
            blocks5 = {}
            for bname, bl in BLOCKS.items():
                st = {}
                for i in (9, 10, 11, 12):
                    c = CODES[i - 1]
                    d = dm["qlike"]["b"][best][a:b] - dm["qlike"]["b"][c][a:b]
                    d = d[np.isfinite(d)]
                    st[i] = L.stat_block(d, bl or L.block_len(len(d)))
                ph = L.holm([st[i]["p"] for i in st])
                blocks5[bname] = {i: dict(mean=st[i]["mean"], p=st[i]["p"], p_holm=p_h, lo=st[i]["lo"], hi=st[i]["hi"]) for i, p_h in zip(st, ph)}
            for i in (9, 10, 11, 12):
                bb, b21 = blocks5["b"][i], blocks5["b21"][i]
                rel = bb["mean"] / meanL[best] if meanL[best] else float("nan")
                res[per]["rel5"][i] = dict(rel=rel, pass_b=bool(bb["mean"] > 0 and bb["p_holm"] < 0.05), pass_b21=bool(b21["mean"] > 0 and b21["p_holm"] < 0.05))
                db.execute("INSERT INTO s3_diff VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (H, "qlike_vs_best18", per, "b", i, L.block_len(int(np.isfinite(dm['qlike']['b'][best][a:b]).sum())), bb["mean"], 0.0, bb["lo"], bb["hi"], bb["p"], bb["p_holm"], b - a, rel))
        dec = decide(res)
        dec["best18"] = {per: res[per]["best18"] for per in res}
        db.execute("INSERT INTO s3_decision VALUES(?,?,?,?,?,?)", (H, dec["outcome"], dec["winner"] or 0, json.dumps(dec["passers"]), json.dumps(dec["unclear"]), json.dumps({k: v for k, v in dec.items() if k not in ("passers", "unclear")}, ensure_ascii=False, default=str)))
        result[f"H{H}"]["decision"] = dec
        result[f"H{H}"]["res"] = {per: {str(k): v for k, v in res[per].items()} for per in res}
        # описательно: по часу суток и третям оборота (набор б, FJ+SEL, QLIKE)
        a, b = L.HALVES["FJ"][0], L.HALVES["SEL"][1]
        inw = base_set & (day >= a) & (day < b) & common
        win_c = dec["winner"] if dec["winner"] else 12
        cmp_codes = [BASE, CODES[win_c - 1] if win_c else "har", "har"]
        for c in dict.fromkeys(cmp_codes):
            v = loss["qlike"][c]
            for hb in range(6):
                sel = inw & (D["hour"] // 4 == hb)
                if sel.any():
                    db.execute("INSERT INTO s3_desc VALUES(?,?,?,?,?,?,?,?)", (f"hour_bin_H{H}", "FJ+SEL", f"{c}|{hb}", float(np.nanmean(v[sel])), None, None, int(sel.sum()), "QLIKE по четырёхчасовым корзинам UTC"))
            # трети оборота внутри суток
            rank = np.full(n, np.nan)
            idxs = np.where(inw & np.isfinite(D["turn24"]))[0]
            if len(idxs):
                od = np.lexsort((D["turn24"][idxs], day[idxs]))
                ii = idxs[od]
                dd = day[ii]
                starts = np.r_[0, np.where(np.diff(dd) != 0)[0] + 1]
                ends = np.r_[starts[1:], len(ii)]
                for s0, e0 in zip(starts, ends):
                    rank[ii[s0:e0]] = np.arange(e0 - s0) / max(e0 - s0, 1)
                for tb in range(3):
                    sel = inw & (rank >= tb / 3) & (rank < (tb + 1) / 3)
                    if sel.any():
                        db.execute("INSERT INTO s3_desc VALUES(?,?,?,?,?,?,?,?)", (f"turn_third_H{H}", "FJ+SEL", f"{c}|{tb}", float(np.nanmean(v[sel])), None, None, int(sel.sum()), "QLIKE по третям оборота за 24 ч внутри суток (0 — нижняя)"))
        # (1) раздувание NATR30 подходом: ln(natr30 / natr30 за 30 минут до) для сигналов против случайных минут, SEL (H-независимо: один раз)
        if H == 60:
            a, b = L.HALVES["SEL"]
            with np.errstate(all="ignore"):
                lr = np.log(D["natr30"] / D["natr30_prev"])
            lr = np.where(np.isfinite(lr), lr, np.nan)
            da = L.day_means(day, np.where(~base_set, lr, np.nan), a, b)
            dbm = L.day_means(day, np.where(base_set, lr, np.nan), a, b)
            diff = da - dbm
            ok_d = np.isfinite(diff)
            for key, x in (("signals", da), ("random", dbm), ("signals_minus_random", diff)):
                x = x[np.isfinite(x)]
                if len(x) >= 5:
                    s_ = L.stat_block(x, L.block_len(len(x)))
                    db.execute("INSERT INTO s3_desc VALUES(?,?,?,?,?,?,?,?)", ("natr_inflation", "SEL", key, s_["mean"], s_["lo"], s_["hi"], s_["n"], "среднее ln(NATR30[t−1]/NATR30[t−31]) по суткам; блок ⌈n^{1/3}⌉"))
        db.commit()
        print(f"H={H}: {dec['outcome']}  ({time.time() - t0:.0f} с)", flush=True)
    json.dump(result, open(os.path.join(out, "s3_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    db.commit()
    db.close()
    return result
