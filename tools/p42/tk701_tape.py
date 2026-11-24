#!/usr/bin/env python3
"""TK-701 (П-42, описательно): лента склада тиков с битом агрессора и количеством (`StoreTape`) + таблица ленты по сделкам линий TK-698.
Склад — `/data/p42-tk599/tick-store` (формат v2, `src/breakout/store.rs`): блок A даёт цену, время и бит `bm` (покупатель — мейкер), блок Q — количество; `tk671_ticks` читает только (t, p).
Охрана склада как у `StoreTicks`: размер и mtime zip в каталоге окна = заголовок файла, иначе / при флагах неточности — чтение zip. Сутки < 01.07 не открываются (В-170), сутки ≥ 14.11 не открываются (изоляция).
Признаки сделки (знак s = +1 лонг, −1 шорт; все «+» = по ходу сделки; NATR — из файла пути TK-698, 30-минутный TR / close закрытой минуты перед сигналом, одно определение у обеих сторон):
  до сигнала (тики строго раньше sig_ms, G-causal): поток TK-594 `flow_features` (tfv / tfn 10 / 60 / 300 / 1800, acc_v, acc_n, lmsz300, msz_r), знаковый ход за 5 / 15 / 30 / 60 / 300 с до сигнала (pre_r<k>, доля от цены входа);
  после входа (исход, не ось): знаковый ход r<τ>, бегущие MFE / MAE (mfe<τ> / mae<τ>, доли), τ = 1…3 600 с от входа; первое достижение +x NATR (up<x>), −y NATR (dn<y>), −4 % (stop4), «дошло до +x и вернулось к входу» (ret<x>), ход после точки возврата (ret<x>_c900 / c3600); поток [0, +10) / [0, +60) с;
  выход: ход от входа через 900 / 3 600 с после выхода движка (post900 / post3600) и столбцы пути `path_one` в [вход, выход] для ворот G-ref против файлов TK-698.
Запуск на srv: python3 tk701_tape.py <линия> <окно> [<выход>]   — таблица `<выход>/<линия>-<окно>.parquet`."""
from __future__ import annotations

import datetime as dt
import math
import os
import struct
import sys
import time

import numba
import numpy as np
import pandas as pd
import pyarrow as pa

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk594_feat as F594  # noqa: E402
import tk685_lib as lib685  # noqa: E402

STORE_ROOT = os.environ.get("TK671_TICK_STORE", "/data/p42-tk599/tick-store")
WIN_ROOT = "/data/p42-tk599/%s/aggtrades"
TR_ROOT = "/data/p42-tk698"
OUT_ROOT = "/data/p42-tk701"
HEAD_LEN = 96
DAY_MS = 86_400_000
EPOCH = dt.date(1970, 1, 1)
UTC = dt.timezone.utc
FIRST_MS = int(dt.datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000)
LAST_MS = int(dt.datetime(2026, 11, 14, tzinfo=UTC).timestamp() * 1000)
F_PX_INEXACT, F_QTY_BAD, F_QTY_F64 = 1, 2, 4
NOTIONAL = 500.0
FEE_ROUND = 0.315                                   # $ круг на 500 $: тейкер 3,5 бп на ногу минус 10 % возврата, обе ноги по рынку (в данных TK-698 fees 0,314–0,315)
TAUS = (1, 2, 5, 10, 30, 60, 120, 300, 900, 1800, 3600)
XS = (0.5, 1.0, 2.0)                                # барьеры «вверх» (по ходу), NATR
YS = (0.25, 0.5, 1.0, 2.0)                          # барьеры «вниз» (против хода), NATR
PRE = (5, 15, 30, 60, 300)
STOP_FRAC = 0.04
H_MS = 3_600_000
EXT_MS = 7_200_000
LOOK_MS = 2_100_000                                 # `msz_r`: 35 мин назад от сигнала
POST_EXIT = (900, 3600)


# ───────────────────────────── декодер ─────────────────────────────

