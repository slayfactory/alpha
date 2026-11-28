#!/usr/bin/env python3
"""TK-739 (П-42 шаг 8, §13.81.4 п. 1): ворота до движка — только srv. P&L точек шага 8 не читают (G-src читает сделки базы — их читал шаг 7).

  python3 tk739_gates.py bstar-line      # строка движка B* (V0, якорь s4) — для дампа G-map (`tk739_sig.sh`)
  python3 tk739_gates.py gmap            # G-map: таблица «квантиль → абсолютный порог» по сигнальным минутам B* (дампы `gmap/<окно>/dumps/BSX`, окна m02…m06, wa, wb, wc)
  python3 tk739_gates.py gsrc            # G-src + G-hold: якорь s4 = B*, m_B*, σ суток, стопы, длительность позиций (purge), B9 тех же суток
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")
import tk726_seam as sm  # noqa: E402
import tk727_lib as t7  # noqa: E402
import tk736_an as A  # noqa: E402
import tk739_design as dz  # noqa: E402

ROOT = os.environ.get("TK739_ROOT", "/data/p42-tk739")
RES = ROOT + "/res"
GMAP_DIR = ROOT + "/gmap"
WINS8 = ("m02", "m03", "m04", "m05", "m06", "wa", "wb", "wc")
ASM = {"h1": "/data/p42-tk700/p671/asm", "sel": "/data/p42-tk700/gate/p671/asm"}
ANCHOR_CID = 7180023
DAY_MS = 86_400_000


def jwrite(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)


def bstar_line():
    import tk718_cells as t
    keys0, _ = t.split_ckey(t.ANCHOR["short"])
    return t.to_line("short", dict(keys0, l_days="7", tf="60"))


def cmd_bline(a):
    print(bstar_line())
    return 0


# ───────────────────────────── G-map ─────────────────────────────

def win_env(win):
    """Окружение окна дампа (`tk700_win.py env`): T0 (мс эпохи) и каталог дерева (кэш свечей той же сетки минут, что у дампа)."""
    import datetime
    import subprocess
    env = subprocess.run([sys.executable, os.path.join(HERE, "tk700_win.py"), "env", win], capture_output=True, text=True, check=True).stdout
    e = dict(x.split("=", 1) for x in env.split())
    return int(datetime.datetime.fromisoformat(e["T0"]).replace(tzinfo=datetime.timezone.utc).timestamp() * 1000), e["TREE"]


_cc = {}


def candles_tree(tree, sym):
    """RV60 (numpy, `t7.rv_sqrt_sum`) по свечам дерева дампа; None — нет файла."""
    if (tree, sym) not in _cc:
        fn = os.path.join(tree, "cache", sym + ".b42")
        if not os.path.exists(fn):
            _cc[(tree, sym)] = None
        else:
            with open(fn, "rb") as f:
                n = int.from_bytes(f.read(8)[4:8], "little")
            a = np.fromfile(fn, dtype="<i8", offset=8, count=5 * n).reshape(5, n)
            S = dict(o=a[0].astype(np.float64), h=a[1].astype(np.float64), l=a[2].astype(np.float64), c=a[3].astype(np.float64), pres=a[0] != 0)
            _cc[(tree, sym)] = t7.rv_sqrt_sum(S, 60)
    return _cc[(tree, sym)]


def cmd_gmap(a):
    import pandas as pd
    rep = dict(windows={}, note="G-map §13.81.4 п. 1.2: сигнальные минуты B* без фильтра живости (natr_min = 0), P&L не читается")
    natr, rv, n60 = [], [], []
    diff_max, ncheck = 0.0, 0
    for w in WINS8:
        lo, hi = sm.day_of(sm.WINDOWS[w][0]), sm.day_of(sm.WINDOWS[w][1])
        t0, tree = win_env(w)
        p = "%s/%s/dumps/BSX/short-sigsx.csv" % (GMAP_DIR, w)
        with open(p, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        n_all = len(rows)
        n_in = n_val = 0
        for r in rows:
            sig_ms = int(r["sig_ms"])
            day = (t0 + sig_ms - sm.EPOCH0) // DAY_MS
            if not (lo <= day <= hi):
                continue
            n_in += 1
            rvs = candles_tree(tree, r["sym"])
            if rvs is None:
                continue
            ti = sig_ms // 60000 - 1
            rvt = rvs[ti] if 0 <= ti < len(rvs) else float("nan")
            nv = int(r["natr_e8"]) / 1e6
            if not np.isfinite(rvt) or rvt <= 0 or nv <= 0:
                continue
            n_val += 1
            natr.append(nv)
            rv.append(rvt * 100.0)
        # n60 — строки базы окна (кандидаты S_base: таблица признаков)
        part = "h1" if w.startswith("m") else "sel"
        d = pd.read_parquet("%s/%s-short.parquet" % (ASM[part], w), columns=["meta_base_pass", "n60"])
        v = d.loc[d["meta_base_pass"] > 0, "n60"].to_numpy(float)
        v = v[np.isfinite(v)]
        n60.extend(v.tolist())
        rep["windows"][w] = dict(rows=n_all, in_days=n_in, with_v=n_val, base_rows_n60=int(len(v)))
    natr, rv, n60 = np.array(natr), np.array(rv), np.array(n60)
    gm = dz.GMap(natr, rv, n60)
    qs = [round(0.05 * i, 2) for i in range(2, 20)]
    rep["table"] = dict(u=qs, natr30_pct=[round(float(np.quantile(natr, q)), 3) for q in qs], rv60_pct=[round(float(np.quantile(rv, q)), 3) for q in qs], n60=[round(float(np.quantile(n60, q)), 1) for q in qs])
    rep["n"] = dict(natr30=int(len(natr)), rv60=int(len(rv)), n60=int(len(n60)))
    rep["u_base_natr30_0p4"] = gm.u_base()
    rep["share_rv60_below_base_natr"] = float((rv < dz.BASE_NATR).mean())
    rep["u_lo_range_natr30_pct"] = [gm.thr_natr(0, dz.U_LO[0]), gm.thr_natr(0, dz.U_LO[1])]
    rep["note_base_equiv"] = "эквивалент базы 0,4 %% по NATR30 — доля сигнальных минут ниже 0,4 %% (u_base = %.4f)" % gm.u_base()
    jwrite(RES + "/gmap.json", dict(rep, natr30=natr.round(6).tolist(), rv60=rv.round(6).tolist(), n60=n60.round(3).tolist()))
    print(json.dumps({k: v for k, v in rep.items() if k != "windows"}, ensure_ascii=False))
    print({w: v for w, v in rep["windows"].items()})
    return 0


def load_gmap():
    d = json.load(open(RES + "/gmap.json", encoding="utf-8"))
    return dz.GMap(d["natr30"], d["rv60"], d["n60"])


# ───────────────────────────── G-src + G-hold ─────────────────────────────

def cmd_gsrc(a):
    """Якорь s4 = B*: ключ, файлы, сделки на 286 сутках; m_B* — средний суточный $ (нули входят); σ суток (ddof = 1); стопы; длительность позиций; B9 тех же суток (описательно)."""
    import sqlite3
    rep = {}
    con = sqlite3.connect("file:%s?mode=ro" % A.TK726, uri=True)
    ck = con.execute("SELECT ckey FROM tk726_cell WHERE cid=?", (ANCHOR_CID,)).fetchone()[0]
    rep["anchor_cid"] = ANCHOR_CID
    rep["ckey_db"] = ck
    rep["ckey_mine"] = dz.anchor_ckey()
    rep["ckey_equal"] = bool(ck == rep["ckey_mine"])
    lo, hi = sm.day_of("2026-02-01"), sm.day_of("2026-11-13")
    nd = hi - lo + 1
    rep["days"] = int(nd)
    files = {w: A.disk_v0(ANCHOR_CID, w) is not None for w in WINS8}
    rep["files"] = files
    tr = A.concat([A.disk_v0(ANCHOR_CID, w) for w in WINS8])
    m = (tr["eday"] >= lo) & (tr["eday"] <= hi)
    tr = {k: v[m] for k, v in tr.items()}
    d = np.bincount((tr["eday"] - lo).astype(np.int64), weights=tr["pnl"], minlength=nd)
    cnt = np.bincount((tr["eday"] - lo).astype(np.int64), minlength=nd)
    rep["trades"] = int(len(tr["pnl"]))
    rep["sum_usd"] = float(d.sum())
    rep["m_bstar_usd_per_day"] = float(d.mean())
    rep["sigma_day_usd"] = float(d.std(ddof=1))
    rep["share_days_with_trade"] = float((cnt > 0).mean())
    st = tr["kind"] == "stop"
    rep["stops"] = int(st.sum())
    rep["usd_per_stop"] = float(tr["pnl"][st].mean()) if st.any() else None
    dur = (tr["exit_ms"] - tr["entry_ms"]) / DAY_MS
    rep["hold_days"] = dict(max=float(dur.max()), p99=float(np.percentile(dur, 99)), mean=float(dur.mean()), over_1d=int((dur > 1.0).sum()))
    rep["purge_days"] = int(max(1, math.ceil(float(dur.max())) + 1)) if float(dur.max()) > 1.0 else 1
    rep["rr"] = None
    w_, l_ = tr["pnl"][tr["pnl"] > 0], tr["pnl"][tr["pnl"] < 0]
    if len(w_) and len(l_):
        rep["rr"] = float(w_.mean() / abs(l_.mean()))
    rep["worst_trade_usd"] = float(tr["pnl"].min())
    # B9 тех же суток (описательно): равновесный ряд девяти клеток
    b9 = []
    for name, cid in A.b9_cids():
        t9 = A.concat([A.disk_v0(cid, w) for w in WINS8])
        mm = (t9["eday"] >= lo) & (t9["eday"] <= hi)
        b9.append(np.bincount((t9["eday"][mm] - lo).astype(np.int64), weights=t9["pnl"][mm], minlength=nd))
    eq = np.mean(b9, axis=0)
    rep["b9_equal_weight"] = dict(sum_usd=float(eq.sum()), mean_usd_day=float(eq.mean()), sigma_day=float(eq.std(ddof=1)))
    rep["window_days"] = {w: [sm.day_of(sm.WINDOWS[w][0]), sm.day_of(sm.WINDOWS[w][1])] for w in WINS8}
    jwrite(RES + "/gsrc.json", rep)
    np.save(RES + "/bstar_daily.npy", d)
    np.save(RES + "/bstar_n.npy", cnt)
    print(json.dumps(rep, ensure_ascii=False)[:1800])
    return 0 if rep["ckey_equal"] and all(files.values()) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n, f in (("bstar-line", cmd_bline), ("gmap", cmd_gmap), ("gsrc", cmd_gsrc)):
        sp.add_parser(n).set_defaults(fn=f)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
