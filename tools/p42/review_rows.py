#!/usr/bin/env python3
"""TK-745: строки сделок набора «30 дней» из свежего прогона движка (`review_loop.sh`) — в формате `res/trades.json` вкладки «Сделки» (tk741_cluster.cmd_run). Только srv.

  python3 review_rows.py <каталог прогона> <метка> <первые сутки ISO> <последние сутки ISO> [<ключи> <бинарь> <md5>]
    → <каталог>/res/trades.json (№ сделок с 1000, период D30), <каталог>/meta.json, <каталог>/summary.json; печатает «сделок, Σ $».

Сделки — окна wc (03.10–13.11) и n1 (14.11–24.11) прогона, отобранные по суткам входа (UTC, как `entry_day` движка). Признаки — `tk741_cluster.features` (только минутки ≤ t−1); кластер — ближайший центр
уже подогнанных кластеров TK-741 (`res/cluster.json`: mu, sd, центры z; кластеры не пересчитываются, «найдено на этих данных» на сделки набора не распространяется).
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk741_lib as T  # noqa: E402
import tk741_cluster as C  # noqa: E402
from review_cells import CID, WINS  # noqa: E402

I0 = 1000      # № сделок набора в index.json (прежние — 0…531)


def main(root, tag, lo, hi, keys="", binp="", md5=""):
    parts = [T.read_win("nt", w, d="%s/trades/%s-%s" % (root, tag, "sel" if w == "wc" else "n1"), cid=CID) for w in WINS]
    a = T.A.concat(parts)
    lo_d, hi_d = T.sm.day_of(lo), T.sm.day_of(hi)
    sel = np.nonzero((a["eday"] >= lo_d) & (a["eday"] <= hi_d))[0]
    cl = json.load(open(T.RES + "/cluster.json", encoding="utf-8"))
    mu, sd = np.array(cl["mu"]), np.array(cl["sd"])
    cen = np.array([[c["z"][k] for k in C.FEATS] for c in cl["clusters"]])
    ids = [c["id"] for c in cl["clusters"]]
    n = len(sel)
    F = np.full((n, len(C.FEATS)), np.nan)
    for r, i in enumerate(sel):
        w, s = a["win"][i], a["sym"][i]
        b, btc, t = C.bars(w, s), C.bars(w, "BTCUSDT"), int(a["sig_min"][i])
        if b is None or btc is None or t < 1441:
            continue
        f = C.features(b, btc, t, float(a["lvl"][i]), int(a["first_bar"][i]))
        F[r] = [f[k] for k in C.FEATS]
    ok = np.isfinite(F).all(1)
    Z = np.clip((C.transform(F) - mu) / np.where(sd > 0, sd, 1.0), -4.0, 4.0)
    lab = np.full(n, 0)
    if ok.any():
        lab[ok] = [ids[j] for j in ((Z[ok][:, None, :] - cen[None]) ** 2).sum(-1).argmin(1)]
    ex = a["entry_px_e8"][sel] / 1e8
    avg_exit = ex * (1.0 - a["gross"][sel] / C.STAKE)
    rows = []
    for r, i in enumerate(sel):
        rows.append(dict(i=I0 + r, win=str(a["win"][i]), per="D30", sym=str(a["sym"][i]), level=int(a["level"][i]), sig_ms=int(a["sig_ms"][i]), entry_ms=int(a["entry_ms"][i]), exit_ms=int(a["exit_ms"][i]),
                         sig_min=int(a["sig_min"][i]), entry_min=int(a["entry_min"][i]), exit_min=int(a["exit_min"][i]), entry_px=float(ex[r]), exit_px=float(avg_exit[r]), lvl=float(a["lvl"][i]), kind=str(a["kind"][i]),
                         gross=float(a["gross"][i]), fees=float(a["fees"][i]), pnl=float(a["pnl"][i]), natr=float(a["natr"][i]), cluster=int(lab[r]),
                         f={k: (None if not np.isfinite(F[r, j]) else float(F[r, j])) for j, k in enumerate(C.FEATS)}))
    rows.sort(key=lambda x: x["entry_ms"])
    for r, x in enumerate(rows):
        x["i"] = I0 + r
    os.makedirs(root + "/res", exist_ok=True)
    json.dump(rows, open(root + "/res/trades.json", "w", encoding="utf-8", newline="\n"), ensure_ascii=False)
    meta = dict(tag=tag, prefix=tag, cid=CID, wins=list(WINS), lo=lo, hi=hi, keys=keys, bin=binp, md5=md5, n=n)
    json.dump(meta, open(root + "/meta.json", "w", encoding="utf-8", newline="\n"), ensure_ascii=False)
    pn = np.array([x["pnl"] for x in rows])
    kinds = {k: int(sum(1 for x in rows if x["kind"] == k)) for k in sorted({x["kind"] for x in rows})}
    summ = dict(n=n, sum=float(pn.sum()) if n else 0.0, mean=float(pn.mean()) if n else None, kinds=kinds, stops=kinds.get("stop", 0), no_features=int((~ok).sum()), lo=lo, hi=hi)
    json.dump(summ, open(root + "/summary.json", "w", encoding="utf-8", newline="\n"), ensure_ascii=False)
    print("30 дней %s…%s: сделок %d, Σ %.2f $, на сделку %s $, по виду выхода %s, без полного набора признаков %d" % (lo, hi, n, summ["sum"], "—" if not n else "%.3f" % summ["mean"], kinds, summ["no_features"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
