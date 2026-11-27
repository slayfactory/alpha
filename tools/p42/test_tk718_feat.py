"""TK-718 T6: признаки сделки против прямого расчёта, успех «+1R раньше стопа», фандинг, корреляция рынка, терцили, нули перестановок / сдвигов, эпизоды, сквозной прогон стадии 3 по синтетике."""
import datetime as dt
import json
import math
import os
import sys
import zipfile

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")
import tk692_walk as w  # noqa: E402
import tk718_feat as FT  # noqa: E402
import tk718_tests as T  # noqa: E402
from test_tk718 import build_db  # noqa: E402


def write_klines(root, sym, ndays=60, seed=3, win="m01"):
    """Синтетические 1-мин свечи: случайное блуждание с 12.12.2025; возвращает (open, high, low, close, qv) на сетке FT."""
    rng = np.random.default_rng(seed)
    n = ndays * 1440
    c = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    o = np.concatenate([[100.0], c[:-1]])
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0005, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0005, n)))
    qv = rng.uniform(1000, 5000, n)
    o, h, l, c, qv = np.round(o, 8), np.round(h, 8), np.round(l, 8), np.round(c, 8), np.round(qv, 4)   # как в файле (%.8f / %.4f)
    d = os.path.join(root, win, "klines", sym)
    os.makedirs(d, exist_ok=True)
    for k in range(ndays):
        day = (dt.date(2025, 12, 12) + dt.timedelta(days=k)).isoformat()
        lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,tbv,tbq,ignore"]
        for j in range(k * 1440, (k + 1) * 1440):
            t = FT.G0_MS + j * 60000
            lines.append("%d,%.8f,%.8f,%.8f,%.8f,1,%d,%.4f,1,0,0,0" % (t, o[j], h[j], l[j], c[j], t + 59999, qv[j]))
        with open(os.path.join(d, day + ".csv"), "w") as f:
            f.write("\n".join(lines))
    return o, h, l, c, qv


def test_coin_features_match_bruteforce(tmp_path):
    root = str(tmp_path / "kl")
    o, h, l, c, qv = write_klines(root, "AAAUSDT", ndays=60)
    sig_min = np.array([45 * 1440 + 100, 50 * 1440 + 777])
    sig = FT.G0_MS + sig_min * 60000 + 5000                                   # внутри минуты → окна до минуты sig_min (исключая её)
    ent, ext = sig + 120_000, sig + 3_600_000
    roots = {"kl": root, "fund": str(tmp_path / "nf"), "oi": str(tmp_path / "no")}
    res = FT.coin_job(("AAAUSDT", {"short": (sig, ent, ext)}, roots))
    f = res["trades"]["short"]["f"]
    r = np.concatenate([[np.nan], np.log(c[1:] / c[:-1])])
    for k, i in enumerate(sig_min):
        want_vol = math.sqrt(np.mean(r[i - 60:i] ** 2) / np.mean(r[i - 43200:i] ** 2))
        assert f["volratio"][k] == pytest.approx(want_vol, rel=1e-9)
        assert f["jag"][k] == pytest.approx((r[i - 60:i] ** 2).max() / (r[i - 60:i] ** 2).sum(), rel=1e-9)
        assert f["turnover"][k] == pytest.approx(qv[i - 1440:i].sum(), rel=1e-9)
        for q, nm in ((15, "vr15"), (60, "vr60")):
            rq = np.concatenate([np.full(q, np.nan), np.log(c[q:] / c[:-q])])
            vq = rq[i - 4320 + q:i]
            v1 = r[i - 4320 + 1:i]
            assert f[nm][k] == pytest.approx(vq.var() / (q * v1.var()), rel=1e-9)
        assert np.isnan(f["funding"][k]) and np.isnan(f["doi"][k])                # нет архивов → «нет»
    t5 = FT.G0_MS + 5 * 1440 * 60000                                           # сигнал на 5-е сутки: 30-суточное окно не укладывается
    res2 = FT.coin_job(("AAAUSDT", {"long": (np.array([t5]), np.array([t5 + 60000]), np.array([t5 + 86_400_000]))}, roots))
    assert np.isnan(res2["trades"]["long"]["f"]["volratio"][0])
    assert res["have"] and res["vrat"].shape == (FT.NH,) and res["c5"].shape == (FT.N5,)


