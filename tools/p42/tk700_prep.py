#!/usr/bin/env python3
"""TK-700: строки базы и входы Rust-признаков для отложенной выборки (окна m01…m06) и ворот (wa …). Без движка и без P&L.

  python3 tk700_prep.py base  --lvl <каталог lvl полный> --out <prod/lvl-<окно>-<side>> [--root-full <каталог>]
      из `rows.parquet` шага `tk671_lvl.py build` оставляет строки базы (`base_pass > 0`): `row_id` не меняется (ключ строки внутри окна и стороны), порядок прежний. Полная таблица остаётся в `--lvl`.
  python3 tk700_prep.py feat-in --rows <rows.parquet> --out <каталог>
      вход `alpha-feat` (как tk671_prep + tk699_f12tf prep --which all): sigsx.csv (+ tf_min по источнику), eps-ts.csv, eps-key.csv (row_id, sym, sig_ms, level_px_e8, src, tf_min), eps-tox.csv (+ side при --side)

Признаки TK-674 читают только строки базы (`meta_base_pass > 0`): 357 / 255 / 299 (лонг) и 1 094 / 618 / 933 (шорт) строк на wa / wb / wc против 195–314 тыс. строк полной таблицы.
Равенство признаков на строках базы тем же признакам полной таблицы TK-671 проверяют ворота `tk700_gate_rows.py` (wa)."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

SRC_TF = np.array([30, 30, 30, 60, 60, 60, 240, 240, 240, 60, 60, 60], dtype=np.int64)       # как `rules_tf_min` в tk671_assemble и `SRC_TF` в tk699_f12tf


def cmd_base(a):
    r = pd.read_parquet(os.path.join(a.lvl, "rows.parquet"))
    b = r[r["base_pass"] > 0]
    os.makedirs(a.out, exist_ok=True)
    b.to_parquet(os.path.join(a.out, "rows.parquet"), index=False)
    for f in ("lvl.json",):
        if os.path.exists(os.path.join(a.lvl, f)):
            with open(os.path.join(a.lvl, f), encoding="utf-8") as src, open(os.path.join(a.out, f), "w", encoding="utf-8", newline="\n") as dst:
                dst.write(src.read())
    info = {"rows_full": int(len(r)), "rows_base": int(len(b)), "coins": int(b["sym"].nunique()), "base_bits": {str(k): int(v) for k, v in b["base_pass"].value_counts().sort_index().items()}}
    with open(os.path.join(a.out, "base.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps(info))


def cmd_feat_in(a):
    r = pd.read_parquet(a.rows).sort_values(["sym", "sig_ms", "level"], kind="stable").reset_index(drop=True)
    empty = r["members"].fillna("") == ""                  # lx: у уровня источника журнала членов нет — одна цена на баре first_bar (как tk671_prep)
    r["members"] = r["members"].where(~empty, r["first_bar"].astype("int64").astype(str) + ":" + r["level_px_e8"].astype("int64").astype(str))
    r["tf_min"] = SRC_TF[r["src"].to_numpy(dtype=np.int64)]
    os.makedirs(a.out, exist_ok=True)
    r[["sym", "level", "sig_ms", "dist_e8", "level_px_e8", "born", "end", "first_bar", "natr_e8", "members", "tf_min"]].to_csv(os.path.join(a.out, "sigsx.csv"), index=False)
    r[["sym", "sig_ms"]].to_csv(os.path.join(a.out, "eps-ts.csv"), index=False)
    r[["row_id", "sym", "sig_ms", "level_px_e8", "src", "tf_min"]].to_csv(os.path.join(a.out, "eps-key.csv"), index=False)
    print(len(r), r["sym"].nunique())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("base")
    b.add_argument("--lvl", required=True)
    b.add_argument("--out", required=True)
    f = sp.add_parser("feat-in")
    f.add_argument("--rows", required=True)
    f.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    {"base": cmd_base, "feat-in": cmd_feat_in}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