@numba.njit(cache=True)
def _decode_a(raw, n, day_base):
    """Блок A → (t абсолютное мс, цена e8, bm 0/1, ok)."""
    t = np.empty(n, np.int64)
    p = np.empty(n, np.int64)
    bm = np.empty(n, np.uint8)
    m = raw.shape[0]
    if m < 16:
        return t, p, bm, False
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
                return t, p, bm, False
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
                return t, p, bm, False
            c = raw[i]
            i += 1
            w |= np.uint64(c & 0x7F) << np.uint64(s)
            if c < 0x80:
                break
            s += 7
        off += np.int64(w >> np.uint64(1))
        if off >= DAY_MS:
            return t, p, bm, False
        t[k] = day_base + off
        p[k] = px
        bm[k] = np.uint8(w & np.uint64(1))
    return t, p, bm, i == m


@numba.njit(cache=True)
def _decode_q(raw, n, step):
    """Блок Q → количество целым в единицах 10^-qty_dec (varint × шаг), ok."""
    q = np.empty(n, np.uint64)
    m = raw.shape[0]
    i = 0
    for k in range(n):
        z = np.uint64(0)
        s = 0
        while True:
            if i >= m or s > 63:
                return q, False
            c = raw[i]
            i += 1
            z |= np.uint64(c & 0x7F) << np.uint64(s)
            if c < 0x80:
                break
            s += 7
        q[k] = z * np.uint64(step)
    return q, i == m


