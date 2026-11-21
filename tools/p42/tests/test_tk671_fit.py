"""Тесты §13.71 (TK-671), `tk671_fit.py`: заморозка N* по июлю (метки ≥ 01.08 не читаются), очистка 24 ч, детерминизм, признаки без meta_/y_, подвыборка по хэшу, плацебо внутри суток,
G-leak, порядок артефактов (nstar.json → модели + sha256 → оценки), важность и качество на синтетике. Синтетика (≈ 24 тыс. строк, 12 признаков), данных srv нет.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk671_fit.py` — выполняет все test_* и печатает итог (нужен catboost: на srv `/data/tk592/venv/bin/python`)."""
import atexit
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk671_fit as fit  # noqa: E402

TMP = tempfile.mkdtemp(prefix="tk671fit-")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
SYM = ["S%dUSDT" % i for i in range(8)]
SMALL_GRID = (20, 40, 80, 160)
_cnt = [0]
_TRUTH = {}


def synth_df(n=24000, seed=7, leak=False):
    """Синтетические строки одной стороны (июль–13.11, три окна). Истина: 0,5·x0 + 0,15·эффект монеты; шум 1. Метка — только для y_has (≈ 75 %)."""
    rng = np.random.default_rng(seed)
    abs_ms = np.sort(rng.integers(fit.utc_ms(2026, 7, 1), fit.utc_ms(2026, 11, 14), n)).astype(np.int64)
    win = np.where(abs_ms < fit.utc_ms(2026, 8, 26), "wa", np.where(abs_ms < fit.utc_ms(2026, 10, 3), "wb", "wc"))
    row_id = np.zeros(n, dtype=np.int64)
    for w in ("wa", "wb", "wc"):
        m = win == w
        row_id[m] = np.arange(m.sum())
    sc = rng.integers(0, len(SYM), n)
    eff = (sc - 3.5) / 2.0
    x0 = rng.normal(size=n)
    truth = 0.5 * x0 + 0.15 * eff
    has = rng.random(n) < 0.75
    y_net = np.where(has, truth + rng.normal(size=n), np.nan)
    y_exit = np.where(has, abs_ms + 60_000 + (rng.random(n) * 2.5 * fit.MS_DAY).astype(np.int64), -1)
    stop = np.where(has, (rng.random(n) < 1 / (1 + np.exp(-(-1.2 * x0 - 0.3)))).astype(float), np.nan)
    df = pd.DataFrame({"win": win, "row_id": row_id, "sym": np.array(SYM)[sc]})
    for k in ("meta_abs_ms", "meta_sig_ms", "meta_ep_start", "meta_end"):
        df[k] = abs_ms
    df["meta_src"] = rng.integers(0, 12, n).astype(np.int8)
    df["meta_level_px_e8"] = rng.integers(1, 10**12, n)
    df["meta_dist_e8"] = rng.integers(1, 10**8, n)
    df["meta_base_pass"] = rng.random(n) < 0.1
    df["meta_n_ep"] = rng.integers(1, 9, n).astype(np.int32)
    df["meta_leak"] = np.nan_to_num(y_net)                                   # метаданные с утечкой: в признаки попасть не должны
    f = {"rules_x0": x0, "rules_x1": rng.normal(size=n), "rules_x2": rng.integers(0, 5, n).astype(float), "bd_a": rng.normal(size=n), "bd_b": rng.normal(size=n),
         "f12_c": rng.exponential(size=n), "t668_d": rng.normal(size=n), "t668_e": rng.normal(size=n), "mkt_f": rng.normal(size=n), "mkt_g": rng.normal(size=n),
         "time_h": (abs_ms // 3_600_000 % 24).astype(float)}
    f["bd_a"][rng.random(n) < 0.3] = np.nan
    f["t668_d"][rng.random(n) < 0.5] = np.nan
    if leak:
        f["rules_leak"] = np.where(has, y_net + 0.01 * rng.normal(size=n), np.nan)
    f["bd_const"] = np.full(n, 5.0)                                          # константа — снимается
    f["mkt_allnan"] = np.full(n, np.nan)                                     # пусто целиком — снимается
    for k, v in f.items():
        df[k] = v.astype(np.float32)
    df["y_has"], df["y_net"], df["y_entry_ms"], df["y_exit_ms"], df["y_stop"] = has, y_net, np.where(has, abs_ms + 4, -1), y_exit, stop
    _TRUTH[id(df)] = truth                                                   # истина — вне файла (не в parquet)
    return df


def write_pq(df, path):
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False, row_group_size=4000)
    return path


def new_dir():
    _cnt[0] += 1
    d = os.path.join(TMP, "d%d" % _cnt[0])
    os.makedirs(d)
    return d


_BASE = []


def base_data():
    if not _BASE:
        df = synth_df()
        _BASE.append((df, write_pq(df, os.path.join(new_dir(), "long.parquet"))))
    return _BASE[0]


def run(data, out, step, threads=1, grid=SMALL_GRID, **patch):
    old = {"GRID": fit.GRID}
    fit.GRID = grid
    for k, v in patch.items():
        old[k] = getattr(fit, k)
        setattr(fit, k, v)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = fit.main(["--side", "long", "--data", data, "--out", out, "--threads", str(threads), "--step", step])
    finally:
        for k, v in old.items():
            setattr(fit, k, v)
    return rc


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def log_lines(out):
    return open(os.path.join(out, "fit.log"), encoding="utf-8").read().splitlines()


# ---------------------------------------------------------------------------------------------------- чистые функции

def test_splitmix64_reference():
    def ref(x):
        m = (1 << 64) - 1
        z = (x + 0x9E3779B97F4A7C15) & m
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & m
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & m
        return z ^ (z >> 31)
    xs = [0, 1, 2, 12345, (1 << 32) + 7, (2 << 32) + 99999, (1 << 63) + 5]
    got = fit.splitmix64(np.array(xs, dtype=np.uint64))
    assert [int(g) for g in got] == [ref(x) for x in xs]
    assert int(got[0]) == 0xE220A8397B1DCDAF                                  # первый выход splitmix64 с зерном 0 (Vigna)


def test_subsample_stable_fraction_nested():
    class F:
        pass
    fr = F()
    n = 200_000
    win = np.repeat(np.array([0, 1, 2], dtype=np.int8), n // 3 + 1)[:n]
    rid = np.concatenate([np.arange((win == w).sum()) for w in (0, 1, 2)]).astype(np.int64)
    fr.key = fit.row_key(win, rid)
    idx = np.arange(n)
    a, b = fit.subsample(fr, idx, 0.5), fit.subsample(fr, idx, 0.5)
    assert np.array_equal(a, b)
    assert abs(len(a) / n - 0.5) < 0.01, len(a) / n
    q = fit.subsample(fr, idx, 0.25)
    assert abs(len(q) / n - 0.25) < 0.01 and set(q) <= set(a)                # 25 % ⊂ 50 %
    sub = idx[::3]
    assert np.array_equal(fit.subsample(fr, sub, 0.5), np.intersect1d(sub, a))   # решение по строке не зависит от остальных
    assert fit.keep_fraction(600_000) == 1.0 and fit.keep_fraction(600_001) == 0.5
    assert fit.keep_fraction(1_200_000) == 0.5 and fit.keep_fraction(1_200_001) == 0.25
    assert len(fit.subsample(fr, idx, 1.0)) == n


def test_permute_within_day_keeps_multiset():
    rng = np.random.default_rng(3)
    n = 5000
    abs_ms = np.sort(rng.integers(fit.utc_ms(2026, 7, 1), fit.utc_ms(2026, 7, 9), n)).astype(np.int64)
    y = rng.normal(size=n)
    day = abs_ms // fit.MS_DAY
    idx = np.flatnonzero(rng.random(n) < 0.7)
    p = fit.permute_within_day(y, day, idx)
    assert np.array_equal(p, fit.permute_within_day(y, day, idx))             # детерминизм
    out = np.setdiff1d(np.arange(n), idx)
    assert np.array_equal(p[out], y[out]), "строки вне обучения не меняются"
    for d in np.unique(day[idx]):
        r = idx[day[idx] == d]
        assert np.array_equal(np.sort(p[r]), np.sort(y[r])), "мультимножество меток суток сохранено"
    assert (p[idx] != y[idx]).mean() > 0.9, "связь строка↔метка разорвана"
    assert abs(np.corrcoef(p[idx], y[idx])[0, 1]) < 0.1
    day2 = day.copy()                                                        # перестановка суток не зависит от соседних суток
    r0 = idx[day[idx] == day[idx][0]]
    p2 = fit.permute_within_day(y, day2, r0)
    assert np.array_equal(p2[r0], p[r0])


def test_pick_nstar_ties_smaller():
    g = fit.GRID
    assert fit.pick_nstar({100: 1.0, 200: 0.9, 400: 0.9, 800: 1.1}) == 200
    assert fit.pick_nstar({100: 0.5, 200: 0.5, 400: 0.5, 800: 0.5}) == 100
    assert fit.pick_nstar({100: 1.0, 200: 0.9, 400: 0.8, 800: 0.7}) == 800 and g == (100, 200, 400, 800)


def test_spearman_scan_matches_scipy():
    from scipy.stats import spearmanr
    rng = np.random.default_rng(1)
    m = 3000
    y = rng.normal(size=m)
    X = np.stack([y + rng.normal(size=m), rng.normal(size=m), np.full(m, 2.0), np.round(y * 2) / 2, rng.normal(size=m)], axis=1).astype(np.float32)
    X[rng.random(m) < 0.4, 4] = np.nan
    X[:, 1][rng.random(m) < 0.999] = np.nan                                  # < 30 значений
    r = fit.spearman_scan(X, y, None)
    for j in (0, 3, 4):
        ok = ~np.isnan(X[:, j])
        assert abs(r[j] - spearmanr(X[ok, j], y[ok])[0]) < 1e-9, j
    assert np.isnan(r[1]) and np.isnan(r[2])


def test_family_and_importance_groups():
    assert fit.family("rules_src_p30n1") == "rules" and fit.family("bd_a") == "bd" and fit.family("sym") == "sym"


# ---------------------------------------------------------------------------------------------------- загрузка

def test_load_frame_features_no_meta_no_labels():
    df, path = base_data()
    fr = fit.load_frame(path)
    assert fr.n == len(df)
    assert not any(c.startswith("meta_") or c.startswith("y_") or c in ("win", "row_id") for c in fr.feats), fr.feats
    assert "meta_leak" not in fr.feats and "rules_x0" in fr.feats and "sym" in fr.feats
    assert set(fr.dropped) == {"bd_const", "mkt_allnan"}, fr.dropped
    assert fr.feats == [c for c in df.columns if c == "sym" or (c not in ("win", "row_id") and not c.startswith(("meta_", "y_")) and c not in fr.dropped)]
    assert fr.sym_pos == fr.feats.index("sym") and fr.X.shape == (len(df), len(fr.feats) - 1) and fr.X.dtype == np.float32
    d = fr.make_df(np.arange(10))
    assert list(d.columns) == fr.feats and d["sym"].dtype == np.int32
    assert np.array_equal(fr.abs_ms, df["meta_abs_ms"].to_numpy()) and np.array_equal(fr.y_has, df["y_has"].to_numpy())
    assert np.array_equal(fr.y_net, df["y_net"].to_numpy(), equal_nan=True)
    assert np.array_equal(fr.y_exit, df["y_exit_ms"].to_numpy())


def test_load_frame_cutoff_reads_no_labels_after_august():
    df, path = base_data()
    fr = fit.load_frame(path, y_cutoff_ms=fit.T_AUG1)
    late = fr.abs_ms >= fit.T_AUG1
    assert late.sum() > 1000 and not fr.y_has[late].any() and np.isnan(fr.y_net[late]).all() and (fr.y_exit[late] == -1).all() and np.isnan(fr.y_stop[late]).all()
    early = ~late
    assert np.array_equal(fr.y_net[early], df["y_net"].to_numpy()[early], equal_nan=True) and np.array_equal(fr.y_has[early], df["y_has"].to_numpy()[early])
    try:
        fr.assert_y_allowed(late)
        raise AssertionError("guard не сработал")
    except AssertionError as e:
        assert "заморозки" in str(e)


# ---------------------------------------------------------------------------------------------------- шаг nstar

def test_nstar_ignores_labels_after_august():
    df, path = base_data()
    pois = df.copy()
    late = pois["meta_abs_ms"].to_numpy() >= fit.T_AUG1
    rng = np.random.default_rng(99)
    k = int(late.sum())
    pois.loc[late, "y_has"] = True                                           # метки «после заморозки» подменены на мусор
    pois.loc[late, "y_net"] = rng.normal(size=k) * 1e6
    pois.loc[late, "y_exit_ms"] = fit.T_JUL1 - 5
    pois.loc[late, "y_stop"] = 1.0
    p2 = write_pq(pois, os.path.join(new_dir(), "long.parquet"))
    clean = df.copy()
    clean.loc[late, ["y_net", "y_stop"]] = np.nan
    clean.loc[late, "y_has"] = False
    clean.loc[late, ["y_entry_ms", "y_exit_ms"]] = -1
    p3 = write_pq(clean, os.path.join(new_dir(), "long.parquet"))
    o1, o2, o3 = new_dir(), new_dir(), new_dir()
    for p, o in ((path, o1), (p2, o2), (p3, o3)):
        assert run(p, o, "nstar", grid=(100, 200, 400, 800)) == 0
    a, b, c = (json.load(open(os.path.join(o, "nstar.json"))) for o in (o1, o2, o3))
    for x in (b, c):                                                         # у файлов разный sha/размер — остальное идентично побайтно
        for key in ("data_sha256", "data_size_bytes"):
            x[key] = a[key]
    assert a == b == c, "N* и RMSE не зависят от меток ≥ 01.08"
    assert a["grid"] == [100, 200, 400, 800] and set(a["rmse"]) == {"100", "200", "400", "800"} and a["nstar"] in (100, 200, 400, 800)
    assert a["rmse"][str(a["nstar"])] == min(a["rmse"].values())
    exp_tr = ((df.meta_abs_ms >= fit.T_JUL1) & (df.meta_abs_ms < fit.T_JUL21) & df.y_has & (df.y_exit_ms <= fit.T_JUL21)).sum()
    exp_va = ((df.meta_abs_ms >= fit.T_JUL22) & (df.meta_abs_ms < fit.T_JUL31) & df.y_has).sum()
    assert a["n_train"] == exp_tr and a["n_val"] == exp_va and a["n_train_used"] == exp_tr
    assert a["y_cutoff_ms"] == fit.T_AUG1
    assert any("July-only" in s and "N*=%d" % a["nstar"] in s for s in log_lines(o1))
    assert not os.path.exists(os.path.join(o1, "pred.parquet")) and not os.path.exists(os.path.join(o1, "models.sha256"))


def test_nstar_rerun_refused_and_later_steps_need_it():
    _, path = base_data()
    o = new_dir()
    assert run(path, o, "months") == 2 and run(path, o, "plc") == 2
    assert not os.path.exists(os.path.join(o, "nstar.json")) and not os.path.exists(os.path.join(o, "models"))
    assert any("REFUSE" in s and "nstar.json" in s for s in log_lines(o))
    assert run(path, o, "nstar") == 0
    assert run(path, o, "nstar") == 2, "N* замораживается один раз на сторону"


def test_data_changed_after_freeze_refused():
    df, path = base_data()
    o = new_dir()
    assert run(path, o, "nstar") == 0
    p2 = write_pq(df.assign(rules_x1=df["rules_x1"] + np.float32(1.0)), os.path.join(new_dir(), "long.parquet"))
    assert run(p2, o, "months") == 2 and not os.path.exists(os.path.join(o, "pred.parquet"))
    assert any("изменён после заморозки" in s for s in log_lines(o))


def test_staged_prediction_equals_short_fit():
    df, path = base_data()
    fr = fit.load_frame(path, y_cutoff_ms=fit.T_AUG1)
    tr = np.flatnonzero(fr.y_has & (fr.abs_ms < fit.T_JUL21))
    va = np.flatnonzero(fr.y_has & (fr.abs_ms >= fit.T_JUL22) & (fr.abs_ms < fit.T_JUL31))
    big = fit.fit_model(fr, tr, fr.y_net, "reg", 60, 1)
    for k in (20, 40, 60):
        small = fit.fit_model(fr, tr, fr.y_net, "reg", k, 1)
        d = np.abs(fit.predict(big, fr, va, "reg", 1, ntree_end=k) - fit.predict(small, fr, va, "reg", 1))
        assert d.max() < 1e-6, (k, d.max())


# ---------------------------------------------------------------------------------------------------- G-leak

def test_gleak_abort_writes_offenders_no_exclusion():
    df = synth_df(leak=True)
    path = write_pq(df, os.path.join(new_dir(), "long.parquet"))
    o = new_dir()
    assert run(path, o, "nstar") == 3
    lk = json.load(open(os.path.join(o, "leak-nstar.json")))
    assert [x["feature"] for x in lk["offenders"]] == ["rules_leak"] and abs(lk["offenders"][0]["rho"]) > 0.9 and lk["threshold"] == 0.5
    assert not os.path.exists(os.path.join(o, "nstar.json")), "после G-leak шаг остановлен"
    g = json.load(open(os.path.join(o, "gleak.json")))
    assert g["nstar"]["max_feature"] == "rules_leak" and g["nstar"]["max_abs_rho"] > 0.9
    assert any("ABORT" in s and "rules_leak" in s for s in log_lines(o))


def test_gleak_sample_deterministic_and_logged():
    _, path = base_data()
    fr = fit.load_frame(path)
    y = np.where(fr.y_has, fr.y_net, np.nan)
    idx = np.flatnonzero(fr.y_has)
    o = new_dir()
    msgs = []
    old = fit.GLEAK_SAMPLE
    fit.GLEAK_SAMPLE = 700
    try:
        a = fit.gleak(fr, idx, y, "m1", o, msgs.append)
        b = fit.gleak(fr, idx, y, "m2", o, msgs.append)
    finally:
        fit.GLEAK_SAMPLE = old
    assert a == b and a[1] == "rules_x0" and 0.2 < a[0] < 0.5 and "n_sample=700" in msgs[0]


# ---------------------------------------------------------------------------------------------------- полный прогон

_FULL = {}


def full_run(tag, threads=1):
    """Месяцы + плацебо на синтетике; SUB_BIG снижен до 6000, чтобы сработала подвыборка 50 % (сентябрь и позже)."""
    if tag not in _FULL:
        df, path = base_data()
        o = new_dir()
        assert run(path, o, "all", threads=threads, SUB_BIG=6000) == 0
        _FULL[tag] = o
    return _FULL[tag]


def test_full_run_artifacts_order_and_schema():
    df, path = base_data()
    o = full_run("a")
    for f in ("nstar.json", "features.json", "models.sha256", "pred.parquet", "fit.log", "done.json", "gleak.json", "models-info.json"):
        assert os.path.exists(os.path.join(o, f)), f
    ns = json.load(open(os.path.join(o, "nstar.json")))
    pred = pd.read_parquet(os.path.join(o, "pred.parquet"))
    assert list(pred.columns) == ["win", "row_id", "month", "s_hat", "p_stop", "s_plc"]
    assert sorted(pred.month.unique()) == list(fit.MONTHS)
    aug_plus = df[df.meta_abs_ms >= fit.T_AUG1]
    assert len(pred) == len(aug_plus) and (~aug_plus.y_has).sum() > 500, "оценки на ВСЕХ строках месяцев, включая без сделки"
    assert set(zip(pred.win, pred.row_id)) == set(zip(aug_plus.win, aug_plus.row_id))
    assert np.isfinite(pred.s_hat).all() and ((pred.p_stop > 0) & (pred.p_stop < 1)).all()
    early = pred.month.isin(["2026-08", "2026-09"])
    assert pred.s_plc[early].isna().all() and np.isfinite(pred.s_plc[~early]).all()
    # заморозка: sha256 моделей совпадают, записаны раньше оценок
    lines = open(os.path.join(o, "models.sha256")).read().strip().splitlines()
    names = sorted(os.listdir(os.path.join(o, "models")))
    assert [l.split("  ")[1] for l in lines] == ["models/" + n for n in names]
    assert len(names) == 4 + 4 + 2 and all(sha(os.path.join(o, l.split("  ")[1])) == l.split("  ")[0] for l in lines)
    lg = log_lines(o)
    fz = [i for i, s in enumerate(lg) if "FROZEN models.sha256" in s]
    wp = [i for i, s in enumerate(lg) if "WROTE pred.parquet" in s]
    ni = [i for i, s in enumerate(lg) if s.split(" ", 1)[1].startswith("NSTAR side")][0]
    mi = [i for i, s in enumerate(lg) if "MODEL " in s][0]
    assert len(fz) == len(wp) == 2 and all(f < w for f, w in zip(fz, wp)) and ni < mi
    assert any("N*=%d" % ns["nstar"] in s and "July-only" in s for s in lg)       # N* и июльское происхождение — в fit.log
    done = json.load(open(os.path.join(o, "done.json")))
    assert set(done["steps"]) == {"nstar", "months", "plc"} and done["files"]["pred.parquet"] == sha(os.path.join(o, "pred.parquet"))


def test_full_run_purge_and_row_counts():
    df, _ = base_data()
    o = full_run("a")
    info = {d["model"]: d for d in json.load(open(os.path.join(o, "models-info.json")))}
    for label in fit.MONTHS:
        S = fit.month_start_ms(label)
        exp = int((df.y_has & (df.meta_abs_ms >= fit.T_JUL1) & (df.y_exit_ms <= S - fit.MS_DAY)).sum())
        d = info["%s-reg" % label]
        assert d["n_train_all"] == exp, (label, d["n_train_all"], exp)
        assert d["max_exit_ms"] <= S - fit.MS_DAY and d["max_sig_ms"] < S and d["purge_limit_exit_ms"] == S - fit.MS_DAY
        mx = df[df.y_has & (df.meta_abs_ms >= fit.T_JUL1) & (df.y_exit_ms <= S - fit.MS_DAY)].y_exit_ms.max()
        assert d["max_exit_ms"] == mx, "максимум выхода обучения — по независимому пересчёту"
        assert d["n_val_rows"] == int(((df.meta_abs_ms >= S) & (df.meta_abs_ms < fit.month_start_ms(fit.month_label(fit.month_idx_of(label) + 1)))).sum())
        c = info["%s-clf" % label]
        assert c["subsample_fraction"] == 0.25 and abs(c["n_train_used"] / c["n_train_all"] - 0.25) < 0.05
    assert info["2026-08-reg"]["subsample_fraction"] == 1.0 and info["2026-09-reg"]["subsample_fraction"] == 0.5
    d = info["2026-10-reg"]
    assert abs(d["n_train_used"] / d["n_train_all"] - 0.5) < 0.03
    for label in fit.PLC_MONTHS:
        p = info["plc-%s-reg" % label]
        assert p["n_train_all"] == info["%s-reg" % label]["n_train_all"] and p["placebo"] is True
    lg = log_lines(o)
    assert any("subsample=yes" in s and "2026-09-reg" in s for s in lg) and any("subsample=no" in s and "2026-08-reg" in s for s in lg)
    assert all(any(("GLEAK %s-reg" % m) in s for s in lg) and any(("GLEAK %s-clf" % m) in s for s in lg) for m in fit.MONTHS)


def test_full_run_features_exclude_meta_and_labels():
    o = full_run("a")
    import catboost as cb
    feats = json.load(open(os.path.join(o, "features.json")))["features"]
    assert not any(c.startswith(("meta_", "y_")) or c in ("win", "row_id") for c in feats)
    for n in sorted(os.listdir(os.path.join(o, "models"))):
        m = cb.CatBoost()
        m.load_model(os.path.join(o, "models", n))
        assert list(m.feature_names_) == feats, n
        assert m.get_cat_feature_indices() == [feats.index("sym")]


def test_full_run_importance_and_skill():
    df, _ = base_data()
    o = full_run("a")
    for label in fit.MONTHS:
        imp = json.load(open(os.path.join(o, "importance-%s.json" % label)))
        reg, clf = imp["reg"], imp["clf"]
        assert list(reg["features"])[0] == "rules_x0" and reg["features"]["rules_x0"] > 0, label
        assert list(clf["features"])[0] == "rules_x0"
        assert abs(sum(reg["families"].values()) - 100.0) < 1e-3 and list(reg["families"])[0] == "rules"
    pred = pd.read_parquet(os.path.join(o, "pred.parquet"))
    truth = pd.Series(_TRUTH[id(df)], index=pd.MultiIndex.from_arrays([df.win, df.row_id]))
    t = truth.loc[list(zip(pred.win, pred.row_id))].to_numpy()
    for label in fit.MONTHS[1:]:
        m = (pred.month == label).to_numpy()
        assert np.corrcoef(pred.s_hat[m], t[m])[0, 1] > 0.5, label
    x0 = df.set_index(["win", "row_id"]).rules_x0.loc[list(zip(pred.win, pred.row_id))].to_numpy()
    sel = (pred.month == "2026-10").to_numpy()
    assert np.corrcoef(pred.p_stop[sel], x0[sel])[0, 1] < -0.3                 # классификатор стопа: связь с x0 отрицательная, как в синтетике
    pl = (pred.month.isin(["2026-10", "2026-11"])).to_numpy()
    assert abs(np.corrcoef(pred.s_plc[pl], t[pl])[0, 1]) < 0.25 < np.corrcoef(pred.s_hat[pl], t[pl])[0, 1]


def test_full_run_deterministic_two_runs():
    o1 = full_run("a")
    df, path = base_data()
    o2 = new_dir()
    assert run(path, o2, "all", threads=1, SUB_BIG=6000) == 0
    p1, p2 = pd.read_parquet(os.path.join(o1, "pred.parquet")), pd.read_parquet(os.path.join(o2, "pred.parquet"))
    pd.testing.assert_frame_equal(p1, p2, check_exact=True)
    assert json.load(open(os.path.join(o1, "nstar.json"))) == json.load(open(os.path.join(o2, "nstar.json")))
    # байты .cbm у CatBoost содержат случайный GUID модели — побайтно равны не будут; детерминизм — по оценкам (выше) и по числу моделей
    assert len(open(os.path.join(o1, "models.sha256")).read().splitlines()) == len(open(os.path.join(o2, "models.sha256")).read().splitlines()) == 10


def test_steps_separately_equal_all():
    """nstar → months → plc по отдельности даёт тот же pred.parquet, что `all`."""
    _, path = base_data()
    o1 = full_run("a")
    o2 = new_dir()
    for st in ("nstar", "months"):
        assert run(path, o2, st, SUB_BIG=6000) == 0
    mid = pd.read_parquet(os.path.join(o2, "pred.parquet"))
    assert mid.s_plc.isna().all() and not mid.s_hat.isna().any()
    assert run(path, o2, "plc", SUB_BIG=6000) == 0
    pd.testing.assert_frame_equal(pd.read_parquet(os.path.join(o1, "pred.parquet")), pd.read_parquet(os.path.join(o2, "pred.parquet")), check_exact=True)
    assert len(open(os.path.join(o2, "models.sha256")).read().strip().splitlines()) == 10


if __name__ == "__main__":
    fails = 0
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for k, v in tests:
        try:
            v()
            print("ok  ", k, flush=True)
        except BaseException:
            fails += 1
            print("FAIL", k, flush=True)
            traceback.print_exc()
    print("%d тестов, провалов %d" % (len(tests), fails))
    sys.exit(1 if fails else 0)
