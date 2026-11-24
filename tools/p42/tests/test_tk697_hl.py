"""Тесты TK-697 (№ 8), семейство `h697` (`tk697_hl`): разбор zip дневных свечей, определение на малых числах вручную, ядра против независимого счёта, причинность, молодые монеты.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk697_hl.py` (на srv — `/data/tk592/venv/bin/python`)."""
import math
import os
import sys
import tempfile
import traceback
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk668_feat as ft  # noqa: E402
import tk697_cols as m6  # noqa: E402
import tk697_gate as g6  # noqa: E402
import tk697_hl as fam  # noqa: E402
import tk697_hl_gate as gate  # noqa: E402
from test_tk697_cols import walk  # noqa: E402

DAY = fam.DAY_MS
T0_DAY = 20500             # начало окна — сутки с эпохи (граница суток)
N_MIN = 11 * 1440


def make_daily(rng, syms, first_day, last_day, base=1000.0):
    recs = []
    for k, sym in enumerate(syms):
        c = base * (k + 1) * np.exp(np.cumsum(rng.normal(0, 0.03, last_day - first_day[sym] + 1)))
        for i, day in enumerate(range(first_day[sym], last_day + 1)):
            hi = int(round(c[i] * 1.02 * 1e8))
            lo = int(round(c[i] * 0.97 * 1e8))
            recs.append((sym, day, int(round(c[i] * 1e8)), hi, lo, int(round(c[i] * 1.001 * 1e8))))
    return pd.DataFrame(recs, columns=["sym", "day", "o", "h", "l", "c"])


def make_rows(rng, syms, k, win_days=11):
    t = rng.integers(2000, N_MIN - 5, k)
    abs_ms = (T0_DAY * DAY + t * 60_000).astype(np.int64)
    return pd.DataFrame(dict(row_id=np.arange(k), sym=[syms[i % len(syms)] for i in range(k)], sig_ms=(t * 60_000).astype(np.int64), abs_ms=abs_ms,
                             level_px_e8=(rng.choice([-1, 1], k) * rng.integers(10**11, 5 * 10**12, k))))


def test_parse_zip_with_and_without_header():
    with tempfile.TemporaryDirectory() as d:
        for name, header in (("a.zip", True), ("b.zip", False)):
            p = os.path.join(d, name)
            ms = 20400 * DAY
            lines = (["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"] if header else [])
            lines += ["%d,0.01234,0.01300,0.01100,0.01250,1,%d,1,1,1,1,0" % (ms, ms + DAY - 1), "%d,0.0125,0.0140,0.0120,0.0130,1,%d,1,1,1,1,0" % (ms + DAY, ms + 2 * DAY - 1)]
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("x.csv", "\n".join(lines) + "\n")
            r = fam.parse_zip(p)
            assert r == [(20400, 1234000, 1300000, 1100000, 1250000), (20401, 1250000, 1400000, 1200000, 1300000)], (name, r)


def test_hand_numbers():
    # монета: сутки 20490…20499 с high = 100 + d, low = 50 − d; сигнал в сутках 20500, минута 100 (b = 99); минутки суток 20500 с данными: high 1000, low 10
    o = np.zeros(N_MIN, dtype=np.int64)
    h = np.zeros(N_MIN, dtype=np.int64)
    l = np.zeros(N_MIN, dtype=np.int64)
    c = np.zeros(N_MIN, dtype=np.int64)
    o[:] = 5 * 10**10
    h[:] = 6 * 10**10
    l[:] = 4 * 10**10
    c[:] = 5 * 10**10
    h[50] = 1000 * 10**8
    l[60] = 10 * 10**8
    h[120] = 5000 * 10**8       # позже b — не должна читаться
    a = dict(o=o, h=h, l=l, c=c, qv=np.ones(N_MIN, dtype=np.int64))
    days = np.arange(20490, 20500)
    dh = (100.0 + (days - 20490)) * 1e8
    dl = (50.0 - (days - 20490)) * 1e8
    daily = pd.DataFrame(dict(sym="AAAUSDT", day=days, o=0, h=dh.astype(np.int64), l=dl.astype(np.int64), c=0))
    with tempfile.TemporaryDirectory() as tmp:
        gate_dir = tmp
        g6.write_b42(os.path.join(gate_dir, "AAAUSDT.b42"), a)
        rows = pd.DataFrame(dict(row_id=[0], sym=["AAAUSDT"], sig_ms=[100 * 60_000], abs_ms=[T0_DAY * DAY + 100 * 60_000], level_px_e8=[-500 * 10**8]))
        df = fam.compute(rows, fam.daily_arrays(daily), "wa", "short", cache_dir=gate_dir)
    # окно 30: дни 20470…20499 → есть 20490…20499: max high = 109, min low = 41; минутки суток: high 1000 (минута 50), low 10 (минута 60) → H = 1000, Lw = 10
    L = 500.0
    assert abs(df["hl_hi_p30"].iat[0] - 100.0 * (1000.0 - L) / L) < 1e-9, df.iloc[0].to_dict()
    assert abs(df["hl_lo_p30"].iat[0] - 100.0 * (L - 10.0) / L) < 1e-9
    assert np.allclose(df[[f"hl_hi_p{n}" for n in fam.NS]].to_numpy(), df["hl_hi_p30"].iat[0])      # те же сутки истории во всех окнах: монета «моложе» 30 суток
    assert df["hl_hi_n30"].iat[0] == df["hl_hi_n30"].iat[0]                                          # NATR 60 определён (минутки с данными)
    # ни одних суток истории и ни одной минутки суток сигнала с данными → NaN
    o2 = o.copy()
    o2[:200] = 0
    a2 = dict(o=o2, h=h, l=l, c=c, qv=a["qv"])
    with tempfile.TemporaryDirectory() as tmp:
        g6.write_b42(os.path.join(tmp, "AAAUSDT.b42"), a2)
        df2 = fam.compute(rows, fam.daily_arrays(daily.iloc[:0]), "wa", "short", cache_dir=tmp)
    assert df2.isna().all().all()


