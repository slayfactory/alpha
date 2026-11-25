#!/usr/bin/env python3
"""П-42 TK-690, шаг sac: пустоты `sac10/30/60` — определение или ошибка (счёт в счёт по окнам).

  python3 tk690_sac.py run      — движок признаков: `alpha-feat sacd` (независимый линейный счёт записей и покупок окон 10 / 30 / 60 с до σ) → fam/rust/<окно>-short/sacd.csv
  python3 tk690_sac.py compare  — пустота столбцов `sac*` из fast.csv против правила: окно не покрыто данными (σ − W < начало загруженных) ∪ записей окна < 30 ∪ все записи одной стороны
Правило — `tox::sign_autocorr` + `fast_features` (`ok(w_ms)`): три причины NaN. Равенство множеств = «пусто по определению»; расхождение любого знака = ошибка.
Среда: TK690_ROOT (по умолчанию /data/p42-tk690), TK690_SACD_BIN, TK690_THREADS (по умолчанию 2).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

ROOT = os.environ.get("TK690_ROOT", "/data/p42-tk690")
BIN = os.environ.get("TK690_SACD_BIN", "/data/p42-tk690/bin/alpha-feat-sacd")
STORE = "/data/p42-tk599/tick-store"
T0 = {"wa": "2026-06-17", "wb": "2026-07-25", "wc": "2026-09-01"}
SN = "short"
W_S = (10, 30, 60)
MIN_N = 30                                  # SAC_MIN_N, `tox.rs`


def rust_dir(win):
    return os.path.join(ROOT, "fam", "rust", "%s-%s" % (win, SN))


def classify(fast, sacd):
    """{W: счётчики} — строки `fast` и `sacd` в одном порядке (один eps-tox.csv); категории не пересекаются: unc → few → one."""
    if len(fast) != len(sacd) or not (fast["sym"].to_numpy() == sacd["sym"].to_numpy()).all() or not (fast["sig_ms"].to_numpy() == sacd["sig_ms"].to_numpy()).all():
        raise SystemExit("fast.csv и sacd.csv: строки не совпадают по (sym, sig_ms) и порядку")
    gap = sacd["gap"].to_numpy(np.float64)
    out = {}
    for w in W_S:
        n = sacd["n%d" % w].to_numpy(np.float64)
        nb = sacd["nb%d" % w].to_numpy(np.float64)
        nan = fast["sac%d" % w].isna().to_numpy()
        unc = gap < w * 1000
        few = ~unc & (n < MIN_N)
        one = ~unc & (n >= MIN_N) & ((nb == 0) | (nb == n))
        rule = unc | few | one
        out[w] = dict(rows=int(len(nan)), nan=int(nan.sum()), share=round(float(nan.mean()), 4),
                      uncovered=int(unc.sum()), few=int(few.sum()), one_side=int(one.sum()), rule=int(rule.sum()),
                      nan_not_rule=int((nan & ~rule).sum()), rule_not_nan=int((~nan & rule).sum()),
                      nan_in_unc=int((nan & unc).sum()), nan_in_few=int((nan & few).sum()), nan_in_one=int((nan & one).sum()),
                      n0=int(((n == 0) & ~unc).sum()), finite_mixed=int((~nan & ~rule).sum()), equal=bool((nan == rule).all()))
    return out


def cmd_run(a):
    for win, t0 in T0.items():
        d = rust_dir(win)
        out = os.path.join(d, "sacd.csv")
        if os.path.exists(out):
            continue
        cmd = [BIN, "sacd", "--eps", os.path.join(d, "eps-tox.csv"), "--out", out, "--base-date", t0, "--trades", "/data/p42-tk621/%s/aggtrades" % win,
               "--store", STORE, "--threads", os.environ.get("TK690_THREADS", "2")]
        print(" ".join(cmd), flush=True)
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print(r.stdout[-600:], flush=True)
        if r.returncode:
            raise SystemExit("sacd %s: rc=%d" % (win, r.returncode))


def cmd_compare(a):
    res, ok = {}, True
    for win in T0:
        d = rust_dir(win)
        res[win] = classify(pd.read_csv(os.path.join(d, "fast.csv")), pd.read_csv(os.path.join(d, "sacd.csv")))
    for w in W_S:
        for k in ("rows", "nan", "uncovered", "few", "one_side", "rule", "nan_not_rule", "rule_not_nan", "nan_in_unc", "nan_in_few", "nan_in_one", "n0", "finite_mixed"):
            res.setdefault("sum", {}).setdefault(str(w), {})[k] = sum(res[x][w][k] for x in T0)
        res["sum"][str(w)]["share"] = round(res["sum"][str(w)]["nan"] / res["sum"][str(w)]["rows"], 4)
        res["sum"][str(w)]["equal"] = all(res[x][w]["equal"] for x in T0)
        ok &= res["sum"][str(w)]["equal"]
    res["verdict"] = "определение" if ok else "ошибка"
    out = os.path.join(ROOT, "res", "sac690.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in res.items()}, f, ensure_ascii=False, indent=1)
    print("окно  W   строк   пусто  доля   unc  <30  1 сторона  правило  NaN∖правило  правило∖NaN")
    for win in list(T0) + ["sum"]:
        for w in W_S:
            r = res[win][w] if win != "sum" else res["sum"][str(w)]
            print("%-4s %3d %6d %7d %5.1f%% %5d %5d %8d %8d %8d %10d" % (win, w, r["rows"], r["nan"], 100 * r["share"], r["uncovered"], r["few"], r["one_side"], r["rule"], r["nan_not_rule"], r["rule_not_nan"]))
    print("вердикт:", res["verdict"], "→", out)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("run")
    sp.add_parser("compare")
    a = ap.parse_args()
    {"run": cmd_run, "compare": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
