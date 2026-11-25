"""TK-692: геометрия шагов, охрана, отбор без заглядывания, состав набора, склейка, базы, Holm, бутстреп, сквозной прогон по синтетической БД (tk692_walk.py)."""
import json
import math
import os
import sqlite3
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk684_metrics as m684  # noqa: E402
import tk692_walk as w  # noqa: E402


def mk_pool(X, N=None, P=None, ckeys=None, meta=None, cids=None):
    X = np.asarray(X, float)
    N = np.ones(X.shape, np.int32) * (X != 0) if N is None else np.asarray(N, np.int32)
    nm = w.month_of(X.shape[1] - 1) + 1
    P = np.ones((X.shape[0], nm), bool) if P is None else np.asarray(P, bool)
    return w.Pool(cids if cids is not None else np.arange(1, X.shape[0] + 1), X, N, P, ckeys, meta)


def test_blocks_geometry():
    assert w.blocks("month", 31) == [(31, 59, 31)]                       # отложенный выбор по январю: торговых суток нет
    assert w.blocks("month", 59) == [(31, 59, 59), (59, 90, 59)]      # февраль торгуется; граница 59 — отложенный выбор по январю–февралю
    assert w.blocks("month", 181)[-1] == (151, 181, 181)
    wk = w.blocks("week", 181)
    assert wk[0] == (11, 18, 18) and wk[-1] == (179, 181, 181)           # понедельник 12.01 = сутки 11; последний блок обрезан
    assert w.blocks("week", 31)[-1] == (25, 32, 31)                      # граница 25 (26.01), торговля до 31 включительно
    d3 = w.blocks("day3", 181)
    assert d3[0] == (3, 6, 6) and d3[-1] == (180, 181, 181) and len(d3) == 60
    assert w.blocks("day3", 31)[-1] == (30, 33, 31)
    assert (datetime_day(2026, 1, 12) - datetime_day(2026, 1, 1)) == 11   # 12.01.2026 — понедельник


def datetime_day(y, m, d):
    import datetime
    dt = datetime.date(y, m, d)
    assert dt.weekday() == 0 or (y, m, d) == (2026, 1, 1)
    return dt.toordinal()


def test_month_starts():
    assert w.MONTH_START == (0, 31, 59, 90, 120, 151, 181)
    assert [w.month_of(d) for d in (0, 30, 31, 58, 59, 180)] == [0, 0, 1, 1, 2, 5]


def test_n_min_guard():
    assert w.n_min("long", 136) == 105 and w.n_min("short", 136) == 81      # L-335
    assert w.n_min("short", 3) == 2 and w.n_min("long", 3) == 3
    assert w.n_min("short", 10) == 6


def test_metrics_match_tk684():
    rng = np.random.default_rng(1)
    for _ in range(20):
        x = rng.normal(0.5, 3, 40).round(2)
        assert w.sharpe_of(x) == pytest.approx(m684.sharpe(list(x)), rel=1e-9)
        u = w.underwater(x)
        assert u["maxdd"] == pytest.approx(m684.maxdd(list(x)), abs=1e-9)
        assert u["gap_longest"] == m684.gap_longest(list(x))


def test_underwater_hand_example():
    u = w.underwater([1, -2, 3, -1, -1, 5, 0])
    assert u["gap_longest"] == 3 and u["tail"] == 1 and u["gap_mean"] == 2.0 and u["maxdd"] == 2.0


def test_select_best_ties_and_guard():
    # 4 клетки × 10 суток: клетка 0 — ровный плюс (максимальный Шарп), 1 — тот же ряд (дубль), 2 — выше Σ, но шумная, 3 — мало сделок
    base = np.array([1, 1.2, 0.9, 1.1, 1, 1.3, 0.8, 1, 1.1, 1.0])
    noisy = np.array([9, -6, 8, -5, 9, -4, 10, -3, 6, 1.0])
    X = np.vstack([base, base, noisy, base * 3])
    N = np.vstack([np.ones(10), np.ones(10), np.ones(10), np.r_[np.ones(1), np.zeros(9)]]).astype(int)
    pool = mk_pool(X, N)
    pk = w.select(pool, "short", 10, 0)
    assert pk["cid"] == 1                              # клетка 4 (тот же Шарп, ×3) отсечена охраной n = 1 < ⌈81/136·10⌉ = 6; клетки 1 и 2 — дубли: равны балл, Σ и n → меньший cid
    assert pk["n_dup"] == 2
    pool2 = mk_pool(X, np.ones(X.shape, int))
    assert w.select(pool2, "short", 10, 0)["cid"] == 4  # без охраны клетка 3 (тот же Шарп, большая Σ) побеждает по Σ при равенстве


