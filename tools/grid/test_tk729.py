"""Тесты TK-729: синтетика ядра (синус → расчётные круги и $; тренд → расчётный убыток выхода за край), наклон по запасу, фильтр режима, отбор по автокорреляции, объём-нарастание, маркетабельный вход."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402

TICK = 0.001
MK, TK, SL = L.MAKER, L.TAKER, L.SLIP


def mk(c, ndays):
    """Свечи из ряда закрытий: открытие = прошлое закрытие, h/l — по ним (без хвостов)."""
    c = np.asarray(c, float)
    o = np.concatenate([[c[0]], c[:-1]])
    n = ndays * 1440
    assert len(c) == n
    return dict(o=o, h=np.maximum(o, c), l=np.minimum(o, c), c=c, qv=np.ones(n), pres=np.ones(n, bool))


def feats(ndays, rv=0.01, tick=TICK):
    n = ndays * 1440
    return dict(rv60=np.full(n, rv), tick=np.full(ndays, tick))


def run(S, ndays, P, F=None, **kw):
    return L.run_coin(S, F or feats(ndays), P, **kw)


def fl(path, n):
    """Путь, продлённый константой до n минут."""
    path = np.asarray(path, float)
    return np.concatenate([path, np.full(n - len(path), path[-1])])


# ───────────── синус (контроль C0): круги и $ ─────────────

def test_sine_cycles_and_usd():
    nd, T, A, g = 2, 720, 3.5, 0.01
    t = np.arange(nd * 1440)
    c = np.round(100 + A * np.sin(2 * np.pi * t / T), 3)
    S = mk(c, nd)
    P = L.make_params(g0=g, day0=0, t_start=0)
    out = run(S, nd, P)
    # 4 периода по 6 кругов (3 шорта: 101/102/103 → 100/101/102, 3 лонга: 99/98/97 → 98/99/100) минус незакрытый лонг-слот 1 на последней минуте
    assert int(out[L.R_NCYC].sum()) == 4 * 6 - 1
    assert out[L.R_NMKT].sum() == 0 and out[L.R_NEDGE].sum() == 0
    exp = 0.0
    for k in (1, 2, 3):
        lk, lk1 = 100 * (1 - 0.01 * k), 100 * (1 - 0.01 * (k - 1))
        uk, uk1 = 100 * (1 + 0.01 * k), 100 * (1 + 0.01 * (k - 1))
        cyc_long = 25 * (lk1 / lk - 1) - 25 * MK - 25 * (lk1 / lk) * MK
        cyc_short = 25 * (uk - uk1) / uk - 25 * MK - 25 * (uk1 / uk) * MK
        exp += 4 * cyc_short + (4 if k > 1 else 3) * cyc_long  # лонг-слот 1 — 3 полных круга + 1 незакрытый
    q1 = 25 / 99.0
    open_part = -25 * MK + q1 * (c[-1] - 99.0)  # незакрытый лонг-слот 1: вход 99, оценка по последнему закрытию
    assert abs(out[L.R_CYC].sum() - exp) < 1e-9
    assert abs(out[L.R_PNL].sum() - (exp + open_part)) < 1e-9
    assert int(out[L.R_NOPEN].sum()) == 4 * 6


# ───────────── тренд: убыток выхода за край ─────────────

def test_trend_edge_exit_loss():
    nd, g = 2, 0.01
    t = np.arange(502)
    c = fl(np.round(100 - 0.02 * t, 3), nd * 1440)  # первое закрытие < 90: t = 501 → 89.98
    S = mk(c, nd)
    out = run(S, nd, L.make_params(g0=g, day0=0, t_start=0))
    ce = 89.98
    px = ce * (1 - SL)
    exp = 0.0
    for k in range(1, 11):
        lk = 100 * (1 - 0.01 * k)
        q = 25 / lk
        exp += q * (px - lk) - 25 * MK - q * px * TK
    assert int(out[L.R_NEDGE].sum()) == 1 and out[L.R_NCYC].sum() == 0
    assert abs(out[L.R_EXIT].sum() - exp) < 1e-9
    assert abs(out[L.R_PNL].sum() - exp) < 1e-9  # открытого нет: итог по суткам = убыток выхода
    assert exp < 0


def test_trend_up_short_edge_exit():
    nd, g = 2, 0.01
    t = np.arange(502)
    c = fl(np.round(100 + 0.02 * t, 3), nd * 1440)  # первое закрытие > 110: 110.02
    out = run(mk(c, nd), nd, L.make_params(g0=g, day0=0, t_start=0))
    px = 110.02 * (1 + SL)
    exp = 0.0
    for k in range(1, 11):
        uk = 100 * (1 + 0.01 * k)
        q = 25 / uk
        exp += q * (uk - px) - 25 * MK - q * px * TK
    assert int(out[L.R_NEDGE].sum()) == 1
    assert abs(out[L.R_EXIT].sum() - exp) < 1e-9


def test_volume_ramp_sums_250_and_weights():
    nd = 2
    t = np.arange(502)
    c = fl(np.round(100 - 0.02 * t, 3), nd * 1440)
    b = 0.5
    out = run(mk(c, nd), nd, L.make_params(g0=0.01, b=b, day0=0, t_start=0))
    w = np.array([1 + b * (k - 1) for k in range(1, 11)])
    nk = 250 * w / w.sum()
    assert abs(nk.sum() - 250) < 1e-12 and abs(nk[9] / nk[0] - 5.5) < 1e-12
    px = 89.98 * (1 - SL)
    exp = 0.0
    for k in range(1, 11):
        lk = 100 * (1 - 0.01 * k)
        q = nk[k - 1] / lk
        exp += q * (px - lk) - nk[k - 1] * MK - q * px * TK
    assert abs(out[L.R_EXIT].sum() - exp) < 1e-9


# ───────────── наклон по запасу ─────────────

def _skew_path():
    down = np.linspace(100, 98.5, 300)
    up = np.linspace(98.5, 99.8, 300)
    return fl(np.round(np.concatenate([down, up]), 3), 2 * 1440)


def test_inventory_skew_brings_exit_closer():
    nd = 2
    S = mk(_skew_path(), nd)
    cen = np.full(nd * 1440, 100.0)
    F = feats(nd, rv=0.01)  # a = 1 → g = 1,0 (1 % от 100)
    base = dict(a=1.0, use_cen=1, day0=0, t_start=0)
    # с наклоном q·g/2: после входа на 99 центр → 99,5, выход слота 1 на 99,5 достижим (макс. 99,8) → 1 круг; слот 2 на 97,5 не достигнут
    o1 = L.run_coin(S, F, L.make_params(**base, skew_coef=0.5), cen=cen)
    assert int(o1[L.R_NOPEN].sum()) == 1 and int(o1[L.R_NCYC].sum()) == 1
    q = 25 / 99.0
    exp = q * (99.5 - 99.0) - 25 * MK - q * 99.5 * MK
    assert abs(o1[L.R_CYC].sum() - exp) < 1e-9
    # без наклона выход на 100 не достигнут → круга нет, запас остаётся
    o0 = L.run_coin(S, F, L.make_params(**base, skew_coef=0.0), cen=cen)
    assert int(o0[L.R_NCYC].sum()) == 0 and int(o0[L.R_NOPEN].sum()) == 1


def test_skew_pushes_add_level_further():
    nd = 2
    # цена доходит до 97,8: без наклона слот 2 (98) заполнится, с наклоном (слот 2 на 97,5) — нет
    c = fl(np.round(np.linspace(100, 97.8, 400), 3), nd * 1440)
    S = mk(c, nd)
    cen = np.full(nd * 1440, 100.0)
    F = feats(nd)
    base = dict(a=1.0, use_cen=1, day0=0, t_start=0)
    n_skew = L.run_coin(S, F, L.make_params(**base, skew_coef=0.5), cen=cen)[L.R_NOPEN].sum()
    n_flat = L.run_coin(S, F, L.make_params(**base, skew_coef=0.0), cen=cen)[L.R_NOPEN].sum()
    assert n_skew == 1 and n_flat == 2


def test_skew_array_mode_matches_coef_mode():
    nd = 2
    S = mk(_skew_path(), nd)
    cen = np.full(nd * 1440, 100.0)
    F = feats(nd)
    a = L.run_coin(S, F, L.make_params(a=1.0, use_cen=1, day0=0, t_start=0, skew_coef=0.5), cen=cen)
    b = L.run_coin(S, F, L.make_params(a=1.0, use_cen=1, day0=0, t_start=0, skew_mode=1), cen=cen, skw=np.full(nd * 1440, 0.5))
    assert np.allclose(a, b)


# ───────────── фильтр режима ─────────────

def test_er_known_values():
    n = 1440 * 2
    S = dict(c=np.cumsum(np.ones(n)) + 100, pres=np.ones(n, bool))
    er = L.er_kaufman(S, 60)
    assert np.isnan(er[59]) and abs(er[60] - 1.0) < 1e-12 and abs(er[-1] - 1.0) < 1e-12  # монотонный ряд: ER = 1
    S2 = dict(c=100 + (np.arange(n) % 2) * 1.0, pres=np.ones(n, bool))  # пила с чётным окном: сдвиг 0 → ER = 0
    er2 = L.er_kaufman(S2, 60)
    assert abs(er2[100]) < 1e-12


def test_allow_mask_blocks_trend_and_keeps_flat():
    nd = 40
    n = nd * 1440
    rng = np.random.default_rng(3)
    steps = rng.normal(0, 0.05, n)
    steps[36 * 1440:] += 0.2  # со дня 36 — сильный дрейф: ER60 ≈ 1
    S = dict(c=100 + np.cumsum(steps), pres=np.ones(n, bool))
    er = L.er_kaufman(S, 60)
    th = L.er_thresholds(er, [1 / 3, 1 / 2], ndays=nd)
    assert np.isnan(th[29]).all() and np.isfinite(th[30]).all()
    allow = L.allow_mask(er, th[:, 0], None, ndays=nd)
    assert allow[37 * 1440: 38 * 1440].mean() < 0.02  # тренд: почти всегда закрыто
    assert 0.2 < allow[31 * 1440: 35 * 1440].mean() < 0.5  # флэт: около трети (q = 1/3)
    assert allow[:30 * 1440].sum() == 0  # разгон — закрыто


def test_allow_zero_blocks_new_entries_but_exits_stand():
    nd = 3
    t = np.arange(nd * 1440)
    c = np.round(100 + 3.5 * np.sin(2 * np.pi * t / 720), 3)
    allow = np.zeros(nd * 1440, np.uint8)
    allow[:1440] = 1  # новые уровни — только в первые сутки
    out = run(mk(c, nd), nd, L.make_params(g0=0.01, day0=0, t_start=0), allow=allow)
    assert out[L.R_NOPEN, 1:].sum() == 0  # входов после суток 0 нет
    assert out[L.R_NOPEN, 0] > 0 and out[L.R_NCYC, 1:].sum() >= 1  # выходы стоят: открытый запас закрывается кругом


# ───────────── отбор по автокорреляции ─────────────

def _closes_from_r5(r5, nd):
    """Минутки: закрытие бара (минута ≡ 4 mod 5) = ряд цен, внутри бара — линейно."""
    nb = nd * 288
    p = 100 * np.cumprod(1 + np.concatenate([[0.0], r5[: nb - 1]]))
    c = np.empty(nd * 1440)
    prev = p[0]
    for j in range(nb):
        c[j * 5: j * 5 + 5] = np.linspace(prev, p[j], 6)[1:]
        prev = p[j]
    return c


def test_autocorr_selects_mean_reverting():
    nd = 35
    rng = np.random.default_rng(5)
    nb = nd * 288
    res = {}
    for name, rho in (("mr", -0.5), ("tr", 0.5)):
        e = rng.normal(0, 0.002, nb)
        x = np.zeros(nb)
        for j in range(1, nb):
            x[j] = rho * x[j - 1] + e[j]
        c = _closes_from_r5(x, nd)
        res[name] = L.autocorr5_days(dict(c=c, pres=np.ones(nd * 1440, bool)), ndays=nd)
    assert np.isnan(res["mr"][29]) and abs(res["mr"][31] - (-0.5)) < 0.1 and abs(res["tr"][31] - 0.5) < 0.1
    assert L.sel_days(res["mr"])[31] == 1 and L.sel_days(res["tr"])[31] == 0
    assert L.sel_days(res["mr"])[10] == 0  # NaN (нет окна) → не выбрана


# ───────────── вход выше рынка = тейкер по открытию ─────────────

def test_marketable_entry_is_taker_at_open():
    nd = 2
    c = np.full(nd * 1440, 98.0)
    out = run(mk(c, nd), nd, L.make_params(a=1.0, use_cen=1, day0=0, t_start=0, skew_coef=0.0), cen=np.full(nd * 1440, 100.0))
    # рынок 98: вход-покупка слота 1 на 99 выставлена выше рынка → тейкер по открытию 98·(1+проскальзывание)
    assert int(out[L.R_NMKT].sum()) == 1 and int(out[L.R_NOPEN].sum()) == 1
    p = 98.0 * (1 + SL)
    exp = -25 * TK + (25 / p) * (98.0 - p)
    assert abs(out[L.R_PNL].sum() - exp) < 1e-9


# ───────────── утилиты ─────────────

def test_drawdown_and_high_to_high():
    r = L.drawdown([1, 1, -1, -1, -1, 1, 1, 2, 1])
    assert abs(r["dd"] - 3) < 1e-12
    # накопленное: 1,2,1,0,-1,0,1,3,4 → новые максимумы на днях 0,1,7,8; интервалы 1,1,6,1
    assert r["hw_max"] == 6 and abs(r["hw_mean"] - 2.25) < 1e-12


def test_stat_block_is_seeded():
    x = np.random.default_rng(1).normal(1.0, 3.0, 300)
    a = L.stat_block(x, 7, 2000)
    b = L.stat_block(x, 7, 2000)
    assert a == b and a["lo"] < a["mean"] < a["hi"]


def test_tick_days_gcd():
    n = 10 * 1440
    p = np.round(np.random.default_rng(2).uniform(10, 20, n), 2)
    S = dict(o=p, h=p, l=p, c=p, pres=np.ones(n, bool))
    t = L.tick_days(S, ndays=10)
    assert abs(t[5] - 0.01) < 1e-9 and abs(t[0] - 0.01) < 1e-9