def test_hourly_grid_uses_only_past_minutes(tmp_path):
    root = str(tmp_path / "kl")
    o, h, l, c, qv = write_klines(root, "AAAUSDT", ndays=60)
    res = FT.coin_job(("AAAUSDT", {}, {"kl": root, "fund": str(tmp_path / "nf"), "oi": str(tmp_path / "no")}))
    hr = 45 * 24 + 13                                                          # час с 45-х суток от G0
    e = hr * 60
    r = np.concatenate([[np.nan], np.log(c[1:] / c[:-1])])
    want = math.sqrt(np.mean(r[e - 1440:e] ** 2) / np.mean(r[e - 43200:e] ** 2))
    assert float(res["vrat"][hr]) == pytest.approx(want, rel=1e-5)
    assert float(res["ret24"][hr]) == pytest.approx(c[e - 1] / c[e - 1 - 1440] - 1.0, rel=1e-5)
    assert np.isnan(res["vrat"][10 * 24])                                      # история < 30 сут.


def test_trade_success_rules():
    n = 30
    o = np.full(n, 100.0)
    h = np.full(n, 100.5)
    l = np.full(n, 99.5)
    l2, h2 = l.copy(), h.copy()
    l2[5] = 95.0                                                               # шорт, стоп 4 %: цель 96 — в минуте 5, стопа нет
    assert FT.trade_success(o, h2, l2, np.array([0]), np.array([10]), "short", 4.0)[0] == 1.0
    h3 = h2.copy()
    h3[5] = 105.0                                                              # обе границы в одной минуте → «не дошёл»
    assert FT.trade_success(o, h3, l2, np.array([0]), np.array([10]), "short", 4.0)[0] == 0.0
    h4 = h2.copy()
    h4[3] = 105.0                                                              # стоп раньше цели
    assert FT.trade_success(o, h4, l2, np.array([0]), np.array([10]), "short", 4.0)[0] == 0.0
    assert FT.trade_success(o, h2, l2, np.array([0]), np.array([4]), "short", 4.0)[0] == 0.0    # выход до цели
    h5 = h.copy()
    h5[2] = 103.5                                                              # лонг, стоп 3 %: цель 103
    assert FT.trade_success(o, h5, l, np.array([0]), np.array([10]), "long", 3.0)[0] == 1.0


def test_funding_zip_and_last_before(tmp_path):
    d = tmp_path / "fund" / "AAAUSDT"
    d.mkdir(parents=True)
    with zipfile.ZipFile(d / "AAAUSDT-fundingRate-2026-01.zip", "w") as z:
        z.writestr("AAAUSDT-fundingRate-2026-01.csv", "calc_time,funding_interval_hours,last_funding_rate\n1000,8,0.0001\n30001000,8,-0.0002\n")
    ts, rt = FT.load_funding("AAAUSDT", str(tmp_path / "fund"))
    assert list(ts) == [1000, 30001000] and rt[1] == -0.0002
    v = FT.last_before(ts, rt, np.array([500, 2000, 30_001_000 + 100, 30_001_000 + 10 * 3600 * 1000]), 9 * 3600 * 1000)
    assert np.isnan(v[0]) and v[1] == 0.0001 and v[2] == -0.0002 and np.isnan(v[3])


def test_open_interest_zip(tmp_path):
    d = tmp_path / "oi" / "AAAUSDT"
    d.mkdir(parents=True)
    with zipfile.ZipFile(d / "AAAUSDT-metrics-2026-01-19.zip", "w") as z:
        z.writestr("AAAUSDT-metrics-2026-01-19.csv", "create_time,symbol,sum_open_interest,sum_open_interest_value\n2026-01-19 00:00:00,AAAUSDT,1,100.0\n2026-01-19 00:05:00,AAAUSDT,1,110.0\n")
    ts, v = FT.load_oi("AAAUSDT", str(tmp_path / "oi"))
    assert len(ts) == 2 and v[1] == 110.0 and ts[1] - ts[0] == 300_000


def test_mean_pairwise_corr_matches_corrcoef():
    rng = np.random.default_rng(11)
    f = rng.normal(size=(300, 1))
    A = 0.6 * f + rng.normal(size=(300, 40))
    got, n = FT.mean_pairwise_corr(A, 0, 300)
    C = np.corrcoef(A.T)
    assert n == 40 and got == pytest.approx((C.sum() - 40) / (40 * 39), abs=1e-12)
    B = A.copy()
    B[10, 3] = np.nan                                                          # монета с пропуском выпадает из окна
    got2, n2 = FT.mean_pairwise_corr(B, 0, 300)
    assert n2 == 39 and got2 == pytest.approx((np.corrcoef(np.delete(A, 3, axis=1).T).sum() - 39) / (39 * 38), abs=1e-12)
    assert np.isnan(FT.mean_pairwise_corr(A[:, :10], 0, 300)[0])                 # < 20 монет


