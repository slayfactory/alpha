#!/usr/bin/env python3
"""TK-698, пункт 4: почему суммы различаются внутри одного состава при равном числе сделок. Шорт c539 окна 01.07–13.11, клетки `…+s4` из токенов {s4, e20, e2t1/2/3, w225 / w267 / w270 / w275 / w280} с n = 172 (по умолчанию: вход двумя частями e2 = 0,05, w2 = 50; `e20` — одна часть; e2t — часть B за N тиков до уровня; w2 — доля B, % ставки).
По клетке: n, Σ $ (gross − fees, как метрика БД), $ на сделку, ставка в деле на сделку = 500·((100 − w2) + lot2·w2)/100 (у `e20` без e2t — 500), доля исполнения B = mean(lot2), $ на $ в деле, Σ по частям (`lot_net1`, `lot_net2` — net с фандингом), разложение разности Σ против `e20+s4` на одних и тех же 172 сделках: по доле B (lot2 = 0 / 1).
Описательно, найдено на этих данных. Запуск на srv: python3 tk698_item4.py <корень /data/p42-tk698> <run_key>"""
import json
import os
import sys

import numpy as np

import tk698_db as D
import tk698_item3 as I3
import tk698_lib as L

WIN = "01.07–13.11"
ALLOWED = {"s4", "e20", "e2t1", "e2t2", "e2t3", "w225", "w250", "w267", "w270", "w275", "w280"}
N = 172
REF = "e20+s4"


def tokens_w2(cell):
    for t in cell.split("+"):
        if t.startswith("w2") and t[2:].isdigit():
            return int(t[2:])
    return 50


def main(root, run_key):
    db = D.connect()
    D.new_run(db, run_key, "item4", "tools/p42/tk698_item4.py", "e20 против e2t / w2 в составе s4 (172 сделки): ставка в деле, доля B, $ на $; описательно, найдено на этих данных")
    chron = L.chron_ro()
    best = {}
    for cid, cell, n in chron.execute("select id, cell, trades from metric where side = 'short' and uni = 'c539' and win = ? and trades = ?", (WIN, N)):
        t = set(cell.split("+"))
        if "s4" in t and t <= ALLOWED and (cell not in best or cid > best[cell]):
            best[cell] = cid
    cells = ["s4"] + sorted(c for c in best if c != "s4")
    assert "s4" in best and REF in best, sorted(best)
    D.put(db, "res_text", run_key, [("cells", json.dumps(cells, ensure_ascii=False))], ["key", "value"])
    rows = {c: I3.row_by_id(chron, best[c], c) for c in cells}
    I3.prefetch(list(rows.values()))
    data = {}
    for c, r in rows.items():
        tr = L.load_cell(r, lots=True)
        assert len(tr) == N and abs(tr["val"].sum() - r["usd"]) < 1e-5, ("охрана n / Σ", c)
        tr["key"] = tr["sym"].astype(str) + "|" + tr["level"].astype(str) + "|" + tr["sig_ms"].astype(str)
        assert tr["key"].is_unique, ("ключ (sym, level, sig_ms) неуникален", c)
        data[c] = tr
    ref = data[REF].set_index("key")
    linerows, split = [], []
    for c in cells:
        tr = data[c]
        t = set(c.split("+"))
        w2 = tokens_w2(c)
        single = "e20" in t and not any(x.startswith("e2t") for x in t)
        v = tr["val"].to_numpy()
        lot2 = tr["lot_lot2"].to_numpy(np.float64) if "lot_lot2" in tr.columns else np.zeros(len(tr))
        stake = np.full(len(tr), 500.0) if single else 500.0 * ((100 - w2) + lot2 * w2) / 100.0
        d = L.daily_pnl(tr)
        m = L.metrics(d)
        net1 = tr["lot_net1"].to_numpy() if "lot_net1" in tr.columns else tr["net"].to_numpy()          # нет `*-lots.csv` — одна часть, вся сделка в части A
        net2 = tr["lot_net2"].to_numpy() if "lot_net2" in tr.columns else np.zeros(len(tr))
        extra = dict(single_part=single, w2=w2, fill_b=float(lot2.mean()), n_b0=int((lot2 == 0).sum()), stake_mean=float(stake.mean()), stake_sum=float(stake.sum()),
                     usd_per_stake=float(v.sum() / stake.sum()), usd_per_500=float(v.sum() / len(tr)), net_parts=dict(a=float(net1.sum()), b=float(net2.sum()), funding=float(tr["funding"].sum())),
                     stake_a=float((500.0 * (100 - w2) / 100.0) * len(tr)) if not single else float(500.0 * len(tr)), stake_b=0.0 if single else float((500.0 * w2 / 100.0 * lot2).sum()),
                     kinds={k: int(n) for k, n in tr["kind"].value_counts().items()}, kind2={str(k): int(n) for k, n in tr["lot_kind2"].fillna("-").value_counts().items()} if "lot_kind2" in tr.columns else None,
                     takes_mean=float(tr["takes"].mean()), fees=float(tr["fees"].sum()), gross=float(tr["gross"].sum()))
        linerows.append(("I4|" + c, c, "short", len(tr), m["usd"], m["usd"] / len(tr), m["sharpe"], m["dd"], m["dd_pct"], m["gap_longest"], m["gap_mean"], m["gap_tail"], L.rr(tr["val"]), extra))
        if c == REF:
            continue
        x = tr.set_index("key")
        com = x.index.intersection(ref.index)
        dv = (x.loc[com, "val"] - ref.loc[com, "val"]).to_numpy()
        l2 = x.loc[com, "lot_lot2"].to_numpy(np.float64) if "lot_lot2" in x.columns else np.zeros(len(com))
        split.append(("item4", "I4|" + c, "vs_e20", "all", "common", 0, int(len(com)), float(dv.sum()), float(dv.mean()), None, float(len(com) / N), dict(only_here=int(len(x) - len(com)), only_ref=int(len(ref) - len(com)))))
        for nm, mm in (("b_filled", l2 > 0), ("b_not_filled", l2 == 0)):
            split.append(("item4", "I4|" + c, "vs_e20", "all", nm, 0, int(mm.sum()), float(dv[mm].sum()), float(dv[mm].mean()) if mm.any() else None, None, float(mm.mean()), None))
        kk = x.loc[com, "kind"].to_numpy() != ref.loc[com, "kind"].to_numpy()
        split.append(("item4", "I4|" + c, "vs_e20", "all", "kind_changed", 0, int(kk.sum()), float(dv[kk].sum()), float(dv[kk].mean()) if kk.any() else None, None, float(kk.mean()), None))
    D.put(db, "res_line", run_key, linerows, ["line", "cell", "side", "n", "usd", "per_trade", "sharpe", "dd", "dd_pct", "gap_longest", "gap_mean", "gap_tail", "rr", "extra"])
    D.put(db, "res_split", run_key, split, ["item", "line", "dim", "market", "bin", "n_days", "n", "usd", "per_trade", "per_day", "share", "extra"])
    print("клеток %d, срезов %d" % (len(linerows), len(split)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
