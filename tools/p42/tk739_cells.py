#!/usr/bin/env python3
"""TK-739 (П-42 шаг 8, §13.81.1): наборы клеток движка для замера цены, прогона дизайна, G-ctl, коробки, N1 / января — только srv.

  python3 tk739_cells.py build <этап> [--S N] [--freeze /data/p42-tk739/tk739-frozen.json]   # набор `tk739-<этап>` в mass739.sqlite, точки и роли — в tk739.sqlite
  python3 tk739_cells.py list                                                                # этапы, окна
  python3 tk739_cells.py design-print [--S N]                                                # G-axes: таблица дизайна без записи (печать, дубли, замены)

Этапы: cost — первые 8 точек R × (m04, wb) (замер цены); design — R(S) ∪ OAT ∪ далёкие × 8 окон; ctl — 8 точек замера + B* × m02 (G-ctl); box — 14 + 256 точек вокруг замороженного кандидата × 8 окон;
desc — кандидат, B*, 14 осевых соседей × (n1, m01). Клетка = (cellset, cid, 'short', ckey, line, sig, filt_json, feats, 0, 0.0, None, 0, BIN); cid — один на `cell_id` (ckey) во всех этапах (s8_point).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")
import tk739_design as dz  # noqa: E402

ROOT = os.environ.get("TK739_ROOT", "/data/p42-tk739")
MASS739 = ROOT + "/mass739.sqlite"
DB739 = ROOT + "/tk739.sqlite"
RES = ROOT + "/res"
FREEZE = ROOT + "/tk739-frozen.json"
WINS8 = ("m02", "m03", "m04", "m05", "m06", "wa", "wb", "wc")
CID0 = 7390000
STAGES = {
    "cost": (("m04", "wb"), 1),
    "design": (WINS8, 2),
    "ctl": (("m02",), 3),
    "box": (WINS8, 4),
    "desc": (("n1", "m01"), 5),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS s8_point(cid INTEGER PRIMARY KEY, ckey TEXT UNIQUE, line TEXT, keys TEXT, filt TEXT);
CREATE TABLE IF NOT EXISTS s8_role(cid INTEGER, role TEXT, seq INTEGER, label TEXT, coords TEXT, u TEXT, PRIMARY KEY(cid, role, seq)) WITHOUT ROWID;
"""


def db739():
    os.makedirs(ROOT, exist_ok=True)
    c = sqlite3.connect(DB739, timeout=120)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


def jcoords(c):
    return json.dumps({k: (list(v) if isinstance(v, tuple) else v) for k, v in c.items()}, sort_keys=True)


def load_gm():
    d = json.load(open(RES + "/gmap.json", encoding="utf-8"))
    return dz.GMap(d["natr30"], d["rv60"], d["n60"])


def register(db, cell):
    """cid по cell_id (ckey): новый — следующий номер."""
    r = db.execute("SELECT cid FROM s8_point WHERE ckey=?", (cell["ckey"],)).fetchone()
    if r:
        return r[0]
    cid = CID0 + 1 + db.execute("SELECT COUNT(*) FROM s8_point").fetchone()[0]
    db.execute("INSERT INTO s8_point VALUES(?,?,?,?,?)", (cid, cell["ckey"], cell["line"], json.dumps(cell["keys"], sort_keys=True), json.dumps(cell["filt_json"])))
    return cid


def role_row(db, cid, role, seq, label, coords=None, u=None):
    db.execute("INSERT OR REPLACE INTO s8_role VALUES(?,?,?,?,?,?)", (cid, role, seq, label, jcoords(coords) if coords is not None else "", json.dumps([float(x) for x in u]) if u is not None else ""))


def frozen(path=FREEZE):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None


