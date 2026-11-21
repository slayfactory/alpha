#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), ворота G-pf: реплика занятости портфеля (`tk671_pf.replay`) на сделках двойников баз (`tk671_twin.sh`) даёт те же сделки, что база движка.
  python3 tk671_gate_pf.py --twin <каталог twin-<окно>/out> --win wa --out gate-pf-wa.json [--base642 /data/p42-tk642/cells] [--base668 /data/p42-tk668/e4/cells]
Сравнение по ключу (sym, level, sig_ms): совпавшие сделки / сделки базы, различия, ΔΣ net. Принято: 100 % сделок базы совпали и ΔΣ ≤ 1e-6 (порог записан в протоколе, §10). Код возврата 3 — не принято."""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk671_pf as pf  # noqa: E402

PAIRS = (("L_ind", "long", "{b642}/{win}/L_base"), ("S_ind", "short", "{b642}/{win}/S_base"), ("D_ind", "short", "{b668}/{win}/S_lx1"))


def keyset(df):
    return set(zip(df["sym"], df["level"], df["sig_ms"]))


def run(a):
    res = {"win": a.win, "cells": {}}
    ok = True
    for tw, side, base_dir in PAIRS:
        t = pd.read_csv(os.path.join(a.twin, tw, f"{side}-trades.csv"))
        b = pd.read_csv(os.path.join(base_dir.format(b642=a.base642, b668=a.base668, win=a.win), f"{side}-trades.csv"))
        coins = {c: i for i, c in enumerate(sorted(t["sym"].unique()))}
        acc = pf.replay(t["sig_ms"].to_numpy(), np.arange(len(t)), t["sym"].map(coins).to_numpy(), t["level"].to_numpy(), t["exit_ms"].to_numpy(), (t["kind"] == "stop").to_numpy())
        r = t[acc]
        ks, kb = keyset(r), keyset(b)
        c = {"twin_trades": int(len(t)), "replica_trades": int(len(r)), "base_trades": int(len(b)), "common": len(ks & kb), "only_replica": len(ks - kb), "only_base": len(kb - ks),
             "sum_replica": float(r["net"].sum()), "sum_base": float(b["net"].sum()), "d_sum": float(r["net"].sum() - b["net"].sum())}
        c["pass"] = c["only_replica"] == 0 and c["only_base"] == 0 and abs(c["d_sum"]) <= 1e-6
        ok &= c["pass"]
        res["cells"][tw] = c
    res["pass"] = bool(ok)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: (v if k != "cells" else {c: {x: y for x, y in d.items() if x in ("common", "base_trades", "only_replica", "only_base", "d_sum", "pass")} for c, d in v.items()}) for k, v in res.items()}, ensure_ascii=False))
    return 0 if ok else 3


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--twin", required=True)
    ap.add_argument("--win", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base642", default="/data/p42-tk642/cells")
    ap.add_argument("--base668", default="/data/p42-tk668/e4/cells")
    sys.exit(run(ap.parse_args()))
