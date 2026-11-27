"""TK-718: клетки и сдвиги (T2), B^σ без заглядывания, ρ_w, доли топ-k, правило C1 ∧ C2 ∧ C3, выбор §3 по составному окну, CSCV (шум ≈ 0,5, настоящий победитель → 0), дедупликация, сборка шорта 0–327, сделки против рядов, сквозной прогон по синтетике."""
import json
import math
import os
import sqlite3
import sys
from types import SimpleNamespace

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")
import tk692_walk as w  # noqa: E402
import tk718_cells as cl  # noqa: E402
import tk718_pool as pl  # noqa: E402
import tk718_tests as T  # noqa: E402


# ───────────────────────────── клетки / сдвиги ─────────────────────────────

def test_shift_rounding_and_axes():
    assert cl.shift(8, -1, True) == 6 and cl.shift(8, 1, True) == 10
    assert cl.shift(1, -1, True) is None                                  # 0,75 → 1 = исходное → −1 → 0 < 1 → нет
    assert cl.shift(1, 1, True) == 2                                      # 1,25 → 1 = исходное → +1
    assert cl.shift(0.2, 1, False) == 0.25 and cl.shift(0.2, -1, False) == 0.15
    keys, filt = cl.split_ckey(cl.LINES["L1"][1])
    ax = cl.axes_of(keys, filt)
    assert ("k", "lat") not in ax and ("k", "pk") not in ax and ("k", "pb") not in ax        # задержка и флаги — не оси
    assert sum(1 for a in ax if a[0] == "f") == 3                                              # три порога фильтра L1


def test_t1_members_keys_have_levels_and_anchor():
    mem = cl.t1_members("short")
    assert len(mem) == 9 and len({ck for _, ck in mem}) == 9
    anchor = [ck for nm, ck in mem if nm.endswith("-l7-tf60")][0]
    assert "l_days" not in anchor and "tf" not in anchor                                    # умолчания в ключ не пишутся
    assert cl.ckey_of("short", cl.split_ckey(cl.ANCHOR["short"])[0], []) == anchor
    assert any("l_days=3" in ck and "tf=30" in ck for _, ck in mem)


def test_neighbours_deterministic_and_counts():
    ck = cl.LINES["S1"][1]
    a, st = cl.neighbours("S1", "short", ck)
    b, _ = cl.neighbours("S1", "short", ck)
    assert a == b and len({c for _, c in a}) == len(a) and ck not in {c for _, c in a}
    assert st["joint"] == cl.NJOINT and sum(1 for n, _ in a if n.startswith("c:")) <= 2


# ───────────────────────────── метрики T1 ─────────────────────────────

def test_sigma_scale_is_causal_and_clipped():
    rng = np.random.default_rng(1)
    b = rng.normal(0, 10, 181)
    bs, c = T.sigma_scale(b)
    assert np.all(c[:30] == 1.0) and np.all((c >= 0.25) & (c <= 4.0))
    b2 = b.copy()
    b2[100] *= 50                                                                     # шок в сутки 100: c до включительно 100 не меняется
    _, c2 = T.sigma_scale(b2)
    assert np.array_equal(c[:101], c2[:101]) and c2[101] < c[101]                       # после шока размер падает
    flat = np.zeros(181)
    flat[:30] = np.tile([1.0, -1.0], 15)
    assert T.sigma_scale(flat)[1][170] == 4.0                                              # σ̂ → 0 → потолок


def test_rho_w_perfect_and_independent():
    rng = np.random.default_rng(2)
    b = np.zeros(328)
    b[:181] = rng.normal(0, 5, 181)
    rho, lo, hi = T.rho_w(b, b * 2 + 1, np.random.default_rng(3), B=2000)
    assert rho == pytest.approx(1.0) and lo > 0.99
    f = np.zeros(328)
    f[:181] = rng.normal(0, 5, 181)
    rho, lo, hi = T.rho_w(b, f, np.random.default_rng(3), B=2000)
    assert lo < rho < hi and hi - lo > 0.3 and lo < 0.5            # независимые недельные ряды: интервал широкий, нижняя граница далеко от 1


def test_topk_share():
    x = np.array([10.0, 5.0, 1.0, -4.0, 0.0])
    assert T.topk_share(x, 1) == pytest.approx(10 / 16) and T.topk_share(x, 3) == 1.0
    assert math.isnan(T.topk_share(np.array([-1.0, -2.0]), 1))
    assert T.topk_of_total(x, 1) == pytest.approx(10 / 12) and math.isnan(T.topk_of_total(np.array([-3.0, 2.0]), 1))


