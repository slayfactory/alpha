#!/usr/bin/env python3
"""TK-684: метрики линии и суммы двух линий для вида «Варианты» вкладки «Эквити» (:8090). Чистый Python, без numpy — те же формулы повторяет JS страницы.
Определения — как в летописи (tk674_stats.metrics): суточный ряд Σ $ по дню ВХОДА за все сутки окна (нули в сутки без сделок); Шарп = среднее / станд. откл. (ddof = 1) × √365;
просадка — от пика накопленного суточного ряда (старт 0), % — от маржи (250 $ на линию, у суммы двух линий — 500 $); перехай — самый длинный зазор между новыми максимумами,
сутки, включая хвост (tk663_shape.uw → gap_longest); R:R = средний выигрыш ÷ |средний проигрыш|; худшая сделка — минимум. Медиан нет. P&L сделки — простой ($ при ставке 500 $)."""
from __future__ import annotations

import math

STAKE = 500.0
MARGIN = 250.0
NDAYS = 136
YEAR_DAYS = 365
EPS = 1e-9


def daily(days, vals, ndays=NDAYS):
    s = [0.0] * ndays
    for d, v in zip(days, vals):
        s[d] += v
    return s


def sharpe(s):
    n = len(s)
    if n < 2:
        return None
    m = sum(s) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in s) / (n - 1))
    return m / sd * math.sqrt(YEAR_DAYS) if sd > 0 else None


def maxdd(s):
    c = pk = dd = 0.0
    for x in s:
        c += x
        pk = max(pk, c)
        dd = max(dd, pk - c)
    return dd


def gap_longest(s):
    """Самый длинный зазор между новыми максимумами накопленного ряда, шагов; хвост после последнего максимума считается (ряд без максимумов — вся длина)."""
    c = pk = 0.0
    last, best = -1, 0
    for i, x in enumerate(s):
        c += x
        if c > pk + EPS:
            best = max(best, i - last)
            last = i
            pk = c
    return max(best, len(s) - 1 - last)


def daily_metrics(s):
    """Метрики готового суточного ряда (веер, tk684_fan.py): Шарп, просадка, перехай."""
    s = [float(x) for x in s]
    return {"sharpe": sharpe(s), "dd": maxdd(s), "gap": gap_longest(s)}


def rr(vals):
    win = [v for v in vals if v > 0]
    loss = [v for v in vals if v < 0]
    if not win or not loss:
        return None
    return (sum(win) / len(win)) / abs(sum(loss) / len(loss))


def metrics(days, vals, ndays=NDAYS, margin=MARGIN):
    n = len(vals)
    s = daily(days, vals, ndays)
    tot = sum(vals)
    dd = maxdd(s)
    return {"n": n, "sum": tot, "mean": tot / n if n else None, "sharpe": sharpe(s), "dd": dd, "ddp": dd / margin * 100.0, "gap": gap_longest(s),
            "rr": rr(vals), "worst": min(vals) if n else None}


def combine(a, b, ndays=NDAYS):
    """Шорт + лонг: сделки обеих линий вместе, по 500 $ на сделку с каждой стороны; маржа — две."""
    return metrics(list(a["d"]) + list(b["d"]), list(a["v"]) + list(b["v"]), ndays, 2 * MARGIN)


def gap_under(s):
    """Перехай чтения вперёд (tk616_forward.describe_rows, В-120): самый длинный ряд суток подряд без нового максимума накопленного; первые сутки — максимум (у gap_longest ряд с первых суток ниже нуля — зазор с шага −1)."""
    c = 0.0
    peak, since, best = None, 0, 0
    for x in s:
        c += x
        if peak is None or c > peak:
            peak, since = c, 0
        else:
            since += 1
        best = max(best, since)
    return best


def fwd_metrics(days, vals, ndays, margin=MARGIN):
    """Метрики линии за дни вперёд (суточный ряд по дню входа, ndays суток чтения) — те же числа, что в describe_rows; перехай — gap_under."""
    m = metrics(days, vals, ndays, margin)
    m["gap"] = gap_under(daily(days, vals, ndays))
    return m


def fwd_combine(a, b, ndays):
    """Шорт + лонг за дни вперёд: сделки обеих линий вместе, маржа — две."""
    return fwd_metrics(list(a["d"]) + list(b["d"]), list(a["v"]) + list(b["v"]), ndays, 2 * MARGIN)

