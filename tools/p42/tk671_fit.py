#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), шаг fit: скользящее обучение CatBoost на всех кандидатах одной стороны (контракт — prereg-13.71.md §3–§5).

  python3 tk671_fit.py --side long|short --data <side>.parquet --out <dir> [--threads 4] [--step nstar|months|plc|all]

Вход: parquet стороны (все три окна подряд): ключи `win`, `row_id`; `sym` — признак-категория; `meta_*` — НЕ признаки; `y_has/y_net/y_entry_ms/y_exit_ms/y_stop` — метки, НЕ признаки; остальное — признаки float32.
Константы (nunique ≤ 1 по всему файлу, метки не нужны) снимаются, список — `<out>/features.json`.
  nstar   ОДИН раз на сторону, до чтения любой метки сигнала ≥ 01.08: файл читается ТОЛЬКО по строкам meta_abs_ms < 2026-08-01 (фильтр parquet), окно обучения 01–20.07 (выход ≤ 21.07 00:00 UTC),
          проверка 22–30.07, одна подгонка на 800 итераций, RMSE на 100/200/400/800 деревьях, N* = минимум (ничья — меньший) → `nstar.json`. Без него months/plc отказываются работать.
  months  по месяцу проверки (авг, сен, окт, 01–13.11): обучение — строки с меткой, сигнал ≥ 01.07, выход ≤ начало месяца − 24 ч; регрессор RMSE на y_net (`s_hat`) + классификатор Logloss на y_stop (`p_stop`, 25 % подвыборка);
          оценки — на ВСЕХ строках месяца. Модели + sha256 (`models.sha256`) пишутся ДО файла оценок.
  plc     плацебо окт и 01–13.11: как months, но y_net переставлен внутри каждых суток UTC среди строк обучения этих суток → `s_plc`.
