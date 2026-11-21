#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): набор данных стороны для обучения — признаки сборки + метки (`lab`), один parquet на сторону.

  python3 tk671_data.py build --side long|short [--asm /data/p42-tk671/asm] [--out /data/p42-tk671/data]
  python3 tk671_data.py glab  --side long|short                 # ворота G-lab: метки строк = сделки двойников баз (те же сигналы), счёт расхождений (печатаются только числа совпадений)

Склейка по ключу `(src, sym, level, sig_ms)` (§3 протокола): у строки метка — сделка прогона ЕЁ источника (`prod/lab-<окно>-<сторона>/<источник>/lab_<источник>/<сторона>-trades.csv`); у строки без сделки
метки нет (`y_has = False`). Время сделок в файлах — мс от T0 окна, в наборе — абсолютные UTC мс (`+ t0_ms`). Столбцы: `win`, `row_id`, `sym`, `meta_*` (в обучение НЕ идут), `y_*` (метки, НЕ признаки), остальное — признаки (float32).
Дополнительно к сборке: `meta_sig_ms` (мс от T0 окна) и `meta_dist_e8` (для порядка реплики) — из `rows.parquet`. Не читать значения меток до PASS протокола."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_stats as st  # noqa: E402

PROD = "/data/p42-tk671/prod"
SOURCES = ["p30n1", "p30n2", "p30n3", "p60n1", "p60n2", "p60n3", "p240n1", "p240n2", "p240n3", "lxD", "lxW", "lxP"]
WINS = {w.name: w for w in st.TRADE_WINDOWS}
LABEL_COLS = ["sym", "level", "sig_ms", "entry_ms", "exit_ms", "entry_px_e8", "kind", "net"]


def read_trades(path, cols=LABEL_COLS):
    t = pacsv.read_csv(path, convert_options=pacsv.ConvertOptions(include_columns=cols, column_types={"sym": pa.string(), "kind": pa.string()}))
    return t.to_pandas()


def attach_labels(rows, trades_by_src, t0_ms):
    """rows: DataFrame с `meta_src`, `meta_sym`, `meta_level`, `meta_sig_ms`; trades_by_src: {индекс источника: сделки}. Возвращает DataFrame y_* в порядке строк rows."""
    n = len(rows)
    y = pd.DataFrame({"y_has": np.zeros(n, dtype=bool), "y_net": np.full(n, np.nan), "y_entry_ms": np.full(n, -1, dtype=np.int64), "y_exit_ms": np.full(n, -1, dtype=np.int64),
                      "y_entry_px_e8": np.full(n, -1, dtype=np.int64), "y_stop": np.full(n, np.nan)}, index=rows.index)
    for si, t in trades_by_src.items():
        sel = rows.index[rows["meta_src"].to_numpy() == si]
        if len(sel) == 0 or len(t) == 0:
            continue
        key = pd.MultiIndex.from_arrays([t["sym"], t["level"], t["sig_ms"]])
        assert key.is_unique, f"источник {si}: дубли ключа (sym, level, sig_ms) в сделках"
        rk = pd.MultiIndex.from_arrays([rows.loc[sel, "meta_sym"], rows.loc[sel, "meta_level"], rows.loc[sel, "meta_sig_ms"]])
        pos = key.get_indexer(rk)
        ok = pos >= 0
        idx = sel[ok]
        tt = t.iloc[pos[ok]]
        y.loc[idx, "y_has"] = True
        y.loc[idx, "y_net"] = tt["net"].to_numpy()
        y.loc[idx, "y_entry_ms"] = tt["entry_ms"].to_numpy(dtype=np.int64) + t0_ms
        y.loc[idx, "y_exit_ms"] = tt["exit_ms"].to_numpy(dtype=np.int64) + t0_ms
        y.loc[idx, "y_entry_px_e8"] = tt["entry_px_e8"].to_numpy(dtype=np.int64)
        y.loc[idx, "y_stop"] = (tt["kind"].to_numpy() == "stop").astype(float)
    return y