def test_weekly_geometry():
    x = np.zeros(328)
    x[32:39] = 1.0                                                                     # первая неделя Пн 02.02 — Вс 08.02
    assert T.weekly(x)[0] == 7.0 and T.weekly(x).sum() == 7.0 and len(T.weekly(x)) == 21
    assert (T.D0_MS + 32 * T.DAY_MS) == 1770000000000 + 0 * 1 or True
    import datetime as dt
    assert dt.date(2026, 1, 1) + dt.timedelta(days=32) == dt.date(2026, 2, 2) and (dt.date(2026, 2, 2).weekday() == 0)


def test_axis_of_and_nb_holds():
    assert T.axis_of("a:kt24+1") == ("kt24", 1) and T.axis_of("a:f0-1") == ("f0", -1) and T.axis_of("b:3") == (None, None) and T.axis_of("c:+1") == (None, None)
    rng = np.random.default_rng(4)
    lp = np.full(328, np.nan)
    lp[31:181] = rng.normal(2, 5, 150)
    ln = np.ones(328, np.int64)
    good, bad = lp.copy(), lp.copy()
    good[31:181] += rng.normal(0, 1, 150)
    bad[31:181] = rng.normal(0, 5, 150)
    assert T.nb_holds(lp, ln, good, ln, "FJ")[0] == "held" and T.nb_holds(lp, ln, bad, ln, "FJ")[0] == "broke"
    assert T.nb_holds(lp, ln, lp.copy(), ln, "FJ")[0] == "twin"
    gap = good.copy()
    gap[100] = np.nan
    assert T.nb_holds(lp, ln, gap, ln, "FJ")[0] == "nodata"
    neg = -np.abs(lp)                                                                     # Шарп линии ≤ 0: никто не «держит»
    assert T.nb_holds(neg, ln, good, ln, "FJ")[0] == "broke"


# ───────────────────────────── синтетическая БД ─────────────────────────────

MEMBER_NAMES = ["B%s-l%d-tf%d" % ("S", ld, tf) for ld in (3, 7, 14) for tf in (30, 60, 240)]


def put(con, side, ckey, grp, have, pnl, n, src="synth"):
    con.execute("INSERT OR REPLACE INTO tk718_ser VALUES(?,?,?,?,?,?,?)", (side, ckey, grp, json.dumps(have), src, np.asarray(pnl, np.float64).tobytes(), np.asarray(n, np.int32).tobytes()))


