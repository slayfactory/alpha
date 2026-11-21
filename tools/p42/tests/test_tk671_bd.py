"""Тесты §13.71 (TK-671), семейство `bd` (`tk671_bd.py`): чтение суток, признаки против `bookdepth_feat.features_at`, охват ω (вне — NaN без чтения), причинность (мусор после σ − 30 с), `turn1h` из `.b42`,
охрана 14.11. Синтетика (временные zip / .b42), данных srv нет.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk671_bd.py` — выполняет все test_* и печатает итог."""
import datetime as dt
import io
import os
import struct
import sys
import tempfile
import traceback
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import bookdepth_feat as bf  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk648_feat as f48  # noqa: E402
import tk671_bd as bd  # noqa: E402

WC = {x.name: x for x in st.TRADE_WINDOWS}["wc"]                       # T0 = 2026-09-01
BANDS = bf.BANDS


def utc(y, m, d, hh=0, mm=0, ss=0.0):
    return dt.datetime(y, m, d, hh, mm, int(ss), int(round((ss % 1) * 1e6)), tzinfo=dt.timezone.utc).timestamp()


def write_day(zdir, sym, day, snaps, extra_dup=False, drop_band=None, phase=4):
    """Суточный zip как у Binance: по 12 строк на снимок (полосы в порядке `BANDS`), `snaps` — массив (n, 12) notional."""
    os.makedirs(os.path.join(zdir, sym), exist_ok=True)
    d0 = dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp()
    lines = ["timestamp,percentage,depth,notional"]
    for k, row in enumerate(snaps):
        ts = dt.datetime.fromtimestamp(d0 + phase + 30 * k, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for b, v in zip(BANDS, row):
            if b == drop_band:
                continue
            lines.append("%s,%.2f,%.8f,%.8f" % (ts, b, v * 3.0, v))
        if extra_dup and k == 5:
            lines.append("%s,%.2f,%.8f,%.8f" % (ts, BANDS[0], 1.0, 123.5))      # дубль (ts, полоса): последний побеждает
    with zipfile.ZipFile(os.path.join(zdir, sym, "%s-bookDepth-%s.zip" % (sym, day)), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("%s-bookDepth-%s.csv" % (sym, day), "\n".join(lines) + "\n")


def synth_snaps(n, seed):
    rng = np.random.default_rng(seed)
    return np.round(rng.uniform(1e5, 5e6, size=(n, 12)), 6)


def write_b42(cdir, sym, n_min, qv):
    os.makedirs(cdir, exist_ok=True)
    z = np.zeros(n_min, dtype="<i8")
    cols = [z, z, z, z, np.asarray(qv, dtype="<i8")]
    with open(os.path.join(cdir, "%s.b42" % sym), "wb") as f:
        f.write(b"B42\x01" + struct.pack("<I", n_min))
        for c in cols:
            f.write(c.tobytes())


def same(a, b):
    return np.array_equal(np.asarray(a, dtype=float), np.asarray(b, dtype=float), equal_nan=True)


def test_load_day_fast_equals_pandas_loader():
    with tempfile.TemporaryDirectory() as z:
        write_day(z, "AAAUSDT", "2026-09-20", synth_snaps(200, 1), extra_dup=True)
        write_day(z, "BBBUSDT", "2026-09-20", synth_snaps(150, 2), drop_band=2.0)           # нет полосы +2 % → столбец NaN
        for sym in ("AAAUSDT", "BBBUSDT"):
            a, b = bf.load_day(z, sym, "2026-09-20"), bd.load_day_fast(z, sym, "2026-09-20")
            assert np.array_equal(a[0], b[0]) and same(a[1], b[1]), sym
        assert bd.load_day_fast(z, "AAAUSDT", "2026-09-21") is None                         # нет файла
        a, b = bf.load_sym(z, "AAAUSDT", ["2026-09-19", "2026-09-20"]), bd.load_sym_fast(z, "AAAUSDT", ["2026-09-19", "2026-09-20"])
        assert np.array_equal(a[0], b[0]) and same(a[1], b[1])


def test_duplicate_timestamp_band_last_wins():
    with tempfile.TemporaryDirectory() as z:
        write_day(z, "AAAUSDT", "2026-09-20", synth_snaps(10, 3), extra_dup=True)
        t, m = bd.load_day_fast(z, "AAAUSDT", "2026-09-20")
        assert m[5, 0] == 123.5 and len(t) == 10                                            # снимок 5, полоса −5 %: дубль последний


def test_features_vec_equals_scalar_original():
    rng = np.random.default_rng(5)
    n = 400
    t = 1000.0 + 30.0 * np.arange(n) + rng.integers(0, 3, size=n)
    m = rng.uniform(1e4, 1e7, size=(n, 12))
    m[rng.random((n, 12)) < 0.02] = np.nan
    m[rng.random((n, 12)) < 0.02] = 0.0
    s = np.sort(rng.uniform(900.0, t[-1] + 900.0, size=300))
    s[7] = t[100] + 30.0                                                                    # ровно на границе: снимок 100 допустим (ts + 30 ≤ σ)
    long_side = rng.random(300) < 0.5
    turn = rng.uniform(1e5, 1e8, size=300)
    turn[3], turn[4] = 0.0, np.nan
    got = bd.features_vec(t, m, s, long_side, turn)
    modes = f48.day_mode(t)
    for k in range(300):
        want = bf.features_at(t, m, s[k], bool(long_side[k]), float(turn[k]))
        for name in bf.FEATS:
            assert (np.isnan(want[name]) and np.isnan(got[name][k])) or abs(want[name] - got[name][k]) <= 1e-12 * max(1.0, abs(want[name])), (k, name, want[name], got[name][k])
        i = int(np.searchsorted(t, s[k] - bf.LAG_S, side="right")) - 1
        age_ok = i >= 0 and s[k] - t[i] <= bf.MAX_AGE_S
        assert np.isnan(got["bd_age_s"][k]) == (not age_ok)
        if age_ok:
            assert got["bd_age_s"][k] == s[k] - t[i] and got["bd_mode"][k] == modes[int(t[i] // 86400)]


def test_causal_rule_boundary_and_hole():
    t = 1000.0 + 30.0 * np.arange(40)
    m = np.ones((40, 12)) * 1e6
    m[:, bd.BI[0.2]] = 2e6 + np.arange(40) * 1e4
    g = bd.features_vec(t, m, np.array([t[30] + 30.0, t[30] + 29.9, t[-1] + 700.0, t[-1] + 630.0]), np.array([True] * 4), np.full(4, 1e7))
    want30 = (1e6 - m[30, bd.BI[0.2]]) / (1e6 + m[30, bd.BI[0.2]])
    want29 = (1e6 - m[29, bd.BI[0.2]]) / (1e6 + m[29, bd.BI[0.2]])
    assert abs(g["bd_imb02"][0] - want30) < 1e-15 and abs(g["bd_imb02"][1] - want29) < 1e-15   # снимок на σ − 30 с берётся, на σ − 29,9 с — нет
    assert np.isnan(g["bd_imb02"][2]) and np.isnan(g["bd_imb02"][3])                          # возраст > 600 с (700 и σ − ts = 630)
    assert np.isnan(g["bd_age_s"][3]) and np.isnan(g["bd_mode"][3])


def test_garbage_after_cut_changes_nothing():
    rng = np.random.default_rng(9)
    n = 300
    t = 5000.0 + 30.0 * np.arange(n)
    m = rng.uniform(1e4, 1e7, size=(n, 12))
    s = np.array([t[150] + 31.0, t[200] + 45.0, t[250] + 30.0])
    turn = np.full(3, 5e6)
    clean = bd.features_vec(t, m, s, np.array([True, False, True]), turn)
    for k in range(3):
        late = t > s[k] - bf.LAG_S
        m2 = m.copy()
        m2[late] = rng.normal(0, 1e9, size=(int(late.sum()), 12))
        t2 = t.copy()
        t2[late] = s[k] - bf.LAG_S + rng.uniform(0.001, 900.0, size=int(late.sum()))
        o = np.argsort(t2, kind="stable")
        d = bd.features_vec(t2[o], m2[o], s[k:k + 1], np.array([[True, False, True][k]]), turn[k:k + 1])
        for name in list(bf.FEATS) + ["bd_age_s"]:
            assert same(clean[name][k], d[name][0]), (k, name)


def test_turn1h_from_qv_hand_computed():
    qv = np.full(200, 1_000_000, dtype=np.int64)
    qv[40] = 5_000_000                                                                      # внутри окна t=100: [40, 100)
    t = np.array([59, 60, 100, 200, 201], dtype=np.int64)
    got = bd.turn1h_from_qv(qv, t)
    assert np.isnan(got[0]) and np.isnan(got[4])                                            # t < 60 и t > n — NaN
    assert got[1] == 640.0 and got[2] == (59 * 1_000_000 + 5_000_000) / 1e5 and got[3] == 600.0                    # t = 60: окно [0; 60) тоже содержит минуту 40
    q2 = qv.copy()
    q2[100:] = 12345678901234                                                               # минуты ≥ t не входят
    assert bd.turn1h_from_qv(q2, np.array([100]))[0] == got[2]
    big = np.full(70, 2 ** 62, dtype=np.int64)                                              # переполнение по модулю 2^64, как wrapping_add Rust
    assert bd.turn1h_from_qv(big, np.array([70]))[0] == float(np.int64(0)) / 1e5               # 60 · 2^62 = 15 · 2^64 ≡ 0


def run_compute(rows, zdir, cdir, **kw):
    return bd.compute(rows, "wc", "long", procs=kw.pop("procs", 1), zdir=zdir, cache_dir=cdir, **kw)


def mk_rows(sigs, syms, **extra):
    n = len(sigs)
    sig_ms = np.array([int(round((s - WC.t0_ms / 1000.0) * 1000)) for s in sigs], dtype=np.int64)
    d = dict(row_id=np.arange(100, 100 + n, dtype=np.int64), sym=syms, sig_ms=sig_ms, abs_ms=sig_ms + WC.t0_ms, side="long", win="wc")
    d.update(extra)
    return pd.DataFrame(d)


def test_compute_omega_nan_outside_and_no_archive():
    with tempfile.TemporaryDirectory() as tmp:
        z, c = os.path.join(tmp, "zip"), os.path.join(tmp, "cache")
        snaps = synth_snaps(2880, 11)
        for day in ("2026-09-13", "2026-09-14", "2026-10-07", "2026-10-09", "2026-11-13"):
            write_day(z, "AAAUSDT", day, snaps)
        write_b42(c, "AAAUSDT", 115200, np.full(115200, 1_000_000))
        sigs = [utc(2026, 9, 14, 12, 0, 1.5),        # первые сутки ω — есть
                utc(2026, 9, 13, 12, 0, 1.5),        # накануне ω — NaN, хотя файл суток есть
                utc(2026, 10, 8, 12, 0, 1.5),        # дыра 08.10 — NaN
                utc(2026, 10, 9, 0, 30, 0.0),        # 09.10 после дыры — есть (сутки 09.10)
                utc(2026, 11, 13, 23, 59, 59.0),     # последние сутки — есть
                utc(2026, 9, 14, 12, 0, 1.5)]        # монета без архива — NaN
        rows = mk_rows(sigs, ["AAAUSDT"] * 5 + ["ZZZUSDT"])
        st_ = {}
        out = run_compute(rows, z, c, stats=st_)
        assert list(out.index) == list(range(100, 106)) and list(out.columns) == bd.COLS and (out.dtypes == np.float64).all()
        ok = out["bd_imb1"].notna().tolist()
        assert ok == [True, False, False, True, True, False], ok
        assert st_["rows_omega"] == 4 and st_["coins"] == 2 and st_["coins_with_archive"] == 1
        # 09.10 00:30: сутки 08.10 (σ − 1 ч) файла нет, 09.10 есть → значения считаются только по 09.10, 30-секундные снимки есть
        assert np.isfinite(out["bd_age_s"].iloc[3]) and 30.0 <= out["bd_age_s"].iloc[3] <= 60.0 + 1e-6
        assert np.isnan(out["bd_thin30"].iloc[3]) and np.isfinite(out["bd_thin5"].iloc[3])      # назад только 58 снимков суток 09.10: 60 нет → thin30 NaN, thin5 есть


def test_row_independent_of_other_rows_lag_does_not_cross_unloaded_days():
    """Строка 09.10 00:30 при загруженных сутках 14.09 и 13.11 (других строк монеты) даёт те же значения, что и одна: thin30 NaN, а не снимок из чужих суток."""
    with tempfile.TemporaryDirectory() as tmp:
        z = os.path.join(tmp, "zip")
        for day in ("2026-09-14", "2026-10-09", "2026-11-13"):
            write_day(z, "AAAUSDT", day, synth_snaps(2880, 30 + int(day[-2:])))
        sigs = [utc(2026, 9, 14, 12, 0, 1.5), utc(2026, 10, 9, 0, 30, 0.0), utc(2026, 11, 13, 12, 0, 1.5)]
        rows = mk_rows(sigs, ["AAAUSDT"] * 3, turn1h=[2e6, 3e6, 4e6])
        many = run_compute(rows, z, "/x")
        for i in range(3):
            one = run_compute(rows.iloc[[i]], z, "/x")
            assert same(one.iloc[0], many.iloc[i]), (i, one.iloc[0].to_dict(), many.iloc[i].to_dict())
        assert np.isnan(many["bd_thin30"].iloc[1]) and np.isfinite(many["bd_thin5"].iloc[1])


def test_compute_turn_from_column_vs_b42_and_garbage_cache():
    with tempfile.TemporaryDirectory() as tmp:
        z, c = os.path.join(tmp, "zip"), os.path.join(tmp, "cache")
        for day in ("2026-09-20",):
            write_day(z, "AAAUSDT", day, synth_snaps(2880, 12))
        t_sig = utc(2026, 9, 20, 12, 0, 1.5)
        rows = mk_rows([t_sig], ["AAAUSDT"])
        tmin = int(rows["sig_ms"].iloc[0] // 60_000)
        qv = np.full(115200, 2_000_000)
        write_b42(c, "AAAUSDT", 115200, qv)
        a = run_compute(rows, z, c)
        col = run_compute(rows.assign(turn1h=60 * 2_000_000 / 1e5), z, c)
        assert same(a, col)                                                                  # столбец turn1h и `.b42` дают одно и то же
        qv2 = qv.copy()
        qv2[tmin:] = np.random.default_rng(1).integers(-10 ** 12, 10 ** 12, size=len(qv2) - tmin)
        write_b42(c, "AAAUSDT", 115200, qv2)
        assert same(run_compute(rows, z, c), a)                                              # мусор в минутках ≥ t не влияет
        os.remove(os.path.join(c, "AAAUSDT.b42"))
        n = run_compute(rows, z, c)
        assert np.isnan(n["bd_depth_turn"].iloc[0]) and np.isfinite(n["bd_imb1"].iloc[0])    # нет кэша: только bd_depth_turn NaN


def test_compute_equals_original_per_row_loop():
    with tempfile.TemporaryDirectory() as tmp:
        z = os.path.join(tmp, "zip")
        for day in ("2026-09-20", "2026-09-21"):
            write_day(z, "AAAUSDT", day, synth_snaps(2880, 20 + int(day[-1])))
        sigs = [utc(2026, 9, 20, 0, 40, 0.25), utc(2026, 9, 20, 9, 10, 5.0), utc(2026, 9, 21, 0, 5, 30.0), utc(2026, 9, 21, 23, 59, 40.0)]
        turn = [3e6, 4e6, 5e6, 6e6]
        rows = mk_rows(sigs, ["AAAUSDT"] * 4, turn1h=turn)
        out = run_compute(rows, z, "/nonexistent")
        s_abs = f48.abs_s(WC, rows["sig_ms"].to_numpy())
        # оригинал читает архив из `tk648_feat.ZIP` — подменим путь на временный
        old = f48.ZIP
        f48.ZIP = z
        try:
            want = dict(f48.one_sym(("AAAUSDT", [(i, float(s_abs[i]), True, turn[i]) for i in range(4)])))
        finally:
            f48.ZIP = old
        for i in range(4):
            for k in bd.COLS:
                a, b = out[k].iloc[i], want[i][k]
                assert (np.isnan(a) and np.isnan(b)) or abs(a - b) <= 1e-12 * max(1.0, abs(b)), (i, k, a, b)


def test_guard_forward_window():
    with tempfile.TemporaryDirectory() as tmp:
        rows = mk_rows([utc(2026, 11, 14, 0, 0, 1.0)], ["AAAUSDT"])
        try:
            run_compute(rows, tmp, tmp)
        except SystemExit:
            return
        raise AssertionError("охрана 14.11 не сработала")


def test_arg_checks():
    rows = mk_rows([utc(2026, 9, 20, 12, 0, 1.5)], ["AAAUSDT"])
    for bad in (rows.assign(win="wa"), rows.assign(side="short"), pd.concat([rows, rows])):
        try:
            bd.compute(bad, "wc", "long")
        except ValueError:
            continue
        raise AssertionError("ошибка аргументов не поймана")


if __name__ == "__main__":
    fails = 0
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for k, v in tests:
        try:
            v()
            print("ok  ", k)
        except BaseException:
            fails += 1
            print("FAIL", k)
            traceback.print_exc()
    print("%d тестов, провалов %d" % (len(tests), fails))
    sys.exit(1 if fails else 0)
