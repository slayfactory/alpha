#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): `StoreTicks` — замена `sigfeat_lib.Ticks` (zip aggTrades через pandas) чтением склада тиков Rust (`/data/p42-tk599/tick-store`, формат v2 `src/breakout/store.rs`):
блок A = zstd от [первая цена i64, НОД u64, затем на сделку varint зигзаг((цена − прежняя)/НОД) и varint((off − прежний)<<1 | bm)]; off — мс от полуночи суток. Те же массивы (t, p), что у `Ticks`
(t — мс от `SIGFEAT_BASE_DATE`, p — цена в e8), те же правила «сутки есть / нет» (есть zip в каталоге окна), тот же `span`. Склад устарел / нет файла / цена неточная (флаг) → чтение zip, как раньше.
Только чтение склада; сутки ≥ 14.11.2026 не открываются (изоляция). Ворота равенства — `tk671_ticks_gate.py`.
"""
from __future__ import annotations

import datetime
import os
import struct

import numpy as np
import numba
import pyarrow as pa

import sigfeat_lib as L

STORE_ROOT = os.environ.get("TK671_TICK_STORE", "/data/p42-tk599/tick-store")
HEAD_LEN = 96
FORBIDDEN_FROM = datetime.date(2026, 11, 14)
F_PX_INEXACT = 1
DAY_MS = L.DAY_MS


@numba.njit(cache=True)
def _decode(raw, n, day_base):
    """Нагрузка блока A → (t, p, ok): t = day_base + off (int64), p = цена e8. ok = False при обрыве / лишних байтах / off вне суток."""
    t = np.empty(n, np.int64)
    p = np.empty(n, np.int64)
    m = raw.shape[0]
    if m < 16:
        return t, p, False
    px = np.int64(0)
    g = np.int64(0)
    for j in range(8):
        px |= np.int64(raw[j]) << np.int64(8 * j)
        g |= np.int64(raw[8 + j]) << np.int64(8 * j)
    i = 16
    off = np.int64(0)
    for k in range(n):
        z = np.uint64(0)
        s = 0
        while True:
            if i >= m or s > 63:
                return t, p, False
            c = raw[i]
            i += 1
            z |= np.uint64(c & 0x7F) << np.uint64(s)
            if c < 0x80:
                break
            s += 7
        d = np.int64(z >> np.uint64(1)) ^ -np.int64(z & np.uint64(1))
        px += d * g
        w = np.uint64(0)
        s = 0
        while True:
            if i >= m or s > 63:
                return t, p, False
            c = raw[i]
            i += 1
            w |= np.uint64(c & 0x7F) << np.uint64(s)
            if c < 0x80:
                break
            s += 7
        off += np.int64(w >> np.uint64(1))
        if off >= DAY_MS:
            return t, p, False
        t[k] = day_base + off
        p[k] = px
    return t, p, i == m


class StoreTicks(L.Ticks):
    """Как `Ticks`, но сутки читаются из склада; счётчики: `n_store`, `n_zip` (запасной путь), `n_missing`."""

    def __init__(self, root: str, sym: str, keep: int = 4):
        super().__init__(root, sym, keep)
        self.n_store = self.n_zip = self.n_missing = 0

    def _read(self, d: int):
        date = L.day_date(d)
        zpath = f"{self.root}/{self.sym}/{self.sym}-aggTrades-{date}.zip"
        try:
            zst = os.stat(zpath)
        except FileNotFoundError:                      # каталоги окон кончаются 13.11: сутки нет → None, как у `Ticks`
            self.n_missing += 1
            return None
        if datetime.date.fromisoformat(date) >= FORBIDDEN_FROM:
            raise RuntimeError("склад: сутки %s ≥ 14.11 не открываются (изоляция)" % date)
        v = self._from_store(date, d, zst)
        if v is None:
            self.n_zip += 1
            return super()._read(d)
        self.n_store += 1
        return v

    def _from_store(self, date: str, d: int, zst):
        path = f"{STORE_ROOT}/{self.sym}/{self.sym}-{date}.tk"
        try:
            with open(path, "rb") as f:
                head = f.read(HEAD_LEN)
                if len(head) < HEAD_LEN or head[:4] != b"TK42":
                    return None
                ver, size, mtime_s, mtime_ns, n = struct.unpack_from("<IQqII", head, 4)
                if ver != 2 or size != zst.st_size or (mtime_s, mtime_ns) != divmod(zst.st_mtime_ns, 1_000_000_000):
                    return None
                raw_a, comp_a, flags = struct.unpack_from("<III", head, 64)
                if flags & F_PX_INEXACT:
                    return None
                body = f.read(comp_a)
            if len(body) != comp_a:
                return None
            raw = np.frombuffer(pa.decompress(body, decompressed_size=raw_a, codec="zstd"), dtype=np.uint8)
            day_base = d * DAY_MS
            t, p, ok = _decode(raw, n, day_base)
            if not ok:
                return None
        except (OSError, pa.ArrowInvalid):
            return None
        return t, p
