"""TK-717 стадия A: геометрия недель и половин, ранги, T1 (матрица, бутстреп, перестановки), выбор по скользящему окну без заглядывания, совпадение накопленного окна с `tk692_walk.select`, Holm, фильтр капитала, исключение главной линии, сквозной прогон по синтетическому набору."""
import json
import math
import os
import sqlite3
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk692_walk as w  # noqa: E402
import tk717_stagea as t  # noqa: E402


def synth_pool(C=60, nd=181, seed=1, trade_p=0.4):
    rng = np.random.default_rng(seed)
    N = (rng.random((C, nd)) < trade_p).astype(np.int32) * rng.integers(1, 3, (C, nd)).astype(np.int32)
    X = rng.normal(0.2, 6.0, (C, nd)) * (N > 0)
    P = np.ones((C, 6), bool)
    return w.Pool(np.arange(1, C + 1) * 10, X, N, P, ["k%d" % i for i in range(C)], {})


def make_tk692(path, pool, side, drop_day=None, bump=0.0, rid="A-m06"):
    """Ссылка L-436 (`tk692_oos`) из ряда `tk692_walk.walk` — независимый от tk717_stagea расчёт накопленного окна."""
    picks, days, pnl, nn = w.walk(pool, side, "week", 1)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS tk692_oos(rid TEXT, side TEXT, step TEXT, emb INTEGER, day INTEGER, cid INTEGER, pnl REAL, n INTEGER)")
    k = 0
    for r in picks:
        if r["pick"] is not None and r["e"] > r["b"]:
            for d in range(r["b"], r["e"]):
                if d != drop_day:
                    con.execute("INSERT INTO tk692_oos VALUES(?,?,?,?,?,?,?,?)", (rid, side, "week", 1, d, r["pick"]["cid"], float(pnl[k]) + bump, int(nn[k])))
                k += 1
    con.commit()
    con.close()


def test_gate_expanding_stops_on_empty_or_short_reference_and_passes_on_match(tmp_path):
    pool = synth_pool(C=150, seed=21, trade_p=0.8)
    pre = t.Pre(pool)
    recs = t.walk_ab(pre, pool, "short", None)
    ok = str(tmp_path / "ok.sqlite")
    make_tk692(ok, pool, "short")
    n_ref, bad = t.gate_expanding(recs, ok, "short", pool.nd)
    assert n_ref == 170 and bad == []                                                      # сутки 11…180 — как в L-436
    empty = str(tmp_path / "empty.sqlite")
    make_tk692(empty, pool, "short", rid="другой-rid")                                     # не тот rid: ссылка пуста
    with pytest.raises(SystemExit):
        t.gate_expanding(recs, empty, "short", pool.nd)
    with pytest.raises(SystemExit):
        t.gate_expanding(recs, ok, "long", pool.nd)                                        # нет строк этой стороны
    short = str(tmp_path / "short.sqlite")
    make_tk692(short, pool, "short", drop_day=100)                                         # 169 суток вместо 170
    with pytest.raises(SystemExit):
        t.gate_expanding(recs, short, "short", pool.nd)
    shift = str(tmp_path / "shift.sqlite")
    make_tk692(shift, pool, "short", bump=0.5)                                             # P&L расходится → ворота видят расхождения
    n_ref, bad = t.gate_expanding(recs, shift, "short", pool.nd)
    assert n_ref == 170 and len(bad) == 170


