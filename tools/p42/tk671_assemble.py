#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), шаг assemble: признаки всех семейств одного файла строк (окно × сторона) → один parquet `<out>/<окно>-<side>.parquet` (индекс — столбец `row_id`).

  python3 tk671_assemble.py --win wa --side long [--out /data/p42-tk671/asm]

Входы: `prod/lvl-<окно>-<side>/rows.parquet`; Rust — `fam/rust/<окно>-<side>/{f12,all,tcount,tsize,tox}.csv` (связь по позиции строки с `eps-key.csv`, ключ (sym, sig_ms) проверяется на каждой строке);
Python-семейства — `fam/<имя>/<окно>-<side>.parquet` (time, bd, fund, t668, coin, hist, bk, mkt). Метки (`lab`) здесь НЕ читаются.
Правила базы числами (`rules_*`): индекс источника → `tf_min`, `n_bounce`, `lx`; 12 бит маски источников `src_<имя>`; `n_src`; сторона. НЕ признаки (в выход идут отдельными столбцами `meta_*`): `ep_start`, `base_pass` (только членство строки),
`n_ep` (число сигналов эпизода — известно только после), `end` (конец уровня — после), `abs_ms`, окно, `src`. Дубли имён между семействами: значения сверяются; равные — второй столбец снимается, разные — переименование `<имя>__<семейство>`.
Выход: float32 (кроме `sym` — категория для CatBoost и мета-столбцов), `<out>/<окно>-<side>.cov.json` — доля не-NaN по столбцам, снятые дубли, константы.
"""
from __future__ import annotations

import argparse
import json
import os
import time

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tk700_win import win_choices  # noqa: E402
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv

SOURCES = ["p30n1", "p30n2", "p30n3", "p60n1", "p60n2", "p60n3", "p240n1", "p240n2", "p240n3", "lxD", "lxW", "lxP"]
ROOT = os.environ.get("TK671_ROOT", "/data/p42-tk671")          # TK-700: отложенная выборка — свой корень (`/data/p42-tk700/p671`), строки — только база
PROD, FAM, RUST = ROOT + "/prod", ROOT + "/fam", ROOT + "/fam/rust"
PY_FAMS = ["time", "bd", "fund", "t668", "coin", "hist", "bk", "mkt"]
F12_META_END = "tick_ok"                        # f12.csv: столбцы до `tick_ok` включительно — ключи/служебные, признаки — после
META = ["row_id", "sym", "abs_ms", "win", "side", "src", "level", "level_px_e8", "mask", "ep_start", "base_pass", "n_ep", "end"]


def read_csv(path):
    return pacsv.read_csv(path, convert_options=pacsv.ConvertOptions(strings_can_be_null=False, null_values=[""], column_types={"sym": pa.string(), "side": pa.string(), "ep_id": pa.string(), "univ": pa.string()})).to_pandas()


def rules(rows):
    src = rows["src"].to_numpy()
    tf = np.array([30, 30, 30, 60, 60, 60, 240, 240, 240, 60, 60, 60], dtype=float)
    nb = np.array([1, 2, 3, 1, 2, 3, 1, 2, 3, 0, 0, 0], dtype=float)
    lx = np.array([0] * 9 + [1, 2, 3], dtype=float)
    out = pd.DataFrame({"rules_tf_min": tf[src], "rules_n_bounce": nb[src], "rules_lx": lx[src], "rules_side": float(rows["side"].iloc[0] == "short")}, index=rows.index)
    mask = rows["mask"].to_numpy(dtype=np.int64)
    for i, n in enumerate(SOURCES):
        out["rules_src_" + n] = ((mask >> i) & 1).astype(float)
    out["rules_n_src"] = out[["rules_src_" + n for n in SOURCES]].sum(axis=1)
    return out


def rust_blocks(win, side, key):
    """Rust-выходы → DataFrame по row_id (позиция i = строка i `eps-key.csv`; ключ (sym, sig_ms) сверяется)."""
    d = f"{RUST}/{win}-{side}"
    ek = read_csv(f"{d}/eps-key.csv")
    assert (ek["row_id"].to_numpy() == key["row_id"].to_numpy()).all() or set(ek["row_id"]) == set(key["row_id"])
    out = {}
    # f12 и all (flow+deriv) идут в порядке выхода f12 (не в порядке eps-key): связь по ключу (sym, sig_ms, цена уровня); `ep_id` файла all = «e<позиция строки f12>»
    f12 = read_csv(f"{d}/f12.csv")
    fk = pd.MultiIndex.from_arrays([f12["sym"], f12["sig_ms"], f12["lp_e8"]])
    ekm = pd.MultiIndex.from_arrays([ek["sym"], ek["sig_ms"], ek["level_px_e8"]])
    assert len(f12) == len(ek) and fk.is_unique and ekm.is_unique, "f12: число строк / дубли ключа (sym, sig_ms, lp_e8)"
    pos = ekm.get_indexer(fk)
    assert (pos >= 0).all(), "f12: ключ строки не найден среди eps-key"
    f12_rid = ek["row_id"].to_numpy()[pos]
    cols = list(f12.columns)
    out["f12"] = f12[cols[cols.index(F12_META_END) + 1:]].copy()
    out["f12"].index = f12_rid
    al = read_csv(f"{d}/all.csv")
    ei = al["ep_id"].str.slice(1).astype(np.int64).to_numpy()
    assert len(al) == len(f12) and (ei == np.arange(len(f12))).all(), "all: ep_id не e0…e(n-1) в порядке f12"
    al = al.drop(columns=["ep_id"])
    al.index = f12_rid
    out["flow_deriv"] = al
    for n in ("tcount", "tsize", "tox"):
        x = read_csv(f"{d}/{n}.csv")
        assert len(x) == len(ek) and (x["sym"].to_numpy() == ek["sym"].to_numpy()).all() and (x["sig_ms"].to_numpy() == ek["sig_ms"].to_numpy()).all(), f"{n}: ключи не совпали с eps-key"
        out[n] = x.drop(columns=["sym", "sig_ms"])
        out[n].index = ek["row_id"].to_numpy()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--win", required=True, choices=win_choices())
    ap.add_argument("--side", required=True, choices=("long", "short"))
    ap.add_argument("--out", default=ROOT + "/asm")
    ap.add_argument("--skip-missing", action="store_true", help="проверочный прогон: семейства без файла пропустить (в рабочем прогоне не использовать)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    rows = pd.read_parquet(f"{PROD}/lvl-{a.win}-{a.side}/rows.parquet")
    assert rows["row_id"].is_unique, "G-uni: row_id не уникален"
    assert not rows.duplicated(["sym", "sig_ms", "level", "level_px_e8"]).any(), "G-uni: дубль (sym, sig_ms, level, level_px_e8)"
    rows = rows.set_index("row_id", drop=False)
    fams = {"rules": rules(rows)}
    fams.update(rust_blocks(a.win, a.side, rows))
    for n in PY_FAMS:
        pth = f"{FAM}/{n}/{a.win}-{a.side}.parquet"
        if a.skip_missing and not os.path.exists(pth):
            print("пропуск семейства (нет файла):", n)
            continue
        f = pd.read_parquet(pth)
        if "row_id" in f.columns:
            f = f.set_index("row_id")
        fams[n] = f
    order = [n for n in ["rules", "f12", "flow_deriv", "tcount", "tsize", "tox"] + PY_FAMS if n in fams]
    cov = dict(win=a.win, side=a.side, rows=len(rows), families={}, dups=[], renamed=[])
    cols, seen = {}, {}
    for n in order:
        f = fams[n].reindex(rows.index)
        miss = int(fams[n].index.difference(rows.index).size)
        cov["families"][n] = dict(columns=f.shape[1], missing_rows=int(len(rows) - fams[n].index.isin(rows.index).sum()), extra_rows=miss)
        for c in f.columns:
            v = f[c].to_numpy(dtype=np.float64)
            if c in cols:
                w = cols[c]
                same = np.array_equal(np.isnan(v), np.isnan(w)) and np.allclose(v[~np.isnan(v)], w[~np.isnan(w)], rtol=1e-6, atol=1e-9)
                if same:
                    cov["dups"].append(dict(col=c, dropped_from=n, kept_from=seen[c]))
                    continue
                cov["renamed"].append(dict(col=c, family=n, kept_from=seen[c]))
                c = f"{c}__{n}"
            cols[c] = v
            seen[c] = n
    feat = pd.DataFrame({c: v.astype(np.float32) for c, v in cols.items()}, index=rows.index)
    const = [c for c in feat.columns if feat[c].nunique(dropna=True) <= 1]
    cov["constant_columns"] = const
    cov["nonnan"] = {c: round(float(feat[c].notna().mean()), 4) for c in feat.columns}
    cov["n_features"] = int(feat.shape[1])
    meta = rows[["row_id", "sym", "abs_ms", "win", "side", "src", "level", "level_px_e8", "mask", "ep_start", "base_pass"]].copy()
    meta.columns = ["row_id"] + ["meta_" + c for c in meta.columns[1:]]
    meta["meta_n_ep"] = rows["n_ep"].to_numpy()
    meta["meta_end"] = rows["end"].to_numpy()
    out = pd.concat([meta.reset_index(drop=True), feat.reset_index(drop=True)], axis=1)
    out["sym"] = out["meta_sym"].astype("category")
    out.to_parquet(f"{a.out}/{a.win}-{a.side}.parquet", compression="zstd", index=False)
    cov["seconds"] = round(time.time() - t0, 1)
    with open(f"{a.out}/{a.win}-{a.side}.cov.json", "w", encoding="utf-8") as f:
        json.dump(cov, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: cov[k] for k in ("rows", "n_features", "seconds")}), "dups", len(cov["dups"]), "renamed", len(cov["renamed"]), "const", len(const))


if __name__ == "__main__":
    main()
