#!/usr/bin/env python3
"""TK-741 (В-192): шаг 1 — метрики V0 (tt=20) против V0nt (tt=0), виды выхода, парная суточная разность с блочным бутстрепом, проба «ключ живой» (tt=1). Только srv; движок не запускает.

  python3 tk741_an.py metrics      # → res/metrics.json, res/metrics.txt, таблицы t741_* в /data/p42-tk741/tk741.sqlite (В-179)
"""
from __future__ import annotations

import collections
import json
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk741_lib as T  # noqa: E402

A, L = T.A, T.L
DB = T.ROOT + "/tk741.sqlite"
OUT = []


def say(*a):
    s = " ".join(str(x) for x in a)
    OUT.append(s)
    print(s)


def fmt(x, d=2):
    return "—" if x is None else ("%.*f" % (d, x))


def cmd_metrics(_a):
    res = dict(scopes={}, kinds={}, trans={}, probe={}, delta={})
    series = {}
    data = {st: {sc: T.load(st, w, with_sigsx=False) for sc, (w, lo, hi) in T.SCOPES.items()} for st in ("v0", "nt", "pr")}
    for sc, (w, lo, hi) in T.SCOPES.items():
        for st in ("v0", "nt", "pr"):
            m, p, n = T.metrics(data[st][sc], lo, hi)
            res["scopes"]["%s/%s" % (sc, st)] = m
            series[(sc, st)] = (p, n)
            kc = collections.Counter(data[st][sc]["kind"])
            res["kinds"]["%s/%s" % (sc, st)] = {k: dict(n=int(c), sum=float(data[st][sc]["pnl"][data[st][sc]["kind"] == k].sum())) for k, c in kc.items()}
    say("## Метрики V0 (tt=20) против V0nt (tt=0); проба pr (tt=1) — только для проверки ключа")
    hdr = "| область | вар. | Σ $ | сделок | $/сд | Шарп | просадка $ (%) | перехай сут (длин. / сред. / p90) | R:R | стопов | $/стоп | худшая $ |"
    say(hdr)
    say("|" + "---|" * 12)
    for sc in T.SCOPES:
        for st in ("v0", "nt", "pr"):
            m = res["scopes"]["%s/%s" % (sc, st)]
            say("| %s | %s | %s | %d | %s | %s | %s (%s %%) | %d / %s / %s | %s | %d | %s | %s |" % (sc, st, fmt(m["sum"]), m["trades"], fmt(m["per_trade"], 3), fmt(m["sharpe"]), fmt(m["dd"]), fmt(m["dd_pct"], 1),
                                                                                                   m["gap_longest"], fmt(m["gap_mean"], 1), fmt(m["gap_p90"], 1), fmt(m["rr"]), m["stops"], fmt(m["loss_per_stop"]), fmt(m["worst"])))
    say("")
    say("## Виды выхода (n / Σ $)")
    for sc in T.SCOPES:
        for st in ("v0", "nt", "pr"):
            say("%s/%s: %s" % (sc, st, "; ".join("%s %d / %s" % (k, v["n"], fmt(v["sum"])) for k, v in sorted(res["kinds"]["%s/%s" % (sc, st)].items()))))
    # переходы: сделка V0 → та же сделка (монета, сигнал) у V0nt и у пробы
    for st2 in ("nt", "pr"):
        tr = collections.Counter()
        only0 = only2 = 0
        for sc in ("POOL", "JAN", "N1"):
            v, n = data["v0"][sc], data[st2][sc]
            kv = {(s, t): i for i, (s, t) in enumerate(zip(v["sym"], v["sig_ms"]))}
            kn = {(s, t): i for i, (s, t) in enumerate(zip(n["sym"], n["sig_ms"]))}
            for k in set(kv) & set(kn):
                tr[(v["kind"][kv[k]], n["kind"][kn[k]])] += 1
            only0 += len(set(kv) - set(kn))
            only2 += len(set(kn) - set(kv))
        res["trans"][st2] = dict(pairs={"%s>%s" % k: c for k, c in tr.items()}, only_v0=only0, only_other=only2)
        say("V0 → %s: %s; только в V0: %d, только в %s: %d" % (st2, ", ".join("%s→%s %d" % (k[0], k[1], c) for k, c in sorted(tr.items())), only0, st2, only2))
    # парная суточная разность V0nt − V0 (пул FJ ∪ SEL), блочный бутстреп блоки 6 и 21 (описательно)
    p0, _ = series[("POOL", "v0")]
    p1, _ = series[("POOL", "nt")]
    d = p1 - p0
    ci = L.delta_ci(d, (6, 21))
    res["delta"] = dict(sum=float(d.sum()), days=int(len(d)), nonzero_days=int((np.abs(d) > 1e-9).sum()), max_abs=float(np.abs(d).max()), ci={str(b): v for b, v in ci.items()})
    say("Парная разность V0nt − V0 по суткам пула (%d суток): Σ %s $, суток с разностью ≠ 0: %d, макс |Δ| %s $; 95 %% (блоки 6 / 21): [%s; %s] / [%s; %s]" % (
        len(d), fmt(d.sum()), res["delta"]["nonzero_days"], fmt(res["delta"]["max_abs"]), fmt(ci[6][0]), fmt(ci[6][1]), fmt(ci[21][0]), fmt(ci[21][1])))
    # контроль: файлы
    ctl = []
    for w in T.ALLW:
        a = open(os.path.join(T.run_dir("v0", w), w, "%d-trades.csv" % T.CID["v0"]), "rb").read()
        b = open(os.path.join(T.run_dir("nt", w), w, "%d-trades.csv" % T.CID["nt"]), "rb").read()
        ctl.append((w, a == b))
    res["files_v0_eq_nt"] = {w: bool(x) for w, x in ctl}
    say("Файлы сделок V0 и V0nt побайтно равны: %s" % ", ".join("%s %s" % (w, "да" if x else "НЕТ") for w, x in ctl))
    os.makedirs(T.RES, exist_ok=True)
    json.dump(res, open(T.RES + "/metrics.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    open(T.RES + "/metrics.txt", "w", encoding="utf-8").write("\n".join(OUT) + "\n")
    db = sqlite3.connect(DB)
    db.executescript("CREATE TABLE IF NOT EXISTS t741_metric(scope TEXT, variant TEXT, key TEXT, value REAL, PRIMARY KEY(scope, variant, key));"
                     "CREATE TABLE IF NOT EXISTS t741_kind(scope TEXT, variant TEXT, kind TEXT, n INTEGER, usd REAL, PRIMARY KEY(scope, variant, kind));")
    db.execute("DELETE FROM t741_metric")
    db.execute("DELETE FROM t741_kind")
    for k, m in res["scopes"].items():
        sc, st = k.split("/")
        db.executemany("INSERT INTO t741_metric VALUES(?,?,?,?)", [(sc, st, key, None if v is None else float(v)) for key, v in m.items()])
    for k, kk in res["kinds"].items():
        sc, st = k.split("/")
        db.executemany("INSERT INTO t741_kind VALUES(?,?,?,?,?)", [(sc, st, kind, v["n"], v["sum"]) for kind, v in kk.items()])
    db.commit()
    return 0


def main(argv=None):
    cmd = (argv or sys.argv[1:] or ["metrics"])[0]
    return {"metrics": cmd_metrics}[cmd](None)


if __name__ == "__main__":
    sys.exit(main())
