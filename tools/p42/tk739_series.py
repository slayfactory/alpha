#!/usr/bin/env python3
"""TK-739 (П-42 шаг 8, §13.81.4 п. 1.8–1.9, §13.81.1 п. 6): ряды точек из сделок прогона, G-ctl, G-day — только srv. После прогона `tk739_run.sh design|box|desc`.

  python3 tk739_series.py series <этап>     # сделки → суточные ряды (286 суток: pnl $, число сделок), стопы → res/series-<этап>.npz + таблицы s8_day, s8_trade_n в tk739.sqlite
  python3 tk739_series.py gctl              # G-ctl: 8 точек замера на m02 (прогон ctl) против прогона design — побайтно; B* против диска (TK-718 / TK-726) на всех окнах
  python3 tk739_series.py gday <этап>       # G-day: mass_day против склейки по сделкам (все клетки этапа); B* против tk726_day (SEL)
Сутки — от 01.01.2026 (`tk726_seam.day_of`), 286 суток 01.02–13.11 (строка = сутки − 31). Окна: m02…m06 — прогон `tk739-<этап>-h1`, wa / wb / wc — `…-sel`, n1 — `…-n1`.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")
import tk726_seam as sm  # noqa: E402
import tk736_an as A  # noqa: E402

ROOT = os.environ.get("TK739_ROOT", "/data/p42-tk739")
RES = ROOT + "/res"
TR = ROOT + "/trades"
MASS739 = ROOT + "/mass739.sqlite"
DB739 = ROOT + "/tk739.sqlite"
WINS8 = ("m02", "m03", "m04", "m05", "m06", "wa", "wb", "wc")
LO, HI = sm.day_of("2026-02-01"), sm.day_of("2026-11-13")
ND = HI - LO + 1
SEL0 = sm.day_of("2026-07-01")                                      # mass_day SEL-режима нумерует сутки от начала wa
ANCHOR_CID = 7180023


def run_of(stage, win):
    return "tk739-%s-%s" % (stage, "h1" if win.startswith("m0") else ("sel" if win in ("wa", "wb", "wc") else win))


def cell_path(stage, cid, win):
    return os.path.join(TR, run_of(stage, win), win, "%d-trades.csv" % cid)


def read_run(stage, cid, win):
    p = cell_path(stage, cid, win)
    if not os.path.exists(p):
        return None
    t0 = json.load(open(os.path.join(TR, run_of(stage, win), "t0.json"), encoding="utf-8"))[win]
    return A.read_cell(p, t0, win)


def jwrite(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)


def day_series(tr):
    d = (tr["eday"] - LO).astype(np.int64)
    m = (d >= 0) & (d < ND)
    return np.bincount(d[m], weights=tr["pnl"][m], minlength=ND), np.bincount(d[m], minlength=ND)


def cmd_series(a):
    mdb = sqlite3.connect(MASS739)
    cids = [r[0] for r in mdb.execute("SELECT cid FROM cell WHERE cellset=? ORDER BY cid", ("tk739-" + a.stage,))]
    pnl = np.zeros((len(cids), ND))
    cnt = np.zeros((len(cids), ND), np.int32)
    nst = np.zeros(len(cids), np.int32)
    usd_st = np.zeros(len(cids))
    tl = np.zeros((len(cids), 5))                                   # n выигрышных, Σ выигрышей, n проигрышных, Σ проигрышей, худшая сделка (для R:R, В-169)
    miss = []
    for i, cid in enumerate(cids):
        parts = [read_run(a.stage, cid, w) for w in WINS8]
        if any(p is None for p in parts):
            miss.append((cid, [w for w, p in zip(WINS8, parts) if p is None]))
            continue
        tr = A.concat(parts)
        pnl[i], cnt[i] = day_series(tr)
        st = tr["kind"] == "stop"
        nst[i], usd_st[i] = int(st.sum()), float(tr["pnl"][st].sum())
        w_, l_ = tr["pnl"][tr["pnl"] > 0], tr["pnl"][tr["pnl"] < 0]
        tl[i] = (len(w_), w_.sum(), len(l_), l_.sum(), tr["pnl"].min() if len(tr["pnl"]) else 0.0)
    if miss:
        print("НЕТ файлов сделок: %d клеток, примеры %s" % (len(miss), miss[:3]))
        return 1
    np.savez(RES + "/series-%s.npz" % a.stage, cids=np.array(cids), pnl=pnl, cnt=cnt, nstop=nst, usd_stop=usd_st, tl=tl)
    db = sqlite3.connect(DB739)
    db.executescript("CREATE TABLE IF NOT EXISTS s8_day(stage TEXT, cid INTEGER, day INTEGER, pnl REAL, n INTEGER, PRIMARY KEY(stage, cid, day)) WITHOUT ROWID;"
                     "CREATE TABLE IF NOT EXISTS s8_trade_n(stage TEXT, cid INTEGER, n INTEGER, stops INTEGER, usd_stops REAL, PRIMARY KEY(stage, cid)) WITHOUT ROWID;")
    db.execute("DELETE FROM s8_day WHERE stage=?", (a.stage,))
    db.execute("DELETE FROM s8_trade_n WHERE stage=?", (a.stage,))
    db.executemany("INSERT INTO s8_day VALUES(?,?,?,?,?)", ((a.stage, int(c), int(d), float(pnl[i, d]), int(cnt[i, d])) for i, c in enumerate(cids) for d in np.nonzero(cnt[i])[0]))
    db.executemany("INSERT INTO s8_trade_n VALUES(?,?,?,?,?)", ((a.stage, int(c), int(cnt[i].sum()), int(nst[i]), float(usd_st[i])) for i, c in enumerate(cids)))
    db.commit()
    print("series %s: клеток %d, суток %d, сделок %d, стопов %d (Σ$ не печатается — читает процедура)" % (a.stage, len(cids), ND, int(cnt.sum()), int(nst.sum())))
    return 0


def _bytes(p):
    with open(p, "rb") as f:
        return f.read()


def cmd_gctl(a):
    mdb = sqlite3.connect(MASS739)
    ctl = [r[0] for r in mdb.execute("SELECT cid FROM cell WHERE cellset='tk739-ctl' ORDER BY cid")]
    out = dict(ctl_cells=len(ctl), vs_design={}, base_vs_disk={})
    bad = 0
    for cid in ctl:
        p1, p2 = cell_path("ctl", cid, "m02"), cell_path("design", cid, "m02")
        if not (os.path.exists(p1) and os.path.exists(p2)):
            out["vs_design"][cid] = "нет файла (ctl %s, design %s)" % (os.path.exists(p1), os.path.exists(p2))
            continue
        same = _bytes(p1) == _bytes(p2)
        out["vs_design"][cid] = bool(same)
        bad += (not same)
    bc = _base_cid()
    for w in WINS8:                                                 # B* против диска (прогон TK-718 / TK-726)
        p_disk = os.path.join(sm.WINDOWS[w][2], w, "%d-trades.csv" % ANCHOR_CID)
        p_new = cell_path("design", bc, w)
        if not (os.path.exists(p_disk) and os.path.exists(p_new)):
            out["base_vs_disk"][w] = "нет файла (диск %s, новый %s)" % (os.path.exists(p_disk), os.path.exists(p_new))
            bad += 1
            continue
        b1, b2 = _bytes(p_disk), _bytes(p_new)
        same = b1 == b2
        if not same:                                                # колонки vstop и пр. могли измениться — сравнить разобранные сделки (все числовые поля)
            t0d = json.load(open(os.path.join(sm.WINDOWS[w][2], "t0.json"), encoding="utf-8"))[w]
            t0n = json.load(open(os.path.join(TR, run_of("design", w), "t0.json"), encoding="utf-8"))[w]
            ad, an = sm.read_trades(p_disk, t0d), sm.read_trades(p_new, t0n)
            same = len(ad["net"]) == len(an["net"]) and all(np.array_equal(ad[k], an[k]) for k in ("gross", "fees", "funding", "net", "entry_ms", "exit_ms", "sig_ms", "entry_px_e8"))
            out["base_vs_disk"][w] = "побайтно НЕ равны, разобранные сделки равны: %s (строк %d / %d)" % (same, len(ad["net"]), len(an["net"]))
        else:
            out["base_vs_disk"][w] = True
        bad += (not same)
    out["passed"] = bool(bad == 0)
    jwrite(RES + "/gctl.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:3000], "\nG-ctl", "ПРОЙДЕН" if out["passed"] else "ПРОВАЛЕН")
    return 0 if out["passed"] else 1


def _base_cid():
    db = sqlite3.connect(DB739)
    r = db.execute("SELECT cid FROM s8_role WHERE role='OAT' AND label='base' ORDER BY cid LIMIT 1").fetchone()
    return r[0]


def cmd_gday(a):
    mdb = sqlite3.connect("file:%s?mode=ro" % MASS739, uri=True)
    z = np.load(RES + "/series-%s.npz" % a.stage)
    cids, pnl, cnt = z["cids"], z["pnl"], z["cnt"]
    idx = {int(c): i for i, c in enumerate(cids)}
    out = dict(stage=a.stage, runs={})
    ok = True
    for run, wins in (("tk739-%s-h1" % a.stage, [w for w in WINS8 if w.startswith("m0")]), ("tk739-%s-sel" % a.stage, ["wa", "wb", "wc"])):
        base_off = 0 if run.endswith("-h1") else SEL0
        lo_d = min(sm.day_of(sm.WINDOWS[w][0]) for w in wins)
        hi_d = max(sm.day_of(sm.WINDOWS[w][1]) for w in wins)
        maxd, nbad, nmiss, nrows = 0.0, 0, 0, 0
        for cid, day, p, n in mdb.execute("SELECT cid, day, pnl, n FROM mass_day WHERE run_id=?", (run,)):
            eday = day + base_off
            if eday < lo_d or eday > hi_d or cid not in idx:
                continue
            i, d = idx[cid], eday - LO
            nrows += 1
            maxd = max(maxd, abs(float(p) - float(pnl[i, d])))
            nbad += int(int(n) != int(cnt[i, d]))
        # обратно: сутки со сделками в ряду, которых нет в mass_day
        have = {(c, d + base_off) for c, d in mdb.execute("SELECT cid, day FROM mass_day WHERE run_id=?", (run,))}
        for c, i in idx.items():
            for d in np.nonzero(cnt[i])[0]:
                e = int(d) + LO
                if lo_d <= e <= hi_d and (c, e) not in have:
                    nmiss += 1
        out["runs"][run] = dict(rows=nrows, max_abs_diff_usd=maxd, n_mismatch=nbad, missing_in_mass_day=nmiss, passed=bool(maxd < 1e-6 and nbad == 0 and nmiss == 0))
        ok &= out["runs"][run]["passed"]
    if a.stage == "design":                                         # B* против tk726_day (SEL)
        con = sqlite3.connect("file:%s?mode=ro" % A.TK726, uri=True)
        ref = {int(d): (float(p), int(n)) for d, p, n in con.execute("SELECT day, pnl, n FROM tk726_day WHERE win='sel' AND cid=?", (ANCHOR_CID,))}
        bi = idx[_base_cid()]
        mine = {int(d) + LO: (float(pnl[bi, d]), int(cnt[bi, d])) for d in np.nonzero(cnt[bi])[0] if SEL0 <= int(d) + LO <= HI}
        keys = set(ref) | set(mine)
        maxd = max([0.0] + [abs(mine.get(k, (0.0, 0))[0] - ref.get(k, (0.0, 0))[0]) for k in keys])
        nbad = sum(1 for k in keys if mine.get(k, (0, 0))[1] != ref.get(k, (0, 0))[1])
        out["bstar_vs_tk726_day"] = dict(days=len(keys), max_abs_diff_usd=maxd, n_mismatch=nbad, passed=bool(maxd < 1e-6 and nbad == 0))
        ok &= out["bstar_vs_tk726_day"]["passed"]
    out["passed"] = bool(ok)
    jwrite(RES + "/gday-%s.json" % a.stage, out)
    print(json.dumps(out, ensure_ascii=False, indent=1), "\nG-day", "ПРОЙДЕН" if ok else "ПРОВАЛЕН")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("series", cmd_series), ("gday", cmd_gday)):
        s = sp.add_parser(name)
        s.add_argument("stage", choices=("design", "box"))
        s.set_defaults(fn=fn)
    sp.add_parser("gctl").set_defaults(fn=cmd_gctl)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
