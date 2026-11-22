#!/usr/bin/env python3
"""П-42 §13.76 (TK-685): стадии half1 / half2 (признаки по AUC, шумы N1 / N2, семейный порог, дерево, CatBoost) строго по PS-970 + PS-971.
Половина 1 — поиск и обучение, половина 2 — читается один раз (замок `half2.lock` создаётся ПЕРВЫМ)."""
from __future__ import annotations

import glob
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk604_stats as st  # noqa: E402
import tk685_lib as lib  # noqa: E402
import tk685_run as R  # noqa: E402

N_NULL_TREES = 200
N_NULL_CB = 20
CB_PARAMS = dict(loss_function="Logloss", iterations=300, depth=4, learning_rate=0.05, random_seed=685, thread_count=2, verbose=0, allow_writing_files=False)


def contrast_labels(group, c):
    """C1: строки убыток / прибыль, метка 1 = убыток; C2: все строки, метка 1 = около нуля. Возвращает (маска строк, метки 0/1)."""
    g = np.asarray(group)
    if c == "C1":
        rows = g != lib.ZERO
        return rows, (g[rows] == lib.LOSS).astype(float)
    return np.ones(len(g), bool), (g == lib.ZERO).astype(float)


def testable(y):
    n1 = int(y.sum())
    return min(n1, len(y) - n1) >= R.MIN_GROUP


def set_frame(name):
    """Сделки набора, присоединённые к признакам (внутреннее соединение, порядок по времени входа), и список признаков матрицы; None — набор не прошёл G-join."""
    fp = R.p("feat", name + ".parquet")
    if not os.path.exists(fp):
        return None, None
    df = R.load_trades(name)
    feat = pd.read_parquet(fp)
    fc = [c for c in feat.columns if c not in ("row", "dup")]
    return df.merge(feat, on="row", how="inner"), fc


def nulls(Rk, V, y, rng):
    A1 = lib.null_auc(Rk, V, y, R.B, "N1", rng)
    A2 = lib.null_auc(Rk, V, y, R.B, "N2", rng)
    return A1, A2


# ───────────────────────────── половина 1 ─────────────────────────────