def day_of(ms: int) -> int:
    return int(ms // DAY_MS)


def day_date(d: int) -> str:
    return (EPOCH + dt.timedelta(days=int(d))).isoformat()


class StoreTape:
    """Сутки монеты со склада с количеством и агрессором: (t мс абс., цена e8, v = цена·кол-во, покупка тейкера); кэш LRU по суткам. Счётчики `n_store`, `n_zip`, `n_missing`."""

    def __init__(self, win: str, sym: str, keep: int = 4, allow_nozip: bool = False):
        """allow_nozip: сутки, чьего zip нет в дереве окна (RIF, VIC: монеты убраны из дерева после сборки таблицы TK-671), читаются со склада без сверки размера и времени zip (TK-701 единички; `n_nozip`)."""
        self.root, self.sym, self.keep, self.allow_nozip = WIN_ROOT % win, sym, keep, allow_nozip
        self.cache, self.order = {}, []
        self.n_store = self.n_zip = self.n_missing = self.n_nozip = 0

    def day(self, d: int):
        if d in self.cache:
            return self.cache[d]
        v = self._read(d)
        self.cache[d] = v
        self.order.append(d)
        while len(self.order) > self.keep:
            self.cache.pop(self.order.pop(0), None)
        return v

    def _read(self, d: int):
        date = day_date(d)
        if date < "2026-07-01" or date >= "2026-11-14":
            raise RuntimeError("TK-701: сутки %s вне 01.07–13.11 не открываются" % date)
        zpath = f"{self.root}/{self.sym}/{self.sym}-aggTrades-{date}.zip"
        try:
            zst = os.stat(zpath)
        except FileNotFoundError:
            v = self._from_store(date, d, None) if self.allow_nozip else None
            if v is None:
                self.n_missing += 1
            else:
                self.n_nozip += 1
            return v
        v = self._from_store(date, d, zst)
        if v is None:
            self.n_zip += 1
            return self._from_zip(date, d)
        self.n_store += 1
        return v

    def _from_store(self, date, d, zst):
        path = f"{STORE_ROOT}/{self.sym}/{self.sym}-{date}.tk"
        try:
            with open(path, "rb") as f:
                head = f.read(HEAD_LEN)
                if len(head) < HEAD_LEN or head[:4] != b"TK42":
                    return None
                ver, size, mtime_s, mtime_ns, n = struct.unpack_from("<IQqII", head, 4)
                if ver != 2 or (zst is not None and (size != zst.st_size or (mtime_s, mtime_ns) != divmod(zst.st_mtime_ns, 1_000_000_000))):
                    return None
                raw_a, comp_a, flags, qty_dec = struct.unpack_from("<IIII", head, 64)
                qty_step, raw_q, comp_q = struct.unpack_from("<QII", head, 80)
                if flags & (F_PX_INEXACT | F_QTY_BAD | F_QTY_F64) or qty_dec > 18:
                    return None
                body = f.read(comp_a)
                qbody = f.read(comp_q)
            if len(body) != comp_a or len(qbody) != comp_q:
                return None
            t, p, bm, ok = _decode_a(np.frombuffer(pa.decompress(body, decompressed_size=raw_a, codec="zstd"), dtype=np.uint8), n, d * DAY_MS)
            if not ok:
                return None
            q, ok = _decode_q(np.frombuffer(pa.decompress(qbody, decompressed_size=raw_q, codec="zstd"), dtype=np.uint8), n, qty_step)
            if not ok:
                return None
        except (OSError, pa.ArrowInvalid):
            return None
        qty = q.astype(np.float64) / (10.0 ** qty_dec)
        return t, p, p.astype(np.float64) / 1e8 * qty, bm == 0

    def _from_zip(self, date, d):
        import zipfile
        name = f"{self.sym}-aggTrades-{date}"
        with zipfile.ZipFile(f"{self.root}/{self.sym}/{name}.zip") as zf, zf.open(name + ".csv") as fh:
            df = pd.read_csv(fh, usecols=["price", "quantity", "transact_time", "is_buyer_maker"], dtype={"price": np.float64, "quantity": np.float64, "transact_time": np.int64})
        t = df["transact_time"].to_numpy()
        p = np.rint(df["price"].to_numpy() * 1e8).astype(np.int64)
        v = df["price"].to_numpy() * df["quantity"].to_numpy()
        col = df["is_buyer_maker"]
        maker = col.to_numpy() if col.dtype == bool else col.astype(str).str.lower().eq("true").to_numpy()
        buy = ~maker
        if len(t) > 1 and np.any(np.diff(t) < 0):
            o = np.argsort(t, kind="stable")
            t, p, v, buy = t[o], p[o], v[o], buy[o]
        return t, p, v, buy

    def span(self, a: int, b: int):
        """Сделки с t в [a, b] (абс. мс; a не раньше 01.07, b не позже 13.11 23:59:59.999): (T, P, V, B, пропущенные сутки)."""
        a, b = max(a, FIRST_MS), min(b, LAST_MS - 1)
        ts, ps, vs, bs, miss = [], [], [], [], []
        for d in range(day_of(a), day_of(b) + 1):
            x = self.day(d)
            if x is None:
                miss.append(d)
                continue
            t, p, v, B = x
            i0, i1 = np.searchsorted(t, a, "left"), np.searchsorted(t, b, "right")
            ts.append(t[i0:i1]); ps.append(p[i0:i1]); vs.append(v[i0:i1]); bs.append(B[i0:i1])
        if not ts:
            return np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0), np.empty(0, bool), miss
        return np.concatenate(ts), np.concatenate(ps), np.concatenate(vs), np.concatenate(bs), miss


# ───────────────────────────── признаки сделки ─────────────────────────────

def _first(mask):
    i = int(np.argmax(mask)) if len(mask) else 0
    return i if len(mask) and mask[i] else -1


def _flow_window(T, V, B, lo, hi, s):
    """Знаковая доля покупок по обороту в [lo, hi) мс; NaN — нет оборота."""
    i0, i1 = np.searchsorted(T, lo, "left"), np.searchsorted(T, hi, "left")
    v = V[i0:i1]
    vt = float(v.sum())
    return s * (2.0 * float(v[B[i0:i1]].sum()) - vt) / vt if vt > 0 else float("nan")


