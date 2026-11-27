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




# ───────────── §3: OU, условие Дики–Фуллера, Калман, шок-фильтры, объёмы, шаг из массива ─────────────

def _ou_path(n, b, se, mu=4.6, seed=11):
    rng = np.random.default_rng(seed)
    x = np.empty(n)
    x[0] = mu
    e = rng.normal(0, se, n)
    for t in range(1, n):
        x[t] = mu + b * (x[t - 1] - mu) + e[t]
    return x


def test_ou_recovers_known_hl_and_sigma_eq():
    n, b_true, se = 8 * 1440, 0.99, 0.0005  # HL = −ln2/ln0,99 ≈ 69 мин; σ_eq = se/√(1−b²) ≈ 0.00354
    x = _ou_path(n, b_true, se)
    r = L.ou_rolling(dict(c=np.exp(x), pres=np.ones(n, bool)), win=5 * 1440)
    assert np.isnan(r["mu"][5 * 1440 - 2]) and np.isfinite(r["mu"][-1])
    hl_true = -np.log(2) / np.log(b_true)
    sig_true = se / np.sqrt(1 - b_true ** 2)
    assert abs(r["hl"][-1] / hl_true - 1) < 0.15
    assert abs(r["sig_eq"][-1] / sig_true - 1) < 0.1
    assert abs(np.log(r["mu"][-1]) - 4.6) < 2 * sig_true
    assert abs(r["sig_eps"][-1] / se - 1) < 0.05


def test_ou_known_hl_at_1440_window_and_df_condition():
    n = 10 * 1440
    x = _ou_path(n, 0.97, 0.0004, seed=5)  # HL ≈ 22,8 мин
    r = L.ou_rolling(dict(c=np.exp(x), pres=np.ones(n, bool)), 1440)
    ok = (r["tstat"] < -2.86) & (r["hl"] >= 5) & (r["hl"] <= 60)
    assert ok[2 * 1440:].mean() > 0.95  # возврат сильный: условие почти всегда выполнено
    hl = -np.log(2) / np.log(0.97)
    assert abs(np.nanmedian(r["hl"][2 * 1440:]) / hl - 1) < 0.15
    rng = np.random.default_rng(6)
    w = 4.6 + np.cumsum(rng.normal(0, 0.0004, n))  # блуждание: условие — редкость (≈ 5 %)
    rw = L.ou_rolling(dict(c=np.exp(w), pres=np.ones(n, bool)), 1440)
    okw = (rw["tstat"] < -2.86) & (rw["hl"] >= 5) & (rw["hl"] <= 120)
    assert okw[2 * 1440:].mean() < 0.15


def test_hold_every_15_uses_last_update():
    a = np.arange(60, dtype=float)
    h = L.hold_every(a, 15)
    assert h[14] == 0 and h[15] == 15 and h[29] == 15 and h[30] == 30


def test_kalman_step_change_matches_steady_state_gain():
    n = 6000
    R, Q = 1e-6, 1e-9
    P = (Q + np.sqrt(Q * Q + 4 * Q * R)) / 2  # априорная дисперсия установившегося режима
    K = P / (P + R)
    y = np.full(n, 4.6)
    y[3000:] += 0.01  # ступенька уровня на 1 %
    m = L.kalman_center(y, np.ones(n, bool), np.full(n, R), np.full(n, Q))
    assert abs(m[2999] - 4.6) < 1e-3  # до ступеньки центр на уровне
    for j in (10, 50, 100):
        assert abs((m[3000 + j] - 4.6) / 0.01 - (1 - (1 - K) ** (j + 1))) < 5e-3  # отклик на ступеньку: 1 − (1 − K)^n
    assert abs(m[-1] - 4.61) < 1e-4  # сошёлся на новый уровень
    # пропуск минут: центр держится, неопределённость растёт (не падает), после пропуска центр снова ловит цену
    pres = np.ones(n, bool)
    pres[1000:1100] = False
    m2 = L.kalman_center(y, pres, np.full(n, R), np.full(n, Q))
    assert abs(m2[1050] - m2[999]) < 1e-12 and abs(m2[-1] - 4.61) < 1e-4


def test_kalman_q_matches_explicit_loop():
    rng = np.random.default_rng(8)
    n = 3000
    c = np.exp(4.6 + np.cumsum(rng.normal(0, 0.001, n)))
    S = dict(c=c, pres=np.ones(n, bool))
    q = L.kalman_q(S, win=600, step=60)
    t = 2500
    x = np.log(c)
    m = np.array([x[max(0, i - 59): i + 1].mean() if i >= 59 else np.nan for i in range(n)])
    d = [(m[i] - m[i - 60]) / 60 for i in range(t - 599, t + 1) if i >= 60 and np.isfinite(m[i]) and np.isfinite(m[i - 60])]
    assert abs(q[t] / np.var(d) - 1) < 1e-6


