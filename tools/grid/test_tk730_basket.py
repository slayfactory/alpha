"""TK-730 §6.2–§6.3: синтетические проверки корзины (посаженный сигнал, нулевой мир, выравнивание, «без 3 лучших», месяцы, слова вердикта) — до чтения результатов."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402
import tk730_basket as B  # noqa: E402
import tk730_lib as W  # noqa: E402

NC = 100


def _world(signal: float, seed: int = 1, shift: int = 0, noise: float = 2.0):
    """Мир: 8 признаков; P&L монеты в сутки недели k = signal · z(признак 'vr' недели k + shift)/7 + шум. Внутри недели P&L одинаково разложен по суткам."""
    rng = np.random.default_rng(seed)
    fe = rng.normal(size=(8, NC, W.NW))
    pnl = rng.normal(size=(NC, L.NOUT)) * noise
    for k in range(W.NW):
        kk = min(max(k + shift, 0), W.NW - 1)
        ws = W.week_start_day(k)
        pnl[:, ws:ws + 7] += signal * fe[2, :, kk][:, None] / 7.0
    return fe, pnl


def _core(fe, pnl):
    pd = {"C0": pnl, "MRf": pnl * 0.5}
    feats = {"C0": fe, "MRf": fe.copy()}
    U = np.ones((NC, W.NW), bool)
    pw = {b: W.weekly(pd[b]) for b in pd}
    tw = {b: np.ones((NC, W.NW), bool) for b in pd}
    ic = B.stage62_core(feats, pw, tw)
    return feats, pd, ic, U


def test_planted_signal_found_and_works():
    fe, pnl = _world(signal=14.0)
    feats, pd, ic, U = _core(fe, pnl)
    assert ic["C0"][2]["pass"] and ic["C0"][2]["feat"] == "vr" and not any(ic["C0"][i]["pass"] for i in (0, 1, 3, 4, 5, 6, 7))
    r = B.stage63_core(feats, pd, ic, U)
    for d in r["dirs"]:
        assert d["best"]["feat"] == "vr" and d["best"]["sign"] == 1 and d["best"]["bot"] == "C0"
        assert d["check"]["ok"], d["check"]
    assert r["verdict"] == "работает"


def test_null_world_not_working():
    fe, pnl = _world(signal=0.0, seed=2)
    feats, pd, ic, U = _core(fe, pnl)
    r = B.stage63_core(feats, pd, ic, U)
    assert r["verdict"] != "работает"
    assert sum(ic["C0"][i]["pass"] for i in range(8)) <= 1  # ложных проходов при 8 признаках после Холма почти нет


def test_negative_sign_taken_from_training_half():
    fe, pnl = _world(signal=-14.0, seed=3)
    feats, pd, ic, U = _core(fe, pnl)
    r = B.stage63_core(feats, pd, ic, U)
    assert all(d["best"]["sign"] == -1 and d["best"]["feat"] == "vr" for d in r["dirs"]) and r["verdict"] == "работает"


def test_feature_of_next_week_is_not_usable():
    """P&L недели k построен из признака недели k + 1: корзина по признаку недели k сигнала не видит (выравнивание — признак строго до недели)."""
    fe, pnl = _world(signal=14.0, seed=4, shift=1)
    feats, pd, ic, U = _core(fe, pnl)
    assert not ic["C0"][2]["pass"] and abs(ic["C0"][2]["full"]["mean"]) < 0.1
    assert B.stage63_core(feats, pd, ic, U)["verdict"] != "работает"


def test_basket_run_picks_top_k_and_sums_that_week_days():
    fe = np.zeros((NC, W.NW))
    fe[:, 5] = np.arange(NC)  # неделя 5: монеты 99, 98, … лучшие
    pnl = np.zeros((NC, L.NOUT))
    ws = W.week_start_day(5)
    pnl[99, ws + 2] = 10.0
    pnl[98, ws + 6] = 3.0
    pnl[0, ws + 2] = 1000.0  # не в корзине
    pnl[99, ws + 7] = 500.0  # первые сутки следующей недели — не в этой
    U = np.ones((NC, W.NW), bool)
    run = B.basket_run(pnl, fe, U, +1, 20, [5])
    assert run["daily"].tolist() == [0, 0, 10, 0, 0, 0, 3] and run["days"].tolist() == list(range(ws, ws + 7))
    assert run["coin_tot"][99] == 10.0 and run["coin_tot"][98] == 3.0 and run["coin_tot"][0] == 0.0
    assert sorted(run["members"][5].tolist()) == list(range(80, 100))
    assert run["bench"].sum() == pytest.approx(20 * pnl[:, ws:ws + 7].mean(axis=0).sum())


def test_universe_mask_excludes_coins():
    fe = np.tile(np.arange(NC, dtype=float)[:, None], (1, W.NW))
    U = np.ones((NC, W.NW), bool)
    U[90:, 3] = False
    assert set(W.pick_basket(np.where(U, fe, np.nan), 3, +1, 5, U).tolist()) == {85, 86, 87, 88, 89}


def test_check_without_best3_and_months():
    ndays = 7 * len(W.H2W)
    days = B.days_of(W.H2W)
    daily = np.zeros(ndays)
    daily[::7] = 1.0  # маленький плюс
    coin_tot = np.zeros(NC)
    coin_tot[:3] = [daily.sum() * 0.4, daily.sum() * 0.4, daily.sum() * 0.4]  # три монеты несут всё и больше: без них Σ < 0
    run = dict(daily=daily, bench=np.zeros(ndays), coin_tot=coin_tot, days=days)
    c = B.check_basket(run, 20)
    assert c["ex3"] < 0 and not c["c3"] and not c["ok"]
    mon = B.month_sums(np.ones(20), np.arange(100, 120))  # 11.04–30.04: апрель, 20 суток ≥ 14
    assert mon == {"2026-04": 20.0}
    assert B.month_sums(np.ones(10), np.arange(100, 110)) == {}  # 10 суток < 14 — месяц не считается
    m2 = B.month_sums(np.ones(30), np.arange(0, 30))  # 01.01–30.01: январь 30 суток
    assert list(m2.values()) == [30.0]
    m3 = B.month_sums(np.ones(35), np.arange(25, 60))  # 26.01–01.03: январь 6 суток (нет), февраль 28 (да), март 1 (нет)
    assert list(m3.keys()) == ["2026-02"] and m3["2026-02"] == 28.0


def test_verdict_words():
    def chk(ok, lo7, lo21, usd):
        return dict(ok=ok, boot={7: dict(lo=lo7), 21: dict(lo=lo21)}, metrics=dict(usd=usd))

    assert B.verdict_words([chk(True, 1, 1, 5), chk(True, 1, 1, 5)]) == "работает"
    assert B.verdict_words([chk(False, 1, -1, 5), chk(True, 1, 1, 5)]) == "не ясно"  # блоки 7 и 21 разошлись
    assert B.verdict_words([chk(False, -1, -1, -5), chk(False, -1, -1, 5)]) == "не ясно"  # направления разных знаков
    assert B.verdict_words([chk(False, -1, -1, -5), chk(False, -2, -2, -3)]) == "нет"


def test_metrics_b169_margin_scales_with_k():
    d = np.array([5.0, -10.0, 5.0, 1.0, -2.0])
    m = B.metrics_b169(d, 20)
    assert m["usd"] == -1.0 and m["margin"] == 5000.0 and m["dd"] == pytest.approx(10.0) and m["dd_pct"] == pytest.approx(0.2)
    assert m["hw_max"] >= 1
