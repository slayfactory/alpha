#!/usr/bin/env python3
"""TK-745: клетка прогона «30 дней» (`review_loop.sh`) — база V0nt (якорь s4 с `tt=0`, TK-741) плюс ключи `--keys "k=v …"` поверх. Только srv.

  python3 review_cells.py build <каталог прогона> <метка> ["k=v k=v …"]   # набор `rv-<метка>` в <каталог>/mass.sqlite, номер клетки 7450001
  python3 review_cells.py show  ["k=v …"]                                  # ключ клетки и строка движка без записи

Ключи — как в строке движка (`tk718_cells.split_ckey`): `fh=0` — V1 (TK-744), `tt=…`, `bm=…` и т.д.; без `--keys` — ровно V0nt (сделки те же, что `tk741-nt`).
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")

CID = 7450001
WINS = ("wc", "n1")


def parse_keys(s):
    out = {}
    for kv in (s or "").split():
        k, _, v = kv.partition("=")
        if not k or not _:
            raise SystemExit("ключ «%s» — нужен вид k=v" % kv)
        out[k] = v
    return out


def row_for(tag, keys):
    import tk718_cells as c
    keys0, _ = c.split_ckey(c.ANCHOR["short"])
    assert float(keys0["tt"]) == 20, keys0
    unknown = [k for k in keys if k not in keys0]
    if unknown:
        raise SystemExit("нет такого ключа в якоре s4: %s (есть: %s)" % (",".join(unknown), ",".join(sorted(keys0))))
    keys0 = dict(keys0, tt="0", **keys)
    ckey = c.ckey_of("short", keys0, [])
    r = c.cell_row("x", 0, "short", ckey)[1:]
    return "rv-" + tag, ("rv-" + tag, CID, "short", r[2], r[3], r[4], r[5], r[6], 0, 0.0, None, 0, r[11])


def cmd_build(root, tag, keys):
    cellset, row = row_for(tag, keys)
    import tk700_cells as cl
    import tk700_mass as ms
    os.makedirs(root, exist_ok=True)
    db = cl.connect(os.path.join(root, "mass.sqlite"))
    db.executescript(ms.SCHEMA)
    db.executescript("CREATE TABLE IF NOT EXISTS cellset(name TEXT PRIMARY KEY, created TEXT, params TEXT);"
                     "CREATE TABLE IF NOT EXISTS cell(cellset TEXT, cid INTEGER, side TEXT, ckey TEXT, line TEXT, sig TEXT, filt TEXT, feats TEXT, ref_n INTEGER, ref_usd REAL, ref_daily TEXT,"
                     " db_id INTEGER, bin TEXT, PRIMARY KEY(cellset, cid));")
    db.execute("DELETE FROM cell WHERE cellset=?", (cellset,))
    db.execute("DELETE FROM cellset WHERE name=?", (cellset,))
    db.execute("INSERT INTO cellset VALUES(?,?,?)", (cellset, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(dict(task="TK-745", tag=tag, keys=keys, wins=list(WINS)))))
    db.execute("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    db.commit()
    print("%s cid %d: %s" % (cellset, CID, row[4]))
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["build"]:
        sys.exit(cmd_build(a[1], a[2], parse_keys(a[3] if len(a) > 3 else "")))
    if a[:1] == ["show"]:
        cs, r = row_for("show", parse_keys(a[1] if len(a) > 1 else ""))
        print(r[3], "\n", r[4])
        sys.exit(0)
    raise SystemExit(__doc__)
