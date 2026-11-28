#!/usr/bin/env python3
"""TK-744 (В-195): набор клеток движка — V1 = V0nt (TK-741, `tt=0`) с `fh=0`: свежесть уровня только по правилу (б) «наторговка» (`fm=50`; src/breakout/tick.rs `fresh_ok`). Только srv.

  python3 tk744_cells.py build fh0       # набор `tk744-fh0` в /data/p42-tk744/mass744.sqlite
  python3 tk744_cells.py list

V0nt — якорь s4 (`tt=20 tb=0.1`) с `tt=0`; V1 — то же и `fh=0`: правило (а) «касание за 4 ч» выключено, остаётся (б) «не меньше 50 % закрытий из 120 мин в полосе 0,5 % под уровнем и ни одного за ним».
10 окон (m01…m06, wa, wb, wc, n1), одна клетка, шорт; движок, окна, ставка, задержка — как в TK-741.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")

ROOT = os.environ.get("TK744_ROOT", "/data/p42-tk744")
MASS744 = ROOT + "/mass744.sqlite"
WINS = ("m01", "m02", "m03", "m04", "m05", "m06", "wa", "wb", "wc", "n1")
STAGES = {"fh0": 1}                       # этап → номер для cid
PATCH = {"fh0": {"tt": "0", "fh": "0"}}   # значения ключей поверх якоря s4


def anchor_row(stage):
    """(cid, side, ckey, line, sig, filt, feats, ref_n, ref_usd, ref_daily, db_id, bin) якоря s4 с заменой ключей этапа."""
    import tk718_cells as c
    keys0, _ = c.split_ckey(c.ANCHOR["short"])
    assert float(keys0["tt"]) == 20 and float(keys0["fh"]) == 4, keys0
    keys0 = dict(keys0, **PATCH[stage])
    ckey = c.ckey_of("short", keys0, [])
    return c.cell_row("x", 0, "short", ckey)[1:]


def build_rows(stage):
    r = anchor_row(stage)
    cid = 7440000 + 1000 * STAGES[stage] + 1
    cellset = "tk744-" + stage
    row = (cellset, cid, "short", r[2], r[3], r[4], r[5], r[6], 0, 0.0, None, 0, r[11])
    return cellset, row


def cmd_build(a):
    cellset, row = build_rows(a.stage)
    import tk700_cells as cl
    import tk700_mass as ms
    os.makedirs(ROOT, exist_ok=True)
    db = cl.connect(MASS744)
    db.executescript(ms.SCHEMA)
    db.executescript("CREATE TABLE IF NOT EXISTS cellset(name TEXT PRIMARY KEY, created TEXT, params TEXT);"
                     "CREATE TABLE IF NOT EXISTS cell(cellset TEXT, cid INTEGER, side TEXT, ckey TEXT, line TEXT, sig TEXT, filt TEXT, feats TEXT, ref_n INTEGER, ref_usd REAL, ref_daily TEXT,"
                     " db_id INTEGER, bin TEXT, PRIMARY KEY(cellset, cid));")
    db.execute("DELETE FROM cell WHERE cellset=?", (cellset,))
    db.execute("DELETE FROM cellset WHERE name=?", (cellset,))
    db.execute("INSERT INTO cellset VALUES(?,?,?)", (cellset, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(dict(task="TK-744", stage=a.stage, wins=list(WINS)))))
    db.execute("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    db.commit()
    print("%s cid %d %s: %s" % (cellset, row[1], row[2], row[4]))
    return 0


def cmd_list(a):
    for st in STAGES:
        cs, row = build_rows(st)
        print("%-4s cid %d окон %d: %s" % (st, row[1], len(WINS), row[3]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("stage", choices=sorted(STAGES))
    b.set_defaults(fn=cmd_build)
    sp.add_parser("list").set_defaults(fn=cmd_list)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
