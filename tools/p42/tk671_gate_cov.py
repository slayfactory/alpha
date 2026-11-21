#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), ворота G-cov-base и G-uni (протокол PS-948 §10; порог ≥ 95 % записан до счёта).

  python3 tk671_gate_cov.py [--out /data/p42-tk671/asm/gates-cov.json]

G-cov-base: доля сделок баз `L_base`, `L_n24` (лонг), `S_base`, `D_S` (шорт), чей ключ `(sym, sig_ms)` есть среди строк `prod/lvl-<окно>-<сторона>/rows.parquet`, по каждому окну и базе; читаются ТОЛЬКО ключи сделок
(`sym`, `sig_ms`), P&L не читается. Считаются сделки ЗАЧЁТНОГО диапазона окна (`lo_ms ≤ sig_ms < hi_ms`, §2 протокола; сетка окна шире — сутки 14…73 — и файлы баз несут сигналы вне зачёта, их срезает `tk604_stats.splice`); в выход рядом печатается доля по всем сделкам файла. Ниже порога — код возврата 3 (стоп, отчёт; G = 60 и состав строк не менять).
G-uni: `row_id` уникален, нет дублей `(sym, sig_ms, level, level_px_e8)`, `sig_ms` внутри зачётного диапазона окна, суточные диапазоны окон не пересекаются и покрывают 136 суток (01.07–13.11);
метки `lab`: у каждой строки не больше одной сделки по ключу `(src, sym, level, sig_ms)` (join 1:1; читаются только ключи); сделки прогона источника, чья строка закреплена за ДРУГИМ источником (та же цена уровня при
дедупликации по `(sym, sig_ms, level_px_e8)`; в `ftab` ключ `(sym, sig_ms)`), в метки не идут — считаются и должны иметь ключ `(sym, sig_ms)` среди строк окна, иначе — красный."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_stats as st  # noqa: E402

PROD = "/data/p42-tk671/prod"
SOURCES = ["p30n1", "p30n2", "p30n3", "p60n1", "p60n2", "p60n3", "p240n1", "p240n2", "p240n3", "lxD", "lxW", "lxP"]
THRESH = 0.95
DAY_MS = 86_400_000
BASES = {                                           # база → (сторона, путь к сделкам по окну)
    "L_base": ("long", "/data/p42-tk642/cells/{win}/L_base/long-trades.csv"),
    "L_n24": ("long", "/data/p42-tk638/ladd-h775/{win}-L/N24_q60/long-trades.csv"),
    "S_base": ("short", "/data/p42-tk642/cells/{win}/S_base/short-trades.csv"),
    "D_S": ("short", "/data/p42-tk668/e4/cells/{win}/S_lx1/short-trades.csv"),
}