def stage_points(stage, gm, S=None, fz=None):
    """[(роль, номер, метка, координаты | None, u | None, ячейка)] для этапа. R — первые S допустимых точек Соболя (допустимость — `point_cell`); OAT — база и точки по осям; FAR — 128 далёких."""
    out = []
    if stage in ("cost", "ctl"):
        rows, _rep = dz.design_R(gm, 8)
        out += [("R", i, "meas%d" % i, r["coords"], r["u"], r["cell"]) for i, r in enumerate(rows)]
        if stage == "ctl":
            b = dz.base_coords(gm)
            out.append(("OAT", 0, "base", b, None, dz.point_cell(b, gm)))
    elif stage == "design":
        rows, _rep = dz.design_R(gm, S)
        out += [("R", i, "R%d" % r["seq"], r["coords"], r["u"], r["cell"]) for i, r in enumerate(rows)]
        for j, (lab, c) in enumerate(dz.design_oat(gm)):
            cell = dz.point_cell(c, gm)
            if not cell["valid"]:
                print("OAT %s: недопустима (%s) — пропуск" % (lab, cell["why"]))
                continue
            out.append(("OAT", j, lab, c, None, cell))
        for r in dz.design_far(gm):
            out.append(("FAR", r["seq"], "far%d" % r["seq"], r["far"], r["u"], r["cell"]))
    elif stage in ("box", "desc"):
        c = {k: (tuple(v) if k == "LS" else v) for k, v in fz["candidate"]["coords"].items()}
        cell = dz.point_cell(c, gm)
        if stage == "box":
            for j, (lab, d2) in enumerate(dz.box_axial(c, gm)):
                out.append(("BOX", j, "ax:" + lab, d2, None, dz.point_cell(d2, gm)))
            for j, d2 in enumerate(dz.box_sobol(c, gm)):
                out.append(("BOX", 100 + j, "sb%d" % j, d2, None, dz.point_cell(d2, gm)))
        else:
            out.append(("CAND", 0, "cand", c, None, cell))
            b = dz.base_coords(gm)
            out.append(("OAT", 0, "base", b, None, dz.point_cell(b, gm)))
            for j, (lab, d2) in enumerate(dz.box_axial(c, gm)):
                out.append(("BOX", j, "ax:" + lab, d2, None, dz.point_cell(d2, gm)))
    return out


def cmd_build(a):
    gm = load_gm()
    fz = frozen(a.freeze)
    S = a.S or (fz or {}).get("S")
    if a.stage == "design" and not S:
        raise SystemExit("design: нужен S (--S или заморозка)")
    if a.stage in ("box", "desc") and not fz:
        raise SystemExit("%s: нужна заморозка кандидата (--freeze)" % a.stage)
    pts = stage_points(a.stage, gm, S, fz)
    wins, no = STAGES[a.stage]
    import tk700_cells as cl
    import tk700_mass as ms
    db = db739()
    mdb = cl.connect(MASS739)
    mdb.executescript(ms.SCHEMA)
    mdb.executescript("CREATE TABLE IF NOT EXISTS cellset(name TEXT PRIMARY KEY, created TEXT, params TEXT);"
                      "CREATE TABLE IF NOT EXISTS cell(cellset TEXT, cid INTEGER, side TEXT, ckey TEXT, line TEXT, sig TEXT, filt TEXT, feats TEXT, ref_n INTEGER, ref_usd REAL, ref_daily TEXT,"
                      " db_id INTEGER, bin TEXT, PRIMARY KEY(cellset, cid));")
    cellset = "tk739-" + a.stage
    mdb.execute("DELETE FROM cell WHERE cellset=?", (cellset,))
    mdb.execute("DELETE FROM cellset WHERE name=?", (cellset,))
    rows, seen, raw, bad = [], set(), 0, 0
    for role, seq, label, coords, u, cell in pts:
        if not cell["valid"]:
            bad += 1
            continue
        raw += 1
        cid = register(db, cell)
        role_row(db, cid, role, seq, label, coords, u)
        if cid not in seen:
            seen.add(cid)
            rows.append((cellset, cid, dz.SIDE, cell["ckey"], cell["line"], cell["sig"], json.dumps(cell["filt_json"]), cell["feats"], 0, 0.0, None, 0, dz.BIN_SHORT))
    mdb.execute("INSERT INTO cellset VALUES(?,?,?)", (cellset, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(dict(ps="PS-1084…1088", stage=a.stage, wins=list(wins), S=S))))
    mdb.executemany("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    mdb.commit()
    db.commit()
    print("%s: точек %d (сырых допустимых), уникальных клеток %d, недопустимых отброшено %d; окна %s" % (cellset, raw, len(rows), bad, ",".join(wins)))
    return 0


def cmd_list(a):
    for st, (wins, no) in STAGES.items():
        print("%-7s окна %s" % (st, ",".join(wins)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("stage", choices=sorted(STAGES))
    b.add_argument("--S", type=int, default=0)
    b.add_argument("--freeze", default=FREEZE)
    b.set_defaults(fn=cmd_build)
    sp.add_parser("list").set_defaults(fn=cmd_list)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
