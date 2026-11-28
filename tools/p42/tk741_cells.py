#!/usr/bin/env python3
"""TK-741 (В-192): наборы клеток движка — база шорта V0 (якорь s4) и V0nt (то же, `tt=0`: без выхода по времени). Только srv.

  python3 tk741_cells.py build <этап>     # набор `tk741-<этап>` в /data/p42-tk741/mass741.sqlite
  python3 tk741_cells.py list

Этапы: v0 — якорь как есть (`tt=20 tb=0.1`), nt — `tt=0` (в ключе клетки `tb` отброшен каноникой как неактивный, в строке движка `tb=0`; при `tx_min = 0` выход по времени выключен), pr — проба `tt=1` (ключ живой). 10 окон (m01…m06, wa, wb, wc, n1), одна клетка, шорт.
Всё остальное — как V0 в TK-736 (движок, окна, ставка, задержка): код — копия `tk736_cells.py` с одной клеткой.
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

ROOT = os.environ.get("TK741_ROOT", "/data/p42-tk741")
MASS741 = ROOT + "/mass741.sqlite"
WINS = ("m01", "m02", "m03", "m04", "m05", "m06", "wa", "wb", "wc", "n1")
STAGES = {"v0": 1, "nt": 2, "pr": 3}   # этап → номер для cid
TT = {"v0": None, "nt": "0", "pr": "1"}     # значение `tt` этапа; pr — проба «ключ живой» (tt=1: выход по времени через 1 мин после касания), в метрики не входит


def anchor_row(stage):
    """(cid, side, ckey, line, sig, filt, feats, ref_n, ref_usd, ref_daily, db_id, bin) якоря s4; для nt `tt=20` → `tt=0` в ключе и в строке (последнее значение выигрывает)."""
    import tk718_cells as c
    keys0, _ = c.split_ckey(c.ANCHOR["short"])
    if TT[stage] is not None:
        assert float(keys0["tt"]) == 20, keys0
        keys0 = dict(keys0, tt=TT[stage])
    ckey = c.ckey_of("short", keys0, [])
    return c.cell_row("x", 0, "short", ckey)[1:]


def build_rows(stage):
    r = anchor_row(stage)
    cid = 7410000 + 1000 * STAGES[stage] + 1
    cellset = "tk741-" + stage
    row = (cellset, cid, "short", r[2], r[3], r[4], r[5], r[6], 0, 0.0, None, 0, r[11])
    return cellset, row


def cmd_build(a):
    cellset, row = build_rows(a.stage)
    import tk700_cells as cl
    import tk700_mass as ms
    os.makedirs(ROOT, exist_ok=True)
    db = cl.connect(MASS741)
    db.executescript(ms.SCHEMA)
    db.executescript("CREATE TABLE IF NOT EXISTS cellset(name TEXT PRIMARY KEY, created TEXT, params TEXT);"
                     "CREATE TABLE IF NOT EXISTS cell(cellset TEXT, cid INTEGER, side TEXT, ckey TEXT, line TEXT, sig TEXT, filt TEXT, feats TEXT, ref_n INTEGER, ref_usd REAL, ref_daily TEXT,"
                     " db_id INTEGER, bin TEXT, PRIMARY KEY(cellset, cid));")
    db.execute("DELETE FROM cell WHERE cellset=?", (cellset,))
    db.execute("DELETE FROM cellset WHERE name=?", (cellset,))
    db.execute("INSERT INTO cellset VALUES(?,?,?)", (cellset, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(dict(task="TK-741", stage=a.stage, wins=list(WINS)))))
    db.execute("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    db.commit()
    print("%s cid %d %s: %s" % (cellset, row[1], row[2], row[4]))
    return 0


def cmd_list(a):
    for st in STAGES:
        cs, row = build_rows(st)
        print("%-3s cid %d окон %d: %s" % (st, row[1], len(WINS), row[3]))
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
