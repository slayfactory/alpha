#!/usr/bin/env python3
"""Тесты П-42 §13.72 (TK-672): столбцы `rl_*` и G-rule, причинный выбор «топ-K в сутки», AUC, сквозной прогон fit (train → sha256 → score) на малых синтетических данных.
Запуск (на srv): PYTHONUTF8=1 /data/tk592/venv/bin/python tools/p42/tests/test_tk672_core.py"""
from __future__ import annotations

import math
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk672_fit as fit  # noqa: E402
import tk672_rules as rules  # noqa: E402
import tk672_sel as sel  # noqa: E402

H, D = sel.HOUR_MS, sel.DAY_MS


def test_rules_long():
    cols = dict(meta_base_pass=np.array([1, 0, 0, 1]), natr_bp=np.array([40.0, 39.9, np.nan, 50]), turn1h=np.array([5e5, 6e5, 6e5, 6e5]), turn24h=np.array([12e6, 13e6, 13e6, 13e6]),
                rules_src_p60n2=np.array([1, 1, 1, 1], dtype=np.float32), rules_src_lxD=np.zeros(4, dtype=np.float32), n24=np.array([220000.0, 1.0, np.nan, 100.0]))
    rl = rules.rl_columns(cols, "long")
    assert set(rl) == set(rules.RL) and all(v.dtype == np.float32 for v in rl.values())
    assert rl["rl_base"].tolist() == [1, 0, 0, 1]
    assert rl["rl_ref"].tolist() == [1, 0, 0, 0]                      # база ∧ n24 ≥ 220000
    assert rl["rl_c_natr"].tolist() == [1, 0, 0, 1]                   # NaN → 0
    assert rl["rl_c_n24"].tolist() == [1, 0, 0, 0]
    assert rl["rl_n_base"].tolist() == [4, 3, 3, 4]
    assert rl["rl_n_ref"].tolist() == [5, 3, 3, 4]
    g = rules.g_rule(cols, rl, "long")
    assert g["ok"] and g["base_n4"] == g["base_rows"] == 2 and g["ref_full"] == g["ref_rows"] == 1 and g["ref_full_need"] == 5, g


def test_rules_short():
    cols = dict(meta_base_pass=np.array([1, 2, 3, 0]), natr_bp=np.array([40.0] * 4), turn1h=np.array([5e5] * 4), turn24h=np.array([12e6] * 4),
                rules_src_p60n2=np.array([1, 0, 1, 0], dtype=np.float32), rules_src_lxD=np.array([0, 1, 1, 0], dtype=np.float32))
    rl = rules.rl_columns(cols, "short")
    assert rl["rl_base"].tolist() == [1, 0, 1, 0] and rl["rl_ref"].tolist() == [0, 1, 1, 0]
    assert rl["rl_c_n24"].tolist() == [0, 0, 0, 0]                    # n24 в данных нет → 0
    assert rl["rl_n_ref"].tolist() == [3, 4, 4, 3]
    assert rules.g_rule(cols, rl, "short")["ok"]
    bad = dict(rl, rl_n_base=np.array([3, 0, 4, 0], dtype=np.float32))
    assert not rules.g_rule(cols, bad, "short")["ok"]                 # флаговая строка с rl_n_base < 4 — красный


def test_rates():
    ms = np.array([0, 1, D, D + 5, 2 * D, 3 * D])
    base = np.array([1, 0, 1, 1, 0, 1])
    rb, ra = sel.rates(ms, base, 0, 3 * D)
    assert abs(rb - 3 / 3) < 1e-12 and abs(ra - 5 / 3) < 1e-12        # строка 3·D вне интервала


def _brute(ms, score, q, first_ms):
    out = np.zeros(len(ms), dtype=bool)
    for i in range(len(ms)):
        if ms[i] < first_ms:
            continue
        h = ms[i] // H * H
        w = score[(ms >= h - D) & (ms < h)]
        if len(w) == 0:
            continue
        k = min(max(1, math.ceil(q * len(w))), len(w))
        out[i] = score[i] >= np.sort(w)[::-1][k - 1]
    return out


