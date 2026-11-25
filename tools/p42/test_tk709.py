#!/usr/bin/env python3
"""TK-709: юнит-тесты прохода по ленте и событий (синтетика, без файлов). Запуск: python3 test_tk709.py (srv: нужны numpy, pyarrow, numba)."""
import sys

import numpy as np

import tk709_events as ev
import tk709_tape as tp

I64 = np.int64


def tape(rows):
    """rows: (t, price, qty, buy)"""
    a = list(zip(*rows))
    return np.array(a[0], I64), np.array(a[1], float), np.array(a[2], float), np.array(a[3], bool)


def close(a, b, e=1e-9):
    return abs(a - b) <= e * max(1.0, abs(b))


def test_entry_first_trade_enough():
    t, p, q, b = tape([(1000, 10.0, 100.0, True)])               # $1000 одной сделкой
    e0, p1, vw, d = tp.entry_walk(t, p, q, b, 1000)
    assert close(e0, 10.0) and close(p1, 10.0) and close(vw, 10.0) and d == 0


def test_entry_depth_walk():
    # 3 сделки по $200 (20 × 10, 20 × 11, 20 × 12) → набираем $500: 200 + 220 + 80 из третьей; кол-во 20 + 20 + 80/12
    t, p, q, b = tape([(1000, 10.0, 20.0, True), (1010, 11.0, 20.0, True), (1020, 12.0, 20.0, True)])
    e0, p1, vw, d = tp.entry_walk(t, p, q, b, 1000)
    qty = 20 + 20 + 80 / 12.0
    assert close(vw, 500.0 / qty)                                   # VWAP набранного
    assert close(p1, 10.0) and close(e0, max(10.0, vw)) and d == 20


def test_entry_floor_by_first_trade():
    # падающий нож: первая сделка 10, следующие дешевле → VWAP < первой, E0 = первая (пол)
    t, p, q, b = tape([(1000, 10.0, 25.0, True), (1005, 9.0, 25.0, True), (1006, 8.0, 25.0, True)])
    e0, p1, vw, _ = tp.entry_walk(t, p, q, b, 1000)
    assert vw < 10.0 and close(e0, 10.0) and close(p1, 10.0)


def test_entry_ignores_sellers_and_early():
    t, p, q, b = tape([(900, 5.0, 1000.0, True), (1000, 5.5, 1000.0, False), (1100, 10.0, 60.0, True)])
    e0, p1, vw, d = tp.entry_walk(t, p, q, b, 1000)
    assert close(p1, 10.0) and close(e0, 10.0) and d == 100         # сделка до T0 и сделки продавца не считаются


def test_entry_thin_and_window():
    t, p, q, b = tape([(1000, 10.0, 10.0, True), (1000 + 60_001, 10.0, 1000.0, True)])   # $100 в окне, остальное — за окном
    assert tp.entry_walk(t, p, q, b, 1000) is None
    t, p, q, b = tape([(1000, 10.0, 100.0, False)])                # покупателей нет
    assert tp.entry_walk(t, p, q, b, 1000) is None
    t, p, q, b = tape([(1000 + 60_000, 10.0, 50.0, True)])         # ровно на краю окна — внутри
    assert tp.entry_walk(t, p, q, b, 1000) is not None


def test_exit_ok_and_floor():
    t, p, q, b = tape([(5000, 10.0, 30.0, False), (5001, 9.0, 30.0, False), (5002, 8.0, 40.0, False)])
    e1, p1, vw, st = tp.exit_walk(t, p, q, b, 5000, 60.0, 7.0)    # 30 по 10, 30 по 9: VWAP 9,5
    assert st == tp.ST_OK and close(p1, 10.0) and close(vw, 9.5) and close(e1, 9.5)
    t, p, q, b = tape([(5000, 10.0, 30.0, False), (5001, 11.0, 30.0, False)])   # растущая цена: E1 = min(первая, VWAP) = первая
    e1, p1, vw, st = tp.exit_walk(t, p, q, b, 5000, 60.0, 7.0)
    assert close(e1, 10.0) and vw > 10.0


def test_exit_extended_partial_last():
    t, p, q, b = tape([(5000, 10.0, 30.0, False), (5000 + 120_000, 9.0, 30.0, False)])
    e1, p1, vw, st = tp.exit_walk(t, p, q, b, 5000, 60.0, 7.0)    # вторая сделка после 60 с, до 600 с: расширение
    assert st == tp.ST_EXT and close(vw, 9.5)
    t, p, q, b = tape([(5000, 10.0, 30.0, False), (5001, 9.0, 10.0, False)])      # набрано 40 из 60 → неполный VWAP
    e1, p1, vw, st = tp.exit_walk(t, p, q, b, 5000, 60.0, 7.0)
    assert st == tp.ST_PART and close(vw, (300.0 + 90.0) / 40.0)


def test_exit_last_fallback():
    t, p, q, b = tape([(5000, 10.0, 30.0, True)])                  # только покупатели
    e1, p1, vw, st = tp.exit_walk(t, p, q, b, 5000, 60.0, 7.5)
    assert st == tp.ST_LAST and close(e1, 7.5)
    assert tp.exit_walk(t, p, q, b, 5000, 60.0, float("nan")) is None


def test_events_episodes():
    m = 60_000
    t = np.array([0, m, 2 * m, 4 * m, 5 * m, 6 * m, 8 * m, 9 * m], I64)   # дыры: 3 пропущена, 7 пропущена
    low = np.array([-0.06, -0.07, -0.01, -0.06, -0.06, -0.12, -0.06, -0.04])
    op = np.zeros(8); cl = np.zeros(8)
    e5 = [(int(t[i]), n) for i, n in ev.events_of(t, op, low, cl, 0.05)]
    assert e5 == [(0, 2), (4 * m, 3), (8 * m, 1)]                     # серии: [0,1], [4,5,6], [8]; 2 разрывает, дыра 7 — новый
    e10 = [(int(t[i]), n) for i, n in ev.events_of(t, op, low, cl, 0.10)]
    assert e10 == [(6 * m, 1)]


def main():
    n = 0
    for k, f in sorted(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
            n += 1
    print("ok", n)


if __name__ == "__main__":
    sys.exit(main())