def test_week_geometry():
    import datetime
    d0 = datetime.date(2026, 1, 1)
    assert (d0 + datetime.timedelta(days=t.WEEK0)).weekday() == 0                          # 05.01.2026 — понедельник
    s, e = t.week_days(t.NWEEK - 1)
    assert (s, e) == (172, 178) and (d0 + datetime.timedelta(days=s)).weekday() == 0 and e <= w.TOTAL
    assert t.week_days(t.NWEEK)[1] > w.TOTAL                                               # неделя 25 в 181 сутки не помещается
    assert t.half_of_week(16) == "H1" and t.half_of_week(17) == "H2"                      # понедельник 27.04 (сутки 116) → H1; 04.05 (123) → H2
    assert (d0 + datetime.timedelta(days=t.HALF_CUT)) == datetime.date(2026, 5, 1) and (d0 + datetime.timedelta(days=t.FEB1)) == datetime.date(2026, 2, 1)
    hd = t.half_days(181, "H1"), t.half_days(181, "H2"), t.half_days(181, "all")
    assert hd[0].sum() == 89 and hd[1].sum() == 61 and hd[2].sum() == 150 and not (hd[0] & hd[1]).any()


def test_rank_avg_ties_and_spearman():
    x = np.array([3.0, 1.0, 1.0, 2.0, 3.0, 3.0])
    assert t.rank_avg(x).tolist() == [5.0, 1.5, 1.5, 3.0, 5.0, 5.0]
    rng = np.random.default_rng(3)
    a, b = rng.normal(size=500).round(1), rng.normal(size=500).round(1)
    try:
        from scipy.stats import spearmanr
    except ImportError:
        pytest.skip("нет scipy")
    assert t.pearson(t.rank_avg(a), t.rank_avg(b)) == pytest.approx(spearmanr(a, b)[0], abs=1e-12)
    assert math.isnan(t.pearson(t.rank_avg(np.zeros(5)), t.rank_avg(np.arange(5.0))))


def persistent_pool(C=300, seed=2, drift=1.0):
    """Клетки с устойчивым вкладом μ_c: ранги сохраняются от недели к неделе."""
    rng = np.random.default_rng(seed)
    mu = rng.normal(0, 1, C)
    N = np.ones((C, 181), np.int32) * 2
    X = mu[:, None] * drift + rng.normal(0, 0.5, (C, 181))
    return w.Pool(np.arange(1, C + 1), X, N, np.ones((C, 6), bool), None, {})


def test_t1_persistent_vs_noise():
    pool = persistent_pool()
    M, NU = t.spearman_matrix(pool, "short")
    rows = {(r[0], r[1]): r for r in t.t1_stats(M, nboot=500, nperm=300)}
    r = rows[(1, "H1")]
    assert r[3] > 0.8 and r[4] > 0.7 and r[2] == 13                                       # 13 пар лага 1 с целью с 02.02 в H1 (январские цели — разгон)
    assert rows[(1, "H2")][2] == 8 and rows[(8, "H1")][2] == 9 and rows[(1, "all")][2] == 21 and t.T1_FIRST == 4
    assert abs(r[6] - r[3]) < 0.05                                                         # стационарно: нуль перестановок недель ≈ наблюдение
    noise = synth_pool(C=400, seed=5, trade_p=0.9)
    noise.X[:] = np.random.default_rng(6).normal(0, 3, noise.X.shape)
    Mn, _ = t.spearman_matrix(noise, "short")
    rn = {(r[0], r[1]): r for r in t.t1_stats(Mn, nboot=500, nperm=300)}
    assert abs(rn[(1, "all")][3]) < 0.1 and rn[(1, "H1")][4] < 0.1


def test_t1_january_targets_do_not_enter():
    """Поправка PS-1035 п. 1: цели-недели января (k = 1…3) в T1 не считаются ни в H1, ни в «all»."""
    M = np.full((t.NWEEK, t.NWEEK), -1.0)
    M[:, 4:] = 1.0                                                                          # у января-цели ρ = −1, у февраля и позже +1
    np.fill_diagonal(M, np.nan)
    rows = {(r[0], r[1]): r for r in t.t1_stats(M, nboot=200, nperm=50)}
    assert rows[(1, "H1")][2] == 13 and rows[(1, "H1")][3] == 1.0 and rows[(1, "all")][3] == 1.0
    assert rows[(2, "H1")][2] == 13 and rows[(5, "H1")][2] == 12 and rows[(8, "H1")][2] == 9


