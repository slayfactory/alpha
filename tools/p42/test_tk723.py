"""Тесты `tk723_kill.py` и чтения таблицы остановки до n = 365 в `tk720_fwd.py` (PYTHONUTF8=1 python -m pytest test_tk723.py -q -p no:cacheprovider)."""
import json
import os
import sqlite3
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk720_fwd as F  # noqa: E402
import tk722_checks as C  # noqa: E402
import tk723_kill as K  # noqa: E402


def _kill365(level=99.5, dd=10.0, gap=30.0):
    return {"version": F.KILL_VERSION, "level": level, "q%g" % level: {"dd": [dd] * 365, "gap": [gap] * 365},
            "expect": {"label": "FJ", "per_day": F.E_MU1, "window": "w", "sharpe": 2.0}, "source": "src, x", "cone": {k: [0.0] * 365 for k in ("q05", "q50", "q95")}}


def test_kill_flags_reads_all_365_days():
    kill = _kill365()
    x = np.full(200, 0.1)
    ok = F.kill_flags(x, kill)
    assert ok["n"] == 200 and ok["stop"] is False and ok["dd_q"] == 10.0
    x[150:] = -0.2                                                   # 50 сут. вниз: DD 10,0 не больше 10, перехай 50 > 30
    st = F.kill_flags(x, kill)
    assert st["stop_gap"] is True and st["stop_dd"] is False and st["stop"] is True
    assert F.kill_flags(np.zeros(366), kill)["stop"] is None
    fz60 = {"level": 99, "q99": {"dd": [10.0] * 60, "gap": [3.0] * 60}}  # заморозка TK-720: n > 60 — по-прежнему без таблицы
    assert F.kill_flags(np.zeros(61), fz60)["stop"] is None


def test_first_fire_and_accept():
    dd = np.array([[0.0, 1.0, 5.0], [0.0, 0.0, 0.0]])
    gap = np.array([[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]])
    assert list(K.first_fire(dd, gap, [1.0, 1.0, 1.0], [5.0, 5.0, 5.0])) == [3, K.NEVER]   # строго больше: 1,0 не останавливает
    assert list(K.first_fire(dd, gap, [9.0] * 3, [1.0, 1.0, 2.0])) == [2, 2]
    p = np.array([[10.0, 10.0, 10.0], [-1.0, -1.0, -1.0]])
    acc = K.first_accept(p, 1.0, 1.0, e_accept=20.0)                    # ln E = n (10 − 0,5) ≥ ln 20 с первых суток
    assert list(acc) == [1, K.NEVER]


def test_kill_tables_fj_ladder_and_race():
    rng = np.random.default_rng(3)
    x = rng.normal(0.5, 4.8, 150)
    idx = C._boot_long(150, 365, np.random.default_rng([1, 2]), boot=600)
    kt = K.kill_tables_fj(x[idx], 0.5, 4.8)
    for q in ("q95", "q99", "q99.5"):
        t = kt[q]
        assert len(t["dd"]) == 365 and len(t["gap"]) == 365
        assert all(np.diff(t["dd"]) >= -1e-9) and all(np.diff(t["gap"]) >= 0)
        assert abs(t["stop_before_works"] + t["works_before_stop"] + t["same_day"] + t["neither"] - 1) < 1e-12
        assert t["false_stop_at"]["60"] <= t["false_stop_at"]["365"] == t["false_stop"]
    assert kt["q95"]["false_stop"] >= kt["q99"]["false_stop"] >= kt["q99.5"]["false_stop"]
    first_ok = next((q for q in K.LEVELS if kt["q%g" % q]["false_stop"] <= 0.10), None)
    assert kt["level"] == (first_ok if first_ok is not None else 99.5) and kt["level_ok"] == (first_ok is not None)


def test_load_kill_binds_freeze(tmp_path):
    p = tmp_path / "k.json"
    k = _kill365()
    k["freeze_sha256"] = "abc"
    p.write_text(json.dumps(k), encoding="utf-8")
    assert F.load_kill(str(p), "abc")["level"] == 99.5
    with pytest.raises(SystemExit):
        F.load_kill(str(p), "other")


def test_report_uses_kill365_after_day_60(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.executescript(F.TABLES)
    conn.execute("INSERT INTO tk720_run VALUES('r','2027-01-31','s','c0de','m','','',0,'ok')")
    for i in range(70):
        d = F.F0 + i
        conn.execute("INSERT INTO tk720_day VALUES('r',?,?,?,?,0)", (d, F.day_date(d), 3, 0.1 if i % 3 else -0.05))
    fz = {"expect": {F.EXPECT_PER_DAY_KEY: 0.889}}
    assert F.report(conn, "r", fz, "s", _kill365(), out_path=str(tmp_path / "r.md")) == 0
    txt = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "нет таблицы" not in txt and "ожид. Σ (FJ)" in txt and "q99.5" in txt
    assert "+39.28 |" in txt                                          # ожидание 70 × 0,5611 (FJ), не × 0,889 (SEL)
    assert "E-процесс" in txt and "«работает» не достигнуто" in txt


def test_e_process_matches_log_e_and_accept():
    x = np.array([1.0, -2.0, 3.5, 0.4])
    assert np.allclose(np.log(F.e_process(x)), C.log_e(x, F.E_MU1, F.E_SD))
    assert F.E_ACCEPT == C.E_ACCEPT and abs(F.E_MU1 - 0.5611) < 1e-4 and abs(F.E_SD - 4.8150) < 1e-4
    big = np.full(40, 30.0)                                           # ln E растёт ≈ 0,72 в сутки → E ≥ 20 к 5-м суткам
    e = F.e_process(big)
    assert e[3] < 20 <= e[4]


def test_report_prints_works_when_e_crosses(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.executescript(F.TABLES)
    conn.execute("INSERT INTO tk720_run VALUES('r','2026-12-04','s','c0de','m','','',0,'ok')")
    for i in range(10):
        d = F.F0 + i
        conn.execute("INSERT INTO tk720_day VALUES('r',?,?,?,?,0)", (d, F.day_date(d), 3, 30.0))
    k = _kill365(dd=1000.0, gap=1000.0)
    assert F.report(conn, "r", {"expect": {F.EXPECT_PER_DAY_KEY: 0.889}}, "s", k, out_path=str(tmp_path / "r.md")) == 0
    txt = (tmp_path / "r.md").read_text(encoding="utf-8")
    rows = [l for l in txt.splitlines() if l.startswith("| 2026-")]
    assert "РАБОТАЕТ" not in rows[3] and rows[4].endswith(" РАБОТАЕТ |")
    assert "— «работает» (E ≥ 20)" in txt
