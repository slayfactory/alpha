"""TK-719: масштабы размера (c₁, κ, m₃), кросс-секционный ранг фандинга, σ̂ по 1-мин свечам, Холм m = 4, таблицы остановки, пик ставок, чтение сделок и фандинга по малым файлам."""
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
import tk719_size as Z  # noqa: E402

D0 = Z.D0_MS
DAY = Z.DAY_MS


def test_solve_scale_mean_one():
    rng = np.random.default_rng(1)
    x = rng.uniform(0.0, 2.0, 500)
    w = np.ones(500)
    fixed = np.zeros(500, bool)
    fixed[:50] = True
    c = Z.solve_scale(x, w, fixed, 0.0, 2.0)
    s = np.where(fixed, 1.0, np.clip(c * x, 0.0, 2.0))
    assert abs(float((w * s).sum() / w.sum()) - 1.0) < 1e-9


def test_solve_scale_weighted_and_unreachable():
    x = np.array([1.0, 3.0])
    w = np.array([3.0, 1.0])
    c = Z.solve_scale(x, w, np.zeros(2, bool), 0.0, 100.0)
    assert abs((3 * c * 1 + 1 * c * 3) / 4 - 1.0) < 1e-9                    # c = 1 / 1,5
    with pytest.raises(ValueError):
        Z.solve_scale(np.array([1.0, 1.0]), np.ones(2), np.zeros(2, bool), 1.5, 2.0)   # нижняя граница уже выше 1


def feat_sample(n=400, seed=2, nan_u=40, nan_s=30):
    rng = np.random.default_rng(seed)
    u = rng.uniform(0.0, 1.0, n)
    sg = rng.lognormal(math.log(0.0007), 0.5, n)
    u[:nan_u] = np.nan
    sg[n - nan_s:] = np.nan
    return u, sg


def test_fit_scales_all_variants_mean_one():
    u, sg = feat_sample()
    w = np.ones(len(u))
    sc = Z.fit_scales(u, sg, w)
    for s in Z.sizes(u, sg, sc):
        assert abs(float(s.mean()) - 1.0) < 1e-7
        assert s.min() >= 0.0 and s.max() <= Z.CAP + 1e-12
    s1, s2, s3 = Z.sizes(u, sg, sc)
    assert s2.min() >= Z.FLOOR - 1e-12                                       # нижняя граница только у 1/σ
    assert np.all(s1[:40] == 1.0)                                             # нет u — размер 1
    assert np.all(s2[-30:] == 1.0)                                            # нет σ̂ — размер 1
    assert np.all(s3[:40] == 1.0) and np.all(s3[-30:] == 1.0)                 # V3 — только при обоих признаках
    ok = np.isfinite(u) & np.isfinite(sg)
    assert np.all(np.diff(s1[ok][np.argsort(u[ok])]) >= -1e-12)               # V1 не убывает по рангу фандинга


def test_sizes_missing_feature_is_one():
    u = np.array([np.nan, 0.5, np.nan])
    sg = np.array([0.001, np.nan, np.nan])
    sc = dict(c1=1.0, kappa=0.001, m3=1.0)
    s1, s2, s3 = Z.sizes(u, sg, sc)
    assert list(s1) == [1.0, 1.0, 1.0]                                       # c1 · 2 · 0,5 = 1
    assert list(s2) == [1.0, 1.0, 1.0]
    assert list(s3) == [1.0, 1.0, 1.0]
    s1, _, _ = Z.sizes(np.array([0.0, 0.25, 1.0]), np.array([1.0] * 3), sc)
    assert list(s1) == [0.0, 0.5, 2.0]


def test_cross_rank_basic_and_gate():
    n = 150
    M = np.tile(np.arange(n, dtype=float), (3, 1))                           # 3 сигнала, 150 монет, ставки 0…149
    M[1, 100:] = np.nan                                                      # N_t = 100 — на границе, годится
    M[2, 99:] = np.nan                                                       # N_t = 99 — не годится
    col = np.array([30, 30, 30])
    u, nt = Z.cross_rank(M, col)
    assert list(nt) == [150, 100, 99]
    assert abs(u[0] - (30 + 0.5) / 150) < 1e-12
    assert abs(u[1] - (30 + 0.5) / 100) < 1e-12
    assert np.isnan(u[2])
    u2, _ = Z.cross_rank(M[:1], np.array([-1]))                              # монеты нет в c539
    assert np.isnan(u2[0])
    M[0, 5] = M[0, 30]                                                       # ничья: 0,5 × равных (включая саму)
    u3, _ = Z.cross_rank(M[:1], np.array([30]))
    assert abs(u3[0] - (29 + 0.5 * 2) / 150) < 1e-12                         # ниже 30 — 29 монет (0…29 без 5 → 29), равных 2


