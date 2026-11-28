#!/usr/bin/env python3
"""П-42 этап A, шаг 4б (TK-728, §13.78.4): случайные 200 000 строк таблицы подходов SEL (h775 → c539) со столбцами всех признаков списка, у которых столбец есть в данных. srv.

  python3 tk728_sample.py --features <ml_features.json> --out /data/p42-tk728/res/sample.parquet
Источники столбцов (все строки — по ключу `row_id` файла окна; строки таблицы TK-671 — `asm/<окно>-<сторона>.parquet`):
  asm (TK-671: f12, flow/deriv, tcount, tsize, tox, time, bd, fund, t668, coin, hist, bk, mkt) · fast (TK-681: `fast/<окно>-<сторона>/fast.csv` по порядку `eps-key.csv`) · TK-697 (`fam/m697|h697|l697/<окно>-<сторона>.parquet`).
Состав: монеты `symbols-crypto.txt` (c539, В-175) из h775. Выборка — без возвращения, зерно `tk728_lib.SEED`; порядок файлов: wa, wb, wc × long, short. Признак без столбца в данных (ключи движка, сводные ранги, зависящие от базы) в выборку не входит.
Пишет `<out>` (столбцы win, side, row_id, sym, abs_ms + признаки) и `<out>.meta.json` (размер выборки, строк по файлам, список столбцов → признак)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk728_lib as L  # noqa: E402

ASM = "/data/p42-tk671/asm"
FAST = "/data/p42-tk681/fast"
T697 = "/data/p42-tk697/fam"
CRYPTO = "/data/p42-tk621/universe/symbols-crypto.txt"
WINS = ("wa", "wb", "wc")
SIDES = ("long", "short")
KEYS = ["win", "side", "row_id", "sym", "abs_ms"]


def column_map(feats, asm_cols, fast_cols, t697_cols):
    """Признак → (источник, столбец). Приоритет: asm, fast, TK-697; псевдонимы `ALIASES`."""
    out = {}
    for f in feats:
        name = f["name"]
        col = L.ALIASES.get(name, name)
        for src, cols in (("asm", asm_cols), ("fast", fast_cols), ("t697", t697_cols)):
            if col in cols:
                out[name] = (src, col)
                break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--features", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=L.N_SAMPLE)
    a = ap.parse_args()
    t0 = time.time()
    coins = set(x for x in open(CRYPTO, encoding="utf-8").read().replace("\n", ",").split(",") if x)
    feats = L.feature_table(a.features)
    asm_cols = set(pq.ParquetFile(os.path.join(ASM, "wa-long.parquet")).schema_arrow.names)
    fast_cols = set(open(os.path.join(FAST, "wa-long", "fast.csv"), encoding="utf-8").readline().strip().split(","))
    t_cols = set()
    for fam in ("m697", "h697", "l697"):
        t_cols |= set(pq.ParquetFile(os.path.join(T697, fam, "wa-long.parquet")).schema_arrow.names)
    cmap = column_map(feats, asm_cols, fast_cols, t_cols - {"row_id"})
    print("признаков %d, со столбцом %d" % (len(feats), len(cmap)), flush=True)

    # проход 1: размеры файлов после состава c539
    files, sizes = [], []
    for w in WINS:
        for s in SIDES:
            meta = pd.read_parquet(os.path.join(ASM, "%s-%s.parquet" % (w, s)), columns=["row_id", "meta_sym", "meta_abs_ms"])
            keep = meta["meta_sym"].isin(coins).to_numpy()
            files.append((w, s, meta["row_id"].to_numpy()[keep], meta["meta_sym"].to_numpy()[keep], meta["meta_abs_ms"].to_numpy()[keep]))
            sizes.append(int(keep.sum()))
    total = int(sum(sizes))
    rng = np.random.default_rng(L.SEED)
    pick = np.sort(rng.choice(total, size=a.n, replace=False))
    print("строк c539 %d, выборка %d (%.0f с)" % (total, len(pick), time.time() - t0), flush=True)

    parts, off = [], 0
    for (w, s, rid, sym, ams), size in zip(files, sizes):
        loc = pick[(pick >= off) & (pick < off + size)] - off
        off += size
        if len(loc) == 0:
            continue
        rows = pd.DataFrame({"win": w, "side": s, "row_id": rid[loc], "sym": sym[loc], "abs_ms": ams[loc]})
        newc = {}
        need_asm = sorted({c for (src, c) in cmap.values() if src == "asm"})
        base = pd.read_parquet(os.path.join(ASM, "%s-%s.parquet" % (w, s)), columns=["row_id"] + need_asm).set_index("row_id")
        sel = base.loc[rows["row_id"].to_numpy()]
        for name, (src, c) in cmap.items():
            if src == "asm":
                newc[name] = sel[c].to_numpy(np.float64)
        need_fast = sorted({c for (src, c) in cmap.values() if src == "fast"})
        if need_fast:
            key = pd.read_csv(os.path.join(FAST, "%s-%s" % (w, s), "eps-key.csv"), usecols=["row_id", "sym", "sig_ms"])
            fz = pd.read_csv(os.path.join(FAST, "%s-%s" % (w, s), "fast.csv"), usecols=["sym", "sig_ms"] + need_fast)
            if len(key) != len(fz) or not (key["sym"].to_numpy() == fz["sym"].to_numpy()).all() or not (key["sig_ms"].to_numpy() == fz["sig_ms"].to_numpy()).all():
                sys.exit("G-key fast %s-%s: eps-key.csv и fast.csv не совпадают построчно" % (w, s))
            fz.index = key["row_id"].to_numpy()
            fsel = fz.loc[rows["row_id"].to_numpy()]
            for name, (src, c) in cmap.items():
                if src == "fast":
                    newc[name] = fsel[c].to_numpy(np.float64)
        for fam in ("m697", "h697", "l697"):
            cols_f = [c for (src, c) in cmap.values() if src == "t697" and c in pq.ParquetFile(os.path.join(T697, fam, "%s-%s.parquet" % (w, s))).schema_arrow.names]
            if not cols_f:
                continue
            tt = pd.read_parquet(os.path.join(T697, fam, "%s-%s.parquet" % (w, s)), columns=cols_f + (["row_id"] if "row_id" in pq.ParquetFile(os.path.join(T697, fam, "%s-%s.parquet" % (w, s))).schema_arrow.names else []))
            if "row_id" not in tt.columns:
                tt = tt.reset_index().rename(columns={tt.index.name or "index": "row_id"})
            if not tt["row_id"].is_unique:
                sys.exit("G-key %s %s-%s: row_id не уникален" % (fam, w, s))
            tt = tt.set_index("row_id").reindex(rows["row_id"].to_numpy())
            for name, (src, c) in cmap.items():
                if src == "t697" and c in cols_f:
                    newc[name] = tt[c].to_numpy(np.float64)
        rows = pd.concat([rows, pd.DataFrame(newc)], axis=1)
        parts.append(rows)
        print("  %s-%s: %d строк (%.0f с)" % (w, s, len(rows), time.time() - t0), flush=True)
    df = pd.concat(parts, ignore_index=True)
    cols = KEYS + [f["name"] for f in feats if f["name"] in cmap]
    df = df[cols]
    tmp = a.out + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, a.out)
    json.dump(dict(seed=L.SEED, n=int(len(df)), total_c539=total, files={"%s-%s" % (w, s): int(z) for (w, s, *_), z in zip(files, sizes)},
                   colmap={k: list(v) for k, v in cmap.items()}, no_column=[f["name"] for f in feats if f["name"] not in cmap],
                   seconds=round(time.time() - t0)),
              open(a.out + ".meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("готово: %d строк × %d признаков, %.0f с" % (len(df), len(cols) - len(KEYS), time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
