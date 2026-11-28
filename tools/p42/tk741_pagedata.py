#!/usr/bin/env python3
"""TK-741 (В-192): данные страницы :8090 — вкладка «Сделки» и вариант «база без выхода по времени» в «Эквити». Только srv; движок не запускает.

  python3 tk741_pagedata.py trades <каталог страницы /data/pages/ml>   # data/trades/index.json (сделки, кластеры, описание), data/trades/c/<i>.json (минутки сделки ±2 ч), data/trades/check.json (числа из БД для клик-теста)
  python3 tk741_pagedata.py equity                                      # /data/p42-tk741/page/{wa,wb,wc}/short-{trades,daily}.csv для варианта s4nt (tk684_variants.json)

Входы — res/trades.json, res/cluster.json (`tk741_cluster.py run`) и БД /data/p42-tk741/tk741.sqlite.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk741_lib as T  # noqa: E402
import tk741_cluster as C  # noqa: E402

PAD_MIN = 120           # минутки до входа и после выхода, мин
RES = T.RES
DB = T.ROOT + "/tk741.sqlite"
KIND_RU = {"stop": "стоп", "trail": "трейл", "rt": "возврат за уровень", "tx": "по времени"}
PERIOD_RU = {"FJ": "фев–июн", "SEL": "июл–13.11", "JAN": "январь", "N1": "14–24.11"}


def r(x, d):
    return None if x is None else round(float(x), d)


def build_trades(page):
    rows = json.load(open(RES + "/trades.json", encoding="utf-8"))
    cl = json.load(open(RES + "/cluster.json", encoding="utf-8"))
    out = os.path.join(page, "data", "trades")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out + "/c")
    a = T.load("nt", T.ALLW, with_sigsx=True)
    t0 = {w: int(a["t0"][np.nonzero(a["win"] == w)[0][0]]) for w in T.ALLW}
    trades = []
    for rw in rows:
        w, sym = rw["win"], rw["sym"]
        b = C.bars(w, sym)
        n = len(b[0])
        m0, m1 = max(0, rw["entry_min"] - PAD_MIN), min(n - 1, rw["exit_min"] + PAD_MIN)
        sl = slice(m0, m1 + 1)
        pres = b[6][sl]
        cdl = dict(t0=t0[w] + m0 * 60000, o=[], h=[], l=[], c=[])
        for k, arr in (("o", b[0]), ("h", b[1]), ("l", b[2]), ("c", b[3])):
            cdl[k] = [int(x) if p else None for x, p in zip(arr[sl], pres)]
        json.dump(cdl, open("%s/c/%d.json" % (out, rw["i"]), "w"), separators=(",", ":"))
        f = rw["f"]
        trades.append([rw["i"], rw["per"], rw["sym"], rw["entry_ms"], rw["exit_ms"], r(rw["entry_px"], 8), r(rw["exit_px"], 8), r(rw["lvl"], 8), rw["kind"], r(rw["pnl"], 6), rw["cluster"],
                       [r(f[k], 4) for k in C.FEATS], t0[w] + m0 * 60000, m1 - m0 + 1, rw["entry_min"] - m0, rw["exit_min"] - m0])
    ver = int(os.path.getmtime(RES + "/trades.json"))
    index = dict(v=ver, feats=C.FEATS, kinds=KIND_RU, periods=PERIOD_RU, k=cl["k"], silhouette=cl["silhouette"], margin=250,
                 clusters=[dict(id=c["id"], name=c["name"], med={k: r(v, 3) for k, v in c["med"].items()}) for c in cl["clusters"]],
                 cols=["i", "per", "sym", "entry_ms", "exit_ms", "entry_px", "exit_px", "lvl", "kind", "pnl", "cluster", "f", "c_t0", "c_n", "c_ie", "c_ix"], trades=trades)
    json.dump(index, open(out + "/index.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    # числа из БД — для клик-теста (страница считает свои из сделок, тест сверяет)
    db = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    chk = {"clusters": {}, "periods": {}}
    for per_set, key in ((("FJ", "SEL"), "main"), (("FJ", "SEL", "JAN", "N1"), "all"), (("JAN",), "JAN"), (("N1",), "N1")):
        q = ",".join("'%s'" % p for p in per_set)
        for cid in [0] + [c["id"] for c in cl["clusters"]]:
            cond = "per IN (%s)" % q + ("" if cid == 0 else " AND cluster=%d" % cid)
            rows_ = db.execute("SELECT pnl, kind FROM t741_trade WHERE " + cond).fetchall()
            pn = np.array([x[0] for x in rows_])
            st = np.array([x[1] == "stop" for x in rows_])
            w_, l_ = pn[pn > 0], pn[pn < 0]
            chk["clusters"]["%s/%d" % (key, cid)] = dict(n=len(pn), sum=float(pn.sum()), mean=float(pn.mean()) if len(pn) else None, rr=float(w_.mean() / abs(l_.mean())) if len(w_) and len(l_) else None,
                                                         stops=int(st.sum()), loss_per_stop=float(pn[st].mean()) if st.any() else None)
    json.dump(chk, open(out + "/check.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    tot = sum(os.path.getsize("%s/c/%d.json" % (out, rw["i"])) for rw in rows)
    print("data/trades: сделок %d, кластеров %d, минутки %.1f МБ, index.json %d КБ, v=%d" % (len(trades), cl["k"], tot / 1e6, os.path.getsize(out + "/index.json") // 1024, ver))
    return 0


def build_equity():
    """Каталоги окон wa / wb / wc для варианта s4nt: сделки V0nt (копия файла прогона) и суточный ряд по дню входа (охрана G7: Σ ряда == Σ net сделок)."""
    for w in T.SEL:
        d = "%s/page/%s" % (T.ROOT, w)
        os.makedirs(d, exist_ok=True)
        src = os.path.join(T.run_dir("nt", w), w, "%d-trades.csv" % T.CID["nt"])
        shutil.copyfile(src, d + "/short-trades.csv")
        rows = list(csv.DictReader(open(src, encoding="utf-8", newline="")))
        day = {}
        for x in rows:
            day[int(x["entry_day"])] = day.get(int(x["entry_day"]), 0.0) + float(x["net"])
        with open(d + "/short-daily.csv", "w", encoding="utf-8", newline="") as f:
            f.write("day,pnl\n")
            for k in sorted(day):
                f.write("%d,%.6f\n" % (k, day[k]))
        print(d, len(rows), "сделок")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1]
    sys.exit(build_trades(sys.argv[2]) if cmd == "trades" else build_equity())
