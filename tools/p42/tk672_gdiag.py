#!/usr/bin/env python3
"""П-42 §13.72 (TK-672), G-diag (prereg-13.72.md §11): описательно перемеряет диагноз координатора на выходах TK-671; решений не двигает.

  python3 tk672_gdiag.py [--data /data/p42-tk671/data] [--fit /data/p42-tk671/fit] [--out /data/p42-tk672/report]

(а) доля строк с `rl_base` (бит 1 `meta_base_pass`; у шорта справкой бит 2 `D_S`) в наборе по месяцам (июль–ноябрь);
(б) средний `y_net` строк с флагом против прочих по месяцам проверки (только строки с меткой);
(в) процентиль `s_hat` флаговых строк внутри месяца среди всех строк месяца и доля флаговых строк с `s_hat > 0` (против прочих);
(г) суммарная важность семейства `rules_*` у моделей TK-671 (PredictionValuesChange, регрессор и классификатор по месяцам).
Метки месяцев проверки читаются: запуск — только после `chron.py psec add --prereg` §13.72."""
from __future__ import annotations

import argparse
import calendar
import json
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

MONTHS = ("2026-07", "2026-08", "2026-09", "2026-10", "2026-11")


def _ms(y, m, d=1):
    return calendar.timegm((y, m, d, 0, 0, 0)) * 1000


EDGES = np.array([_ms(2026, 7), _ms(2026, 8), _ms(2026, 9), _ms(2026, 10), _ms(2026, 11), _ms(2026, 11, 14)], dtype=np.int64)
SIDES = ("long", "short")


def month_idx(ms):
    return np.searchsorted(EDGES, np.asarray(ms), side="right") - 1


def descr_side(side, data_dir, fit_dir):
    d = pq.read_table(os.path.join(data_dir, "%s.parquet" % side), columns=["win", "row_id", "meta_abs_ms", "meta_base_pass", "y_has", "y_net"]).to_pandas()
    d["win"] = d["win"].astype(str)
    mi = month_idx(d["meta_abs_ms"].to_numpy())
    assert mi.min() >= 0 and mi.max() <= 4, "строки вне 01.07–13.11"
    d["m"] = mi
    d["flag"] = (d["meta_base_pass"].to_numpy() & 1) > 0
    d["flag2"] = (d["meta_base_pass"].to_numpy() & 2) > 0
    out = {"side": side}
    # (а)
    a = []
    for k, lab in enumerate(MONTHS):
        s = d[d["m"] == k]
        a.append(dict(month=lab, rows=int(len(s)), flag_rows=int(s["flag"].sum()), flag_share=float(s["flag"].mean()), flag2_rows=int(s["flag2"].sum()), flag2_share=float(s["flag2"].mean())))
    out["a"] = a
    out["a_total"] = dict(rows=int(len(d)), flag_rows=int(d["flag"].sum()), flag_share=float(d["flag"].mean()), flag2_rows=int(d["flag2"].sum()))
    # (б)
    lab = d[d["y_has"].astype(bool) & np.isfinite(d["y_net"])]
    b = []
    for k, name in enumerate(MONTHS):
        if k == 0:
            continue
        s = lab[lab["m"] == k]
        f, o = s[s["flag"]], s[~s["flag"]]
        b.append(dict(month=name, n_flag=int(len(f)), mean_flag=float(f["y_net"].mean()) if len(f) else None, n_other=int(len(o)), mean_other=float(o["y_net"].mean()), share_pos_flag=float((f["y_net"] > 0).mean()) if len(f) else None,
                      share_pos_other=float((o["y_net"] > 0).mean())))
    s = lab[lab["m"] >= 1]
    f, o = s[s["flag"]], s[~s["flag"]]
    out["b"] = b
    out["b_total"] = dict(n_flag=int(len(f)), mean_flag=float(f["y_net"].mean()), n_other=int(len(o)), mean_other=float(o["y_net"].mean()), share_pos_flag=float((f["y_net"] > 0).mean()), share_pos_other=float((o["y_net"] > 0).mean()))
    # (в)
    pred = pd.read_parquet(os.path.join(fit_dir, side, "pred.parquet"), columns=["win", "row_id", "month", "s_hat"])
    pred["win"] = pred["win"].astype(str)
    m = pred.merge(d[["win", "row_id", "flag", "meta_abs_ms"]], on=["win", "row_id"], how="inner", validate="one_to_one")
    c = []
    for name in MONTHS[1:]:
        s = m[m["month"] == name]
        pct = s["s_hat"].rank(pct=True).to_numpy()
        fl = s["flag"].to_numpy()
        c.append(dict(month=name, rows=int(len(s)), flag_rows=int(fl.sum()), mean_percentile_flag=float(pct[fl].mean() * 100) if fl.any() else None,
                      share_top10_flag=float((pct[fl] > 0.9).mean()) if fl.any() else None, share_top1_flag=float((pct[fl] > 0.99).mean()) if fl.any() else None,
                      share_pos_flag=float((s["s_hat"].to_numpy()[fl] > 0).mean()) if fl.any() else None, share_pos_other=float((s["s_hat"].to_numpy()[~fl] > 0).mean())))
    out["c"] = c
    # (г)
    g = []
    for name in MONTHS[1:]:
        p = os.path.join(fit_dir, side, "importance-%s.json" % name)
        if not os.path.exists(p):
            continue
        imp = json.load(open(p, encoding="utf-8"))
        row = {"month": name}
        for kind in ("reg", "clf"):
            if kind in imp:
                fam = imp[kind]["families"]
                row[kind + "_rules_pct"] = float(fam.get("rules", 0.0))
                feats = imp[kind]["features"]
                row[kind + "_top"] = [(k, round(v, 2)) for k, v in list(feats.items())[:3]]
        g.append(row)
    out["d"] = g
    return out


