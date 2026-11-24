#!/usr/bin/env python3
"""П-42 TK-697 (№ 2 и № 7), семейство `l697`: столбцы из дампов уровней движка (`P42_LEVELS=1`, как у `tk671_t668`: R), 12 штук. Новых проходов движка нет — читаются готовые дампы TK-671.

  compute(rows, win, side, procs=3, cache_dir=None, dumps=None, info=None) -> DataFrame (индекс row_id, float64, NaN = не определено, строки не выбрасываются)
  python3 tk697_lv.py --rows <rows.parquet> --win wa --side long --out <out.parquet> [--procs 2] [--prod /data/p42-tk671/prod] [--cache-dir <каталог .b42 окна>]

Уровни — те же, что у строки: дамп источника строки (`rows.src` → `<источник>/<side>-levels.csv`, 12 источников TK-671; номер уровня `rows.level` — индекс в дампе монеты), окно и сторона строки.
Дамп лонга — уровни сопротивления (пробой вверх), дамп шорта — поддержки (пробой вниз); состояние на минуте сигнала `t = sig_ms // 60000` (как `tk671_t668`: уровень живёт, если
рождение ≤ t < конец; цена — последняя версия с минутой ≤ t). Знак стороны `s` = +1 лонг, −1 шорт. NATR — NATR 60 на минуте b = t − 1 (доля цены, `tk697_cols.natr_kernel`).

  № 2 серия уровней за пробиваемым (6): «другие» уровни той же монеты и источника, живые на t, цена строго за уровнем строки (лонг — выше, шорт — ниже), расстояние до него
      d = |P − L| / L; ls_c{1,2,3}p — число таких уровней с d ≤ 1 / 2 / 3 %; ls_c{2,4,8}n — число с d / NATR60 ≤ 2 / 4 / 8. Нет значения NATR или уровень строки вне дампа — NaN; нет уровней — 0.
      Расстояние до ближайшего за уровнем — готовый столбец `R` (TK-671, семейство t668, в единицах NATR движка), здесь не дублируется.
  № 7 BTC / ETH у своего уровня (6 = 2 монеты × 3): ref ∈ {BTCUSDT, ETHUSDT}, p_ref = закрытие ref на минуте b, NATR ref — NATR 60 ref на b; уровни ref — из дампов ТОГО ЖЕ источника:
      bl_{btc,eth}_ahead_n = расстояние от p_ref до ближайшего уровня ref «по ходу» пробоя монеты (лонг — сопротивление выше p_ref из дампа лонга, шорт — поддержка ниже p_ref из дампа шорта)
      в NATR ref; bl_{…}_back_n — то же «против» (лонг — поддержка ниже p_ref из дампа шорта, шорт — сопротивление выше p_ref из дампа лонга); нет такого уровня — NaN;
      bl_{…}_brk60 = [ref за последние 60 закрытых минут прошёл по ходу пробоя монеты через свой уровень] − [… против хода]: уровень жил на минуте m0 = b − 60 и стоял за ценой ref
      (сопротивление выше закрытия m0 / поддержка ниже), цена уровня на m0 достигнута high / low ref в минутах [b − 59, b]; значения −1 / 0 / +1; нет данных ref на m0 или b — NaN.
Причинность: уровни и цены версий — состояние ≤ t, ряды ref — минуты ≤ b (сверка — `tk697_lv_gate.py`: G-brute, G-causal с мусором в уровнях и минутках ≥ t)."""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk668_feat as ft  # noqa: E402
from tk668_feat import njit  # noqa: E402
import tk671_t668 as t668  # noqa: E402
import tk697_cols as m6  # noqa: E402

REFS = ("BTCUSDT", "ETHUSDT")
PROD = "/data/p42-tk671/prod"
CACHE_ROOT = "/data/p42-tk621"
SIGN = {"long": 1.0, "short": -1.0}
P_THR = (0.01, 0.02, 0.03)
N_THR = (2.0, 4.0, 8.0)
BRK_MIN = 60
NAN = float("nan")
COLUMNS = ([f"ls_c{k}p" for k in (1, 2, 3)] + [f"ls_c{k}n" for k in (2, 4, 8)]
           + [f"bl_{r}_{x}" for r in ("btc", "eth") for x in ("ahead_n", "back_n", "brk60")])


# ---- дампы -----------------------------------------------------------------------------------------------------------------------------

