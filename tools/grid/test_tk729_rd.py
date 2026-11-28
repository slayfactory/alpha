"""Тесты TK-729 §5 (PS-1065): режим монеты и рынка, торговля по направлению, закрытие запаса против режима, разметка по режиму и стороне;
а также (замечание проверяющего) «касание — не исполнение» и «один раз на уровень в минуту»."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402
from test_tk729 import MK, SL, TK, feats, fl, mk, run  # noqa: E402


def raw(o, h, l, c, ndays=2):
    """Свечи, заданные вручную; остаток суток — плоские свечи по последнему закрытию."""
    n = ndays * 1440
    ext = lambda a: np.concatenate([np.asarray(a, float), np.full(n - len(a), c[-1])])  # хвост — плоские свечи на последнем закрытии
    return dict(o=ext(o), h=ext(h), l=ext(l), c=ext(c), qv=np.ones(n), pres=np.ones(n, bool))


def test_touch_is_not_a_fill_and_one_fill_per_level_per_minute():
    nd = 2
    P = L.make_params(g0=0.01, day0=0, t_start=0)  # центр 100, шаг 1 → покупка на 99,0, её продажа на 100,0; тик 0,001

    def go(lo, hi):
        """Минута 0 — запуск; далее по минутам (low, high); open = close = 100."""
        k = len(lo) + 1
        out = run(raw([100.0] * k, [100.0] + list(hi), [100.0] + list(lo), [100.0] * k, nd), nd, P)
        return int(out[L.R_NOPEN].sum()), int(out[L.R_NCYC].sum())

    assert go([99.0], [100.0]) == (0, 0)  # минимум ровно на уровне 99,0 — касание, не исполнение
    assert go([98.999], [100.0]) == (1, 0)  # на тик глубже — исполнение
    assert go([98.999], [100.5]) == (1, 0)  # вход и выход в одну минуту: выход — не раньше следующей
    assert go([98.999, 99.5], [100.5, 100.5]) == (1, 1)  # выход на следующей минуте
    assert go([98.999, 98.999], [100.5, 100.5]) == (1, 1)  # выход минуты 2; повторный вход в ту же минуту невозможен
    assert go([98.999, 98.999, 98.999], [100.5, 100.5, 100.5]) == (2, 1)  # вход минуты 3 — на следующей минуте после выхода


def test_regime_T_lag_hold_and_nan():
    import tk729_cfg as C
    n = 2 * 1440
    t = np.arange(n)
    kal = np.exp(0.001 * t)  # ln-тренд 0,001 в минуту → за 240 минут 0,24
    T = C.regime_T(kal, np.full(n, 0.1))
    assert np.isnan(T[:240]).all()
    assert abs(T[600] - 2.4) < 1e-9 and np.all(T[600:615] == T[600])  # значение держится 15 минут (600 кратно 15)
    assert abs(T[615] - 2.4) < 1e-9
    assert C.reg_from_T(T[600:601], 2.0).tolist() == [1] and C.reg_from_T(T[600:601], 3.0).tolist() == [0]
    Td = C.regime_T(kal[::-1].copy(), np.full(n, 0.1))
    assert C.reg_from_T(Td[600:601], 2.0).tolist() == [-1]
    assert np.isnan(C.regime_T(kal, np.full(n, np.nan))[600])
    assert C.reg_from_T(np.array([np.nan, 0.99, 1.0, -1.0, -0.99]), 1.0).tolist() == [0, 0, 1, -1, 0]  # σ_eq неизвестна → флэт


def test_regime_btc_combination():
    import tk729_cfg as C
    r = np.array([1, 1, -1, -1, 0, 1, 0], np.int8)
    b = np.array([1, 0, -1, 1, 1, -1, 0], np.int8)
    assert C.combine_btc(r, b).tolist() == [1, 0, -1, 0, 0, 0, 0]


def _kw(**kw):
    return dict(use_cen=1, gmode=1, skew_coef=0.0, day0=0, t_start=0, **kw)


def _side(out, ri, sd, m):
    return out[L.A0 + (ri * 2 + sd) * 6 + m].sum()


def test_regime_up_blocks_shorts_down_blocks_longs():
    nd = 2
    n = nd * 1440
    t = np.arange(n)
    S = mk(np.round(100 + 3.5 * np.sin(2 * np.pi * t / 720), 3), nd)
    cen, gfr = np.full(n, 100.0), np.full(n, 0.01)
    base = L.run_coin(S, feats(nd), L.make_params(**_kw()), cen=cen, gfr=gfr, reg=np.zeros(n, np.int8), attrib=True)
    up = L.run_coin(S, feats(nd), L.make_params(**_kw(regmode=1)), cen=cen, gfr=gfr, reg=np.ones(n, np.int8), attrib=True)
    dn = L.run_coin(S, feats(nd), L.make_params(**_kw(regmode=1)), cen=cen, gfr=gfr, reg=-np.ones(n, np.int8), attrib=True)
    assert _side(base, 1, 0, 3) > 0 and _side(base, 1, 1, 3) > 0  # флэт: круги в обе стороны
    assert _side(up, 2, 1, 3) == 0 and _side(up, 2, 1, 0) == 0  # вверх: шортов нет
    assert _side(up, 2, 0, 3) == _side(base, 1, 0, 3) and abs(_side(up, 2, 0, 0) - _side(base, 1, 0, 0)) < 1e-9  # лонги — те же круги, что у нейтральной сетки
    assert _side(dn, 0, 0, 3) == 0 and _side(dn, 0, 0, 0) == 0  # вниз: лонгов нет
    assert _side(dn, 0, 1, 3) == _side(base, 1, 1, 3) and abs(_side(dn, 0, 1, 0) - _side(base, 1, 1, 0)) < 1e-9
    assert up[L.R_NREG].sum() == 0 and dn[L.R_NREG].sum() == 0  # запаса против режима не было
    tag_only = L.run_coin(S, feats(nd), L.make_params(**_kw()), cen=cen, gfr=gfr, reg=np.ones(n, np.int8), attrib=True)  # без regmode режим — только метка
    assert np.allclose(tag_only[: L.NR], base[: L.NR])


def test_adverse_regime_closes_long_inventory_at_market():
    nd = 2
    n = nd * 1440
    c = fl(np.round(100 - 0.007 * np.arange(200), 3), n)  # падение до 98,607: покупка на 99,0 (слот 1), затем константа
    S = mk(c, nd)
    cen, gfr = np.full(n, 100.0), np.full(n, 0.01)
    P = L.make_params(**_kw(regmode=1))
    reg = np.zeros(n, np.int8)
    reg[400:] = -1  # «вниз» с минуты 400
    out = L.run_coin(S, feats(nd), P, cen=cen, gfr=gfr, reg=reg, attrib=True)
    px = c[400] * (1 - SL)
    q = 25 / 99.0
    exp = q * (px - 99.0) - 25 * MK - q * px * TK
    assert int(out[L.R_NREG].sum()) == 1 and int(out[L.R_NCYC].sum()) == 0 and int(out[L.R_NEDGE].sum()) == 0
    assert abs(out[L.R_EXIT].sum() - exp) < 1e-9 and abs(out[L.R_PNL].sum() - exp) < 1e-9  # открытого запаса после закрытия нет
    b = L.A0 + (1 * 2 + 0) * 6  # эпизод начался при «флэт» (метка = reg: 0), лонг
    assert abs(out[b].sum() - exp) < 1e-9 and out[b + 5].sum() == 1 and out[b + 4].sum() == 0 and out[L.AEP + 1].sum() == 1
    assert int(out[L.R_NOPEN].sum()) == 1  # при «вниз» лонги больше не открываются (цена 98,607 < 99)
    for rg in (0, 1):  # «флэт» / «вверх» запас лонга не закрывают
        r2 = np.zeros(n, np.int8)
        r2[400:] = rg
        o2 = L.run_coin(S, feats(nd), P, cen=cen, gfr=gfr, reg=r2, attrib=True)
        assert int(o2[L.R_NREG].sum()) == 0 and o2[L.R_EXIT].sum() == 0


def test_adverse_regime_closes_short_inventory_at_market():
    nd = 2
    n = nd * 1440
    c = fl(np.round(100 + 0.007 * np.arange(200), 3), n)
    S = mk(c, nd)
    cen, gfr = np.full(n, 100.0), np.full(n, 0.01)
    reg = np.zeros(n, np.int8)
    reg[400:] = 1
    out = L.run_coin(S, feats(nd), L.make_params(**_kw(regmode=1)), cen=cen, gfr=gfr, reg=reg, attrib=True)
    px = c[400] * (1 + SL)
    q = 25 / 101.0
    exp = q * (101.0 - px) - 25 * MK - q * px * TK
    assert int(out[L.R_NREG].sum()) == 1 and abs(out[L.R_EXIT].sum() - exp) < 1e-9
    b = L.A0 + (1 * 2 + 1) * 6
    assert abs(out[b].sum() - exp) < 1e-9 and out[b + 5].sum() == 1


def test_attribution_by_regime_at_first_fill():
    nd = 2
    n = nd * 1440
    up = np.round(100 + 0.012 * np.arange(110), 3)  # до 101,308: шорт слота 1 на 101,0 (около минуты 84)
    down = np.round(up[-1] - 0.012 * np.arange(1, 150), 3)  # возврат ниже 100,0 — выход шорта (продажа → покупка на 100,0)
    S = mk(fl(np.concatenate([up, down]), n), nd)
    cen, gfr = np.full(n, 100.0), np.full(n, 0.01)
    tag = np.zeros(n, np.int8)
    tag[:100] = 1  # на входе режим «вверх», к выходу — «флэт»
    out = L.run_coin(S, feats(nd), L.make_params(**_kw()), cen=cen, gfr=gfr, reg=np.zeros(n, np.int8), tag=tag, attrib=True)
    assert int(out[L.R_NCYC].sum()) == 1
    tot = sum(out[L.A0 + i * 6].sum() for i in range(6))
    assert abs(tot - (out[L.R_CYC].sum() + out[L.R_EXIT].sum())) < 1e-9  # разметка покрывает все закрытые лоты
    assert out[L.AEP + 2].sum() >= 1 and _side(out, 2, 1, 3) == 1  # эпизод начался при «вверх»; круг записан шорту в режиме «вверх»
