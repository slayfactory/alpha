#!/usr/bin/env python3
"""TK-745: сверка заявок и уровней сетапа вкладки «Сделки»: data/trades/<№>.json (`ord`, `lv`) против CSV журнала движка (TK-743), событие в событие. Только srv, только чтение.

  /opt/venv/bin/python tk745_verify_orders.py <каталог страницы /data/pages/ml> [<каталог прогона review_loop.sh, например /data/p42-review/d30>]

Для каждой сделки (набор `main` — журнал /data/p42-tk743, набор «30 дней» — журнал прогона): абсолютное время = мс от начала окна + t0 окна (в файле — от t0 файла минуток + `c_t0`-сдвиг, как `pk["t0"]`);
  заявки — множество событий {выставлена, пересчёт цены, исполнена, снята} (роль, лот, время, цена, доля, тип, время срабатывания, причина) файла == CSV;
  уровни — множество строк (вид, цена, с, до, …) файла == CSV (зона — одна строка из трёх; пивоты T/G, версии V, уровень L, следующий X, прочие O);
  счёт в check.json["orders"] == счёт по CSV (группы: заявок, отрезков, исполнено, снято; уровни по ролям) — по нему клик-тест сверяет холст.
Разбор CSV здесь — независимая запись (не вызывает tk745_orders.pack_*); общее — только таблица причин NOTE_CODE."""
from __future__ import annotations

import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk745_orders as O  # noqa: E402  (только NOTE_CODE и grp — таблицы, не упаковка)

TK743, TK741_RES = "/data/p42-tk743/trades", "/data/p42-tk741/res"
CID_MAIN, PREFIX_MAIN = 7412001, "tk743-nt"


def part(w):
    return "h1" if w.startswith("m") else "n1" if w == "n1" else "sel"


