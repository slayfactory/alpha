"""Тесты §13.71 (TK-671), семейство mkt (`tk671_mkt`): определения столбцов на малых числах вручную, ядра = независимый построчный счёт (`tk671_gate_mkt.brute_row`), сборка `compute` (индекс, порядок, 27 float64, NaN, нет кэша — NaN,
строки не выброшены), знак стороны, причинность (мусор в минутках ≥ t не меняет строку), охрана суток ≥ 14.11. Синтетика, без srv.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk671_mkt.py`."""
import os
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk604_stats as st  # noqa: E402
import tk668_feat as ft  # noqa: E402
import tk671_gate_mkt as gate  # noqa: E402
import tk671_mkt as fam  # noqa: E402

N = 4000
COLS = list(fam.COLUMNS)


def eqv(a, b):
    return np.array_equal(np.asarray(a, dtype=float), np.asarray(b, dtype=float), equal_nan=True)


def walk(rng, n=N, start=100000.0, vol=0.001, listed=0, gaps=0.03, flat=None):
    """Минутки: случайное блуждание; `listed` — первые минуты без данных (до листинга); `gaps` — доля пустых минут; `flat` = (a, b) — минуты с постоянным закрытием (нулевая дисперсия)."""
    c = start * np.exp(np.cumsum(rng.normal(0, vol, n)))
    c = np.maximum(np.rint(c), 1).astype(np.int64)
    if flat:
        c[flat[0]:flat[1]] = c[flat[0]]
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + rng.integers(0, 20, n)
    l = np.maximum(np.minimum(o, c) - rng.integers(0, 20, n), 1)
    qv = rng.integers(1, 10 ** 7, n)
    z = rng.random(n) < gaps
    z[:listed] = True
    o = np.where(z, 0, o)
    return dict(o=o.astype(np.int64), h=h.astype(np.int64), l=l.astype(np.int64), c=c.astype(np.int64), qv=qv.astype(np.int64))


def write_b42(path, a):
    gate.write_b42(path, a)


def cf_of(a):
    return ft.ffill_close(a["o"], a["c"])


# ---- определения вручную -----------------------------------------------------------------------------------------------------------------

def test_returns_by_hand():
    """r_h = cf[b] / cf[b − h] − 1, b = t − 1; нет базы (b − h < 0), b ≥ N (кэш кончился), база до листинга (NaN), нулевая база — NaN. cf[m] = m + 1; до минуты 50 — NaN; cf[100] = 0."""
    cf = np.arange(1, 2001, dtype=float)
    cf[:50] = np.nan
    cf[100] = 0.0
    b = np.array([10, 54, 55, 101, 105, 160, 165, 1999, 2000, 2001], dtype=np.int64)
    blk = fam.returns_block(cf, b)
    nan = np.nan
    want = {1: [nan, 55 / 54 - 1, 56 / 55 - 1, nan, 106 / 105 - 1, 161 / 160 - 1, 166 / 165 - 1, 2000 / 1999 - 1, nan, nan],
            5: [nan, nan, 56 / 51 - 1, 102 / 97 - 1, nan, 161 / 156 - 1, 166 / 161 - 1, 2000 / 1995 - 1, nan, nan],
            60: [nan, nan, nan, nan, nan, nan, 166 / 106 - 1, 2000 / 1940 - 1, nan, nan],
            1440: [nan, nan, nan, nan, nan, nan, nan, 2000 / 560 - 1, nan, nan]}
    for h, w in want.items():
        assert eqv(blk[:, fam.HORIZONS.index(h)], w), (h, blk[:, fam.HORIZONS.index(h)])
    assert np.isnan(fam.returns_block(None, b)).all()


def test_zero_base_is_nan():
    cf = np.array([5.0, 0.0, 3.0, 4.0, 6.0, 7.0, 8.0, 9.0])
    h1 = fam.returns_block(cf, np.array([1, 2, 3], dtype=np.int64))[:, fam.HORIZONS.index(1)]
    assert eqv(h1, [0.0 / 5.0 - 1.0, np.nan, 4.0 / 3.0 - 1.0])             # b = 1: закрытие 0 — число (−1); b = 2: база cf[1] = 0 → NaN
    q = fam.step_returns(np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))
    assert np.isnan(q[:6]).all() and q[6] == 6.0 / 1.0 - 1.0               # q[5] = 5 / 0 − 1 = inf → NaN