def stage_half1():
    outdir = R.p("h1")
    os.makedirs(os.path.join(outdir, "cb"), exist_ok=True)
    summary = {}
    for si, name in enumerate(R.SETS):
        m, fc = set_frame(name)
        if m is None:
            print("%s: G-join не пройден — признаки не считаются" % name)
            continue
        d = m[m["half"] == 1].reset_index(drop=True)
        X = d[fc].to_numpy(float)
        elig = lib.eligible_columns(X)
        names_e = [fc[j] for j in elig]
        Xe = X[:, elig]
        mean_h1 = np.nanmean(Xe, axis=0)
        info = {"set": name, "n_h1": len(d), "n_joined": len(m), "m_f": len(elig), "eligible": names_e, "contrasts": {}}
        print("%s: half 1 joined %d / %d, пригодных признаков m_f = %d" % (name, len(d), int((R.load_trades(name)["half"] == 1).sum()), len(elig)))
        for ci, c in enumerate(R.CONTRASTS):
            rows, y = contrast_labels(d["group"].to_numpy(), c)
            res = {"n": int(len(y)), "n1": int(y.sum()), "n0": int(len(y) - y.sum()), "testable": bool(testable(y))}
            if res["testable"]:
                Rk, V = lib.rank_matrix(Xe[rows])
                auc1 = lib.auc_from_ranks(y[None, :], Rk, V)[0]
                rng = np.random.default_rng([685, si, ci, 1])
                A1, A2 = nulls(Rk, V, y, rng)
                T1, T2 = lib.family_threshold(A1, R.ALPHA_SET), lib.family_threshold(A2, R.ALPHA_SET)
                T = max(T1, T2)
                dev = np.abs(auc1 - 0.5)
                cand = [j for j in range(len(names_e)) if dev[j] > T]
                uniq = ~d["dup"].to_numpy()[rows]
                if (y[uniq].sum() >= 1) and ((1 - y[uniq]).sum() >= 1):
                    Ru, Vu = lib.rank_matrix(Xe[rows][uniq])
                    auc_u = lib.auc_from_ranks(y[uniq][None, :], Ru, Vu)[0]
                else:
                    auc_u = np.full(len(names_e), np.nan)
                res.update(auc1=auc1, sigma_n1=np.nanstd(A1, axis=0), sigma_n2=np.nanstd(A2, axis=0), T_n1=T1, T_n2=T2, T=T, n_unique=int(uniq.sum()), auc1_unique=auc_u,
                           candidates=[{"name": names_e[j], "auc1": float(auc1[j]), "auc1_unique": float(auc_u[j]), "dev": float(dev[j])} for j in sorted(cand, key=lambda j: -dev[j])])
                print("  %s: n=%d (метка 1: %d) T_N1=%.4f T_N2=%.4f T=%.4f, кандидатов c=%d, max|AUC−0,5|=%.4f (%s)" % (
                    c, len(y), int(y.sum()), T1, T2, T, len(cand), float(np.nanmax(dev)), names_e[int(np.nanargmax(np.where(np.isnan(dev), -1, dev)))]))
                for cd in res["candidates"][:12]:
                    print("     %-14s AUC1=%.3f (без дублей %.3f)" % (cd["name"], cd["auc1"], cd["auc1_unique"]))
            else:
                print("  %s: не тестируется (группа < %d в половине 1: метка 1 = %d из %d)" % (c, R.MIN_GROUP, int(y.sum()), len(y)))
            info["contrasts"][c] = res
        # дерево и CatBoost (C1, половина 1)
        rows, y = contrast_labels(d["group"].to_numpy(), "C1")
        if testable(y):
            Xf = np.where(np.isnan(Xe), mean_h1[None, :], Xe)
            Xtr = Xf[rows]
            min_leaf = max(20, int(math.ceil(0.03 * len(y))))
            nodes = lib.cart_fit(Xtr, y, max_depth=3, min_leaf=min_leaf)
            leaf = lib.cart_leaf(nodes, Xtr)
            rng = np.random.default_rng([685, si, 9])
            null_nodes = [lib.cart_fit(Xtr, y[rng.permutation(len(y))], max_depth=3, min_leaf=min_leaf) for _ in range(N_NULL_TREES)]
            info["tree"] = {"min_leaf": min_leaf, "mean_h1": mean_h1, "nodes": nodes, "null_nodes": null_nodes, "depth": lib.cart_depth(nodes),
                            "auc1": lib.auc_scores(lib.cart_score(nodes, Xtr), y), "rules": tree_rules(nodes, names_e),
                            "leaves_h1": {int(k): {"n": int((leaf == k).sum()), "loss_share": float(y[leaf == k].mean())} for k in np.unique(leaf)}}
            print("  дерево (C1, min_leaf=%d, глубина %d, AUC1 обучения %.3f):" % (min_leaf, info["tree"]["depth"], info["tree"]["auc1"]))
            for line in info["tree"]["rules"]:
                print("     " + line)
            info["catboost"] = fit_catboost(Xe[rows], y, names_e, name, outdir)
        else:
            print("  дерево / CatBoost: C1 не тестируется в половине 1")
        R.jdump(info, os.path.join(outdir, name + ".json"))
        summary[name] = {"m_f": len(elig), "candidates": {c: len(info["contrasts"][c].get("candidates", [])) for c in R.CONTRASTS}}
    files = sorted(glob.glob(os.path.join(outdir, "*.json")) + glob.glob(os.path.join(outdir, "cb", "*.cbm")))
    with open(os.path.join(outdir, "DONE"), "w", encoding="utf-8") as f:
        for fn in files:
            f.write("%s  %s\n" % (R.sha(fn), os.path.relpath(fn, outdir)))
    print("half1 готово: %d файлов, sha256 в h1/DONE" % len(files))


def tree_rules(nodes, names, k=0, depth=0):
    nd = nodes[k]
    if nd["leaf"]:
        return ["  " * depth + "лист %d: n=%d, доля убытков %.3f" % (k, nd["n"], nd["p"])]
    f, thr = names[nd["feat"]], nd["thr"]
    return (["  " * depth + "%s ≤ %.6g:" % (f, thr)] + tree_rules(nodes, names, nd["l"], depth + 1)
            + ["  " * depth + "%s > %.6g:" % (f, thr)] + tree_rules(nodes, names, nd["r"], depth + 1))


def fit_catboost(Xc, y, names, name, outdir):
    from catboost import CatBoostClassifier, Pool
    mdl = CatBoostClassifier(**CB_PARAMS)
    mdl.fit(Xc, y)
    mdl.save_model(os.path.join(outdir, "cb", name + ".cbm"))
    shap = mdl.get_feature_importance(Pool(Xc), type="ShapValues")[:, :-1]
    imp = np.abs(shap).mean(0)
    rng = np.random.default_rng([685, 77])
    for k in range(N_NULL_CB):
        yk = y[rng.permutation(len(y))]
        mk = CatBoostClassifier(**dict(CB_PARAMS, random_seed=685 + 1 + k))
        mk.fit(Xc, yk)
        mk.save_model(os.path.join(outdir, "cb", "%s-null%02d.cbm" % (name, k)))
    order = np.argsort(-imp)
    print("  CatBoost (справочно): топ-5 mean|SHAP| половины 1: %s" % ", ".join("%s %.3f" % (names[j], imp[j]) for j in order[:5]))
    return {"shap_mean_abs_h1": imp, "top_h1": [names[j] for j in order[:20]]}


