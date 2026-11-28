"""TK-730 (П-TK729 §6, PS-1066): анализ — суточные ряды ботов C0 и MRf по монетам, 6.1 постоянство, 6.2 признаки, 6.3 корзина.

    python tk730_an.py daily --out /data/tk730 --res /data/tk729    # суточные P&L и «торговля» по монетам из готовых прогонов TK-729 (без пересчёта ботов)
    python tk730_an.py s61   --out /data/tk730                       # §6.1
    python tk730_an.py s62   --out /data/tk730                       # §6.2 (нужны feat/*.npz)
    python tk730_an.py s63   --out /data/tk730                       # §6.3
Бот C0 — base/`C0`, MRf — frz/`ABf_MR_c1.5_h120` (MR(1,5; 120) с замороженным центром, §4).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402
import tk730_lib as W  # noqa: E402

BOTS = {"C0": ("base", "C0"), "MRf": ("frz", "ABf_MR_c1.5_h120")}
BOT_NAMES = tuple(BOTS)


def build_daily(res: str, out: str) -> dict:
    """Читает res/<фаза>/<SYM>.npz готовых прогонов TK-729: суточные P&L и число выставленных входов (R_NOPEN) по монетам, 328 суток отчёта."""
    syms = L.read_syms()
    d = {}
    for bot, (ph, name) in BOTS.items():
        pnl = np.zeros((len(syms), L.NOUT))
        nop = np.zeros((len(syms), L.NOUT))
        for i, s in enumerate(syms):
            z = np.load(os.path.join(res, "res", ph, s + ".npz"), allow_pickle=False)
            names = [str(x) for x in z["runs"]]
            j = names.index(name)
            pnl[i] = z["out"][j, L.R_PNL]
            nop[i] = z["out"][j, L.R_NOPEN]
        d[bot + "_pnl"] = pnl
        d[bot + "_nopen"] = nop
    os.makedirs(out, exist_ok=True)
    np.savez(os.path.join(out, "daily.npz"), syms=np.array(syms), **d)
    return d


def load_daily(out: str):
    z = np.load(os.path.join(out, "daily.npz"), allow_pickle=False)
    syms = [str(x) for x in z["syms"]]
    return syms, {b: (z[b + "_pnl"], z[b + "_nopen"]) for b in BOT_NAMES}


def bot_weekly(pnl_d: np.ndarray, nop_d: np.ndarray):
    """Недельные P&L и «торговля» (≥ 1 выставленный вход за неделю) по монетам; (ncoin, NW)."""
    return W.weekly(pnl_d), W.weekly(nop_d) > 0


def half_weeks(h: str):
    return W.H1W if h == "H1" else W.H2W


def db(out: str):
    return sqlite3.connect(os.path.join(out, "tk730.sqlite"))


def write_rows(out: str, table: str, cols: list[str], rows: list[tuple], replace: bool = True):
    con = db(out)
    if replace:
        con.execute("DROP TABLE IF EXISTS %s" % table)
    con.execute("CREATE TABLE IF NOT EXISTS %s (%s)" % (table, ", ".join(cols)))
    con.executemany("INSERT INTO %s VALUES (%s)" % (table, ",".join("?" * len(cols))), rows)
    con.commit()
    con.close()


def jclean(o):
    if isinstance(o, dict):
        return {str(k): jclean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jclean(v) for v in o]
    if isinstance(o, np.ndarray):
        return jclean(o.tolist())
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


def stage_s61(out: str):
    syms, D = load_daily(out)
    res = {}
    rows, srows = [], []
    for bot in BOT_NAMES:
        pw, tw = bot_weekly(*D[bot])
        r = W.persistence(pw, tw)
        # описательно по половинам (пары недель, обе недели в половине)
        halves = {}
        for h in ("H1", "H2"):
            ks = [k for k in half_weeks(h) if k + 1 in half_weeks(h)]
            halves[h] = dict(rho=W.boot_week(r["rho"][ks]), diff=W.boot_week(r["diff"][ks]))
        res[bot] = dict(rho_b=r["rho_b"], top_b=r["top_b"], all_b=r["all_b"], diff_b=r["diff_b"], halves=halves,
                        top_sum_mean=float(np.nanmean(r["top_sum"])), ncoin_mean=float(np.mean(r["ncoin"])), ntop_mean=float(np.mean(r["ntop"])),
                        traded_share=float(tw.mean()), total_usd=float(pw.sum()))
        for k in range(len(r["rho"])):
            rows.append((bot, k, W.week_date(k), float(r["rho"][k]), int(r["ncoin"][k]), float(r["top_m"][k]), float(r["all_m"][k]), float(r["diff"][k]), float(r["top_sum"][k]), int(r["ntop"][k])))
        for nm in ("rho_b", "top_b", "all_b", "diff_b"):
            b = r[nm]
            srows.append((bot, nm, b["mean"], b["lo"], b["hi"], b["p"], b["n"]))
    write_rows(out, "t730_61", ["bot", "k", "week", "rho", "ncoin", "top_mean", "all_mean", "diff", "top_sum", "ntop"], rows)
    write_rows(out, "t730_61s", ["bot", "name", "mean", "lo", "hi", "p", "n"], srows)
    json.dump(jclean(res), open(os.path.join(out, "r61.json"), "w"), ensure_ascii=False, indent=1)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("daily", "s61", "s62", "s63"))
    ap.add_argument("--out", default="/data/tk730")
    ap.add_argument("--res", default="/data/tk729")
    a = ap.parse_args()
    if a.stage == "daily":
        d = build_daily(a.res, a.out)
        for bot in BOT_NAMES:  # сверка с отчётом TK-729 (Σ MRf: +35 $ на 01.07–24.11, −129 $ на 21.01–30.06; C0 −90 410 / −86 806 $ на половинах)
            p = d[bot + "_pnl"]
            h1, h2 = L.HALVES["H1"], L.HALVES["H2"]
            print(bot, "H1 %.0f" % p[:, h1[0]:h1[1]].sum(), "H2 %.0f" % p[:, h2[0]:h2[1]].sum())
    elif a.stage == "s61":
        r = stage_s61(a.out)
        for bot in BOT_NAMES:
            print(bot, json.dumps(jclean(r[bot]), ensure_ascii=False)[:1500])
    else:
        import tk730_basket as B  # noqa: E402
        (B.stage_s62 if a.stage == "s62" else B.stage_s63)(a.out)


if __name__ == "__main__":
    main()