def test_rv_and_corr_by_hand():
    """На ряде с известными q: std ddof 1 12 доходностей и корреляция 48 пар — числа из numpy на тех же списках; сетка заякорена на b."""
    rng = np.random.default_rng(1)
    n = 900
    qx = np.full(n, np.nan)
    qy = np.full(n, np.nan)
    qx[5:] = rng.normal(0, 0.003, n - 5)
    qy[5:] = 0.5 * qx[5:] + rng.normal(0, 0.002, n - 5)
    b = np.array([700, 701, 702, 703, 704], dtype=np.int64)                 # все пять остатков по модулю 5
    out = np.empty(len(b))
    fam._rv_kernel(qx, b, 60, out)
    for i, bi in enumerate(b):
        v = [qx[bi - 5 * k] for k in range(12)]
        assert abs(out[i] - np.std(v, ddof=1)) < 1e-15
    fam._corr_kernel(qx, qy, b, 240, out)
    for i, bi in enumerate(b):
        xs = [qx[bi - 5 * k] for k in range(48)]
        ys = [qy[bi - 5 * k] for k in range(48)]
        assert abs(out[i] - np.corrcoef(xs, ys)[0, 1]) < 1e-12
    assert out.min() > 0.3                                                  # зависимость видна: тест не пустой


def test_min_valid_and_zero_variance():
    """Окно 60 минут от b = 299: m = 299, 294, …, 244 (12 доходностей); q определены с минуты `fv`: нужно ≥ 10 определённых; нулевая дисперсия — rv = 0 (число), corr = NaN."""
    rng = np.random.default_rng(2)
    q = rng.normal(0, 0.002, 300)
    out = np.empty(1)
    b = np.array([299], dtype=np.int64)
    for fv, n_valid in ((244, 12), (249, 11), (250, 10), (255, 9), (299, 1)):
        qq = q.copy()
        qq[:fv] = np.nan
        fam._rv_kernel(qq, b, 60, out)
        vals = [qq[299 - 5 * k] for k in range(12) if not np.isnan(qq[299 - 5 * k])]
        assert len(vals) == n_valid
        if n_valid >= fam.MIN_VALID:
            assert abs(out[0] - np.std(vals, ddof=1)) < 1e-15, (fv, out[0])
        else:
            assert np.isnan(out[0]), (fv, out[0])
    flat = np.zeros(300)
    fam._rv_kernel(flat, b, 60, out)
    assert out[0] == 0.0
    fam._corr_kernel(flat, q, b, 240, out)
    assert np.isnan(out[0])                                                 # corr при нулевой дисперсии — NaN
    fam._corr_kernel(q, np.full(300, 0.001), b, 240, out)
    assert np.isnan(out[0])                                                 # константа ≠ 0 тоже (проверка точным равенством)
    fam._corr_kernel(q, q, b, 240, out)
    assert out[0] == 1.0                                                    # зажато в [−1, 1]
    fam._corr_kernel(q, -q, b, 240, out)
    assert out[0] == -1.0
    qq = q.copy()
    qq[:293] = np.nan                                                       # 2 пары из 48 < 10
    fam._corr_kernel(qq, q, b, 240, out)
    assert np.isnan(out[0])
    fam._rv_kernel(q, np.array([300, 1000], dtype=np.int64), 60, np.empty(2))     # b ≥ N: не падает


# ---- ядра = независимый счёт --------------------------------------------------------------------------------------------------------------

