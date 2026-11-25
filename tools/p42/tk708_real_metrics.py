#!/usr/bin/env python3
"""TK-708: метрики лонг-линий (n, Σ $, $/сделку, Шарп годовой по суточному ряду, просадка $ и % маржи 250 $, перехай) по выходам движка — те же функции, что у отбора L-426 (`tk674_stats.load` / `metrics`).
Выходы — `<root>/out/<окно>/<vid>/long-{trades,daily}.csv` (корень ворот `tk708_gate.py`; `replay --real` — настоящие значения признаков, без чтения пустыми строк вне parquet).
Запуск на srv в среде отбора: `source /data/p42-tk674c/env-s4.sh; python3 tk708_real_metrics.py --root /data/p42-tk708/real --cells L1,L2 --gate-replay /data/p42-tk708/gate-replay.json --out m.json`.
Сверка с шлюзом TK-674 (`tk674-gate-cells.json`, `metrics.trades / usd / sharpe`) печатается по каждой линии, если `--ref-gate` задан."""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"))


def line_metrics(root, vid, st, p8, u, side="L"):
    import tk674_stats as ms
    dirs = {w.name: (os.path.join(root, "out", w.name, vid), side) for w in st.TRADE_WINDOWS}
    ser, kinds, pnl = ms.load(u, side, dirs)
    return ms.metrics(ser, kinds, pnl)


def brief(m):
    return {"n": m["n"], "usd": m["usd"], "mean_usd": m["mean_usd"], "sharpe": m["sharpe"], "maxdd": m["maxdd"], "maxdd_pct": m["maxdd_pct"],
            "gap_longest": m["kpi"]["gap_longest"], "gap_mean": m["kpi"]["gap_mean"], "gap_tail": m["kpi"]["gap_tail"], "rr": m["rr"]["rr"], "worst": m["lmax"]["worst"],
            "worst_pct_margin": m["lmax"]["worst_pct_margin"], "months": m["months"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    ap.add_argument("--cells", default="L1,L2")
    ap.add_argument("--gate-replay", required=True, help="gate-replay.json — vid линий")
    ap.add_argument("--ref-gate", default=os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk674-gate-cells.json"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    import tk643_run as t
    st, r5, p8, _ = t._srv()
    u = r5.Uni(os.environ.get("TK674_UNI", "c539"))
    vids = {i: x["vid"] for i, x in json.load(open(a.gate_replay, encoding="utf-8"))["cells"].items()}
    ref = json.load(open(a.ref_gate, encoding="utf-8"))["cells"] if os.path.exists(a.ref_gate) else {}
    res = {}
    for i in a.cells.split(","):
        m = line_metrics(a.root, vids[i], st, p8, u)
        res[i] = brief(m)
        r = (ref.get(i) or {}).get("metrics") or {}
        print("%s: n %d, Σ %+.2f $, $/сделку %+.3f, Шарп %.3f, просадка %.2f $ (%.1f %% маржи), перехай %d сут. (средний %.1f, хвост %d), R:R %.2f, худшая %.2f $; шлюз TK-674: n %s, Σ %s, Шарп %s" % (
            i, m["n"], m["usd"], m["mean_usd"], m["sharpe"], m["maxdd"], m["maxdd_pct"], m["kpi"]["gap_longest"], m["kpi"]["gap_mean"], m["kpi"]["gap_tail"], m["rr"]["rr"], m["lmax"]["worst"],
            r.get("trades"), r.get("usd"), r.get("sharpe")), flush=True)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    return 0


if __name__ == "__main__":
    sys.exit(main())
