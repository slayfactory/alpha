#!/usr/bin/env python3
"""TK-721 (PS-1043 + PS-1044): наборы клеток движка для стресса B шорта — копии 9 членов `tk719b` из `mass718.sqlite` в новую БД `/data/p42-tk721/mass721.sqlite`.

  python3 tk721_cells.py build     # набор `tk721g` (g0: строки без изменений, cid 7210000 + k) и `tk721s` (s2: `slip=2`, cid 7210100 + k; s3: `slip=3`, cid 7210200 + k), k = 0…8 по порядку cid 7180015…7180023

Ключ `sig` (проход сигналов) копируется как есть: `slip` сигналов не меняет. Задержка (`lat` / `latx`) не стрессуется — В-168 (PS-1044 п. 1).

  python3 tk721_cells.py build-long   # набор `tk721l` (лонг B: 9 членов `tk718core`, cid 7180006…7180014 → 7210300 + k; строки без изменений) — ворота L1 / L2 форварда лонга (`tk721_fwd.py`)"""
from __future__ import annotations

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SRC = "/data/p42-tk718/mass718.sqlite"
DST = "/data/p42-tk721/mass721.sqlite"
BASE_CIDS = tuple(range(7180015, 7180024))
G0, S2, S3 = 7210000, 7210100, 7210200
LONG_CIDS = tuple(range(7180006, 7180015))
L0 = 7210300


def with_slip(line, ckey, k):
    """Строка движка и ключ клетки с `slip=k` (в строках B ключа нет — умолчание движка 1 бп; ключ — после `s=…`, как в ключах заморозки)."""
    assert "slip=" not in line and "slip=" not in ckey
    parts = ckey.split()
    i = parts.index("s=4")
    parts.insert(i + 1, "slip=%d" % k)
    return line + " slip=%d" % k, " ".join(parts)


def rows_from(src_rows):
    """src_rows: строки `cell` набора `tk719b` (cellset, cid, side, ckey, line, sig, filt, feats, ref_n, ref_usd, ref_daily, db_id, bin) по cid 7180015…7180023 → строки новых наборов."""
    out = []
    by = {r[1]: r for r in src_rows}
    assert tuple(sorted(by)) == BASE_CIDS, sorted(by)
    for k, cid in enumerate(BASE_CIDS):
        r = by[cid]
        out.append(("tk721g", G0 + k) + tuple(r[2:]))
        for base, kk in ((S2, 2), (S3, 3)):
            line, ckey = with_slip(r[4], r[3], kk)
            out.append(("tk721s", base + k, r[2], ckey, line) + tuple(r[5:]))
    return out


def build():
    import tk700_mass as ms
    src = sqlite3.connect("file:%s?mode=ro" % SRC, uri=True)
    rows = src.execute("SELECT * FROM cell WHERE cellset='tk719b' AND cid BETWEEN 7180015 AND 7180023").fetchall()
    new = rows_from(rows)
    db = ms.connect(DST)
    db.execute("DELETE FROM cell WHERE cellset IN ('tk721g', 'tk721s')")
    db.executemany("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", new)
    db.commit()
    for name in ("tk721g", "tk721s"):
        print(name, db.execute("SELECT count(*), count(DISTINCT sig) FROM cell WHERE cellset=?", (name,)).fetchone())
    return 0


def rows_long(src_rows):
    """src_rows: строки `cell` набора `tk718core` по cid 7180006…7180014 → строки набора `tk721l` (cid = 7210300 + k по возрастанию исходного cid, остальные колонки без изменений)."""
    by = {r[1]: r for r in src_rows}
    assert tuple(sorted(by)) == LONG_CIDS, sorted(by)
    assert all(r[2] == "long" for r in src_rows)
    return [("tk721l", L0 + k) + tuple(by[cid][2:]) for k, cid in enumerate(LONG_CIDS)]


def build_long():
    import tk700_mass as ms
    src = sqlite3.connect("file:%s?mode=ro" % SRC, uri=True)
    rows = src.execute("SELECT * FROM cell WHERE cellset='tk718core' AND cid BETWEEN 7180006 AND 7180014").fetchall()
    db = ms.connect(DST)
    db.execute("DELETE FROM cell WHERE cellset='tk721l'")
    db.executemany("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_long(rows))
    db.commit()
    print("tk721l", db.execute("SELECT count(*), count(DISTINCT sig) FROM cell WHERE cellset='tk721l'").fetchone())
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("build", "build-long"):
        raise SystemExit(__doc__)
    sys.exit(build() if sys.argv[1] == "build" else build_long())
