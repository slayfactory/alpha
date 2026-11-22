"""TK-681 (В-173 № 2.1, № 25): пороги фильтра живости на реализованной волатильности, равные по квантилю порогам `natr_min`, и квантили волатильности для сетки `ea`.

Читает только минутный кэш `.b42` (цены и оборот); сделки, сигналы и P&L не читает. Совокупность — минуты монет, где фильтр живости решает:
минута `t` зачётных суток окна (по умолчанию с 14-х суток сетки), у закрытой минутки `t − 1` есть NATR 30 и оборот часа `[t − 60, t)` не ниже `--t1h` млн $.
Для каждого порога `g` сетки `natr_min`: `q` = доля совокупности с NATR 30 < `g`; порог источника `nf` = квантиль `q` его ряда на тех же минутах.

  python3 tk681_volq.py --cache wa=/data/p42-tk621/wa/cache:14:69 --cache wb=…:32:69 --out volq681.json [--grid 0.2,0.4,…] [--t1h 0.5] [--coins N]

Ряды — порт `data.rs` (`natr_of`, `rv_of`) на float64 с отсечением дробной части: расхождение с целочисленным движком ≤ 1e-6 % цены, порог округляется до 0,01 %."""
import argparse
import glob
import json
import os
import struct

import numpy as np

SCALE = 100_000_000
START_MIN = 14 * 1440
VOL_PERIODS = (5, 15, 30, 60)
# коды `nf` / `ev` / `vs`: 1–4 NATR 5/15/30/60, 5–8 реализованная волатильность 5/15/30/60 минут
CODES = {1: ("natr", 5), 2: ("natr", 15), 3: ("natr", 30), 4: ("natr", 60), 5: ("rv", 5), 6: ("rv", 15), 7: ("rv", 30), 8: ("rv", 60)}
NAMES = {1: "NATR 5", 2: "NATR 15", 3: "NATR 30", 4: "NATR 60", 5: "RV 5", 6: "RV 15", 7: "RV 30", 8: "RV 60"}
GRID = (0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6)
# гистограмма: шаг 0,001 % цены (1000 e8), до 50 %
BIN_E8 = 1000
NBINS = 50_000


def read_b42(path):
    """`.b42` → (o, h, l, c, qv) int64."""
    with open(path, "rb") as f:
        buf = f.read()
    if buf[:4] != b"B42\x01":
        raise ValueError("%s: не .b42" % path)
    n = struct.unpack_from("<I", buf, 4)[0]
    a = np.frombuffer(buf, dtype="<i8", count=5 * n, offset=8).reshape(5, n)
    return a[0], a[1], a[2], a[3], a[4]


def natr_series(o, h, l, c, p):
    """`natr_of`: среднее истинных диапазонов `p` минут / закрытие, доля цены ×1e8; −1 — нет значения."""
    n = len(o)
    valid = o != 0
    prevc = np.zeros(n, dtype=np.int64)
    idx = np.where(valid, np.arange(n), -1)
    last = np.maximum.accumulate(idx)
    prev_idx = np.concatenate(([-1], last[:-1]))
    has_prev = prev_idx >= 0
    prevc[has_prev] = c[prev_idx[has_prev]]
    tr = (h - l).astype(np.int64)
    with_prev = np.maximum(tr, np.maximum(np.abs(h - prevc), np.abs(l - prevc)))
    tr = np.where(has_prev, with_prev, tr)
    tr = np.where(valid, tr, 0)
    trp = np.concatenate(([0], np.cumsum(tr)))
    cnt = np.concatenate(([0], np.cumsum(valid.astype(np.int64))))
    out = np.full(n, -1, dtype=np.int64)
    t = np.arange(p - 1, n)
    k = cnt[t + 1] - cnt[t + 1 - p]
    s = trp[t + 1] - trp[t + 1 - p]
    ok = valid[t] & (k * 2 >= p)
    tt = t[ok]
    out[tt] = np.minimum(np.floor(s[ok].astype(np.float64) * SCALE / (k[ok] * c[tt].astype(np.float64))), 2**31 - 1).astype(np.int64)
    return out


def rv_series(o, c, p):
    """`rv_of`: √Σ r² по `p` минутам, `r` — доля цены ×1e8 с отсечением; −1 — нет значения."""
    n = len(o)
    valid = o != 0
    both = np.zeros(n, dtype=bool)
    both[1:] = valid[1:] & valid[:-1]
    r = np.zeros(n)
    cp = c[:-1].astype(np.float64)
    num = (c[1:] - c[:-1]).astype(np.float64) * SCALE
    with np.errstate(divide="ignore", invalid="ignore"):
        r[1:] = np.where(both[1:], np.trunc(num / cp), 0.0)
    # r² — целые: накопление в int64 точное (переполнение — только при среднем |r| > 2,8 % за минуту на всём ряду)
    r2 = (r * r).astype(np.int64)
    sq = np.concatenate(([0], np.cumsum(r2)))
    cnt = np.concatenate(([0], np.cumsum(both.astype(np.int64))))
    out = np.full(n, -1, dtype=np.int64)
    t = np.arange(p, n)
    k = cnt[t + 1] - cnt[t + 1 - p]
    win = sq[t + 1] - sq[t + 1 - p]
    ok = valid[t] & (k * 2 >= p)
    out[t[ok]] = np.minimum(np.floor(np.sqrt(win[ok].astype(np.float64))), 2**31 - 1).astype(np.int64)
    return out


