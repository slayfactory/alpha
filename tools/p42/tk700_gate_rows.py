#!/usr/bin/env python3
"""TK-700: ворота строк базы (wa / wb / wc) — пересчёт признаков ТОЛЬКО на строках базы (`base_pass > 0`, вселенная c539) равен прежним столбцам таблицы TK-671 на тех же строках (ключ строки — (монета, abs_ms, цена уровня)).

  python3 tk700_gate_rows.py --mine <asm/wa-long.parquet> --ref /data/p42-tk671/asm/wa-long.parquet --out <gate.json> [--cols-from <список столбцов>] [--rtol 0 --atol 0]

Ссылка — таблица `asm` TK-671 на ВСЕХ строках окна и стороны (775 монет): берутся строки базы со своими монетами (вселенная — монеты `--mine`). Метки не читаются.
Проверки: (1) множество ключей строк базы равно; (2) для каждого общего столбца-признака значения равны (float32: пустые на тех же местах, остальные — байт в байт, допуск по умолчанию 0);
(3) столбцы ссылки, которых нет в `--mine` (семейство не построено на H1), печатаются отдельно: «не построено» — это не расхождение, но попадает в отчёт.
Выход: json (по столбцу: строк, пустых, расходящихся, макс. модуль разности) + код возврата 0 (равно) / 3 (есть расхождения или ключи различаются)."""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

KEY = ["meta_sym", "meta_abs_ms", "meta_level_px_e8"]
SKIP = {"row_id", "sym", "meta_win", "meta_side", "meta_src", "meta_level", "meta_mask", "meta_ep_start", "meta_base_pass", "meta_n_ep", "meta_end"}   # ключ и служебные: проверяются отдельно (src / mask / base_pass — ниже)
STRICT_META = ["meta_src", "meta_mask", "meta_base_pass", "meta_ep_start", "meta_n_ep", "meta_end"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mine", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rtol", type=float, default=0.0)
    ap.add_argument("--atol", type=float, default=0.0)
    ap.add_argument("--allow-diff", default="", help="столбцы, расхождение которых известно и объяснено (через запятую): печатаются, код возврата не меняют")
    a = ap.parse_args(argv)
    mine = pd.read_parquet(a.mine)
    ref = pd.read_parquet(a.ref)
    ref = ref[(ref["meta_base_pass"] > 0) & ref["meta_sym"].isin(set(mine["meta_sym"]))]
    res = {"mine_rows": int(len(mine)), "ref_base_rows": int(len(ref)), "problems": [], "columns": {}, "not_built": [], "allowed": []}
    km, kr = mine.set_index(KEY, drop=False), ref.set_index(KEY, drop=False)
    if not km.index.is_unique or not kr.index.is_unique:
        res["problems"].append("ключ строки не уникален: mine %s ref %s" % (km.index.is_unique, kr.index.is_unique))
    only_m, only_r = km.index.difference(kr.index), kr.index.difference(km.index)
    res["keys_only_mine"], res["keys_only_ref"] = int(len(only_m)), int(len(only_r))
    if len(only_m) or len(only_r):
        res["problems"].append("ключи строк базы различаются: только mine %d, только ref %d" % (len(only_m), len(only_r)))
        res["examples_only_mine"] = [list(map(str, k)) for k in list(only_m)[:5]]
        res["examples_only_ref"] = [list(map(str, k)) for k in list(only_r)[:5]]
    common = km.index.intersection(kr.index)
    km, kr = km.loc[common], kr.loc[common]
    allow = {c for c in a.allow_diff.split(",") if c}
    for c in STRICT_META:
        if c in km.columns and c in kr.columns and not np.array_equal(km[c].to_numpy(), kr[c].to_numpy()):
            res["problems"].append("служебный столбец %s расходится" % c)
    for c in kr.columns:
        if c in SKIP or c in KEY or c in STRICT_META or c.startswith("y_"):
            continue
        if c not in km.columns:
            res["not_built"].append(c)
            continue
        x, y = km[c].to_numpy(dtype=np.float64), kr[c].to_numpy(dtype=np.float64)
        nx, ny = np.isnan(x), np.isnan(y)
        bad_nan = int((nx != ny).sum())
        both = ~nx & ~ny
        d = np.abs(x[both] - y[both])
        tol = a.atol + a.rtol * np.abs(y[both])
        bad_val = int((d > tol).sum())
        res["columns"][c] = {"rows": int(len(x)), "nan_mine": int(nx.sum()), "nan_ref": int(ny.sum()), "bad_nan_pos": bad_nan, "bad_values": bad_val, "max_abs": float(d.max()) if len(d) else 0.0}
        if bad_nan or bad_val:
            (res["allowed"] if c in allow else res["problems"]).append("%s: пустых не на тех местах %d, значений расходится %d (макс. %.3g)" % (c, bad_nan, bad_val, res["columns"][c]["max_abs"]))
    res["compared_columns"] = len(res["columns"])
    res["passed"] = not res["problems"]
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("строк базы mine %d / ref %d, сравнено столбцов %d, не построено %d, расхождений %d → %s" % (res["mine_rows"], res["ref_base_rows"], res["compared_columns"], len(res["not_built"]), len(res["problems"]), "PASS" if res["passed"] else "FAIL"))
    for p in res["problems"][:30]:
        print("  ", p)
    return 0 if res["passed"] else 3


if __name__ == "__main__":
    sys.exit(main())
