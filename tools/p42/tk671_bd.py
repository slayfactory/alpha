#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), семейство `bd`: глубина книги Binance USD-M `bookDepth` (TK-595 / TK-648) на строках `rows.parquet` (все кандидаты, 12 источников).

  python3 tk671_bd.py --rows <rows.parquet> --win wa --side long --out <out.parquet> [--procs 3] [--zip /data/p42-book/zip] [--cache <каталог .b42>]
Модуль: `compute(rows, win, side, procs=3) -> DataFrame` (индекс `row_id`, float64; NaN — не определено; строки не выбрасываются). Пишет `<out>.info.json`.

Столбцы (8) — как у `tk648_feat.py` (определения TK-595 `bookdepth_feat.features_at` без изменений, формулы импортируются оттуда: BANDS / BI / LAG_S / MAX_AGE_S / FEATS):
  bd_imb02 bd_imb1 bd_imb2 bd_thin5 bd_thin30 bd_depth_turn   — `bookdepth_feat.FEATS`
  bd_age_s   возраст использованного снимка на сигнале, с (в протоколе §13.71 — `bd_age`; имя столбца — `bd_age_s`, как в TK-648)
  bd_mode    режим меток снимков (A = 1: метка на 2…6 с после узла 30-секундной сетки, B = 0: на узле) — РЕШЕНИЕ КООРДИНАТОРА 21.11: по снимкам строго до сигнала (последние `MODE_N` = 20 снимков той же суток,
             индекс ≤ индекса использованного снимка, т. е. ts + 30 с ≤ σ), а не метка суток по всем снимкам (как в TK-648, заглядывала в будущее суток; у 5,5 % строк значение другое)
