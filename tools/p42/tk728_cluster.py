#!/usr/bin/env python3
"""П-42 этап A, шаг 4б (TK-728, §13.78.4): копии признаков — корреляция Спирмена на выборке `tk728_sample.py` (200 000 строк SEL, c539, зерно этапа), иерархическая кластеризация
со средней связью, порог |ρ| ≥ 0,7 (до счёта). Представитель кластера — наибольший охват (число непустых строк выборки), при равенстве — самое короткое окно (`tk728_lib.window_minutes`),
затем меньший номер признака. srv (≤ 2 ядер: потоки BLAS ограничены окружением).

  OMP_NUM_THREADS=2 python3 tk728_cluster.py --sample /data/p42-tk728/res/sample.parquet --features ml_features.json --out /data/p42-tk728/res/cluster.json [--db /data/p42-tk728/res728.sqlite]
Пишет `<out>` (признаки, кластеры, сводка) и таблицы `t728_feat` / `t728_cluster` / `t728_pair` в БД (run_key `A4`)."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk728_lib as L  # noqa: E402
import tk728_db as D  # noqa: E402

KEYS = ["win", "side", "row_id", "sym", "abs_ms"]
RUN = "A4"
# группы оценки протокола (копий ≈ 140 по описаниям, не замер): поток и лента 79, корреляции и беты к BTC / ETH ≈ 30, оборот 7, пустые минуты 6, история пробоев 17
PROTO_GROUPS = {
    "поток и лента": {"flow", "toxic", "fast"},
}


def group_of(name: str, set_id: str) -> str:
    if set_id in ("flow", "toxic", "fast") and name not in L.OI_LS10 and name not in ("fund_last", "fund_chg24", "fund_int", "basis") and not name.startswith(("oi_", "ls_", "dls_", "loi_")):
        return "поток и лента"
    if name in L.HISTORY17:
        return "история пробоев"
    if name.startswith(("c5_", "c15_", "c1_", "cb_", "ce_", "bt_", "cc_", "mk_btc", "mk_eth", "btc_mv", "eth_mv", "mk_rv5", "mk_corr5", "mk_own", "mk_rel")) or name in ("COUP_15m", "COUP_1h", "COUP_4h", "COUP_24h"):
        return "корреляции, беты, ход к BTC / ETH"
    if name in ("turn1h", "turn24h", "lturn24", "TOV", "D", "H", "loi_turn") or name.startswith(("size", "n60", "n24", "n360", "n720", "n1440")):
        return "оборот и число сделок"
    if name.startswith(("emp", "zv", "flat", "cons", "tpm", "gap60")):
        return "пустые минуты, темп"
    return "прочее"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--features", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    t0 = time.time()
    feats = {r["name"]: r for r in L.feature_table(a.features)}
    df = pd.read_parquet(a.sample)
    names = [c for c in df.columns if c not in KEYS]
    X = df[names].to_numpy(np.float64)
    N = len(df)
    cnt = np.isfinite(X).sum(axis=0)
    print("выборка %d строк, признаков %d (%.0f с)" % (N, len(names), time.time() - t0), flush=True)
    rho, npair = L.spearman_pairwise(X)
    print("корреляции готовы (%.0f с)" % (time.time() - t0), flush=True)
    lab = L.cluster_average(rho)
    win = [L.window_minutes(n) for n in names]
    num = [feats[n]["n"] for n in names]
    clusters = {}
    for i, c in enumerate(lab):
        clusters.setdefault(int(c), []).append(i)
    rows_feat, rows_cl, out_feat, out_cl = {}, [], [], []
    for c, mem in sorted(clusters.items()):
        rep, tie = L.pick_rep(mem, cnt, win, num)
        sub = rho[np.ix_(mem, mem)]
        off = sub[~np.eye(len(mem), dtype=bool)] if len(mem) > 1 else np.array([])
        mean_abs = float(np.nanmean(np.abs(off))) if off.size and np.isfinite(off).any() else float("nan")
        out_cl.append(dict(cluster=c, size=len(mem), rep=names[rep], tie=tie, mean_abs_rho=mean_abs, members=[names[i] for i in mem]))
        for i in mem:
            rows_feat[i] = dict(cluster=c, rep=rep, tie=tie, is_rep=int(i == rep))
    for i, nm in enumerate(names):
        r = rows_feat[i]
        out_feat.append(dict(n=num[i], name=nm, set=feats[nm]["set"], cnt=int(cnt[i]), cov=float(cnt[i] / N), window_min=None if math.isinf(win[i]) else win[i],
                             cluster=r["cluster"], rep_name=names[r["rep"]], rep_n=num[r["rep"]], tie=r["tie"], is_rep=r["is_rep"], group=group_of(nm, feats[nm]["set"]),
                             constant=bool(not np.isfinite(rho[i, i]))))
    k = len(clusters)
    copies = len(names) - k
    by_group, by_set = {}, {}
    for f in out_feat:
        if not f["is_rep"]:
            by_group[f["group"]] = by_group.get(f["group"], 0) + 1
            by_set[f["set"]] = by_set.get(f["set"], 0) + 1
    ties = {}
    for c in out_cl:
        if c["size"] > 1:
            ties[c["tie"]] = ties.get(c["tie"], 0) + 1
    summary = dict(sample_rows=N, features_in_sample=len(names), clusters=k, singletons=sum(1 for c in out_cl if c["size"] == 1), copies=copies, copies_by_group=by_group, copies_by_set=by_set,
                   multi_clusters=sum(1 for c in out_cl if c["size"] > 1), tie_levels=ties, constant_columns=[f["name"] for f in out_feat if f["constant"]],
                   pairs_ge_07=int((np.triu(np.abs(np.where(np.isfinite(rho), rho, 0)), 1) >= L.RHO_THR).sum()), rho_thr=L.RHO_THR, seed=L.SEED, seconds=round(time.time() - t0))
    json.dump(dict(summary=summary, features=out_feat, clusters=out_cl), open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.db:
        db = D.connect(a.db)
        D.new_part(db, RUN, ("t728_feat", "t728_cluster", "t728_pair"), "TK-728 шаг 4б: кластеры признаков", "выборка %d строк, |ρ| ≥ %.1f, зерно %d" % (N, L.RHO_THR, L.SEED))
        D.put(db, "t728_feat", RUN, [(f["n"], f["name"], f["set"], 1, f["cnt"], f["cov"], f["window_min"], f["cluster"], f["rep_n"], f["rep_name"], f["tie"], f["is_rep"], None, None) for f in out_feat])
        D.put(db, "t728_cluster", RUN, [(c["cluster"], c["size"], c["rep"], c["tie"], c["mean_abs_rho"], ",".join(c["members"])) for c in out_cl])
        iu, ju = np.triu_indices(len(names), 1)
        okp = np.isfinite(rho[iu, ju])
        D.put(db, "t728_pair", RUN, [(names[i], names[j], float(rho[i, j]), int(npair[i, j])) for i, j in zip(iu[okp], ju[okp])])
        D.text(db, RUN, "cluster_summary", summary)
        db.commit()
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
