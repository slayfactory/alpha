#!/usr/bin/env python3
"""TK-698: ряды рынка 01.07–13.11 из минуток `.b42` (кэши окон wa / wb / wc, `/data/p42-tk621/<окно>/cache/<СИМВОЛ>.b42`) — BTC, ETH и равновзвешенный индекс c539.
Выход `<out>/mkt.npz`: суточные ходы (136 сут., UTC) `d_btc`, `d_eth`, `d_idx` (+ `d_n` — монет в индексе за сутки) и минутные ходы за h = 5 / 15 / 60 мин по закрытой минуте `b` (r_h[b] = cf[b] / cf[b − h] − 1, как `tk671_mkt`; индекс по минуте — `b − FIRST_MIN`).
Суточный ход монеты = close последней минуты суток / open первой минуты суток − 1 (open первой минуты, а не close минуты до 00:00 — чтобы не читать 30.06, В-170; отличие — одна минута). Индекс суток — среднее по монетам c539, у которых есть данные в обе точки (без весов; не цепочка).
Индекс по минуте — среднее по монетам с определёнными закрытиями в обе точки. Минуты до 01.07 не читаются: у ходов в первые h минут 01.07 — NaN.
Запуск на srv: python3 tk698_mkt.py <каталог выхода>"""
import datetime as dt
import os
import sys

import numpy as np

import tk698_lib as L

CACHE = "/data/p42-tk621/%s/cache/%s.b42"
UNI_FILE = "/data/p42-tk621/universe/symbols-crypto.txt"
HS = (5, 15, 60)
NMIN = L.NDAYS * 1440
FIRST_MIN = L.FIRST_MS // 60000


def load_oc(path):
    with open(path, "rb") as f:
        head = f.read(8)
    assert head[:4] == b"B42\x01", path
    n = int.from_bytes(head[4:8], "little")
    a = np.memmap(path, dtype="<i8", mode="r", offset=8, shape=(5, n))
    return a[0], a[3], n


def ffill(o, c):
    cf = np.where(o != 0, c.astype(np.float64), np.nan)
    ok = ~np.isnan(cf)
    idx = np.where(ok, np.arange(len(cf)), 0)
    np.maximum.accumulate(idx, out=idx)
    out = cf[idx]
    out[: np.argmax(ok)] = np.nan if ok.any() else np.nan
    return out


def main(out):
    syms = [s for s in open(UNI_FILE, encoding="utf-8").read().replace(",", "\n").split() if s]
    syms = sorted(set(syms))
    assert "BTCUSDT" in syms and "ETHUSDT" in syms and len(syms) == 539, len(syms)
    d_btc = np.full(L.NDAYS, np.nan); d_eth = np.full(L.NDAYS, np.nan); d_idx = np.full(L.NDAYS, np.nan); d_n = np.zeros(L.NDAYS, dtype=np.int64)
    r = {k: np.full(NMIN, np.nan, dtype=np.float32) for k in ["btc%d" % h for h in HS] + ["eth%d" % h for h in HS] + ["idx%d" % h for h in HS]}
    for w, (t0s, lo_s, hi_s) in L.WINDOWS.items():
        t0 = dt.date.fromisoformat(t0s)
        lo, hi = (dt.date.fromisoformat(lo_s) - L.D0).days, (dt.date.fromisoformat(hi_s) - L.D0).days
        off = (L.D0 - t0).days * 1440                      # минута 01.07 00:00 в кэше окна (вычитание — только до D0, читаются дни ≥ lo)
        m0 = (L.D0 + dt.timedelta(days=lo) - t0).days * 1440    # первая зачётная минута в кэше окна
        m1 = (L.D0 + dt.timedelta(days=hi + 1) - t0).days * 1440
        lbs = max(0, m0 - 60) if w != "wa" else m0         # у wa минуты до 01.07 не читаем
        gi0 = lo * 1440                                    # глобальная минута (от 01.07) первой зачётной минуты
        sums = {h: np.zeros(m1 - m0) for h in HS}
        cnts = {h: np.zeros(m1 - m0, dtype=np.int64) for h in HS}
        dsum = np.zeros(hi - lo + 1); dcnt = np.zeros(hi - lo + 1, dtype=np.int64)
        for k, sym in enumerate(syms):
            p = CACHE % (w, sym)
            if not os.path.exists(p):
                continue
            o, c, n = load_oc(p)
            assert n >= m1, (w, sym, n, m1)
            oo = np.array(o[lbs:m1]); cc = np.array(c[lbs:m1])
            cf = ffill(oo, cc)                             # индекс i ↔ минута lbs + i
            base = m0 - lbs
            # суточный ход: open первой минуты суток / close последней
            for dd in range(hi - lo + 1):
                a, b = base + dd * 1440, base + dd * 1440 + 1439
                o0, c1 = oo[a], cc[b]
                if o0 > 0 and c1 > 0:
                    x = float(c1) / float(o0) - 1.0
                    dsum[dd] += x; dcnt[dd] += 1
                    if sym == "BTCUSDT":
                        d_btc[lo + dd] = x
                    elif sym == "ETHUSDT":
                        d_eth[lo + dd] = x
            for h in HS:
                x = np.full(m1 - m0, np.nan)
                i = np.arange(base, base + (m1 - m0))
                ok = i - h >= 0
                with np.errstate(invalid="ignore", divide="ignore"):
                    x[ok] = cf[i[ok]] / cf[i[ok] - h] - 1.0
                x[~np.isfinite(x)] = np.nan
                v = ~np.isnan(x)
                sums[h][v] += x[v]; cnts[h][v] += 1
                if sym == "BTCUSDT":
                    r["btc%d" % h][gi0:gi0 + (m1 - m0)] = x
                elif sym == "ETHUSDT":
                    r["eth%d" % h][gi0:gi0 + (m1 - m0)] = x
        for dd in range(hi - lo + 1):
            if dcnt[dd]:
                d_idx[lo + dd] = dsum[dd] / dcnt[dd]
            d_n[lo + dd] = dcnt[dd]
        for h in HS:
            with np.errstate(invalid="ignore", divide="ignore"):
                r["idx%d" % h][gi0:gi0 + (m1 - m0)] = np.where(cnts[h] > 0, sums[h] / np.maximum(cnts[h], 1), np.nan)
        print(w, "готово", file=sys.stderr, flush=True)
    # первые h минут 01.07 не читались (у wa до 01.07 нет данных) — NaN уже стоит: lbs = m0 → cf до base нет
    os.makedirs(out, exist_ok=True)
    np.savez_compressed(os.path.join(out, "mkt.npz"), d_btc=d_btc, d_eth=d_eth, d_idx=d_idx, d_n=d_n, **r)
    print("дни без BTC / ETH / индекса:", int(np.isnan(d_btc).sum()), int(np.isnan(d_eth).sum()), int(np.isnan(d_idx).sum()), "монет в индексе за сутки: мин", int(d_n.min()), "макс", int(d_n.max()))
    print("BTC за окно, Σ суточных ходов %:", round(float(np.nansum(d_btc)) * 100, 2), "| дней роста / падения:", int((d_btc > 0).sum()), int((d_btc < 0).sum()))


if __name__ == "__main__":
    main(sys.argv[1])
