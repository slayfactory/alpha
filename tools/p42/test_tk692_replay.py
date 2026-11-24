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