def parse_levels_filtered(path, syms):
    """Как `ft.parse_levels`, но только монеты из `syms` (быстрее на больших дампах: чужие строки не разбираются)."""
    out = {}
    keep = tuple(s + "," for s in syms)
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            if not line.startswith(keep):
                continue
            sym, lev, born, end, _fb, segs, _rest = (line.rstrip("\n").split(",", 6) + [""])[:7]
            sl = out.setdefault(sym, ft.SymLevels())
            if int(lev) != len(sl.born):
                raise ValueError("%s: номер уровня %s ≠ %d" % (sym, lev, len(sl.born)))
            sl.add(int(born), int(end), [(int(x.split(":")[0]), int(x.split(":")[1])) for x in segs.split("|") if x])
    for sl in out.values():
        sl.freeze()
    return out


class Dumps:
    """Дампы окна: `get(side, src_name, full)` → {монета: SymLevels}; `full` — все монеты (своя сторона), иначе только BTC / ETH. Кэш по (сторона, источник)."""

    def __init__(self, dirs):
        self.dirs = dirs                               # {"long": <каталог dumps>, "short": …}
        self.cache = {}

    def get(self, side, src, full):
        key = (side, src, bool(full))
        if key not in self.cache:
            path = os.path.join(self.dirs[side], src, side + "-levels.csv")
            self.cache[key] = ft.parse_levels(path) if full else parse_levels_filtered(path, REFS)
        return self.cache[key]


def flat(sl):
    """`SymLevels | None` → (b, e, off, segm, segp)."""
    if sl is None:
        z = np.zeros(0, dtype=np.int64)
        return z, z, np.zeros(1, dtype=np.int64), z, z
    return t668.flatten_levels(sl)


# ---- ядра ------------------------------------------------------------------------------------------------------------------------------

@njit(cache=True)
def count_kernel(t, p, lev, sign, natr, b, e, off, segm, segp, out):
    """№ 2: числа живых на t других уровней монеты за уровнем строки (цена версии на t строго за `p`) в 1 / 2 / 3 % и 2 / 4 / 8 NATR от него; `lev` вне дампа или NATR ≤ 0 — NaN."""
    nl = len(b)
    for i in range(len(t)):
        for q in range(6):
            out[i, q] = np.nan
        ti, pi, li, sg, na = t[i], p[i], lev[i], sign[i], natr[i]
        if li < 0 or li >= nl or not (na > 0.0) or not (pi > 0.0):
            continue
        c0 = c1 = c2 = c3 = c4 = c5 = 0
        for j in range(nl):
            if j == li or not (b[j] <= ti and ti < e[j]):
                continue
            k = t668._px_idx(off, segm, j, ti)
            if k < 0:
                continue
            px = float(segp[k])
            if sg > 0:
                if not (px > pi):
                    continue
            elif not (px < pi):
                continue
            d = abs(px - pi) / pi
            dn = d / na
            if d <= 0.01:
                c0 += 1
            if d <= 0.02:
                c1 += 1
            if d <= 0.03:
                c2 += 1
            if dn <= 2.0:
                c3 += 1
            if dn <= 4.0:
                c4 += 1
            if dn <= 8.0:
                c5 += 1
        out[i, 0], out[i, 1], out[i, 2] = c0, c1, c2
        out[i, 3], out[i, 4], out[i, 5] = c3, c4, c5


@njit(cache=True)
def roll_ext_kernel(o, h, l, w, hmax, lmin):
    """hmax[m] / lmin[m] — максимум high / минимум low минуток с данными в [m − w + 1, m]; нет данных или m < w − 1 — NaN."""
    n = len(o)
    for m in range(n):
        hmax[m] = np.nan
        lmin[m] = np.nan
        if m < w - 1:
            continue
        mx = -np.inf
        mn = np.inf
        for q in range(m - w + 1, m + 1):
            if o[q] != 0:
                if h[q] > mx:
                    mx = h[q]
                if l[q] < mn:
                    mn = l[q]
        if np.isfinite(mx):
            hmax[m] = mx
            lmin[m] = mn