Перед каждой подгонкой (и в nstar) — G-leak: |Spearman ρ| каждого признака к y_net на строках обучения этой модели (≤ 150 000, детерминированно); любой |ρ| > 0,5 → `leak-<модель>.json`, остановка шага (код 3), без автоисключения.
Выход: `pred.parquet` (win,row_id,month,s_hat,p_stop,s_plc), `fit.log`, `done.json`, `models/`, `importance-*.json`, `gleak.json`, `models-info.json`.
"""
from __future__ import annotations

import argparse
import calendar
import gc
import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

SEED = 671
GRID = (100, 200, 400, 800)
PARAMS = dict(depth=6, learning_rate=0.05, l2_leaf_reg=3, border_count=128, random_seed=SEED, allow_writing_files=False, verbose=0)
WIN = {"wa": 0, "wb": 1, "wc": 2}
WIN_NAME = {v: k for k, v in WIN.items()}
MS_DAY = 86_400_000
MONTHS = ("2026-08", "2026-09", "2026-10", "2026-11")
PLC_MONTHS = ("2026-10", "2026-11")
SUB_BIG, SUB_HUGE = 600_000, 1_200_000            # строк обучения: > 600 k → 50 %, > 1,2 M → 25 %
CLF_FRAC = 0.25
GLEAK_THR, GLEAK_SAMPLE = 0.5, 150_000
GLEAK_SALT = 0x5EED_671
PRED_CHUNK = 100_000
REQUIRED = ("win", "row_id", "sym", "meta_abs_ms", "y_has", "y_net", "y_exit_ms", "y_stop")


def utc_ms(y, m, d=1):
    return calendar.timegm((y, m, d, 0, 0, 0)) * 1000


T_JUL1, T_JUL21, T_JUL22, T_JUL31, T_AUG1 = utc_ms(2026, 7, 1), utc_ms(2026, 7, 21), utc_ms(2026, 7, 22), utc_ms(2026, 7, 31), utc_ms(2026, 8, 1)


def month_start_ms(label):
    y, m = label.split("-")
    return utc_ms(int(y), int(m))


def month_index(abs_ms):
    """Месяцы от эпохи UTC для массива мс."""
    return abs_ms.astype("datetime64[ms]").astype("datetime64[M]").astype(np.int64)


def month_idx_of(label):
    y, m = label.split("-")
    return (int(y) - 1970) * 12 + int(m) - 1


def month_label(mi):
    return "%d-%02d" % (1970 + mi // 12, mi % 12 + 1)


class Refuse(Exception):
    """Шаг отказывается работать (нет заморозки, расхождение данных)."""


class LeakAbort(Exception):
    """G-leak: признак с |ρ| > 0,5 к метке на обучении — стоп и разбор."""


class Log:
    def __init__(self, out):
        self.f = open(os.path.join(out, "fit.log"), "a", encoding="utf-8")

    def __call__(self, msg):
        line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


# ------------------------------------------------------------------------------------------------------ хэш, подвыборка

def splitmix64(x):
    """splitmix64 (Vigna): массив uint64 → массив uint64; стабильный, не `hash()`."""
    z = np.asarray(x, dtype=np.uint64) + np.uint64(0x9E3779B97F4A7C15)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return z ^ (z >> np.uint64(31))


def row_key(win_code, row_id):
    return (np.asarray(win_code, dtype=np.uint64) << np.uint64(32)) + np.asarray(row_id, dtype=np.uint64)


def hash_u01(key, salt=0):
    h = splitmix64(np.asarray(key, dtype=np.uint64) ^ np.uint64(salt))
    return (h >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def keep_fraction(n_train):
    return 0.25 if n_train > SUB_HUGE else (0.5 if n_train > SUB_BIG else 1.0)


def subsample(fr, idx, frac):
    """Детерминированная подвыборка строк idx (индексы в Frame) по хэшу ключа (win, row_id)."""
    if frac >= 1.0:
        return idx
    return idx[hash_u01(fr.key[idx]) < frac]


# ------------------------------------------------------------------------------------------------------ чтение

def to_f32(col):
    return pc.cast(col, pa.float32()).to_numpy(zero_copy_only=False).astype(np.float32, copy=False)


def is_const(a):
    ok = ~np.isnan(a)
    if not ok.any():
        return True
    v = a[ok]
    return bool(v.min() == v.max())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class Frame:
    """Строки одной стороны: ключи, время, sym-коды, признаки float32 (n, F), метки. `y_cutoff_ms`: метки читались только для abs_ms < cutoff (иначе None)."""

    def assert_y_allowed(self, mask):
        if self.y_cutoff_ms is not None and mask.any():
            assert int(self.abs_ms[mask].max()) < self.y_cutoff_ms, "метка строки с сигналом ≥ %d использована до заморозки N*" % self.y_cutoff_ms

    def make_df(self, idx):
        df = pd.DataFrame(self.X[idx], columns=self.num_names, copy=False)
        if self.sym_pos is not None:
            df.insert(self.sym_pos, "sym", self.sym_code[idx])
        return df

    @property
    def cat_features(self):
        return ["sym"] if self.sym_pos is not None else []


def load_frame(path, y_cutoff_ms=None, chunk=48):
    pf = pq.ParquetFile(path)
    names = list(pf.schema_arrow.names)
    miss = [c for c in REQUIRED if c not in names]
    if miss:
        raise Refuse("в %s нет столбцов %s" % (path, miss))
    n = pf.metadata.num_rows
    fr = Frame()
    fr.path, fr.n, fr.y_cutoff_ms = path, n, y_cutoff_ms
    head = pf.read(columns=["win", "row_id", "sym", "meta_abs_ms"])
    w = np.asarray(pc.cast(head.column("win"), pa.string()).to_numpy(zero_copy_only=False), dtype=object)
    fr.win = np.full(n, -1, dtype=np.int8)
    for k, v in WIN.items():
        fr.win[w == k] = v
    assert (fr.win >= 0).all(), "win вне {wa,wb,wc}"
    fr.row_id = head.column("row_id").to_numpy().astype(np.int64)
    assert (fr.row_id >= 0).all() and fr.row_id.max() < (1 << 32), "row_id вне [0, 2^32)"
    fr.key = row_key(fr.win, fr.row_id)
    assert len(np.unique(fr.key)) == n, "ключ (win,row_id) не уникален"
    fr.abs_ms = head.column("meta_abs_ms").to_numpy().astype(np.int64)
    sym = np.asarray(pc.cast(head.column("sym"), pa.string()).to_numpy(zero_copy_only=False), dtype=object)
    assert not pd.isna(sym).any(), "sym содержит null"
    fr.sym_code, uniq = pd.factorize(sym, sort=True)
    fr.sym_code = fr.sym_code.astype(np.int32)
    fr.sym_categories = [str(u) for u in uniq]
    del head, w, sym
    fcols = [c for c in names if c not in ("win", "row_id", "sym") and not c.startswith("meta_") and not c.startswith("y_")]
    X = np.empty((n, len(fcols)), dtype=np.float32)
    const = np.zeros(len(fcols), dtype=bool)
    for i in range(0, len(fcols), chunk):
        cs = fcols[i:i + chunk]
        t = pf.read(columns=cs)
        for j, c in enumerate(cs):
            try:
                a = to_f32(t.column(c))
            except pa.ArrowInvalid as e:
                raise Refuse("признак %s не числовой: %s" % (c, e))
            const[i + j] = is_const(a)
            X[:, i + j] = a
        del t
    sym_const = len(fr.sym_categories) <= 1
    keep = ~const
    fr.dropped = [c for c, k in zip(fcols, keep) if not k] + (["sym"] if sym_const else [])
    if not keep.all():
        X = np.ascontiguousarray(X[:, keep])
    fr.X = X
    fr.num_names = [c for c, k in zip(fcols, keep) if k]
    feats, num_it = [], iter(fr.num_names)
    for c in names:
        if c == "sym":
            if not sym_const:
                feats.append("sym")
        elif c in fcols:
            if not const[fcols.index(c)]:
                feats.append(next(num_it))
    fr.feats = feats
    fr.sym_pos = feats.index("sym") if "sym" in feats else None
    fr.feat_sha = sha256_text("\n".join(feats))
    _load_labels(fr, pf, path)
    return fr


def _load_labels(fr, pf, path):
    n = fr.n
    fr.y_has = np.zeros(n, dtype=bool)
    fr.y_net = np.full(n, np.nan)
    fr.y_exit = np.full(n, -1, dtype=np.int64)
    fr.y_stop = np.full(n, np.nan)
    cols = ["win", "row_id", "meta_abs_ms", "y_has", "y_net", "y_exit_ms", "y_stop"]
    if fr.y_cutoff_ms is None:
        t = pf.read(columns=cols)
        pos = np.arange(n)
    else:
        # заморозка: метки читаются только для строк до cutoff (фильтр на чтении; остальные строки остаются нейтральными)
        t = pq.read_table(path, columns=cols, filters=[("meta_abs_ms", "<", int(fr.y_cutoff_ms))])
        w = np.asarray(pc.cast(t.column("win"), pa.string()).to_numpy(zero_copy_only=False), dtype=object)
        wc = np.full(len(w), -1, dtype=np.int8)
        for k, v in WIN.items():
            wc[w == k] = v
        key = row_key(wc, t.column("row_id").to_numpy().astype(np.int64))
        order = np.argsort(fr.key)
        pos = order[np.searchsorted(fr.key[order], key)]
        assert (fr.key[pos] == key).all(), "ключи фильтрованных строк не найдены в файле"
        assert len(key) == 0 or int(t.column("meta_abs_ms").to_numpy().max()) < fr.y_cutoff_ms, "фильтр вернул строку с сигналом ≥ cutoff"
    fr.y_has[pos] = pc.fill_null(pc.cast(t.column("y_has"), pa.bool_()), False).to_numpy(zero_copy_only=False)
    fr.y_net[pos] = pc.cast(t.column("y_net"), pa.float64()).to_numpy(zero_copy_only=False)
    fr.y_exit[pos] = pc.fill_null(pc.cast(t.column("y_exit_ms"), pa.int64()), -1).to_numpy(zero_copy_only=False)
    fr.y_stop[pos] = pc.cast(t.column("y_stop"), pa.float64()).to_numpy(zero_copy_only=False)
    if fr.y_has.any():
        assert not np.isnan(fr.y_net[fr.y_has]).any(), "y_has без y_net"
        assert (fr.y_exit[fr.y_has] >= 0).all(), "y_has без y_exit_ms"


# ------------------------------------------------------------------------------------------------------ G-leak

def rank(a):
    try:
        from scipy.stats import rankdata
        return rankdata(a)
    except ImportError:
        return pd.Series(a).rank().to_numpy()


def spearman_scan(Xs, ys, names):
    """|ρ| Спирмена каждого числового столбца Xs к ys (попарно по не-NaN); столбцы с < 30 значений или константы — NaN."""
    m, F = Xs.shape
    ry = rank(ys)
    out = np.full(F, np.nan)
    for j in range(F):
        x = Xs[:, j]
        ok = ~np.isnan(x)
        k = int(ok.sum())
        if k < 30:
            continue
        if k == m:
            rx, r2 = rank(x), ry
        else:
            rx, r2 = rank(x[ok]), rank(ys[ok])
        rx = rx - rx.mean()
        r2 = r2 - r2.mean()
        den = float(np.sqrt((rx * rx).sum() * (r2 * r2).sum()))
        if den > 0:
            out[j] = float((rx * r2).sum()) / den
    return out


def gleak(fr, idx, y, model, out, log):
    """G-leak на строках обучения модели (индексы idx): max |ρ| признаков к y; > GLEAK_THR — leak-<model>.json и LeakAbort. `sym` не порядковый — пропуск."""
    if len(idx) > GLEAK_SAMPLE:
        o = np.argsort(hash_u01(fr.key[idx], GLEAK_SALT), kind="stable")[:GLEAK_SAMPLE]
        idx = idx[np.sort(o)]
    rho = spearman_scan(fr.X[idx], y[idx], fr.num_names)
    a = np.abs(rho)
    if np.isnan(a).all():
        mx, mf = float("nan"), None
    else:
        j = int(np.nanargmax(a))
        mx, mf = float(a[j]), fr.num_names[j]
    log("GLEAK %s: n_sample=%d max|rho|=%.4f (%s)" % (model, len(idx), mx, mf))
    gp = os.path.join(out, "gleak.json")
    g = json.load(open(gp, encoding="utf-8")) if os.path.exists(gp) else {}
    g[model] = {"n_sample": int(len(idx)), "max_abs_rho": mx, "max_feature": mf, "threshold": GLEAK_THR}
    json.dump(g, open(gp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    bad = [(fr.num_names[j], float(rho[j])) for j in np.flatnonzero(a > GLEAK_THR)]
    if bad:
        bad.sort(key=lambda t: -abs(t[1]))
        json.dump({"model": model, "threshold": GLEAK_THR, "n_sample": int(len(idx)), "offenders": [{"feature": f, "rho": r} for f, r in bad]},
                  open(os.path.join(out, "leak-%s.json" % model), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        raise LeakAbort("G-leak %s: %d признаков с |rho| > %.2f (макс %s %.3f) — стоп и разбор, leak-%s.json" % (model, len(bad), GLEAK_THR, bad[0][0], bad[0][1], model))
    return mx, mf


# ------------------------------------------------------------------------------------------------------ модели

def catboost():
    import catboost as cb
    return cb


def fit_model(fr, sel, y, kind, iters, threads):
    """Одна Pool на модель; после подгонки Pool освобождается."""
    cb = catboost()
    pool = cb.Pool(fr.make_df(sel), label=y[sel], cat_features=fr.cat_features)
    cls = cb.CatBoostRegressor if kind == "reg" else cb.CatBoostClassifier
    model = cls(loss_function="RMSE" if kind == "reg" else "Logloss", iterations=int(iters), thread_count=int(threads), **PARAMS)
    model.fit(pool)
    del pool
    gc.collect()
    return model


def predict(model, fr, idx, kind, threads, ntree_end=0):
    out = np.empty(len(idx))
    for i in range(0, len(idx), PRED_CHUNK):
        df = fr.make_df(idx[i:i + PRED_CHUNK])
        if kind == "reg":
            out[i:i + PRED_CHUNK] = model.predict(df, ntree_end=ntree_end, thread_count=threads)
        else:
            out[i:i + PRED_CHUNK] = model.predict_proba(df, ntree_end=ntree_end, thread_count=threads)[:, 1]
    return out


def family(name):
    return name.split("_", 1)[0] if "_" in name else name


def importance(model):
    v = np.asarray(model.get_feature_importance(type="PredictionValuesChange"), dtype=float)
    names = list(model.feature_names_)
    per = {n: float(x) for n, x in sorted(zip(names, v), key=lambda t: -t[1])}
    fam = {}
    for n, x in per.items():
        fam[family(n)] = fam.get(family(n), 0.0) + x
    return {"features": per, "families": dict(sorted(fam.items(), key=lambda t: -t[1]))}


def permute_within_day(y, day, idx, seed=SEED):
    """Плацебо: копия y, где y[idx] переставлены внутри каждых суток UTC (мультимножество меток суток сохраняется). Перестановка суток зависит только от (seed, сутки)."""
    out = y.copy()
    d = day[idx]
    order = np.argsort(d, kind="stable")
    ds = d[order]
    cuts = np.flatnonzero(np.diff(ds)) + 1
    for part in np.split(order, cuts):
        rows = idx[part]
        rng = np.random.default_rng([seed, int(day[rows[0]])])
        out[rows] = y[rows][rng.permutation(len(rows))]
    return out


def train_rows(fr, start_ms):
    """Обучение месяца: строка с меткой, сигнал ≥ 01.07, выход ≤ начало месяца − 24 ч."""
    return fr.y_has & (fr.abs_ms >= T_JUL1) & (fr.y_exit <= start_ms - MS_DAY)


def pick_nstar(rmse):
    return min(GRID, key=lambda k: (rmse[k], k))


def write_json(path, obj, sort=True):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=sort)
        f.write("\n")


def read_json(path, default=None):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def feature_report(fr, out, log):
    path = os.path.join(out, "features.json")
    obj = {"features": fr.feats, "n_features": len(fr.feats), "feature_list_sha256": fr.feat_sha, "constants_dropped": fr.dropped, "sym_categories": fr.sym_categories}
    if os.path.exists(path):
        old = read_json(path)
        if old["feature_list_sha256"] != fr.feat_sha:
            raise Refuse("список признаков не равен features.json (sha %s != %s)" % (fr.feat_sha, old["feature_list_sha256"]))
    else:
        write_json(path, obj)
    log("FEATURES n=%d sha256=%s constants_dropped=%d (%s)" % (len(fr.feats), fr.feat_sha, len(fr.dropped), ",".join(fr.dropped[:20]) + ("…" if len(fr.dropped) > 20 else "")))


# ------------------------------------------------------------------------------------------------------ шаги

def step_nstar(a, log):
    t0 = time.perf_counter()
    out = a.out
    if os.path.exists(os.path.join(out, "nstar.json")):
        raise Refuse("nstar.json уже есть — N* замораживается один раз на сторону")
    fr = load_frame(a.data, y_cutoff_ms=T_AUG1)
    feature_report(fr, out, log)
    ab = fr.abs_ms
    tr = fr.y_has & (ab >= T_JUL1) & (ab < T_JUL21) & (fr.y_exit <= T_JUL21)
    va = fr.y_has & (ab >= T_JUL22) & (ab < T_JUL31)
    fr.assert_y_allowed(tr | va)
    idx_all = np.flatnonzero(tr)
    if len(idx_all) < 100 or va.sum() < 30:
        raise Refuse("nstar: мало строк (обучение %d, проверка %d)" % (len(idx_all), va.sum()))
    frac = keep_fraction(len(idx_all))
    sel = subsample(fr, idx_all, frac)
    gleak(fr, sel, fr.y_net, "nstar", out, log)
    vidx = np.flatnonzero(va)
    t1 = time.perf_counter()
    model = fit_model(fr, sel, fr.y_net, "reg", max(GRID), a.threads)
    fit_s = time.perf_counter() - t1
    df = fr.make_df(vidx)
    yv = fr.y_net[vidx]
    rmse = {}
    for k in GRID:
        p = model.predict(df, ntree_end=k, thread_count=a.threads)
        rmse[k] = float(np.sqrt(np.mean((p - yv) ** 2)))
    nstar = pick_nstar(rmse)
    obj = {"side": a.side, "grid": list(GRID), "rmse": {str(k): v for k, v in rmse.items()}, "nstar": int(nstar),
           "n_train": int(len(idx_all)), "n_train_used": int(len(sel)), "subsample_fraction": frac, "n_val": int(len(vidx)),
           "train": "signal 2026-07-01..2026-07-20, exit <= 2026-07-21T00:00Z", "validate": "signal 2026-07-22..2026-07-30",
           "y_cutoff_ms": T_AUG1, "n_features": len(fr.feats), "feature_list_sha256": fr.feat_sha, "constants_dropped": fr.dropped,
           "data_file": os.path.basename(a.data), "data_size_bytes": os.path.getsize(a.data), "data_sha256": sha256_file(a.data),
           "params": dict(PARAMS, loss_function="RMSE", iterations=max(GRID)), "catboost_version": catboost().__version__}
    write_json(os.path.join(out, "nstar.json"), obj)
    log("NSTAR side=%s N*=%d rmse=%s n_train=%d(used %d, frac %.2f) n_val=%d fit_s=%.1f total_s=%.1f; July-only: labels read only for signal < 2026-08-01; frozen in nstar.json sha256=%s"
        % (a.side, nstar, {k: round(v, 6) for k, v in rmse.items()}, len(idx_all), len(sel), frac, len(vidx), fit_s, time.perf_counter() - t0, sha256_file(os.path.join(out, "nstar.json"))))
    update_done(out, a, "nstar", {"nstar": int(nstar), "wall_s": time.perf_counter() - t0})
    del fr, model, df
    gc.collect()


def require_nstar(a, fr, log):
    p = os.path.join(a.out, "nstar.json")
    ns = read_json(p)
    if ns is None:
        raise Refuse("нет nstar.json — сначала шаг nstar (заморозка N* по июлю)")
    if ns["side"] != a.side:
        raise Refuse("nstar.json для стороны %s, запрошена %s" % (ns["side"], a.side))
    if os.path.getsize(a.data) != ns["data_size_bytes"] or sha256_file(a.data) != ns["data_sha256"]:
        raise Refuse("файл данных изменён после заморозки N* (размер/sha256 не равны nstar.json)")
    if fr.feat_sha != ns["feature_list_sha256"]:
        raise Refuse("список признаков не равен записанному в nstar.json")
    log("NSTAR-FROZEN N*=%d (July-only provenance: nstar.json sha256=%s, train 01-20.07 / validate 22-30.07, data sha256 %s…)" % (ns["nstar"], sha256_file(p), ns["data_sha256"][:12]))
    return ns


def month_rows(fr, label):
    return np.flatnonzero(month_index(fr.abs_ms) == month_idx_of(label))


def check_purge(fr, tr, start_ms, label, kind, info):
    idx = np.flatnonzero(tr)
    assert len(idx) >= 100, "%s: мало строк обучения (%d)" % (label, len(idx))
    mx_exit, mx_sig = int(fr.y_exit[idx].max()), int(fr.abs_ms[idx].max())
    assert mx_exit <= start_ms - MS_DAY, "%s: выход строки обучения позже начала месяца − 24 ч" % label
    assert mx_sig < start_ms, "%s: сигнал строки обучения не раньше начала месяца" % label
    info.update(max_exit_ms=mx_exit, max_sig_ms=mx_sig, purge_limit_exit_ms=start_ms - MS_DAY)
    return idx


def step_months_like(a, log, fr, ns, months, plc, info_all):
    """months (plc=False): s_hat + p_stop по месяцам; plc (True): s_plc (плацебо). Возвращает словарь month → (rows, s, p)."""
    N = ns["nstar"]
    res, models = {}, []
    day = fr.abs_ms // MS_DAY
    for label in months:
        S = month_start_ms(label)
        tr = train_rows(fr, S)
        fr.assert_y_allowed(tr)
        name = ("plc-%s-reg" % label) if plc else ("%s-reg" % label)
        info = {"model": name, "month": label, "iterations": N}
        idx_all = check_purge(fr, tr, S, label, "reg", info)
        frac = keep_fraction(len(idx_all))
        sel = subsample(fr, idx_all, frac)
        y = permute_within_day(fr.y_net, day, idx_all) if plc else fr.y_net
        mx, mf = gleak(fr, sel, y, name, a.out, log)
        t1 = time.perf_counter()
        model = fit_model(fr, sel, y, "reg", N, a.threads)
        fit_s = time.perf_counter() - t1
        vidx = month_rows(fr, label)
        s = predict(model, fr, vidx, "reg", a.threads)
        pth = os.path.join(a.out, "models", name + ".cbm")
        model.save_model(pth)
        if not plc:
            write_json(os.path.join(a.out, "importance-%s.json" % label), {"reg": importance(model)}, sort=False)
        info.update(n_train_all=int(len(idx_all)), n_train_used=int(len(sel)), subsample_fraction=frac, n_val_rows=int(len(vidx)), gleak_max=mx, gleak_feature=mf, fit_s=fit_s, placebo=plc)
        log("MODEL %s: N=%d n_train_all=%d used=%d frac=%.2f subsample=%s val_rows=%d max_sig_ms=%d max_exit_ms=%d (limit %d) gleak=%.4f(%s) fit_s=%.1f"
            % (name, N, len(idx_all), len(sel), frac, "yes" if frac < 1 else "no", len(vidx), info["max_sig_ms"], info["max_exit_ms"], info["purge_limit_exit_ms"], mx, mf, fit_s))
        info_all.append(info)
        del model
        p = None
        if not plc:
            ctr = np.flatnonzero(tr & ~np.isnan(fr.y_stop))
            csel = subsample(fr, ctr, CLF_FRAC)
            cname = "%s-clf" % label
            cinfo = {"model": cname, "month": label, "iterations": N, "n_train_all": int(len(ctr)), "n_train_used": int(len(csel)), "subsample_fraction": CLF_FRAC, "max_exit_ms": int(fr.y_exit[ctr].max()) if len(ctr) else None, "n_val_rows": int(len(vidx))}
            assert len(ctr) == 0 or cinfo["max_exit_ms"] <= S - MS_DAY
            cmx, cmf = gleak(fr, csel, fr.y_net, cname, a.out, log)
            t1 = time.perf_counter()
            if len(np.unique(fr.y_stop[csel])) < 2:
                log("MODEL %s: SKIPPED single class in training rows" % cname)
                cinfo["skipped"] = "single class"
            else:
                cm = fit_model(fr, csel, fr.y_stop, "clf", N, a.threads)
                p = predict(cm, fr, vidx, "clf", a.threads)
                cm.save_model(os.path.join(a.out, "models", cname + ".cbm"))
                imp = read_json(os.path.join(a.out, "importance-%s.json" % label))
                imp["clf"] = importance(cm)
                write_json(os.path.join(a.out, "importance-%s.json" % label), imp, sort=False)
                del cm
            cinfo.update(gleak_max=cmx, gleak_feature=cmf, fit_s=time.perf_counter() - t1)
            log("MODEL %s: N=%d n_train_all=%d used=%d frac=%.2f subsample=yes max_exit_ms=%s gleak=%.4f(%s) fit_s=%.1f" % (cname, N, len(ctr), len(csel), CLF_FRAC, cinfo["max_exit_ms"], cmx, cmf, cinfo["fit_s"]))
            info_all.append(cinfo)
        gc.collect()
        res[label] = (vidx, s, p)
    return res


def write_models_sha(out, log):
    d = os.path.join(out, "models")
    lines = ["%s  models/%s" % (sha256_file(os.path.join(d, f)), f) for f in sorted(os.listdir(d)) if f.endswith(".cbm")]
    with open(os.path.join(out, "models.sha256"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("FROZEN models.sha256 written (%d models) — before any prediction file" % len(lines))


def merge_info(out, new):
    p = os.path.join(out, "models-info.json")
    cur = {d["model"]: d for d in read_json(p, [])}
    for d in new:
        cur[d["model"]] = d
    write_json(p, [cur[k] for k in sorted(cur)])


def write_part(out, name, fr, res, plc):
    rows = []
    for label, (vidx, s, p) in res.items():
        rows.append(pd.DataFrame({"win": [WIN_NAME[int(c)] for c in fr.win[vidx]], "row_id": fr.row_id[vidx], "month": label,
                                  "s_plc" if plc else "s_hat": s, **({} if plc else {"p_stop": p if p is not None else np.full(len(vidx), np.nan)})}))
    df = pd.concat(rows, ignore_index=True)
    os.makedirs(os.path.join(out, "parts"), exist_ok=True)
    df.to_parquet(os.path.join(out, "parts", name + ".parquet"), engine="pyarrow", compression="zstd", index=False)


def assemble_pred(out, log):
    pm = os.path.join(out, "parts", "main.parquet")
    if not os.path.exists(pm):
        return
    m = pd.read_parquet(pm)
    pp = os.path.join(out, "parts", "plc.parquet")
    if os.path.exists(pp):
        m = m.merge(pd.read_parquet(pp)[["win", "row_id", "s_plc"]], on=["win", "row_id"], how="left", validate="1:1")
    else:
        m["s_plc"] = np.nan
    m = m[["win", "row_id", "month", "s_hat", "p_stop", "s_plc"]]
    m.to_parquet(os.path.join(out, "pred.parquet"), engine="pyarrow", compression="zstd", index=False)
    log("WROTE pred.parquet rows=%d (s_plc rows %d)" % (len(m), int(m["s_plc"].notna().sum())))


def update_done(out, a, step, extra):
    p = os.path.join(out, "done.json")
    d = read_json(p, {"side": a.side, "steps": {}, "files": {}})
    d["steps"][step] = extra
    for f in ("nstar.json", "features.json", "models.sha256", "pred.parquet"):
        fp = os.path.join(out, f)
        if os.path.exists(fp):
            d["files"][f] = sha256_file(fp)
    write_json(p, d)


def step_months(a, log, fr, plc):
    t0 = time.perf_counter()
    feature_report(fr, a.out, log)
    ns = require_nstar(a, fr, log)
    os.makedirs(os.path.join(a.out, "models"), exist_ok=True)
    info = []
    months = PLC_MONTHS if plc else MONTHS
    res = step_months_like(a, log, fr, ns, months, plc, info)
    merge_info(a.out, info)
    write_models_sha(a.out, log)             # модели заморожены ДО файла оценок
    write_part(a.out, "plc" if plc else "main", fr, res, plc)
    assemble_pred(a.out, log)
    update_done(a.out, a, "plc" if plc else "months", {"months": list(months), "N": ns["nstar"], "wall_s": time.perf_counter() - t0})


# ------------------------------------------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--side", required=True, choices=("long", "short"))
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--step", default="all", choices=("nstar", "months", "plc", "all"))
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    log = Log(a.out)
    log("START side=%s step=%s threads=%d data=%s" % (a.side, a.step, a.threads, a.data))
    try:
        if a.step == "nstar" or (a.step == "all" and not os.path.exists(os.path.join(a.out, "nstar.json"))):
            step_nstar(a, log)
        elif a.step == "all":
            log("NSTAR already frozen (nstar.json exists) — not recomputed")
        if a.step in ("months", "plc", "all"):
            if not os.path.exists(os.path.join(a.out, "nstar.json")):
                raise Refuse("нет nstar.json — сначала шаг nstar (заморозка N* по июлю)")
            fr = load_frame(a.data)
            if a.step in ("months", "all"):
                step_months(a, log, fr, plc=False)
            if a.step in ("plc", "all"):
                step_months(a, log, fr, plc=True)
    except Refuse as e:
        log("REFUSE: %s" % e)
        return 2
    except LeakAbort as e:
        log("ABORT: %s" % e)
        return 3
    log("DONE side=%s step=%s" % (a.side, a.step))
    return 0


if __name__ == "__main__":
    sys.exit(main())