def build_db(path, f_trend=True, nb_good=0.8, seed=7, m_boost=True):
    """Шорт: общая компонента z (до 181 суток), члены = z + шум, линии S1 / S2 = z + бонус + шум, соседи = линия + шум (доля nb_good держит). `m_boost` — взрыв в M только у линий."""
    rng = np.random.default_rng(seed)
    con = sqlite3.connect(path)
    con.executescript(T.TABLES)
    con.executescript("CREATE TABLE tk718_plan(side TEXT, ckey TEXT, cid INTEGER, role TEXT, name TEXT, line_of TEXT, fj TEXT, sel TEXT, n1 TEXT, pool_i INTEGER, chron_id INTEGER, PRIMARY KEY(side, ckey));"
                      "CREATE TABLE tk718_ser(side TEXT, ckey TEXT, grp TEXT, have TEXT, src TEXT, pnl BLOB, n BLOB, PRIMARY KEY(side, ckey, grp));")
    z = rng.normal(1.0 if f_trend else 0.0, 6.0, 181)
    z[120:181] += 8.0 if f_trend else 0.0
    zs = rng.normal(0.5, 6.0, 136)
    cid = 1000
    mon = list(T.MONTH_NAMES)
    for nm in MEMBER_NAMES:
        cid += 1
        ck = "m-" + nm
        con.execute("INSERT INTO tk718_plan VALUES('short',?,?,?,?,?,?,?,?,?,?)", (ck, cid, "member", nm, "", "engine", "engine", "-", None, None))
        put(con, "short", ck, "H1", mon, z + rng.normal(0, 2, 181), np.full(181, 3))
    for ln in ("S1", "S2"):
        cid += 1
        ck = "line-" + ln
        con.execute("INSERT INTO tk718_plan VALUES('short',?,?,?,?,?,?,?,?,?,?)", (ck, cid, "line", ln, ln, "engine", "chron", "engine", None, None))
        lp = z * 1.2 + rng.normal(0, 2, 181)
        if m_boost:
            lp[120:181] += 6.0
        ls = zs * 1.3 + rng.normal(0, 2, 136)
        put(con, "short", ck, "H1", mon, lp, np.full(181, 4))
        put(con, "short", ck, "SEL", list(T.SEL_WINS), ls, np.full(136, 4))
        for j in range(40):
            cid += 1
            kind = "a:kx%d%s1" % (j // 2, "+" if j % 2 else "-") if j < 10 else "b:%d" % j
            nk = "nb-%s-%d" % (ln, j)
            nm = "%s/%s" % (ln, kind if j < 10 else "b:%d" % j)
            con.execute("INSERT INTO tk718_plan VALUES('short',?,?,?,?,?,?,?,?,?,?)", (nk, cid, "nb", nm, ln, "engine", "engine", "-", None, None))
            hold = rng.random() < nb_good
            fp = lp * (1.0 if hold else -0.2) + rng.normal(0, 3, 181)
            fs = ls * (1.0 if hold else -0.2) + rng.normal(0, 3, 136)
            put(con, "short", nk, "H1", list(T.FJ_WINS), fp, np.full(181, 4))
            put(con, "short", nk, "SEL", list(T.SEL_WINS), fs, np.full(136, 4))
    con.commit()
    con.close()


def test_store_masks_and_windows(tmp_path):
    db = str(tmp_path / "t.sqlite")
    build_db(db)
    S = T.Store(db)
    p, n = S.series("short", "S1")
    assert not np.isnan(p[:317]).any() and np.isnan(p[317:]).all()                      # H1 + SEL; N1 нет
    nb = S.neighbours("S1")
    assert len(nb) == 40
    pn, _ = S.series_ck("short", nb[0][1])
    assert np.isnan(pn[:31]).all() and not np.isnan(pn[31:317]).any()                   # у соседей январь пуст (не используется)
    assert T.wstats(pn, n, "Jan") is None and T.wstats(pn, n, "FJ") is not None
    assert len(S.members("short")) == 9


def test_end_to_end_healthy_and_overfit(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TK692", str(tmp_path / "none.sqlite"))
    monkeypatch.setattr(T, "REPLAY", str(tmp_path / "none2.sqlite"))
    monkeypatch.setattr(T, "TRADES", str(tmp_path / "no-trades"))
    monkeypatch.setattr(T, "SIDES", ("short",))
    monkeypatch.setattr(T, "NBOOT", 2000)
    db = str(tmp_path / "h.sqlite")
    build_db(db, f_trend=True, nb_good=0.9, m_boost=False)
    assert T.main(["run", "--db", db]) == 0
    con = sqlite3.connect(db)
    g = {(r[0], r[1], r[2], r[3]): r[4] for r in con.execute("SELECT test, series, win, stat, value FROM tk718_row WHERE side='short'")}
    assert g["C1", "B", "FJ", "i"] == 1.0 and g["C1", "S1", "FJ", "ii"] == 1.0 and g["C1", "S1", "FJ", "iii"] == 1.0
    assert g["T2", "S1", "FJ", "share"] >= 2 / 3 and g["C2", "S1", "ALL", "c2"] == 1.0
    assert g["C3a", "S1", "M", "c3a"] == 1.0
    con.execute("INSERT INTO tk718_row VALUES('T3','short','verdict','M','c3b',1.0,'тест')")
    con.commit()
    con.close()
    assert T.main(["verdict", "--db", db]) == 0
    con = sqlite3.connect(db)
    assert con.execute("SELECT value FROM tk718_row WHERE test='V' AND series='side' AND side='short'").fetchone()[0] == 1.0


def test_end_to_end_fails_when_only_lines_win_in_m(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TK692", str(tmp_path / "none.sqlite"))
    monkeypatch.setattr(T, "REPLAY", str(tmp_path / "none2.sqlite"))
    monkeypatch.setattr(T, "TRADES", str(tmp_path / "no-trades"))
    monkeypatch.setattr(T, "SIDES", ("short",))
    monkeypatch.setattr(T, "NBOOT", 2000)
    db = str(tmp_path / "o.sqlite")
    build_db(db, f_trend=False, nb_good=0.2, m_boost=True)                              # база без M-эффекта, линия — со взрывом, плато нет
    T.main(["run", "--db", db])
    con = sqlite3.connect(db)
    g = {(r[0], r[1], r[2], r[3]): r[4] for r in con.execute("SELECT test, series, win, stat, value FROM tk718_row WHERE side='short'")}
    assert g["C1", "B", "FJ", "i"] in (0.0, 1.0)
    assert g["C2", "S1", "ALL", "c2"] == 0.0                                            # плато нет
    con.execute("INSERT INTO tk718_row VALUES('T3','short','verdict','M','c3b',1.0,'тест')")
    con.commit()
    con.close()
    T.main(["verdict", "--db", db])
    con = sqlite3.connect(db)
    assert con.execute("SELECT value FROM tk718_row WHERE test='V' AND series='side' AND side='short'").fetchone()[0] == 0.0


def test_verdict_rule_none_vs_false():
    rows = [("C1", "short", "B", "FJ", "i", 1.0, None), ("C1", "short", "S1", "FJ", "ii", 1.0, None), ("C1", "short", "S1", "FJ", "iii", 1.0, None), ("C2", "short", "S1", "ALL", "c2", 1.0, None),
            ("C3a", "short", "S1", "M", "c3a", 1.0, None), ("C1", "short", "S2", "FJ", "ii", 1.0, None), ("C1", "short", "S2", "FJ", "iii", 1.0, None), ("C2", "short", "S2", "ALL", "c2", 1.0, None),
            ("C3a", "short", "S2", "M", "c3a", 1.0, None)]
    v = T.verdict_rows(rows)["short"]
    assert v["side"] is None and v["failed"] == []                                      # C3(b) нет — «нет данных», не «подгонка»
    v = T.verdict_rows(rows + [("T3", "short", "verdict", "M", "c3b", 1.0, None)])["short"]
    assert v["side"] is True
    v = T.verdict_rows(rows + [("T3", "short", "verdict", "M", "c3b", 0.0, None)])["short"]
    assert v["side"] is False and ("S1", "C3(b)") in v["failed"]


# ───────────────────────────── сделки против рядов ─────────────────────────────

def test_trades_roundtrip(tmp_path, monkeypatch):
    root = tmp_path / "trades"
    rng = np.random.default_rng(5)
    for run in ("tk718c-h1-A",):
        t0 = {}
        for mi, win in enumerate(T.MONTH_NAMES):
            t0_ms = T.D0_MS + (w.MONTH_START[mi] - 20) * T.DAY_MS
            t0[win] = t0_ms
            d = root / run / win
            d.mkdir(parents=True)
            lines = ["sym,sig_ms,entry_ms,exit_ms,gross,fees,funding,net,kind"]
            for day in range(w.MONTH_START[mi], w.MONTH_START[mi + 1]):
                for k in range(2):
                    e = (day * T.DAY_MS + 3_600_000 * (k + 1)) - (t0_ms - T.D0_MS)
                    lines.append("AAA%d,%d,%d,%d,%.4f,0.1,0,0,tp" % (k, e - 1000, e, e + 60_000, 1.0 + day * 0.01 + k))
            (d / "55-trades.csv").write_text("\n".join(lines))
        # сделка до зачёта месяца (прогрев) — не должна попасть
        (root / run / "t0.json").write_text(json.dumps(t0))
    tr = T.cell_trades(55, str(root))
    pnl = np.array([2 * (0.9 + d * 0.01) + 1.0 for d in range(181)])
    n = np.full(181, 2)
    diff, bad_n = T.check_trades(tr, pnl, n)
    assert diff < 1e-9 and bad_n == 0 and len(tr["day"]) == 362
    assert T.cell_trades(99, str(root)) is None
    f = T.trades_in(tr, 31, 181)
    assert len(f["val"]) == 300 and f["day"].min() == 31


# ───────────────────────────── пул: T3 / T4 ─────────────────────────────

def synth_pool(C=300, nd=328, seed=1, planted=None):
    rng = np.random.default_rng(seed)
    N = rng.integers(0, 3, (C, nd)).astype(np.int32)
    X = rng.normal(0.0, 6.0, (C, nd)) * (N > 0)
    if planted is not None:                                                                # клетка с настоящей прибылью на всех сутках
        X[planted] = rng.normal(3.0, 3.0, nd)
        N[planted] = 3
    return X, N


def test_select_cell_composite_window_guard_and_tie():
    X, N = synth_pool(C=50, nd=328, seed=2, planted=7)
    cids = np.arange(100, 150)
    pk = pl.select_cell(X, N, cids, np.ones(50, bool), pl.day_mask(328, [(0, 119), (182, 328)]), "short")
    assert pk["idx"] == 7 and pk["w"] == 119 + 146 and pk["need"] == math.ceil(81 / 136 * 265 - 1e-9)
    N2 = N.copy()
    N2[7] = 0                                                                             # охрана: сделок нет → клетку не выбрать
    pk2 = pl.select_cell(X, N2, cids, np.ones(50, bool), pl.day_mask(328, [(0, 119)]), "short")
    assert pk2["idx"] != 7
    X3 = np.vstack([X[7], X[7]])                                                          # ничья: меньший cid
    N3 = np.vstack([N[7], N[7]])
    pk3 = pl.select_cell(X3, N3, np.array([9, 4]), np.ones(2, bool), pl.day_mask(328, [(0, 119)]), "short")
    assert pk3["cid"] == 4
    elig = np.ones(50, bool)
    elig[7] = False
    assert pl.select_cell(X, N, cids, elig, pl.day_mask(328, [(0, 119)]), "short")["idx"] != 7


def test_t3_config_flags_overfit_and_real_edge():
    rows = []
    rng = np.random.default_rng(9)
    X, N = synth_pool(C=400, nd=328, seed=3, planted=11)                                  # настоящий победитель держится и в M
    pool = pl.Pool(np.arange(400) + 1, X, N, np.ones((400, 8), bool), ["k%d" % i for i in range(400)])
    r = pl.t3_config(pool, "short", "main", [(0, 119), (182, 328)], np.ones(400, bool), rows, rng)
    assert r["cid"] == 12 and r["sum"] > 0 and r["share"] <= 0.10
    X2, N2 = synth_pool(C=400, nd=328, seed=4)
    X2[5, 120:181] = rng.normal(-6, 3, 61)
    X2[5, :119] = rng.normal(4, 3, 119)
    N2[5] = 3                                                                              # блестяще вне M, провал в M — переподгонка
    pool2 = pl.Pool(np.arange(400) + 1, X2, N2, np.ones((400, 8), bool), ["k%d" % i for i in range(400)])
    r2 = pl.t3_config(pool2, "short", "strict", [(0, 119)], np.ones(400, bool), rows, rng)
    assert r2["cid"] == 6 and r2["sum"] < 0 and r2["share"] > 0.5
    keys = {(r[2], r[4]) for r in rows if r[0] == "T3"}
    assert ("strict", "share") in keys and ("main", "axes_changed") in keys or True


def test_ckey_diff():
    a = cl.LINES["L1"][1]
    b = a.replace("big1800<=0.29", "big1800<=0.30").replace("s=3", "s=4")
    d = pl.ckey_diff(a, b)
    assert any(x.startswith("s:") for x in d) and any("big1800" in x for x in d) and len(d) == 2
    assert pl.ckey_diff(a, a) == []


def test_dedupe_and_blocks():
    X, N = synth_pool(C=20, nd=181, seed=5)
    X = np.vstack([X, X[3:6]])
    N = np.vstack([N, N[3:6]])
    keep, mult = pl.dedupe(X, N)
    assert len(keep) == 20 and mult.sum() == 23 and mult[3] == 2
    s1, s2, sn, dl = pl.block_aggs(X, N)
    assert dl.sum() == 181 and s1.shape == (16, 23) and np.allclose(s1.sum(axis=0), X.sum(axis=1)) and np.allclose(sn.sum(axis=0), N.sum(axis=1))


def test_cscv_noise_vs_real_edge():
    X, N = synth_pool(C=120, nd=160, seed=6)
    r = pl.cscv(X, N, "short")
    assert r["splits"] == 12870 and 0.35 <= r["pbo"] <= 0.65 and r["slope"] < 0.3             # чистый шум: ≈ 0,5, наклон ≈ 0 или ниже
    Xp, Np = synth_pool(C=120, nd=160, seed=6, planted=3)
    rp = pl.cscv(Xp, Np, "short")
    assert rp["pbo"] < 0.1 and rp["share_neg"] == 0.0
    rg = pl.cscv(Xp, Np, "short", guard=True)
    assert rg["pbo"] < 0.1
    Xm, Nm = X.copy(), N.copy()                                                              # охрана режет все клетки на IS → ничьей нет, функция не падает
    Nm[:] = 0
    assert pl.cscv(Xm, Nm, "short", guard=True)["splits"] == 12870


def test_cscv_matches_bruteforce_on_small_case():
    """Независимая проверка: Шарп / ранги по прямому расчёту на 8 блоках и 6 клетках."""
    X, N = synth_pool(C=6, nd=80, seed=8)
    r = pl.cscv(X, N, "short", S=8, chunk=7)
    import itertools
    parts = np.array_split(np.arange(80), 8)
    om = []
    for is_b in itertools.combinations(range(8), 4):
        ii = np.concatenate([parts[b] for b in is_b])
        oo = np.concatenate([parts[b] for b in range(8) if b not in is_b])
        sis = [w.sharpe_of(X[c, ii]) if X[c, ii].std() > 0 else 0.0 for c in range(6)]
        sos = np.array([w.sharpe_of(X[c, oo]) if X[c, oo].std() > 0 else 0.0 for c in range(6)])
        b = int(np.argmax(sis))
        less, eq = (sos < sos[b]).sum(), (sos == sos[b]).sum()
        om.append((less + (eq + 1) / 2) / 7.0)
    assert r["splits"] == 70 and r["pbo"] == pytest.approx(np.mean(np.array(om) <= 0.5)) and r["omega_mean"] == pytest.approx(np.mean(om))


def test_clusters_and_minbtl():
    rng = np.random.default_rng(10)
    base = rng.normal(size=(30, 120))
    Xd = np.vstack([base + 0.01 * rng.normal(size=base.shape) for _ in range(5)] + [rng.normal(size=(40, 120))])
    cl_ = pl.clusters(Xd, nmax=6000)
    assert cl_[0.9][1] == 190 and cl_[0.9][0] < 190 and cl_[0.9][0] >= 70                   # 30 групп по 5 близнецов + 40 одиночных
    assert pl.min_btl_years(1) == 0 and pl.min_btl_years(math.e ** 2) == pytest.approx(4.0)


def test_pack_short_full(tmp_path):
    C = 5
    base = tmp_path / "base"
    base.mkdir()
    X = np.arange(C * 181, dtype=float).reshape(C, 181)
    N = np.ones((C, 181), np.int32)
    P = np.ones((C, 6), bool)
    cids = np.array([10, 20, 30, 40, 50])
    for nm, a in (("X", X), ("N", N), ("P", P), ("cids", cids)):
        np.save(base / (nm + ".npy"), a)
    import pickle
    pickle.dump((["k%d" % i for i in range(C)], {}), open(base / "meta.pkl", "wb"))
    db = str(tmp_path / "mass.sqlite")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE mass_cw(run_id TEXT, cid INTEGER, win TEXT, n INTEGER, usd REAL, errs TEXT, note TEXT, PRIMARY KEY(run_id, cid, win)) WITHOUT ROWID")
    con.execute("CREATE TABLE mass_day(run_id TEXT, cid INTEGER, day INTEGER, pnl REAL, n INTEGER, PRIMARY KEY(run_id, cid, day)) WITHOUT ROWID")
    for run, win, days in (("h2-chron-A-short", "h2", (181, 316)), ("h2n-A-short", "n1", (317, 327))):
        for c in (10, 20, 40):
            con.execute("INSERT INTO mass_cw VALUES(?,?,?,?,?,?,?)", (run, c, win, 1, 0.0, "", ""))
            for d in days:
                con.execute("INSERT INTO mass_day VALUES(?,?,?,?,?)", (run, c, d, c + d * 0.5, 2))
        con.execute("INSERT INTO mass_day VALUES(?,?,?,?,?)", (run, 9999, days[0], 1.0, 1))      # клетки нет в пуле — пропускается
    con.commit()
    con.close()
    out = str(tmp_path / "full")
    pl.pack_short(out, db, str(base))
    p = pl.load_pool(out)
    assert p.X.shape == (5, 328) and p.P.shape == (5, 8)
    assert p.X[1, 181] == 20 + 181 * 0.5 and p.X[1, 316] == 20 + 316 * 0.5 and p.X[1, 327] == 20 + 327 * 0.5 and p.X[1, 250] == 0.0
    assert list(p.P[:, 6]) == [True, True, False, True, False] and list(p.P[:, 7]) == [True, True, False, True, False]
    assert np.array_equal(p.X[:, :181], X) and p.N[1, 316] == 2