@njit(cache=True)
def ref_kernel(t, b, sign, cf, hmax, lmin, nat, rb, re, roff, rsm, rsp, sb, se, soff, ssm, ssp, out):
    """№ 7 для одной монеты-ориентира: out[i] = (ahead_n, back_n, brk60). R* — уровни сопротивления ref (дамп лонга), S* — поддержки (дамп шорта)."""
    n = len(cf)
    for i in range(len(t)):
        out[i, 0] = np.nan
        out[i, 1] = np.nan
        out[i, 2] = np.nan
        ti, bi, sg = t[i], b[i], sign[i]
        if bi < 0 or bi >= n:
            continue
        pr = cf[bi]
        na = nat[bi]
        if not (np.isfinite(pr) and pr > 0.0 and np.isfinite(na) and na > 0.0):
            continue
        res_up = np.inf      # ближайшее сопротивление выше pr
        sup_dn = -np.inf     # ближайшая поддержка ниже pr
        for j in range(len(rb)):
            if rb[j] <= ti and ti < re[j]:
                k = t668._px_idx(roff, rsm, j, ti)
                if k >= 0:
                    px = float(rsp[k])
                    if px > pr and px < res_up:
                        res_up = px
        for j in range(len(sb)):
            if sb[j] <= ti and ti < se[j]:
                k = t668._px_idx(soff, ssm, j, ti)
                if k >= 0:
                    px = float(ssp[k])
                    if px < pr and px > sup_dn:
                        sup_dn = px
        up_n = (res_up - pr) / pr / na if np.isfinite(res_up) else np.nan
        dn_n = (pr - sup_dn) / pr / na if np.isfinite(sup_dn) else np.nan
        if sg > 0:
            out[i, 0], out[i, 1] = up_n, dn_n
        else:
            out[i, 0], out[i, 1] = dn_n, up_n
        m0 = bi - BRK_MIN
        if m0 < 0 or not np.isfinite(cf[m0]) or not np.isfinite(hmax[bi]):
            continue
        c0 = cf[m0]
        crossed_up = False
        crossed_dn = False
        for j in range(len(rb)):
            if rb[j] <= m0 and m0 < re[j]:
                k = t668._px_idx(roff, rsm, j, m0)
                if k >= 0:
                    px = float(rsp[k])
                    if px > c0 and hmax[bi] >= px:
                        crossed_up = True
        for j in range(len(sb)):
            if sb[j] <= m0 and m0 < se[j]:
                k = t668._px_idx(soff, ssm, j, m0)
                if k >= 0:
                    px = float(ssp[k])
                    if px < c0 and lmin[bi] <= px:
                        crossed_dn = True
        if sg > 0:
            out[i, 2] = (1.0 if crossed_up else 0.0) - (1.0 if crossed_dn else 0.0)
        else:
            out[i, 2] = (1.0 if crossed_dn else 0.0) - (1.0 if crossed_up else 0.0)


def warm_up():
    z = np.zeros(1, dtype=np.int64)
    zf = np.zeros(1)
    zo = np.zeros((1, 6))
    off = np.zeros(2, dtype=np.int64)
    count_kernel(z, zf, z, zf, zf, z, z, off, z, z, zo)
    roll_ext_kernel(z, zf, zf, 1, zf, zf.copy())
    ref_kernel(z, z, zf, zf, zf, zf, zf, z, z, off, z, z, z, z, off, z, z, np.zeros((1, 3)))


# ---- сборка ----------------------------------------------------------------------------------------------------------------------------

def coin_natr(rows_b, sym, cache_dir):
    """NATR 60 на минутах `rows_b` монеты (NaN — нет кэша / нет значения)."""
    path = os.path.join(cache_dir, sym + ".b42")
    if not os.path.exists(path):
        return np.full(len(rows_b), np.nan)
    a = ft.load_b42_full(path)
    nat = m6.natr_kernel(a["o"], a["h"], a["l"], a["c"], m6.NATR_P)
    return np.where((rows_b >= 0) & (rows_b < len(nat)), nat[np.clip(rows_b, 0, len(nat) - 1)], np.nan)


def load_ref(cache_dir, sym):
    """(cf, hmax60, lmin60, natr60) монеты-ориентира или None."""
    path = os.path.join(cache_dir, sym + ".b42")
    if not os.path.exists(path):
        return None
    a = ft.load_b42_full(path)
    cf = ft.ffill_close(a["o"], a["c"])
    hmax, lmin = np.empty(len(cf)), np.empty(len(cf))
    roll_ext_kernel(a["o"], a["h"].astype(np.float64), a["l"].astype(np.float64), BRK_MIN, hmax, lmin)
    return cf, hmax, lmin, m6.natr_kernel(a["o"], a["h"], a["l"], a["c"], m6.NATR_P)