def trade_row(tp: StoreTape, r) -> dict:
    """Одна сделка → словарь признаков. r — строка сделки (sym, sig_ms, entry_ms, exit_ms, entry_px_e8, side_sign, natr, kind)."""
    nan = float("nan")
    s, sig, e, x, p0, natr = int(r.s), int(r.sig_ms), int(r.entry_ms), int(r.exit_ms), int(r.entry_px_e8), float(r.natr)
    out = {}
    a = max(sig - LOOK_MS, FIRST_MS)
    b = min(max(x + 3_600_000, e + EXT_MS), LAST_MS - 1)
    T, P, V, B, miss = tp.span(a, b)
    out["miss_days"] = len(miss)
    # ---- ворота G-ref: путь в [вход, выход] — как `tk698_path` (b = min(exit, последняя минута окна))
    b_ref = min(x, LAST_MS - 1)
    j0, j1 = np.searchsorted(T, e, "left"), np.searchsorted(T, b_ref, "right")
    full_ref = not any(d for d in miss if day_of(e) <= d <= day_of(b_ref)) and b_ref >= e
    g = lib685.path_one(T[j0:j1], P[j0:j1], e, x, p0, s, float(r.fees), notional=NOTIONAL)
    for k in ("ticks", "mfe", "mae", "t_mfe", "t_mae", "green", "green_first", "exit_move", "giveback", "hold_min"):
        out["g_" + k] = g[k]
    out["g_tape_ok"] = bool(full_ref and g["ticks"] > 0)
    # ---- до сигнала
    pre_ok = sig - LOOK_MS >= FIRST_MS and not any(d for d in miss if day_of(sig - LOOK_MS) <= d <= day_of(sig))
    out["pre_ok"] = bool(pre_ok)
    if pre_ok and len(T):
        f = F594.flow_features(T, V, B, sig, s, None, False)
        for k in ("tfv10", "tfv60", "tfv300", "tfv1800", "tfn10", "tfn60", "tfn300", "tfn1800", "acc_v", "acc_n", "lmsz300", "msz_r"):
            out[k] = f[k]
        for k in PRE:
            i = int(np.searchsorted(T, sig - k * 1000, "right")) - 1
            out["pre_r%d" % k] = s * (p0 / float(P[i]) - 1.0) if i >= 0 and T[i] >= sig - LOOK_MS else nan
        out["pre_n60"] = int(np.searchsorted(T, sig, "left") - np.searchsorted(T, sig - 60_000, "left"))
    else:
        for k in ("tfv10", "tfv60", "tfv300", "tfv1800", "tfn10", "tfn60", "tfn300", "tfn1800", "acc_v", "acc_n", "lmsz300", "msz_r", "pre_n60"):
            out[k] = nan
        for k in PRE:
            out["pre_r%d" % k] = nan
    # ---- после входа: исход (горизонт 3 600 с; NaN, если горизонт не покрыт лентой / днями ≥ 14.11)
    h_ok = e + H_MS <= LAST_MS - 1 and not any(d for d in miss if day_of(e) <= d <= day_of(e + H_MS))
    out["h_ok"] = bool(h_ok)
    i0, i1 = np.searchsorted(T, e, "left"), np.searchsorted(T, e + EXT_MS, "right")
    ext_ok = e + EXT_MS <= LAST_MS - 1 and not any(d for d in miss if day_of(e) <= d <= day_of(e + EXT_MS))
    tt = T[i0:i1] - e
    f = s * (P[i0:i1] - p0) / float(p0)
    fz = np.concatenate([[0.0], f])
    cmax = np.maximum.accumulate(fz)
    cmin = np.maximum.accumulate(-fz)
    for tau in TAUS:
        k = int(np.searchsorted(tt, tau * 1000, "right"))
        if h_ok:
            out["r%d" % tau], out["mfe%d" % tau], out["mae%d" % tau] = float(fz[k]), float(cmax[k]), float(cmin[k])
        else:
            out["r%d" % tau] = out["mfe%d" % tau] = out["mae%d" % tau] = nan
    kh = int(np.searchsorted(tt, H_MS, "right"))
    if h_ok and kh > 0:
        im, ia = int(np.argmax(f[:kh])), int(np.argmax(-f[:kh]))
        out["t_mfeH"] = float(tt[im]) / 1000.0 if f[im] > 0 else 0.0
        out["t_maeH"] = float(tt[ia]) / 1000.0 if f[ia] < 0 else 0.0
    else:
        out["t_mfeH"] = out["t_maeH"] = 0.0 if h_ok else nan
    ok_n = h_ok and natr > 0
    fh, th = f[:kh], tt[:kh]
    for y in YS:
        i = _first(fh <= -y * natr) if ok_n else -1
        out["dn%g" % y] = float(th[i]) / 1000.0 if i >= 0 else nan
    for xb in XS:
        i = _first(fh >= xb * natr) if ok_n else -1
        out["up%g" % xb] = float(th[i]) / 1000.0 if i >= 0 else nan
        out["ret%g" % xb] = nan
        out["ret%g_c900" % xb] = out["ret%g_c3600" % xb] = nan
        if i >= 0:
            jr = _first(f[i + 1:kh] <= 0.0) if kh > i + 1 else -1
            if jr >= 0:
                tr = float(tt[i + 1 + jr])
                out["ret%g" % xb] = tr / 1000.0
                if ext_ok:
                    for c, key in ((900, "c900"), (3600, "c3600")):
                        kk = int(np.searchsorted(tt, tr + c * 1000, "right"))
                        out["ret%g_%s" % (xb, key)] = float(fz[kk])
    i = _first(fh <= -STOP_FRAC) if h_ok else -1
    out["stop4"] = float(th[i]) / 1000.0 if i >= 0 else nan
    for w in (10_000, 60_000):
        out["pflow%d" % (w // 1000)] = _flow_window(T, V, B, e, e + w, s) if h_ok else nan
    # ---- после выхода движка
    for c in POST_EXIT:
        if x + c * 1000 <= LAST_MS - 1 and not any(d for d in miss if day_of(x) <= d <= day_of(x + c * 1000)):
            i = int(np.searchsorted(T, x + c * 1000, "right")) - 1
            out["post%d" % c] = s * (float(P[i]) - p0) / float(p0) if i >= 0 else nan
        else:
            out["post%d" % c] = nan
    return out


def load_trades(line: str, win: str) -> pd.DataFrame:
    tr = pd.read_parquet(os.path.join(TR_ROOT, "trades", line + ".parquet"))
    tr = tr[tr["win"] == win].copy()
    pth = pd.read_parquet(os.path.join(TR_ROOT, "path", "%s-%s.parquet" % (line, win)))[["sym", "entry_ms", "natr"]]
    tr = tr.merge(pth, on=["sym", "entry_ms"], how="left", validate="one_to_one")
    tr["s"] = 1 if line.startswith("L_") else -1
    return tr.sort_values(["sym", "entry_ms"], kind="stable").reset_index(drop=True)


def run(line: str, win: str, out_dir: str, limit: int = 0):
    t0 = time.time()
    tr = load_trades(line, win)
    if limit:
        tr = tr.iloc[:limit]
    rows, cur, tp = [], None, None
    cnt = dict(n_store=0, n_zip=0, n_missing=0)
    for r in tr.itertuples():
        if r.sym != cur:
            if tp is not None:
                for k in cnt:
                    cnt[k] += getattr(tp, k)
            cur, tp = r.sym, StoreTape(win, r.sym)
        row = trade_row(tp, r)
        row.update(sym=r.sym, entry_ms=int(r.entry_ms))
        rows.append(row)
    for k in cnt:
        cnt[k] += getattr(tp, k) if tp is not None else 0
    df = pd.DataFrame(rows)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s-%s%s.parquet" % (line, win, "-sample" if limit else ""))
    df.to_parquet(path, index=False)
    print(line, win, "trades", len(df), "sec", round(time.time() - t0, 1), "store/zip/missing", cnt["n_store"], cnt["n_zip"], cnt["n_missing"], "g_tape_ok", round(float(df["g_tape_ok"].mean()), 4), flush=True)
    return path


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else os.path.join(OUT_ROOT, "tape"), int(sys.argv[4]) if len(sys.argv) > 4 else 0)