def test_columns_for_equals_brute():
    rng = np.random.default_rng(4)
    arrs = dict(own=walk(rng, vol=0.003, listed=1500, flat=(2500, 2800)), btc=walk(rng, vol=0.001), eth=walk(rng, vol=0.0015, gaps=0.5), flat=walk(rng, vol=0.003, flat=(0, N), gaps=0.0))
    cfs = {k: cf_of(v) for k, v in arrs.items()}
    t = np.concatenate([np.array([0, 1, 2, 6, 11, 60, 61, 240, 1440, 1441, 1442, 1500, 1501, 1510, 1520, 2900, N - 1, N, N + 1, N + 7]), rng.integers(1, N + 5, 300)]).astype(np.int64)
    n_cells = n_defined = 0
    for own in ("own", "flat", "btc"):
        for sgn in (1.0, -1.0):
            s = np.full(len(t), sgn)
            got = fam.columns_for(cfs[own], cfs["btc"], cfs["eth"], t - 1, s)
            pl = {k: gate.ffill_py(arrs[k]["o"], arrs[k]["c"]) for k in cfs}
            ref = np.array([gate.brute_row(pl[own], pl["btc"], pl["eth"], int(ti), sgn) for ti in t])
            bad, mx, _, _ = gate.compare(got, ref)
            assert bad.sum() == 0 and mx < 1e-12, (own, sgn, dict(zip(COLS, bad)), mx)
            n_cells += got.size
            n_defined += int(np.isfinite(got).sum())
            if own == "own":                                                       # каждый из 27 столбцов где-то определён и где-то NaN: рисунок NaN не вырожден
                assert (np.isfinite(got).sum(axis=0) > 5).all(), dict(zip(COLS, np.isfinite(got).sum(axis=0)))
                assert (np.isnan(got).sum(axis=0) > 0).all()
            if own == "flat":                                                      # постоянная цена: ходы 0, волатильность 0, корреляция — NaN (нулевая дисперсия)
                assert np.nanmax(np.abs(got[:, 2 * fam.NH:3 * fam.NH])) == 0.0 and np.nanmax(got[:, COLS.index("mk_rv5_own_60")]) == 0.0 and np.isnan(got[:, -2:]).all()
    assert n_defined > 0.5 * n_cells


def test_missing_refs():
    """Нет кэша BTC → mk_btc, mk_rv5_btc, mk_rel, mk_corr5 = NaN; нет ETH → mk_eth; нет своей монеты → mk_own, mk_rel, mk_rv5_own, mk_corr5; остальные столбцы считаются."""
    rng = np.random.default_rng(5)
    cf = cf_of(walk(rng))
    b = np.arange(1500, 1520, dtype=np.int64)
    s = np.ones(len(b))
    ref_btc, ref_eth = cf_of(walk(rng)), cf_of(walk(rng))
    full = fam.columns_for(cf, ref_btc, ref_eth, b, s)
    no_btc = fam.columns_for(cf, None, ref_eth, b, s)
    no_eth = fam.columns_for(cf, ref_btc, None, b, s)
    no_own = fam.columns_for(None, ref_btc, ref_eth, b, s)
    ix = {c: i for i, c in enumerate(COLS)}
    deps = {"btc": [c for c in COLS if c.startswith(("mk_btc", "mk_rel", "mk_rv5_btc", "mk_corr5"))],
            "eth": [c for c in COLS if c.startswith("mk_eth")],
            "own": [c for c in COLS if c.startswith(("mk_own", "mk_rel", "mk_rv5_own", "mk_corr5"))]}
    assert np.isfinite(full).all()
    for name, x in (("btc", no_btc), ("eth", no_eth), ("own", no_own)):
        cols = deps[name]
        assert np.isnan(x[:, [ix[c] for c in cols]]).all(), name
        rest = [ix[c] for c in COLS if c not in cols]
        assert eqv(x[:, rest], full[:, rest]), name                       # независимые от пропавшего ряда столбцы не тронуты


def test_sign_symmetry():
    """Шорт = −лонг у столбцов с знаком (mk_*_s*, mk_rel_s*), rv5 и corr5 без знака — те же."""
    rng = np.random.default_rng(6)
    cfs = [cf_of(walk(rng, vol=v)) for v in (0.003, 0.001, 0.002)]
    b = rng.integers(1500, N, 200).astype(np.int64)
    lo = fam.columns_for(cfs[0], cfs[1], cfs[2], b, np.ones(len(b)))
    sh = fam.columns_for(cfs[0], cfs[1], cfs[2], b, -np.ones(len(b)))
    sg = [i for i, c in enumerate(COLS) if "_s" in c.replace("mk_", "") and "rv5" not in c and "corr5" not in c]
    un = [i for i in range(len(COLS)) if i not in sg]
    assert len(sg) == 21 and len(un) == 6
    assert eqv(sh[:, sg], -lo[:, sg]) and eqv(sh[:, un], lo[:, un])


