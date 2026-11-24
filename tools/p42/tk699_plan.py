#!/usr/bin/env python3
"""TK-699 шаг 3: вход пересчёта клеток c539 на исправленных столбцах f12 — отдельный корень (оригиналы TK-674 и res/thr674*.json не трогаются).

  tk699_plan.py plan --scope scope-cells.json --delta delta.json --root /data/p42-tk699/r674 [--k3 30]
      по delta.json (tk699_delta.py): клетки, у которых единичка смысла фильтра изменилась на любом окне (ключ — сторона + ckey основной метки БД), и K3 — контрольные клетки
      без изменения (f12-смыслы с той же единичкой, добор — клетки без f12 из таблиц партий), которые обязаны совпасть с БД байт в байт.
      Пишет в корень (как стадии TK-674 уровня 3 с партией t699): res/level674bs3t699-<сторона>.json, res/ftabcols6743t699-<сторона>.json, ftab6743t699/<окно>-<сторона>.csv,
      res/invalid674bs3t699-<сторона>.json ({}), res/recount-lists-<сторона>.json (cid: changed / k3 / bd-клетки), res/recount-plan.json (числа для таблицы «есть / считаем»).
  tk699_plan.py cmp --root ... --side short --win-a 01.07–13.11 --win-b TK699-K3 --list k3|changed
      строки БД двух меток окна по клеткам списка: trades, usd, sharpe, daily — точное равенство (код 1 при расхождении или отсутствии строки).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import sqlite3
import sys
import time

import numpy as np

MAIN = "01.07–13.11"
DB = "/data/chron/chron.sqlite"
R674 = "/data/p42-tk674c"
SN = {"S": "short", "L": "long"}


def con_ro():
    for k in range(30):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=120)
            c.execute("select 1 from metric limit 1")
            return c
        except sqlite3.OperationalError as e:
            print("retry", k, e, flush=True)
            time.sleep(20)
    raise SystemExit("БД занята")


def fetch(con, ids, win=MAIN):
    out = []
    for i in range(0, len(ids), 900):
        q = ids[i:i + 900]
        out += con.execute("select id, entry, cell, ckey, trades, usd, sharpe, extra, win from metric where id in (%s)" % ",".join("?" * len(q)), q).fetchall()
    return [r for r in out if r[8] == win]


def cell_of(r):
    x = json.loads(r[7])
    return dict(id=r[0], entry=r[1], cid=r[2], ckey=r[3], patch=x["patch"], filt=x["filt"], sig=x.get("sigkey", []), trades=r[4], usd=r[5], sharpe=r[6])


def unpack(npz, side, w, sem, n):
    return np.unpackbits(npz["%s|%s|%s" % (side, w, sem)])[:n].astype(bool)


def cmd_plan(a):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    delta = json.load(open(a.delta, encoding="utf-8"))
    pref = os.path.splitext(a.delta)[0]
    npz = np.load(pref + "-sems.npz")
    rows = json.load(open(pref + "-rows.json"))
    scope = json.load(open(a.scope, encoding="utf-8"))
    wins = delta["wins"]
    con = con_ro()
    rnd = random.Random(674)
    report = {}
    for S, sn in SN.items():
        info = delta["sides"].get(S)
        if not info:
            continue
        sems = info["sems"]
        changed = {s for s, v in sems.items() if v.get("changed")}
        same = {s for s, v in sems.items() if v.get("changed") is False}
        sc = [s for s in scope["sems"] if s["side"] == sn]
        ids_ch = [i for s in sc if s["sem"] in changed for i in s["ids"]]
        ids_same = [i for s in sc if s["sem"] in same for i in s["ids"]]
        cells_ch = {c["ckey"]: c for c in map(cell_of, fetch(con, ids_ch))}
        cells_same = {c["ckey"]: c for c in map(cell_of, fetch(con, ids_same))}
        bd_sems = {s["sem"] for s in sc if s["bd"]}
        for c in list(cells_ch.values()) + list(cells_same.values()):
            c["bd"] = c["ckey"].split(" F=", 1)[1] in bd_sems
        # K3: без bd, из неизменных f12-смыслов
        pool = sorted(k for k, c in cells_same.items() if not c["bd"])
        rnd.shuffle(pool)
        k3 = [cells_same[k] for k in pool[:a.k3]]
        k3_f12 = len(k3)
        extra_cols = {}
        if len(k3) < a.k3:                                       # добор: клетки без f12 из таблиц партий (единичка — столбец прежнего файла)
            import glob
            tok = {"age_h", "approach_dur_min", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h", "depth_min_bp", "depth_mean_bp", "since_touch_prior_h", "touch_prior_none"}
            cand_sem = {}
            for f in sorted(glob.glob(R674 + "/res/ftabcols6743r0*-%s.json" % S)):
                suf = os.path.basename(f)[len("ftabcols674"):-len("-%s.json" % S)]
                for sem, name in json.load(open(f, encoding="utf-8")).items():
                    fs = {t.replace("<=", ">=").split(">=")[0] for t in sem.split("&")}
                    if "&" in sem and not (fs & tok) and not any(x.startswith("bd_") for x in fs):
                        cand_sem.setdefault(sem, (R674 + "/ftab674" + suf, name))
            rows_db = con.execute("select id, entry, cell, ckey, trades, usd, sharpe, extra, win from metric where uni='c539' and side=? and win=? and entry in (363, 385) and ckey glob '* F=*'",
                                  (sn, MAIN)).fetchall()
            rows_db = [r for r in rows_db if r[3].split(" F=", 1)[1] in cand_sem]
            rnd.shuffle(rows_db)
            for r in rows_db[:a.k3 - len(k3)]:
                c = cell_of(r)
                c["bd"] = False
                c["f12"] = False
                k3.append(c)
                extra_cols[c["ckey"].split(" F=", 1)[1]] = cand_sem[c["ckey"].split(" F=", 1)[1]]
        # плотность дубликатов: (патч, единички трёх окон)
        vec_of = {sem: b"".join(npz["%s|%s|%s" % (S, w, sem)].tobytes() for w in wins) for sem in {c["ckey"].split(" F=", 1)[1] for c in cells_ch.values()}}
        cls = collections.defaultdict(list)
        for k, c in cells_ch.items():
            line, sem = c["ckey"].split(" F=", 1)
            cls[(line, vec_of[sem])].append(k)
        # таблица единичек
        all_sems = sorted({c["ckey"].split(" F=", 1)[1] for c in list(cells_ch.values()) + k3})
        cols = {sem: "f%04d" % i for i, sem in enumerate(all_sems)}
        csvs = {}
        for w in wins:
            n = len(rows["%s-%s" % (w, S)])
            keys = rows["%s-%s" % (w, S)]
            mat = np.zeros((n, len(all_sems)), dtype=bool)
            for j, sem in enumerate(all_sems):
                if sem in extra_cols:
                    import pandas as pd
                    d, name = extra_cols[sem]
                    df = pd.read_csv("%s/%s-%s.csv" % (d, w, S), dtype=str, keep_default_na=False)
                    assert np.array_equal((df["sym"] + "|" + df["sig_ms"]).to_numpy(), np.array(keys))
                    mat[:, j] = df[name].to_numpy() == "1.0"
                else:
                    mat[:, j] = unpack(npz, S, w, sem, n)
            csvs[w] = (keys, mat)
        out_root = a.root
        os.makedirs(out_root + "/res", exist_ok=True)
        d = "%s/ftab6743t699" % out_root
        os.makedirs(d, exist_ok=True)
        for w, (keys, mat) in csvs.items():
            with open("%s/%s-%s.csv" % (d, w, S), "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms," + ",".join(cols[s_] for s_ in all_sems) + "\n")
                for i, kk in enumerate(keys):
                    sym, ms = kk.split("|")
                    f.write("%s,%s,%s\n" % (sym, ms, ",".join("1.0" if v else "0.0" for v in mat[i])))
        json.dump(cols, open("%s/res/ftabcols6743t699-%s.json" % (out_root, S), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        todo = list(cells_ch.values()) + k3
        plan = dict(side=S, n_cells=len(todo), batch="t699",
                    cells={c["ckey"]: dict(cid=c["cid"], patch=c["patch"], filt=c["filt"], sig=c["sig"]) for c in todo})
        json.dump(plan, open("%s/res/level674bs3t699-%s.json" % (out_root, S), "w", encoding="utf-8"), ensure_ascii=False)
        json.dump({}, open("%s/res/invalid674bs3t699-%s.json" % (out_root, S), "w"))
        lists = dict(changed=sorted(c["cid"] for c in cells_ch.values()), k3=sorted(c["cid"] for c in k3), bd=sorted(c["cid"] for c in cells_ch.values() if c["bd"]),
                     changed_ckeys=sorted(cells_ch), k3_ckeys=sorted(c["ckey"] for c in k3))
        json.dump(lists, open("%s/res/recount-lists-%s.json" % (out_root, S), "w", encoding="utf-8"), ensure_ascii=False)
        ent = collections.Counter(c["entry"] for c in cells_ch.values())
        ent_same = collections.Counter(c["entry"] for c in cells_same.values())
        report[S] = dict(scope_cells=len(cells_ch) + len(cells_same), changed_cells=len(cells_ch), unchanged_cells=len(cells_same), changed_bd=sum(1 for c in cells_ch.values() if c["bd"]),
                         changed_by_entry=dict(ent), unchanged_by_entry=dict(ent_same), sems_total=len(sems), sems_changed=len(changed), sems_unchanged=len(same),
                         sems_missing=sum(1 for v in sems.values() if "missing" in v), classes=len(cls), k3=len(k3), k3_f12=k3_f12, table_columns=len(all_sems),
                         rows_per_win={w: len(rows["%s-%s" % (w, S)]) for w in wins})
        print("== %s: клеток в охвате %d, изменились %d (bd %d), без изменения %d; смыслов %d / изменились %d; классов (патч + единички) %d; K3 %d (f12 %d); столбцов %d" %
              (S, report[S]["scope_cells"], len(cells_ch), report[S]["changed_bd"], len(cells_same), len(sems), len(changed), len(cls), len(k3), k3_f12, len(all_sems)), flush=True)
    json.dump(report, open(a.root + "/res/recount-plan.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def cmd_cmp(a):
    lists = json.load(open("%s/res/recount-lists-%s.json" % (a.root, "S" if a.side == "short" else "L"), encoding="utf-8"))
    keys = lists["k3_ckeys" if a.list == "k3" else "changed_ckeys"]
    con = con_ro()
    rows = {}
    for win in (a.win_a, a.win_b):
        got = {}
        for i in range(0, len(keys), 400):
            q = keys[i:i + 400]
            for r in con.execute("select ckey, trades, usd, sharpe, extra from metric where uni='c539' and side=? and win=? and ckey in (%s)" % ",".join("?" * len(q)), (a.side, win, *q)):
                x = json.loads(r[4])
                got[r[0]] = (r[1], r[2], r[3], x.get("daily"), x.get("dayn"))
        rows[win] = got
    a_, b_ = rows[a.win_a], rows[a.win_b]
    same = [k for k in keys if k in a_ and k in b_ and a_[k] == b_[k]]
    diff = [k for k in keys if k in a_ and k in b_ and a_[k] != b_[k]]
    no_a = [k for k in keys if k not in a_]
    no_b = [k for k in keys if k not in b_]
    print("cmp %s %s: клеток %d, равны %d, различаются %d, нет в %s %d, нет в %s %d" % (a.side, a.list, len(keys), len(same), len(diff), a.win_a, len(no_a), a.win_b, len(no_b)))
    for k in diff[:5]:
        print("  ", k[-80:], a_[k][:3], b_[k][:3])
    sys.exit(0 if len(same) == len(keys) else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("plan")
    p.add_argument("--scope", required=True)
    p.add_argument("--delta", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--k3", type=int, default=30)
    p.set_defaults(f=cmd_plan)
    p = sp.add_parser("cmp")
    p.add_argument("--root", required=True)
    p.add_argument("--side", required=True, choices=("short", "long"))
    p.add_argument("--win-a", default=MAIN)
    p.add_argument("--win-b", required=True)
    p.add_argument("--list", required=True, choices=("k3", "changed"))
    p.set_defaults(f=cmd_cmp)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