Причинность (без изменений): снимок с `ts + 30 с ≤ σ` (последний такой), возраст `σ − ts ≤ 600 с`, иначе NaN. σ = `t0_ms/1000 + sig_ms/1000` (float, как `tk648_feat.abs_s`: при совпадении σ − 30 с с целой секундой снимка
сложение двух float может дать σ на ulp меньше → снимок отбрасывается, то есть строже, не мягче точной арифметики).
Охват: архив есть только для суток 14.09–13.11 без 08.10 (`tk648_feat.in_omega`, по суткам СИГНАЛА); остальные строки — NaN без чтения файлов (≈ 41 % строк окон). Сутки ≥ 14.11 не читаются (охрана `check_day`).
`turn1h` (знаменатель bd_depth_turn — оборот последних 60 закрытых минут, USDT = Σ qv[t−60:t] / 1e5, t = sig_ms // 60000): берётся из столбца `turn1h` в `rows`, если он есть (выход f12 `alpha-feat`);
иначе считается из минутного кэша окна `<cache>/<sym>.b42` — то же определение, что у Rust `f12::context_features` и Python `sigfeat_lib.context_features` (таблицы TK-634, откуда брал `turn1h` TK-648).
Строка не зависит от других строк: `bd_thin5` / `bd_thin30` берут снимок «10 / 60 снимков назад» по индексу (окно по числу снимков, TK-595), но только из суток строки (σ и σ − 1 ч, как грузил бы её ОДНУ оригинал).
Оригинал `tk648_feat.one_sym` грузит объединение суток всех строк монеты, и при пропуске суток между ними индекс «60 назад» мог попасть на снимок из не прилегающих суток (НЕ вперёд, но не за 30 минут). Здесь такая строка — NaN;
число таких строк — в отчёте ворот (`tk671_gate_bd.py eq`). Скорость: суточный архив читается pyarrow-csv + numpy-сводкой (вместо pandas read_csv + pivot_table, результат тот же; замер — в отчёте), признаки — векторно по монете."""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bookdepth_feat as bf  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk648_feat as f48  # noqa: E402

ZIP = f48.ZIP
CACHE_ROOT = "/data/p42-tk621"
FEATS = tuple(bf.FEATS)
COLS = list(FEATS) + ["bd_age_s", "bd_mode"]
MODE_N = 20                                     # bd_mode: доля меток «на 2…6 с после узла сетки» среди последних 20 снимков суток до сигнала (≈ 10 мин)
_EPOCH = dt.date(1970, 1, 1)
OMEGA_LO_D = (f48.OMEGA_LO - _EPOCH).days
OMEGA_HI_D = (f48.OMEGA_HI - _EPOCH).days
HOLES_D = tuple((h - _EPOCH).days for h in f48.HOLES)
LAST_OK_D = (f48.LAST_OK - _EPOCH).days
BS = np.array(sorted(bf.BANDS))
BS_ORDER = [list(BS).index(b) for b in bf.BANDS]       # столбцы сводки в порядке `bf.BANDS`
BI = bf.BI


def day_str(d):
    return (_EPOCH + dt.timedelta(int(d))).isoformat()


# ---- чтение суток архива -------------------------------------------------------------------------------------------------------------

def load_day_fast(zdir, sym, day):
    """Как `bookdepth_feat.load_day` (ts — секунды UTC по возрастанию, матрица снимков × 12 полос в порядке `bf.BANDS`; дубль (ts, полоса) — последний, NaN пропускаются), но без pandas."""
    p = os.path.join(zdir, sym, "%s-bookDepth-%s.zip" % (sym, day))
    if not os.path.exists(p) or os.path.getsize(p) == 0:
        return None
    with zipfile.ZipFile(p) as z:
        raw = z.read(z.namelist()[0])
    t = pacsv.read_csv(io.BytesIO(raw), convert_options=pacsv.ConvertOptions(
        include_columns=["timestamp", "percentage", "notional"],
        column_types={"timestamp": pa.timestamp("s"), "percentage": pa.float64(), "notional": pa.float64()}))
    ts = t["timestamp"].cast(pa.int64()).to_numpy()
    pc = t["percentage"].to_numpy(zero_copy_only=False)
    nv = t["notional"].to_numpy(zero_copy_only=False)
    bi = np.minimum(np.searchsorted(BS, pc), len(BS) - 1)
    ok = (BS[bi] == pc) & ~np.isnan(nv)
    ts, bi, nv = ts[ok], bi[ok], nv[ok]
    u, inv = np.unique(ts, return_inverse=True)
    key = inv * len(BS) + bi
    _, first_rev = np.unique(key[::-1], return_index=True)          # последнее вхождение ключа
    last = len(key) - 1 - first_rev
    m = np.full((len(u), len(BS)), np.nan)
    m.ravel()[key[last]] = nv[last]
    return u.astype(np.float64), m[:, BS_ORDER]


def load_sym_fast(zdir, sym, days):
    parts = [load_day_fast(zdir, sym, d) for d in days]
    parts = [p for p in parts if p is not None]
    if not parts:
        return None
    t = np.concatenate([p[0] for p in parts])
    m = np.vstack([p[1] for p in parts])
    o = np.argsort(t, kind="stable")
    return t[o], m[o]


# ---- оборот последнего часа из минутного кэша ----------------------------------------------------------------------------------------

def turn1h_from_qv(qv, t):
    """Σ qv[t−60:t] / 1e5 по минутам `t` (int64-массив); вне 60 ≤ t ≤ len(qv) — NaN. Сумма int64 с переполнением по модулю 2^64 — как `wrapping_add` Rust f12."""
    qv = np.asarray(qv, dtype=np.int64)
    cs = np.concatenate([np.zeros(1, dtype=np.int64), np.cumsum(qv, dtype=np.int64)])
    t = np.asarray(t, dtype=np.int64)
    ok = (t >= 60) & (t <= len(qv))
    tc = np.clip(t, 60, len(qv))
    out = (cs[tc] - cs[tc - 60]).astype(np.float64) / 1e5
    return np.where(ok, out, np.nan)


def read_qv(path):
    """Столбец qv (5-й) кэша `.b42`: `B42\\x01`, n (u32), затем 5 столбцов int64 по n."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] != b"B42\x01":
        raise ValueError("%s: не .b42" % path)
    n = int.from_bytes(head[4:8], "little")
    return np.fromfile(path, dtype="<i8", offset=8 + 4 * n * 8, count=n)


# ---- признаки по монете (векторно) ----------------------------------------------------------------------------------------------------

def features_vec(t, m, s, long_side, turn):
    """`bookdepth_feat.features_at` + `tk648_feat.one_sym` для массива сигналов монеты. t, m — снимки; s — σ (с, float), long_side — bool-массив, turn — оборот 1 ч (NaN допустим)."""
    n = len(s)
    out = {k: np.full(n, np.nan) for k in COLS}
    i = np.searchsorted(t, s - bf.LAG_S, side="right") - 1
    ic = np.maximum(i, 0)
    age = s - t[ic]
    ok = (i >= 0) & (age <= bf.MAX_AGE_S)
    if not ok.any():
        return out
    ci = {}                                                                      # индексы столбцов: сторона уровня / противоположная
    for b in (0.2, 1.0, 2.0):
        ci[b] = (np.where(long_side, BI[b], BI[-b]), np.where(long_side, BI[-b], BI[b]))
    for name, b in (("bd_imb02", 0.2), ("bd_imb1", 1.0), ("bd_imb2", 2.0)):
        a, p = m[ic, ci[b][0]], m[ic, ci[b][1]]
        v = ok & np.isfinite(a) & np.isfinite(p) & (a + p > 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[name] = np.where(v, (p - a) / (p + a), np.nan)
    a1 = m[ic, ci[1.0][0]]
    dsnap = np.floor(t / 86400.0).astype(np.int64)
    d_sig, d_hour = np.floor(s / 86400.0).astype(np.int64), np.floor((s - 3600.0) / 86400.0).astype(np.int64)
    for name, lag in (("bd_thin5", 10), ("bd_thin30", 60)):
        j = i - lag
        jc = np.maximum(j, 0)
        b1 = m[jc, ci[1.0][0]]
        # снимок «lag снимков назад» берётся только из суток строки ({сутки σ, сутки σ − 1 ч}, как грузил бы её одну `tk648_feat.one_sym`): значение не зависит от того, какие
        # ещё сутки монеты загружены для других строк (в TK-648 объединение суток по строкам монеты могло увести индекс на не прилегающие сутки — см. docstring модуля)
        v = ok & (j >= 0) & ((dsnap[jc] == d_sig) | (dsnap[jc] == d_hour)) & np.isfinite(a1) & np.isfinite(b1) & (a1 > 0) & (b1 > 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[name] = np.where(v, np.log(a1 / b1), np.nan)
    v = ok & np.isfinite(a1) & (a1 > 0) & (turn > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["bd_depth_turn"] = np.where(v, a1 / turn, np.nan)
    out["bd_age_s"] = np.where(ok, age, np.nan)
    # bd_mode (решение координатора 21.11): по снимкам строго до сигнала — последние до MODE_N снимков суток использованного снимка с индексом ≤ i (все с ts + 30 с ≤ σ), не метка суток по всем снимкам
    rr = (np.mod(np.round(t).astype(np.int64), 30) >= 2).astype(np.int64)
    cs = np.concatenate([[0], np.cumsum(rr)])
    day_first = np.searchsorted(dsnap, dsnap[ic], side="left")
    lo = np.maximum(np.maximum(i - (MODE_N - 1), day_first), 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        frac = (cs[np.maximum(i, 0) + 1] - cs[lo]) / np.maximum(i - lo + 1, 1)
    out["bd_mode"] = np.where(ok, (frac > 0.5).astype(np.float64), np.nan)
    return out


def _coin_job(job):
    sym, idx, s, long_side, turn, tmin, zdir, qpath = job
    if turn is None:
        turn = np.full(len(s), np.nan)
        if qpath is not None and os.path.exists(qpath):
            turn = turn1h_from_qv(read_qv(qpath), tmin)
    days = set()
    for x in (s, s - 3600.0):
        days.update(np.unique(np.floor(x / 86400.0).astype(np.int64)).tolist())
    if max(days) > LAST_OK_D:
        raise SystemExit("охрана: сутки %s позже 13.11 — не читаются (проверка вперёд)" % day_str(max(days)))
    tm = load_sym_fast(zdir, sym, [day_str(d) for d in sorted(days)])
    if tm is None:
        return idx, {k: np.full(len(s), np.nan) for k in COLS}, turn, False
    return idx, features_vec(tm[0], tm[1], s, long_side, turn), turn, True


def row_seconds(rows, win):
    """σ (с) как у `tk648_feat.abs_s`: `t0_ms / 1000 + sig_ms / 1000`; без `sig_ms` в `rows` — `abs_ms / 1000`."""
    if "sig_ms" in rows.columns:
        w = {x.name: x for x in st.TRADE_WINDOWS}[win]
        return w.t0_ms / 1000.0 + rows["sig_ms"].to_numpy(dtype=np.int64) / 1000.0
    return rows["abs_ms"].to_numpy(dtype=np.int64) / 1000.0


def omega_mask(s):
    d = np.floor(s / 86400.0).astype(np.int64)
    ok = (d >= OMEGA_LO_D) & (d <= OMEGA_HI_D)
    for h in HOLES_D:
        ok &= d != h
    return ok, d


def compute(rows, win, side, procs=3, zdir=ZIP, cache_dir=None, stats=None):
    import pandas as pd
    for col, want in (("win", win), ("side", side)):
        if col in rows.columns and not (rows[col] == want).all():
            raise ValueError("rows.%s ≠ %s" % (col, want))
    if "row_id" not in rows.columns or not rows["row_id"].is_unique:
        raise ValueError("rows.row_id отсутствует или не уникален")
    n = len(rows)
    vals = {k: np.full(n, np.nan) for k in COLS}
    s = row_seconds(rows, win)
    if n and float(s.max()) >= 1794614400.0:                                     # 14.11.2026 00:00 UTC
        raise SystemExit("охрана: строка позже 13.11 — не читается (проверка вперёд)")
    inw, _ = omega_mask(s)
    cache_dir = cache_dir or os.path.join(CACHE_ROOT, win, "cache")
    have_turn = "turn1h" in rows.columns
    turn_all = rows["turn1h"].to_numpy(dtype=np.float64) if have_turn else None
    sig_ms = rows["sig_ms"].to_numpy(dtype=np.int64) if "sig_ms" in rows.columns else rows["abs_ms"].to_numpy(dtype=np.int64) - {x.name: x for x in st.TRADE_WINDOWS}[win].t0_ms
    tmin = sig_ms // 60_000                                                      # минута сигнала от T0 окна (индекс в `.b42`)
    long_side = np.full(n, side == "long")
    sym = rows["sym"].to_numpy()
    sel = np.flatnonzero(inw)
    order = sel[np.argsort(sym[sel], kind="stable")]
    jobs = []
    if len(order):
        sy = sym[order]
        cut = np.flatnonzero(sy[1:] != sy[:-1]) + 1
        for g in np.split(order, cut):
            jobs.append((str(sym[g[0]]), g, s[g], long_side[g], None if turn_all is None else turn_all[g], tmin[g], zdir, os.path.join(cache_dir, "%s.b42" % sym[g[0]])))
    jobs.sort(key=lambda j: -len(j[1]))
    st_ = dict(rows=n, rows_omega=int(inw.sum()), coins=len(jobs), coins_with_archive=0, rows_coin_has_archive=0)
    turn_out = np.full(n, np.nan)
    if procs > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(procs) as ex:
            results = ex.map(_coin_job, jobs, chunksize=1)
            results = list(results)
    else:
        results = [_coin_job(j) for j in jobs]
    for idx, f, turn, has in results:
        for k in COLS:
            vals[k][idx] = f[k]
        turn_out[idx] = turn
        st_["coins_with_archive"] += has
        st_["rows_coin_has_archive"] += len(idx) * has
    if stats is not None:
        stats.update(st_)
        stats["turn1h_source"] = "rows.turn1h" if have_turn else "b42 (%s)" % cache_dir
        stats["turn1h_nan_in_omega"] = int(np.isnan(turn_out[inw]).sum())
    return pd.DataFrame(vals, index=pd.Index(rows["row_id"].to_numpy(), name="row_id"))[COLS]


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--zip", default=ZIP)
    ap.add_argument("--cache", default=None)
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows)
    t = time.time()
    stats = {}
    df = compute(rows, a.win, a.side, a.procs, a.zip, a.cache, stats)
    sec = time.time() - t
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    df.to_parquet(a.out)
    info = dict(seconds=round(sec, 3), ms_per_row=round(1000.0 * sec / max(len(df), 1), 5), procs=a.procs,
                nonnan={c: round(float(df[c].notna().mean()), 6) for c in df.columns}, **stats)
    with open(a.out + ".info.json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=1)
    print(json.dumps({k: info[k] for k in ("rows", "seconds", "ms_per_row", "rows_omega", "coins")}))


if __name__ == "__main__":
    main()
