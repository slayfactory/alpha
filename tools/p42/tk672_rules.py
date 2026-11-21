#!/usr/bin/env python3
"""П-42 §13.72 (TK-672), шаг rules: 8 столбцов правил базы `rl_*` к набору TK-671 (prereg-13.72.md §3) и ворота G-rule.

  python3 tk672_rules.py --side long|short [--src /data/p42-tk671/data] [--out /data/p42-tk672/data]

Вход — `<src>/<side>.parquet` (набор TK-671, 306 признаков + `meta_*` + `y_*`), выход — тот же файл со столбцами `rl_*` в конце (float32, NaN → 0), порядок строк не меняется;
рядом `<out>/<side>.rules.json` (числа G-rule). Источники — только признаки и флаги на момент сигнала; метки не читаются.
Красный G-rule (i) — код 3, определение не меняется."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

RL = ("rl_base", "rl_ref", "rl_c_natr", "rl_c_t1h", "rl_c_t24", "rl_c_n24", "rl_n_base", "rl_n_ref")
NATR_MIN, T1H_MIN, T24_MIN, N24_MIN = 40.0, 500_000.0, 12_000_000.0, 220_000.0
NEED = ("meta_base_pass", "natr_bp", "turn1h", "turn24h", "rules_src_p60n2", "rules_src_lxD")


def _f(a):
    return np.asarray(a, dtype=np.float64)


def rl_columns(cols, side):
    """`cols` — словарь массивов (NEED + опционально `n24`); результат — словарь 8 массивов float32. NaN в сравнении даёт 0."""
    bp = np.asarray(cols["meta_base_pass"]).astype(np.int64)
    natr, t1h, t24 = _f(cols["natr_bp"]), _f(cols["turn1h"]), _f(cols["turn24h"])
    n24 = _f(cols["n24"]) if cols.get("n24") is not None else np.full(len(bp), np.nan)
    p60 = (_f(cols["rules_src_p60n2"]) > 0.5)
    lxd = (_f(cols["rules_src_lxD"]) > 0.5)
    base = (bp & 1) > 0
    c_natr, c_t1h, c_t24 = natr >= NATR_MIN, t1h >= T1H_MIN, t24 >= T24_MIN
    c_n24 = n24 >= N24_MIN
    n_base = p60.astype(np.int64) + c_natr + c_t1h + c_t24
    if side == "long":
        ref = base & c_n24
        n_ref = n_base + c_n24
    elif side == "short":
        ref = (bp & 2) > 0
        n_ref = lxd.astype(np.int64) + c_natr + c_t1h + c_t24
    else:
        raise ValueError(side)
    out = dict(rl_base=base, rl_ref=ref, rl_c_natr=c_natr, rl_c_t1h=c_t1h, rl_c_t24=c_t24, rl_c_n24=c_n24, rl_n_base=n_base, rl_n_ref=n_ref)
    return {k: np.asarray(out[k], dtype=np.float32) for k in RL}


def g_rule(cols, rl, side):
    """G-rule (§3 протокола): (i) rl_base = 1 → rl_n_base = 4; rl_ref = 1 → rl_n_ref полный (лонг 5, шорт 4); (ii) rl_base / rl_ref равны битам по построению (счёт строк);
    (iii) доля флага среди строк с rl_n_base = 4. Возвращает словарь; `ok` — условия (i) и (ii)."""
    bp = np.asarray(cols["meta_base_pass"]).astype(np.int64)
    base, ref = rl["rl_base"] > 0.5, rl["rl_ref"] > 0.5
    full_ref = 5 if side == "long" else 4
    n4 = rl["rl_n_base"] >= 4
    res = dict(side=side, rows=int(len(bp)), base_rows=int(base.sum()), ref_rows=int(ref.sum()),
               base_n4=int((rl["rl_n_base"][base] == 4).sum()), ref_full=int((rl["rl_n_ref"][ref] == full_ref).sum()), ref_full_need=full_ref,
               base_equals_bit1=bool((((bp & 1) > 0) == base).all()),
               ref_equals_def=bool((ref == (((bp & 2) > 0) if side == "short" else (base & (rl["rl_c_n24"] > 0.5)))).all()),
               rows_n_base_4=int(n4.sum()), flag_share_among_n_base_4=float(base[n4].mean()) if n4.any() else float("nan"),
               rows_n_ref_full=int((rl["rl_n_ref"] >= full_ref).sum()), ref_share_among_n_ref_full=float(ref[rl["rl_n_ref"] >= full_ref].mean()) if (rl["rl_n_ref"] >= full_ref).any() else float("nan"))
    res["ok"] = bool(res["base_n4"] == res["base_rows"] and res["ref_full"] == res["ref_rows"] and res["base_equals_bit1"] and res["ref_equals_def"])
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--side", required=True, choices=("long", "short"))
    ap.add_argument("--src", default="/data/p42-tk671/data")
    ap.add_argument("--out", default="/data/p42-tk672/data")
    a = ap.parse_args(argv)
    src = os.path.join(a.src, "%s.parquet" % a.side)
    dst = os.path.join(a.out, "%s.parquet" % a.side)
    os.makedirs(a.out, exist_ok=True)
    names = pq.read_schema(src).names
    have_n24 = "n24" in names
    t = pq.read_table(src)
    cols = {c: t.column(c).to_numpy(zero_copy_only=False) for c in NEED + (("n24",) if have_n24 else ())}
    rl = rl_columns(cols, a.side)
    rep = g_rule(cols, rl, a.side)
    rep["n24_in_data"] = bool(have_n24)
    print("G-rule %s: %s" % (a.side, json.dumps(rep, ensure_ascii=False)), flush=True)
    with open(os.path.join(a.out, "%s.rules.json" % a.side), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
        f.write("\n")
    if not rep["ok"]:
        print("G-rule КРАСНЫЙ — стоп, отчёт; определение не меняется", flush=True)
        return 3
    for k in RL:
        if k in names:
            raise SystemExit("в наборе уже есть столбец %s" % k)
        t = t.append_column(pa.field(k, pa.float32()), pa.array(rl[k], type=pa.float32()))
    pq.write_table(t, dst, compression="zstd", row_group_size=max(t.num_rows, 1))
    print("записано %s: строк %d, столбцов %d" % (dst, t.num_rows, t.num_columns), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