def test_select_no_lookahead():
    rng = np.random.default_rng(2)
    X = rng.normal(0.3, 2, (60, 31)).round(2)
    pool = mk_pool(X)
    a = w.select(pool, "long", 20, 1)
    X2 = X.copy()
    X2[:, 19:] = rng.normal(5, 9, (60, 12))                 # сутки ≥ b − E меняются как угодно
    b = w.select(mk_pool(X2), "long", 20, 1)
    assert (a["cid"], a["score"], a["w"]) == (b["cid"], b["score"], 19)
    c = w.select(mk_pool(X2), "long", 20, 0)                # при E = 0 окно длиннее на сутки — сутки 19 уже участвуют
    assert c["w"] == 20


def test_embargo_window_too_short():
    pool = mk_pool(np.random.default_rng(3).normal(1, 1, (5, 31)))
    assert w.select(pool, "long", 2, 1) is None             # окно из 1 суток — σ не определено
    assert w.select(pool, "long", 3, 1) is not None


def test_eligibility_only_months_before_b():
    X = np.random.default_rng(4).normal(1, 2, (6, 59))
    P = np.ones((6, 2), bool)
    P[0, 1] = False                                         # клетка 0 не посчитана в феврале
    pool = mk_pool(X, P=P)
    assert w.eligible(pool, 31)[0]                          # граница 31: в состав входят только месяцы до 31-х суток — январь
    assert not w.eligible(pool, 40)[0]                      # окно уже захватило февраль


def test_walk_glues_oos_days_and_skip_month():
    rng = np.random.default_rng(5)
    X = rng.normal(0.5, 2, (30, 59)).round(2)
    P = np.ones((30, 2), bool)
    pool = mk_pool(X, P=P)
    picks, days, pnl, nn = w.walk(pool, "long", "week", 1)
    assert list(days) == list(range(11, 59)) and len(pnl) == len(days)
    r0 = picks[0]
    assert r0["b"] == 11 and np.allclose(pnl[:7], X[r0["pick"]["idx"], 11:18])
    # выбранная клетка не посчитана в феврале → её P&L в сутках февраля = 0, шаг помечен
    last = [r for r in picks if r["e"] > 31 and r["b"] < 59][0]
    pool.P[last["pick"]["idx"], 1] = False
    picks2, days2, pnl2, _ = w.walk(pool, "long", "week", 1)
    # step_pnl: клетка, не посчитанная в месяце, даёт 0 $ / 0 сделок и пометку
    p, n, miss = pool.step_pnl([last["pick"]["idx"]], 31, 40)
    assert (p == 0).all() and (n == 0).all() and miss[0]


def test_pending_pick_after_january():
    pool = mk_pool(np.random.default_rng(6).normal(1, 2, (10, 31)))
    picks, days, pnl, nn = w.walk(pool, "long", "month", 1)
    assert len(picks) == 1 and picks[0]["b"] == 31 and picks[0]["e"] == 31 and picks[0]["pick"] is not None
    assert len(days) == 0


def test_line_changes_by_series_not_cid():
    X = np.array([[1, 2, 3.0, 4, 5, 6, 7, 8, 9, 10, 11, 12], [1, 2, 3.0, 4, 5, 6, 7, 8, 9, 10, 11, 12], [0, 1, 0.0, 1, 0, 1, 0, 1, 0, 1, 0, 1]])
    pool = mk_pool(X)
    picks = [dict(b=3, e=6, pick=dict(idx=0)), dict(b=6, e=9, pick=dict(idx=1)), dict(b=9, e=12, pick=dict(idx=2))]
    assert w.line_changes(pool, picks) == (1, 2)           # 0 → 1 — тот же ряд, 1 → 2 — смена