def test_btc_shock_blocks_30_minutes_after_last_window():
    n = 3 * 1440
    rng = np.random.default_rng(2)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0003, n)))
    c[2000:] *= 1.02  # скачок на 2 % за минуту 2000 (при σ_мин 0,03 %)
    Sb = dict(c=c, pres=np.ones(n, bool))
    blk = L.btc_block(Sb, L.rv_sqrt_sum(Sb, 60))
    # скачок на минуте 2000 лежит в пяти 5-минутных окнах (минуты 2000…2004): шок на каждой, блок [2000, 2004 + 30)
    assert blk[2000] and blk[2033] and not blk[2034] and not blk[1999]
    assert blk[:1900].sum() < 30  # без скачка — почти не срабатывает


def test_turnover_shock_blocks_30_minutes_after_last_window():
    nd = 30
    n = nd * 1440
    rng = np.random.default_rng(4)
    qv = rng.uniform(900, 1100, n)
    qv[25 * 1440 + 600] = 60000  # всплеск оборота на минуте 10:00 суток 25 (в 5-минутке: 60 тыс. против ≈ 5 тыс.)
    S = dict(qv=qv, pres=np.ones(n, bool))
    blk = L.turnover_block(S)
    t0 = 25 * 1440 + 600
    assert blk[t0] and blk[t0 + 33] and not blk[t0 + 34]  # всплеск лежит в пяти 5-минутных окнах (t0…t0+4)
    assert not blk[t0 - 1] and blk[: 20 * 1440].sum() == 0  # первые 20 суток — разгон, базы нет
    assert blk[20 * 1440: 25 * 1440].sum() == 0


def test_decreasing_volume_levels():
    nd = 2
    t = np.arange(502)
    c = fl(np.round(100 - 0.02 * t, 3), nd * 1440)
    out = run(mk(c, nd), nd, L.make_params(g0=0.01, vmode=1, dec=0.05, day0=0, t_start=0))
    px = 89.98 * (1 - SL)
    exp = 0.0
    tot_n = 0.0
    for k in range(1, 11):
        lk = 100 * (1 - 0.01 * k)
        nk = 25 * (1 - 0.05 * (k - 1))
        tot_n += nk
        exp += (nk / lk) * (px - lk) - nk * MK - (nk / lk) * px * TK
    assert abs(tot_n - 193.75) < 1e-9
    assert abs(out[L.R_EXIT].sum() - exp) < 1e-9


def test_step_from_array_equals_fixed_step_grid():
    nd = 2
    t = np.arange(nd * 1440)
    c = np.round(100 + 3.5 * np.sin(2 * np.pi * t / 720), 3)
    S = mk(c, nd)
    base = run(S, nd, L.make_params(g0=0.01, day0=0, t_start=0))
    cen = np.full(nd * 1440, 100.0)
    alt = L.run_coin(S, feats(nd), L.make_params(use_cen=1, gmode=1, skew_coef=0.0, day0=0, t_start=0), cen=cen, gfr=np.full(nd * 1440, 0.01))
    assert np.allclose(base, alt)


def test_conditions_allow_logic():
    import tk729_cfg as C
    n = 6
    F = dict(tstat=np.array([-3.5, -3.5, -2.0, -3.5, -3.5, np.nan]), hl=np.array([30.0, 90.0, 30.0, 30.0, 30.0, np.nan]),
             rv60=np.array([0.001, 0.001, 0.001, 0.02, 0.001, 0.001]), sig=np.array([0.003] * 5 + [np.nan]), sig_or_rv=np.array([0.003] * 5 + [0.001]),
             shock=np.array([False, False, False, False, True, False]), kal=np.full(n, 100.0), mu=np.full(n, 100.0))
    full = [v for v in C.base_variants() if v["name"] == "MR_c1_h60"][0]
    _, allow, gfr = C.build_components(F, full)
    assert allow.tolist() == [1, 0, 0, 0, 0, 0]  # HL 90 > 60; t −2,0 не значим; срыв (RV60 0,02 > 3·0,003); шок; NaN
    assert abs(gfr[0] - 0.003) < 1e-12  # c = 1: g = σ_eq = 0,3 % ≥ пола 0,15 %
    F2 = dict(F, sig=np.array([0.0005] * 6))
    assert C.build_components(F2, full)[2][0] == C.GMIN  # σ_eq мал → пол 0,15 %
    abd = [v for v in C.ablation_variants(1.0, 60) if v["kind"] == "ABd"][0]
    _, al_d, _ = C.build_components(F, abd)
    assert al_d.tolist() == [1, 1, 1, 0, 0, 1]  # условие п. 1 снято (HL 90, t −2,0, NaN-σ → RV60 вместо σ_eq); срыв и шок остались
    abe = [v for v in C.ablation_variants(1.0, 60) if v["kind"] == "ABe"][0]
    _, al_e, _ = C.build_components(F, abe)
    assert al_e.tolist() == [1, 0, 0, 1, 1, 0]  # шок и срыв сняты, условие п. 1 осталось
