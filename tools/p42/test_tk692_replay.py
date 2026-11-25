"""TK-692 повтор движком: пары (клетка, месяц), стоп клетки, R:R как в tk684_metrics, утечка, ряд по суткам торговли."""
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk684_metrics as m684  # noqa: E402
import tk692_replay as rp  # noqa: E402


def test_needed_pairs_months_up_to_last_traded_day():
    # блок недели 25–32 (обрезан до 31) торгуется в январе; блок 59–66 — в феврале и июне нет
    picks = [(7, 25, 31, 0), (8, 55, 62, 0), (9, 31, 31, 0), (None, 40, 47, 0), (10, 12, 18, 1)]
    need = rp.needed_pairs(picks, [], 2)
    assert need == {(7, 0), (8, 0), (8, 1)}                           # 9 — отложенный выбор (e == b), 10 — пропуск месяца, None — нет выбора
    assert rp.needed_pairs([], [3], 3) == {(3, 0), (3, 1), (3, 2)}    # клетка базы — все загруженные месяцы


def test_needed_pairs_clipped_by_loaded_months():
    assert rp.needed_pairs([(1, 28, 40, 0)], [], 1) == {(1, 0)}       # блок вышел за загруженные месяцы


def test_stop_pct():
    assert rp.stop_pct("ew=5 fm=50 rs=20 s=3 t24=12") == 3.0         # rs=20 — не стоп
    assert rp.stop_pct("ew=5", "x=1 s=1.5 y=2") == 1.5
    with pytest.raises(ValueError):
        rp.stop_pct("ew=5 rs=20")


def test_rr_matches_tk684():
    v = [3.0, -1.0, 2.0, -4.0, 1.0]
    assert rp.rr_of(v) == pytest.approx(m684.rr(v))
    assert rp.rr_of([1.0, 2.0]) is None and rp.rr_of([-1.0]) is None


def test_leak_usd_counts_only_window_trades_closed_after_boundary():
    day = np.array([2, 8, 9, 10, 11])
    exit_ms = np.array([5, 9, 12, 12, 12]) * rp.DAY_MS + rp.D0_MS
    val = np.array([1.0, 2.0, 4.0, 8.0, 16.0])
    # граница суток 10, окно отбора — сутки < 10: сделки 8 (закрыта в сутки 9) не в утечке, 9 (закрыта 12) — да; сутки 10, 11 вне окна
    assert rp.leak_usd(day, exit_ms, val, 10, rp.D0_MS + 10 * rp.DAY_MS) == 4.0
    assert rp.leak_usd(day, exit_ms, val, 3, rp.D0_MS + 10 * rp.DAY_MS) == 0.0


def test_series_stats_rr_selects_trade_days_and_stop():
    tr = {1: dict(day=np.array([0, 1, 2, 3]), val=np.array([2.0, -1.0, 4.0, -2.0]), fees=np.full(4, 0.5)),
          2: dict(day=np.array([5, 6]), val=np.array([-3.0, 1.0]), fees=np.full(2, 1.0))}
    r, sl, n = rp.series_stats_rr(tr, {1: 3.0, 2: 4.0}, {1: {0, 1, 2}, 2: {5}})
    assert n == 4
    assert r == pytest.approx(m684.rr([2.0, -1.0, 4.0, -3.0]))
    assert sl == pytest.approx(rp.STAKE * np.mean([3.0, 3.0, 3.0, 4.0]) / 100.0 + np.mean([0.5, 0.5, 0.5, 1.0]))
    assert rp.series_stats_rr(tr, {1: 3.0}, {9: {0}}) == (None, None, 0)


def test_base_cid_set_splits_set_and_extra():
    import sqlite3
    out = sqlite3.connect(":memory:")
    out.execute("CREATE TABLE tk692_sum(rid, side, series, extra)")
    out.executemany("INSERT INTO tk692_sum VALUES('r','short',?,?)", [("anchor", '{"cid": 9000001}'), ("frozen-L384", '{"cid": 7}'), ("x", '{"waiting": 1}'), ("select", '{"cid": 5}')])
    assert rp.base_cid_set(out, "r", "short") == {7}
    assert rp.base_cid_set(out, "r", "short", extra=True) == {9000001}


def test_extra_base_requires_gate_and_all_months(tmp_path):
    import sqlite3
    import tk692_walk as w
    path = str(tmp_path / "rp.sqlite")
    con = rp.rp_connect(path)
    cid = w.BASE_CID0 + 1
    nd = 59                                                            # январь + февраль
    assert w.extra_base(path, "short", "anchor", nd) is None           # нет ворот
    con.execute("INSERT INTO rp_gate VALUES('g','short',?,-1,172,172,133.93,133.94,0.01,1,'')", (cid,))
    con.executemany("INSERT INTO rp_done VALUES('short','base-anchor',?,?,'r')", [(cid, 0)])
    con.executemany("INSERT INTO rp_trade VALUES('short','base-anchor',?,0,'X',?,0,0,?,0,0,'be')", [(cid, 3, 2.0), (cid, 3, -0.5), (cid, 40, 9.0)])
    con.commit()
    assert w.extra_base(path, "short", "anchor", nd) is None           # февраль не покрыт
    con.execute("INSERT INTO rp_done VALUES('short','base-anchor',?,1,'r')", (cid,))
    con.commit()
    x, n, c = w.extra_base(path, "short", "anchor", nd)
    assert c == cid and x[3] == 1.5 and n[3] == 2 and x[40] == 9.0 and n[40] == 1 and x.sum() == 10.5
    con.execute("UPDATE rp_gate SET ok=0")
    con.commit()
    assert w.extra_base(path, "short", "anchor", nd) is None           # ворота не пройдены
    sqlite3.connect(path).close()
