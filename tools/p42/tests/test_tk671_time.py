"""Тесты §13.71 (TK-671), семейство `time`: признаки времени сигнала на руками посчитанных отметках UTC. Синтетика, данных нет.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk671_time.py` — выполняет все test_* и печатает итог.

Отметки (эпоха мс считана от 2026-09-01 00:00:00 UTC = 1 788 220 800 000, вторник):
  A  вт 01.09.2026 00:00:00.000   = 1 788 220 800 000
  B  сб 05.09.2026 07:57:30.500   = 1 788 220 800 000 + 4·86 400 000 + 28 650 500 = 1 788 595 050 500
  C  вс 06.09.2026 23:59:59.999   = 1 788 220 800 000 + 5·86 400 000 + 86 399 999 = 1 788 739 199 999
  D  чт 08.10.2026 12:30:00.000   = 1 791 417 600 000 + 45 000 000 = 1 791 462 600 000
"""
import os
import sys
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk671_time as tt  # noqa: E402

A = 1_788_220_800_000
B = A + 4 * 86_400_000 + 28_650_500
C = A + 5 * 86_400_000 + 86_399_999
D = 1_791_417_600_000 + 45_000_000


def col(scheme_prefix, label):
    hit = [k for k, v in tt.KZ_LABELS.items() if v.startswith(scheme_prefix) and v.endswith("| " + label)]
    assert len(hit) == 1, (scheme_prefix, label, hit)
    return hit[0]


def lit(ts):
    f = tt.time_features(np.array([ts], dtype=np.int64))
    return {k: float(v[0]) for k, v in f.items()}


def on(f):
    return sorted(k for k in tt.KZ_COLS if f[k] == 1.0)


def test_columns_and_labels():
    assert len(tt.COLS) == 4 + 3 + 65 and len(set(tt.COLS)) == len(tt.COLS)
    assert tt.KZ_COLS[0] == "kz00" and tt.KZ_COLS[-1] == "kz64"
    assert len(tt.KZ_LABELS) == 65


def test_A_tuesday_midnight_funding_instant():
    f = lit(A)
    assert (f["t_hour"], f["t_min"], f["t_wday"]) == (0, 0, 1)
    assert f["t_to_fund_min"] == 480.0                                          # ровно на фандинге: следующий через 8 ч
    assert (f["t_sess_asia"], f["t_sess_eu"], f["t_sess_us"]) == (1, 0, 0)
    want = sorted([col("час (мин", "0…+5"), col("полчаса, :00", "0…+5"), col("фандинг", "0…+10"), col("смена суток", "0…+10"),
                   col("открытие сессии", "Азия 00:00–01:00"), col("день недели", "вт"), col("час суток", "00")])
    assert on(f) == want, (on(f), want)


def test_B_saturday_before_funding_europe_open():
    f = lit(B)
    assert (f["t_hour"], f["t_min"], f["t_wday"]) == (7, 57, 5)
    assert abs(f["t_to_fund_min"] - (2 + 29.5 / 60)) < 1e-12                    # 07:57:30,5 → 08:00:00 = 2 мин 29,5 с
    assert (f["t_sess_asia"], f["t_sess_eu"], f["t_sess_us"]) == (1, 1, 0)      # Азия [0;8) и Европа [7;16) перекрываются
    want = sorted([col("час (мин", "−5…0"), col("полчаса, :00", "−5…0"), col("фандинг", "−10…0"), col("открытие сессии", "Европа 07:00–08:00"),
                   col("день недели", "сб"), col("день недели", "выходные (сб+вс)"), col("час суток", "07")])
    assert on(f) == want, (on(f), want)


def test_C_sunday_last_millisecond():
    f = lit(C)
    assert (f["t_hour"], f["t_min"], f["t_wday"]) == (23, 59, 6)
    assert abs(f["t_to_fund_min"] - 0.001 / 60.0) < 1e-12                        # до 00:00:00 осталась 1 мс
    assert (f["t_sess_asia"], f["t_sess_eu"], f["t_sess_us"]) == (0, 0, 0)
    want = sorted([col("час (мин", "−5…0"), col("полчаса, :00", "−5…0"), col("фандинг", "−10…0"), col("смена суток", "−10…0"),
                   col("день недели", "вс"), col("день недели", "выходные (сб+вс)"), col("час суток", "23")])
    assert on(f) == want, (on(f), want)


def test_D_thursday_midday_half_hour():
    f = lit(D)
    assert (f["t_hour"], f["t_min"], f["t_wday"]) == (12, 30, 3)
    assert f["t_to_fund_min"] == 210.0                                           # 12:30 → 16:00
    assert (f["t_sess_asia"], f["t_sess_eu"], f["t_sess_us"]) == (0, 1, 0)       # США с 13:00
    want = sorted([col("час (мин", "+20…+40"), col("полчаса :30", "0…+5"), col("полчаса, :00", "0…+5"), col("день недели", "чт"), col("час суток", "12")])
    assert on(f) == want, (on(f), want)


