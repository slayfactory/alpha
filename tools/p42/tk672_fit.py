#!/usr/bin/env python3
"""П-42 §13.72 (TK-672), шаг fit: скользящее обучение классификатора «сделка в плюс» (`y_pos = [y_net > 0]`) на ВСЕХ обучающих строках стороны, CatBoost или LightGBM (prereg-13.72.md §4, §6).

  python3 tk672_fit.py --lib cb|lgb --side long|short --data <side>.parquet --out <dir> [--threads 4] [--step train|score]

Загрузка, константы, G-leak, очистка 24 ч, плацебо-перестановка — из `tk671_fit` (импорт, не копия). Подвыборки обучающих строк нет.
  train  N* (одна подгонка на 200 деревьях по июлю, метки читаются фильтром parquet только до 01.08, критерий — AUC проверки 22–30.07 на 25/50/100/200 деревьях; ничья — меньшее N) → `nstar.json`;
         модели месяцев (авг, сен, окт, 01–13.11) и — для CatBoost — плацебо (окт, 01–13.11); G-leak перед каждой подгонкой; затем `models.sha256`. Оценок НЕ пишет (заморозка до GO критика).
  score  проверяет sha256 моделей и пишет `pred.parquet` (win,row_id,month,warm,score,score_plc): оценки на ВСЕХ строках окна `[начало m − 24 ч, конец m)`, `warm` — сутки разогрева.
Выход: `nstar.json`, `features.json`, `models/`, `models.sha256`, `models-info.json`, `gleak.json`, `importance-<месяц>.json`, `pred.parquet`, `fit.log`."""
from __future__ import annotations

import argparse
import gc
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk671_fit as f1  # noqa: E402

SEED = 672
GRID = (25, 50, 100, 200)
CB_PARAMS = dict(loss_function="Logloss", auto_class_weights="Balanced", depth=6, learning_rate=0.05, l2_leaf_reg=3, border_count=128, random_seed=SEED, allow_writing_files=False, verbose=0)
LGB_PARAMS = dict(objective="binary", is_unbalance=True, num_leaves=64, learning_rate=0.05, min_child_samples=100, lambda_l2=3.0, max_bin=128, seed=SEED, verbosity=-1, deterministic=True, force_row_wise=True)
MONTHS = f1.MONTHS
PLC_MONTHS = f1.PLC_MONTHS
MS_DAY = f1.MS_DAY
END_MS = f1.utc_ms(2026, 11, 14)                      # первая миллисекунда 14.11 — вне периода
PRED_CHUNK = 100_000
EXT = {"cb": ".cbm", "lgb": ".txt"}
Refuse, LeakAbort = f1.Refuse, f1.LeakAbort


def month_end_ms(label):
    y, m = (int(x) for x in label.split("-"))
    return f1.utc_ms(y + (m == 12), 1 if m == 12 else m + 1)


def month_end_ms_capped(label):
    return min(month_end_ms(label), END_MS)


def auc(y, s):
    """AUC по рангам (средние ранги при ничьих): (Σранги положительных − n₁(n₁+1)/2) / (n₁·n₀)."""
    y = np.asarray(y) > 0.5
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = pd.Series(np.asarray(s, dtype=float)).rank().to_numpy()
    return float((r[y].sum() - n1 * (n1 + 1) / 2.0) / (float(n1) * n0))


def pick_nstar(aucs):
    """Максимум AUC на сетке; ничья — меньшее N."""
    return min(GRID, key=lambda k: (-aucs[k], k))


def y_pos_of(fr):
    return np.where(fr.y_has, (fr.y_net > 0).astype(np.float64), np.nan)


# ------------------------------------------------------------------------------------------------------ модели