def test_rel_is_own_minus_btc():
    rng = np.random.default_rng(7)
    cfs = [cf_of(walk(rng, vol=v)) for v in (0.003, 0.001, 0.002)]
    b = rng.integers(1500, N, 200).astype(np.int64)
    for sgn in (1.0, -1.0):
        x = fam.columns_for(cfs[0], cfs[1], cfs[2], b, np.full(len(b), sgn))
        for h in fam.REL_H:
            assert eqv(x[:, COLS.index("mk_rel_s%d" % h)], x[:, COLS.index("mk_own_s%d" % h)] - x[:, COLS.index("mk_btc_s%d" % h)])


# ---- сборка -----------------------------------------------------------------------------------------------------------------------------------

def make_world(tmp, rng, with_btc=True, with_eth=True):
    cache = os.path.join(tmp, "cache")
    os.makedirs(cache, exist_ok=True)
    arrs = {}
    for sy in ("BTCUSDT", "ETHUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT"):
        arrs[sy] = walk(rng, vol=0.001 if sy == "BTCUSDT" else 0.003, listed=1700 if sy == "CCCUSDT" else 0)
        if (sy == "BTCUSDT" and not with_btc) or (sy == "ETHUSDT" and not with_eth):
            continue
        write_b42(os.path.join(cache, sy + ".b42"), arrs[sy])
    return cache, arrs


