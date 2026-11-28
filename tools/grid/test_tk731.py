"""Тесты TK-731 (П-TK729 §8, «Проверка метода»): синтетика ядра `tk731_lib.rr_run` до полного прогона.

Тренд → AB прибыль / C0 зеркальный убыток; пила 0↔1 → AB убыток на цикл, C0 круг; ES(k) на тренде — убыток; гэп через стоп → по открытию; стоп, пройденный ценой при выставлении → по открытию следующей минуты;
два пути минуты → худший; HY: переключение за k-м уровнем и возврат; C0 = ES(10) совпадает со старым ядром `tk729_lib.grid_run`; Σ циклов половины = Σ P&L половины.
Запуск: `python -m pytest tools/grid/test_tk731.py` или `python tools/grid/test_tk731.py` (на srv без pytest).
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402
import tk731_lib as W  # noqa: E402

TICK = 0.001
MK, TK, SL = L.MAKER, L.TAKER, L.SLIP
NOM = 25.0


def mk(c, ndays):
    """Свечи из ряда закрытий: открытие = прошлое закрытие, h/l — по ним (без хвостов)."""
    c = np.asarray(c, float)
    o = np.concatenate([[c[0]], c[:-1]])
    n = ndays * 1440
    assert len(c) == n
    return dict(o=o, h=np.maximum(o, c), l=np.minimum(o, c), c=c, qv=np.ones(n), pres=np.ones(n, bool))


def fl(path, n):
    path = np.asarray(path, float)
    return np.concatenate([path, np.full(n - len(path), path[-1])])


def tickd(ndays, tick=TICK):
    return np.full(ndays, tick)


def run(S, ndays, **kw):
    cut = kw.pop("cutd", ())
    P = W.make_params(day0=0, t_start=0, **kw)
    out, hn, hs, dbg = W.run_rr(S, tickd(ndays), P, cutd=cut)
    assert dbg[0] == 0
    return out, hn, hs


def lv(p0, g, k, up=True):
    """Уровень, как у ядра: _rt(p0 ± k·g·, tick); g — абсолютный шаг."""
    return L._rt(p0 + k * g if up else p0 - k * g, TICK)


# ───────────── тренд вверх: AB выигрывает, C0 теряет зеркально ─────────────

def trend_up(nd=2, n=252):
    """Рост 0,02 за минуту от 100: закрытие минуты 251 = 105,02 — первое строго за 10-м уровнем (105,0); дальше цена стоит."""
    t = np.arange(n)
    return mk(fl(np.round(100 + 0.02 * t, 3), nd * 1440), nd)


def test_trend_up_ab_profit_and_c0_mirror_loss():
    nd = 2
    S = trend_up(nd)
    c = S["c"]
    g = 0.5  # 0,5 % от 100
    ce = c[np.argmax(c > 105.0)]  # первое закрытие строго за 10-м уровнем
    out, _, _ = run(S, nd, mode=W.M_AB, g_ab=0.005)
    px = ce * (1 - SL)
    exp = 0.0
    for k in range(1, 11):
        p = lv(100.0, g, k) * (1 + SL)
        q = NOM / p
        exp += q * (px - p) - NOM * TK - q * px * TK
    assert int(out[W.R_EAN].sum()) == 1 and int(out[W.R_STN].sum()) == 0
    assert abs(out[W.R_EAU].sum() - exp) < 1e-9
    assert exp > 0
    assert int(out[W.R_CN].sum()) == 1 and int(out[W.R_CW].sum()) == 1 and abs(out[W.R_CWS].sum() - exp) < 1e-9
    assert int(out[W.R_ENT].sum()) == 10
    # C0 (= ES(10)): 10 шортов лимитами на уровнях выше, выход за край по рынку — зеркальный убыток
    out0, _, _ = run(S, nd, mode=W.M_GRID, kgrid=10)
    px0 = ce * (1 + SL)
    exp0 = 0.0
    for k in range(1, 11):
        u = lv(100.0, g, k)
        q = NOM / u
        exp0 += q * (u - px0) - NOM * MK - q * px0 * TK
    assert int(out0[W.R_EGN].sum()) == 1
    assert abs(out0[W.R_EGU].sum() - exp0) < 1e-9
    assert exp0 < 0 and int(out0[W.R_CN].sum()) == 1 and int(out0[W.R_CW].sum()) == 0


def test_trend_down_ab_short_profit_and_c0_mirror_loss():
    nd = 2
    full = np.round(100 - 0.02 * np.arange(400), 3)
    ie = int(np.argmax(full < 95.0))
    S = mk(fl(full[: ie + 1], nd * 1440), nd)
    ce = S["c"][ie]
    out, _, _ = run(S, nd, mode=W.M_AB, g_ab=0.005)
    px = ce * (1 + SL)
    exp = 0.0
    for k in range(1, 11):
        p = lv(100.0, 0.5, k, up=False) * (1 - SL)
        q = NOM / p
        exp += q * (p - px) - NOM * TK - q * px * TK
    assert int(out[W.R_EAN].sum()) == 1 and abs(out[W.R_EAU].sum() - exp) < 1e-9 and exp > 0
    out0, _, _ = run(S, nd, mode=W.M_GRID, kgrid=10)
    px0 = ce * (1 - SL)
    exp0 = sum((NOM / lv(100.0, 0.5, k, up=False)) * (px0 - lv(100.0, 0.5, k, up=False)) - NOM * MK - NOM * px0 / lv(100.0, 0.5, k, up=False) * TK for k in range(1, 11))
    assert abs(out0[W.R_EGU].sum() - exp0) < 1e-9 and exp0 < 0


def test_es_trend_loss_k3_and_k2():
    nd = 2
    for k in (2, 3):
        edge = 100.0 - 0.5 * k
        full = np.round(100 - 0.02 * np.arange(502), 3)
        ie = int(np.argmax(full < edge))
        S = mk(fl(full[: ie + 1], nd * 1440), nd)  # после выхода за край цена стоит
        c = S["c"]
        ce = c[ie]
        out, _, _ = run(S, nd, mode=W.M_GRID, kgrid=k)
        px = ce * (1 - SL)
        exp = 0.0
        for j in range(1, k + 1):
            d = lv(100.0, 0.5, j, up=False)
            q = NOM / d
            exp += q * (px - d) - NOM * MK - q * px * TK
        assert int(out[W.R_EGN].sum()) == 1 and int(out[W.R_LPN].sum()) == 0
        assert abs(out[W.R_EGU].sum() - exp) < 1e-9
        assert abs(out[W.R_PNL].sum() - exp) < 1e-9


# ───────────── пила между P0 и уровнем 1 ─────────────

def saw_path(nper=5):
    path = [100.0]
    for _ in range(nper):
        path += [round(100.0 + 0.05 * i, 3) for i in range(1, 13)]  # до 100,6
        path += [round(100.6 - 0.05 * i, 3) for i in range(1, 15)]  # до 99,9
        path += [round(99.9 + 0.05 * i, 3) for i in range(1, 15)]  # до 100,6
        path += [round(100.6 - 0.05 * i, 3) for i in range(1, 15)]  # до 99,9
    return path


def test_saw_ab_loses_per_cycle_c0_earns_lap():
    nd = 2
    path = saw_path(5)
    S = mk(fl(path, nd * 1440), nd)
    out, _, _ = run(S, nd, mode=W.M_AB, g_ab=0.005)
    # питон-модель AB: вход при касании 100,5 (не в минуту закрытия части), стоп при касании 100,0
    inpos, n_cyc, last_close = False, 0, -1
    for i, x in enumerate(path):
        if i == 0:
            continue
        if inpos and x <= 100.0:
            inpos = False
            n_cyc += 1
            last_close = i
        elif (not inpos) and x >= 100.5 and last_close != i:
            inpos = True
    p1 = 100.5 * (1 + SL)
    q = NOM / p1
    px = 100.0 * (1 - SL)
    cyc = q * (px - p1) - NOM * TK - q * px * TK
    assert n_cyc >= 8
    assert int(out[W.R_CN].sum()) + (1 if inpos else 0) >= n_cyc and int(out[W.R_CW].sum()) == 0
    assert int(out[W.R_STN].sum()) == n_cyc
    assert abs(out[W.R_STU].sum() - n_cyc * cyc) < 1e-9
    assert cyc < 0  # убыток на цикл ≈ g + издержки
    assert abs(cyc / NOM + 0.005 + 2 * (TK + SL)) < 3e-4
    out0, _, _ = run(S, nd, mode=W.M_GRID, kgrid=10)
    lap = NOM / 100.5 * (100.5 - 100.0) - NOM * MK - NOM / 100.5 * 100.0 * MK
    assert int(out0[W.R_LPN].sum()) >= 4
    assert abs(out0[W.R_LPU].sum() / out0[W.R_LPN].sum() - lap) < 1e-9
    assert lap > 0


# ───────────── гэп и стопы за ценой ─────────────

def hand(cs, nd=2):
    """Свечи по списку (o, h, l, c); на остальные минуты — плоская свеча на последнем закрытии."""
    n = nd * 1440
    a = np.array(cs, float)
    o, h, l, c = (np.concatenate([a[:, i], np.full(n - len(a), a[-1, 3])]) for i in range(4))
    return dict(o=o, h=h, l=l, c=c, qv=np.ones(n), pres=np.ones(n, bool))


def test_gap_through_stop_fills_at_open():
    # минута 0 — запуск P0 = 100; минута 1: вход лонга 1 на 100,5; минута 3: гэп вниз — открытие 99,8 ниже стопа 100,0 (но выше входа шорта 99,5)
    S = hand([(100, 100, 100, 100), (100.2, 100.5, 100.2, 100.5), (100.5, 100.5, 100.4, 100.4), (99.8, 99.8, 99.8, 99.8)])
    out, _, _ = run(S, 2, mode=W.M_AB, g_ab=0.005)
    p1 = 100.5 * (1 + SL)
    q = NOM / p1
    px = 99.8 * (1 - SL)  # худшее из уровня 100,0 и открытия 99,8
    exp = q * (px - p1) - NOM * TK - q * px * TK
    assert int(out[W.R_STN].sum()) == 1 and int(out[W.R_GAP].sum()) == 1
    assert abs(out[W.R_STU].sum() - exp) < 1e-9


def test_gap_through_entries_fills_at_open():
    # гэп вверх: открытие 101,2 выше уровней 100,5 и 101,0 → оба входа по открытию
    S = hand([(100, 100, 100, 100), (101.2, 101.3, 101.2, 101.3)])
    out, _, _ = run(S, 2, mode=W.M_AB, g_ab=0.005)
    assert int(out[W.R_ENT].sum()) == 2 and int(out[W.R_GAP].sum()) == 2
    # P&L: две части вошли по 101,2·(1+slip); на конец — оценка по закрытию 101,3
    p = 101.2 * (1 + SL)
    q = NOM / p
    exp = 2 * (-NOM * TK + q * (101.3 - p))
    assert abs(out[W.R_PNL].sum() - exp) < 1e-9


def test_reentry_stop_passed_at_placement_fills_next_open():
    # минута 1: вход лонга 1 на 100,5; минута 2: падение до стопа 100,0 и возврат на 100,8 в той же минуте (перевход не раньше следующей минуты);
    # минута 3: открытие 100,8 выше уровня входа 100,5 → вход по открытию (+1 bp, тейкер)
    S = hand([(100, 100, 100, 100), (100.2, 100.6, 100.2, 100.6), (100.6, 100.8, 99.95, 100.8), (100.8, 100.9, 100.8, 100.9)])
    out, _, _ = run(S, 2, mode=W.M_AB, g_ab=0.005)
    assert int(out[W.R_ENT].sum()) == 2 and int(out[W.R_STN].sum()) == 1
    assert int(out[W.R_GAP].sum()) == 1  # второй вход — по открытию
    p2 = 100.8 * (1 + SL)
    q2 = NOM / p2
    p1 = 100.5 * (1 + SL)
    q1 = NOM / p1
    px1 = 100.0 * (1 - SL)
    st1 = q1 * (px1 - p1) - NOM * TK - q1 * px1 * TK
    open2 = -NOM * TK + q2 * (100.9 - p2)
    assert abs(out[W.R_PNL].sum() - (st1 + open2)) < 1e-9


# ───────────── два пути минуты — берётся худший ─────────────

def test_two_paths_take_worse():
    """Лонг 1 открыт (вход 100,5, стоп 100,0). Минута: o 100,2, h 100,6, l 99,4, c = 99,6. Путь A (O→H→L→C): стоп лонга на 100,0, вход шорта на 99,5 — шорт остаётся;
    путь B (O→L→H→C): то же, но при подъёме к 100,6 шорт выбит стопом 100,0 — хуже; берётся B."""
    cl = 99.6
    S = hand([(100, 100, 100, 100), (100.2, 100.5, 100.2, 100.5), (100.2, 100.6, 99.4, cl)])
    out, _, _ = run(S, 2, mode=W.M_AB, g_ab=0.005)
    p1 = 100.5 * (1 + SL)
    q1 = NOM / p1
    px1 = 100.0 * (1 - SL)
    ps = 99.5 * (1 - SL)
    qs = NOM / ps
    pb = 100.0 * (1 + SL)
    long_loss = q1 * (px1 - p1) - NOM * TK - q1 * px1 * TK  # включая комиссию входа
    short_stop = qs * (ps - pb) - NOM * TK - qs * pb * TK
    b_total = long_loss + short_stop
    a_total = long_loss - NOM * TK + qs * (ps - cl)
    assert a_total > b_total
    assert abs(out[W.R_PNL].sum() - b_total) < 1e-9
    assert int(out[W.R_STN].sum()) == 2


def test_two_paths_equals_min_of_forced_paths_random():
    rng = np.random.default_rng(7)
    n_diff = 0
    for _ in range(300):
        lo = rng.uniform(99.0, 99.9)
        hi = rng.uniform(100.0, 101.3)
        op = rng.uniform(lo, hi)
        cl = rng.uniform(99.51, 99.99)
        lo, hi = min(lo, op, cl), max(hi, op, cl)
        lo, hi, op, cl = (round(x, 3) for x in (lo, hi, op, cl))
        S = hand([(100, 100, 100, 100), (100.2, 100.5, 100.2, 100.5), (op, hi, lo, cl)])
        r = [run(S, 2, mode=W.M_AB, g_ab=0.005, force=f)[0][W.R_PNL].sum() for f in (0, 1, 2)]
        assert abs(r[0] - min(r[1], r[2])) < 1e-12, (op, hi, lo, cl, r)
        n_diff += abs(r[1] - r[2]) > 1e-9
    assert n_diff >= 5, n_diff  # пути действительно различаются в части сценариев


# ───────────── HY: переключение за k-м уровнем и возврат ─────────────

def test_hy_switches_beyond_k_and_returns_to_grid_after_ab_edge():
    nd = 2
    full = np.round(100 + 0.02 * np.arange(600), 3)
    c1 = full[int(np.argmax(full > 101.5))]
    gab = max(0.005 * c1, 2 * TICK)
    ie = int(np.argmax(full > c1 + 10 * gab))
    S = mk(fl(full[: ie + 1], nd * 1440), nd)  # после края AB цена стоит
    c = S["c"]
    out, _, _ = run(S, nd, mode=W.M_HY, kgrid=3, g_grid=0.005, g_ab=0.005)
    # фаза сетки: 3 шорта на 100,5 / 101,0 / 101,5; закрытие > 101,5 → выход по рынку; пробой вверх → AB от этого закрытия
    px = c1 * (1 + SL)
    exp_edge = 0.0
    for j in range(1, 4):
        u = lv(100.0, 0.5, j)
        q = NOM / u
        exp_edge += q * (u - px) - NOM * MK - q * px * TK
    assert int(out[W.R_EGN].sum()) == 1 and abs(out[W.R_EGU].sum() - exp_edge) < 1e-9
    assert int(out[W.R_G2A].sum()) == 1
    # фаза AB: лонги стоп-входами на P0_ab + k·g; край — закрытие > P0_ab + 10·g
    ce = c[ie]
    px2 = ce * (1 - SL)
    exp_ab = 0.0
    for k in range(1, 11):
        p = lv(c1, gab, k) * (1 + SL)
        q = NOM / p
        exp_ab += q * (px2 - p) - NOM * TK - q * px2 * TK
    assert int(out[W.R_EAN].sum()) == 1 and abs(out[W.R_EAU].sum() - exp_ab) < 1e-9
    assert int(out[W.R_A2G].sum()) == 1  # после края AB — снова сетка
    assert int(out[W.R_STN].sum()) == 0
    # после перезапуска сетки (от закрытия края) — фаза сетки идёт: минуты в фазе сетки есть и после AB
    assert out[W.R_MAB].sum() > 100 and out[W.R_MGR].sum() > 2000
    # циклов: сетка-цикл (убыток), AB-цикл (прибыль)
    assert int(out[W.R_CN].sum()) == 2 and int(out[W.R_CW].sum()) == 1


def test_hy_returns_to_grid_when_flat_and_price_closes_other_side():
    nd = 2
    up = np.round(100 + 0.02 * np.arange(0, 77), 3)  # до 101,52 (закрытие > 101,5 на минуте 76)
    down = np.round(101.52 - 0.02 * np.arange(1, 2), 3)  # откат на 101,50 — ниже начала AB-фазы 101,52; дальше цена стоит
    S = mk(fl(np.concatenate([up, down]), nd * 1440), nd)
    out, _, _ = run(S, nd, mode=W.M_HY, kgrid=3, g_grid=0.005, g_ab=0.005)
    assert int(out[W.R_G2A].sum()) == 1 and int(out[W.R_A2G].sum()) == 1
    assert int(out[W.R_EGN].sum()) == 1 and int(out[W.R_EAN].sum()) == 0 and int(out[W.R_ENT].sum()) == 3
    assert out[W.R_MAB].sum() == 1  # одна минута в фазе AB: уже на следующем закрытии (101,50 < 101,52) запас 0 и цена ниже начала → сетка


# ───────────── C0 = ES(10) совпадает со старым ядром ─────────────

def rand_candles(nd, seed, vol=0.0004):
    rng = np.random.default_rng(seed)
    n = nd * 1440
    r = rng.standard_normal(n) * vol
    c = np.empty(n)
    o = np.empty(n)
    x = 100.0
    for i in range(n):
        o[i] = x
        x = x * (1 + r[i])
        c[i] = x
    h = np.maximum(o, c) * (1 + np.abs(rng.standard_normal(n)) * vol * 0.6)
    l = np.minimum(o, c) * (1 - np.abs(rng.standard_normal(n)) * vol * 0.6)
    q = lambda a: np.round(a / TICK) * TICK
    o, c = q(o), q(c)
    h = np.maximum(q(h), np.maximum(o, c))
    l = np.minimum(q(l), np.minimum(o, c))
    return dict(o=o, h=h, l=l, c=c, qv=np.ones(n), pres=np.ones(n, bool))


def test_c0_equals_old_kernel_on_random_walk():
    nd = 6
    for seed in (1, 2, 3):
        S = rand_candles(nd, seed, vol=0.0007)
        F = dict(rv60=np.full(nd * 1440, 0.01), tick=tickd(nd))
        old = L.run_coin(S, F, L.make_params(g0=0.005, use_cen=0, b=0.0, timex=0, pause=0, t_start=0, day0=0))
        new, _, _ = run(S, nd, mode=W.M_GRID, kgrid=10)
        assert np.allclose(old[L.R_PNL], new[W.R_PNL], atol=1e-8, rtol=0)
        assert np.allclose(old[L.R_CYC], new[W.R_LPU], atol=1e-8, rtol=0)
        assert np.allclose(old[L.R_EXIT], new[W.R_EGU], atol=1e-8, rtol=0)
        assert np.array_equal(old[L.R_NCYC], new[W.R_LPN]) and np.array_equal(old[L.R_NEDGE], new[W.R_EGN])
        assert np.array_equal(old[L.R_NOPEN], new[W.R_ENT]) and np.array_equal(old[L.R_ACT], new[W.R_ACT])
        assert new[W.R_LPN].sum() > 50  # сетка действительно торговала


# ───────────── Σ циклов половины = Σ P&L половины; гистограмма = события ─────────────

def test_cycle_sum_equals_pnl_sum_with_cuts():
    nd = 8
    for mode, kw in ((W.M_GRID, dict(kgrid=3)), (W.M_GRID, dict(kgrid=10)), (W.M_AB, dict(g_ab=0.005)), (W.M_AB, dict(g_ab=0.002)), (W.M_HY, dict(kgrid=3, g_grid=0.005, g_ab=0.005))):
        S = rand_candles(nd, 11, vol=0.0009)
        cut = (1, 4, nd - 1)
        out, hn, hs = run(S, nd, mode=mode, cutd=cut, **kw)
        cyc = out[W.R_CWS] + out[W.R_CLS]
        edges = (0, 2, 5, nd)
        for a, b in zip(edges[:-1], edges[1:]):
            assert abs(cyc[a:b].sum() - out[W.R_PNL, a:b].sum()) < 1e-7, (mode, kw, a, b)
        assert int(out[W.R_CF].sum()) <= 3
        assert out[W.R_ENT].sum() > 20
        # события выходов в гистограммах (половины в тесте не размечены → hn пуст), счётчики строк согласованы с лотами
        assert int(out[W.R_CN].sum()) >= 1


def test_hist_counts_match_events_on_real_halves():
    # сутки отчёта ≥ BURN попадают в половину H1: генерируем 21 сутки, день 0 = day0 = 0 → hf ≠ −1 только при do ≥ 20
    nd = 22
    S = rand_candles(nd, 5, vol=0.0012)
    P = W.make_params(mode=W.M_AB, g_ab=0.005, day0=0, t_start=0)
    out, hn, hs, dbg = W.run_rr(S, tickd(nd), P, cutd=())
    inh = slice(L.BURN, nd)
    assert int(round(hn[1, 0].sum())) == int(out[W.R_STN, inh].sum())
    assert abs(hs[1, 0].sum() - out[W.R_STU, inh].sum()) < 1e-7
    st = W.hist_stats(hn[1, 0], hs[1, 0])
    assert st["n"] == int(out[W.R_STN, inh].sum()) and st["worst"] <= st["mean"] + 1e-12


def test_hist_stats_worst_percent():
    hn = np.zeros(W.NBIN)
    hs = np.zeros(W.NBIN)
    vals = np.concatenate([np.full(99, -0.15), np.array([-5.0])])
    for v in vals:
        b = int(math.floor((v + W.HLO) / W.HW))
        hn[b] += 1
        hs[b] += v
    st = W.hist_stats(hn, hs, 0.01)
    assert abs(st["worst"] + 5.0) < 1e-9 and abs(st["mean"] - vals.mean()) < 1e-9


if __name__ == "__main__":
    import traceback

    fails = 0
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)):
        try:
            fn()
            print("PASS", name)
        except Exception:
            fails += 1
            print("FAIL", name)
            traceback.print_exc()
    print("FAILED" if fails else "ALL PASS", fails)
    sys.exit(1 if fails else 0)