def test_session_boundaries_by_hour():
    base = A                                                                     # 00:00
    ts = np.array([base + h * 3_600_000 for h in (6, 7, 8, 12, 13, 15, 16, 20, 21)] + [base + 7 * 3_600_000 - 1], dtype=np.int64)
    f = tt.time_features(ts)
    assert f["t_sess_asia"].tolist() == [1, 1, 0, 0, 0, 0, 0, 0, 0, 1]           # 08:00 уже не Азия; 06:59:59.999 ещё Азия
    assert f["t_sess_eu"].tolist() == [0, 1, 1, 1, 1, 1, 0, 0, 0, 0]             # [7;16)
    assert f["t_sess_us"].tolist() == [0, 0, 0, 0, 1, 1, 1, 1, 0, 0]             # [13;21)


def test_funding_minutes_across_the_eight_hour_grid():
    ts = np.array([A + k * 60_000 for k in (0, 1, 479, 480, 481, 959, 960)], dtype=np.int64)
    f = tt.time_features(ts)["t_to_fund_min"]
    assert f.tolist() == [480.0, 479.0, 1.0, 480.0, 479.0, 1.0, 480.0]


def test_flags_are_binary_and_vector_equals_scalar():
    rng = np.random.default_rng(7)
    ts = (A + rng.integers(0, 70 * 86_400_000, size=2000)).astype(np.int64)
    f = tt.time_features(ts)
    for k in tt.KZ_COLS + ["t_sess_asia", "t_sess_eu", "t_sess_us"]:
        assert set(np.unique(f[k])) <= {0.0, 1.0}, k
    for i in range(0, 2000, 97):
        s = lit(int(ts[i]))
        for k in tt.COLS:
            assert s[k] == f[k][i], (i, k)
    assert (f["t_to_fund_min"] > 0).all() and (f["t_to_fund_min"] <= 480).all()
    assert (f["t_hour"] == np.floor(f["t_hour"])).all() and f["t_min"].max() <= 59 and f["t_wday"].max() <= 6


def test_calendar_cross_check_with_datetime():
    import datetime as dt
    rng = np.random.default_rng(11)
    ts = (A + rng.integers(-60 * 86_400_000, 70 * 86_400_000, size=500)).astype(np.int64)
    f = tt.time_features(ts)
    for i, t in enumerate(ts):
        d = dt.datetime.fromtimestamp(int(t) / 1000.0, dt.timezone.utc)
        assert (f["t_hour"][i], f["t_min"][i], f["t_wday"][i]) == (d.hour, d.minute, d.weekday()), (i, d)


def test_day_of_week_buckets_partition():
    rng = np.random.default_rng(3)
    ts = (A + rng.integers(0, 70 * 86_400_000, size=3000)).astype(np.int64)
    f = tt.time_features(ts)
    names = [col("день недели", x) for x in ("пн", "вт", "ср", "чт", "пт", "сб", "вс")]
    assert (sum(f[k] for k in names) == 1).all()                                  # ровно один день недели
    hrs = [col("час суток", "%02d" % h) for h in range(24)]
    assert (sum(f[k] for k in hrs) == 1).all() and (np.stack([f[k] for k in hrs]).argmax(0) == f["t_hour"]).all()


def test_guard_forward_window():
    last_ok = int(__import__("datetime").datetime(2026, 11, 13, 23, 59, 59, 999000, tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)
    tt.time_features(np.array([last_ok], dtype=np.int64))                          # 13.11 23:59:59.999 — можно
    try:
        tt.time_features(np.array([last_ok + 1], dtype=np.int64))                  # 14.11 00:00:00.000 — нельзя
    except SystemExit:
        return
    raise AssertionError("охрана 14.11 не сработала")


def test_compute_frame_index_columns_no_row_dropped():
    import pandas as pd
    rows = pd.DataFrame({"row_id": np.array([5, 3, 9], dtype=np.int64), "abs_ms": [A, B, C], "side": "long", "win": "wc"})
    out = tt.compute(rows, "wc", "long")
    assert list(out.columns) == tt.COLS and out.index.tolist() == [5, 3, 9] and out.index.name == "row_id"
    assert out.notna().all().all() and (out.dtypes == np.float64).all()
    assert out.loc[3, "t_hour"] == 7 and out.loc[9, "t_wday"] == 6
    try:
        tt.compute(rows, "wa", "long")
    except ValueError:
        return
    raise AssertionError("несовпадение окна не поймано")


if __name__ == "__main__":
    fails = 0
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for k, v in tests:
        try:
            v()
            print("ok  ", k)
        except Exception:
            fails += 1
            print("FAIL", k)
            traceback.print_exc()
    print("%d тестов, провалов %d" % (len(tests), fails))
    sys.exit(1 if fails else 0)
