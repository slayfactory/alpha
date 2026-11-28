#!/usr/bin/env python3
"""TK-736 (П-42 шаг 7, §13.80): наборы клеток движка для ворот и прогонов V2′ — только srv.

  python3 tk736_cells.py build <этап> [--freeze docs/research/freeze/tk736-frozen.json]   # набор `tk736-<этап>` в mass736.sqlite
  python3 tk736_cells.py list                                                               # этапы, окна, число клеток

Клетки — 9 членов B9 (строки из набора `tk726s`, те же cid-файлы на диске, поэтому V0 перепрогона сравнивается с диском побайтно) + лонг-якорь s3 (`tk718_cells.ANCHOR["long"]`).
Рабочий стоп — ключи `vf=1 vs=8 va=<k>` в конце строки (значение — k, не 1000·k: движок умножает на 1000) (`vm` по умолчанию, `s` — потолок якоря). Этапы:
  v0long  — V0 лонга (без ключей стопа), 10 окон;      gv — k = 1 (va=1), 9 + лонг, 10 окон;   ctl — V0 шорта B9 как есть, m02 и wa (G-ctl);
  kinf    — va=1000000 (d = s), 9 + лонг, m02 (G-k∞);  v2fj — k_FJ, wa wb wc;  v2sel — k_SEL, m02…m06;  v2pool — k_pool, 10 окон.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")

ROOT = os.environ.get("TK736_ROOT", "/data/p42-tk736")
MASS736 = ROOT + "/mass736.sqlite"
MASS726 = "/data/p42-tk726/mass726.sqlite"
TK726 = "/data/p42-tk726/tk726.sqlite"
B_NAMES = ("BS-l14-tf240", "BS-l14-tf30", "BS-l14-tf60", "BS-l3-tf240", "BS-l3-tf30", "BS-l3-tf60", "BS-l7-tf240", "BS-l7-tf30", "BS-l7-tf60")
H1, SEL, N1 = ("m01", "m02", "m03", "m04", "m05", "m06"), ("wa", "wb", "wc"), ("n1",)
ALL10 = H1 + SEL + N1
# этап → (окна, стороны, ключи стопа: None — без; «gv» / «inf» — константы; «k_FJ» / «k_SEL» / «k_pool» — из заморозки), номер для cid
STAGES = {
    "v0long": (ALL10, ("long",), None, 1),
    "gv": (ALL10, ("short", "long"), "gv", 2),
    "ctl": (("m02", "wa"), ("short",), None, 3),
    "kinf": (("m02",), ("short", "long"), "inf", 4),
    "v2fj": (SEL, ("short", "long"), "k_FJ", 5),
    "v2sel": (("m02", "m03", "m04", "m05", "m06"), ("short", "long"), "k_SEL", 6),
    "v2pool": (ALL10, ("short", "long"), "k_pool", 7),
}


def va_of(key, side, freeze):
    """Значение ключа `va` = k (дробное; движок сам делает va_m = round(1000·k), `params.rs: pm`). Протокол v3 писал `va=round(1000·k)` — ошибка записи ключа (amend)."""
    if key == "gv":
        return "1"
    if key == "inf":
        return "1000000"
    return "%g" % round(float(freeze[side][key]), 2)


def b9_rows():
    """[(имя, cid, строка набора tk726s)] — 9 членов B9 по именам из tk726_cell."""
    out = sqlite3.connect("file:%s?mode=ro" % TK726, uri=True)
    names = {name: cid for cid, name in out.execute("SELECT cid, name FROM tk726_cell")}
    mass = sqlite3.connect("file:%s?mode=ro" % MASS726, uri=True)
    rows = []
    for n in B_NAMES:
        r = mass.execute("SELECT cid, side, ckey, line, sig, filt, feats, ref_n, ref_usd, ref_daily, db_id, bin FROM cell WHERE cellset='tk726s' AND cid=?", (names[n],)).fetchone()
        assert r is not None and r[1] == "short" and r[5] == "[]", (n, r and r[:2])
        rows.append((n, r))
    return rows


def long_row():
    import tk718_cells as c
    keys0, _ = c.split_ckey(c.ANCHOR["long"])
    ckey = c.ckey_of("long", keys0, [])
    return c.cell_row("x", 0, "long", ckey)[1:]          # (cid, side, ckey, line, sig, filt, feats, ref_n, ref_usd, ref_daily, db_id, bin)


def build_rows(stage, freeze=None):
    wins, sides, key, no = STAGES[stage]
    cellset = "tk736-" + stage
    rows, names = [], []
    base = 7360000 + 1000 * no
    j = 0
    srcs = ([(n, r) for n, r in b9_rows()] if "short" in sides else []) + ([("L-anchor-s3", (0,) + long_row()[1:])] if "long" in sides else [])
    for name, r in srcs:
        j += 1
        cid = base + j
        side = r[1]
        va = va_of(key, side, freeze) if key else None
        tail = (" vf=1 vs=8 va=%s" % va) if va else ""
        rows.append((cellset, cid, side, r[2] + tail, r[3] + tail, r[4], r[5], r[6], 0, 0.0, None, 0, r[11]))
        names.append((cid, name, side, float(va or 0), r[0]))
    return cellset, wins, sides, rows, names


def cmd_build(a):
    freeze = json.load(open(a.freeze, encoding="utf-8")) if a.freeze and os.path.exists(a.freeze) else None
    if STAGES[a.stage][2] in ("k_FJ", "k_SEL", "k_pool") and freeze is None:
        raise SystemExit("этап %s: нужен файл заморозки (--freeze)" % a.stage)
    cellset, wins, sides, rows, names = build_rows(a.stage, freeze)
    import tk700_cells as cl
    import tk700_mass as ms
    os.makedirs(ROOT, exist_ok=True)
    db = cl.connect(MASS736)
    db.executescript(ms.SCHEMA)
    db.executescript("CREATE TABLE IF NOT EXISTS cellset(name TEXT PRIMARY KEY, created TEXT, params TEXT);"
                     "CREATE TABLE IF NOT EXISTS cell(cellset TEXT, cid INTEGER, side TEXT, ckey TEXT, line TEXT, sig TEXT, filt TEXT, feats TEXT, ref_n INTEGER, ref_usd REAL, ref_daily TEXT,"
                     " db_id INTEGER, bin TEXT, PRIMARY KEY(cellset, cid));"
                     "CREATE TABLE IF NOT EXISTS t736_cell(cellset TEXT, cid INTEGER, name TEXT, side TEXT, va REAL, src_cid INTEGER, PRIMARY KEY(cellset, cid));")
    db.execute("DELETE FROM cell WHERE cellset=?", (cellset,))
    db.execute("DELETE FROM cellset WHERE name=?", (cellset,))
    db.execute("DELETE FROM t736_cell WHERE cellset=?", (cellset,))
    import time
    db.execute("INSERT INTO cellset VALUES(?,?,?)", (cellset, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(dict(ps="PS-1080…1083 v3", stage=a.stage, wins=list(wins)))))
    db.executemany("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.executemany("INSERT INTO t736_cell VALUES(?,?,?,?,?,?)", [(cellset, cid, n, s, va, src) for cid, n, s, va, src in names])
    db.commit()
    for r in rows:
        print("%s cid %d %s: %s" % (cellset, r[1], r[2], r[4]))
    return 0


def cmd_list(a):
    for st, (wins, sides, key, no) in STAGES.items():
        n = (9 if "short" in sides else 0) + (1 if "long" in sides else 0)
        print("%-7s окон %2d (%s), стороны %s, клеток %d → клеток-окон %d, ключ %s" % (st, len(wins), ",".join(wins), "+".join(sides), n, n * len(wins), key))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("stage", choices=sorted(STAGES))
    b.add_argument("--freeze", default="")
    b.set_defaults(fn=cmd_build)
    sp.add_parser("list").set_defaults(fn=cmd_list)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
