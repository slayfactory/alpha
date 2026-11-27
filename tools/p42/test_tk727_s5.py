"""Тесты TK-727 шага 5: признаки по свечам на синтетике, окна и дубликаты, регрессия с известным наклоном (нужен numpy)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk727_lib as L  # noqa: E402
import tk727_s5 as S5  # noqa: E402


def synth(nd=30, seed=1, base=100.0, vol=1000.0):
    rng = np.random.default_rng(seed)
    n = nd * 1440
    c = base * np.exp(np.cumsum(rng.normal(0, 1e-3, n)))
    o = np.concatenate([[base], c[:-1]])
    h = np.maximum(o, c) * (1 + 1e-4)
    l = np.minimum(o, c) * (1 - 1e-4)
    v = np.full(n, vol)
    qv = v * (h + l) / 2
    S = dict(o=o, h=h, l=l, c=c, qv=qv, vol=v, pres=np.ones(n, bool))
    return S


def test_v_matches_natr_series():
    S = synth()
    nat = L.natr(S, 30)
    m = np.array([20000, 25000])
    f = S5.coin_features(S, np.full(30, 10_000_000, np.int64), m, S["c"][m - 1])
    assert np.allclose(f["V"], nat[m - 1], equal_nan=False)


def test_f5_share_in_band():
    S = synth(nd=12)
    n = len(S["c"])
    # цена всех минут фиксирована 100; уровень 100 → доля 1, уровень 150 → 0
    S["o"][:] = S["h"][:] = S["l"][:] = S["c"][:] = 100.0
    S["qv"][:] = 5.0
    S["vol"][:] = 5.0 / 100.0
    m = np.array([11000, 11000])
    f = S5.coin_features(S, np.full(12, 10_000_000, np.int64), m, np.array([100.0, 150.0]))
    assert abs(f["F5"][0] - 1.0) < 1e-12 and f["F5"][1] == 0.0


def test_f5_partial_band():
    S = synth(nd=12)
    S["o"][:] = S["h"][:] = S["l"][:] = S["c"][:] = 100.0
    S["qv"][:] = 1.0
    S["vol"][:] = 1.0 / 100.0
    # половина окна по цене 100, половина по 110 (VWAP минуты): уровень 100 → доля ≈ 0,5
    t = 11000 - 1
    seg = slice(t - 7 * 1440 + 1, t + 1)
    idx = np.arange(seg.start, seg.stop)
    S["vol"][idx[: len(idx) // 2]] = 1.0 / 110.0
    f = S5.coin_features(S, np.full(12, 10_000_000, np.int64), np.array([11000]), np.array([100.0]))
    assert abs(f["F5"][0] - 0.5) < 1e-3


def test_f4_ratio_one_for_constant_volume():
    S = synth(nd=30)
    S["qv"][:] = 7.0
    f = S5.coin_features(S, np.full(30, 10_000_000, np.int64), np.array([40000]), np.array([100.0]))
    assert abs(f["F4"][0] - 1.0) < 1e-12


def test_f3_hour_bin():
    S = synth(nd=30)
    m0 = (L.G0_MS // L.MIN_MS)
    # минута сетки m → час UTC: (m*60000 + G0) // 3.6e6 % 24; G0 = 00:00 UTC → m = 5*60 → час 5 → корзина 1
    f = S5.coin_features(S, np.full(30, 10_000_000, np.int64), np.array([24 * 1440 + 5 * 60]), np.array([100.0]))
    assert int(f["F3"][0]) == 1 and m0 > 0


def test_window_days_and_pnl_columns():
    assert S5.day_of("2026-02-01") == 31 and S5.day_of("2026-07-01") == 181 and S5.day_of("2026-11-13") == 316
    assert S5.WIN_NAMES[:3] == ["m01", "m02", "m03"] and len(S5.FAM) == 9


# ───────────── анализ (tk727_s5_an / tk727_s5lib) ─────────────
import tk727_s5_an as AN  # noqa: E402
import tk727_s5lib as R  # noqa: E402


def synth_panel(nd=60, nc=40, slope=2.0, noise=1.0, seed=3, per_day=15):
    rng = np.random.default_rng(seed)
    day_e = rng.normal(0, 3, nd)
    coin_e = rng.normal(0, 3, nc)
    days, coins = [], []
    for d in range(nd):
        cs = rng.choice(nc, per_day, replace=False)
        days += [d] * per_day
        coins += list(cs)
    day, coin = np.array(days), np.array(coins)
    V = rng.uniform(0.2, 2.0, len(day)) + 0.3 * coin_e[coin]  # V коррелирует с эффектом монеты
    y = slope * V + day_e[day] + coin_e[coin] * 2 + rng.normal(0, noise, len(day))
    return V[:, None], y[:, None], day, coin, nd, nc


def test_fe_recovers_slope_with_day_and_coin_effects():
    X, Y, day, coin, nd, nc = synth_panel()
    b = R.fe_coefs(X, Y, day, nd, coin, nc)
    assert abs(b[0, 0] - 2.0) < 0.15
    # без эффектов монеты (только сутки) наклон смещён эффектом монеты, коррелирующим с V
    b2 = R.fe_coefs(X, Y, day, nd, np.zeros(len(day), np.int64), 1)
    assert abs(b2[0, 0] - 2.0) > abs(b[0, 0] - 2.0)


def test_boot_interval_covers_known_slope_and_zero_for_null():
    X, Y, day, coin, nd, nc = synth_panel()
    bt = R.boot_fe(X, Y, day, coin, nd, nc, 4, 300, 20261127, 0)[:, 0]
    iv = R.interval(bt, float(R.fe_coefs(X, Y, day, nd, coin, nc)[0, 0]))
    assert iv["lo"] < 2.0 < iv["hi"] and iv["lo"] > 0 and iv["p"] < 0.01
    X0, Y0, day0, coin0, nd0, nc0 = synth_panel(slope=0.0, seed=5)
    bt0 = R.boot_fe(X0, Y0, day0, coin0, nd0, nc0, 4, 300, 20261127, 0)[:, 0]
    iv0 = R.interval(bt0, float(R.fe_coefs(X0, Y0, day0, nd0, coin0, nc0)[0, 0]))
    assert iv0["lo"] < 0 < iv0["hi"]


def test_run_reg_win_loss_kinds():
    X, Y, day, coin, nd, nc = synth_panel(per_day=25)
    Xf = np.column_stack([X[:, 0], np.zeros(len(day)), np.zeros(len(day)), np.ones(len(day)) * 0.1 + np.random.default_rng(1).normal(0, 0.01, len(day))])
    P = dict(X=Xf, y=Y[:, 0], day=day, coin=coin, nd=nd, nc=nc)
    _, r_all = AN.run_reg((None, P, "all", "fe", 4, 100))
    _, r_win = AN.run_reg((None, P, "win", "fe", 4, 100))
    _, r_loss = AN.run_reg((None, P, "loss", "fe", 4, 100))
    assert r_all["n_rows"] == len(day) and r_win["n_rows"] + r_loss["n_rows"] == len(day)
    assert abs(r_all["point"] - 2.0) < 0.3


def test_thirds_in_day_ranks():
    x = np.array([5.0, 1.0, 3.0, 9.0, 7.0, 2.0, 4.0, 6.0, 8.0, np.nan])
    day = np.array([0, 0, 0, 1, 1, 1, 1, 1, 1, 1])
    th = R.thirds_in_day(x, day)
    assert th[1] == 0 and th[2] == 1 and th[0] == 2 and th[9] == -1
    assert set(th[3:9]) == {0, 1, 2} and (th[3:9] == 2).sum() == 2


def test_forecast_sign_and_vstar():
    n = 40
    d = dict(eday=np.r_[np.full(n, 40), np.full(n, 200), np.full(n, 220)], V=np.r_[np.full(n, 0.005), np.full(n, 0.008), np.full(n, 0.003)],
             F2=np.ones(3 * n), turn24=np.ones(3 * n), pnl=np.r_[np.full(n, 1.0), np.full(n, 3.0), np.full(n, -1.0)])
    rows = AN.forecast_rows(d, 2.0, "FJ", ("jul", "aug"), ())
    jul = [r for r in rows if r["period"] == "jul"][0]
    aug = [r for r in rows if r["period"] == "aug"][0]
    # jul (сутки 181–211, день 200): V̄ 0,8 против 0,5 → +0,6, факт +2 → знак верен; aug (день 220): V̄ 0,3 → −0,4, факт −2 → верен
    assert jul["correct"] is True and abs(jul["pred"] - 0.6) < 1e-9 and abs(jul["actual"] - 2.0) < 1e-9
    assert aug["correct"] is True and aug["pred"] < 0 and aug["actual"] < 0


def test_decide_g2_outcomes():
    iv = lambda lo, hi, pt: dict(lo=lo, hi=hi, point=pt)
    base = lambda: {h: dict(alpha6=iv(0.5, 2, 1), alpha21=iv(0.4, 2, 1), win=iv(0.1, 2, 1.0), loss=iv(-0.5, 0.4, 0.0)) for h in ("FJ", "SEL")}
    r = base()
    r["fc"] = {"FJ": dict(correct=5, total=5), "SEL": dict(correct=5, total=5)}
    assert AN.decide_g2(r)["outcome"] == "подтверждена"
    r["fc"]["SEL"] = dict(correct=4, total=5)
    assert AN.decide_g2(r)["outcome"] == "между"
    r2 = base()
    r2["SEL"]["alpha6"] = iv(-0.2, 1.5, 0.5)
    r2["fc"] = {"FJ": dict(correct=5, total=5), "SEL": dict(correct=5, total=5)}
    assert AN.decide_g2(r2)["outcome"] == "опровергнута"


def test_dedupe_family_counts_identical_trades_once():
    tr = dict(cid=np.array([7180015, 7180016, 7180017, 758805]), sym=np.array(["A", "A", "B", "A"]), sig_ms=np.array([1, 1, 2, 1]), entry_ms=np.array([5, 5, 6, 5]),
              exit_ms=np.array([9, 9, 10, 9]), pnl=np.array([1.0, 1.0, 2.0, 1.0]))
    idx, n_raw, n_dup = AN.dedupe_family(tr)
    assert n_raw == 3 and n_dup == 1 and list(idx) == [0, 2]


def test_feat_task_detects_top_minus_bottom_effect():
    rng = np.random.default_rng(2)
    nd, per = 60, 30
    day = np.repeat(np.arange(nd), per)
    x = rng.normal(size=nd * per)
    y = 0.5 * x + rng.normal(size=nd * per)
    _, f, r = AN.feat_task((("O1", "FJ"), "F2", x, y, day, nd, 4, 300))
    assert r["point"] > 0.5 and r["lo"] > 0
    yb = rng.normal(size=nd * per)
    _, f, r0 = AN.feat_task((("O1", "FJ"), "F2", x, yb, day, nd, 4, 300))
    assert r0["lo"] < 0 < r0["hi"]


def test_holm_pass_requires_same_sign_on_second_half():
    mk = lambda pt, p: dict(point=pt, p=p)
    first = {f: mk(1.0, 0.5) for f in AN.FNAMES}
    second = {f: mk(1.0, 0.5) for f in AN.FNAMES}
    first["F2"] = mk(2.0, 1e-9)
    first["F4"] = mk(2.0, 1e-9)
    second["F2"] = mk(1.0, 0.1)
    second["F4"] = mk(-1.0, 0.1)
    first["F3"] = dict(spread=1.0, p=0.5, bin_means=[1, 2, 3, 4, 5, 6])
    second["F3"] = dict(spread=1.0, p=0.5, bin_means=[1, 2, 3, 4, 5, 6])
    out = AN.holm_pass(first, second)
    assert out["F2"]["passed"] is True and out["F4"]["passed"] is False and out["F4"]["same_sign"] is False
    assert out["F1"]["passed"] is False
