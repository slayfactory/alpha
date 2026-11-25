#!/usr/bin/env python3
"""TK-700 (приказ координатора 25.11): посуточные ряды Jul 1–13.11 клеток набора `all1` (сторона, класс) из летописи в `mass_day` — БЕЗ движка.

  python3 tk700_h2load.py load --run-id h2-chron-A-short --cellset all1 --side short --class A [--chunk 4000] [--db …] [--chron …]

Источник — `chron.sqlite`, строка `metric` (`cell.db_id`): `extra.daily` (P&L за сутки, 136 суток от 01.07) и `extra.dayn` (число сделок за сутки), те же числа, что у движка
(`dry1`: посуточный ряд равен БД). `mass_day.day` — от D0 = 2026-01-01 (как в прогонах H1): 01.07 = 181, 13.11 = 316; строки только за сутки со сделками (как у движка).
`mass_cw` (окно `h2`): n = Σ dayn, usd = Σ daily; сверка с `cell.ref_n` / `ref_usd` — расхождение пишется в `errs`. Повторный запуск пропускает клетки, уже записанные в `mass_cw`."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk700_mass as ms  # noqa: E402

D0 = datetime.date(2026, 1, 1)
DAY0 = datetime.date(2026, 7, 1)
DAYS = 136                                                                  # 01.07–13.11
WIN = "h2"
CHRON_DB = "/data/chron/chron.sqlite"


def cmd_load(a):
    db = ms.connect(a.db)
    src = sqlite3.connect("file:%s?mode=ro" % a.chron, uri=True)
    base = (DAY0 - D0).days
    classes = set(a.cls.split(",")) if a.cls else set()
    unbuilt, cov_sha = ms.bad_columns(a.cov) if classes else (set(), "")
    cells = ms.keep_classes(ms.load_cells(db, a.cellset, a.side), classes, unbuilt)
    ids = dict(db.execute("SELECT cid, db_id FROM cell WHERE cellset=? AND side=?", (a.cellset, a.side)))
    row = db.execute("SELECT cellset FROM mass_run WHERE run_id=?", (a.run_id,)).fetchone()
    if row and row[0] != a.cellset:
        raise SystemExit("run-id %s уже занят набором %s" % (a.run_id, row[0]))
    params = dict(source="chron.metric.extra.daily/dayn", chron=a.chron, win="01.07–13.11", day0=base, days=DAYS, D0=D0.isoformat(), side=a.side, classes=sorted(classes), cov_sha=cov_sha, load=True)
    if not row:
        db.execute("INSERT INTO mass_run VALUES(?,?,?,?,?,?,?,?)", (a.run_id, a.cellset, 0, WIN, "", json.dumps(params), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "running"))
        db.commit()
    done = {r[0] for r in db.execute("SELECT cid FROM mass_cw WHERE run_id=? AND win=?", (a.run_id, WIN))}
    todo = [c for c in cells if c["cid"] not in done]
    print("%s %s: клеток класса %s %d, уже записано %d, к записи %d" % (a.cellset, a.side, ",".join(sorted(classes)) or "все", len(cells), len(done), len(todo)), flush=True)
    t0, n_days, n_bad, n_none = time.time(), 0, 0, 0
    for i in range(0, len(todo), a.chunk):
        part = todo[i:i + a.chunk]
        mid = {ids[c["cid"]]: c for c in part}
        cw, days = [], []
        for mid_, extra in src.execute("SELECT id, extra FROM metric WHERE id IN (%s)" % ",".join("?" * len(mid)), list(mid)):
            c = mid[mid_]
            e = json.loads(extra)
            daily, dayn = e.get("daily"), e.get("dayn")
            if not daily or not dayn or len(daily) != DAYS or len(dayn) != DAYS:
                n_none += 1
                cw.append((a.run_id, c["cid"], WIN, 0, 0.0, "нет daily/dayn в летописи (len %s / %s)" % (len(daily or []), len(dayn or [])), "chron"))
                continue
            n, usd = int(sum(dayn)), float(sum(daily))
            errs = []
            if c["ref_n"] is not None and n != c["ref_n"]:
                errs.append("n %d≠ref %d" % (n, c["ref_n"]))
            if c["ref_usd"] is not None and abs(usd - c["ref_usd"]) > max(1e-3, 1e-6 * abs(c["ref_usd"])):
                errs.append("usd %.4f≠ref %.4f" % (usd, c["ref_usd"]))
            n_bad += bool(errs)
            cw.append((a.run_id, c["cid"], WIN, n, usd, ";".join(errs), "chron"))
            days += [(a.run_id, c["cid"], base + d, float(daily[d]), int(dayn[d])) for d in range(DAYS) if dayn[d] > 0]
        db.executemany("INSERT OR REPLACE INTO mass_cw VALUES(?,?,?,?,?,?,?)", cw)
        db.executemany("INSERT OR REPLACE INTO mass_day VALUES(?,?,?,?,?)", days)
        db.commit()
        n_days += len(days)
        print("%d / %d клеток, строк mass_day %d, расхождений с ref %d, без ряда %d, %.0f с" % (min(i + a.chunk, len(todo)), len(todo), n_days, n_bad, n_none, time.time() - t0), flush=True)
    if n_none == 0 and n_bad == 0:
        db.execute("UPDATE mass_run SET state='done' WHERE run_id=?", (a.run_id,))
    else:
        db.execute("UPDATE mass_run SET state='done-with-notes' WHERE run_id=?", (a.run_id,))
    db.commit()
    print("запись %s: клеток %d, строк mass_day %d, расхождений %d, без ряда %d, %.0f с" % (a.run_id, len(todo), n_days, n_bad, n_none, time.time() - t0))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("load")
    s.add_argument("--run-id", required=True)
    s.add_argument("--cellset", default="all1")
    s.add_argument("--side", required=True, choices=("long", "short"))
    s.add_argument("--class", dest="cls", default="")
    s.add_argument("--chunk", type=int, default=4000)
    s.add_argument("--cov", default="/data/p42-tk700/cov/cov.json")
    s.add_argument("--db", default=ms.MASS_DB)
    s.add_argument("--chron", default=CHRON_DB)
    a = ap.parse_args(argv)
    return cmd_load(a)


if __name__ == "__main__":
    sys.exit(main())