def test_causal_select_equals_bruteforce():
    rng = np.random.default_rng(7)
    n = 3000
    ms = np.sort(rng.integers(0, 6 * D, n)).astype(np.int64)
    ms = ms[rng.permutation(n)]                                       # порядок входа не отсортирован
    score = np.round(rng.random(n), 2)                                # ничьи
    for q in (0.001, 0.01, 0.05, 0.2):
        got = sel.causal_select(ms, score, q, 2 * D)
        assert (got == _brute(ms, score, q, 2 * D)).all(), q
        assert not got[ms < 2 * D].any()


def test_causal_select_is_causal():
    rng = np.random.default_rng(11)
    n = 4000
    ms = rng.integers(0, 5 * D, n).astype(np.int64)
    score = rng.random(n)
    base = sel.causal_select(ms, score, 0.02, 1 * D)
    for T in (2 * D, 2 * D + 7 * H, 3 * D + 13 * H):
        s2 = score.copy()
        late = ms >= T
        s2[late] = rng.random(int(late.sum()))                        # оценки позже T меняем
        got = sel.causal_select(ms, s2, 0.02, 1 * D)
        early = ms < T                                                # оценки не раньше T не меняют выбор строк раньше T (собственная оценка строки — меняет её выбор)
        assert (got[early] == base[early]).all(), T
        # и порог часа T не видит строк ≥ T: перестановка оценок только строк ≥ T + 1 ч не меняет выбор строк раньше T + 1 ч
        s3 = score.copy()
        s3[ms >= T + H] = rng.random(int((ms >= T + H).sum()))
        got3 = sel.causal_select(ms, s3, 0.02, 1 * D)
        assert (got3[ms < T + H] == base[ms < T + H]).all(), T


def test_k_at_least_one_and_counts():
    ms = np.arange(0, 3 * D, 600_000, dtype=np.int64)                 # 144 строки в сутки
    score = np.random.default_rng(3).random(len(ms))
    got = sel.causal_select(ms, score, 6.0 / 144, D)
    per_day = [int(got[(ms >= d * D) & (ms < (d + 1) * D)].sum()) for d in (1, 2)]
    assert all(4 <= p <= 9 for p in per_day), per_day                 # около K = 6 в сутки
    assert sel.causal_select(ms[:1], score[:1], 0.5, 0)[0] == False   # пустое окно — ничего


def test_auc_and_pick():
    y = np.array([0, 0, 1, 1, 1, 0])
    s = np.array([0.1, 0.4, 0.35, 0.8, 0.8, 0.4])
    pairs = [(sp > sn) + 0.5 * (sp == sn) for sp in s[y == 1] for sn in s[y == 0]]
    assert abs(fit.auc(y, s) - np.mean(pairs)) < 1e-12
    assert fit.auc(np.ones(4), np.arange(4)) != fit.auc(np.ones(4), np.arange(4))  # один класс → NaN
    assert fit.pick_nstar({25: 0.6, 50: 0.7, 100: 0.7, 200: 0.5}) == 50            # ничья — меньшее N
    assert fit.pick_nstar({25: 0.6, 50: 0.61, 100: 0.7, 200: 0.71}) == 200


