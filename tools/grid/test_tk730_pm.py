"""TK-730 §7 (PS-1067 / PS-1068 / PS-1069): синтетические проверки слоёв управления позицией — PM1 (равный риск, множитель размера), PM4 (дневной лимит убытка монеты), метрики и правило «слой полезен» — до чтения результатов."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402
import tk730_lib as W  # noqa: E402
import tk730_pm as M  # noqa: E402
from test_tk729 import MK, fl, mk, run  # noqa: E402

# ───────────── PM1: множитель равного риска ─────────────


def test_pm1_mult_equal_risk_and_cap():
    ref = 0.01
    sig = np.array([0.01, 0.02, 0.005, 0.0025, 0.04, np.nan, 0.0, -1.0])
    m = M.pm1_mult(sig, ref)
    assert m[0] == 1.0 and m[1] == pytest.approx(0.5) and m[2] == pytest.approx(2.0)
    assert m[3] == 2.0  # σ в 4 раза меньше опоры → потолок 2
    assert m[4] == pytest.approx(0.25)
    assert m[5] == 1.0 and m[6] == 1.0 and m[7] == 1.0  # σ неизвестна или не положительна → 1
    ok = (sig > 0) & (sig >= ref / 2)
    assert np.allclose((sig * m)[ok], ref)  # σ_eq × объём одинаков у всех монет ниже потолка
    assert (M.pm1_mult(sig, float("nan")) == 1.0).all() and (M.pm1_mult(sig, 0.0) == 1.0).all()


def test_weekly_mult_reference_is_median_of_the_set_only():
    nc = 10
    sig = np.tile(np.arange(1, nc + 1, dtype=float)[:, None] * 1e-3, (1, W.NW))  # σ = 1…10 ‰
    sel = np.zeros((nc, W.NW), bool)
    sel[[0, 2, 4], 3] = True  # неделя 3: корзина σ = 1, 3, 5 ‰ → опора (медиана) 3 ‰
    sel[:, 5] = True  # неделя 5: все → медиана 5,5 ‰
    m = M.weekly_mult(sig, sel)
    assert m[2, 3] == pytest.approx(1.0) and m[0, 3] == 2.0 and m[4, 3] == pytest.approx(0.6) and m[9, 3] == pytest.approx(0.3)
    assert m[5, 5] == pytest.approx(5.5 / 6.0) and m[9, 5] == pytest.approx(0.55)
    assert (m[:, 0] == 1.0).all()  # пустое множество → опоры нет → 1


def test_expand_weekly_minutes_alignment():
    mult = np.arange(1, W.NW + 1, dtype=float)
    n = (L.DAY0 + L.NOUT) * 1440
    e = M.expand_weekly(mult, n)
    a3 = W.week_start_min(3)
    assert e[a3 - 1] == 3.0 and e[a3] == 4.0 and e[a3 + 7 * 1440 - 1] == 4.0 and e[a3 + 7 * 1440] == 5.0
    assert e[W.week_start_min(0) - 1] == 1.0 and e[W.week_start_min(0)] == 1.0 + 0.0 and e[0] == 1.0 and e[W.week_start_min(W.NW - 1) + 7 * 1440 - 1] == 43.0 and e[-1] == 1.0


# ───────────── ядро: множитель размера входа ─────────────

def _sine(nd=2, T=720, A=3.5):
    t = np.arange(nd * 1440)
    return nd, np.round(100 + A * np.sin(2 * np.pi * t / T), 3)


def test_kernel_szm_scales_pnl_linearly_and_one_is_identity():
    nd, c = _sine()
    S = mk(c, nd)
    P = L.make_params(g0=0.01, day0=0, t_start=0)
    base = run(S, nd, P)
    n = nd * 1440
    assert np.array_equal(run(S, nd, P, szm=np.ones(n)), base)  # множитель 1 — как без него
    two = run(S, nd, P, szm=np.full(n, 2.0))
    assert np.allclose(two[[L.R_PNL, L.R_CYC, L.R_EXIT]], 2 * base[[L.R_PNL, L.R_CYC, L.R_EXIT]], rtol=1e-12, atol=1e-12)
    assert np.array_equal(two[[L.R_NCYC, L.R_NOPEN, L.R_NEDGE]], base[[L.R_NCYC, L.R_NOPEN, L.R_NEDGE]])  # счёт сделок тот же (размер не двигает цены)


def test_kernel_szm_is_fixed_at_entry_not_at_exit():
    """Лонг-слот 1 входит на 99,0 при множителе 3, выход на 100,0 — когда множитель уже 1: круг считается по размеру входа."""
    nd = 1
    n = nd * 1440
    c = np.full(n, 100.0)
    c[100:200] = 98.9  # ниже уровня 99,0 на ≥ 1 тик: вход исполняется
    c[200:] = 100.1  # выше уровня выхода 100,0
    c = np.round(c, 3)
    S = mk(c, nd)
    P = L.make_params(g0=0.01, day0=0, t_start=0)
    szm = np.ones(n)
    szm[:150] = 3.0
    out = run(S, nd, P, szm=szm)
    base = run(S, nd, P)
    assert int(out[L.R_NCYC].sum()) == 1 and int(base[L.R_NCYC].sum()) == 1
    exp_unit = 25 * (100.0 / 99.0 - 1) - 25 * MK - 25 * (100.0 / 99.0) * MK
    assert base[L.R_CYC].sum() == pytest.approx(exp_unit, abs=1e-9)
    assert out[L.R_CYC].sum() == pytest.approx(3 * exp_unit, abs=1e-9)


# ───────────── PM4: дневной лимит убытка монеты ─────────────

def _decline_path(nd, segs):
    """Кусочно-линейная цена по сегментам (мин_от, мин_до, цена_до) с начальной 100; дальше — константа."""
    n = nd * 1440
    c = np.full(n, 100.0)
    cur, t0 = 100.0, 0
    for (a, b, to) in segs:
        c[t0:a] = cur
        c[a:b] = np.linspace(cur, to, b - a)
        cur, t0 = to, b
    c[t0:] = cur
    return np.round(c, 3)


def _P(limit=0.0, **kw):
    return L.make_params(g0=0.01, day0=0, t_start=0, limit=limit, **kw)


def test_pm4_limit_closes_inventory_and_blocks_rest_of_day_then_resumes():
    nd = 3
    c = _decline_path(nd, [(100, 400, 96.0)])
    t = np.arange(nd * 1440)
    c = np.where(t >= 1440, np.round(96.0 + 1.5 * np.sin(2 * np.pi * (t - 1440) / 120.0), 3), c)  # сутки 1–2: колебание около 96 (сетка запускается заново вокруг цены)
    S = mk(c, nd)
    base = run(S, nd, _P())
    lim = run(S, nd, _P(0.5))
    assert lim[L.R_NREG].tolist() == [1.0, 0.0, 0.0]  # одно срабатывание, сутки 0
    assert base[L.R_NREG].sum() == 0
    # без лимита входы идут и дальше — падение глубже, на конец суток убыток больше
    assert lim[L.R_NOPEN, 0] < base[L.R_NOPEN, 0] and lim[L.R_PNL, 0] > base[L.R_PNL, 0]
    # убыток суток при лимите ограничен лимитом + проскальзывание/комиссия закрытия + запаздывание на минуту
    assert -0.5 - 0.15 <= lim[L.R_PNL, 0] <= -0.5
    assert lim[L.R_EXIT, 0] < 0  # закрытие по рынку записано в выходы
    # блок снят в сутки 1: сетка возобновила входы и круги
    assert lim[L.R_NOPEN, 1] > 0 and lim[L.R_NCYC, 1] > 0 and lim[L.R_PNL, 1] > 0


def test_pm4_no_new_entries_after_trigger_same_day():
    nd = 2
    c = _decline_path(nd, [(100, 400, 96.0)])
    S = mk(c, nd)
    lim = run(S, nd, _P(0.5))
    # сравнение по минутам: срабатывание → число входов в сутки 0 не растёт дальше уровня на момент срабатывания; находим его повторным прогоном на усечённом ряде
    c2 = c.copy()
    S1 = mk(c2, nd)
    probe = run(S1, nd, _P(0.5))
    assert probe[L.R_NOPEN, 0] == lim[L.R_NOPEN, 0] and lim[L.R_NREG, 0] == 1.0
    # с очень большим лимитом падение до 96 даёт больше входов (уровни 99, 98, 97 → ещё и 96)
    big = run(S, nd, _P(1e6))
    assert big[L.R_NOPEN, 0] > lim[L.R_NOPEN, 0]


def test_pm4_limit_not_reached_is_bitwise_identical_to_no_limit():
    nd, c = _sine()
    S = mk(c, nd)
    assert np.array_equal(run(S, nd, _P(0.0)), run(S, nd, _P(1e9)))


def test_pm4_day_boundary_uses_day_change_not_cumulative():
    """Убыток прошлых суток (оценка открытого на их конце) не считается в лимит следующих: лимит 0,2 $; сутки 0 −0,16, сутки 1 −0,10 (накоплено −0,26 — не срабатывает), сутки 2 −0,60 — срабатывает."""
    nd = 3
    c = _decline_path(nd, [(100, 1200, 98.5), (1440 + 0, 1440 + 360, 98.1), (2880, 3300, 97.0)])
    S = mk(c, nd)
    lim = run(S, nd, _P(0.2))
    base = run(S, nd, _P())
    assert lim[L.R_NREG].tolist() == [0.0, 0.0, 1.0]
    assert np.array_equal(lim[:, :2], base[:, :2])  # до срабатывания — как без лимита
    assert lim[L.R_PNL, 2] > base[L.R_PNL, 2]  # лимит сократил убыток суток 2
    assert -0.2 - 0.15 <= lim[L.R_PNL, 2]


def test_pm4_short_side_symmetric():
    nd = 2
    c = _decline_path(nd, [(100, 400, 104.0)])
    S = mk(c, nd)
    base = run(S, nd, _P())
    lim = run(S, nd, _P(0.5))
    assert lim[L.R_NREG, 0] == 1.0 and lim[L.R_PNL, 0] > base[L.R_PNL, 0] and lim[L.R_NOPEN, 0] < base[L.R_NOPEN, 0]


def test_pm4_profit_day_never_triggers():
    nd = 2
    t = np.arange(nd * 1440)
    c = np.round(100 + 3.5 * np.sin(2 * np.pi * t / 720), 3)
    S = mk(c, nd)
    out = run(S, nd, _P(5.0))
    assert out[L.R_NREG].sum() == 0


# ───────────── метрики и правило «слой полезен» ─────────────

def test_metrics_pm_tail_and_rr():
    d = np.array([3.0, -1.0, 2.0, -4.0, 0.0, 5.0])
    m = M.metrics_pm(d, 5000.0)
    assert m["usd"] == 5.0 and m["worst"] == -4.0 and m["worst5"] == pytest.approx(-4 - 1 + 0 + 2 + 3)
    assert m["rr"] == pytest.approx(np.mean([3, 2, 5]) / np.mean([1, 4])) and m["loss_share"] == pytest.approx(2 / 6)
    assert m["dd_pct"] == pytest.approx(100 * m["dd"] / 5000.0) and m["days"] == 6


def test_layer_useful_rules():
    base = dict(usd=70.0, days=140, dd_pct=1.0, sharpe=1.0)  # средний суточный Σ PM0 = 0,5 → порог −0,05
    lay = dict(base, dd_pct=0.5, sharpe=0.9)
    pr_ok = {7: dict(lo=-0.04), 21: dict(lo=-0.03)}
    assert M.layer_useful(lay, base, pr_ok)["a"] is True  # просадка ниже, не съедает доход (нижняя граница ≥ −10 %)
    pr_eat = {7: dict(lo=-0.20), 21: dict(lo=-0.03)}
    assert M.layer_useful(lay, base, pr_eat)["useful"] is False  # съедает доход
    lay_b = dict(base, dd_pct=1.5, sharpe=1.4)
    pr_pos = {7: dict(lo=0.01), 21: dict(lo=0.02)}
    u = M.layer_useful(lay_b, base, pr_pos)
    assert u["b"] is True and u["a"] is False and u["useful"] is True
    assert M.layer_useful(lay_b, base, {7: dict(lo=0.01), 21: dict(lo=-0.01)})["useful"] is False  # нижняя граница одного блока ниже 0


def test_set_daily_basket_members_and_margin():
    nc = 30
    pnl = np.zeros((nc, L.NOUT))
    k = 4
    ws = W.week_start_day(k)
    pnl[3, ws + 1] = 5.0
    pnl[7, ws + 1] = 2.0
    pnl[9, ws + 1] = 100.0  # не в корзине
    pnl[3, ws + 8] = 50.0  # следующая неделя
    mem = {k: [3, 7]}
    d = M.set_daily(pnl, [k], mem)
    assert d.tolist() == [0, 7.0, 0, 0, 0, 0, 0]
    assert M.set_daily(pnl, [k], None).tolist() == [0, 107.0, 0, 0, 0, 0, 0]
    mult = np.ones((nc, W.NW))
    mult[3, k] = 2.0
    mult[7, k] = 0.5
    assert M.set_margin([k], mem, np.zeros((nc, L.NOUT)), None) == 500.0  # 250 × K
    assert M.set_margin([k], mem, np.zeros((nc, L.NOUT)), mult) == pytest.approx(250 * 2.5)
    act = np.zeros((nc, L.NOUT))
    act[:, ws:ws + 7] = 1.0
    assert M.set_margin([k], None, act, None) == 250.0 * nc
    assert M.set_margin([k], None, act, mult) == pytest.approx(250.0 * (nc - 2 + 2.5))


def test_layer_variants_have_all_layers_and_pm1_uses_week_multiplier():
    mb = np.full(W.NW, 1.5)
    ma = np.full(W.NW, 0.5)
    n = (L.DAY0 + L.NOUT) * 1440
    vs = M.layer_variants(mb, ma, n)
    assert [v["name"] for v in vs] == list(M.LAYERS)
    byn = {v["name"]: v for v in vs}
    assert "szm" not in byn["PM0"] and byn["PM1b"]["szm"][W.week_start_min(2)] == 1.5 and byn["PM1a"]["szm"][W.week_start_min(2)] == 0.5
    assert [byn["PM4_%d" % x]["P"]["limit"] for x in (25, 50, 100)] == [25, 50, 100] and byn["PM0"]["P"].get("limit", 0.0) == 0.0
    assert byn["PM1b"]["P"].get("freeze") == 1 and byn["PM4_25"]["P"].get("freeze") == 1  # все слои — на MRf с замороженным центром