def test_compute_vs_brute_and_causal():
    rng = np.random.default_rng(7)
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    first = {"AAAUSDT": T0_DAY - 400, "BBBUSDT": T0_DAY - 100, "CCCUSDT": T0_DAY - 5}       # третья — моложе 30 суток; вторая — моложе 365
    daily = make_daily(rng, syms, first, T0_DAY + 10)
    arrs = fam.daily_arrays(daily)
    with tempfile.TemporaryDirectory() as tmp:
        for k, sym in enumerate(syms):
            a = walk(rng, N_MIN, start=1000.0 * (k + 1), vol=0.0015, listed=300 if k == 2 else 0)
            g6.write_b42(os.path.join(tmp, sym + ".b42"), a)
        rows = make_rows(rng, syms, 150)
        rows["level_px_e8"] = (np.abs(rows["level_px_e8"]) % (3 * 10**11) + 10**11) * np.where(rows["row_id"] % 2 == 0, 1, -1)
        df = fam.compute(rows, arrs, "wa", "long", cache_dir=tmp)
        assert list(df.columns) == fam.COLUMNS and len(df) == len(rows)
        store = {}
        ref = []
        for r in rows.itertuples():
            am = g6.load_arr(tmp, r.sym, store)
            b = int(r.sig_ms // 60_000) - 1
            o_, h_, l_, c_, _ = g6.lists_of(am)
            nb = g6.natr_at(o_, h_, l_, c_, b)
            dd, dh, dl = arrs[r.sym]
            ref.append(gate.brute_row(am, dd.tolist(), dh.tolist(), dl.tolist(), int(r.abs_ms), int(r.sig_ms), abs(int(r.level_px_e8)) / 1e8, nb))
        bad, dmax = g6.compare(df.to_numpy(), np.array(ref))
        assert bad.sum() == 0, (dict(zip(fam.COLUMNS, bad)), dmax)
        assert np.isfinite(df.to_numpy()).mean(axis=0).min() > 0.5
        for i in (4, 50, 120):
            r = rows.iloc[[i]]
            t = int(r["sig_ms"].iat[0] // 60_000)
            D = int(r["abs_ms"].iat[0] // DAY)
            base = fam.compute(r, arrs, "wa", "long", cache_dir=tmp).to_numpy()
            with tempfile.TemporaryDirectory() as g:
                sym = r["sym"].iat[0]
                g6.write_b42(os.path.join(g, sym + ".b42"), ft.garbage_from(g6.load_arr(tmp, sym, store), t, rng))
                gd = fam.daily_arrays(gate.garbage_daily(daily[daily["sym"] == sym], D, rng))
                got = fam.compute(r, gd, "wa", "long", cache_dir=g).to_numpy()
            assert np.array_equal(base, got, equal_nan=True), i


def test_guards():
    rng = np.random.default_rng(8)
    rows = make_rows(rng, ["AAAUSDT"], 3)
    rows["abs_ms"] = np.int64(1_800_000_000_000)
    rows["sig_ms"] = rows["abs_ms"] - T0_DAY * DAY
    try:
        fam.compute(rows, {}, "wa", "long", cache_dir="/nonexistent")
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
