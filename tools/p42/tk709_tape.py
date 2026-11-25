#!/usr/bin/env python3
"""TK-709 (протокол P-TK709 §5): лента склада тиков (прямое чтение `.tk`, формат v2, `src/breakout/store.rs`) и проход по ленте на $500.
Склад читается без сверки с zip (протокол §3; склад доказан). Цены — float из e8, количество — float, `buy` = покупатель-тейкер (`bm == 0`).
Проход входа: сделки покупателей-тейкеров со временем ≥ T0 (окно 60 с) копятся до $500; E0 = max(первая сделка, VWAP набранного); мало — нет входа («тонко»).
Проход выхода зеркально по продавцам-тейкерам на количество позиции: E1 = min(первая сделка, VWAP); окно 60 с → 600 с → неполный VWAP → последняя цена."""
from __future__ import annotations

import datetime as dt
import os
import struct

import numpy as np
import pyarrow as pa

import tk701_tape as T

STORE = os.environ.get("TK709_STORE", "/data/p42-tk599/tick-store")
DAY_MS = 86_400_000
EPOCH = dt.date(1970, 1, 1)
HEAD_LEN = 96
BAD_FLAGS = 1 | 2 | 4                                # цена неточна / количество плохо / количество f64

ST_OK, ST_EXT, ST_PART, ST_LAST = 0, 1, 2, 3          # статус выхода


def day_index(ms: int) -> int:
    return int(ms // DAY_MS)


def day_str(d: int) -> str:
    return (EPOCH + dt.timedelta(days=int(d))).isoformat()


def read_day(sym: str, d: int):
    """Сутки `d` (дней от эпохи) монеты: (t мс, цена, кол-во, buy) или None (нет файла / неточный / битый). Нарушения порядка — как у склада: файл в порядке биржи."""
    path = f"{STORE}/{sym}/{sym}-{day_str(d)}.tk"
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_LEN)
            if len(head) < HEAD_LEN or head[:4] != b"TK42":
                return None
            ver, size, mtime_s, mtime_ns, n = struct.unpack_from("<IQqII", head, 4)
            if ver != 2:
                return None
            raw_a, comp_a, flags, qty_dec = struct.unpack_from("<IIII", head, 64)
            qty_step, raw_q, comp_q = struct.unpack_from("<QII", head, 80)
            if flags & BAD_FLAGS or qty_dec > 18:
                return None
            body = f.read(comp_a)
            qbody = f.read(comp_q)
    except OSError:
        return None
    if len(body) != comp_a or len(qbody) != comp_q:
        return None
    try:
        t, p, bm, ok = T._decode_a(np.frombuffer(pa.decompress(body, decompressed_size=raw_a, codec="zstd"), dtype=np.uint8), n, d * DAY_MS)
        q, ok2 = T._decode_q(np.frombuffer(pa.decompress(qbody, decompressed_size=raw_q, codec="zstd"), dtype=np.uint8), n, qty_step)
    except pa.ArrowInvalid:
        return None
    if not (ok and ok2):
        return None
    return t, p.astype(np.float64) / 1e8, q.astype(np.float64) / (10.0 ** qty_dec), bm == 0