def fit_model(lib, fr, sel, y, iters, threads):
    df = fr.make_df(sel)
    if lib == "cb":
        import catboost as cb
        pool = cb.Pool(df, label=y[sel], cat_features=fr.cat_features)
        del df
        model = cb.CatBoostClassifier(iterations=int(iters), thread_count=int(threads), **CB_PARAMS)
        model.fit(pool)
        del pool
    else:
        import lightgbm as lgb
        ds = lgb.Dataset(df, label=y[sel], categorical_feature=list(fr.cat_features) or "auto", free_raw_data=True)
        model = lgb.train(dict(LGB_PARAMS, num_threads=int(threads)), ds, num_boost_round=int(iters))
        del ds, df
    gc.collect()
    return model


def predict(lib, model, fr, idx, threads, ntree_end=0):
    out = np.empty(len(idx))
    for i in range(0, len(idx), PRED_CHUNK):
        df = fr.make_df(idx[i:i + PRED_CHUNK])
        if lib == "cb":
            out[i:i + PRED_CHUNK] = model.predict_proba(df, ntree_end=ntree_end, thread_count=threads)[:, 1]
        else:
            out[i:i + PRED_CHUNK] = model.predict(df, num_iteration=int(ntree_end) if ntree_end else None, num_threads=int(threads))
    return out


def save_model(lib, model, path):
    model.save_model(path)


def load_model(lib, path):
    if lib == "cb":
        import catboost as cb
        m = cb.CatBoostClassifier()
        m.load_model(path)
        return m
    import lightgbm as lgb
    return lgb.Booster(model_file=path)


def importance(lib, model):
    if lib == "cb":
        return f1.importance(model)
    g = np.asarray(model.feature_importance(importance_type="gain"), dtype=float)
    names = list(model.feature_name())
    tot = float(g.sum()) or 1.0
    per = {n: float(x) / tot * 100.0 for n, x in sorted(zip(names, g), key=lambda t: -t[1])}
    fam = {}
    for n, x in per.items():
        fam[f1.family(n)] = fam.get(f1.family(n), 0.0) + x
    return {"features": per, "families": dict(sorted(fam.items(), key=lambda t: -t[1])), "kind": "LightGBM gain, % of total"}


def params_of(lib, iters):
    return dict(CB_PARAMS if lib == "cb" else LGB_PARAMS, iterations=int(iters))


def lib_version(lib):
    if lib == "cb":
        import catboost as cb
        return "catboost " + cb.__version__
    import lightgbm as lgb
    return "lightgbm " + lgb.__version__


# ------------------------------------------------------------------------------------------------------ шаги

def step_nstar(a, log):
    t0 = time.perf_counter()
    out = a.out
    if os.path.exists(os.path.join(out, "nstar.json")):
        raise Refuse("nstar.json уже есть — N* замораживается один раз на сторону и библиотеку")
    fr = f1.load_frame(a.data, y_cutoff_ms=f1.T_AUG1)
    f1.feature_report(fr, out, log)
    ab = fr.abs_ms
    tr = fr.y_has & (ab >= f1.T_JUL1) & (ab < f1.T_JUL21) & (fr.y_exit <= f1.T_JUL21)
    va = fr.y_has & (ab >= f1.T_JUL22) & (ab < f1.T_JUL31)
    fr.assert_y_allowed(tr | va)
    idx = np.flatnonzero(tr)
    vidx = np.flatnonzero(va)
    if len(idx) < 100 or len(vidx) < 30:
        raise Refuse("nstar: мало строк (обучение %d, проверка %d)" % (len(idx), len(vidx)))
    y = y_pos_of(fr)
    f1.gleak(fr, idx, y, "nstar", out, log)
    if len(np.unique(y[idx])) < 2 or len(np.unique(y[vidx])) < 2:
        raise Refuse("nstar: один класс в обучении или проверке")
    t1 = time.perf_counter()
    model = fit_model(a.lib, fr, idx, y, max(GRID), a.threads)
    fit_s = time.perf_counter() - t1
    aucs = {k: auc(y[vidx], predict(a.lib, model, fr, vidx, a.threads, ntree_end=k)) for k in GRID}
    nstar = pick_nstar(aucs)
    obj = {"side": a.side, "lib": a.lib, "grid": list(GRID), "auc": {str(k): v for k, v in aucs.items()}, "nstar": int(nstar), "criterion": "AUC validation, max; tie -> smaller N",
           "n_train": int(len(idx)), "n_val": int(len(vidx)), "pos_rate_train": float(y[idx].mean()), "pos_rate_val": float(y[vidx].mean()),
           "train": "signal 2026-07-01..2026-07-20, exit <= 2026-07-21T00:00Z", "validate": "signal 2026-07-22..2026-07-30", "y_cutoff_ms": f1.T_AUG1,
           "n_features": len(fr.feats), "feature_list_sha256": fr.feat_sha, "constants_dropped": fr.dropped,
           "data_file": os.path.basename(a.data), "data_size_bytes": os.path.getsize(a.data), "data_sha256": f1.sha256_file(a.data),
           "params": params_of(a.lib, max(GRID)), "version": lib_version(a.lib)}
    f1.write_json(os.path.join(out, "nstar.json"), obj)
    log("NSTAR lib=%s side=%s N*=%d auc=%s n_train=%d n_val=%d pos_train=%.4f fit_s=%.1f total_s=%.1f; frozen nstar.json sha256=%s"
        % (a.lib, a.side, nstar, {k: round(v, 5) for k, v in aucs.items()}, len(idx), len(vidx), y[idx].mean(), fit_s, time.perf_counter() - t0, f1.sha256_file(os.path.join(out, "nstar.json"))))
    del fr, model
    gc.collect()


