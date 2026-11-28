#!/usr/bin/env python3
"""П-42 этап A, шаг 4а (TK-728): кодек суточного файла склада тиков v2 (`src/breakout/store.rs`) на питоне — чтение целиком (блок A + блок Q + заголовок), запись «как `head()` + `encode()` + `encode_qty()` + zstd-3 с контрольной суммой», обрезка суток по моменту сигнала.
Склад Rust бинарей проверяет в заголовке размер и mtime zip (поля копируются из исходного файла) — файл, записанный здесь с теми же полями, бинарь принимает как свой.
Формат: заголовок 96 байт (`<4sIQqIIQQQQIIIIQII`: магия, версия, размер zip, mtime_s, mtime_ns, n, rows, unsorted, out_of_day, bad_rows, raw_a, comp_a, flags, qty_dec, qty_step, raw_q, comp_q),
блок A = zstd(first i64, НОД u64, затем на сделку varint зигзаг((цена − прежняя)/НОД), varint((off − прежний)<<1 | bm)), блок Q = zstd(varint(количество/шаг) на сделку).
Сжатие — `zstd -3 -c` (CLI, контрольная сумма включена, размер входа не записывается — как у `zstd::stream::write::Encoder` с `include_checksum(true)` в Rust).
  python3 tk728_trunc_store.py check <файл.tk> [...]                 — проверка метода 1: декод → кодирование без обрезки → сверка (байты, нагрузки блоков, массивы)
  python3 tk728_trunc_store.py cut <файл.tk> <выход.tk> <cut_off_мс>  — оставить сделки с off ≤ cut_off (мс от полуночи UTC даты файла)"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys

import numba
import numpy as np
import pyarrow as pa

HEAD_LEN = 96
HEAD_FMT = "<4sIQqIIQQQQIIIIQII"
MAGIC = b"TK42"
VERSION = 2
DAY_MS = 86_400_000
ZSTD_LEVEL = 3
F_PX_INEXACT, F_QTY_BAD, F_QTY_F64 = 1, 2, 4
F64_EXACT = 1 << 53
HEAD_KEYS = ("magic", "ver", "size", "mtime_s", "mtime_ns", "n", "rows", "unsorted", "out_of_day", "bad_rows", "raw_a", "comp_a", "flags", "qty_dec", "qty_step", "raw_q", "comp_q")


def parse_head(buf: bytes) -> dict:
    if len(buf) < HEAD_LEN:
        raise ValueError("заголовок короче %d байт" % HEAD_LEN)
    h = dict(zip(HEAD_KEYS, struct.unpack(HEAD_FMT, buf[:HEAD_LEN])))
    if h["magic"] != MAGIC or h["ver"] != VERSION:
        raise ValueError("не склад v2: %r v%d" % (h["magic"], h["ver"]))
    return h


def pack_head(h: dict) -> bytes:
    return struct.pack(HEAD_FMT, *[h[k] for k in HEAD_KEYS])


@numba.njit(cache=True)
def _dec_a(raw, n):
    """Блок A → (px int64, off int64, bm uint8, ok). Как `decode` в store.rs: обрыв, лишние байты, off ≥ сутки → ok = False."""
    px_o = np.empty(n, np.int64)
    off_o = np.empty(n, np.int64)
    bm_o = np.empty(n, np.uint8)
    m = raw.shape[0]
    if m < 16:
        return px_o, off_o, bm_o, False
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
                return px_o, off_o, bm_o, False
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
                return px_o, off_o, bm_o, False
            c = raw[i]
            i += 1
            w |= np.uint64(c & 0x7F) << np.uint64(s)
            if c < 0x80:
                break
            s += 7
        off += np.int64(w >> np.uint64(1))
        if off >= 86400000:
            return px_o, off_o, bm_o, False
        px_o[k] = px
        off_o[k] = off
        bm_o[k] = np.uint8(w & np.uint64(1))
    return px_o, off_o, bm_o, i == m


@numba.njit(cache=True)
def _dec_q(raw, n, step):
    q = np.empty(n, np.uint64)
    m = raw.shape[0]
    i = 0
    for k in range(n):
        r = np.uint64(0)
        s = 0
        while True:
            if i >= m or s > 63:
                return q, False
            c = raw[i]
            i += 1
            r |= np.uint64(c & 0x7F) << np.uint64(s)
            if c < 0x80:
                break
            s += 7
        q[k] = r * step
    return q, i == m


@numba.njit(cache=True)
def _gcd(a, b):
    while b != np.uint64(0):
        t = a % b
        a = b
        b = t
    return a


@numba.njit(cache=True)
def _put(out, pos, v):
    while v >= np.uint64(0x80):
        out[pos] = np.uint8((v & np.uint64(0x7F)) | np.uint64(0x80))
        pos += 1
        v >>= np.uint64(7)
    out[pos] = np.uint8(v)
    return pos + 1


@numba.njit(cache=True)
def _enc_a(px, off, bm, out):
    """Как `encode` в store.rs. Возвращает длину нагрузки или −1 (off не по порядку / вне суток)."""
    n = px.shape[0]
    first = px[0] if n > 0 else np.int64(0)
    g = np.uint64(0)
    prev = first
    poff = np.int64(0)
    for k in range(n):
        if off[k] < poff or off[k] >= 86400000:
            return -1
        poff = off[k]
        if g != np.uint64(1):
            d = px[k] - prev
            g = _gcd(g, np.uint64(d if d >= 0 else -d))
        prev = px[k]
    if g == np.uint64(0):
        g = np.uint64(1)
    for j in range(8):
        out[j] = np.uint8((np.uint64(first) >> np.uint64(8 * j)) & np.uint64(0xFF))
        out[8 + j] = np.uint8((g >> np.uint64(8 * j)) & np.uint64(0xFF))
    pos = 16
    ppx = first
    poff = np.int64(0)
    gi = np.int64(g)
    for k in range(n):
        d = (px[k] - ppx) // gi
        z = np.uint64((d << np.int64(1)) ^ (d >> np.int64(63)))
        pos = _put(out, pos, z)
        pos = _put(out, pos, (np.uint64(off[k] - poff) << np.uint64(1)) | np.uint64(bm[k]))
        ppx = px[k]
        poff = off[k]
    return pos


@numba.njit(cache=True)
def _enc_q(q, out):
    """Как `encode_qty`: шаг = НОД (минимум 1), на сделку varint(q / шаг). Возвращает (шаг, длина)."""
    g = np.uint64(0)
    for k in range(q.shape[0]):
        g = _gcd(g, q[k])
    if g == np.uint64(0):
        g = np.uint64(1)
    pos = 0
    for k in range(q.shape[0]):
        pos = _put(out, pos, q[k] // g)
    return g, pos


@numba.njit(cache=True)
def _rd(q, qd):
    """Число знаков после точки количества «как разобрано» (`dec_parts`: хвостовые нули убраны): qd − min(нулей на конце значения, qd); 0 для нуля."""
    n = q.shape[0]
    r = np.empty(n, np.int64)
    for k in range(n):
        v = q[k]
        if v == np.uint64(0):
            r[k] = 0
            continue
        z = 0
        while z < qd and v % np.uint64(10) == np.uint64(0):
            v //= np.uint64(10)
            z += 1
        r[k] = qd - z
    return r


def zstd_compress(raw: bytes) -> bytes:
    exe = shutil.which("zstd")
    if exe is None:
        raise RuntimeError("нет zstd (CLI): точное сжатие невозможно")
    r = subprocess.run([exe, "-%d" % ZSTD_LEVEL, "-q", "-c", "-T1"], input=raw, capture_output=True, check=True)
    return r.stdout


def zstd_decompress(body: bytes, raw_len: int) -> bytes:
    out = pa.decompress(body, decompressed_size=raw_len, codec="zstd")
    out = out.to_pybytes() if hasattr(out, "to_pybytes") else bytes(out)
    if len(out) != raw_len:
        raise ValueError("распаковано %d байт вместо %d" % (len(out), raw_len))
    return out


def read_day_file(path: str) -> dict:
    """Файл склада → dict(head, px, off, bm, qty, raw_a, raw_q, comp_a, comp_q): массивы по сделкам и исходные байты блоков."""
    with open(path, "rb") as f:
        data = f.read()
    h = parse_head(data)
    if len(data) != HEAD_LEN + h["comp_a"] + h["comp_q"]:
        raise ValueError("длина файла %d ≠ заголовок + блоки %d" % (len(data), HEAD_LEN + h["comp_a"] + h["comp_q"]))
    ca = data[HEAD_LEN:HEAD_LEN + h["comp_a"]]
    cq = data[HEAD_LEN + h["comp_a"]:]
    ra = zstd_decompress(ca, h["raw_a"])
    rq = zstd_decompress(cq, h["raw_q"])
    px, off, bm, ok = _dec_a(np.frombuffer(ra, np.uint8), h["n"])
    if not ok:
        raise ValueError("блок A повреждён")
    q, ok = _dec_q(np.frombuffer(rq, np.uint8), h["n"], np.uint64(h["qty_step"]))
    if not ok:
        raise ValueError("блок Q повреждён")
    return dict(head=h, px=px, off=off, bm=bm, qty=q, raw_a=ra, raw_q=rq, comp_a=ca, comp_q=cq)


def encode_day(head: dict, px, off, bm, qty, keep_counters: bool) -> tuple:
    """Сутки (массивы) → байты файла. `head` — исходный заголовок: size / mtime копируются. Правила `head()` и `DayTrades::new` / `QtyRaw::finish`:
    qty_dec = максимум знаков «как разобрано» по оставленным сделкам, значение пересчитывается в единицы 10^-qty_dec, F_QTY_F64 — по значению > 2^53.
    Счётчики (`rows, unsorted, out_of_day, bad_rows`): keep_counters — как в исходном; иначе rows = n + out_of_day + bad_rows (оставленные сделки + прежние отсевы — их место в суточном файле неизвестно), остальные — прежние.
    Возвращает (байты, dict сведений)."""
    n = int(px.shape[0])
    qd0 = int(head["qty_dec"])
    rd = _rd(qty, qd0) if n else np.zeros(0, np.int64)
    qd = int(rd.max()) if n else 0
    sh = qd0 - qd
    if sh:
        qv = qty // np.uint64(10 ** sh)
        if np.any(qv * np.uint64(10 ** sh) != qty):
            raise ValueError("количество не делится на 10^%d: масштаб пересчитать нельзя" % sh)
    else:
        qv = qty
    flags = int(head["flags"]) & (F_PX_INEXACT | F_QTY_BAD)
    if n and bool(np.any(qv > np.uint64(F64_EXACT))):
        flags |= F_QTY_F64
    buf = np.empty(16 + 20 * n + 16, np.uint8)
    la = _enc_a(px, off, bm, buf)
    if la < 0:
        raise ValueError("сделки вне допущений кодека (off не по порядку)")
    ra = buf[:la].tobytes()
    qbuf = np.empty(10 * n + 16, np.uint8)
    step, lq = _enc_q(qv, qbuf)
    rq = qbuf[:lq].tobytes()
    ca, cq = zstd_compress(ra), zstd_compress(rq)
    h = dict(head)
    h.update(n=n, raw_a=len(ra), comp_a=len(ca), flags=flags, qty_dec=qd, qty_step=int(step), raw_q=len(rq), comp_q=len(cq))
    if not keep_counters:
        h["rows"] = n + int(head["out_of_day"]) + int(head["bad_rows"])
    return pack_head(h) + ca + cq, dict(n=n, qty_dec=qd, flags=flags, qty_step=int(step), raw_a=len(ra), raw_q=len(rq))


def write_atomic(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def cut_file(src: str, dst: str, cut_off: int | None) -> dict:
    """Оставить сделки `off ≤ cut_off` (None — все, счётчики как в исходном); записать в dst. Возвращает сведения (n до / после)."""
    d = read_day_file(src)
    n0 = int(d["px"].shape[0])
    k = n0 if cut_off is None else int(np.searchsorted(d["off"], cut_off, side="right"))
    data, info = encode_day(d["head"], d["px"][:k], d["off"][:k], d["bm"][:k], d["qty"][:k], keep_counters=(cut_off is None))
    write_atomic(dst, data)
    info.update(n_before=n0, n_after=k, last_off_kept=int(d["off"][k - 1]) if k else None, first_off_dropped=int(d["off"][k]) if k < n0 else None)
    return info


def check_file(path: str) -> dict:
    """Метод 1: файл → декод → кодирование без обрезки → сравнение: байты файла, нагрузки блоков (до zstd), заголовок, массивы."""
    d = read_day_file(path)
    h = d["head"]
    data, info = encode_day(h, d["px"], d["off"], d["bm"], d["qty"], keep_counters=True)
    with open(path, "rb") as f:
        orig = f.read()
    new_h = parse_head(data)
    new_ca = data[HEAD_LEN:HEAD_LEN + new_h["comp_a"]]
    new_cq = data[HEAD_LEN + new_h["comp_a"]:]
    # независимая проверка: распаковать собственный файл и сравнить массивы с исходными
    ra, rq = zstd_decompress(new_ca, new_h["raw_a"]), zstd_decompress(new_cq, new_h["raw_q"])
    px2, off2, bm2, ok_a = _dec_a(np.frombuffer(ra, np.uint8), new_h["n"])
    q2, ok_q = _dec_q(np.frombuffer(rq, np.uint8), new_h["n"], np.uint64(new_h["qty_step"]))
    return dict(
        path=path, n=int(h["n"]), flags=int(h["flags"]), qty_dec=int(h["qty_dec"]), rows=int(h["rows"]), out_of_day=int(h["out_of_day"]), bad_rows=int(h["bad_rows"]), unsorted=int(h["unsorted"]),
        head_equal=(pack_head(new_h)[:HEAD_LEN] == orig[:HEAD_LEN]),
        head_diff=[k for k in HEAD_KEYS if new_h[k] != h[k]],
        raw_a_equal=(ra == d["raw_a"]), raw_q_equal=(rq == d["raw_q"]),
        comp_a_equal=(new_ca == d["comp_a"]), comp_q_equal=(new_cq == d["comp_q"]),
        bytes_equal=(data == orig),
        arrays_equal=bool(ok_a and ok_q and np.array_equal(px2, d["px"]) and np.array_equal(off2, d["off"]) and np.array_equal(bm2, d["bm"]) and np.array_equal(q2, d["qty"])),
    )


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("check", "cut"):
        print(__doc__)
        sys.exit(2)
    if sys.argv[1] == "check":
        bad = 0
        for p in sys.argv[2:]:
            r = check_file(p)
            ok = r["arrays_equal"] and r["raw_a_equal"] and r["raw_q_equal"] and r["head_equal"]
            bad += 0 if ok else 1
            print(json.dumps(r, ensure_ascii=False))
        print("ИТОГО файлов %d, расхождений нагрузки/заголовка/массивов %d" % (len(sys.argv) - 2, bad))
        sys.exit(1 if bad else 0)
    print(json.dumps(cut_file(sys.argv[2], sys.argv[3], int(sys.argv[4])), ensure_ascii=False))


if __name__ == "__main__":
    main()
