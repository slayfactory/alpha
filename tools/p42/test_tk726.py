"""TK-726: тесты шага 1 (tk726_seam, tk726_run) на синтетике; запуск: python -m pytest tools/p42/test_tk726.py"""
import csv
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk726_run as rn  # noqa: E402
import tk726_seam as sm  # noqa: E402


def mk(rows, t0_ms=0):
    """rows: (sym, sig_ms, entry_ms, exit_ms, kind, gross, fees, net) — абсолютное время (мс эпохи)."""
    n = len(rows)
    a = {"sym": np.array([r[0] for r in rows], dtype=object), "kind": np.array([r[4] for r in rows], dtype=object)}
    a["sig_ms"], a["entry_ms"], a["exit_ms"] = (np.array([r[i] for r in rows], dtype=np.int64) for i in (1, 2, 3))
    a["gross"], a["fees"], a["net"] = (np.array([r[i] for r in rows], dtype=np.float64) for i in (5, 6, 7))
    a["funding"] = np.zeros(n)
    a["pnl"] = a["gross"] - a["fees"]
    a["eday"] = (a["entry_ms"] - sm.EPOCH0) // sm.DAY_MS
    a["xday"] = (a["exit_ms"] - sm.EPOCH0) // sm.DAY_MS
    a["entry_px_e8"] = np.ones(n, dtype=np.int64)
    return a


def day_ms(iso, hour=12):
    return sm.EPOCH0 + sm.day_of(iso) * sm.DAY_MS + hour * 3_600_000


def test_block_len_protocol():
    assert (sm.block_len(150), sm.block_len(136), sm.block_len(286)) == (6, 6, 7)
    assert sm.block_len(31) == 4 and sm.block_len(1) == 1


def test_classify():
    assert sm.classify(-0.1, 0.1) == "края нет"
    assert sm.classify(0.05, 0.15) == "края нет"            # не накрывает 0, но внутри ±0,2 — эквивалентность
    assert sm.classify(0.1, 0.5) == "край есть"
    assert sm.classify(-0.5, 0.5) == "не ясно"
    assert sm.classify(float("nan"), 1.0) == "не ясно"
    assert sm.combine("края нет", "края нет") == "края нет" and sm.combine("края нет", "не ясно") == "не ясно"


def test_circ_sums_full_block_is_total():
    rng = np.random.default_rng(1)
    p, k = np.arange(1.0, 8.0), np.arange(1.0, 8.0) * 2
    a, b = sm.circ_sums(p, k, 7, 50, rng)
    assert np.allclose(a, p.sum()) and np.allclose(b, k.sum())
    a, b = sm.circ_sums(p, k, 100, 50, rng)                    # блок длиннее ряда — обрезается до ряда
    assert np.allclose(a, p.sum())


def test_edge_test_no_effect_and_effect():
    rng = np.random.default_rng(5)
    wins = [(rng.normal(0, 3, 30), rng.integers(1, 4, 30)) for _ in range(10)]
    r = sm.edge_test(wins, 3, nboot=3000)
    assert r["lo"] < r["point"] < r["hi"] and r["lo"] < 0 < r["hi"]
    assert r["e_days"] == 20 and r["r_days"] == 280
    shifted = [(p + np.r_[np.full(2, 40.0), np.zeros(len(p) - 2)], k) for p, k in wins]
    r2 = sm.edge_test(shifted, 3, nboot=3000)
    assert r2["lo"] > 0 and sm.classify(r2["lo"], r2["hi"]) == "край есть"


def test_split_test_equal_groups():
    p = np.zeros(10)
    k = np.ones(10, np.int64)
    r = sm.split_test((p, k), (np.zeros(21), np.ones(21, np.int64)), 3, nboot=500)
    assert r["point"] == 0 and r["lo"] == 0 and r["hi"] == 0 and sm.classify(r["lo"], r["hi"]) == "края нет"


def test_split_labeled_not_degenerate():
    rng = np.random.default_rng(3)
    p, k = rng.normal(0, 3, 31), rng.integers(1, 4, 31)
    for blk in (4, 21):
        r = sm.split_test_labeled(p, k, 10, blk, nboot=2000)
        assert r["hi"] > r["lo"] and r["e_days"] == 10 and r["r_days"] == 21 and r["lo"] < r["point"] < r["hi"]
    z = sm.split_test_labeled(np.zeros(31), np.ones(31), 10, 21, nboot=500)
    assert z["point"] == 0 and z["lo"] == 0 and z["hi"] == 0


def test_edge_by_window():
    e = sm.edge_by_window([(np.array([2.0, 2.0, 1.0, 1.0]), np.array([2, 2, 1, 3]))])
    assert e[0]["ne"] == 4 and abs(e[0]["ue"] - 1.0) < 1e-12 and e[0]["nr"] == 4 and abs(e[0]["ur"] - 0.5) < 1e-12