def sha_lines(out):
    d = os.path.join(out, "models")
    return ["%s  models/%s" % (f1.sha256_file(os.path.join(d, f)), f) for f in sorted(os.listdir(d)) if f.endswith(tuple(EXT.values()))]


def write_models_sha(out, log):
    lines = sha_lines(out)
    with open(os.path.join(out, "models.sha256"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("FROZEN models.sha256 written (%d models) — before any prediction file" % len(lines))


def verify_models_sha(out, log):
    p = os.path.join(out, "models.sha256")
    if not os.path.exists(p):
        raise Refuse("нет models.sha256 — сначала шаг train")
    want = [l.strip() for l in open(p, encoding="utf-8") if l.strip()]
    got = sha_lines(out)
    if want != got:
        raise Refuse("модели не равны models.sha256 (записано %d, на диске %d)" % (len(want), len(got)))
    log("MODELS-VERIFIED %d models equal models.sha256" % len(got))


def step_train(a, log):
    t0 = time.perf_counter()
    out = a.out
    if not os.path.exists(os.path.join(out, "nstar.json")):
        step_nstar(a, log)
    else:
        log("NSTAR already frozen (nstar.json exists) — not recomputed")
    fr = f1.load_frame(a.data)
    f1.feature_report(fr, out, log)
    ns = f1.require_nstar(a, fr, log)
    N = ns["nstar"]
    os.makedirs(os.path.join(out, "models"), exist_ok=True)
    y = y_pos_of(fr)
    day = fr.abs_ms // MS_DAY
    info = []
    jobs = [(m, False) for m in MONTHS] + ([(m, True) for m in PLC_MONTHS] if a.lib == "cb" else [])
    for label, plc in jobs:
        S = f1.month_start_ms(label)
        tr = f1.train_rows(fr, S)
        name = ("plc-%s-clf" % label) if plc else ("%s-clf" % label)
        inf = {"model": name, "month": label, "iterations": N, "placebo": plc, "lib": a.lib}
        idx = f1.check_purge(fr, tr, S, label, "clf", inf)
        yy = f1.permute_within_day(y, day, idx, seed=SEED) if plc else y
        if len(np.unique(yy[idx])) < 2:
            raise Refuse("%s: один класс в обучении" % name)
        mx, mf = f1.gleak(fr, idx, yy, name, out, log)
        t1 = time.perf_counter()
        model = fit_model(a.lib, fr, idx, yy, N, a.threads)
        fit_s = time.perf_counter() - t1
        save_model(a.lib, model, os.path.join(out, "models", name + EXT[a.lib]))
        if not plc:
            f1.write_json(os.path.join(out, "importance-%s.json" % label), {"clf": importance(a.lib, model)}, sort=False)
        inf.update(n_train_all=int(len(idx)), n_train_used=int(len(idx)), subsample_fraction=1.0, pos_rate=float(yy[idx].mean()), gleak_max=mx, gleak_feature=mf, fit_s=fit_s)
        log("MODEL %s: N=%d n_train=%d (all rows, no subsample) pos_rate=%.4f max_sig_ms=%d max_exit_ms=%d (limit %d) gleak=%.4f(%s) fit_s=%.1f"
            % (name, N, len(idx), yy[idx].mean(), inf["max_sig_ms"], inf["max_exit_ms"], inf["purge_limit_exit_ms"], mx, mf, fit_s))
        info.append(inf)
        del model
        gc.collect()
    f1.write_json(os.path.join(out, "models-info.json"), sorted(info, key=lambda d: d["model"]))
    write_models_sha(out, log)
    f1.write_json(os.path.join(out, "train-done.json"), {"lib": a.lib, "side": a.side, "N": N, "wall_s": time.perf_counter() - t0, "models": len(info), "models_sha256_file_sha": f1.sha256_file(os.path.join(out, "models.sha256")),
                                                      "nstar_sha": f1.sha256_file(os.path.join(out, "nstar.json"))})


def step_score(a, log):
    t0 = time.perf_counter()
    out = a.out
    verify_models_sha(out, log)
    fr = f1.load_frame(a.data)
    ns = f1.require_nstar(a, fr, log)
    parts = []
    for label in MONTHS:
        S, E = f1.month_start_ms(label), month_end_ms_capped(label)
        idx = np.flatnonzero((fr.abs_ms >= S - MS_DAY) & (fr.abs_ms < E))
        m = load_model(a.lib, os.path.join(out, "models", "%s-clf%s" % (label, EXT[a.lib])))
        s = predict(a.lib, m, fr, idx, a.threads)
        plc = np.full(len(idx), np.nan)
        pp = os.path.join(out, "models", "plc-%s-clf%s" % (label, EXT[a.lib]))
        if os.path.exists(pp):
            plc = predict(a.lib, load_model(a.lib, pp), fr, idx, a.threads)
        parts.append(pd.DataFrame({"win": [f1.WIN_NAME[int(c)] for c in fr.win[idx]], "row_id": fr.row_id[idx], "month": label, "warm": fr.abs_ms[idx] < S, "score": s, "score_plc": plc}))
        log("SCORED %s: rows %d (warm %d) from %d to %d" % (label, len(idx), int((fr.abs_ms[idx] < S).sum()), S - MS_DAY, E))
        del m
        gc.collect()
    df = pd.concat(parts, ignore_index=True)
    assert not df.duplicated(["win", "row_id", "month"]).any(), "дубль (win,row_id,month)"
    df.to_parquet(os.path.join(out, "pred.parquet"), engine="pyarrow", compression="zstd", index=False)
    f1.write_json(os.path.join(out, "score-done.json"), {"lib": a.lib, "side": a.side, "N": ns["nstar"], "rows": int(len(df)), "wall_s": time.perf_counter() - t0, "pred_sha256": f1.sha256_file(os.path.join(out, "pred.parquet"))})
    log("WROTE pred.parquet rows=%d" % len(df))


# ------------------------------------------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib", required=True, choices=("cb", "lgb"))
    ap.add_argument("--side", required=True, choices=("long", "short"))
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--step", default="train", choices=("train", "score"))
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    log = f1.Log(a.out)
    log("START lib=%s side=%s step=%s threads=%d data=%s" % (a.lib, a.side, a.step, a.threads, a.data))
    try:
        (step_train if a.step == "train" else step_score)(a, log)
    except Refuse as e:
        log("REFUSE: %s" % e)
        return 2
    except LeakAbort as e:
        log("ABORT: %s" % e)
        return 3
    log("DONE lib=%s side=%s step=%s" % (a.lib, a.side, a.step))
    return 0


if __name__ == "__main__":
    sys.exit(main())