def test_resolve_base_levels():
    X = np.array([[1.0, 2, 3, 4], [1.0, 2, 3, 4], [1.0, 2, 3, 5], [0.0, 1, 0, 1]])
    meta = {1: ("a", 10, 5.0), 2: ("b", 10, 5.0), 3: ("c", 10, 5.0), 4: ("d", 7, 9.0), 10: ("outside", 7, 9.0)}
    pool = mk_pool(X, ckeys=["a", "b", "c", "d"], meta=meta)
    assert pool.resolve_base("d", 99, 0) == (3, "ckey", [4])
    i, how, fam = pool.resolve_base("zzz", 7, 9.0)           # семейство из клеток 4 и 10; 10 не загружена — берётся 4
    assert i == 3 and how.startswith("family") and fam == [4, 10]
    i, how, fam = pool.resolve_base("zzz", 10, 5.0)          # ряды 1, 2 равны, 3 — нет → семейство разошлось
    assert i is None and "series-differ" in how
    assert pool.resolve_base("zzz", 55, 1.0)[1] == "absent"
    meta2 = dict(meta)
    meta2[3] = ("c", 10, 5.01)
    pool2 = mk_pool(X, ckeys=["a", "b", "c", "d"], meta=meta2)
    assert "sigma-differs" in pool2.resolve_base("zzz", 10, 5.0)[1]


def test_holm_and_share_ge():
    r = w.holm([0.001, 0.2, 0.01])
    assert [x[0] for x in r] == [True, False, True]
    assert r[0][1] == pytest.approx(0.05 / 3) and r[2][1] == pytest.approx(0.05 / 2)
    assert w.holm([0.04, 0.04, 0.04])[0][0] is False       # 0,04 > 0,05 / 3
    assert w.share_ge(np.array([1, 2, 3, 4.0]), 3) == pytest.approx((2 + 1) / (4 + 1))
    assert w.share_ge(np.array([1, 2.0]), 99) == pytest.approx(1 / 3)


def test_boot_sums_blocks_and_determinism():
    d = np.r_[np.ones(30), -np.ones(30)]
    a = w.boot_sums(d, 500, np.random.default_rng(1))
    b = w.boot_sums(d, 500, np.random.default_rng(1))
    assert np.array_equal(a, b) and a.shape == (500,)
    z = w.boot_sums(np.zeros(20), 50, np.random.default_rng(1))
    assert (z == 0).all()
    assert abs(float(a.mean())) < 8                         # Σ = 0, блоки ⌈60^{1/3}⌉ = 4 сохраняют знак серий


def test_rand_c1_uses_step_pool_and_zero_for_missing():
    X = np.random.default_rng(7).normal(1, 2, (40, 31)).round(2)
    pool = mk_pool(X)
    picks, days, pnl, nn = w.walk(pool, "short", "day3", 1)
    g = w.rand_c1(pool, "short", picks, 1, 50, np.random.default_rng(1))
    assert g.shape == (50, len(days))
    g2 = w.rand_c2(pool, "short", picks, days, 50, np.random.default_rng(1))
    assert g2.shape == (50, len(days))
    stats, s, sh = w.dist_stats(g)
    assert set(stats) >= {"sum_mean", "sum_p10", "sum_p90", "sharpe_mean"} and "median" not in " ".join(stats)


# ───────────────────────────── сквозной прогон по синтетической mass.sqlite ─────────────────────────────

