"""Тесты TK-697, семейство `m697` (`tk697_cols`): определения столбцов на малых числах вручную, ядра против независимого счёта, причинность, зеркало шорта.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk697_cols.py` (на srv — `/data/tk592/venv/bin/python`: на ПК pandas сломан)."""
import math
import os
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import sigfeat_lib as sl  # noqa: E402
import tk668_feat as ft  # noqa: E402
import tk697_cols as fam  # noqa: E402
import tk697_gate as gate  # noqa: E402


def walk(rng, n, start=100000.0, vol=0.001, listed=0, gaps=0.02):
    c = start * np.exp(np.cumsum(rng.normal(0, vol, n)))
    c = np.maximum(np.rint(c), 1).astype(np.int64)
    o = np.concatenate([[c[0]], c[:-1]])
    spread = np.maximum(np.rint(c * np.abs(rng.normal(0, vol, n))), 1).astype(np.int64)
    h = np.maximum(o, c) + spread
    l = np.maximum(np.minimum(o, c) - spread, 1)
    qv = rng.integers(1, 10**9, n)
    gap = rng.random(n) < gaps
    o2 = np.where(gap, 0, o)
    o2[:listed] = 0
    return dict(o=o2, h=np.where(gap, 0, h), l=np.where(gap, 0, l), c=np.where(gap, 0, c), qv=qv)


def test_natr_matches_engine_port():
    rng = np.random.default_rng(1)
    a = walk(rng, 6000, listed=300)
    for p in (15, 30, 60):
        mine = fam.natr_kernel(a["o"], a["h"], a["l"], a["c"], p)
        ref = sl.natr_series(a, p)
        ok = ref >= 0
        assert np.array_equal(np.isfinite(mine), ok), p
        # у порта e8 — floor целочисленного деления; float64 отличается меньше чем на 1e-8
        assert np.all(np.abs(mine[ok] - ref[ok] / 1e8) < 1.01e-8), p


def test_sweep_long_hand():
    # W = 2, b = 5: B = минуты 2, 3 (lows 5, 4) → lowB = 4; A = минуты 4, 5 (lows 6, 3) → lowA = 3 на минуте 5
    lw = np.array([9.0, 9.0, 5.0, 4.0, 6.0, 3.0])
    cfs = np.full(6, 4.5)
    nat = np.full(6, 0.01)
    b = np.array([5], dtype=np.int64)
    d, ag = np.empty(1), np.empty(1)
    fam.sweep_kernel(lw, cfs, nat, b, 2, d, ag)
    assert abs(d[0] - (4.0 - 3.0) / 4.5 / 0.01) < 1e-12, d
    assert ag[0] == 1.0, ag                      # t = 6, m = 5
    cfs2 = np.full(6, 3.5)                       # закрытие ниже lowB — возврата нет → вынос не засчитан, глубина 0
    fam.sweep_kernel(lw, cfs2, nat, b, 2, d, ag)
    assert d[0] == 0.0 and math.isnan(ag[0])
    lw2 = np.array([9.0, 9.0, 5.0, 4.0, 6.0, 4.0])   # lowA = lowB — выноса нет
    fam.sweep_kernel(lw2, cfs, nat, b, 2, d, ag)
    assert d[0] == 0.0 and math.isnan(ag[0])
    fam.sweep_kernel(lw, cfs, nat, np.array([2], dtype=np.int64), 2, d, ag)   # окно не помещается
    assert math.isnan(d[0]) and math.isnan(ag[0])
    lw3 = lw.copy()
    lw3[2:4] = np.inf                             # в B нет минуток с данными
    fam.sweep_kernel(lw3, cfs, nat, b, 2, d, ag)
    assert math.isnan(d[0])
    lw4 = np.array([9.0, 9.0, 5.0, 4.0, 3.0, 3.0])   # минимум A на минутах 4 и 5 — берётся последняя (давность 1)
    fam.sweep_kernel(lw4, cfs, nat, b, 2, d, ag)
    assert ag[0] == 1.0


def test_sweep_short_mirror():
    rng = np.random.default_rng(2)
    a = walk(rng, 3000)
    K = 10**12
    # зеркало: шорт по минуткам = лонг по «перевёрнутым» ценам p → K − p (high ↔ low); глубина в цене и давность те же
    flip = dict(o=a["o"], h=K - a["l"], l=K - a["h"], c=K - a["c"], qv=a["qv"])
    lw_s, cfs_s, cf_s = fam.frames(a, -1.0)
    lw_l, cfs_l, cf_l = fam.frames(flip, 1.0)
    b = np.arange(200, 2900, 7, dtype=np.int64)
    nat = np.ones(3000)
    for w in fam.SW_W:
        d1, a1, d2, a2 = (np.empty(len(b)) for _ in range(4))
        fam.sweep_kernel(lw_s, cfs_s, nat, b, w, d1, a1)
        fam.sweep_kernel(lw_l, cfs_l, nat, b, w, d2, a2)
        assert np.array_equal(a1, a2, equal_nan=True), w
        assert np.allclose(d1 * np.abs(cf_s[b]), d2 * np.abs(cf_l[b]), rtol=1e-12, atol=1e-6, equal_nan=True), w
        assert np.isfinite(d1).sum() > 100 and (d1 > 0).sum() > 5, w   # тест не пустой: есть и выносы, и нули


