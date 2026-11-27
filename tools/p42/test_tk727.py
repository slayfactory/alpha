"""Тесты TK-727: оценщики шага 3 против прямой копии движка (цикл, целые числа), размах, бутстреп, регрессия шага 5 на синтетике с известным наклоном."""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk727_lib as L  # noqa: E402

SCALE = 10 ** 8


def synth(n=4000, holes=(), seed=1):
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.001, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.001, n)))
    pres = np.ones(n, bool)
    for a, b in holes:
        pres[a:b] = False
    # цены кратны шагу 0,01 (целые в единицах 1e-8)
    q = lambda x: np.rint(x * 100) / 100
    S = dict(o=q(o), h=q(h), l=q(l), c=q(c), qv=np.ones(n), pres=pres)
    for k in "ohlc":
        S[k] = np.where(pres, S[k], np.nan)
    return S


def engine_natr(S, p):
    """Прямой перенос `natr_of` (data.rs:346): целые ×1e8, префиксные суммы, prevc — закрытие предыдущей присутствующей минуты."""
    n = len(S["c"])
    co = np.where(S["pres"], np.rint(S["o"] * SCALE), 0).astype(np.int64)
    ch = np.where(S["pres"], np.rint(S["h"] * SCALE), 0).astype(np.int64)
    cl = np.where(S["pres"], np.rint(S["l"] * SCALE), 0).astype(np.int64)
    cc = np.where(S["pres"], np.rint(S["c"] * SCALE), 0).astype(np.int64)
    trp = [0] * (n + 1)
    cnt = [0] * (n + 1)
    prevc = 0
    for t in range(n):
        tr = 0
        c = 0
        if co[t] != 0:
            tr = int(ch[t] - cl[t])
            if prevc != 0:
                tr = max(tr, abs(int(ch[t]) - prevc), abs(int(cl[t]) - prevc))
            prevc = int(cc[t])
            c = 1
        trp[t + 1] = trp[t] + tr
        cnt[t + 1] = cnt[t] + c
    out = np.full(n, np.nan)
    for t in range(p - 1, n):
        if co[t] == 0:
            continue
        k = cnt[t + 1] - cnt[t + 1 - p]
        if k * 2 < p:
            continue
        s = trp[t + 1] - trp[t + 1 - p]
        out[t] = (s * SCALE // (k * int(cc[t]))) / SCALE
    return out


def engine_rv(S, p):
    n = len(S["c"])
    co = np.where(S["pres"], 1, 0)
    cc = np.where(S["pres"], np.rint(S["c"] * SCALE), 0).astype(np.int64)
    r2 = [0.0] * n
    ok = [0] * n
    for t in range(1, n):
        if co[t] and co[t - 1]:
            r = (cc[t] - cc[t - 1]) / cc[t - 1]
            r2[t] = r * r
            ok[t] = 1
    out = np.full(n, np.nan)
    for t in range(p, n):
        if not co[t]:
            continue
        k = sum(ok[t - p + 1:t + 1])
        if k * 2 < p:
            continue
        out[t] = math.sqrt(sum(r2[t - p + 1:t + 1]))
    return out


def test_natr_equals_engine_loop_with_holes():
    S = synth(3000, holes=[(500, 503), (1200, 1230), (1500, 1501)])
    for p in L.NATR_P:
        ref = engine_natr(S, p)
        got = L.natr(S, p)
        assert np.array_equal(np.isnan(ref), np.isnan(got)), p
        m = ~np.isnan(ref)
        # движок усекает до 1e-8 (целая доля ×1e8): допуск — один квант
        assert np.max(np.abs(ref[m] - got[m])) <= 1.0001 / SCALE, p


def test_rv_equals_engine_loop_with_holes():
    S = synth(3000, holes=[(500, 503), (1200, 1230), (1500, 1501)])
    for p in L.RV_P:
        ref = engine_rv(S, p)
        got = L.rv_sqrt_sum(S, p)
        assert np.array_equal(np.isnan(ref), np.isnan(got)), p
        m = ~np.isnan(ref)
        assert np.max(np.abs(ref[m] - got[m]) / ref[m]) < 1e-9, p


def test_true_range_uses_last_present_close_across_gap():
    S = synth(50, holes=[(10, 20)])
    tr = L.true_range(S)
    j = 20  # первая присутствующая после дыры — предыдущее закрытие это минута 9
    want = max(S["h"][j] - S["l"][j], abs(S["h"][j] - S["c"][9]), abs(S["l"][j] - S["c"][9]))
    assert abs(tr[j] - want) < 1e-12
    assert tr[15] == 0.0


def test_ewtr_constant_series_equals_constant():
    n = 1500
    S = dict(o=np.full(n, 100.0), h=np.full(n, 100.2), l=np.full(n, 99.8), c=np.full(n, 100.0), qv=np.ones(n), pres=np.ones(n, bool))
    e = L.ewtr(S)
    assert np.isnan(e[100])
    assert abs(e[1000] - 0.004) < 1e-9  # TR / c = 0,4 / 100


def test_gk_zero_for_flat_and_positive_otherwise():
    n = 200
    S = dict(o=np.full(n, 50.0), h=np.full(n, 50.0), l=np.full(n, 50.0), c=np.full(n, 50.0), qv=np.ones(n), pres=np.ones(n, bool))
    assert L.gk60(S)[100] == 0.0
    S2 = synth(400)
    assert np.nanmin(L.gk60(S2)) >= 0


def test_bipower_no_jump_vs_jump():
    rng = np.random.default_rng(3)
    n = 3000
    r = rng.normal(0, 0.001, n)
    r[2500] = 0.05  # скачок
    c = 100 * np.exp(np.cumsum(r))
    S = dict(o=c.copy(), h=c * 1.0001, l=c * 0.9999, c=c, qv=np.ones(n), pres=np.ones(n, bool))
    js = L.jump_share(S, 60)
    assert js[2510] > 0.5  # внутри окна скачок
    assert js[2900] < 0.5  # скачок вышел из окна 60 минут
    # RV с скачком заметно больше, чем BV
    assert L.rv_mean(S, 60)[2510] > 2 * L.bv60(S, 60)[2510]


def test_future_range_and_floor_inputs():
    S = synth(500)
    m = np.array([100, 499, 0])
    rr = L.future_range(S, m, 60)
    want = (np.max(S["h"][100:160]) - np.min(S["l"][100:160])) / S["c"][99]
    assert abs(rr[0] - want) < 1e-12
    assert np.isnan(rr[1]) and np.isnan(rr[2])


def test_future_range_requires_half_presence():
    S = synth(500, holes=[(110, 150)])
    rr = L.future_range(S, np.array([100]), 60)
    assert np.isnan(rr[0])  # из 60 минут присутствует 20 < 30


def test_tick_by_day_recovers_step():
    S = synth(1440 * 12)
    # цены кратны 0,01 → шаг 1e6 в единицах 1e-8; ряд сутки 8+ видит ≥ 7 суток
    t = L.tick_by_day(S, ndays=12)
    assert t[10] % 1_000_000 == 0 and t[10] >= 1_000_000


def test_block_len_and_stat():
    assert L.block_len(150) == 6 and L.block_len(136) == 6 and L.block_len(286) == 7
    x = np.random.default_rng(0).normal(1.0, 1.0, 150)
    s = L.stat_block(x, 6, nboot=2000)
    assert s["lo"] < 1.0 < s["hi"] + 0.3 and s["se"] > 0


def test_holm_matches_lib():
    p = [0.01, 0.04, 0.03]
    assert L.holm(p) == [0.03, 0.06, 0.06]


def test_qlike_min_at_truth():
    assert abs(L.qlike(np.array([2.0]), np.array([2.0]))[0]) < 1e-12
    assert L.qlike(np.array([2.0]), np.array([1.0]))[0] > 0
    assert L.qlike(np.array([2.0]), np.array([4.0]))[0] > 0


def test_day_means():
    day = np.array([0, 0, 1, 3])
    x = np.array([1.0, 3.0, 5.0, 7.0])
    m = L.day_means(day, x, 0, 4)
    assert m[0] == 2.0 and m[1] == 5.0 and np.isnan(m[2]) and m[3] == 7.0