def test_t1_decay_has_excess_over_permutation_null():
    """Режим: вклад клетки меняется через каждые 4 недели → соседние недели похожи, далёкие — нет; нуль перестановок ниже наблюдения."""
    rng = np.random.default_rng(4)
    C = 300
    mu = rng.normal(0, 1, (C, 8))
    X = np.zeros((C, 181))
    for d in range(181):
        X[:, d] = mu[:, min(7, d // 28)] + rng.normal(0, 0.5, C)
    pool = w.Pool(np.arange(1, C + 1), X, np.ones((C, 181), np.int32) * 2, np.ones((C, 6), bool), None, {})
    M, _ = t.spearman_matrix(pool, "short")
    rows = {(r[0], r[1]): r for r in t.t1_stats(M, nboot=500, nperm=500)}
    r1 = rows[(1, "all")]
    assert r1[3] > r1[6] + 0.1 and r1[9] < 0.05                                           # ρ(лаг 1) заметно выше нуля перестановок, p_перест мало
    assert rows[(8, "all")][3] < r1[3]                                                     # затухание по лагам


def test_select_ab_matches_tk692_expanding_bit_for_bit():
    pool = synth_pool(C=80, seed=7)
    pre = t.Pre(pool)
    for side in ("long", "short"):
        for b, en, e in w.blocks("week", pool.nd):
            ref = w.select(pool, side, b, 1)
            got = t.select_ab(pre, pool, side, 0, b - 1, b)
            assert (ref is None) == (got is None)
            if ref is not None:
                assert ref["cid"] == got["cid"] and ref["score"] == got["score"] and ref["sum_w"] == got["sum_w"] and ref["n_w"] == got["n_w"] and ref["m"] == got["m"]


def test_sliding_window_no_lookahead_and_no_old_data():
    pool = synth_pool(C=80, seed=8)
    side, L, b = "short", 14, 60
    bb = b - 1
    base = t.select_ab(t.Pre(pool), pool, side, bb - L, bb, b)
    p2 = synth_pool(C=80, seed=8)
    p2.X[:, bb:] = np.random.default_rng(9).normal(5, 20, p2.X[:, bb:].shape)               # сутки с границы (E = 1 сутки: день b − 1 тоже вне окна)
    p2.X[:, :bb - L] = np.random.default_rng(10).normal(5, 20, p2.X[:, :bb - L].shape)      # сутки до окна
    again = t.select_ab(t.Pre(p2), p2, side, bb - L, bb, b)
    assert base["cid"] == again["cid"] and base["score"] == pytest.approx(again["score"], rel=1e-9) and base["w"] == L
    assert t.select_ab(t.Pre(pool), pool, side, 0, bb, b)["w"] == bb


def test_walk_full_window_flags_and_day_series():
    pool = synth_pool(C=50, seed=11)
    pre = t.Pre(pool)
    recs = t.walk_ab(pre, pool, "short", 30)
    full_b = [r["b"] for r in recs if r["full"]]
    assert min(full_b) == 32 and 25 not in full_b and all(b - 1 >= 30 for b in full_b)    # граница 32 = понедельник 02.02: окно [1, 31) целиком
    recs60 = t.walk_ab(pre, pool, "short", 60)
    assert min(r["b"] for r in recs60 if r["full"]) == 67
    pnl, n, cid, inb = t.day_series(recs, pool.nd)
    assert not inb[:32].any() and inb[32:].all() and (cid[inb] > 0).all()
    exp = t.day_series(t.walk_ab(pre, pool, "short", None), pool.nd)
    assert exp[3][11:].all() and not exp[3][:11].any()                                      # накопленное — полное окно с первой границы


def test_holm_family_and_rule_pass():
    ps = [("short", 7, 0.001), ("short", 14, 0.2), ("short", 30, 0.004), ("short", 60, 0.9), ("long", 7, 0.5), ("long", 14, 0.3), ("long", 30, 0.7), ("long", 60, 0.04)]
    hm = t.holm_family(ps)
    assert hm[("short", 7)] == (1, 0.05 / 8, True)                                         # 0,001 ≤ 0,00625
    assert hm[("short", 30)][2] is True and hm[("short", 14)][2] is False and hm[("short", 60)][2] is False
    assert hm[("long", 60)][2] is False                                                    # прошли все прежние, но 0,04 > 0,05 / 5
    rows_ok = [(1, "H1", 16, 0.4, 0.2, 0.6), (1, "H2", 8, 0.3, 0.05, 0.5)]
    rows_bad = [(1, "H1", 16, 0.4, 0.2, 0.6), (1, "H2", 8, 0.3, -0.05, 0.5)]
    assert t.rule_a(rows_ok)[0] is True and t.rule_a(rows_bad)[0] is False
    assert t.rule_a([(1, "H1", 16, 0.4, 0.2, 0.6)])[0] is False                            # нет H2


def test_equity_filter_strict_positive_no_lookahead():
    nd = 181
    x = np.zeros(nd)
    x[10:20] = 1.0                                                                         # плюс в сутках 10–19
    on = t.equity_filter(x, 7)                                                             # сутки 31…180
    assert len(on) == nd - 31 and not on.any()                                             # окно [t − 8, t − 1) для t = 31 — [23, 30): нули → не торгуем
    x[25] = 2.0
    on = t.equity_filter(x, 7)
    t_on = [31 + i for i, v in enumerate(on) if v]
    assert t_on == [31, 32, 33]                                                            # окно [t − 8, t − 1) содержит сутки 25 при t ∈ [27, 33]; ряд с t ≥ 31
    y = x.copy()
    y[30:] = -100.0                                                                         # будущее с t − 1: не должно менять ответ для t = 31 (окно до 29 включительно)
    assert t.equity_filter(y, 7)[0] == t.equity_filter(x, 7)[0]
    with pytest.raises(ValueError):
        t.equity_filter(x, 40)


def test_t2_side_end_to_end_and_main_line_exclusion(tmp_path):
    pool = synth_pool(C=120, seed=12)
    pool.X[7] = np.where(pool.N[7] > 0, 4.0 + np.random.default_rng(1).normal(0, 1, 181), 0.0)   # сильная устойчивая клетка → главная линия
    pool.X[8] = pool.X[7]
    pool.N[8] = pool.N[7]                                                                   # близнец: равный ряд
    res = t.t2_side(pool, "short", nboot=400)
    assert {tr["L"] for tr in res["trials"]} == set(t.LS)
    for tr in res["trials"]:
        assert 0 < tr["p_trial"] <= 1 and tr["p_trial"] == max(tr["half"]["H1"]["p"], tr["half"]["H2"]["p"])
        assert tr["main_slide"] > 0 and tr["excluded_cells"] >= 1 and 1 <= tr["excluded_cells_slide"] <= tr["excluded_cells"]
        assert set(tr["lo_s"]) == {"H1", "H2"} and set(tr["lo"]) == {"H1", "H2"}
        assert set(tr["lb"]) == {"H1", "H2"} and set(tr["lb"]["H1"]) == {str(k) for k in t.HOLM_K}
    sums = {(L, h, s): st for L, h, s, st, ex in res["sums"]}
    assert (7, "all", "slide") in sums and (60, "H2", "expand") in sums
    nd_h1 = {L: sums[(L, "H1", "slide")][0] for L in t.LS}
    assert nd_h1[7] >= nd_h1[14] >= nd_h1[30] >= nd_h1[60] and nd_h1[7] == 89 and nd_h1[60] < 89
    sd = [x for x in res["picks"] if x[0] == "L7"]
    assert len(sd) == len(w.blocks("week", 181))
    # главная линия исключена вместе с близнецом
    pre = t.Pre(pool)
    pnl, n, cid, inb = t.day_series(t.walk_ab(pre, pool, "short", 14), 181)
    ms, mask = t.main_line_mask(pool, pnl, cid, inb)
    if ms in (pool.cids[7], pool.cids[8]):
        assert mask[7] and mask[8]


def test_cli_roundtrip(tmp_path):
    pool = synth_pool(C=200, seed=13, trade_p=0.8)
    d = str(tmp_path / "pool")
    t.save_pool(pool, d)
    p2 = t.load_packed(d)
    assert np.array_equal(p2.X, pool.X) and np.array_equal(p2.cids, pool.cids) and p2.ckeys == pool.ckeys
    out = str(tmp_path / "o.sqlite")
    ref = str(tmp_path / "tk692.sqlite")
    for side in ("short", "long"):
        make_tk692(ref, pool, side)
    with pytest.raises(SystemExit):                                                        # без --tk692 t2 не запускается
        t.main(["t2", "--pool", d, "--side", "short", "--out", out])
    for side in ("short", "long"):
        assert t.main(["t1", "--pool", d, "--side", side, "--out", out, "--nboot", "200", "--nperm", "100"]) == 0
        assert t.main(["t2", "--pool", d, "--side", side, "--out", out, "--nboot", "200", "--tk692", ref]) == 0
        assert t.main(["t3", "--pool", d, "--side", side, "--out", out]) == 0
    assert t.main(["verdict", "--out", out]) == 0
    assert t.main(["report", "--out", out]) == 0
    con = sqlite3.connect(out)
    assert con.execute("SELECT count(*) FROM tk717_t1 WHERE side='short'").fetchall()[0][0] == 8 * 3
    assert con.execute("SELECT count(*) FROM tk717_t2_holm").fetchall()[0][0] == 8
    assert con.execute("SELECT count(*) FROM tk717_verdict WHERE part='stage_b'").fetchall()[0][0] == 2
    assert con.execute("SELECT count(*) FROM tk717_t1 WHERE side='short' AND lag=1 AND half='H1' AND n_pairs=13").fetchall()[0][0] == 1
    # (б): оба варианта «без главной линии» обязательны — только скользящая линия (lo_s) и обе линии (lo_d)
    con.execute("UPDATE tk717_t2_trial SET p_h1=1.0, p_h2=1.0, p_trial=1.0")
    good = json.dumps({h: {str(k): 1.0 for k in t.HOLM_K} for h in ("H1", "H2")})
    con.execute("UPDATE tk717_t2_trial SET p_h1=1e-6, p_h2=1e-6, p_trial=1e-6, d_h1=10, d_h2=10, lb=?, lo_s_h1=5, lo_s_h2=5, lo_d_h1=5, lo_d_h2=5 WHERE side='short' AND L=7", (good,))
    con.commit()
    assert t.main(["verdict", "--out", out]) == 0
    assert con.execute("SELECT pass_b FROM tk717_t2_holm WHERE side='short' AND L=7").fetchall()[0][0] == 1
    con.execute("UPDATE tk717_t2_trial SET lo_s_h2=-1 WHERE side='short' AND L=7")           # без главной линии скользящего ряда знак пропал
    con.commit()
    assert t.main(["verdict", "--out", out]) == 0
    assert con.execute("SELECT pass_b FROM tk717_t2_holm WHERE side='short' AND L=7").fetchall()[0][0] == 0
    con.execute("UPDATE tk717_t2_trial SET lo_s_h2=5, lo_d_h1=-1 WHERE side='short' AND L=7")  # без обеих линий знак пропал
    con.commit()
    assert t.main(["verdict", "--out", out]) == 0
    assert con.execute("SELECT pass_b FROM tk717_t2_holm WHERE side='short' AND L=7").fetchall()[0][0] == 0
