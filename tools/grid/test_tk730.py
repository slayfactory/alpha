"""TK-730 §6: синтетические проверки метода (признаки с известным ответом, выравнивание недель, ρ, IC, Холм, корзина) — до чтения результатов."""
from __future__ import annotations

import datetime
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402
import tk730_lib as W  # noqa: E402


def _S(c, qv=None, pres=None):
    n = len(c)
    return dict(c=np.asarray(c, float), pres=np.ones(n, bool) if pres is None else pres, qv=np.ones(n) if qv is None else qv, o=np.asarray(c, float), h=np.asarray(c, float), l=np.asarray(c, float))


# ── выравнивание недель ──

def test_g0_and_weeks_are_monday_utc():
    assert datetime.datetime.fromtimestamp(L.G0_MS / 1000, datetime.timezone.utc) == datetime.datetime(2025, 12, 12, tzinfo=datetime.timezone.utc)
    for k in range(W.NW):
        d = datetime.date.fromisoformat(W.week_date(k))
        assert d.weekday() == 0, W.week_date(k)
    assert W.week_date(0) == "2026-01-26"
    assert W.week_date(W.NW - 1) == "2026-11-16"
    assert W.week_date(W.H1W[-1]) == "2026-06-29" and W.week_date(W.H2W[0]) == "2026-07-06"
    assert len(W.H1W) + len(W.H2W) == W.NW and W.week_start_day(W.NW - 1) + 7 <= L.NOUT  # последняя неделя целиком в данных
    assert W.week_start_min(0) == (L.DAY0 + 25) * 1440 and W.week_start_min(1) - W.week_start_min(0) == 7 * 1440


def test_weekly_sums_align_to_days():
    daily = np.zeros((2, L.NOUT))
    daily[0, 25:32] = 1.0  # неделя 0
    daily[1, 32] = 5.0  # первый день недели 1
    daily[1, 31] = 7.0  # последний день недели 0
    w = W.weekly(daily)
    assert w[0, 0] == 7.0 and w[0, 1] == 0.0 and w[1, 1] == 5.0 and w[1, 0] == 7.0 and w.shape == (2, W.NW)


# ── ранги, Спирмен, Холм ──

def test_rank_and_spearman_match_scipy():
    scipy_stats = pytest.importorskip("scipy.stats")
    rng = np.random.default_rng(1)
    x = np.round(rng.normal(size=200), 1)  # со связками
    y = np.round(x * 0.5 + rng.normal(size=200), 1)
    assert np.allclose(W.rank_avg(x), scipy_stats.rankdata(x))
    assert W.spearman(x, y) == pytest.approx(scipy_stats.spearmanr(x, y)[0], abs=1e-12)
    y2 = y.copy()
    y2[3] = np.nan
    assert np.isfinite(W.spearman(x, y2)) and math.isnan(W.spearman([1, 1, 1, 1], [1, 2, 3, 4]))


def test_holm():
    adj = W.holm([0.01, 0.04, 0.03])
    assert adj == pytest.approx([0.03, 0.06, 0.06])
    assert all(a >= p for a, p in zip(adj, [0.01, 0.04, 0.03]))


# ── признаки на синтетике с известным ответом ──

def test_chop_ratio_known():
    assert W.chop_ratio(np.linspace(0, 1, 168)) == pytest.approx(1.0)  # прямая
    zz = np.tile([0.0, 1.0], 84)  # 168 точек, 167 сдвигов по 1, чистый сдвиг 1
    assert W.chop_ratio(zz) == pytest.approx(167.0)
    assert W.chop_ratio(np.tile([0.0, 1.0, 1.0, 0.0], 42)) == float("inf")  # нулевой чистый сдвиг
    assert math.isnan(W.chop_ratio(np.array([0.0, np.nan, 1.0])))


def test_variance_ratio_known():
    rng = np.random.default_rng(7)
    n = 200_000
    z = rng.normal(size=n + 1) * 1e-3
    iid = np.cumsum(z[1:])
    assert W.variance_ratio(iid) == pytest.approx(1.0, abs=0.08)
    th = 0.5  # MA(1): ρ1 = θ/(1+θ²) = 0.4, VR(q) = 1 + 2(1 − 1/q)ρ1
    ma = np.cumsum(z[1:] + th * z[:-1])
    assert W.variance_ratio(ma) == pytest.approx(1 + 2 * (1 - 1 / 60) * 0.4, abs=0.08)
    alt = np.cumsum(1e-3 * np.where(np.arange(n) % 2 == 0, 1.0, -1.0))  # доходности +, −: 60-минутный сдвиг всегда 0
    assert W.variance_ratio(alt) == pytest.approx(0.0, abs=1e-9)
    assert math.isnan(W.variance_ratio(np.full(1000, 1.0)))  # нулевая дисперсия


