#!/usr/bin/env python3
"""П-42 §13.72 (TK-672): причинный выбор «топ-K в сутки» (prereg-13.72.md §5).

Строка со временем `t` в часе `[h, h+1)` (UTC) выбрана, если `score(t) ≥ τ_h`; `τ_h` — k-е по величине значение `score` у строк с `abs_ms ∈ [h − 24 ч, h)` (все строки, с меткой и без),
`k = max(1, ⌈q·n⌉)`, `n` — число строк окна. Порог обновляется раз в час и не видит строк позже `h`. K — ожидаемое число выбранных строк в сутки (K₁ = строки базы в сутки, K₂ = 2·K₁), `q = K / R_all`."""
from __future__ import annotations

import math

import numpy as np

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def rates(abs_ms, base, lo_ms, hi_ms):
    """Строки в сутки на интервале `[lo_ms, hi_ms)`: (R_base — строки с base = 1, R_all — все строки). Только счётчики, метки не участвуют."""
    abs_ms = np.asarray(abs_ms, dtype=np.int64)
    m = (abs_ms >= lo_ms) & (abs_ms < hi_ms)
    days = (hi_ms - lo_ms) / DAY_MS
    assert days > 0, "пустой интервал"
    return float((np.asarray(base)[m] > 0.5).sum() / days), float(m.sum() / days)


def causal_select(ms, score, q, first_ms):
    """Булев массив «строка выбрана» в порядке входа. Выбираются только строки со временем ≥ `first_ms` (кратно часу); строки раньше нужны лишь для порога (разогрев).
    Пустое окно порога (ни одной строки за 24 ч до часа) — ничего не выбирается."""
    ms = np.asarray(ms, dtype=np.int64)
    score = np.asarray(score, dtype=np.float64)
    assert first_ms % HOUR_MS == 0, "first_ms не кратен часу"
    assert not np.isnan(score).any(), "NaN в оценках"
    n = len(ms)
    sel = np.zeros(n, dtype=bool)
    if n == 0:
        return sel
    o = np.argsort(ms, kind="stable")
    hs = ms[o] // HOUR_MS
    h0, h1 = int(first_ms // HOUR_MS), int(hs[-1])
    for h in range(h0, h1 + 1):
        lo, mid, hi = np.searchsorted(hs, [h - 24, h, h + 1], side="left")
        if hi == mid:
            continue
        w = score[o[lo:mid]]
        nw = len(w)
        if nw == 0:
            continue
        k = min(max(1, math.ceil(q * nw)), nw)
        tau = np.partition(w, nw - k)[nw - k]
        rows = o[mid:hi]
        sel[rows] = score[rows] >= tau
    return sel