def read_log(root, prefix, cid, win):
    d = "%s/%s-%s" % (root, prefix, part(win))
    t0 = int(json.load(open(d + "/t0.json", encoding="utf-8"))[win])
    out = {}
    for k, fn in enumerate(("orders", "setup")):
        with open("%s/%s/%d-%s.csv" % (d, win, cid, fn), encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                out.setdefault((r["sym"], int(r["level"]), int(r["sig_ms"])), ([], []))[k].append(r)
    return t0, out


def csv_events(rows, t0):
    ev = []
    share = {}
    for r in rows:
        if r["event"] == "place":
            share[(r["role"], int(r["lot"]), int(r["oid"]))] = (r["type"], int(r["share_pm"]))
    for r in rows:
        k, ms, px = (r["role"], int(r["lot"])), int(r["ms"]) + t0, int(r["px_e8"])
        typ, sh = share[(r["role"], int(r["lot"]), int(r["oid"]))]
        e = r["event"]
        if e == "place":
            ev.append(k + ("place", ms, px, typ, sh))
        elif e == "reprice":
            ev.append(k + ("reprice", ms, px, {"trail": 1, "rest": 2}.get(r["note"], 3)))
        elif e == "fill":
            ev.append(k + ("fill", ms, px, sh, int(r["share_pm"]), int(r["aux_ms"]) + t0 if int(r["aux_ms"]) else 0, O.NOTE_CODE.get(r["note"], r["note"])))
        elif e == "cancel":
            ev.append(k + ("cancel", ms, px, O.NOTE_CODE.get(r["note"], r["note"])))
        else:
            raise AssertionError("событие " + e)
    return sorted(ev, key=repr)


def file_events(ordl, pt0):
    ev = []
    for role, lot, typ, share, kind, tend, pend, reason, aux, path, fpm in ordl:
        k = (role, lot)
        ev.append(k + ("place", path[0] + pt0, path[1], typ, share))
        for j in range(3, len(path), 3):
            ev.append(k + ("reprice", path[j] + pt0, path[j + 1], path[j + 2]))
        if kind == "f":
            ev.append(k + ("fill", tend + pt0, pend, share, fpm, aux + pt0 if aux else 0, reason))
        elif kind == "c":
            ev.append(k + ("cancel", tend + pt0, pend, reason))
        else:
            assert kind == "o" and tend == 0, (role, lot, kind)
    return sorted(ev, key=repr)


def csv_levels(rows, t0):
    ab = lambda v: None if v == "" else int(v) + t0
    out, zone = [], {}
    for r in rows:
        role, px, a, b, why = r["role"], abs(int(r["px_e8"])), ab(r["from_ms"]), ab(r["to_ms"]), r["reason"]
        if role.startswith("zone_"):
            zone[role] = (px, a, b)
        elif role == "level":
            m = re.search(r"first_bar=(\d+);end=(\w+)", why)
            out.append(("L", px, a, b, m.group(2), int(m.group(1))))
        elif role == "ver":
            out.append(("V", px, a, b, 1 if why.endswith("active") else 0, int(why.split(";")[0][1:])))
        elif role in ("touch", "touch_gone", "next"):
            out.append(({"touch": "T", "touch_gone": "G", "next": "X"}[role], px, a) + ((b,) if role != "next" else ()))
        elif role == "other":
            m = re.match(r"(live|pk|pb|ban|consumed)(?:@(\d+))?;level=(\d+);end=(\w+)", why)
            out.append(("O", px, a, b, m.group(1), int(m.group(2)) + t0 if m.group(2) else None, int(m.group(3)), m.group(4)))
        else:
            raise AssertionError("роль " + role)
    if zone:
        n, e, f = zone["zone_near"], zone["zone_entry"], zone["zone_far"]
        out.append(("Z", n[0], e[0], f[0], n[1], n[2]))
    return sorted(out, key=repr)


def file_levels(lv, pt0):
    out = []
    for x in lv:
        k = x[0]
        if k in "LV":
            out.append((k, x[1], x[2] + pt0 if x[2] is not None else None, x[3] + pt0 if x[3] is not None else None) + tuple(x[4:]))
        elif k == "Z":
            out.append((k, x[1], x[2], x[3], x[4] + pt0, x[5] + pt0))
        elif k in "TG":
            out.append((k, x[1], x[2] + pt0 if x[2] is not None else None, x[3] + pt0 if x[3] is not None else None))
        elif k == "X":
            out.append((k, x[1], x[2] + pt0))
        elif k == "O":
            out.append((k, x[1], x[2] + pt0, x[3] + pt0 if x[3] is not None else None, x[4], x[5] + pt0 if x[5] is not None else None, x[6], x[7]))
        else:
            raise AssertionError("вид " + k)
    return sorted(out, key=repr)


def csv_counts(orows, srows):
    g = {}
    for r in orows:
        c = g.setdefault(O.grp(r["role"]), [0, 0, 0, 0])
        c[0] += r["event"] == "place"
        c[1] += r["event"] in ("place", "reprice")
        c[2] += r["event"] == "fill"
        c[3] += r["event"] == "cancel"
    lv = {}
    for r in srows:
        lv[r["role"]] = lv.get(r["role"], 0) + 1
    return {"g": g, "lv": lv}


def main(page, review=""):
    chk = json.load(open(page + "/data/trades/check.json", encoding="utf-8"))["orders"]
    sets = [("main", json.load(open(TK741_RES + "/trades.json", encoding="utf-8")), TK743, PREFIX_MAIN, CID_MAIN)]
    if review:
        m = json.load(open(review + "/meta.json", encoding="utf-8"))
        sets.append(("d30", json.load(open(review + "/res/trades.json", encoding="utf-8")), review + "/trades", m["prefix"], m["cid"]))
    bad, ntr, nev, nlv, logs = [], 0, 0, 0, {}
    for name, rows, root, prefix, cid in sets:
        for rw in rows:
            w = rw["win"]
            if (root, w) not in logs:
                logs[(root, w)] = read_log(root, prefix, cid, w)
            t0, lg = logs[(root, w)]
            orows, srows = lg[(rw["sym"], int(rw["level"]), int(rw["sig_ms"]) - t0)]
            j = json.load(open("%s/data/trades/%d.json" % (page, rw["i"]), encoding="utf-8"))
            pt0 = j["t0"]
            e_csv, e_file = csv_events(orows, t0), file_events(j["ord"], pt0)
            l_csv, l_file = csv_levels(srows, t0), file_levels(j["lv"], pt0)
            cc = csv_counts(orows, srows)
            ok = [e_csv == e_file, l_csv == l_file, chk[str(rw["i"])] == json.loads(json.dumps(cc))]
            ntr += 1
            nev += len(e_csv)
            nlv += len(l_csv)
            if not all(ok):
                bad.append((name, rw["i"], ok))
    print("сделок %d, событий заявок %d, строк уровней %d; расхождений: %d %s" % (ntr, nev, nlv, len(bad), bad[:10]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