def test_twin_compare_identical_and_diff():
    d1, d2, d3 = day_ms("2026-11-01"), day_ms("2026-11-05"), day_ms("2026-11-10")
    wc = mk([("AUSDT", d1 - 100, d1, d1 + 1000, "stop", 1.0, 0.1, 0.9), ("BUSDT", d2 - 100, d2, d2 + 1000, "rt", 2.0, 0.1, 1.9), ("CUSDT", d3 - 100, d3, d3 + 1000, "trail", -1.0, 0.1, -1.1)])
    tw = mk([("AUSDT", d1 - 100, d1, d1 + 1000, "stop", 1.0, 0.1, 0.9), ("BUSDT", d2 - 100, d2, d2 + 1000, "rt", 2.0, 0.1, 1.9), ("CUSDT", d3 - 100, d3, d3 + 1000, "trail", -1.0, 0.1, -1.1)])
    r = sm.twin_compare(wc, tw)
    assert r["ok"] and r["rest"]["share"] == 0 and r["rest"]["n_wc"] == 2 and r["first"]["n_wc"] == 1
    tw2 = mk([("AUSDT", d1 - 100, d1, d1 + 1000, "stop", 0.0, 0.1, -0.1), ("BUSDT", d2 - 100, d2, d2 + 1000, "rt", 2.0, 0.1, 1.9)])   # у C нет сделки; у A другой исход (первые сутки)
    r = sm.twin_compare(wc, tw2)
    assert not r["ok"] and r["rest"]["only_wc"] == 1 and r["rest"]["only_tw"] == 0 and abs(r["rest"]["dsum"] + (-1.0 - 0.1)) < 1e-12
    assert r["first"]["only_wc"] == 1 and r["first"]["only_tw"] == 1                       # первые сутки в порог не входят
    twb = mk([("BUSDT", d2 - 100, d2, d2 + 1000, "rt", 2.0, 0.1, 1.9)])                    # окно B: только монеты состава
    r = sm.twin_compare(wc, twb, coins={"BUSDT"})
    assert r["rest"]["n_wc"] == 1 and r["ok"]


def test_contribution_per_day():
    d = day_ms("2026-10-05")
    a = mk([("XUSDT", d - 1, d, d + 10, "stop", 5.0, 1.0, 4.0), ("YUSDT", d - 1, d, d + 10, "stop", 7.0, 1.0, 6.0), ("XUSDT", d - 1, d + sm.DAY_MS * 60, d + 10 + sm.DAY_MS * 60, "stop", 9.0, 1.0, 8.0)])
    c = sm.contribution(a, {"XUSDT"}, sm.day_of("2026-10-03"), sm.day_of("2026-11-13"))
    assert c["n"] == 1 and abs(c["usd"] - 4.0) < 1e-12 and c["days"] == 42 and abs(c["per_day"] - 4.0 / 42) < 1e-12


def test_read_trades_abs_time_and_pnl(tmp_path):
    p = tmp_path / "1-trades.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sym", "level", "sig_min", "entry_min", "exit_min", "entry_px_e8", "kind", "takes", "gross", "fees", "funding", "net", "entry_day", "sig_ms", "entry_ms", "exit_ms"])
        w.writerow(["AUSDT", 1, 0, 0, 1, 100, "stop", 0, "3.5", "0.5", "0.1", "3.1", 0, 1000, 1004, 90_000_000])
    t0 = sm.EPOCH0 + 10 * sm.DAY_MS
    a = sm.read_trades(str(p), t0)
    assert a["sig_ms"][0] == t0 + 1000 and a["entry_ms"][0] == t0 + 1004 and a["eday"][0] == 10 and a["xday"][0] == 11
    assert abs(a["pnl"][0] - 3.0) < 1e-12 and abs(a["net"][0] - 3.1) < 1e-12 and a["kind"][0] == "stop"


def test_day_arrays_and_forced():
    d = day_ms("2026-11-13")
    a = mk([("A", d - 1, d, d + 5, "forced", 1.0, 0.5, 0.5), ("B", d - 1, d + 10, d + 20, "stop", -2.0, 0.5, -2.5)])
    p, k = sm.day_arrays(a, sm.day_of("2026-11-12"), sm.day_of("2026-11-13"))
    assert list(k) == [0, 2] and abs(p[1] - (0.5 - 2.5)) < 1e-12
    assert sm.forced_last_day(a, sm.day_of("2026-11-13")) == (1, 0.5, 1)


def test_twin_geometry_and_cells():
    assert rn.twin_geometry_ok("twA") and rn.twin_geometry_ok("twB")
    assert rn.day_index("2026-11-01") == 304 and rn.SEL_DAY0 + rn.SEL_DAYS == rn.N1_DAY0 - 0 and rn.N1_DAY0 + rn.N1_DAYS == 328
    assert rn.TWINS["twA"]["t0"] == "2026-10-12" and rn.TWINS["twB"]["t0"] == "2026-09-01"


def test_dense_series():
    p, k = rn.dense([(181, 1.5, 2), (183, -1.0, 1), (400, 9.0, 9)], 181, 136)
    assert p[0] == 1.5 and k[2] == 1 and len(p) == 136 and p.sum() == 0.5