def test_funding_matrix_age_and_order():
    h = 3600 * 1000
    data = {"A": (np.array([0, 8 * h, 16 * h], np.int64), np.array([0.1, 0.2, 0.3])), "B": (np.array([0], np.int64), np.array([0.5])), "C": (np.array([], np.int64), np.array([]))}
    sigs = np.array([-1, 0, 7 * h, 8 * h, 9 * h, 9 * h + 1, 25 * h, 26 * h], np.int64)
    M = Z.funding_matrix(["A", "B", "C"], sigs, lambda s: data[s])
    nan = np.nan
    np.testing.assert_array_equal(M[:, 0], [nan, 0.1, 0.1, 0.2, 0.2, 0.2, 0.3, nan])       # возраст ≤ 9 ч — ставка есть, 10 ч — NaN
    np.testing.assert_array_equal(M[:, 1], [nan, 0.5, 0.5, 0.5, 0.5, nan, nan, nan])
    assert np.isnan(M[:, 2]).all()


def write_klines_day(root, sym, date, minute0, closes):
    d = os.path.join(root, sym)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, date + ".csv"), "w", encoding="utf-8", newline="") as f:
        f.write("open_time,open,high,low,close,volume\n")
        for k, c in enumerate(closes):
            f.write("%d,%.8f,%.8f,%.8f,%.8f,1\n" % ((minute0 + k) * 60000, c, c, c, c))


def date_of(day):
    return (dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=day)).strftime("%Y-%m-%d")


def make_series(tmp_path, sym, i_sig, n_days_back=3, seed=5, drop=None):
    """1-мин ряд закрытий на n суток до сигнала; drop — индексы минут без свечи. Возвращает close по номеру минуты."""
    rng = np.random.default_rng(seed)
    first = (i_sig // 1440 - n_days_back) * 1440
    last = (i_sig // 1440 + 1) * 1440
    mins = np.arange(first, last)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.001, len(mins))))
    close = dict(zip(mins.tolist(), c.tolist()))
    for m in (drop or []):
        close.pop(int(m), None)
    for day in range(first // 1440, last // 1440):
        ms = [m for m in range(day * 1440, (day + 1) * 1440) if m in close]
        if ms:
            d = os.path.join(str(tmp_path), sym)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, date_of(day) + ".csv"), "w", encoding="utf-8", newline="") as f:
                f.write("open_time,open,high,low,close,volume\n")
                for m in ms:
                    f.write("%d,%.10f,%.10f,%.10f,%.10f,1\n" % (m * 60000, close[m], close[m], close[m], close[m]))
    return close


def test_sigma_hat_matches_direct(tmp_path):
    i = 20500 * 1440 + 777
    close = make_series(tmp_path, "AAAUSDT", i)
    sg, cov = Z.sigma_hat("AAAUSDT", i * 60000 + 12345, [str(tmp_path)], {})
    r = np.array([math.log(close[m] / close[m - 1]) for m in range(i - 1440, i)])      # бары [i−1440, i−1] против предыдущих
    assert cov == 1.0
    assert abs(sg - math.sqrt(float((r * r).sum()) / 1440)) < 1e-12
    sg2, _ = Z.sigma_hat("AAAUSDT", i * 60000, [str(tmp_path)], {})            # сигнал точно на границе минуты i: бар i не закрыт, не входит
    assert sg2 == sg


def test_sigma_hat_gaps_and_coverage(tmp_path):
    i = 20500 * 1440 + 300
    drop = list(range(i - 1440 - 1, i - 1440 + 5))                           # шесть минут в начале окна — охват ≥ 0,7, пропуск = доходность 0
    close = make_series(tmp_path, "BBBUSDT", i, drop=drop)
    sg, cov = Z.sigma_hat("BBBUSDT", i * 60000, [str(tmp_path)], {})
    assert 0.99 < cov < 1.0 and math.isfinite(sg)
    drop_big = list(range(i - 1000, i - 1))                                  # 999 минут нет — охват < 0,7
    make_series(tmp_path, "CCCUSDT", i, drop=drop_big)
    sg3, cov3 = Z.sigma_hat("CCCUSDT", i * 60000, [str(tmp_path)], {})
    assert np.isnan(sg3) and cov3 < Z.MIN_COV
    sg4, cov4 = Z.sigma_hat("NOFILEUSDT", i * 60000, [str(tmp_path)], {})   # нет файлов
    assert np.isnan(sg4) and cov4 == 0.0