def make_synth(path, n_per_day=40, seed=5):
    import pyarrow as pa
    import pyarrow.parquet as pq
    rng = np.random.default_rng(seed)
    t0 = fit.f1.utc_ms(2026, 7, 1)
    days = 136                                                        # 01.07 … 13.11
    ms = np.sort(np.concatenate([t0 + d * D + rng.integers(0, D, n_per_day) for d in range(days)])).astype(np.int64)
    n = len(ms)
    f0, f1v, f2 = rng.normal(size=n), rng.normal(size=n), rng.integers(0, 5, n).astype(float)
    f2[rng.random(n) < 0.1] = np.nan
    net = 0.6 * f0 + rng.normal(size=n)
    has = rng.random(n) < 0.9
    base = (f0 > 2.0).astype(np.int64)
    t = pa.table({"win": pa.array(np.where(ms < fit.f1.utc_ms(2026, 9, 1), "wa", "wb")), "row_id": pa.array(np.arange(n, dtype=np.int64)), "sym": pa.array(["S%d" % (i % 7) for i in range(n)]),
                  "meta_abs_ms": pa.array(ms), "meta_base_pass": pa.array(base), "y_has": pa.array(has), "y_net": pa.array(np.where(has, net, np.nan)),
                  "y_exit_ms": pa.array(np.where(has, ms + 3 * H, -1).astype(np.int64)), "y_stop": pa.array(np.where(has, (net < -1.5).astype(float), np.nan)),
                  "f0": pa.array(f0.astype(np.float32)), "f1": pa.array(f1v.astype(np.float32)), "f2": pa.array(f2.astype(np.float32)), "const": pa.array(np.ones(n, dtype=np.float32)),
                  "rl_base": pa.array(base.astype(np.float32))})
    pq.write_table(t, path)
    return n


def test_fit_pipeline():
    import pandas as pd
    tmp = tempfile.mkdtemp(prefix="tk672test_")
    try:
        data = os.path.join(tmp, "side.parquet")
        n = make_synth(data)
        for lib in ("cb", "lgb"):
            out = os.path.join(tmp, lib)
            args = ["--lib", lib, "--side", "long", "--data", data, "--out", out, "--threads", "2"]
            assert fit.main(args + ["--step", "score"]) == 2          # без моделей оценок нет
            assert fit.main(args + ["--step", "train"]) == 0
            ns = fit.f1.read_json(os.path.join(out, "nstar.json"))
            assert ns["nstar"] in fit.GRID and set(ns["auc"]) == {"25", "50", "100", "200"} and ns["y_cutoff_ms"] == fit.f1.T_AUG1
            assert "const" in ns["constants_dropped"]
            mods = sorted(os.listdir(os.path.join(out, "models")))
            assert len(mods) == (6 if lib == "cb" else 4), mods
            assert not os.path.exists(os.path.join(out, "pred.parquet")), "оценки до GO"
            sha = open(os.path.join(out, "models.sha256")).read()
            assert fit.main(args + ["--step", "score"]) == 0
            p = pd.read_parquet(os.path.join(out, "pred.parquet"))
            assert set(p["month"]) == set(fit.MONTHS) and p["score"].between(0, 1).all()
            assert p.groupby("month")["warm"].sum().gt(0).all()
            # окно оценок: [начало − 24 ч, конец месяца), ноябрь — до 13.11
            lo = fit.f1.utc_ms(2026, 9, 1) - D
            assert (p[(p["month"] == "2026-09") & p["warm"]].shape[0]) == n_in(data, lo, fit.f1.utc_ms(2026, 9, 1))
            assert open(os.path.join(out, "models.sha256")).read() == sha
            # модель изменена → оценки отказываются
            m0 = os.path.join(out, "models", mods[0])
            with open(m0, "ab") as f:
                f.write(b"x")
            assert fit.main(args + ["--step", "score"]) == 2
        # плацебо только у CatBoost, оценки плацебо в октябре и ноябре
        pc = pd.read_parquet(os.path.join(tmp, "cb", "pred.parquet"))
        assert pc.loc[pc["month"].isin(fit.PLC_MONTHS), "score_plc"].notna().all() and pc.loc[~pc["month"].isin(fit.PLC_MONTHS), "score_plc"].isna().all()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def n_in(path, lo, hi):
    import pyarrow.parquet as pq
    a = pq.read_table(path, columns=["meta_abs_ms"]).column(0).to_numpy()
    return int(((a >= lo) & (a < hi)).sum())


if __name__ == "__main__":
    fails = 0
    for name, f in sorted(globals().items()):
        if name.startswith("test_") and callable(f):
            try:
                f()
                print("ok  ", name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                import traceback
                traceback.print_exc()
                print("FAIL", name, e)
    sys.exit(1 if fails else 0)
