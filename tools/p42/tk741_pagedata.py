#!/usr/bin/env python3
"""TK-741 (В-192): данные страницы :8090 — вкладка «Сделки» и вариант «база без выхода по времени» в «Эквити». Только srv; движок не запускает.

  python3 tk741_pagedata.py trades <каталог страницы /data/pages/ml>   # data/trades/index.json (сделки, кластеры, описание), data/trades/<i>.json (минутки сделки: 3 суток до входа … 1 сутки после выхода, целые столбцы; TK-742;
                                                                       #   TK-745: + все заявки `ord` и уровни сетапа `lv` из журнала движка TK-743, формат — tk745_orders.py), data/trades/check.json (числа из БД и счёт заявок по CSV для клик-теста);
                                                                       #   TK745_REVIEW=<каталог прогона review_loop.sh> добавляет набор «30 дней» (период D30, № сделок с 1000)
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
import tk745_orders as O  # noqa: E402
import tk727_lib as L727  # noqa: E402      # natr(): формула NATR30 движка (data.rs natr_of), сверена с natr_bp на 100 из 100 (TK-727)

TK743 = os.environ.get("TK743_ROOT", "/data/p42-tk743/trades")
PRE_MIN, POST_MIN = 3 * 1440, 1440   # TK-742: минутки от 3 суток до входа до 1 суток после выхода
RES = T.RES
DB = T.ROOT + "/tk741.sqlite"
KIND_RU = {"stop": "стоп", "trail": "трейл", "rt": "возврат за уровень", "tx": "по времени"}
PERIOD_RU = {"D30": "30 дней", "FJ": "фев–июн", "SEL": "июл–13.11", "JAN": "январь", "N1": "14–24.11"}


def r(x, d):
    return None if x is None else round(float(x), d)


def pack_bars(b, m0, m1):
    """Минутки [m0, m1] окна одной монеты → компактный словарь целых столбцов (TK-742): цены — в шагах цены (НОД всех значений), открытие — к закрытию прошлой минуты,
    максимум — к max(откр., закр.), минимум — к min(откр., закр.), закрытие — к открытию; оборот q — в центах ($ = q/100); g — номера минут без сделок (там всё 0, цена = прошлое закрытие)."""
    sl = slice(m0, m1 + 1)
    pres = b[6][sl].astype(bool)
    o, h, l, c = (np.where(pres, b[k][sl], 0).astype(np.int64) for k in range(4))
    qv = np.where(pres, b[4][sl], 0).astype(np.int64)
    vals = np.concatenate([o[pres], h[pres], l[pres], c[pres]])
    tick = int(np.gcd.reduce(vals)) if len(vals) else 1
    tick = max(tick, 1)
    o, h, l, c = o // tick, h // tick, l // tick, c // tick
    prev = np.empty_like(c)          # закрытие прошлой минуты с протяжкой через дыры
    cc = np.where(pres, c, 0)
    idx = np.where(pres, np.arange(len(c)), 0)
    np.maximum.accumulate(idx, out=idx)
    ff = np.where(np.cumsum(pres) > 0, cc[idx], 0)
    prev[0], prev[1:] = 0, ff[:-1]
    do = np.where(pres, o - prev, 0)
    dh = np.where(pres, h - np.maximum(o, c), 0)
    dl = np.where(pres, np.minimum(o, c) - l, 0)
    dc = np.where(pres, c - o, 0)
    q = np.where(pres, np.rint(qv / 1e6), 0).astype(np.int64)
    assert (dh >= 0).all() and (dl >= 0).all(), "минутка с max/min внутри тела"
    return dict(v=1, n=int(len(c)), tick=tick, o=do.tolist(), h=dh.tolist(), l=dl.tolist(), c=dc.tolist(), q=q.tolist(), g=np.nonzero(~pres)[0].tolist()), int(sl.start)


def build_trades(page):
    rows = json.load(open(RES + "/trades.json", encoding="utf-8"))
    cl = json.load(open(RES + "/cluster.json", encoding="utf-8"))
    out0 = os.path.join(page, "data", "trades")
    out = out0 + ".new"                       # сборка рядом, потом подмена каталога (страница не остаётся без данных)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    a = T.load("nt", T.ALLW, with_sigsx=True)
    t0 = {w: int(a["t0"][np.nonzero(a["win"] == w)[0][0]]) for w in T.ALLW}
    # TK-745: заявки и уровни сетапа — из журнала движка TK-743 (V0nt на всех окнах, сделки те же байт в байт)
    srcs = [(rows, O.load_orderlog(TK743, "tk743-nt", T.CID["nt"], t0, T.ALLW))]
    # TK-745: набор «30 дней» — свежий прогон `review_loop.sh` (res/trades.json прогона + его журнал); его сделки идут в index.json после прежних (№ с 1000), период D30
    rv = os.environ.get("TK745_REVIEW", "")
    rv_meta = None
    if rv:
        rv_meta = json.load(open(rv + "/meta.json", encoding="utf-8"))
        rows2 = json.load(open(rv + "/res/trades.json", encoding="utf-8"))
        srcs.append((rows2, O.load_orderlog(rv + "/trades", rv_meta["prefix"], rv_meta["cid"], t0, rv_meta["wins"])))
    allrows = [(rw, lg) for rows_, lg in srcs for rw in rows_]
    trades, sizes, ocheck, nord, nlv = [], [], {}, 0, 0
    for rw, lg in allrows:
        w, sym = rw["win"], rw["sym"]
        b = C.bars(w, sym)
        n = len(b[0])
        m0, m1 = max(0, rw["entry_min"] - PRE_MIN), min(n - 1, rw["exit_min"] + POST_MIN)
        pk, _ = pack_bars(b, m0, m1)
        pk["t0"] = t0[w] + m0 * 60000
        # TK-742: NATR30 как в движке, на целых окнах (разгон — минуты до m0), значение «известное на минуте k» = NATR30 закрытой минуты k − 1 (движок берёт natr[sig_ms // 60000 − 1]);
        # nat — в единицах 1e-5 доли цены (0,001 %), −1 — значения нет (минуты нет, окно короче 15 присутствующих); sg — номер минуты сигнала в файле
        nv = L727.natr(dict(h=b[1], l=b[2], c=b[3], pres=b[6].astype(bool)), 30)
        known = np.concatenate([[np.nan], nv[:-1]])
        pk["nat"] = np.where(np.isfinite(known[m0:m1 + 1]), np.rint(known[m0:m1 + 1] * 1e5), -1).astype(np.int64).tolist()
        pk["sg"] = int(rw["sig_min"] - m0)
        nsg = known[rw["sig_min"]]
        assert np.isfinite(nsg) and abs(nsg - rw["natr"]) <= 3e-8, ("NATR30 сигнала не совпал с движком", rw["i"], nsg, rw["natr"])
        # TK-745: все заявки и уровни сетапа сделки из журнала движка (ключ как в tk741_lib.read_win)
        orows, srows = lg[(w, sym, int(rw["level"]), int(rw["sig_ms"]) - t0[w])]
        off = t0[w] - pk["t0"]
        pk["ord"], pk["lv"] = O.pack_orders(orows, off), O.pack_setup(srows, off)
        nord += len(pk["ord"])
        nlv += len(pk["lv"])
        ocheck[str(rw["i"])] = O.count_orderlog(orows, srows)
        fn = "%s/%d.json" % (out, rw["i"])
        json.dump(pk, open(fn, "w"), separators=(",", ":"))
        sizes.append(os.path.getsize(fn))
        f = rw["f"]
        trades.append([rw["i"], rw["per"], rw["sym"], rw["entry_ms"], rw["exit_ms"], r(rw["entry_px"], 8), r(rw["exit_px"], 8), r(rw["lvl"], 8), rw["kind"], r(rw["pnl"], 6), rw["cluster"],
                       [r(f[k], 4) for k in C.FEATS], t0[w] + m0 * 60000, m1 - m0 + 1,
                       r(rw["entry_px"] * (1.0 - rw["fees"] / C.STAKE), 8)])      # be_px: безубыток — цена выхода шорта, при которой P&L после комиссий = 0 (gross = STAKE·(1 − выход/вход) = fees)
    ver = int(os.path.getmtime(RES + "/trades.json"))
    if rv_meta:
        ver = max(ver, int(os.path.getmtime(rv + "/res/trades.json")))
    index = dict(v=ver, feats=C.FEATS, kinds=KIND_RU, periods=PERIOD_RU, k=cl["k"], silhouette=cl["silhouette"], margin=250,
                 clusters=[dict(id=c["id"], name=c["name"], med={k: r(v, 3) for k, v in c["med"].items()}) for c in cl["clusters"]],
                 cols=["i", "per", "sym", "entry_ms", "exit_ms", "entry_px", "exit_px", "lvl", "kind", "pnl", "cluster", "f", "c_t0", "c_n", "be_px"], trades=trades)
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
    chk["orders"] = ocheck
    json.dump(chk, open(out + "/check.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    shutil.rmtree(out0 + ".old", ignore_errors=True)      # подмена каталога: прежний — в .old, новый на его место
    if os.path.isdir(out0):
        os.rename(out0, out0 + ".old")
    os.rename(out, out0)
    shutil.rmtree(out0 + ".old", ignore_errors=True)
    out = out0
    print("data/trades: сделок %d, заявок %d, строк уровней %d, кластеров %d, файлы сделок %.1f МБ (на сделку: среднее %d КБ, макс %d КБ), index.json %d КБ, v=%d" % (len(trades), nord, nlv, cl["k"], sum(sizes) / 1e6, sum(sizes) / len(sizes) / 1024, max(sizes) / 1024, os.path.getsize(out + "/index.json") // 1024, ver))
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