def series(code, o, h, l, c):
    kind, p = CODES[code]
    return natr_series(o, h, l, c, p) if kind == "natr" else rv_series(o, c, p)


def pool_mask(o, qv, natr30, t1h_musd, d0=14, d1=None):
    """Минуты `t − 1`, на которых фильтр живости решает сигнал минуты `t` зачётных суток `d0 … d1` сетки (включительно; без `d1` — до конца ряда):
    есть NATR 30, оборот часа `[t − 60, t)` ≥ порога."""
    n = len(o)
    pq = np.concatenate(([0], np.cumsum(qv)))
    m = np.zeros(n, dtype=bool)
    t = np.arange(max(d0 * 1440, 60), n if d1 is None else min(n, (d1 + 1) * 1440))
    hour = pq[t] - pq[t - 60]
    m[t - 1] = (natr30[t - 1] >= 0) & (hour >= int(round(t1h_musd * 1e6 * 1e5)))
    return m


def hist_add(hist, v):
    b = np.minimum(v // BIN_E8, NBINS - 1)
    hist += np.bincount(b, minlength=NBINS)


def share_below(hist, g_pct):
    """Доля значений < g % (граница — на краю корзины: сетка кратна шагу)."""
    return float(hist[: int(round(g_pct * 1e6)) // BIN_E8].sum()) / max(int(hist.sum()), 1)


def quantile(hist, q):
    """Наименьшее значение (%, верхний край корзины не включён), ниже которого лежит доля ≥ q."""
    tot = int(hist.sum())
    if tot == 0:
        return None
    cum = np.cumsum(hist)
    i = int(np.searchsorted(cum, q * tot, side="left"))
    return (i + 1) * BIN_E8 / 1e6 if q > 0 else 0.0


def run(caches, grid, t1h, coins=None):
    hists = {code: np.zeros(NBINS, dtype=np.int64) for code in CODES}
    miss = {code: 0 for code in CODES}
    n_min = 0
    n_coin = 0
    for name, spec in caches:
        parts = spec.rsplit(":", 2)
        ranged = len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit()
        d, d0, d1 = (parts[0], int(parts[1]), int(parts[2])) if ranged else (spec, 14, None)
        files = sorted(glob.glob(os.path.join(d, "*.b42")))
        if coins:
            files = files[:coins]
        for f in files:
            o, h, l, c, qv = read_b42(f)
            natr30 = natr_series(o, h, l, c, 30)
            m = pool_mask(o, qv, natr30, t1h, d0, d1)
            if not m.any():
                continue
            n_coin += 1
            n_min += int(m.sum())
            for code in CODES:
                s = natr30 if code == 3 else series(code, o, h, l, c)
                v = s[m]
                miss[code] += int((v < 0).sum())
                hist_add(hists[code], v[v >= 0])
    rows = []
    for g in grid:
        q = share_below(hists[3], g)
        rows.append({"natr_min": g, "share_below": round(q, 6), "match": {str(code): round(quantile(hists[code], q), 2) for code in CODES if code != 3}})
    qs = (0.05, 0.25, 0.5, 0.75, 0.95)
    dist = {str(code): {("q%02d" % round(x * 100)): round(quantile(hists[code], x), 3) for x in qs} for code in CODES}
    return {
        "population": "минуты t−1 перед минутой t зачётных суток: есть NATR 30, оборот часа ≥ %s млн $" % t1h,
        "caches": ["%s=%s" % c for c in caches],
        "coin_windows": n_coin,
        "minutes": n_min,
        "no_value": {str(code): miss[code] for code in CODES},
        "codes": {str(k): v for k, v in NAMES.items()},
        "grid": rows,
        "quantiles_pct": dist,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", action="append", required=True, help="имя=каталог с .b42[:первые:последние зачётные сутки сетки] (можно несколько)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--grid", default=",".join(str(x) for x in GRID))
    ap.add_argument("--t1h", type=float, default=0.5)
    ap.add_argument("--coins", type=int, default=None, help="первые N монет каталога (проба)")
    a = ap.parse_args()
    caches = [tuple(x.split("=", 1)) for x in a.cache]
    res = run(caches, [float(x) for x in a.grid.split(",")], a.t1h, a.coins)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({"minutes": res["minutes"], "coin_windows": res["coin_windows"], "grid": res["grid"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