def compute(rows, win, side, procs=3, cache_dir=None, dumps=None, prod=PROD, info=None):
    """`rows` — row_id, src, sym, level, sig_ms, level_px_e8 (+ abs_ms, win, side — проверяются). `dumps` — `Dumps` (по умолчанию дампы TK-671 `<prod>/siglv-<win>-<side>/dumps`)."""
    import pandas as pd
    t_start = time.time()
    if side not in SIGN:
        raise ValueError("side %r" % side)
    for col in ("win", "side"):
        if col in rows.columns and not (rows[col] == (win if col == "win" else side)).all():
            raise ValueError("строки не одного (окна, стороны): %s" % col)
    if rows["row_id"].duplicated().any():
        raise ValueError("row_id не уникален")
    if "abs_ms" in rows.columns:
        import tk604_stats as st
        st.check_abs_ms(rows["abs_ms"].to_numpy(np.int64), "строка TK-671")
    cache_dir = cache_dir or os.path.join(CACHE_ROOT, win, "cache")
    if dumps is None:
        dumps = Dumps({sd: os.path.join(prod, "siglv-%s-%s" % (win, sd), "dumps") for sd in ("long", "short")})
    n = len(rows)
    s = SIGN[side]
    t = rows["sig_ms"].to_numpy(np.int64) // ft.MIN_MS
    b = t - 1
    src = rows["src"].to_numpy(np.int64)
    lev = rows["level"].to_numpy(np.int64)
    p = np.abs(rows["level_px_e8"].to_numpy(np.int64)).astype(np.float64)
    sign = np.full(n, s)
    names = t668._source_names()
    out = np.full((n, len(COLUMNS)), np.nan)
    stat = dict(win=win, side=side, rows=int(n), sources={}, missing_cache=[], missing_ref=[])
    warm_up()
    syms, sym_code = np.unique(rows["sym"].to_numpy(dtype=object).astype(str), return_inverse=True)
    natr = np.full(n, np.nan)
    for k, sym in enumerate(syms):
        idx = np.flatnonzero(sym_code == k)
        if not os.path.exists(os.path.join(cache_dir, sym + ".b42")):
            stat["missing_cache"].append(sym)
        natr[idx] = coin_natr(b[idx], sym, cache_dir)
    refs = {r: load_ref(cache_dir, r) for r in REFS}
    stat["missing_ref"] = [r for r, v in refs.items() if v is None]
    opp = "short" if side == "long" else "long"
    for si in np.unique(src):
        name = names[int(si)] if int(si) < len(names) else str(int(si))
        ix = np.flatnonzero(src == si)
        own_lv = dumps.get(side, name, True) if os.path.exists(os.path.join(dumps.dirs[side], name, side + "-levels.csv")) else None
        if own_lv is None:
            stat["sources"][name] = dict(rows=int(len(ix)), no_levels_file=True)
            continue
        opp_ok = os.path.exists(os.path.join(dumps.dirs[opp], name, opp + "-levels.csv"))
        opp_lv = dumps.get(opp, name, False) if opp_ok else {}
        res_lv, sup_lv = (own_lv, opp_lv) if side == "long" else (opp_lv, own_lv)       # сопротивления — дамп лонга, поддержки — дамп шорта
        # № 2: по монетам
        code = sym_code[ix]
        order = np.argsort(code, kind="stable")
        cuts = np.flatnonzero(np.diff(code[order])) + 1
        no_coin = 0
        for part in np.split(order, cuts):
            if not len(part):
                continue
            g = ix[part]
            sl = own_lv.get(syms[sym_code[g[0]]])
            if sl is None:
                no_coin += len(g)
                continue
            bb, ee, off, sm, sp = t668.flatten_levels(sl)
            blk = np.empty((len(g), 6))
            count_kernel(t[g], p[g], lev[g], sign[g], natr[g], bb, ee, off, sm, sp, blk)
            out[g, 0:6] = blk
        # № 7: монеты-ориентиры
        for ri, r in enumerate(REFS):
            if refs[r] is None:
                continue
            cf, hmax, lmin, nat = refs[r]
            rb, re_, roff, rsm, rsp = flat(res_lv.get(r))
            sb, se, soff, ssm, ssp = flat(sup_lv.get(r))
            blk = np.empty((len(ix), 3))
            ref_kernel(t[ix], b[ix], sign[ix], cf, hmax, lmin, nat, rb, re_, roff, rsm, rsp, sb, se, soff, ssm, ssp, blk)
            out[ix, 6 + 3 * ri:9 + 3 * ri] = blk
        stat["sources"][name] = dict(rows=int(len(ix)), no_coin=int(no_coin), opp_levels_file=bool(opp_ok))
    df = pd.DataFrame(out, index=pd.Index(rows["row_id"].to_numpy(np.int64), name="row_id"), columns=list(COLUMNS))
    stat["seconds"] = round(time.time() - t_start, 3)
    stat["ms_per_row"] = round(1000.0 * stat["seconds"] / max(n, 1), 4)
    stat["non_nan_share"] = {c: round(float(np.isfinite(df[c]).mean()), 6) if n else 0.0 for c in COLUMNS}
    if info is not None:
        info.update(stat)
    return df


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--prod", default=PROD)
    ap.add_argument("--cache-dir")
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows)
    info = {}
    df = compute(rows, a.win, a.side, a.procs, cache_dir=a.cache_dir, prod=a.prod, info=info)
    df.to_parquet(a.out)
    with open(a.out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: info[k] for k in ("rows", "seconds", "ms_per_row")}), flush=True)


if __name__ == "__main__":
    main()