def test_trend_strength_known():
    mu, s, nn = 2e-5, 1e-3, 10_080
    r = mu + s * np.where(np.arange(nn - 1) % 2 == 0, 1.0, -1.0)
    lnc = np.concatenate([[0.0], np.cumsum(r)])
    expected = abs(lnc[-1]) / math.sqrt((r * r).sum() / ((nn - 1) / 1440.0))
    assert W.trend_strength(lnc) == pytest.approx(expected, rel=1e-12)
    assert expected == pytest.approx((nn - 1) * mu / math.sqrt((mu * mu + s * s) * 1440.0), rel=0.02)  # и по формуле в ручную
    assert W.trend_strength(-lnc) == pytest.approx(expected)  # |.|: знак не важен


def test_beta_known():
    rng = np.random.default_rng(3)
    rb = rng.normal(size=5000) * 1e-3
    ri = 1.7 * rb + rng.normal(size=5000) * 3e-4
    assert W.beta_to(ri, rb) == pytest.approx(1.7, abs=0.05)
    assert W.beta_to(rb, rb) == pytest.approx(1.0)
    assert math.isnan(W.beta_to(np.zeros(100), np.zeros(100)))


def _ar1_series(n=76_000, phi=0.98, se=1e-3, seed=11):
    rng = np.random.default_rng(seed)
    e = rng.normal(size=n) * se
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


def test_week_features_ou_known_halflife_and_cover_and_beta():
    phi, se = 0.98, 1e-3
    x = _ar1_series(phi=phi, se=se)
    S = _S(np.exp(x), qv=np.full(len(x), 2.0))
    ou = L.ou_rolling(S, 1440)
    lb = x * 0.0 + np.cumsum(np.random.default_rng(5).normal(size=len(x)) * 5e-4)  # «BTC»
    # монета = 1.7 · BTC + OU-шум: бета по часовым доходностям около 1,7, hl и σ_eq — от OU-части (BTC дрейфует медленнее часового шума? проверяем только hl/cover на чистом OU ниже)
    f = W.week_features(S, ou, lb, 0)
    chop, vr, hl, cover, turn, beta, trend = f
    hl_true = math.log(2) / -math.log(phi)
    assert hl == pytest.approx(hl_true, rel=0.2)  # оценка AR(1) на 1 440 точках смещена вниз ≈ 12 %
    sig_eq = se / math.sqrt(1 - phi ** 2)
    assert cover == pytest.approx(sig_eq / 0.005, rel=0.15)
    assert turn == pytest.approx(2.0 * W.WIN)
    assert vr == pytest.approx((1 - phi ** 60) / (60 * (1 - phi)), rel=0.1)  # аналитический VR(60) стационарного AR(1) = 0,585
    assert np.isfinite(chop) and chop > 5 and np.isfinite(beta) and np.isfinite(trend)


def test_week_features_beta_recovered():
    rng = np.random.default_rng(9)
    n = 76_000
    lb = np.cumsum(rng.normal(size=n) * 1e-3)
    x = 1.7 * lb + np.cumsum(rng.normal(size=n) * 2e-4)
    S = _S(np.exp(x))
    ou = L.ou_rolling(S, 1440)
    f = W.week_features(S, ou, lb, 0)
    assert f[5] == pytest.approx(1.7, abs=0.1)


def test_features_use_only_data_before_week_start():
    """Подменяем всё, что на минутах ≥ m0 (цены, оборот, наличие), — признаки недели k = 0 те же до последнего знака; ou_rolling причинный."""
    rng = np.random.default_rng(21)
    n = 80_000
    x = np.cumsum(rng.normal(size=n) * 5e-4) + 0.3 * np.sin(np.arange(n) / 50.0)
    lb = np.cumsum(rng.normal(size=n) * 4e-4)
    m0 = W.week_start_min(0)
    S1 = _S(np.exp(x), qv=rng.uniform(1, 2, n))
    f1 = W.week_features(S1, L.ou_rolling(S1, 1440), lb, 0)
    x2, lb2, qv2, p2 = x.copy(), lb.copy(), S1["qv"].copy(), S1["pres"].copy()
    x2[m0:] += rng.normal(size=n - m0) * 0.5 + 3.0
    lb2[m0:] += 2.0
    qv2[m0:] = 1e9
    p2[m0 + 5000:] = False
    S2 = _S(np.exp(x2), qv=qv2, pres=p2)
    f2 = W.week_features(S2, L.ou_rolling(S2, 1440), lb2, 0)
    assert np.array_equal(f1, f2) and np.isfinite(f1).sum() >= 6
    # а подмена внутри окна (последняя минута до m0) — меняет
    x3 = x.copy()
    x3[m0 - 1] += 0.5
    S3 = _S(np.exp(x3), qv=S1["qv"])
    f3 = W.week_features(S3, L.ou_rolling(S3, 1440), lb, 0)
    assert not np.array_equal(f1, f3)


