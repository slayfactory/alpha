#!/usr/bin/env python3
"""TK-739 (П-42 шаг 8, §13.81.1 п. 5, §13.81.4 п. 1.7): разбор замера цены G-cost и запись заморозки — только srv. P&L ($) не читается.

  python3 tk739_ingest.py cost     # время / клеток-окон / сделок из mass739.sqlite (mass_batch, mass_cw.n), c_day, c_pt, правило S → res/gcost.json
  python3 tk739_ingest.py freeze   # tk739-frozen.json: вариант P10, S, δ, T_max, планка DSR (gcount.json), хэш gmap.json, бинарь, база (без кандидата)

Правило (записано до замера, §13.81.1 п. 5): c_day = (t_m04 + t_wb) / (число точек · (d_m04 + d_wb)); c_pt = c_day · 286 · 1,25;
S = наибольшее из {4096, 2048, 1024, 512}: (S + 30 + 128 + 14 + 256) · c_pt ≤ T_max = 16 ч (57 600 с); иначе O6-цена. t — engine_s из mass_batch (стена движка).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time

ROOT = os.environ.get("TK739_ROOT", "/data/p42-tk739")
RES = ROOT + "/res"
MASS739 = ROOT + "/mass739.sqlite"
FREEZE = ROOT + "/tk739-frozen.json"
T_MAX_S = 16 * 3600
S_LADDER = (4096, 2048, 1024, 512)
EXTRA = 30 + 128 + 14 + 256                                          # OAT (≈ 30) + далёкие + оси коробки + Соболь коробки (протокол §13.81.1 п. 5)
SAFETY = 1.25
DAYS_ALL = 286
RUNS = {"m04": "tk739-cost-h1", "wb": "tk739-cost-sel"}
ENGINE_MD5 = "66eab54ed5b28d6ce5bfdb0f21296171"


def jwrite(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def cmd_cost(a):
    gax = json.load(open(RES + "/gaxes.json", encoding="utf-8"))
    d = {"m04": int(gax["d_m04"]), "wb": int(gax["d_wb"])}
    db = sqlite3.connect("file:%s?mode=ro" % MASS739, uri=True, timeout=60)
    rep = dict(note="G-cost: время, клеток-окон, сделки; суммы $ не читаются", runs=RUNS, days=d, per_window={})
    t = {}
    n_pts = None
    for win, run in RUNS.items():
        rows = db.execute("SELECT n_var, n_sig, n_filt, engine_s, cpu_s, post_s, maxrss_mb, rc, utc FROM mass_batch WHERE run_id=? AND win=?", (run, win)).fetchall()
        if not rows:
            raise SystemExit("нет партий %s / %s" % (run, win))
        bad = [r for r in rows if r[7] != 0]
        if bad:
            raise SystemExit("партии с rc != 0 в %s / %s: %s" % (run, win, bad))
        n_var = sum(r[0] for r in rows)
        n_trades, n_cells = db.execute("SELECT COALESCE(SUM(n), 0), COUNT(*) FROM mass_cw WHERE run_id=? AND win=?", (run, win)).fetchone()
        eng = sum(r[3] for r in rows)
        w = dict(batches=len(rows), n_var=int(n_var), n_sig=int(sum(r[1] for r in rows)), n_filt=int(max(r[2] for r in rows)), engine_s=float(eng), cpu_s=float(sum(r[4] for r in rows)), post_s=float(sum(r[5] for r in rows)),
                 maxrss_mb=float(max(r[6] or 0 for r in rows)), cell_windows=int(n_cells), trades=int(n_trades))
        rep["per_window"][win] = w
        t[win] = eng
        n_pts = n_cells if n_pts is None else n_pts
        print("%-4s партий %d, клеток %d, клеток-окон %d, сделок %d, движок %.0f с, ЦП %.0f с, разбор %.0f с, память %.0f МБ, суток окна %d" % (win, len(rows), n_var, n_cells, n_trades, eng, w["cpu_s"], w["post_s"], w["maxrss_mb"], d[win]))
    n_pts = int(rep["per_window"]["m04"]["n_var"])
    rep["n_points"] = n_pts
    c_day = (t["m04"] + t["wb"]) / (n_pts * (d["m04"] + d["wb"]))
    c_pt = c_day * DAYS_ALL * SAFETY
    rep.update(c_day=c_day, c_pt=c_pt, t_max_s=T_MAX_S, extra_points=EXTRA)
    print("c_day = (%.0f + %.0f) / (%d · (%d + %d)) = %.4f с на точку-сутки; c_pt = c_day · %d · %.2f = %.2f с на точку (8 окон)" % (t["m04"], t["wb"], n_pts, d["m04"], d["wb"], c_day, DAYS_ALL, SAFETY, c_pt))
    tab = []
    S = None
    for s in S_LADDER:
        tot = (s + EXTRA) * c_pt
        ok = tot <= T_MAX_S
        tab.append(dict(S=s, points=s + EXTRA, total_s=tot, total_h=tot / 3600, fits=ok))
        print("  S = %4d: (S + %d) · c_pt = %.0f с = %.2f ч %s" % (s, EXTRA, tot, tot / 3600, "≤ T_max" if ok else "> T_max"))
        if ok and S is None:
            S = s
    rep["table"] = tab
    rep["S"] = S
    rep["outcome"] = "S = %d" % S if S else "O6-цена (даже S = 512 не укладывается)"
    # справочно (не решающее): те же числа при реальных счётах из G-axes
    rep["ref_actual_counts"] = {k: {kk: vv for kk, vv in gax[k].items() if kk in ("points", "unique", "total", "axial", "sobol")} for k in ("OAT", "FAR", "BOX_nominal") if k in gax}
    jwrite(RES + "/gcost.json", rep)
    print("→ %s" % rep["outcome"])
    return 0


def cmd_freeze(a):
    cost = json.load(open(RES + "/gcost.json", encoding="utf-8"))
    cnt = json.load(open(RES + "/gcount.json", encoding="utf-8"))
    gsrc = json.load(open(RES + "/gsrc.json", encoding="utf-8"))
    S = a.S or cost["S"]
    if not S:
        raise SystemExit("S не определён (O6-цена)")
    fz = dict(
        step="П-42 шаг 8", protocol="§13.81–§13.81.4 (PS-1084…1088, L-543)", ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        variant="P10", D=10, S=int(S), T_max_s=T_MAX_S, seed=20261127,
        delta=dict(n_bounce=1, k=1, u=0.10, phi_hours=2.4, w_hours=2.0, note="±1 шаг целых; ±0,10 длины интервала u (u_lo, q_hi, F:n60); ±0,10·24 ч (φ); ±0,10·20 ч (w) — §13.81.1 п. 4"),
        dsr=dict(N_eff=cnt["N_eff"], sigma_trials_T286=cnt["sigma_trials_T286"], bar_daily_T286=cnt["bar_daily_T286_at_N_eff"], bar_annual=cnt["bar_annual"], cells_total=cnt["cells_total_chron_cells_count"], onc=cnt["onc"]),
        cost=dict(c_day=cost["c_day"], c_pt=cost["c_pt"], table=cost["table"]),
        base=dict(name="B* = V0 (L-542)", anchor_cid=gsrc.get("anchor_cid", 7180023), m_Bstar=gsrc.get("m_bstar_usd_per_day")),
        engine=dict(path="/data/p42-tk738/bin/alpha-tk738-plain.66eab54e", md5=ENGINE_MD5),
        gmap_sha256=sha256(RES + "/gmap.json"), gaxes_sha256=sha256(RES + "/gaxes.json"),
    )
    jwrite(FREEZE, fz)
    print(json.dumps(fz, ensure_ascii=False, indent=1)[:3000])
    print("sha256 заморозки: %s" % sha256(FREEZE))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("cost").set_defaults(fn=cmd_cost)
    f = sp.add_parser("freeze")
    f.add_argument("--S", type=int, default=0)
    f.set_defaults(fn=cmd_freeze)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