# ───────────────────────────── половина 2 (один раз) ─────────────────────────────

def stage_half2():
    lock = R.p("half2.lock")
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)       # замок ПЕРВЫМ: повторный запуск отказывает
    os.write(fd, b"half2 read once\n")
    os.close(fd)
    done = R.p("h1", "DONE")
    assert os.path.exists(done), "h1/DONE нет"
    for line in open(done, encoding="utf-8"):
        h, rel = line.split(None, 1)
        assert R.sha(os.path.join(R.p("h1"), rel.strip())) == h, ("sha256 половины 1 изменился", rel)
    outdir = R.p("h2")
    os.makedirs(outdir, exist_ok=True)
    import json
    for si, name in enumerate(R.SETS):
        m, fc = set_frame(name)
        if m is None:
            continue
        info = json.load(open(R.p("h1", name + ".json"), encoding="utf-8"))
        names_e = info["eligible"]
        d = m[m["half"] == 2].reset_index(drop=True)
        Xe = d[names_e].to_numpy(float)
        out = {"set": name, "n_h2": len(d), "contrasts": {}}
        mon = st.MONTH_OF_DAY[d["day_in"].to_numpy()]
        for ci, c in enumerate(R.CONTRASTS):
            h1 = info["contrasts"][c]
            rows, y = contrast_labels(d["group"].to_numpy(), c)
            res = {"n": int(len(y)), "n1": int(y.sum()), "n0": int(len(y) - y.sum()), "testable": bool(testable(y)), "c": len(h1.get("candidates", []))}
            if h1["testable"] and res["testable"]:
                Rk, V = lib.rank_matrix(Xe[rows])
                auc2 = lib.auc_from_ranks(y[None, :], Rk, V)[0]
                rng = np.random.default_rng([685, si, ci, 2])
                A1, A2 = nulls(Rk, V, y, rng)
                sig2 = np.maximum(np.nanstd(A1, axis=0), np.nanstd(A2, axis=0))
                auc1 = np.array(h1["auc1"], float)
                cands = [cd["name"] for cd in h1["candidates"]]
                conf = []
                if cands:
                    z = lib.norm_ppf(1.0 - R.ALPHA_SET / (2 * len(cands)))
                    res["z"] = z
                    for nm in cands:
                        j = names_e.index(nm)
                        ok = (np.sign(auc2[j] - 0.5) == np.sign(auc1[j] - 0.5)) and abs(auc2[j] - 0.5) > z * sig2[j]
                        rec = {"name": nm, "auc1": float(auc1[j]), "auc2": float(auc2[j]), "sigma2": float(sig2[j]), "z_sigma2": float(z * sig2[j]), "confirmed": bool(ok)}
                        if ok:
                            rec.update(robust(Xe[rows][:, j], y, d.loc[rows, "sym"].to_numpy(), d.loc[rows, "day_in"].to_numpy(), mon[rows], auc1[j]))
                        conf.append(rec)
                uniq = ~d["dup"].to_numpy()[rows]
                if (y[uniq].sum() >= 1) and ((1 - y[uniq]).sum() >= 1):
                    Ru, Vu = lib.rank_matrix(Xe[rows][uniq])
                    auc_u = lib.auc_from_ranks(y[uniq][None, :], Ru, Vu)[0]
                else:
                    auc_u = np.full(len(names_e), np.nan)
                res.update(auc2=auc2, sigma2=sig2, candidates=conf, auc2_unique=auc_u, n_confirmed=int(sum(r["confirmed"] for r in conf)))
                print("%s %s: половина 2 n=%d (метка 1: %d); кандидатов %d, подтверждено %d" % (name, c, len(y), int(y.sum()), len(cands), res["n_confirmed"]))
                for r in conf:
                    print("     %-14s AUC1=%.3f AUC2=%.3f порог z·σ2=%.4f -> %s" % (r["name"], r["auc1"], r["auc2"], r["z_sigma2"], "ПОДТВЕРЖДЁН" if r["confirmed"] else "не подтвердился"))
            else:
                print("%s %s: не тестируется (половина 1: %s; половина 2: метка 1 = %d из %d)" % (name, c, h1["testable"], int(y.sum()), len(y)))
            out["contrasts"][c] = res
        if "tree" in info:
            out["tree"], out["catboost"] = h2_models(info, d, Xe, names_e, name)
        R.jdump(out, os.path.join(outdir, name + ".json"))
    files = sorted(glob.glob(os.path.join(outdir, "*.json")))
    with open(os.path.join(outdir, "DONE"), "w", encoding="utf-8") as f:
        for fn in files:
            f.write("%s  %s\n" % (R.sha(fn), os.path.relpath(fn, outdir)))
    print("half2 готово: %d файлов" % len(files))