def test_corr_beta():
    rng = np.random.default_rng(12)
    b = rng.normal(0, 1, 864)
    c = 1.5 * b + rng.normal(0, 0.5, 864)
    beta, rho = FT.corr_beta(c, b)
    assert beta == pytest.approx(np.cov(b, c, ddof=0)[0, 1] / b.var(), rel=1e-9) and 0.9 < rho < 1.0
    c2 = c.copy()
    c2[:400] = np.nan
    assert np.isnan(FT.corr_beta(c2, b)[0])                                    # охват < 0,7


def test_episodes():
    ind = np.zeros(150, bool)
    ind[10:25] = True                                                          # 15 сут.
    ind[27:32] = True                                                          # разрыв 2 склеивается: 10…31 (22 сут.)
    ind[60:65] = True                                                          # 5 сут. — коротко
    ind[100:108] = True
    ind[112:118] = True                                                        # разрыв 4 > 3 не склеивается; 8 и 6 сут. — обе коротки
    assert FT.episodes(ind) == 1
    ind[108:112] = True
    assert FT.episodes(ind) == 2


def test_terciles_and_tercile_stats():
    v = np.arange(100.0)
    q = FT.terciles(v)
    lab = FT.labels(np.array([0.0, 33.0, 34.0, 66.0, 67.0, 99.0, np.nan]), q)
    assert list(lab) == [0, 0, 1, 1, 2, 2, 3]
    lab = np.array([0] * 40 + [1] * 40 + [2] * 40)
    val = np.concatenate([np.full(40, 1.0), np.full(40, 2.0), np.full(40, 5.0)])
    n, s, sp = FT.tercile_stats(lab, val)
    assert list(n) == [40, 40, 40] and sp == pytest.approx(4.0)
    assert FT.tercile_stats(lab[:100], val[:100])[2] is None                    # в третьем терциле 20 < 30


def test_perm_nulls_coin_and_market():
    rng = np.random.default_rng(13)
    n = 1500
    day = np.sort(rng.integers(31, 181, n)).astype(float)
    lab_plant = rng.integers(0, 3, n)
    lab_null = rng.integers(0, 3, n)
    val = rng.normal(0, 4, n) + 2.0 * lab_plant                               # зависимость от plant, не от null
    sp = FT.perm_spreads_coin(np.stack([lab_plant, lab_null]), val, day, 400, np.random.default_rng(1))
    obs = [FT.tercile_stats(lab, val)[2] for lab in (lab_plant, lab_null)]
    assert ((sp[:, 0] >= obs[0]).sum() + 1) / 401 < 0.01 and ((sp[:, 1] >= obs[1]).sum() + 1) / 401 > 0.05
    h_lo, h_hi = 31 * 24 + 480, 181 * 24 + 480                                 # рынок: сделка зависит от метки часа своей сетки
    grid = np.zeros((2, FT.NH), np.int64)
    grid[0], grid[1] = rng.integers(0, 3, FT.NH), rng.integers(0, 3, FT.NH)
    hidx = rng.integers(h_lo, h_hi, n)
    val2 = rng.normal(0, 4, n) + 2.0 * grid[0, hidx]
    spm = FT.perm_spreads_market(grid, hidx, val2, h_lo, h_hi, 400, np.random.default_rng(2))
    o0, o1 = (FT.tercile_stats(grid[k, hidx], val2)[2] for k in (0, 1))
    assert ((spm[:, 0] >= o0).sum() + 1) / 401 < 0.02 and ((spm[:, 1] >= o1).sum() + 1) / 401 > 0.05


def test_perm_coin_preserves_within_day_label_counts():
    """Перемешивание внутри суток не меняет число меток по суткам (проверка на искусственном признаке «метка = номер суток mod 3»)."""
    day = np.repeat(np.arange(31, 61), 40).astype(float)
    lab = (day.astype(int) % 3)[None, :].astype(np.int64)
    val = np.arange(len(day), dtype=float)
    sp = FT.perm_spreads_coin(lab, val, day, 50, np.random.default_rng(5))
    assert np.allclose(sp, sp[0])                                              # метка постоянна внутри суток → любая перестановка внутри суток даёт тот же разброс