def test_features_nan_without_history():
    n = 80_000
    c = np.exp(np.cumsum(np.random.default_rng(2).normal(size=n) * 1e-3))
    pres = np.ones(n, bool)
    pres[: W.week_start_min(0) - 3000] = False  # торгуется меньше суток до окна: нет ½ окна
    c[~pres] = np.nan
    S = _S(c, pres=pres)
    f = W.week_features(S, L.ou_rolling(S, 1440), np.zeros(n), 0)
    assert np.isnan(f).all()


# ── ρ, верхний дециль, IC, корзина ──

def test_persistence_detects_and_rejects():
    rng = np.random.default_rng(4)
    nc = 200
    base = rng.normal(size=(nc, W.NW))
    tr = np.ones((nc, W.NW), bool)
    rho_none = W.persistence(base, tr)
    assert abs(rho_none["rho_b"]["mean"]) < 0.05 and rho_none["rho_b"]["lo"] < 0 < rho_none["rho_b"]["hi"]
    skill = rng.normal(size=(nc, 1))  # постоянный «навык» монеты
    pers = skill + rng.normal(size=(nc, W.NW))
    r = W.persistence(pers, tr)
    assert r["rho_b"]["lo"] > 0 and r["diff_b"]["lo"] > 0 and r["diff_b"]["mean"] > 0
    assert r["rho"].shape == (W.NW - 1,) and (r["ncoin"] == nc).all() and (r["ntop"] == 20).all()


def test_persistence_pairs_only_traded_in_both_weeks():
    nc = 30
    p = np.tile(np.arange(nc, dtype=float)[:, None], (1, 4))
    t = np.ones((nc, 4), bool)
    t[:5, 1] = False  # первые 5 монет не торговали на неделе 1
    r = W.persistence(p, t)
    assert r["ncoin"][0] == nc - 5 and r["ncoin"][1] == nc - 5 and r["ncoin"][2] == nc
    assert r["rho"][0] == pytest.approx(1.0) and r["top_m"][2] == pytest.approx(np.mean([29, 28, 27]))  # дециль из 30 монет — 3
    assert r["all_m"][2] == pytest.approx(np.mean(np.arange(nc)))


def test_ic_weekly_alignment_uses_feature_known_before_week():
    rng = np.random.default_rng(8)
    nc = 120
    feat = rng.normal(size=(nc, W.NW))
    tr = np.ones((nc, W.NW), bool)
    ic_same = W.ic_weekly(feat, feat * 2.0, tr)  # P&L недели k упорядочен как признак недели k
    assert np.allclose(ic_same, 1.0)
    shifted = np.roll(feat, -1, axis=1)  # P&L недели k = признак недели k + 1: IC ≈ 0
    assert abs(np.nanmean(W.ic_weekly(feat, shifted, tr))) < 0.05
    tr2 = tr.copy()
    tr2[:, 3] = False
    assert np.isnan(W.ic_weekly(feat, feat, tr2)[3])  # неделя без торговли — NaN, не 0


def test_pick_basket_sign_and_ties():
    f = np.array([[5.0], [1.0], [9.0], [np.nan], [9.0], [3.0]])
    assert list(W.pick_basket(f, 0, +1, 2)) == [2, 4]  # связка — по индексу монеты
    assert list(W.pick_basket(f, 0, -1, 2)) == [1, 5]  # знак «минус»: наименьшие
    assert list(W.pick_basket(f, 0, +1, 10)) == [2, 4, 0, 5, 1]  # NaN не берётся
    el = np.array([[True], [True], [False], [True], [True], [True]])
    assert list(W.pick_basket(f, 0, +1, 2, el)) == [4, 0]


def test_boot_week_zero_mean_includes_zero_and_shift_excludes():
    rng = np.random.default_rng(6)
    x = rng.normal(size=43)
    b0 = W.boot_week(x - x.mean())
    assert b0["lo"] < 0 < b0["hi"] and b0["p"] > 0.9
    b1 = W.boot_week(x + 2.0)
    assert b1["lo"] > 0 and b1["p"] < 0.01
