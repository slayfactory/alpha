#!/usr/bin/env python3
"""П-42 TK-697 (№ 2 и № 7): ворота семейства `l697` (`tk697_lv`) на srv, без P&L.
  python3 tk697_lv_gate.py --rows <rows.parquet> --win wa --side long --out <gate.json> [--k-brute 1200] [--k-causal 200] [--prod /data/p42-tk671/prod]

G-brute   `compute` (боевой путь: ядра numba, плоские массивы уровней) = независимый построчный счёт на чистом Python (`brute_row`: `SymLevels.alive / px_at`, свои циклы) на k строках
          реального файла (случайные + самые «молодые» по листингу): одинаковый рисунок NaN, относительная ошибка ≤ 1e-9.
G-causal  k строк: уровни монеты, BTC и ETH с минуты t и дальше заменены мусором (`tk671_t668.garbage_levels`: рождение, конец, версии цены позже t), минутки монеты, BTC, ETH с минуты t —
          мусором (`tk668_feat.garbage_from`) — 12 значений строки не меняются; контроль силы — мусор с t − 1 меняет часть строк."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk668_feat as ft  # noqa: E402
import tk671_t668 as t668  # noqa: E402
import tk697_cols as m6  # noqa: E402
import tk697_gate as g6  # noqa: E402
import tk697_lv as fam  # noqa: E402

SEED = 699
NAN = float("nan")


def brute_row(s, t, p, lev, natr_b, sl, ref):
    """12 значений строки (порядок `fam.COLUMNS`). `sl` — SymLevels монеты (None — нет), `ref` — {имя: dict(cf=список закрытий | None, hmax=f(b), lmin=f(b), nat=f(b), res=SymLevels | None, sup=SymLevels | None)}.
    NATR60 монеты на b — `natr_b` (NaN — нет)."""
    b = t - 1
    out = [NAN] * 6
    if sl is not None and 0 <= lev < len(sl.born) and natr_b == natr_b and natr_b > 0 and p > 0:
        cnt = [0] * 6
        for j in range(len(sl.born)):
            if j == lev or not sl.alive(j, t):
                continue
            px = sl.px_at(j, t)
            if px is None:
                continue
            if (s > 0 and not px > p) or (s < 0 and not px < p):
                continue
            d = abs(px - p) / p
            dn = d / natr_b
            for q, thr in enumerate((0.01, 0.02, 0.03)):
                cnt[q] += d <= thr
            for q, thr in enumerate((2.0, 4.0, 8.0)):
                cnt[3 + q] += dn <= thr
        out = [float(x) for x in cnt]
    for r in fam.REFS:
        d = ref[r]
        cf, res, sup = d["cf"], d["res"], d["sup"]
        row = [NAN, NAN, NAN]
        if cf is not None and 0 <= b < len(cf):
            pr, na = cf[b], d["nat"](b)
            if pr == pr and pr > 0 and na == na and na > 0:
                ups = [res.px_at(j, t) for j in range(len(res.born)) if res.alive(j, t)] if res is not None else []
                dns = [sup.px_at(j, t) for j in range(len(sup.born)) if sup.alive(j, t)] if sup is not None else []
                ups = [x for x in ups if x is not None and x > pr]
                dns = [x for x in dns if x is not None and x < pr]
                up_n = (min(ups) - pr) / pr / na if ups else NAN
                dn_n = (pr - max(dns)) / pr / na if dns else NAN
                row[0], row[1] = (up_n, dn_n) if s > 0 else (dn_n, up_n)
                m0 = b - fam.BRK_MIN
                hb = d["hmax"](b)
                if m0 >= 0 and cf[m0] == cf[m0] and hb == hb:
                    c0 = cf[m0]
                    lb = d["lmin"](b)
                    up = dn = False
                    if res is not None:
                        for j in range(len(res.born)):
                            if res.b[j] <= m0 < res.e[j]:
                                px = res.px_at(j, m0)
                                if px is not None and px > c0 and hb >= px:
                                    up = True
                    if sup is not None:
                        for j in range(len(sup.born)):
                            if sup.b[j] <= m0 < sup.e[j]:
                                px = sup.px_at(j, m0)
                                if px is not None and px < c0 and lb <= px:
                                    dn = True
                    row[2] = (float(up) - float(dn)) if s > 0 else (float(dn) - float(up))
        out += row
    assert len(out) == len(fam.COLUMNS)
    return out


class GDumps(fam.Dumps):
    """Дампы с мусором в уровнях монеты и ориентиров со штампом позже `t_g` (G-causal)."""

    def __init__(self, base, sym, t_g, rng):
        super().__init__(base.dirs)
        self.base, self.sym, self.t_g, self.rng = base, sym, t_g, rng

    def get(self, side, src, full):
        d = dict(self.base.get(side, src, full))
        for k in {self.sym, *fam.REFS}:
            if k in d:
                d[k] = t668.garbage_levels(d[k], self.t_g, self.rng, n_junk=3)
        return d


class RefSeries:
    """Ряды ориентира для brute: закрытия списком, NATR60 / скользящие max high, min low за 60 минут — лениво и по определению (свои циклы)."""

    def __init__(self, cache_dir, sym, store):
        self.a = g6.load_arr(cache_dir, sym, store)
        self.cf = None if self.a is None else g6.lists_of(self.a)[4]
        self._n, self._h, self._l = {}, {}, {}

    def _lists(self):
        o, h, l, c, _ = g6.lists_of(self.a)
        return o, h, l, c

    def nat(self, b):
        if b not in self._n:
            o, h, l, c = self._lists()
            self._n[b] = g6.natr_at(o, h, l, c, b)
        return self._n[b]

    def _win(self, b):
        if b not in self._h:
            o, h, l, _ = self._lists()
            xs = [(h[q], l[q]) for q in range(b - fam.BRK_MIN + 1, b + 1) if o[q] != 0] if b >= fam.BRK_MIN - 1 else []
            self._h[b] = max(x for x, _ in xs) if xs else NAN
            self._l[b] = min(y for _, y in xs) if xs else NAN
        return self._h[b], self._l[b]

    def hmax(self, b):
        return self._win(b)[0]

    def lmin(self, b):
        return self._win(b)[1]


def refs_for_brute(series, dumps, side, src_name):
    opp = "short" if side == "long" else "long"
    own = dumps.get(side, src_name, False)
    oth = dumps.get(opp, src_name, False)
    res, sup = (own, oth) if side == "long" else (oth, own)
    return {r: dict(cf=series[r].cf, hmax=series[r].hmax, lmin=series[r].lmin, nat=series[r].nat, res=res.get(r), sup=sup.get(r)) for r in fam.REFS}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--k-brute", type=int, default=600)
    ap.add_argument("--k-causal", type=int, default=150)
    ap.add_argument("--prod", default=fam.PROD)
    ap.add_argument("--cache-dir")
    ap.add_argument("--tmp", default="/data/p42-tk697/tmp")
    a = ap.parse_args()
    os.makedirs(a.tmp, exist_ok=True)
    rng = np.random.default_rng(SEED)
    cache_dir = a.cache_dir or os.path.join(fam.CACHE_ROOT, a.win, "cache")
    rows = pd.read_parquet(a.rows).reset_index(drop=True)
    s = fam.SIGN[a.side]
    res = {"win": a.win, "side": a.side, "rows": int(len(rows))}
    dumps = fam.Dumps({sd: os.path.join(a.prod, "siglv-%s-%s" % (a.win, sd), "dumps") for sd in ("long", "short")})
    names = t668._source_names()
    store = {}
    full = fam.compute(rows, a.win, a.side, 1, cache_dir=cache_dir, dumps=dumps)

    # G-brute
    ix = g6.pick(rows, a.k_brute, rng)
    ix = ix[np.argsort(rows["sym"].to_numpy(dtype=object).astype(str)[ix], kind="stable")]
    got, ref_rows = full.iloc[ix].to_numpy(), []
    series = {r: RefSeries(cache_dir, r, store) for r in fam.REFS}
    cache_ref = {}
    for i in ix:
        sym = rows["sym"].iat[i]
        src_name = names[int(rows["src"].iat[i])]
        if src_name not in cache_ref:
            cache_ref[src_name] = refs_for_brute(series, dumps, a.side, src_name)
        t = int(rows["sig_ms"].iat[i] // 60_000)
        am = g6.load_arr(cache_dir, sym, store)
        if am is None:
            ref_rows.append([NAN] * len(fam.COLUMNS))
            continue
        o_, h_, l_, c_, _ = g6.lists_of(am)
        nb = g6.natr_at(o_, h_, l_, c_, t - 1)
        sl = dumps.get(a.side, src_name, True).get(sym)
        ref_rows.append(brute_row(s, t, abs(int(rows["level_px_e8"].iat[i])), int(rows["level"].iat[i]), nb, sl, cache_ref[src_name]))
    bad, dmax = g6.compare(got, np.array(ref_rows))
    res["g_brute"] = dict(k=int(len(ix)), bad_by_col={c: int(x) for c, x in zip(fam.COLUMNS, bad) if x}, max_abs_diff=dmax, ok=bool(bad.sum() == 0),
                          nan_share={c: round(float(np.isnan(got[:, j]).mean()), 4) for j, c in enumerate(fam.COLUMNS)})

    # G-causal
    jx = g6.pick(rows, a.k_causal, rng)
    bad_c = changed = 0
    for i in jx:
        sym = rows["sym"].iat[i]
        am = g6.load_arr(cache_dir, sym, store)
        if am is None:
            continue
        one = rows.iloc[[i]]
        t = int(rows["sig_ms"].iat[i] // 60_000)
        base = fam.compute(one, a.win, a.side, 1, cache_dir=cache_dir, dumps=dumps).to_numpy()
        for t_g, strong in ((t, False), (t - 1, True)):
            tmp = tempfile.mkdtemp(prefix="l697-", dir=a.tmp)
            for k in {sym, *fam.REFS}:
                ak = g6.load_arr(cache_dir, k, store)
                if ak is not None:
                    g6.write_b42(os.path.join(tmp, k + ".b42"), ft.garbage_from(ak, t_g, rng))
            got1 = fam.compute(one, a.win, a.side, 1, cache_dir=tmp, dumps=GDumps(dumps, sym, t_g, rng)).to_numpy()
            shutil.rmtree(tmp, ignore_errors=True)
            same = np.array_equal(base, got1, equal_nan=True)
            if not strong and not same:
                bad_c += 1
            if strong and not same:
                changed += 1
    res["g_causal"] = dict(k=int(len(jx)), bad=int(bad_c), control_changed=int(changed), ok=bool(bad_c == 0 and changed > 0))
    res["ok"] = bool(res["g_brute"]["ok"] and res["g_causal"]["ok"])
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: (v if not isinstance(v, dict) else v.get("ok")) for k, v in res.items()}))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