class Tape:
    """Лента одной монеты с кэшем суток. `bad` — счётчики нарушений ворот G2 (порядок времени, цена/кол-во ≤ 0)."""

    def __init__(self, sym: str, keep: int = 6):
        self.sym, self.keep = sym, keep
        self.cache, self.order = {}, []
        self.bad = {"unsorted": 0, "nonpos": 0}

    def day(self, d: int):
        if d in self.cache:
            return self.cache[d]
        v = read_day(self.sym, d)
        if v is not None:
            t, p, q, _ = v
            if len(t) > 1 and bool((np.diff(t) < 0).any()):
                self.bad["unsorted"] += 1
            if len(t) and (bool((p <= 0).any()) or bool((q <= 0).any())):
                self.bad["nonpos"] += 1
        self.cache[d] = v
        self.order.append(d)
        while len(self.order) > self.keep:
            self.cache.pop(self.order.pop(0), None)
        return v

    def have(self, a: int, b: int) -> bool:
        return all(self.day(d) is not None for d in range(day_index(a), day_index(b) + 1))

    def span(self, a: int, b: int):
        """Сделки с t в [a, b] (мс); все сутки должны быть (`have`), иначе вызов не имеет смысла."""
        ts, ps, qs, bs = [], [], [], []
        for d in range(day_index(a), day_index(b) + 1):
            v = self.day(d)
            if v is None:
                continue
            t, p, q, buy = v
            i0, i1 = np.searchsorted(t, a, "left"), np.searchsorted(t, b, "right")
            if i1 > i0:
                ts.append(t[i0:i1]); ps.append(p[i0:i1]); qs.append(q[i0:i1]); bs.append(buy[i0:i1])
        if not ts:
            return np.empty(0, np.int64), np.empty(0), np.empty(0), np.empty(0, bool)
        if len(ts) == 1:
            return ts[0], ps[0], qs[0], bs[0]
        return np.concatenate(ts), np.concatenate(ps), np.concatenate(qs), np.concatenate(bs)

    def last_before(self, ms: int):
        """Цена последней сделки со временем < ms (суток назад до 2); нет — nan."""
        for d in range(day_index(ms), day_index(ms) - 3, -1):
            v = self.day(d)
            if v is None:
                continue
            t, p, _, _ = v
            i = int(np.searchsorted(t, ms, "left"))
            if i > 0:
                return float(p[i - 1])
        return float("nan")


# ---- проход по ленте -------------------------------------------------------------------------------------------------------------

def entry_walk(t, p, q, buy, t0, cap_ms=60_000, notional=500.0):
    """(e0, p1, vwap, delay_ms) по покупателям-тейкерам в [t0, t0+cap]; нет сделок / меньше `notional` — None («тонко»). Массивы — слайс по времени."""
    m = buy & (t >= t0) & (t <= t0 + cap_ms)
    if not m.any():
        return None
    tt, tp, tq = t[m], p[m], q[m]
    cum = np.cumsum(tp * tq)
    if cum[-1] < notional:
        return None
    k = int(np.searchsorted(cum, notional, "left"))
    prev = float(cum[k - 1]) if k > 0 else 0.0
    qty = float(tq[:k].sum()) + (notional - prev) / float(tp[k])
    vwap = notional / qty
    p1 = float(tp[0])
    return max(p1, vwap), p1, vwap, int(tt[k] - t0)


def exit_walk(t, p, q, buy, t1, qty, last_px, cap1_ms=60_000, cap2_ms=600_000):
    """(e1, p1, vwap, статус) по продавцам-тейкерам на количество `qty` в [t1, t1+cap2]: ST_OK (≤ cap1), ST_EXT (≤ cap2), ST_PART (VWAP набранного), ST_LAST (нет продаж: `last_px`).
    `last_px` — цена последней сделки до t1+cap2 (nan — позиция не оценивается, возвращается None)."""
    m = (~buy) & (t >= t1) & (t <= t1 + cap2_ms)
    if not m.any():
        if np.isnan(last_px):
            return None
        return last_px, last_px, last_px, ST_LAST
    tt, tp, tq = t[m], p[m], q[m]
    p1 = float(tp[0])
    cumq = np.cumsum(tq)
    if cumq[-1] >= qty:
        k = int(np.searchsorted(cumq, qty, "left"))
        prevq = float(cumq[k - 1]) if k > 0 else 0.0
        notional = float((tp[:k] * tq[:k]).sum()) + (qty - prevq) * float(tp[k])
        vwap = notional / qty
        st = ST_OK if tt[k] <= t1 + cap1_ms else ST_EXT
    else:
        vwap = float((tp * tq).sum() / cumq[-1])
        st = ST_PART
    return min(p1, vwap), p1, vwap, st
