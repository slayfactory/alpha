#!/usr/bin/env python3
"""П-42 TK-701 (единички, набор `t701`): ворота столбцов `tfv60`, `pre_n5`, `oi_c1` (`tk701_sig.py`) по ВСЕМ сигналам базы файла (включая сигналы без строки таблицы TK-671), без P&L.
  G-brute  — независимый путь: лента читается из zip (`_from_zip`, не из склада), NATR считается прямым циклом по минутам, `oi_c1` — прямым чтением строк metrics; значения сверяются с файлом столбцов;
  G-causal — тики с t ≥ sig отравлены (цена ×1,7, оборот ×3, бит агрессора инвертирован): `tfv60` и `pre_n5` не меняются; контроль силы — отравление тиков с t < sig (последние 60 с) меняет значения;
  G-eq     — в самом `tk701_sig.py` (`g_eq` в info.json): `tfv60` и `oi_c1` = столбцы таблицы TK-671 на сигналах таблицы.
  python3 tk701_sig_gate.py --win wb --side long --out /data/p42-tk701s/fam/c701/gate/gate-wb-long.json [--k-brute 200] [--k-causal 200]"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk594_feat as F594  # noqa: E402
import tk701_sig as S  # noqa: E402
import tk701_tape as T  # noqa: E402


class ZipTape(T.StoreTape):
    """Лента только из zip (независимый декодер): склад не открывается."""

    def _from_store(self, date, d, zst):
        return None


def natr_loop(c_path, b, p=30):
    """Независимая реализация NATR: прямой цикл по p закрытым минутам до b включительно (TR = max(h − l, |h − prevc|, |l − prevc|), prevc — close предыдущей минуты с данными); NaN при k·2 < p."""
    with open(c_path, "rb") as f:
        head = f.read(8)
    n = int.from_bytes(head[4:8], "little")
    a = np.fromfile(c_path, dtype="<i8", offset=8, count=5 * n).reshape(5, n)
    o, h, l, c = a[0], a[1], a[2], a[3]
    if b < p - 1 or b >= n or o[b] == 0:
        return float("nan")
    tot, k = 0.0, 0
    for m in range(b - p + 1, b + 1):
        if o[m] == 0:
            continue
        j = m - 1
        while j >= 0 and o[j] == 0:
            j -= 1
        pc = float(c[j]) if j >= 0 else 0.0
        tr = float(h[m] - l[m])
        if pc != 0:
            tr = max(tr, abs(float(h[m]) - pc), abs(float(l[m]) - pc))
        tot += tr
        k += 1
    if k * 2 < p:
        return float("nan")
    return tot / k / float(c[b])


def oi_direct(sym, sig):
    """Независимое чтение `oi_c1`: строки metrics на точных отметках T* и T* − 12·5 мин прямо из zip; NaN, если строки нет."""
    Ts = (sig - 10 * 60_000) // 300_000 * 300_000
    vals = []
    for t in (Ts, Ts - 12 * 300_000):
        d = t // T.DAY_MS
        path = f"{F594.METRICS}/{sym}/{sym}-metrics-{F594.date_str(d)}.zip"
        if not os.path.exists(path):
            return float("nan")
        df = F594.read_zip_csv(path, f"{sym}-metrics-{F594.date_str(d)}.csv", ["create_time", "sum_open_interest"])
        import pandas as pd
        ct = pd.to_datetime(df["create_time"]).astype("int64").to_numpy() // 10**6
        k = np.flatnonzero(ct == t)
        if not len(k):
            return float("nan")
        vals.append(float(df["sum_open_interest"].to_numpy(float)[k[0]]))
    return (vals[0] / vals[1] - 1.0) * 1e4 if vals[1] > 0 else float("nan")


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--win", required=True)
    ap.add_argument("--side", required=True)
    ap.add_argument("--col-file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--k-brute", type=int, default=200)
    ap.add_argument("--k-causal", type=int, default=200)
    ap.add_argument("--seed", type=int, default=701)
    a = ap.parse_args()
    res = pd.read_parquet(a.col_file or S.OUT % (a.win, a.side))
    s = 1 if a.side == "long" else -1
    sl = S.SIDE_L[a.side]
    eps = pd.read_parquet(os.path.join(S.FEAT34, "h775", "%s-%s" % (a.win, sl), "eps-%s.parquet" % sl), columns=["sym", "sig_ms"])
    tab = pd.read_parquet(os.path.join(S.PROD, "lvl-%s-%s" % (a.win, a.side), "rows.parquet"), columns=["row_id", "sym", "sig_ms", "abs_ms", "base_pass"])
    rows = S.ext_rows(eps, tab, a.win, a.side).set_index("row_id")
    assert list(rows.index) == list(res.index) and (rows["sym"].to_numpy() == res["sym"].to_numpy()).all() and (rows["sig_ms"].to_numpy() == res["sig_ms"].to_numpy()).all(), "строки файла ≠ ext_rows"
    coins = set(x.strip() for x in open(S.COINS_FILE, encoding="utf-8") if x.strip())
    rng = np.random.default_rng(a.seed)
    cand = np.flatnonzero(np.isin(res["sym"].to_numpy(), list(coins)) & np.isfinite(res["tfv60"].to_numpy()))
    out = dict(win=a.win, side=a.side, rows=int(len(res)), candidates_with_tape=int(len(cand)))
    # ---- G-brute: лента из zip, NATR циклом
    kb = rng.choice(cand, size=min(a.k_brute, len(cand)), replace=False)
    d_tfv, d_pre, d_nat, nb_pre_nan = [], [], [], 0
    for k in kb:
        r = rows.iloc[k]
        sg, sg_rel, sym = int(r["abs_ms"]), int(r["sig_ms"]), str(r["sym"])
        tp = ZipTape(a.win, sym)
        Tm, P, V, B, miss = tp.span(sg - T.LOOK_MS, sg - 1)
        cp = S.CACHE % (a.win, sym)
        nat = natr_loop(cp, sg_rel // 60000 - 1) if os.path.exists(cp) else float("nan")
        tfv, pre = S.sig_vals(Tm, P, V, B, sg, s, nat)
        d_tfv.append(abs(tfv - res["tfv60"].iloc[k]))
        if np.isnan(pre) != np.isnan(res["pre_n5"].iloc[k]):
            nb_pre_nan += 1
        elif not np.isnan(pre):
            d_pre.append(abs(pre - res["pre_n5"].iloc[k]))
    out["brute"] = dict(n=int(len(kb)), max_abs_tfv60=float(np.nanmax(d_tfv)) if d_tfv else 0.0, max_abs_pre_n5=float(max(d_pre)) if d_pre else 0.0, nan_pattern_pre_n5_bad=int(nb_pre_nan),
                        bad=int(sum(x > 1e-9 for x in d_tfv) + sum(x > 1e-9 for x in d_pre) + nb_pre_nan))
    # ---- G-causal
    kc = rng.choice(cand, size=min(a.k_causal, len(cand)), replace=False)
    bad, ctl = 0, 0
    for k in kc:
        r = rows.iloc[k]
        sg, sg_rel, sym = int(r["abs_ms"]), int(r["sig_ms"]), str(r["sym"])
        tp = T.StoreTape(a.win, sym)
        Tm, P, V, B, miss = tp.span(sg - T.LOOK_MS, min(sg + 600_000, T.LAST_MS - 1))
        cp = S.CACHE % (a.win, sym)
        nat = float(S.natr_at(cp, np.array([sg_rel // 60000 - 1]))[0]) if os.path.exists(cp) else float("nan")
        base = S.sig_vals(Tm, P, V, B, sg, s, nat)
        fut = Tm >= sg
        P2, V2, B2 = P.copy(), V.copy(), B.copy()
        P2[fut], V2[fut], B2[fut] = (P2[fut] * 1.7).astype(P2.dtype), V2[fut] * 3.0, ~B2[fut]
        poi = S.sig_vals(Tm, P2, V2, B2, sg, s, nat)
        same = all((np.isnan(x) and np.isnan(y)) or x == y for x, y in zip(base, poi))
        bad += 0 if same else 1
        past = (Tm < sg) & (Tm >= sg - 60_000)
        P3, V3, B3 = P.copy(), V.copy(), B.copy()
        P3[past], V3[past], B3[past] = (P3[past] * 1.7).astype(P3.dtype), V3[past] * 3.0, ~B3[past]
        pst = S.sig_vals(Tm, P3, V3, B3, sg, s, nat)
        ctl += 0 if all((np.isnan(x) and np.isnan(y)) or x == y for x, y in zip(base, pst)) else 1
    out["causal"] = dict(n=int(len(kc)), bad=int(bad), control_changed=int(ctl))
    # ---- oi_c1 независимо
    co = np.flatnonzero(np.isin(res["sym"].to_numpy(), list(coins)) & np.isfinite(res["oi_c1"].to_numpy()))
    ko = rng.choice(co, size=min(a.k_brute, len(co)), replace=False) if len(co) else []
    d_oi, oi_nan = [], 0
    for k in ko:
        r = rows.iloc[k]
        v = oi_direct(str(r["sym"]), int(r["abs_ms"]))
        if np.isnan(v):
            oi_nan += 1
        else:
            d_oi.append(abs(v - res["oi_c1"].iloc[k]))
    out["oi"] = dict(n=int(len(ko)), max_abs=float(max(d_oi)) if d_oi else 0.0, direct_nan=int(oi_nan), bad=int(sum(x > 1e-6 for x in d_oi) + oi_nan))
    out["ok"] = bool(out["brute"]["bad"] == 0 and out["causal"]["bad"] == 0 and out["causal"]["control_changed"] > 0 and out["oi"]["bad"] == 0)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