def test_holm_m4():
    r = Z.holm_m(np.array([0.001, 0.02, 0.5]))                               # α = 0,025: пороги 0,025 / 4, / 3, / 2
    assert [x[0] for x in r] == [True, False, False]
    assert abs(r[0][1] - 0.025 / 4) < 1e-12 and abs(r[1][1] - 0.025 / 3) < 1e-12 and abs(r[2][1] - 0.025 / 2) < 1e-12
    r2 = Z.holm_m(np.array([0.006, 0.0075, 0.01]))
    assert [x[0] for x in r2] == [True, True, True]                          # 0,006 ≤ 0,00625; 0,0075 ≤ 0,00833; 0,01 ≤ 0,0125
    r3 = Z.holm_m(np.array([0.3, 0.001, 0.002]))                             # порядок входа сохраняется
    assert [x[0] for x in r3] == [False, True, True]


def test_kill_tables_shapes_and_rate():
    rng = np.random.default_rng(3)
    dexp = rng.normal(0, 1.0, 136)
    dsized = rng.normal(0.2, 3.0, 136)
    idx = Z.boot_idx(136, 1, B=500)
    Q, DQ, rate = Z.kill_tables(dexp, dsized, idx, 95)
    assert Q.shape == (Z.KILL_N,) and DQ.shape == (Z.KILL_N,)
    assert np.all(Q >= 0) and np.all(DQ >= 0)
    assert 0.0 <= rate <= 1.0
    Q99, DQ99, rate99 = Z.kill_tables(dexp, dsized, idx, 99)
    assert np.all(Q99 >= Q - 1e-12) and rate99 <= rate + 1e-12              # строже уровень — реже ложные остановки


def test_peak_concurrent():
    ent = np.array([0, 5, 10], np.int64)
    ext = np.array([6, 8, 12], np.int64)
    w = np.array([1.0, 2.0, 4.0])
    w = np.array([1.0, 2.0, 0.5])
    assert Z.peak_concurrent(ent, ext, w) == 3.0                              # 1 + 2 при t = 5…6
    assert Z.peak_concurrent(np.array([0, 5], np.int64), np.array([5, 9], np.int64), np.array([1.0, 2.0])) == 2.0   # выход раньше входа в тот же момент
    assert Z.peak_concurrent(np.array([], np.int64), np.array([], np.int64), np.array([])) == 0.0