def read_keys(path, cols):
    t = pacsv.read_csv(path, convert_options=pacsv.ConvertOptions(include_columns=cols, column_types={"sym": pa.string()}))
    return t.to_pandas()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="/data/p42-tk671/asm/gates-cov.json")
    a = ap.parse_args()
    wins = {w.name: w for w in st.TRADE_WINDOWS}
    res = {"cov_base": {}, "uni": {}, "lab_join": {}}
    ok = True
    rows = {}
    for win in wins:
        for side in ("long", "short"):
            rows[(win, side)] = pd.read_parquet(f"{PROD}/lvl-{win}-{side}/rows.parquet", columns=["row_id", "sym", "sig_ms", "level", "level_px_e8", "src", "abs_ms"])
    # G-cov-base
    for win, w in wins.items():
        lo_ms, hi_ms = (w.lod - w.t0d).days * DAY_MS, ((w.hid - w.t0d).days + 1) * DAY_MS
        for base, (side, tpl) in BASES.items():
            p = tpl.format(win=win)
            tr = read_keys(p, ["sym", "sig_ms"])
            keys = set(zip(rows[(win, side)]["sym"].tolist(), rows[(win, side)]["sig_ms"].tolist()))
            inr = (tr["sig_ms"] >= lo_ms) & (tr["sig_ms"] < hi_ms)
            hit_all = sum(1 for k in zip(tr["sym"].tolist(), tr["sig_ms"].tolist()) if k in keys)
            tz = tr[inr]
            hit = sum(1 for k in zip(tz["sym"].tolist(), tz["sig_ms"].tolist()) if k in keys)
            share = hit / len(tz) if len(tz) else 1.0
            res["cov_base"][f"{win}/{base}"] = dict(trades=int(len(tz)), covered=int(hit), share=round(share, 6), trades_all_file=int(len(tr)), share_all_file=round(hit_all / len(tr), 6) if len(tr) else 1.0,
                                                   path=p, **{"pass": share >= THRESH})
            ok &= share >= THRESH
    # G-uni
    days = []
    for win, w in wins.items():
        lo_ms, hi_ms = (w.lod - w.t0d).days * DAY_MS, ((w.hid - w.t0d).days + 1) * DAY_MS
        d0 = w.lod
        d1 = w.hid
        days.append((win, d0, d1))
        for side in ("long", "short"):
            r = rows[(win, side)]
            c = dict(rows=int(len(r)), row_id_unique=bool(r["row_id"].is_unique), dup_keys=int(r.duplicated(["sym", "sig_ms", "level", "level_px_e8"]).sum()),
                     in_range=bool(((r["sig_ms"] >= lo_ms) & (r["sig_ms"] < hi_ms)).all()), lo=str(d0), hi=str(d1),
                     first_day=str(dt.datetime.fromtimestamp(r["abs_ms"].min() / 1000, dt.timezone.utc).date()), last_day=str(dt.datetime.fromtimestamp(r["abs_ms"].max() / 1000, dt.timezone.utc).date()))
            c["pass"] = c["row_id_unique"] and c["dup_keys"] == 0 and c["in_range"]
            res["uni"][f"{win}/{side}"] = c
            ok &= c["pass"]
    span = sorted(days, key=lambda x: x[1])
    disjoint = all(span[i][2] < span[i + 1][1] for i in range(len(span) - 1))
    n_days = sum((d1 - d0).days + 1 for _, d0, d1 in span)
    contiguous = all((span[i + 1][1] - span[i][2]).days == 1 for i in range(len(span) - 1))
    res["uni"]["windows"] = dict(disjoint=bool(disjoint), contiguous=bool(contiguous), days=int(n_days), first=str(span[0][1]), last=str(span[-1][2]), **{"pass": disjoint and contiguous and n_days == 136})
    ok &= res["uni"]["windows"]["pass"]
    # метки lab: 1:1 по ключам (значения не читаются)
    for win, w in wins.items():
        lo_ms, hi_ms = (w.lod - w.t0d).days * DAY_MS, ((w.hid - w.t0d).days + 1) * DAY_MS
        for side in ("long", "short"):
            r = rows[(win, side)]
            by_src = {si: set(zip(g["sym"].tolist(), g["level"].tolist(), g["sig_ms"].tolist())) for si, g in r.groupby("src")}
            any_key = set(zip(r["sym"].tolist(), r["sig_ms"].tolist()))
            tot = dup = orphan = outside = unexplained = 0
            for si, src in enumerate(SOURCES):
                p = f"{PROD}/lab-{win}-{side}/{src}/lab_{src}/{side}-trades.csv"
                if not os.path.exists(p):
                    continue
                t = read_keys(p, ["sym", "level", "sig_ms"])
                outside += int(((t["sig_ms"] < lo_ms) | (t["sig_ms"] >= hi_ms)).sum())
                t = t[(t["sig_ms"] >= lo_ms) & (t["sig_ms"] < hi_ms)]
                tot += len(t)
                dup += int(t.duplicated(["sym", "level", "sig_ms"]).sum())
                ks = by_src.get(si, set())
                for k in zip(t["sym"].tolist(), t["level"].tolist(), t["sig_ms"].tolist()):
                    if k not in ks:
                        orphan += 1
                        unexplained += (k[0], k[2]) not in any_key
            res["lab_join"][f"{win}/{side}"] = dict(label_trades=int(tot), label_trades_outside_zachet=int(outside), duplicate_keys=dup, trades_of_other_source_rows=orphan, orphan_key_not_in_any_row=unexplained, rows=int(len(r)), **{"pass": dup == 0 and unexplained == 0})
            ok &= dup == 0 and unexplained == 0
    res["pass"] = bool(ok)
    res["threshold"] = THRESH
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({"cov_base": {k: v["share"] for k, v in res["cov_base"].items()}, "uni_pass": all(v["pass"] for v in res["uni"].values()), "lab_join_pass": all(v["pass"] for v in res["lab_join"].values()), "pass": ok}, ensure_ascii=False))
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