def build(side, asm, out):
    os.makedirs(out, exist_ok=True)
    parts, info = [], {"side": side, "wins": {}}
    for win, w in WINS.items():
        a = pd.read_parquet(f"{asm}/{win}-{side}.parquet")
        r = pd.read_parquet(f"{PROD}/lvl-{win}-{side}/rows.parquet", columns=["row_id", "sig_ms", "dist_e8"]).set_index("row_id")
        a = a.set_index("row_id", drop=False).rename_axis(None)
        assert a.index.is_unique and set(r.index) == set(a.index), f"{win}: row_id сборки и rows.parquet не совпали"
        a["meta_sig_ms"] = r.loc[a.index, "sig_ms"].to_numpy()
        a["meta_dist_e8"] = r.loc[a.index, "dist_e8"].to_numpy()
        trades = {}
        for si, src in enumerate(SOURCES):
            p = f"{PROD}/lab-{win}-{side}/{src}/lab_{src}/{side}-trades.csv"
            if os.path.exists(p):
                trades[si] = read_trades(p)
        y = attach_labels(a, trades, w.t0_ms)
        a = pd.concat([a, y], axis=1)
        a.insert(0, "win", win)
        parts.append(a)
        info["wins"][win] = dict(rows=int(len(a)), labeled=int(y["y_has"].sum()))
    d = pd.concat(parts, ignore_index=True)
    meta = [c for c in d.columns if c.startswith("meta_")]
    ycols = [c for c in d.columns if c.startswith("y_")]
    feats = [c for c in d.columns if c not in meta + ycols + ["win", "row_id", "sym"]]
    d = d[["win", "row_id", "sym"] + meta + ycols + feats]
    d["win"] = d["win"].astype("category")
    d["sym"] = d["sym"].astype(str).astype("category")           # категории окон разные — склейка дала бы object
    d.to_parquet(f"{out}/{side}.parquet", compression="zstd", index=False)
    info.update(rows=int(len(d)), labeled=int(d["y_has"].sum()), n_features=len(feats) + 1, meta=meta, y=ycols)
    with open(f"{out}/{side}.info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: info[k] for k in ("side", "rows", "labeled", "n_features")}, ensure_ascii=False))
    return 0


def glab(side, out="/data/p42-tk671/data", twin_root="/data/p42-tk671/gate-pf"):
    """G-lab (определение редакции 2, исправлено 21.11 — см. карточку TK-671 и вердикт критика): сделки двойника баз в зачётном диапазоне окна (`lo_ms <= sig_ms < hi_ms`, как у баз) против меток строк.
    Ключ строки `(sym, sig_ms, entry_px_e8)` неоднозначен (у строк разных источников/уровней тот же вход), поэтому: «найдено» = у сделки есть хоть одна строка-кандидат с меткой по ключу;
    «точно» = среди кандидатов есть строка с теми же `net, entry_ms, exit_ms, kind`. Ворота: найдено >= 95 % и точно = 100 % найденных, найдено >= 200 сделок.
    Печатаются числа и ключи ненайденных сделок (без `net`/`kind`); для каждой — есть ли в окне строка с тем же `(sym, sig_ms)` без метки / с иным входом / вообще нет."""
    d = pd.read_parquet(f"{out}/{side}.parquet", columns=["win", "sym", "meta_sig_ms", "meta_src", "meta_level", "y_has", "y_net", "y_entry_ms", "y_exit_ms", "y_entry_px_e8", "y_stop"])
    twins = {"long": ["L_ind"], "short": ["S_ind", "D_ind"]}[side]
    res = {"side": side, "cells": {}, "missing": []}
    ok = True
    for tw in twins:
        tot = found = exact = 0
        for win, w in WINS.items():
            lo_ms = (w.lod - w.t0d).days * 86_400_000
            hi_ms = ((w.hid - w.t0d).days + 1) * 86_400_000
            t = read_trades(f"{twin_root}/{win}/out/{tw}/{side}-trades.csv")
            t = t[(t["sig_ms"] >= lo_ms) & (t["sig_ms"] < hi_ms)].reset_index(drop=True)
            lab = d[(d["win"] == win) & d["y_has"]]
            m = t.reset_index().merge(lab, left_on=["sym", "sig_ms", "entry_px_e8"], right_on=["sym", "meta_sig_ms", "y_entry_px_e8"], how="left")
            m["found"] = m["y_has"].notna()
            m["same"] = m["found"] & (np.abs(m["net"] - m["y_net"]) <= 1e-9) & (m["entry_ms"] + w.t0_ms == m["y_entry_ms"]) & (m["exit_ms"] + w.t0_ms == m["y_exit_ms"])                 & ((m["kind"] == "stop").astype(float) == m["y_stop"])
            g = m.groupby("index").agg(found=("found", "max"), same=("same", "max"))
            tot += len(t)
            found += int(g["found"].sum())
            exact += int(g["same"].sum())
            allrows = d[d["win"] == win]
            for i in g.index[~g["found"].to_numpy()]:
                r = t.loc[i]
                cand = allrows[(allrows["sym"] == r["sym"]) & (allrows["meta_sig_ms"] == r["sig_ms"])]
                res["missing"].append(dict(twin=tw, win=win, sym=r["sym"], sig_ms=int(r["sig_ms"]), level=int(r["level"]), entry_px_e8=int(r["entry_px_e8"]),
                                           rows_same_sym_sig=int(len(cand)), rows_labeled=int(cand["y_has"].sum()), rows_other_entry=int((cand["y_entry_px_e8"] != r["entry_px_e8"]).sum()),
                                           src_levels=[f"{int(a)}:{int(b)}" for a, b in zip(cand["meta_src"], cand["meta_level"])][:6]))
        c = dict(twin_trades=tot, found=found, exact=exact, share_found=round(found / tot, 6) if tot else 1.0, share_exact_of_found=round(exact / found, 6) if found else 0.0)
        c["pass"] = bool(found >= 200 and exact == found and found / max(tot, 1) >= 0.95)
        res["cells"][tw] = c
        ok &= c["pass"]
    res["pass"] = bool(ok)
    with open(f"{out}/{side}.glab.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if ok else 3


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("build", "glab"))
    ap.add_argument("--side", required=True, choices=("long", "short"))
    ap.add_argument("--asm", default="/data/p42-tk671/asm")
    ap.add_argument("--out", default="/data/p42-tk671/data")
    a = ap.parse_args()
    sys.exit(build(a.side, a.asm, a.out) if a.cmd == "build" else glab(a.side, a.out))


if __name__ == "__main__":
    main()