def fmt(res):
    def pc(x, nd=1):
        return "—" if x is None else ("%.*f %%" % (nd, x * 100))
    L = ["# G-diag (§13.72 п. 11): диагноз TK-671, перемерено\n"]
    for r in res:
        side = {"long": "лонг", "short": "шорт"}[r["side"]]
        L.append("## %s\n" % side)
        L.append("(а) доля строк с `rl_base` (бит 1), набор TK-671; бит 2 (`D_S`) — справкой")
        L.append("| месяц | строк | с `rl_base` | доля | бит 2 | доля бита 2 |\n|---|---|---|---|---|---|")
        for x in r["a"]:
            L.append("| %s | %d | %d | %s | %d | %s |" % (x["month"], x["rows"], x["flag_rows"], pc(x["flag_share"], 3), x["flag2_rows"], pc(x["flag2_share"], 3)))
        t = r["a_total"]
        L.append("| всего | %d | %d | %s | %d | |\n" % (t["rows"], t["flag_rows"], pc(t["flag_share"], 3), t["flag2_rows"]))
        L.append("(б) средний `y_net` (строки с меткой): флаг против прочих, и доля «в плюс»")
        L.append("| месяц | с флагом n | средний y_net | прочих n | средний y_net | доля >0 флаг | доля >0 прочие |\n|---|---|---|---|---|---|---|")
        for x in r["b"] + [dict(r["b_total"], month="авг–ноя")]:
            L.append("| %s | %d | %s | %d | %+.4f | %s | %s |" % (x["month"], x["n_flag"], "—" if x["mean_flag"] is None else "%+.4f" % x["mean_flag"], x["n_other"], x["mean_other"], pc(x["share_pos_flag"]), pc(x["share_pos_other"])))
        L.append("\n(в) `s_hat` TK-671 у флаговых строк: процентиль внутри месяца среди всех строк месяца")
        L.append("| месяц | строк | флаговых | средний процентиль | доля в верхних 10 % | в верхнем 1 % | доля `s_hat > 0` флаг | прочие |\n|---|---|---|---|---|---|---|---|")
        for x in r["c"]:
            L.append("| %s | %d | %d | %s | %s | %s | %s | %s |" % (x["month"], x["rows"], x["flag_rows"], "—" if x["mean_percentile_flag"] is None else "%.1f" % x["mean_percentile_flag"], pc(x["share_top10_flag"]), pc(x["share_top1_flag"]), pc(x["share_pos_flag"]), pc(x["share_pos_other"])))
        L.append("\n(г) важность семейства `rules_*` у моделей TK-671 (PredictionValuesChange, % от суммы)")
        L.append("| месяц | регрессор | классификатор | верх регрессора |\n|---|---|---|---|")
        for x in r["d"]:
            L.append("| %s | %.2f %% | %s | %s |" % (x["month"], x.get("reg_rules_pct", float("nan")), "%.2f %%" % x["clf_rules_pct"] if "clf_rules_pct" in x else "—", ", ".join("%s %.1f" % t for t in x.get("reg_top", []))))
        L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default="/data/p42-tk671/data")
    ap.add_argument("--fit", default="/data/p42-tk671/fit")
    ap.add_argument("--out", default="/data/p42-tk672/report")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    res = [descr_side(s, a.data, a.fit) for s in SIDES]
    with open(os.path.join(a.out, "gdiag.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    md = fmt(res)
    with open(os.path.join(a.out, "gdiag.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