def robust(x, y, sym, day, month, auc1_j):
    """Подтверждённый признак: AUC по месяцам (описание); знак AUC2 без самой частой монеты и без трёх суток с наибольшим числом сделок."""
    def auc_of(mask):
        v = ~np.isnan(x[mask])
        yy = y[mask][v]
        if yy.sum() < 1 or (1 - yy).sum() < 1:
            return float("nan")
        return float(lib.auc_all(x[mask][v][:, None], yy)[0])
    s = np.sign(auc1_j - 0.5)
    top_sym = pd.Series(sym).value_counts().index[0]
    top_days = pd.Series(day).value_counts().index[:3]
    a_sym = auc_of(sym != top_sym)
    a_day = auc_of(~np.isin(day, top_days))
    months = {st.MONTH_NAMES[k]: auc_of(month == k) for k in np.unique(month)}
    return {"auc2_months": months, "top_sym": str(top_sym), "auc2_no_top_sym": a_sym, "sign_ok_no_top_sym": bool(np.sign(a_sym - 0.5) == s),
            "top_days": [int(x_) for x_ in top_days], "auc2_no_top_days": a_day, "sign_ok_no_top_days": bool(np.sign(a_day - 0.5) == s)}


def h2_models(info, d, Xe, names_e, name):
    from catboost import CatBoostClassifier, Pool
    rows, y = contrast_labels(d["group"].to_numpy(), "C1")
    t = info["tree"]
    if not testable(y):
        return {"testable": False, "n": int(len(y)), "n1": int(y.sum())}, {"testable": False}
    mean = np.array(t["mean_h1"], float)
    Xf = np.where(np.isnan(Xe), mean[None, :], Xe)[rows]
    nodes = t["nodes"]
    auc2 = lib.auc_scores(lib.cart_score(nodes, Xf), y)
    nulls_auc = np.array([lib.auc_scores(lib.cart_score(nn, Xf), y) for nn in t["null_nodes"]])
    sig = float(np.std(nulls_auc))
    z = lib.norm_ppf(1.0 - R.ALPHA_SET)
    leaf = lib.cart_leaf(nodes, Xf)
    leaves = {}
    for k_, v in t["leaves_h1"].items():
        s = leaf == int(k_)
        leaves[k_] = {"n_h1": v["n"], "loss_share_h1": v["loss_share"], "n_h2": int(s.sum()), "loss_share_h2": float(y[s].mean()) if s.any() else float("nan")}
    tree = {"testable": True, "auc2": auc2, "sigma_null": sig, "z": z, "threshold": 0.5 + z * sig, "confirmed": bool(auc2 > 0.5 + z * sig), "leaves": leaves, "rules": t["rules"],
            "null_auc2_mean": float(nulls_auc.mean())}
    print("%s дерево: AUC2=%.3f, порог 0,5 + z·σ_null = %.3f (σ_null=%.4f) -> %s" % (name, auc2, tree["threshold"], sig, "ПОДТВЕРЖДЕНО" if tree["confirmed"] else "не подтвердилось"))
    mdl = CatBoostClassifier()
    mdl.load_model(R.p("h1", "cb", name + ".cbm"))
    Xc = Xe[rows]
    pr = mdl.predict_proba(Xc)[:, 1]
    a2 = lib.auc_scores(pr, y)
    na = []
    for k in range(N_NULL_CB):
        mk = CatBoostClassifier()
        mk.load_model(R.p("h1", "cb", "%s-null%02d.cbm" % (name, k)))
        na.append(lib.auc_scores(mk.predict_proba(Xc)[:, 1], y))
    shap = mdl.get_feature_importance(Pool(Xc), type="ShapValues")[:, :-1]
    imp2 = np.abs(shap).mean(0)
    imp1 = np.array(info["catboost"]["shap_mean_abs_h1"], float)
    top1, top2 = set(np.argsort(-imp1)[:20]), set(np.argsort(-imp2)[:20])
    rk1, rk2 = pd.Series(imp1).rank().to_numpy(), pd.Series(imp2).rank().to_numpy()
    cb = {"testable": True, "auc2": a2, "null_auc2": na, "sigma_null": float(np.std(na)), "null_mean": float(np.mean(na)), "shap_mean_abs_h2": imp2,
          "top20_overlap": len(top1 & top2), "rank_corr": float(np.corrcoef(rk1, rk2)[0, 1]), "top_h2": [names_e[j] for j in np.argsort(-imp2)[:20]]}
    print("%s CatBoost (справочно): AUC2=%.3f, нули %.3f ± %.3f; перекрытие топ-20 SHAP %d, ранговая корреляция %.2f" % (name, a2, cb["null_mean"], cb["sigma_null"], cb["top20_overlap"], cb["rank_corr"]))
    return tree, cb