def write_trade_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("sym,sig_ms,entry_ms,exit_ms,gross,fees,funding\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def test_load_group_and_unique_keys(tmp_path):
    root, run = str(tmp_path), "r"
    t0 = 1000
    os.makedirs(os.path.join(root, run))
    json.dump({"wa": t0}, open(os.path.join(root, run, "t0.json"), "w"))
    day0 = 181 * DAY + D0 - t0                                               # вход в сутки 181 абсолютного времени
    cids = (7180015, 7180016)
    for cid in cids:
        rows = [("AAAUSDT", day0 + 10, day0 + 20, day0 + 30, 5.0, 1.0, 0.1), ("BBBUSDT", day0 + 15, day0 + 25, day0 + 35, -2.0, 0.5, 0.0)]
        if cid == cids[1]:
            rows.append(("AAAUSDT", day0 + 10, day0 + 20, day0 + 30, 5.0, 1.0, 0.1))        # то же (монета, сигнал) — вторая копия у этого члена
            rows.append(("CCCUSDT", DAY * 200 + D0 - t0, DAY * 200 + D0 - t0, DAY * 200 + D0 - t0 + 5, 1.0, 0.0, 0.0))   # сутки 200 — вне кредита окна
        write_trade_csv(os.path.join(root, run, "wa", "%d-trades.csv" % cid), rows)
    c = Z.load_group("sel", root=root, run=run, credit={"wa": (181, 190)}, cids=cids)
    assert set(c["cid"]) == set(cids)
    assert int(c["day"].min()) == 181 and int(c["day"].max()) == 181
    assert len(c["sym"]) == 5                                                # сутки 200 отрезаны кредитом
    assert np.all(np.diff(c["ent"][c["cid"] == cids[0]]) >= 0)
    assert int(c["sig"].min()) == 181 * DAY + D0 + 10                         # время сдвинуто на t0 окна
    usym, usig, inv = Z.unique_keys(c)
    assert len(usym) == 2                                                    # (AAA, сигнал) и (BBB, сигнал)
    assert all((s, int(g)) == (usym[j], usig[j]) for s, g, j in zip(c["sym"], c["sig"], inv))
    assert abs(float(c["val"].sum()) - 7.0) < 1e-12                          # gross − fees: (4 − 2,5) + (4 − 2,5 + 4)


def write_zip(path, name, lines):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, "calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join(lines) + "\n")


def test_load_funding_coin_merges_archive_and_rest(tmp_path):
    a, b = tmp_path / "arch", tmp_path / "fd"
    (a / "XUSDT").mkdir(parents=True)
    (b / "XUSDT").mkdir(parents=True)
    (b / "rest").mkdir()
    write_zip(str(a / "XUSDT" / "XUSDT-fundingRate-2026-05.zip"), "x.csv", ["1000,8,0.0001", "2000,8,0.0002"])
    write_zip(str(b / "XUSDT" / "XUSDT-fundingRate-2026-07.zip"), "x.csv", ["3000,8,0.0003"])
    (b / "rest" / "XUSDT.csv").write_text("calc_time,funding_interval_hours,last_funding_rate\n3000,8,0.0009\n4000,8,0.0004\n", encoding="utf-8")
    ts, rt = Z.load_funding_coin("XUSDT", dirs=(str(a), str(b)), rest=str(b / "rest"))
    assert list(ts) == [1000, 2000, 3000, 4000]
    assert list(rt) == [0.0001, 0.0002, 0.0009, 0.0004]                       # REST перекрывает архив на общей ставке
    ts2, rt2 = Z.load_funding_coin("NOPEUSDT", dirs=(str(a),), rest=str(b / "rest"))
    assert len(ts2) == 0 and len(rt2) == 0


def test_stats_rows_skips_unfinished_windows():
    rows = []
    pnl = np.ones(200)
    cnt = np.ones(200, np.int64)
    Z.stats_rows(rows, "B", pnl, cnt, ("A", "SEL"), 200)
    wins = {r[3] for r in rows}
    assert wins == {"A"}                                                      # SEL (181…317) за пределами 200 суток — пропуск
    assert any(r[4] == "sum" and r[5] == 89.0 for r in rows)


# ───────────────────────────── сквозной прогон по синтетике ─────────────────────────────

def make_group(root, run, credit, t0_of, cids, rng, coins, per_day=4):
    """Сделки одинаковы у всех членов (общие сигналы), P&L случайный; время в файле — от t0 окна."""
    os.makedirs(os.path.join(root, run), exist_ok=True)
    json.dump(t0_of, open(os.path.join(root, run, "t0.json"), "w"))
    for win, (lo, hi) in credit.items():
        rows = []
        for day in range(lo, hi + 1):
            for k in range(per_day):
                sym = coins[int(rng.integers(len(coins)))]
                sig = (day * DAY + D0 + int(rng.integers(0, 80000000))) - t0_of[win]
                ent = sig + 60000
                rows.append((sym, sig, ent, ent + 600000 + int(rng.integers(0, 3600000)), round(float(rng.normal(0.3, 4.0)), 4), 0.2, round(float(rng.normal(0, 0.05)), 4)))
        for cid in cids:
            write_trade_csv(os.path.join(root, run, win, "%d-trades.csv" % cid), rows)


def make_feat(Zm, grp, root, run, rng):
    c = Zm.load_group(grp, root=root, run=run)
    usym, usig, _ = Zm.unique_keys(c)
    n = len(usym)
    F = dict(usym=usym, usig=usig, u=np.where(rng.random(n) < 0.1, np.nan, rng.random(n)), nt=np.full(n, 300), sigma=np.where(rng.random(n) < 0.05, np.nan, rng.lognormal(math.log(0.0007), 0.4, n)), scov=np.ones(n))
    os.makedirs(Zm.FEAT, exist_ok=True)
    np.savez_compressed(Zm.feat_path(grp), **F)


def test_end_to_end_freeze_and_test(tmp_path, monkeypatch):
    root = str(tmp_path / "p")
    rng = np.random.default_rng(11)
    coins = ["C%02dUSDT" % k for k in range(40)]
    monkeypatch.setattr(Z, "ROOT", root)
    monkeypatch.setattr(Z, "DB", root + "/tk719.sqlite")
    monkeypatch.setattr(Z, "FEAT", root + "/feat")
    monkeypatch.setattr(Z, "FREEZE", root + "/freeze/tk719-frozen.json")
    runs = {"h1": (root + "/h1", "r"), "sel": (root + "/trades", "rs"), "n1": (root + "/trades", "rn")}
    monkeypatch.setattr(Z, "RUNS", runs)
    t0 = lambda cr: {w: D0 + lo * DAY for w, (lo, hi) in cr.items()}               # noqa: E731
    for grp in ("h1", "sel", "n1"):
        cr = Z.CREDIT[grp]
        make_group(runs[grp][0], runs[grp][1], cr, t0(cr), Z.MEMBERS, rng, coins)
        make_feat(Z, grp, runs[grp][0], runs[grp][1], rng)
    # сделки SEL / N1 уже есть — заморозка отказывает
    with pytest.raises(SystemExit):
        Z.main(["freeze", "--code-commit", "deadbeef"])
    # заморозка до движка: каталог сделок SEL / N1 пуст
    import shutil
    shutil.move(root + "/trades", root + "/trades_hold")
    monkeypatch.setattr(Z, "RUNS", {"h1": runs["h1"], "sel": (root + "/trades_hold", "rs"), "n1": (root + "/trades_hold", "rn")})
    assert Z.main(["freeze", "--code-commit", "deadbeef"]) == 0
    fz = json.load(open(Z.FREEZE, encoding="utf-8"))
    assert fz["code_commit"] == "deadbeef" and fz["scales"]["c1"] > 0 and fz["scales"]["kappa"] > 0 and fz["scales"]["m3"] > 0
    assert fz["nt_fj_mean"] == 300 and set(fz["low_power"]) == {"V1", "V2", "V3"}
    import sqlite3
    con = sqlite3.connect(Z.DB)
    assert con.execute("SELECT count(*) FROM tk719_row WHERE test='FJ' AND stat='sbar'").fetchone()[0] == 12       # 3 варианта × 4 окна
    assert abs(con.execute("SELECT value FROM tk719_row WHERE test='FJ' AND series='V1' AND win='FJ' AND stat='sbar'").fetchone()[0] - 1.0) < 0.05
    # SEL / N1
    assert Z.main(["test", "--freeze", Z.FREEZE]) == 0
    con = sqlite3.connect(Z.DB)
    v = con.execute("SELECT value, txt FROM tk719_row WHERE test='V' AND stat='outcome'").fetchone()
    assert v[0] in (-1.0, 0.0, 1.0)
    for k in Z.VARIANTS:
        assert con.execute("SELECT count(*) FROM tk719_row WHERE test='K' AND series=? AND stat IN ('K1','K2','K3','K4','K5','K6','ALL')", (k,)).fetchone()[0] == 7
        for st in ("delta_plain", "delta_exp", "sbar"):
            assert con.execute("SELECT count(*) FROM tk719_row WHERE test='D' AND series=? AND stat=?", (k, st)).fetchone()[0] == 4
        assert con.execute("SELECT count(*) FROM tk719_row WHERE test='KILL' AND series=? AND win='q95' AND stat LIKE 'Q_n%'", (k,)).fetchone()[0] == Z.KILL_N
        lvl = con.execute("SELECT value FROM tk719_row WHERE test='KILL' AND series=? AND win='chosen'", (k,)).fetchone()[0]
        assert lvl in (95.0, 99.0)
    # Δ_plain − Δ_exp = (s̄ − 1) · Σ B на SEL
    for k in Z.VARIANTS:
        sb = con.execute("SELECT value FROM tk719_row WHERE test='D' AND series=? AND win='SEL' AND stat='sbar'", (k,)).fetchone()[0]
        dp = con.execute("SELECT value FROM tk719_row WHERE test='D' AND series=? AND win='SEL' AND stat='delta_plain'", (k,)).fetchone()[0]
        de = con.execute("SELECT value FROM tk719_row WHERE test='D' AND series=? AND win='SEL' AND stat='delta_exp'", (k,)).fetchone()[0]
        b = con.execute("SELECT value FROM tk719_row WHERE test='T1' AND series='B' AND win='SEL' AND stat='sum'").fetchone()
        assert b is not None and abs(sb - 1.0) > 1e-9
        assert abs((dp - de) / (sb - 1.0) - b[0]) < 1e-6