def make_db(path, nmonths, seed=11, ncell=12, missing=None):
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE cell(cellset TEXT, cid INTEGER, side TEXT, ckey TEXT, line TEXT, sig TEXT, filt TEXT, feats TEXT, ref_n INTEGER, ref_usd REAL, ref_daily TEXT, db_id INTEGER, bin TEXT, PRIMARY KEY(cellset, cid));
    CREATE TABLE mass_run(run_id TEXT PRIMARY KEY, cellset TEXT, dry INTEGER, wins TEXT, bin_md5 TEXT, params TEXT, created TEXT, state TEXT);
    CREATE TABLE mass_cw(run_id TEXT, cid INTEGER, win TEXT, n INTEGER, usd REAL, errs TEXT, note TEXT, PRIMARY KEY(run_id, cid, win)) WITHOUT ROWID;
    CREATE TABLE mass_day(run_id TEXT, cid INTEGER, day INTEGER, pnl REAL, n INTEGER, PRIMARY KEY(run_id, cid, day)) WITHOUT ROWID;
    CREATE TABLE mass_skip(run_id TEXT, cid INTEGER, win TEXT, why TEXT, PRIMARY KEY(run_id, cid, win)) WITHOUT ROWID;""")
    rng = np.random.default_rng(seed)
    nd = w.MONTH_START[nmonths]
    mu = rng.normal(0, 0.6, ncell)
    data = rng.normal(mu[:, None], 3, (ncell, w.TOTAL)).round(2)             # ряд на все 181 сутки: добавление месяца не меняет прежние
    for c in range(ncell):
        con.execute("INSERT INTO cell VALUES('all1',?,?,?,?,?,?,?,?,?,?,?,?)", (c + 1, "short", "k%d" % c, "", "", "[]", "", 100, 10.0, "", 0, ""))
    for mi in range(nmonths):
        run = "r-m%02d" % (mi + 1)
        con.execute("INSERT INTO mass_run VALUES(?,?,?,?,?,?,?,?)", (run, "all1", 0, MONTHS[mi], "", json.dumps({"D0": "2026-01-01"}), "", "done"))
        for c in range(ncell):
            if missing and (c + 1, mi) in missing:
                con.execute("INSERT INTO mass_skip VALUES(?,?,?,?)", (run, c + 1, MONTHS[mi], "охват x 0.00"))
                continue
            con.execute("INSERT INTO mass_cw VALUES(?,?,?,?,?,?,?)", (run, c + 1, MONTHS[mi], 5, 1.0, "", ""))
            for d in range(w.MONTH_START[mi], w.MONTH_START[mi + 1]):
                if data[c, d] != 0 and rng.random() < 0.8:
                    con.execute("INSERT INTO mass_day VALUES(?,?,?,?,?)", (run, c + 1, d, float(data[c, d]), 1))
    con.commit()
    con.close()


MONTHS = w.MONTHS


def run_cli(tmp, nmonths, missing=None, nrand=60, nboot=80):
    db, out = os.path.join(tmp, "mass.sqlite"), os.path.join(tmp, "tk692.sqlite")
    make_db(db, nmonths, missing=missing)
    runs = ",".join("r-m%02d" % (i + 1) for i in range(nmonths))
    assert w.main(["run", "--db", db, "--out", out, "--pool", "T", "--runs-short", runs, "--sides", "short", "--nrand", str(nrand), "--nboot", str(nboot)]) == 0
    return out


def test_end_to_end_two_months_and_consistency(tmp_path):
    tmp = str(tmp_path)
    out = run_cli(tmp, 1)
    con = sqlite3.connect(out)
    picks1 = {(s, b, e): c for s, b, e, c in con.execute("SELECT step, b, emb, cid FROM tk692_pick WHERE rid='T-m01'")}
    assert ("month", 31, 1) in picks1                      # «выбрано по январю»: торговых суток ещё нет
    n_oos = con.execute("SELECT count(*) FROM tk692_oos WHERE rid='T-m01' AND step='month'").fetchone()[0]
    assert n_oos == 0
    wk = con.execute("SELECT days, n FROM tk692_sum WHERE rid='T-m01' AND step='week' AND emb=1 AND series='select'").fetchone()
    assert wk[0] == 31 - 11                                # недели внутри января: торговля с 12.01
    con.close()
    out = run_cli(tmp, 2)                                  # добавили февраль: прежние выборы не меняются (проверка встроена)
    con = sqlite3.connect(out)
    assert con.execute("SELECT days FROM tk692_sum WHERE rid='T-m02' AND step='month' AND emb=1 AND series='select'").fetchone()[0] == 28
    assert con.execute("SELECT count(*) FROM tk692_rand WHERE rid='T-m02'").fetchone()[0] > 0
    assert con.execute("SELECT count(*) FROM tk692_run").fetchone()[0] >= 2
    con.close()


def test_end_to_end_missing_cell_month_and_skip_reason(tmp_path):
    tmp = str(tmp_path)
    out = run_cli(tmp, 2, missing={(c, 1) for c in range(1, 13)})        # в феврале ни одна клетка не посчитана → торговля нулями с пометкой
    con = sqlite3.connect(out)
    rows = con.execute("SELECT skipped, why FROM tk692_pick WHERE rid='T-m02' AND step='month' AND emb=1 AND e>b").fetchall()
    assert rows and rows[0][0] == 1 and "охват" in rows[0][1]
    assert con.execute("SELECT sum FROM tk692_sum WHERE rid='T-m02' AND step='month' AND emb=1 AND series='select'").fetchone()[0] == 0
    con.close()


def test_verdict_runs_and_holm(tmp_path):
    out = run_cli(str(tmp_path), 2)
    assert w.main(["verdict", "--out", out, "--rid", "T-m02"]) == 0
    con = sqlite3.connect(out)
    v = con.execute("SELECT step, verdict FROM tk692_verdict WHERE rid='T-m02'").fetchall()
    assert {s for s, _ in v} == {"month", "week", "day3"}
    con.close()


def test_load_pool_rejects_gap_in_months(tmp_path):
    db = os.path.join(str(tmp_path), "m.sqlite")
    make_db(db, 3)
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    with pytest.raises(SystemExit):
        w.load_pool(con, ["r-m01", "r-m03"], "all1", "short")


def test_report_prints_table(tmp_path, capsys):
    out = run_cli(str(tmp_path), 2)
    capsys.readouterr()
    assert w.main(["report", "--out", out, "--rid", "T-m02", "--db", os.path.join(str(tmp_path), "mass.sqlite")]) == 0
    txt = capsys.readouterr().out
    assert "| month | select |" in txt and "случайная c1" in txt and "Следующий выбор" in txt and "ключ: k" in txt


def test_cmp_rows_anchor_best_and_windows():
    # 4 клетки × 59 суток (январь + февраль): клетка 3 — лучшая в январе, клетка 4 — не посчитана в феврале
    X = np.zeros((4, 59))
    X[0, :] = 1.0
    X[1, :31] = -1.0
    X[2, :31] = 3.0
    X[2, 31:] = -0.5
    X[3, :31] = 5.0
    N = (np.abs(X) > 0).astype(np.int32) * 2
    P = np.array([[1, 1], [1, 1], [1, 1], [1, 0]], bool)
    pool = mk_pool(X, N, P)
    anchor = (X[0].copy(), N[0].copy(), 1, "ckey")
    rows = w.cmp_rows(pool, "long", "A-m02", anchor)
    got = {(r[2], r[3]): r for r in rows}
    assert set(got) == {("m01", "anchor"), ("m01", "best"), ("m02", "anchor"), ("m02", "best"), ("cum", "anchor"), ("cum", "best")}
    assert got[("m01", "anchor")][8] == 31.0                       # Σ якоря за январь
    assert got[("m01", "best")][5] == 4 and got[("m01", "best")][8] == 155.0   # клетка 4 (cid 4) лучшая в январе
    assert got[("cum", "best")][5] != 4                             # клетка 4 не во всех месяцах: в «cum» не участвует
    ex = json.loads(got[("m01", "anchor")][13])
    assert ex["n_above_anchor"] == 2 and ex["m"] == 4              # выше Σ якоря 31: клетки 3 (93) и 4 (155)
    assert json.loads(got[("m01", "best")][13])["found_on_same_data"] is True


def test_cmp_rows_without_anchor():
    X = np.ones((2, 31)) * [[1.0], [2.0]]
    pool = mk_pool(X, np.ones(X.shape, np.int32) * 2)
    rows = w.cmp_rows(pool, "long", "A-m01", None)
    assert {r[3] for r in rows} == {"best"} and rows[0][5] == 2