def make_rows(rng, n=240, win="wa", side="long"):
    syms = rng.choice(["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "BTCUSDT", "ETHUSDT"], n)               # DDD — без кэша
    t = rng.integers(1, N + 3, n)
    df = pd.DataFrame(dict(row_id=np.arange(100, 100 + n, dtype=np.int64)[::-1], src=0, sym=syms, level=0, sig_ms=(t * 60_000 + rng.integers(0, 60_000, n)).astype(np.int64)))
    df["win"], df["side"] = win, side
    df["abs_ms"] = 1_781_654_400_000 + df["sig_ms"]                                                       # 17.06.2026 + sig_ms (< 14.11)
    return df


def reference(rows, arrs, side, with_btc=True, with_eth=True):
    s = fam.SIGN[side]
    pl = {k: gate.ffill_py(v["o"], v["c"]) for k, v in arrs.items()}
    pl["DDDUSDT"] = None
    if not with_btc:
        pl["BTCUSDT"] = None
    if not with_eth:
        pl["ETHUSDT"] = None
    return np.array([gate.brute_row(pl[x.sym], pl["BTCUSDT"], pl["ETHUSDT"], x.sig_ms // 60_000, s) for x in rows.itertuples()])


def test_compute_equals_brute_rowwise():
    rng = np.random.default_rng(8)
    for side in ("long", "short"):
        for refs in ((True, True), (False, True), (True, False)):
            with tempfile.TemporaryDirectory() as tmp:
                cache, arrs = make_world(tmp, rng, *refs)
                rows = make_rows(rng, side=side)
                info = {}
                got = fam.compute(rows, "wa", side, procs=1, cache_dir=cache, info=info)
                ref = reference(rows, arrs, side, *refs)
                assert list(got.columns) == COLS and len(COLS) == 27 and got.index.name == "row_id" and (got.dtypes == np.float64).all()
                assert got.index.tolist() == rows["row_id"].tolist() and len(got) == len(rows)           # порядок и состав строк — как на входе, ничего не выброшено
                bad, mx, _, _ = gate.compare(got.to_numpy(), ref)
                assert bad.sum() == 0 and mx < 1e-12, (side, refs, dict(zip(COLS, bad)), mx)
                assert got.loc[rows.loc[rows.sym == "DDDUSDT", "row_id"], [c for c in COLS if "own" in c or c.startswith(("mk_rel", "mk_corr5"))]].isna().all().all()   # монеты без кэша — NaN
                assert "DDDUSDT" in info["missing_cache"] and info["rows"] == len(rows)
                assert info["missing_ref"] == [x for x, w in (("BTCUSDT", refs[0]), ("ETHUSDT", refs[1])) if not w]
                assert got["mk_own_s60"].notna().mean() > 0.4
                assert (got["mk_corr5_1440"].notna().sum() > 20) == refs[0]                                  # нет BTC — corr NaN целиком


def test_compute_procs_equal_serial():
    rng = np.random.default_rng(9)
    with tempfile.TemporaryDirectory() as tmp:
        cache, _ = make_world(tmp, rng)
        rows = make_rows(rng, n=300)
        a = fam.compute(rows, "wa", "long", procs=1, cache_dir=cache)
        b = fam.compute(rows, "wa", "long", procs=3, cache_dir=cache)
        assert eqv(a.to_numpy(), b.to_numpy())


def test_compute_causal_garbage_minutes():
    """Мусор в минутках монеты, BTC и ETH с минуты t и дальше (`garbage_from`) не меняет строку в t — через файлы и `compute`; контроль: на минуте t мусор виден."""
    rng = np.random.default_rng(11)
    with tempfile.TemporaryDirectory() as tmp:
        cache, arrs = make_world(tmp, rng)
        rows = make_rows(rng, n=40)
        rows = rows[rows.sym.isin(["AAAUSDT", "BBBUSDT", "CCCUSDT"])]
        base = fam.compute(rows, "wa", "long", procs=1, cache_dir=cache)
        n_cmp = seen = 0
        for x in rows.itertuples():
            t = x.sig_ms // 60_000
            g_dir = os.path.join(tmp, "g%d" % x.row_id)
            os.makedirs(g_dir)
            for sy in ("BTCUSDT", "ETHUSDT", x.sym):
                write_b42(os.path.join(g_dir, sy + ".b42"), ft.garbage_from(arrs[sy], t, rng))
            one = rows[rows.row_id == x.row_id]
            got = fam.compute(one, "wa", "long", procs=1, cache_dir=g_dir)
            assert eqv(got.loc[x.row_id].to_numpy(), base.loc[x.row_id].to_numpy()), x.row_id
            n_cmp += 1
            late = fam.compute(one.assign(sig_ms=one["sig_ms"] + 60_000), "wa", "long", procs=1, cache_dir=g_dir)     # минута t + 1: последняя закрытая — мусорная t
            real_late = fam.compute(one.assign(sig_ms=one["sig_ms"] + 60_000), "wa", "long", procs=1, cache_dir=cache)
            seen += int(not eqv(late.to_numpy(), real_late.to_numpy()))
        assert n_cmp > 20 and seen > 0.8 * n_cmp


def test_guards():
    rng = np.random.default_rng(12)
    with tempfile.TemporaryDirectory() as tmp:
        cache, _ = make_world(tmp, rng)
        rows = make_rows(rng, n=10)
        for bad in (rows.assign(abs_ms=st.LAST_MS), rows.assign(side="short"), rows.assign(win="wb"), pd.concat([rows, rows.iloc[:1]])):
            try:
                fam.compute(bad, "wa", "long", procs=1, cache_dir=cache)
                assert False
            except (st.GuardError, ValueError):
                pass
        try:
            fam.compute(rows, "wa", "up", procs=1, cache_dir=cache)
            assert False
        except ValueError:
            pass


def test_empty_rows():
    rng = np.random.default_rng(13)
    with tempfile.TemporaryDirectory() as tmp:
        cache, _ = make_world(tmp, rng)
        got = fam.compute(make_rows(rng, n=5).iloc[:0], "wa", "long", procs=1, cache_dir=cache)
        assert len(got) == 0 and list(got.columns) == COLS


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print("ok   ", name)
        except Exception:  # noqa: BLE001
            fails += 1
            print("FAIL ", name)
            traceback.print_exc()
    print("%d из %d зелёных" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