def test_since_open_and_ret_hand():
    n = 2 * 1440 + 10
    o = np.full(n, 100, dtype=np.int64)
    cf = np.full(n, 110.0)
    out = np.empty(2)
    fam.since_open_kernel(o, cf, np.array([1500, 1439], dtype=np.int64), out)
    assert abs(out[0] - 0.10) < 1e-12 and math.isnan(out[1])        # 1439 → t = 1440 — первая минута суток, закрытых минут суток нет
    o2 = o.copy()
    o2[1440] = 0
    o2[1441] = 50
    fam.since_open_kernel(o2, cf, np.array([1500], dtype=np.int64), out[:1])
    assert abs(out[0] - (110.0 / 50.0 - 1.0)) < 1e-12              # первая минута суток с данными
    o3 = o.copy()
    o3[1440:1501] = 0
    fam.since_open_kernel(o3, cf, np.array([1500], dtype=np.int64), out[:1])
    assert math.isnan(out[0])
    r = np.empty(3)
    cfx = np.array([np.nan, 2.0, 3.0, 6.0])
    fam.ret_at_kernel(cfx, np.array([3, 2, 1], dtype=np.int64), 1, r)
    assert abs(r[0] - 1.0) < 1e-12 and abs(r[1] - 0.5) < 1e-12 and math.isnan(r[2])   # cf[0] = NaN


def test_round_distance_hand():
    d, r = fam.round_distance(np.array([0.0123, 3.6, 1400.0, 0.0, np.nan, 5.0]))
    assert abs(d[0] - 0.0023) < 1e-12 and abs(r[0] - 0.01) < 1e-15
    assert abs(d[1] - 1.4) < 1e-12 and r[1] == 5.0
    assert abs(d[2] - 400.0) < 1e-9 and r[2] == 1000.0
    assert math.isnan(d[3]) and math.isnan(d[4])
    assert d[5] == 0.0 and r[5] == 5.0


def make_cache(tmp, rng, syms, n=11520):
    for k, sym in enumerate(syms):
        a = walk(rng, n, start=1000.0 * (k + 1) if sym != fam.BTC else 100000.0, vol=0.0015, listed=600 if k == 1 else 0)
        gate.write_b42(os.path.join(tmp, sym + ".b42"), a)


def synth_rows(rng, syms, k, n=11520):
    t = rng.integers(5000, n - 5, k)
    return pd.DataFrame(dict(row_id=np.arange(k), sym=[syms[i % len(syms)] for i in range(k)], sig_ms=t.astype(np.int64) * 60_000,
                             level_px_e8=(rng.choice([-1, 1], k) * rng.integers(10**11, 5 * 10**12, k)),
                             approach_dur_min=rng.integers(1, 3000, k).astype(np.float64)))


def test_compute_vs_brute_and_causal():
    rng = np.random.default_rng(3)
    syms = ["AAAUSDT", "BBBUSDT", fam.BTC]
    with tempfile.TemporaryDirectory() as tmp:
        make_cache(tmp, rng, syms)
        rows = synth_rows(rng, syms, 120)
        for side in ("long", "short"):
            s = fam.SIGN[side]
            df = fam.compute(rows, "wa", side, procs=1, cache_dir=tmp)
            assert list(df.columns) == fam.COLUMNS and len(df) == len(rows)
            store = {}
            ab = gate.load_arr(tmp, fam.BTC, store)
            ref = np.array([gate.brute_row(gate.load_arr(tmp, r.sym, store), ab, int(r.sig_ms // 60_000), float(r.approach_dur_min), s, abs(int(r.level_px_e8)) / 1e8)
                            for r in rows.itertuples()])
            bad, dmax = gate.compare(df.to_numpy(), ref)
            assert bad.sum() == 0, (side, dict(zip(fam.COLUMNS, bad)), dmax)
            # причинность: мусор с минуты t не меняет строку
            for i in (3, 40, 77, 100):
                r = rows.iloc[[i]]
                t = int(r["sig_ms"].iat[0] // 60_000)
                base = fam.compute(r, "wa", side, 1, cache_dir=tmp).to_numpy()
                with tempfile.TemporaryDirectory() as g:
                    for sym in {r["sym"].iat[0], fam.BTC}:
                        gate.write_b42(os.path.join(g, sym + ".b42"), ft.garbage_from(gate.load_arr(tmp, sym, store), t, rng))
                    got = fam.compute(r, "wa", side, 1, cache_dir=g).to_numpy()
                assert np.array_equal(base, got, equal_nan=True), (side, i)
            share = np.isfinite(df.to_numpy()).mean(axis=0)
            assert share.min() > 0.0, dict(zip(fam.COLUMNS, share))   # каждый столбец где-то определён (иначе тест ничего не проверяет)


def test_no_btc_cache_and_missing_coin():
    rng = np.random.default_rng(4)
    with tempfile.TemporaryDirectory() as tmp:
        make_cache(tmp, rng, ["AAAUSDT"])
        rows = synth_rows(rng, ["AAAUSDT", "NOPEUSDT"], 10)
        info = {}
        df = fam.compute(rows, "wa", "long", 1, cache_dir=tmp, info=info)
        assert len(df) == 10 and info["missing_ref"] == [fam.BTC] and info["missing_cache"] == ["NOPEUSDT"]
        assert df["mk_rel_sd"].isna().all()                       # нет BTC → столбец пуст
        assert df.loc[rows["sym"] == "NOPEUSDT"].isna().all().all()   # нет кэша монеты → вся строка пуста, строка не выброшена


def test_guards():
    rng = np.random.default_rng(5)
    rows = synth_rows(rng, ["AAAUSDT"], 4)
    rows["abs_ms"] = np.int64(1_800_000_000_000)                  # позже 13.11
    try:
        fam.compute(rows, "wa", "long", 1, cache_dir="/nonexistent")
    except Exception:
        return
    raise AssertionError("охрана abs_ms не сработала")


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print("ok  ", name)
        except Exception:
            fails += 1
            print("FAIL", name)
            traceback.print_exc()
    print("%d из %d" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
