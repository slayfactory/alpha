"""Тесты §13.71 (TK-671), семейство TK-668 (`tk671_t668`): ядра = оригиналу `tk668_feat` (X24 / X7 / XP / XV, шок BTC, R и «свой уровень»), парная сумма = numpy, сборка `compute` (индекс, столбцы, NaN, нет строки не выброшена,
нет файла уровней → R пусто), причинность (мусор в минутках ≥ t и в уровнях позже t не меняет значение), охрана суток ≥ 14.11. Синтетика, без srv.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk671_t668.py`."""
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
import tk671_t668 as fam  # noqa: E402

N = 12000


def same(a, b):
    return (a != a and b != b) or a == b


def eqv(a, b):
    return np.array_equal(np.asarray(a, dtype=float), np.asarray(b, dtype=float), equal_nan=True)


def walk(rng, n=N, gaps=True, start=100000.0, vol=0.001):
    c = start * np.exp(np.cumsum(rng.normal(0, vol, n)))
    c = np.maximum(np.rint(c), 1).astype(np.int64)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + rng.integers(0, 20, n)
    l = np.maximum(np.minimum(o, c) - rng.integers(0, 20, n), 1)
    qv = rng.integers(1, 10 ** 7, n)
    if gaps:
        z = rng.random(n) < 0.03
        z[3000:3200] = True
        o = np.where(z, 0, o)
    return dict(o=o.astype(np.int64), h=h.astype(np.int64), l=l.astype(np.int64), c=c.astype(np.int64), qv=qv.astype(np.int64))


def write_b42(path, a):
    n = len(a["o"])
    with open(path, "wb") as f:
        f.write(b"B42\x01" + n.to_bytes(4, "little"))
        f.write(np.stack([a["o"], a["h"], a["l"], a["c"], a["qv"]]).astype("<i8").tobytes())


# ---- парная сумма -------------------------------------------------------------------------------------------------------------------------

def test_pairwise_sum_is_numpy_sum():
    rng = np.random.default_rng(1)
    for n in list(range(1, 300)) + [1000, 1441, 2016, 2017, 4097]:
        x = rng.normal(0, 1e-3, n)
        assert fam._pw(x, 0, n) == float(x.sum()), n
        if n > 5:
            assert fam._pw(x, 3, n - 3) == float(x[3:].sum()), n


# ---- X24 / X7 / XP / XV -----------------------------------------------------------------------------------------------------------------------

def test_place_kernel_equals_original():
    rng = np.random.default_rng(2)
    a = walk(rng)
    m = ft.Mins(a)
    t = np.concatenate([np.array([0, 1, 1440, 1441, 1442, 5000, 10081, 10082, N - 1, N, N + 1, N + 50]), rng.integers(1000, N + 20, 60)]).astype(np.int64)
    s = rng.choice([-1.0, 1.0], len(t))
    natr = np.where(rng.random(len(t)) < 0.1, 0.0, rng.uniform(0.001, 0.05, len(t)))
    out = np.empty((len(t), 4))
    fam._place_kernel(m.cf, m.hh, m.ll, m.nv, m.P1, m.P2, m.n, t, s, natr, out)
    for i in range(len(t)):
        ref = ft.place_features(m, int(t[i]), s[i], float(natr[i]))
        assert all(same(out[i, k], ref[k]) for k in range(4)), (i, t[i], out[i], ref)
    assert np.isfinite(out).any(axis=0).all()                                                  # тест не пустой: каждый из 4 признаков где-то определён
    assert np.isnan(out[:3]).all()                                                             # t ≤ 1 440 — суток истории нет


# ---- шок BTC --------------------------------------------------------------------------------------------------------------------------------------

