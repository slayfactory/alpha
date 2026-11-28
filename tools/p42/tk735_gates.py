#!/usr/bin/env python3
"""TK-735, ворота до чтения P&L (§13.79 п. 8): G-src → G-V → G-trunc. P&L сделок здесь не печатается и не используется (только ключ дедупликации L-477).

  tk735_gates.py src     # все пути открываются, ключи клеток, объём базы против TK-727, столбцы минутных файлов во всех окнах
  tk735_gates.py v       # RV60 по свечам против целочисленной копии `rv_of` движка (data.rs), ≥ 100 моментов
  tk735_gates.py trunc   # причинность признаков φ1, φ2, φ4–φ11 и r60: значение на обрезанных / испорченных после t−1 данных = значению на полных, ≥ 100 моментов
Выход — JSON в /data/p42-tk735/res/g_<имя>.json.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk727_lib as L  # noqa: E402
import tk727_s5 as S5  # noqa: E402
import tk735_lib as T  # noqa: E402

RES = os.path.join(T.ROOT, "res")
SCALE = 100_000_000


def dump(name, obj):
    os.makedirs(RES, exist_ok=True)
    json.dump(obj, open(os.path.join(RES, "g_%s.json" % name), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


def base_unique():
    tr = T.load_trades_all()
    idx, n_raw, n_dup = T.dedupe(tr)
    keep = {k: v[idx] for k, v in tr.items()}
    keep["regime"] = T.regime_of_day(keep["eday"])
    return tr, keep, n_raw, n_dup


# ───────────────────────────── G-src ─────────────────────────────

def cmd_src(a):
    out = dict(gate="G-src", ok=True, problems=[])
    syms = T.read_syms()
    out["n_syms"] = len(syms)
    # окна свечей и столбцы
    wins = {}
    rng = np.random.default_rng(T.SEED)
    for w in L.WINS:
        root = os.path.join(L.KROOT, w, "klines")
        have = sorted(os.listdir(root)) if os.path.isdir(root) else []
        files = []
        for s in have[:]:
            if s in set(syms):
                for fn in sorted(os.listdir(os.path.join(root, s))):
                    if fn[:4].isdigit() and fn.endswith(".csv"):         # дневные свечи; funding.csv и прочее — не минутки
                        files.append((s, fn))
        n_hdr = n_bad = 0
        bad = []
        pick = rng.choice(len(files), min(60, len(files)), replace=False) if files else []
        for i in pick:
            s, fn = files[int(i)]
            with open(os.path.join(root, s, fn), encoding="utf-8") as f:
                h = f.readline().strip()
            n_hdr += 1
            if h != T.KL_HEADER:
                n_bad += 1
                bad.append((s, fn, h[:80]))
        cols = T.KL_HEADER.split(",")
        need = ("count", "taker_buy_quote_volume", "quote_volume", "high", "low")
        wins[w] = dict(dir=os.path.isdir(root), n_syms_c539=len([s for s in have if s in set(syms)]), n_files=len(files), headers_checked=n_hdr, headers_bad=n_bad,
                       cols_present=all(c in cols for c in need), bad=bad[:3])
        if not wins[w]["dir"] or n_bad:
            out["ok"] = False
            out["problems"].append("окно %s: нет каталога или заголовок не совпал" % w)
    out["windows"] = wins
    # покрытие дней: у каждой монеты c539 найден ли хотя бы один день в сетке G0
    # сделки и клетки
    c = sqlite3.connect("file:/data/p42-tk726/tk726.sqlite?mode=ro", uri=True)
    cells = c.execute("select cid, name, ckey from tk726_cell where cid between 7180015 and 7180023 order by cid").fetchall()
    out["cells"] = [dict(cid=r[0], name=r[1], ckey=r[2]) for r in cells]
    anc = [r for r in cells if r[0] == T.ANCHOR][0]
    keys = dict(kv.split("=", 1) for kv in anc[2].split())
    out["anchor"] = dict(cid=T.ANCHOR, name=anc[1], nf=keys.get("nf", "нет в ключе → умолчание движка 0 (NATR `natr_p`, TK-681)"),
                         natr_min=keys.get("natr_min", "нет в ключе → умолчание движка 0,4 % (`params.rs:563`, pm(0.4))"), ckey=anc[2])
    tr, u, n_raw, n_dup = base_unique()
    out["trades_raw_rows"] = n_raw
    out["dups"] = n_dup
    out["unique_total"] = int(len(u["pnl"]))
    ex = {"FJ": (1223, 148, 323), "SEL": (803, 136, 266)}
    reg = {}
    for name in ("FJ", "SEL", "jan", "n1"):
        m = u["regime"] == name
        nd = int(len(np.unique(u["eday"][m])))
        ns = int(len(np.unique(u["sym"][m])))
        sig = int(len(set(zip(u["sym"][m].tolist(), u["sig_ms"][m].tolist()))))
        anchor_n = int(((tr["cid"] == T.ANCHOR) & (T.regime_of_day(tr["eday"]) == name)).sum())
        reg[name] = dict(unique_trades=int(m.sum()), days_with_trades=nd, coins=ns, unique_signals=sig, anchor_s4_rows=anchor_n)
        if name in ex:
            e = ex[name]
            reg[name]["tk727"] = dict(trades=e[0], days=e[1], coins=e[2])
            ok_n = (reg[name]["unique_trades"] in (803, 804)) if name == "SEL" else reg[name]["unique_trades"] == e[0]
            if not (ok_n and nd == e[1] and ns == e[2]):
                out["ok"] = False
                out["problems"].append("объём базы %s не совпал с TK-727" % name)
    out["regimes"] = reg
    # монеты c539: свечи по каждой монете есть хотя бы в одном окне (по первому файлу)
    miss = [s for s in syms if not any(os.path.isdir(os.path.join(L.KROOT, w, "klines", s)) for w in L.WINS)]
    out["syms_without_candles"] = miss
    dump("src", out)
    print(json.dumps({k: out[k] for k in ("ok", "problems", "anchor", "regimes", "dups", "syms_without_candles")}, ensure_ascii=False, default=str))
    for w, d in wins.items():
        print("  %-5s dir=%s coins=%d files=%d hdr %d/%d bad=%d cols=%s" % (w, d["dir"], d["n_syms_c539"], d["n_files"], d["headers_checked"] - d["headers_bad"], d["headers_checked"], d["headers_bad"], d["cols_present"]))
    sys.exit(0 if out["ok"] else 3)


# ───────────────────────────── G-V ─────────────────────────────

def rv_engine_int(S, tc, p=60):
    """Целочисленная копия `rv_of` (src/breakout/data.rs): r = (cc[j] − cc[j−1])·SCALE / cc[j−1] с усечением к нулю по минутам j ∈ [tc−p+1, tc], обе минуты присутствуют; RV = isqrt(Σ r²);
    нужна присутствующая минута tc, tc ≥ p и ≥ ⌈p/2⌉ доходностей, иначе −1. Возвращает (RV в долях цены ×SCALE, число доходностей k)."""
    pres = S["pres"]
    cc = np.where(pres, np.rint(S["c"] * 1e8), 0).astype(np.int64)
    if tc < p or not pres[tc]:
        return -1, 0
    s2, k = 0, 0
    for j in range(tc - p + 1, tc + 1):
        if j > 0 and pres[j] and pres[j - 1]:
            num = (int(cc[j]) - int(cc[j - 1])) * SCALE
            den = int(cc[j - 1])
            r = abs(num) // den * (1 if num >= 0 else -1)      # усечение к нулю, как i128 `/`
            s2 += r * r
            k += 1
    if k * 2 < p:
        return -1, k
    return math.isqrt(s2), k


def pick_moments(u, n, seed):
    rng = np.random.default_rng(seed)
    sel = rng.choice(len(u["sym"]), min(n, len(u["sym"])), replace=False)
    m = (u["sig_ms"][sel] - L.G0_MS) // L.MIN_MS
    return [(str(u["sym"][i]), int(mm) - 1) for i, mm in zip(sel, m)]       # (монета, закрытая минута tc)


def cmd_v(a):
    tr, u, n_raw, n_dup = base_unique()
    ok_reg = np.isin(u["regime"], ("FJ", "SEL"))
    uu = {k: v[ok_reg] for k, v in u.items()}
    mom = pick_moments(uu, 150, T.SEED)
    by = {}
    for s, tc in mom:
        by.setdefault(s, []).append(tc)
    rows, n_ok, n_cmp, maxd = [], 0, 0, 0.0
    n_nan_both = 0
    for s, tcs in sorted(by.items()):
        S = T.load_kl(s)
        rv = L.rv_sqrt_sum(S, 60)
        for tc in tcs:
            py = float(rv[tc]) * SCALE if np.isfinite(rv[tc]) else None
            eng, k = rv_engine_int(S, tc)
            if py is None and eng < 0:
                n_nan_both += 1
                rows.append(dict(sym=s, tc=tc, py=None, eng=None, ok=True))
                continue
            if py is None or eng < 0:
                rows.append(dict(sym=s, tc=tc, py=py, eng=eng, ok=False, why="наличие значения разное"))
                continue
            d = abs(py - eng)
            tol = math.sqrt(k) + 1.0 + 1e-9 * eng       # L2-граница усечения r_i и floor isqrt
            ok = d <= tol
            n_cmp += 1
            n_ok += ok
            maxd = max(maxd, d)
            rows.append(dict(sym=s, tc=tc, py=py, eng=eng, k=k, diff=d, tol=tol, ok=bool(ok)))
    n_total = len(rows)
    passed = n_total >= 100 and all(r["ok"] for r in rows) and n_cmp >= 100
    out = dict(gate="G-V", passed=bool(passed), n_moments=n_total, n_compared=n_cmp, n_equal_within_tol=int(n_ok), n_both_absent=n_nan_both, max_abs_diff_units_e8=maxd,
               rule="RV60 по свечам (`tk727_lib.rv_sqrt_sum`) против целочисленной копии `rv_of` (data.rs:384-410); допуск sqrt(k)+1 единиц e8 (L2-граница усечения доходностей)",
               rows=rows)
    dump("v", out)
    print("G-V: моментов %d, сравнено %d, в допуске %d, макс. |разница| %.3f ед. e8, PASS=%s" % (n_total, n_cmp, n_ok, maxd, passed))
    for r in rows:
        if not r["ok"]:
            print("  расхождение:", r)
    sys.exit(0 if passed else 3)


# ───────────────────────────── G-trunc ─────────────────────────────

def corrupt_future(S, tc, rng):
    """Копия данных, где всё после минуты tc заменено мусором (цены ×[0,3; 3], объёмы случайные, наличие случайное)."""
    X = {k: v.copy() for k, v in S.items()}
    n = len(X["c"])
    f = slice(tc + 1, n)
    m = n - tc - 1
    if m <= 0:
        return X
    for k in ("o", "h", "l", "c"):
        X[k][f] = rng.uniform(0.3, 3.0, m) * np.nanmean(S["c"][max(0, tc - 1000):tc + 1])
    for k in ("qv", "cnt", "tbq"):
        X[k][f] = rng.uniform(0, 1e6, m)
    X["pres"][f] = rng.random(m) < 0.8
    return X


def cmd_trunc(a):
    tr, u, n_raw, n_dup = base_unique()
    mom = pick_moments(u, 150, T.SEED + 1)
    by = {}
    for s, tc in mom:
        by.setdefault(s, []).append(tc)
    rng = np.random.default_rng(T.SEED + 2)
    keys = ("V", "T24", "SURGE", "SIZE", "SELL", "CALM", "ROUND", "NTR", "TURN1", "r60")
    n_cmp = 0
    bad = []
    per = {k: dict(n=0, finite=0, bad=0) for k in keys}
    for s, tcs in sorted(by.items()):
        S = T.load_kl(s)
        for tc in tcs:
            full = T.feat_at(S, [tc])
            S_tr = {k: v[:tc + 1] for k, v in S.items()}
            f_tr = T.feat_at(S_tr, [tc])
            f_g = T.feat_at(corrupt_future(S, tc, rng), [tc])
            n_cmp += 1
            for k in keys:
                a0, a1, a2 = float(full[k][0]), float(f_tr[k][0]), float(f_g[k][0])
                eq = (np.isnan(a0) and np.isnan(a1) and np.isnan(a2)) or (a0 == a1 == a2)
                per[k]["n"] += 1
                per[k]["finite"] += int(np.isfinite(a0))
                if not eq:
                    per[k]["bad"] += 1
                    bad.append(dict(sym=s, tc=tc, key=k, full=a0, trunc=a1, garbage=a2))
    passed = n_cmp >= 100 and not bad
    out = dict(gate="G-trunc", passed=bool(passed), n_moments=n_cmp, per_feature=per, bad=bad[:20],
               rule="признак на данных, обрезанных по закрытой минуте tc, и на данных с мусором после tc = значению на полных данных; φ3 (час) — функция времени сигнала")
    dump("trunc", out)
    print("G-trunc: моментов %d, PASS=%s" % (n_cmp, passed))
    for k, d in per.items():
        print("  %-7s определён %d/%d, расхождений %d" % (k, d["finite"], d["n"], d["bad"]))
    sys.exit(0 if passed else 3)


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("src", "v", "trunc"))
    a = ap.parse_args()
    {"src": cmd_src, "v": cmd_v, "trunc": cmd_trunc}[a.cmd](a)


if __name__ == "__main__":
    main()
