#!/usr/bin/env python3
"""TK-744 (В-195): V1 (`tt=0 fh=0`: свежесть только по правилу (б) «наторговка») против V0nt (TK-741) — метрики В-169, ушедшие и новые сделки, кластеры К1–К3. Только srv; движок не запускает.

  python3 tk744_an.py run      # → res/metrics.{json,txt}, res/removed.json; таблицы t744_* в /data/p42-tk744/tk744.sqlite (В-179)

Сделка — ключ (окно, монета, мс сигнала). «Ушла» — есть у V0nt, нет у V1 (вход был только по правилу (а)); «новая» — есть у V1, нет у V0nt.
Кластеры — из БД TK-741 (`t741_trade.cluster`, k = 3 по сетапу до сигнала): у ушедших сделок берутся готовые, новым кластер не присваивается.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk744_lib as L  # noqa: E402

T = L.T
DB = L.ROOT + "/tk744.sqlite"
DB741 = T.ROOT + "/tk741.sqlite"
VAR = ("nt", "fh0")
NAME = {"nt": "V0nt", "fh0": "V1"}
OUT = []
MAGMA = ("MAGMAUSDT", "2026-10-24", "08:14")   # разбор владельца, GMT+4


def say(*a):
    s = " ".join(str(x) for x in a)
    OUT.append(s)
    print(s)


def fmt(x, d=2):
    return "—" if x is None else ("%.*f" % (d, x))


def hhmm(ms):
    return dt.datetime.fromtimestamp(ms / 1000 + 4 * 3600, dt.timezone.utc).strftime("%d.%m %H:%M:%S")


def keys(a):
    return [(a["win"][i], a["sym"][i], int(a["sig_ms"][i])) for i in range(len(a["pnl"]))]


def per_of(win):
    return "JAN" if win == "m01" else "N1" if win == "n1" else "FJ" if win in T.FJ else "SEL"


def cmd_run(_a):
    res = dict(scopes={}, kinds={}, diff={}, clusters={}, magma={})
    data = {st: {sc: T.load(st, w, with_sigsx=False) for sc, (w, lo, hi) in T.SCOPES.items()} for st in VAR}
    say("## Метрики В-169: V0nt (`tt=0`, `fh=4`) против V1 (`tt=0`, `fh=0`) — найдено на этих данных нет, описательно")
    say("| область | вар. | Σ $ | сделок | $/сд | Шарп | просадка $ (% от 250 $) | перехай сут (длин. / сред. / хвост = p90) | R:R | стопов | $/стоп | худшая $ |")
    say("|" + "---|" * 12)
    for sc, (w, lo, hi) in T.SCOPES.items():
        for st in VAR:
            m, p, n = T.metrics(data[st][sc], lo, hi)
            res["scopes"]["%s/%s" % (sc, st)] = m
            a = data[st][sc]
            kc = collections.Counter(a["kind"])
            res["kinds"]["%s/%s" % (sc, st)] = {k: dict(n=int(c), sum=float(a["pnl"][a["kind"] == k].sum())) for k, c in kc.items()}
            say("| %s | %s | %s | %d | %s | %s | %s (%s %%) | %d / %s / %s | %s | %d | %s | %s |" % (
                sc, NAME[st], fmt(m["sum"]), m["trades"], fmt(m["per_trade"], 3), fmt(m["sharpe"]), fmt(m["dd"]), fmt(m["dd_pct"], 1),
                m["gap_longest"], fmt(m["gap_mean"], 1), fmt(m["gap_p90"], 1), fmt(m["rr"]), m["stops"], fmt(m["loss_per_stop"]), fmt(m["worst"])))
    say("")
    say("## Виды выхода (n / Σ $)")
    for sc in T.SCOPES:
        for st in VAR:
            say("%s/%s: %s" % (sc, NAME[st], "; ".join("%s %d / %s" % (k, v["n"], fmt(v["sum"])) for k, v in sorted(res["kinds"]["%s/%s" % (sc, st)].items()))))
    # ушедшие и новые сделки (все 10 окон)
    full = {st: T.load(st, T.ALLW, with_sigsx=False) for st in VAR}
    db741 = sqlite3.connect("file:%s?mode=ro" % DB741, uri=True)
    cl = {(r[0], r[1], r[2]): (r[3], r[4]) for r in db741.execute("SELECT win, sym, sig_ms, cluster, i FROM t741_trade")}
    kn, kf = keys(full["nt"]), keys(full["fh0"])
    idx_n, idx_f = {k: i for i, k in enumerate(kn)}, {k: i for i, k in enumerate(kf)}
    removed = [i for i, k in enumerate(kn) if k not in idx_f]
    new = [i for i, k in enumerate(kf) if k not in idx_n]
    both = [(idx_n[k], idx_f[k]) for k in kn if k in idx_f]
    nt, f1 = full["nt"], full["fh0"]
    changed = [(i, j) for i, j in both if abs(nt["pnl"][i] - f1["pnl"][j]) > 1e-9 or nt["entry_ms"][i] != f1["entry_ms"][j] or nt["exit_ms"][i] != f1["exit_ms"][j]]
    say("")
    say("## Ушедшие и новые сделки (все окна: фев–13.11, январь, N1)")
    say("V0nt %d сделок, V1 %d; общих %d (из них с другим входом / выходом / P&L: %d); ушли (только V0nt) %d; новые (только V1) %d" % (len(kn), len(kf), len(both), len(changed), len(removed), len(new)))
    for per in ("FJ", "SEL", "POOL", "JAN", "N1", "ALL"):
        pers = {"POOL": ("FJ", "SEL"), "ALL": ("FJ", "SEL", "JAN", "N1")}.get(per, (per,))
        rm = [i for i in removed if per_of(nt["win"][i]) in pers]
        nw = [j for j in new if per_of(f1["win"][j]) in pers]
        rs, ns = float(nt["pnl"][rm].sum()) if rm else 0.0, float(f1["pnl"][nw].sum()) if nw else 0.0
        res["diff"][per] = dict(removed=len(rm), removed_sum=rs, removed_mean=rs / len(rm) if rm else None, removed_stops=int((nt["kind"][rm] == "stop").sum()) if rm else 0,
                                removed_win=int((nt["pnl"][rm] > 0).sum()) if rm else 0, new=len(nw), new_sum=ns, new_mean=ns / len(nw) if nw else None)
        d = res["diff"][per]
        say("%s: ушло %d, Σ %s $, в среднем %s $, из них в плюс %d, стопов %d; новых %d, Σ %s $, в среднем %s $" % (per, d["removed"], fmt(d["removed_sum"]), fmt(d["removed_mean"], 3), d["removed_win"], d["removed_stops"], d["new"], fmt(d["new_sum"]), fmt(d["new_mean"], 3)))
    say("Виды выхода ушедших: %s" % ", ".join("%s %d / %s $" % (k, c, fmt(float(nt["pnl"][[i for i in removed if nt["kind"][i] == k]].sum()))) for k, c in sorted(collections.Counter(nt["kind"][i] for i in removed).items())))
    # кластеры К1–К3 (TK-741): сколько сделок V0nt, сколько ушло, Σ ушедших и оставшихся
    say("")
    say("## Кластеры TK-741 (К1 «тихий подход к молодому уровню», К2 «падение к старому уровню у минимума суток», К3 «резкий обвал, оборот втрое выше»)")
    rm_set = {kn[i] for i in removed}
    rows = collections.defaultdict(lambda: dict(n=0, removed=0, sum_all=0.0, sum_removed=0.0))
    for i, k in enumerate(kn):
        c = cl.get(k, (0, None))[0]
        for sc in (per_of(k[0]), "POOL" if per_of(k[0]) in ("FJ", "SEL") else None, "ALL"):
            if sc is None:
                continue
            r = rows[(sc, c)]
            r["n"] += 1
            r["sum_all"] += float(nt["pnl"][i])
            if k in rm_set:
                r["removed"] += 1
                r["sum_removed"] += float(nt["pnl"][i])
    say("| область | кластер | сделок V0nt | ушло | оставлено | Σ ушедших $ | Σ оставшихся $ |")
    say("|---|---|---|---|---|---|---|")
    for sc in ("POOL", "FJ", "SEL", "JAN", "N1", "ALL"):
        for c in (1, 2, 3):
            r = rows.get((sc, c))
            if not r:
                continue
            res["clusters"]["%s/K%d" % (sc, c)] = dict(r, kept=r["n"] - r["removed"], sum_kept=r["sum_all"] - r["sum_removed"])
            say("| %s | К%d | %d | %d | %d | %s | %s |" % (sc, c, r["n"], r["removed"], r["n"] - r["removed"], fmt(r["sum_removed"]), fmt(r["sum_all"] - r["sum_removed"])))
    # новые сделки: почему появились (у V0nt на той же монете была сделка, державшая позицию в момент сигнала)
    say("")
    say("## Новые сделки V1 (нет у V0nt)")
    if not new:
        say("нет")
    new_rows = []
    for j in new:
        sym, sig = f1["sym"][j], int(f1["sig_ms"][j])
        blk = [i for i in range(len(kn)) if nt["sym"][i] == sym and nt["win"][i] == f1["win"][j] and nt["entry_ms"][i] <= sig <= nt["exit_ms"][i]]
        rem_blk = [i for i in blk if kn[i] in rm_set]
        why = "позицию V0nt держала ушедшая сделка (вход %s – выход %s, %s $)" % (hhmm(int(nt["entry_ms"][blk[0]])), hhmm(int(nt["exit_ms"][blk[0]])), fmt(float(nt["pnl"][blk[0]]))) if rem_blk else (
            "позицию V0nt держала оставшаяся сделка" if blk else "причина не найдена: на этой монете у V0nt в момент сигнала позиции нет")
        new_rows.append((j, why))
        say("%s %s сигнал %s вход %s выход %s %s %s $ — %s" % (f1["win"][j], sym, hhmm(sig), hhmm(int(f1["entry_ms"][j])), hhmm(int(f1["exit_ms"][j])), f1["kind"][j], fmt(float(f1["pnl"][j])), why))
    # разбор владельца
    say("")
    say("## MAGMAUSDT 24.10 08:14 (GMT+4)")
    for st in VAR:
        a = full[st]
        hit = [i for i in range(len(a["pnl"])) if a["sym"][i] == MAGMA[0] and hhmm(int(a["entry_ms"][i]))[:5] == "24.10"]
        res["magma"][st] = [dict(entry=hhmm(int(a["entry_ms"][i])), exit=hhmm(int(a["exit_ms"][i])), kind=str(a["kind"][i]), pnl=float(a["pnl"][i])) for i in hit]
        say("%s: %s" % (NAME[st], "; ".join("вход %s выход %s %s %s $" % (h["entry"], h["exit"], h["kind"], fmt(h["pnl"])) for h in res["magma"][st]) or "сделок MAGMAUSDT 24.10 нет"))
    miss = [h for h in res["magma"]["nt"] if h["entry"][6:11] == MAGMA[2]]
    say("MAGMAUSDT 24.10 %s: у V0nt %s, у V1 %s" % (MAGMA[2], "есть" if miss else "нет", "есть" if any(h["entry"][6:11] == MAGMA[2] for h in res["magma"]["fh0"]) else "нет"))
    # диск
    os.makedirs(L.RES, exist_ok=True)
    json.dump(res, open(L.RES + "/metrics.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    open(L.RES + "/metrics.txt", "w", encoding="utf-8").write("\n".join(OUT) + "\n")
    db = sqlite3.connect(DB)
    db.executescript("DROP TABLE IF EXISTS t744_metric; DROP TABLE IF EXISTS t744_kind; DROP TABLE IF EXISTS t744_removed; DROP TABLE IF EXISTS t744_new; DROP TABLE IF EXISTS t744_cluster; DROP TABLE IF EXISTS t744_diff;"
                     "CREATE TABLE t744_metric(scope TEXT, variant TEXT, key TEXT, value REAL, PRIMARY KEY(scope, variant, key));"
                     "CREATE TABLE t744_kind(scope TEXT, variant TEXT, kind TEXT, n INTEGER, usd REAL, PRIMARY KEY(scope, variant, kind));"
                     "CREATE TABLE t744_removed(i741 INTEGER, win TEXT, per TEXT, sym TEXT, sig_ms INTEGER, entry_ms INTEGER, exit_ms INTEGER, kind TEXT, pnl REAL, cluster INTEGER);"
                     "CREATE TABLE t744_new(win TEXT, per TEXT, sym TEXT, sig_ms INTEGER, entry_ms INTEGER, exit_ms INTEGER, kind TEXT, pnl REAL, why TEXT);"
                     "CREATE TABLE t744_cluster(scope TEXT, cluster TEXT, n_v0nt INTEGER, removed INTEGER, kept INTEGER, sum_removed REAL, sum_kept REAL, PRIMARY KEY(scope, cluster));"
                     "CREATE TABLE t744_diff(scope TEXT PRIMARY KEY, removed INTEGER, removed_sum REAL, removed_mean REAL, removed_win INTEGER, removed_stops INTEGER, new INTEGER, new_sum REAL, new_mean REAL);")
    for k, m in res["scopes"].items():
        sc, st = k.split("/")
        db.executemany("INSERT INTO t744_metric VALUES(?,?,?,?)", [(sc, NAME[st], key, None if v is None else float(v)) for key, v in m.items()])
    for k, kk in res["kinds"].items():
        sc, st = k.split("/")
        db.executemany("INSERT INTO t744_kind VALUES(?,?,?,?,?)", [(sc, NAME[st], kind, v["n"], v["sum"]) for kind, v in kk.items()])
    db.executemany("INSERT INTO t744_removed VALUES(?,?,?,?,?,?,?,?,?,?)", [(cl.get(kn[i], (0, None))[1], kn[i][0], per_of(kn[i][0]), kn[i][1], kn[i][2], int(nt["entry_ms"][i]), int(nt["exit_ms"][i]), str(nt["kind"][i]), float(nt["pnl"][i]), cl.get(kn[i], (0, None))[0]) for i in removed])
    db.executemany("INSERT INTO t744_new VALUES(?,?,?,?,?,?,?,?,?)", [(kf[j][0], per_of(kf[j][0]), kf[j][1], kf[j][2], int(f1["entry_ms"][j]), int(f1["exit_ms"][j]), str(f1["kind"][j]), float(f1["pnl"][j]), why) for j, why in new_rows])
    db.executemany("INSERT INTO t744_cluster VALUES(?,?,?,?,?,?,?)", [(k.split("/")[0], k.split("/")[1], v["n"], v["removed"], v["kept"], v["sum_removed"], v["sum_kept"]) for k, v in res["clusters"].items()])
    db.executemany("INSERT INTO t744_diff VALUES(?,?,?,?,?,?,?,?,?)", [(k, v["removed"], v["removed_sum"], v["removed_mean"], v["removed_win"], v["removed_stops"], v["new"], v["new_sum"], v["new_mean"]) for k, v in res["diff"].items()])
    db.commit()
    return 0


if __name__ == "__main__":
    sys.exit(cmd_run(None))