def test_btc_columns_equal_original():
    rng = np.random.default_rng(3)
    a = walk(rng)
    cf = ft.ffill_close(a["o"], a["c"])
    r1 = ft.minute_returns(cf)
    t = np.concatenate([np.array([0, 5, 1441, 1442, 1456, 1457, 3000, 3300, N - 1, N, N + 1]), rng.integers(1400, N + 5, 40), rng.integers(3000, 3250, 10)]).astype(np.int64)
    s = rng.choice([-1.0, 1.0], len(t))
    col = fam.btc_columns(cf, r1, t, s)
    for i in range(len(t)):
        for h in ft.HORIZONS:
            rh, sg = ft.btc_shock(cf, r1, int(t[i]), h)
            bz = s[i] * rh / sg if np.isfinite(rh) and np.isfinite(sg) and sg > 0 else float("nan")
            assert same(col["r%d" % h][i], rh) and same(col["sg%d" % h][i], sg) and same(col["bz%d" % h][i], bz), (i, t[i], h)
    assert np.isfinite(col["bz5"]).sum() > 10 and np.isnan(col["bz1"][:3]).all()


# ---- уровни: R -------------------------------------------------------------------------------------------------------------------------------------

def random_levels(rng, syms, n_lev=40, span=12000):
    """Файл `<side>-levels.csv` в формате движка со случайными уровнями: рождение, конец (в т. ч. u32::MAX), 1–4 версии цены; цены на сетке (равные цены у разных уровней — на границу «строго»)."""
    lines = ["sym,level,born,end,first_bar,segs,members"]
    for sy in syms:
        for j in range(n_lev):
            born = int(rng.integers(0, span))
            end = ft.U32_MAX if rng.random() < 0.2 else born + int(rng.integers(5, 3000))
            k = int(rng.integers(1, 5))
            ms = np.sort(rng.integers(born, born + 2000, k))
            ms[0] = born if rng.random() < 0.9 else born + 3                                   # у части уровней первая версия позже рождения — до неё цены нет
            segs = "|".join("%d:%d" % (m, int(rng.integers(90, 111)) * 1000 * (1 if rng.random() < 0.9 else -1)) for m in ms)
            lines.append("%s,%d,%d,%d,%d,%s,%d:1,%d:2" % (sy, j, born, end, born // 2, segs, born // 3, born // 4))
    return "\n".join(lines) + "\n"


def test_room_kernel_equals_original():
    rng = np.random.default_rng(4)
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "long-levels.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write(random_levels(rng, syms))
        lv = ft.parse_levels(p)
        n_inf = n_own = n_val = 0
        for sy in syms:
            sl = lv[sy]
            n = 300
            t = rng.integers(0, 14000, n).astype(np.int64)
            lev = rng.integers(0, 45, n).astype(np.int64)                                      # часть номеров вне списка (40)
            p_ = (rng.integers(85, 116, n) * 1000).astype(np.int64)
            # у трети строк цена = версии своего уровня на t (чтобы «свой найден» срабатывал)
            for i in range(0, n, 3):
                if lev[i] < len(sl.born):
                    px = sl.px_at(int(lev[i]), int(t[i]))
                    if px is not None:
                        p_[i] = px
            s = rng.choice([-1.0, 1.0], n)
            natr = np.where(rng.random(n) < 0.05, 0.0, rng.uniform(0.001, 0.05, n))
            R, own = fam.room_rows(sl, t, p_, lev, s, natr)
            for i in range(n):
                if lev[i] >= len(sl.born) or not natr[i] > 0:
                    assert np.isnan(R[i]) and not own[i]
                    continue
                ref = ft.room_to_next(sl, int(lev[i]), int(t[i]), int(p_[i]), int(s[i]), float(natr[i]))
                assert same(R[i], ref[0]) and bool(own[i]) == ref[2], (sy, i, R[i], ref)
                n_inf += int(np.isinf(R[i]))
                n_own += int(own[i])
                n_val += int(np.isfinite(R[i]))
        assert n_inf > 5 and n_own > 20 and n_val > 100, (n_inf, n_own, n_val)                 # тест видит все три исхода: нет следующего / свой найден / число


def test_room_garbage_after_t_does_not_change():
    """Метки уровней позже t (рождение, версии цены, конец) подменены случайными: R и «свой» на строках с этим t не меняются."""
    rng = np.random.default_rng(5)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x-levels.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write(random_levels(rng, ["AAAUSDT"], n_lev=60))
        sl = ft.parse_levels(p)["AAAUSDT"]
        changed = 0
        for _ in range(60):
            t = int(rng.integers(100, 13000))
            lev = int(rng.integers(0, 60))
            px = sl.px_at(lev, t)
            p0 = px if px is not None and rng.random() < 0.7 else 100000
            for s in (1.0, -1.0):
                ref = fam.room_rows(sl, *(np.array([x]) for x in (t, p0, lev)), np.array([s]), np.array([0.01]))
                g = fam.garbage_levels(sl, t, rng, n_junk=3)
                changed += int(not np.array_equal(g.b, sl.b) or not np.array_equal(g.e, sl.e))
                got = fam.room_rows(g, *(np.array([x]) for x in (t, p0, lev)), np.array([s]), np.array([0.01]))
                assert same(ref[0][0], got[0][0]) and ref[1][0] == got[1][0], (t, lev, ref, got)
        assert changed > 50                                                                      # мусор действительно что-то менял


# ---- сборка -----------------------------------------------------------------------------------------------------------------------------------------

def make_world(tmp, rng):
    cache = os.path.join(tmp, "cache")
    os.makedirs(cache)
    arrs = {}
    for sy in ("BTCUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT"):
        arrs[sy] = walk(rng, vol=0.001 if sy == "BTCUSDT" else 0.003)
        write_b42(os.path.join(cache, sy + ".b42"), arrs[sy])
    names = fam._source_names()
    lv = os.path.join(tmp, "lv")
    os.makedirs(os.path.join(lv, names[0]))
    with open(os.path.join(lv, names[0], "long-levels.csv"), "w", encoding="utf-8") as f:
        f.write(random_levels(rng, ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"], n_lev=30))
    return cache, lv, arrs


def make_rows(rng, n=240, win="wa", side="long"):
    syms = rng.choice(["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "BTCUSDT"], n)                # DDD — без кэша
    t = rng.integers(1400, N + 3, n)
    df = pd.DataFrame(dict(row_id=np.arange(100, 100 + n, dtype=np.int64)[::-1], src=rng.choice([0, 0, 1], n).astype(np.int64), sym=syms, level=rng.integers(0, 33, n).astype(np.int64),
                           sig_ms=(t * 60_000 + rng.integers(0, 60_000, n)).astype(np.int64), level_px_e8=(rng.integers(90, 111, n) * 1000).astype(np.int64) * rng.choice([1, 1, 1, -1], n),
                           natr_e8=rng.integers(0, 5_000_000, n).astype(np.int64)))
    df["win"], df["side"] = win, side
    df["abs_ms"] = 1_781_654_400_000 + df["sig_ms"]                                               # 17.06.2026 + sig_ms (< 14.11)
    return df


def reference(rows, cache, lv, side):
    """Оригинал построчно: `tk668_feat` функции, как в `tk668_run.compute_rows`."""
    names = fam._source_names()
    a_btc = ft.load_b42_full(os.path.join(cache, "BTCUSDT.b42"))
    cf = ft.ffill_close(a_btc["o"], a_btc["c"])
    r1 = ft.minute_returns(cf)
    s = fam.SIGN[side]
    mc, lvc, out = {}, {}, []
    for x in rows.itertuples():
        t = x.sig_ms // ft.MIN_MS
        natr = x.natr_e8 / 1e8
        d = {c: np.nan for c in fam.FEATURES}
        pth = os.path.join(cache, x.sym + ".b42")
        if os.path.exists(pth):
            if x.sym not in mc:
                mc[x.sym] = ft.Mins(ft.load_b42_full(pth))
            d["X24"], d["X7"], d["XP"], d["XV"] = ft.place_features(mc[x.sym], t, s, natr)
        f = os.path.join(lv, names[x.src], side + "-levels.csv")
        if os.path.exists(f):
            if f not in lvc:
                lvc[f] = ft.parse_levels(f)
            sl = lvc[f].get(x.sym)
            if sl is not None and x.level < len(sl.born) and natr > 0:
                d["R"] = ft.room_to_next(sl, x.level, t, abs(x.level_px_e8), int(s), natr)[0]
        for h in ft.HORIZONS:
            rh, sg = ft.btc_shock(cf, r1, t, h)
            d["r%d" % h], d["sg%d" % h] = rh, sg
            d["bz%d" % h] = s * rh / sg if np.isfinite(rh) and np.isfinite(sg) and sg > 0 else np.nan
        out.append(d)
    return pd.DataFrame(out, index=rows["row_id"].to_numpy())[list(fam.FEATURES)]


def test_compute_equals_original_rowwise():
    rng = np.random.default_rng(6)
    for side in ("long", "short"):
        with tempfile.TemporaryDirectory() as tmp:
            cache, lv, _ = make_world(tmp, rng)
            rows = make_rows(rng, side=side)
            if side == "short":
                lvd = os.path.join(lv, fam._source_names()[0])
                os.replace(os.path.join(lvd, "long-levels.csv"), os.path.join(lvd, "short-levels.csv"))
            info = {}
            got = fam.compute(rows, "wa", side, procs=1, cache_dir=cache, levels_dir=lv, info=info)
            ref = reference(rows, cache, lv, side)
            assert list(got.columns) == list(fam.FEATURES) and got.index.name == "row_id" and (got.dtypes == np.float64).all()
            assert got.index.tolist() == rows["row_id"].tolist() and len(got) == len(rows)       # порядок и состав строк — как на входе, ничего не выброшено
            assert np.array_equal(got.to_numpy(), ref.to_numpy(), equal_nan=True)
            nn = got.notna()
            assert nn["X24"].mean() > 0.5 and nn["R"].sum() > 10 and np.isinf(got["R"]).sum() > 0 and nn["bz5"].mean() > 0.5
            assert got.loc[rows.loc[rows.sym == "DDDUSDT", "row_id"], ["X24", "X7", "XP", "XV"]].isna().all().all()     # монеты без кэша — NaN, а BTC-столбцы есть
            assert got.loc[rows.loc[rows.src == 1, "row_id"], "R"].isna().all()                  # у источника 1 файла уровней нет — R пусто
            assert info["levels"][fam._source_names()[1]]["no_levels_file"] is True and info["rows"] == len(rows) and "DDDUSDT" in info["missing_cache"]


def test_compute_causal_garbage_minutes():
    """Мусор в минутках монеты и BTC с минуты t и дальше (`garbage_from`) не меняет признаки строки в t (R, уровни — отдельный тест)."""
    rng = np.random.default_rng(7)
    with tempfile.TemporaryDirectory() as tmp:
        cache, lv, arrs = make_world(tmp, rng)
        rows = make_rows(rng, n=40)
        rows = rows[rows.sym.isin(["AAAUSDT", "BBBUSDT", "CCCUSDT"])]
        base = fam.compute(rows, "wa", "long", procs=1, cache_dir=cache, levels_dir=lv)
        n_cmp = 0
        for x in rows.itertuples():
            t = x.sig_ms // ft.MIN_MS
            g_dir = os.path.join(tmp, "g%d" % x.row_id)
            os.makedirs(g_dir)
            for sy in ("BTCUSDT", x.sym):
                write_b42(os.path.join(g_dir, sy + ".b42"), ft.garbage_from(arrs[sy], t, rng))
            one = rows[rows.row_id == x.row_id]
            got = fam.compute(one, "wa", "long", procs=1, cache_dir=g_dir)
            for c in ("X24", "X7", "XP", "XV", "bz1", "bz5", "bz15", "r1", "r5", "r15", "sg1", "sg5", "sg15"):
                assert same(got.loc[x.row_id, c], base.loc[x.row_id, c]), (x.row_id, c)
                n_cmp += 1
        assert n_cmp > 300


def test_guards():
    rng = np.random.default_rng(8)
    with tempfile.TemporaryDirectory() as tmp:
        cache, lv, _ = make_world(tmp, rng)
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
    rng = np.random.default_rng(9)
    with tempfile.TemporaryDirectory() as tmp:
        cache, lv, _ = make_world(tmp, rng)
        got = fam.compute(make_rows(rng, n=5).iloc[:0], "wa", "long", procs=1, cache_dir=cache)
        assert len(got) == 0 and list(got.columns) == list(fam.FEATURES)


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