def test_succ30_history_rule():
    day = 86_400_000
    tr = dict(sym=np.array(["A"] * 6 + ["B"]), sig_ms=np.array([i * day for i in (1, 5, 8, 12, 14, 40)] + [14 * day]), exit_ms=np.array([i * day + 1000 for i in (2, 6, 9, 13, 15, 41)] + [15 * day]))
    succ = np.array([1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0])
    out = FT.succ30(tr, succ)
    assert np.isnan(out[0]) and np.isnan(out[1]) and np.isnan(out[2])           # < 3 завершённых сделок
    assert out[3] == pytest.approx(2 / 3)                                       # к сигналу 12-х суток закрыты сделки 1, 5, 8: 1, 0, 1
    assert out[4] == pytest.approx(3 / 4)                                       # добавилась сделка 12-х суток: 1, 0, 1, 1
    assert np.isnan(out[5]) and np.isnan(out[6])                                # 40-е сутки: старше 30 сут.; монета B без истории


def test_t6_end_to_end_planted_feature(tmp_path, monkeypatch):
    """Сквозной прогон стадии 3: «turnover» связан с P&L у B и S1 — проходит Холм; остальные признаки — шум; спред — «нет данных» без счёта."""
    root = tmp_path / "trades"
    run = "tk718c-h1-A"
    db = str(tmp_path / "t6.sqlite")
    build_db(db)
    monkeypatch.setattr(T, "TRADES", str(root))
    S = T.Store(db)
    rng = np.random.default_rng(21)
    N = 2400
    sym = np.array(["C%02d" % i for i in rng.integers(0, 30, N)], dtype=object)
    sday = np.sort(rng.integers(10, 181, N))
    sig = T.D0_MS + sday.astype(np.int64) * T.DAY_MS + rng.integers(0, T.DAY_MS - 4 * 3600_000, N)
    ent, ext = sig + 60_000, sig + 3_600_000
    turn = rng.uniform(0, 1, N)
    val_all = rng.normal(0, 5, N) + 6.0 * (turn > 2 / 3)
    t0 = {win: T.D0_MS + (w.MONTH_START[mi] - 20) * T.DAY_MS for mi, win in enumerate(T.MONTH_NAMES)}
    (root / run).mkdir(parents=True)
    (root / run / "t0.json").write_text(json.dumps(t0))
    cids = {nm: S.plan["short", S.ckey("short", nm)]["cid"] for nm in S.members("short") + ["S1", "S2"]}
    eday = (ent - T.D0_MS) // T.DAY_MS
    for nm, cid in cids.items():
        for mi, win in enumerate(T.MONTH_NAMES):
            d = root / run / win
            d.mkdir(exist_ok=True)
            sel = np.nonzero((eday >= w.MONTH_START[mi]) & (eday < w.MONTH_START[mi + 1]))[0]
            lines = ["sym,sig_ms,entry_ms,exit_ms,gross,fees,funding,net,kind"]
            lines += ["%s,%d,%d,%d,%.6f,0.0,0,0,tp" % (sym[j], sig[j] - t0[win], ent[j] - t0[win], ext[j] - t0[win], val_all[j]) for j in sel]
            (d / ("%d-trades.csv" % cid)).write_text("\n".join(lines))
    F = {"short__t_sym": sym, "short__t_sig": sig, "short__t_ent": ent, "short__t_ext": ext, "short__succ": rng.integers(0, 2, N).astype(float)}
    for fk in FT.FEATS:
        F["short__" + fk] = rng.uniform(0, 1, N)
    F["short__turnover"] = turn
    for g in ("btc_volratio", "mkt_volratio", "btc_vr60", "mkt_corr", "btc_ret24", "btc_ret7", "breadth_up"):
        F["grid__" + g] = rng.uniform(0, 1, FT.NH)
    rows = []
    detail, hm = FT.t6_test(S, F, rows, nperm=1500)
    g = {(r[1], r[2], r[3], r[4]): r[5] for r in rows if r[0] == "T6"}
    assert g[("short", "B:turnover", "FJ", "holm_pass")] == 1.0 and g[("short", "S1:turnover", "FJ", "holm_pass")] == 1.0
    assert g[("short", "B:turnover", "FJ", "p")] == pytest.approx(1 / 1501)
    assert g[("short", "B:volratio", "FJ", "holm_pass")] == 0.0
    assert not any(k[1] == "B:spread" and k[3] == "p" for k in g) and any(r[0] == "T6cov" and r[2] == "B:spread" for r in rows)
    assert g[("short", "B:turnover", "FJ", "top_share_of_sum")] > 0.6
    assert g[("short", "S1:turnover", "FJ", "one_regime")] == 1.0
    eps = [r for r in rows if r[0] == "T6m" and r[4] == "episodes"]
    assert eps and all(r[5] >= 0 for r in eps)
    assert len(hm) == 2 * 17 or len(hm) > 40                                   # (B, S1, S2, L401*) × признаки с данными; точное число — по охвату
