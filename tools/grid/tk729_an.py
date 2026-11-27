"""TK-729: анализ результатов прогона — метрики §1, выбор (a, q) по Шарпу на одной половине и проверка на другой (§2), абляции, решение по правилу, SQLite (В-179).

    python tk729_an.py select --out /data/tk729      # после фазы base: пишет select.json (вход фазы abl) и select-report.json
    python tk729_an.py final  --out /data/tk729      # после фазы abl: result.json, tables.md, tk729.sqlite
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_cfg as C  # noqa: E402
import tk729_lib as L  # noqa: E402

NB = (7, 21)
HALF = {"H1": L.HALVES["H1"], "H2": L.HALVES["H2"]}
DIRS = (("H1", "H2"), ("H2", "H1"))  # (выбор, проверка)
MARGIN_PER_COIN = 250.0


def load_res(out: str, phases=("base", "abl")):
    """→ runs {имя: (ncoin, NR, 328)}, syms, present (ncoin, 328), turn, natr."""
    runs, syms, pres, turn, natr = {}, None, None, None, None
    for ph in phases:
        fs = sorted(glob.glob(os.path.join(out, "res", ph, "*.npz")))
        if not fs:
            continue
        names = None
        data = [np.load(f, allow_pickle=False) for f in fs]
        s = [os.path.basename(f)[:-4] for f in fs]
        if syms is None:
            syms = s
            pres = np.stack([d["present"] for d in data])
            turn = np.array([float(d["turn"]) for d in data])
            natr = np.array([float(d["natr"]) for d in data])
        elif s != syms:
            raise RuntimeError("наборы монет фаз различаются: %s" % ph)
        names = [str(x) for x in data[0]["runs"]]
        for i, nm in enumerate(names):
            runs[nm] = np.stack([d["out"][i] for d in data])
    return runs, syms, pres, turn, natr


def months_in(d0: int, d1: int):
    return [(m, max(a, d0), min(b, d1)) for m, (a, b) in L.PERIODS.items() if max(a, d0) < min(b, d1)]


def tercile_table(coin_tot, key, ok):
    """Σ, число монет и монет в плюсе по третям `key` (границы по монетам с ok)."""
    idx = np.where(ok & np.isfinite(key))[0]
    if len(idx) < 3:
        return []
    lo, hi = np.quantile(key[idx], [1 / 3, 2 / 3])
    out = []
    for name, sel in (("низ", key[idx] <= lo), ("середина", (key[idx] > lo) & (key[idx] <= hi)), ("верх", key[idx] > hi)):
        j = idx[sel]
        out.append(dict(tercile=name, coins=int(len(j)), usd=float(coin_tot[j].sum()), plus=int((coin_tot[j] > 0).sum())))
    return out


def half_metrics(arr, present, turn, natr, d0, d1, boot=True):
    pnl = arr[:, L.R_PNL, d0:d1]
    daily = pnl.sum(0)
    coin_tot = pnl.sum(1)
    tot = float(pnl.sum())
    cyc_n = int(round(arr[:, L.R_NCYC, d0:d1].sum()))
    cyc_usd = float(arr[:, L.R_CYC, d0:d1].sum())
    exit_usd = float(arr[:, L.R_EXIT, d0:d1].sum())
    cd = int(present[:, d0:d1].sum())
    act = arr[:, L.R_ACT, d0:d1]
    in_work = act.sum(0)  # монет в работе по суткам
    margin = MARGIN_PER_COIN * float(in_work.mean())
    dd = L.drawdown(daily)
    active_coin = arr[:, L.R_NOPEN, d0:d1].sum(1) > 0
    order = np.argsort(coin_tot)
    best10, worst10 = float(coin_tot[order[-10:]].sum()), float(coin_tot[order[:10]].sum())
    mon = {}
    for m, a, b in months_in(d0, d1):
        mon[m] = float(daily[a - d0: b - d0].sum())
    res = dict(
        usd=tot, cycles=cyc_n, usd_per_cycle=(cyc_usd / cyc_n if cyc_n else float("nan")), usd_per_coin_day=tot / cd if cd else float("nan"),
        coin_days=cd, active_coin_days=int(act.sum()), mean_coins_in_work=float(in_work.mean()), margin=margin,
        sharpe=L.sharpe_ann(daily), dd=dd["dd"], dd_pct=100.0 * dd["dd"] / margin if margin > 0 else float("nan"),
        hw_max=dd["hw_max"], hw_mean=dd["hw_mean"], hw_p90=dd["hw_p90"],
        cyc_usd=cyc_usd, exit_usd=exit_usd, other_usd=tot - cyc_usd - exit_usd,
        n_edge=int(arr[:, L.R_NEDGE, d0:d1].sum()), n_time=int(arr[:, L.R_NTIME, d0:d1].sum()), opens=int(arr[:, L.R_NOPEN, d0:d1].sum()), n_mkt=int(arr[:, L.R_NMKT, d0:d1].sum()),
        coins_active=int(active_coin.sum()), coins_plus=int(((coin_tot > 0) & active_coin).sum()), best10=best10, worst10=worst10, ex_best10=tot - best10,
        months=mon, months_plus=int(sum(v > 0 for v in mon.values())), months_n=len(mon),
        terc_natr=tercile_table(coin_tot, natr, active_coin), terc_turn=tercile_table(coin_tot, turn, active_coin),
        daily=daily.tolist(),
    )
    if boot:
        res["boot"] = {b: L.stat_block(daily, b, L.NBOOT, L.SEED) for b in NB}
    return res


def paired(a_daily, b_daily):
    d = np.asarray(a_daily) - np.asarray(b_daily)
    return {b: L.stat_block(d, b, L.NBOOT, L.SEED) for b in NB}, float(d.sum())


def noise_bar(n_days: int, trials: int = 6) -> float:
    """Ожидаемый максимум годового Шарпа из шума при выборе из `trials` вариантов (selection_bias.py): se годового Шарпа нулевой гипотезы ≈ √(365/n)."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".claude", "skills", "alpha-research", "scripts"))
    try:
        from selection_bias import expected_max_sharpe
    except ImportError:  # на srv копия лежит рядом с кодом
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from selection_bias import expected_max_sharpe

    return expected_max_sharpe(math.sqrt(365.0 / n_days), trials)


def select_step(out: str):
    runs, syms, pres, turn, natr = load_res(out, ("base",))
    ag = [v["name"] for v in C.base_variants() if v["kind"] == "MR"]
    sel = {}
    rep = {"ag": ag, "dirs": []}
    for s_h, t_h in DIRS:
        d0, d1 = HALF[s_h]
        sh = {nm: L.sharpe_ann(runs[nm][:, L.R_PNL, d0:d1].sum(0)) for nm in ag}
        best = max(ag, key=lambda k: sh[k])
        v = [x for x in C.base_variants() if x["name"] == best][0]
        sel[s_h] = dict(sel_half=s_h, test_half=t_h, name=best, c=v["c"], hlmax=v["hlmax"], sharpes=sh)
        rep["dirs"].append(sel[s_h])
    json.dump(dict(dirs=[dict(name=r["name"], c=r["c"], hlmax=r["hlmax"], sel_half=r["sel_half"], test_half=r["test_half"]) for r in rep["dirs"]]), open(os.path.join(out, "select.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(rep, open(os.path.join(out, "select-report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for r in rep["dirs"]:
        print(r["sel_half"], "→", r["test_half"], r["name"], {k: round(v, 2) for k, v in r["sharpes"].items()})


def check_plus(tm, ag_vs_c0):
    """Условия «плюс есть» для одного направления на проверочной половине."""
    c = {}
    c["lo_gt0_7"] = tm["boot"][7]["lo"] > 0
    c["lo_gt0_21"] = tm["boot"][21]["lo"] > 0
    need = math.ceil(2 * tm["months_n"] / 3)
    c["months_ge_2_3"] = tm["months_plus"] >= need
    c["ex_best10_gt0"] = tm["ex_best10"] > 0
    c["vs_c0_lo_gt0_7"] = ag_vs_c0[7]["lo"] > 0
    c["vs_c0_lo_gt0_21"] = ag_vs_c0[21]["lo"] > 0
    c["months_need"] = need
    return c


def final_step(out: str):
    runs, syms, pres, turn, natr = load_res(out)
    sel = json.load(open(os.path.join(out, "select.json"), encoding="utf-8"))["dirs"]
    allv = {v["name"]: v for v in C.base_variants()}
    results = {"syms": len(syms), "dirs": [], "halves": {}}
    # метрики всех вариантов на обеих половинах (описательно; решения — только по выбранным)
    M = {}
    for nm, arr in runs.items():
        for h, (d0, d1) in HALF.items():
            M[(nm, h)] = half_metrics(arr, pres, turn, natr, d0, d1)
    ok_dirs = []
    for d in sel:
        s_h, t_h = d["sel_half"], d["test_half"]
        nm = d["name"]
        tm, c0 = M[(nm, t_h)], M[("C0", t_h)]
        vs_c0, vs_c0_sum = paired(tm["daily"], c0["daily"])
        chk = check_plus(tm, vs_c0)
        abl = {}
        for ab in C.ABL:
            an = "%s_%s" % (ab, nm)
            if an in runs:
                am = M[(an, t_h)]
                dl, dsum = paired(am["daily"], tm["daily"])
                abl[ab] = dict(name=an, usd=am["usd"], sharpe=am["sharpe"], dd=am["dd"], months_plus=am["months_plus"], delta_usd=dsum,
                               delta_boot={b: dict(mean=v["mean"], lo=v["lo"], hi=v["hi"]) for b, v in dl.items()})
        nbar = noise_bar(len(tm["daily"]))
        results["dirs"].append(dict(sel_half=s_h, test_half=t_h, name=nm, c=d["c"], hlmax=d["hlmax"], sel_sharpe=M[(nm, s_h)]["sharpe"], noise_bar_sharpe=nbar, noise_bar_sharpe_17=noise_bar(len(tm["daily"]), 17), test_usd=tm["usd"], test_sharpe=tm["sharpe"],
                                    vs_c0={b: dict(mean=v["mean"], lo=v["lo"], hi=v["hi"]) for b, v in vs_c0.items()}, vs_c0_usd=vs_c0_sum, checks=chk, ablations=abl))
        ok_dirs.append(all(chk[k] for k in ("lo_gt0_7", "lo_gt0_21", "months_ge_2_3", "ex_best10_gt0", "vs_c0_lo_gt0_7", "vs_c0_lo_gt0_21")))
    disagree = any(r["checks"]["lo_gt0_7"] != r["checks"]["lo_gt0_21"] or r["checks"]["vs_c0_lo_gt0_7"] != r["checks"]["vs_c0_lo_gt0_21"] for r in results["dirs"])
    results["verdict"] = "плюс есть" if all(ok_dirs) else ("не ясно" if disagree else "плюса нет")
    results["metrics"] = {"%s|%s" % k: {kk: vv for kk, vv in v.items() if kk != "daily"} for k, v in M.items()}
    json.dump(results, open(os.path.join(out, "result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    write_sqlite(out, runs, syms, pres, M)
    write_tables(out, results, M)
    print("verdict:", results["verdict"])


def write_sqlite(out, runs, syms, pres, M):
    path = os.path.join(out, "tk729.sqlite")
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE tk729_run(run TEXT, half TEXT, usd REAL, cycles INT, usd_per_cycle REAL, usd_per_coin_day REAL, coin_days INT, mean_coins_in_work REAL, margin REAL, sharpe REAL, dd REAL, dd_pct REAL,
        hw_max REAL, hw_mean REAL, hw_p90 REAL, cyc_usd REAL, exit_usd REAL, other_usd REAL, n_edge INT, n_time INT, opens INT, n_mkt INT, coins_active INT, coins_plus INT, best10 REAL, worst10 REAL, ex_best10 REAL,
        months_plus INT, months_n INT, lo7 REAL, hi7 REAL, lo21 REAL, hi21 REAL, PRIMARY KEY(run, half));
    CREATE TABLE tk729_month(run TEXT, half TEXT, month TEXT, usd REAL);
    CREATE TABLE tk729_tercile(run TEXT, half TEXT, kind TEXT, tercile TEXT, coins INT, usd REAL, plus INT);
    CREATE TABLE tk729_day(run TEXT, day INT, usd REAL);
    CREATE TABLE tk729_coin(run TEXT, half TEXT, sym TEXT, usd REAL, cycles INT, edge_exits INT, present_days INT);
    """)
    for (nm, h), m in M.items():
        db.execute("INSERT INTO tk729_run VALUES(%s)" % ",".join("?" * 33), (nm, h, m["usd"], m["cycles"], m["usd_per_cycle"], m["usd_per_coin_day"], m["coin_days"], m["mean_coins_in_work"], m["margin"], m["sharpe"], m["dd"], m["dd_pct"],
                   m["hw_max"], m["hw_mean"], m["hw_p90"], m["cyc_usd"], m["exit_usd"], m["other_usd"], m["n_edge"], m["n_time"], m["opens"], m["n_mkt"], m["coins_active"], m["coins_plus"], m["best10"], m["worst10"], m["ex_best10"],
                   m["months_plus"], m["months_n"], m["boot"][7]["lo"], m["boot"][7]["hi"], m["boot"][21]["lo"], m["boot"][21]["hi"]))
        for mo, v in m["months"].items():
            db.execute("INSERT INTO tk729_month VALUES(?,?,?,?)", (nm, h, mo, v))
        for kind, key in (("natr30", "terc_natr"), ("turnover", "terc_turn")):
            for t in m[key]:
                db.execute("INSERT INTO tk729_tercile VALUES(?,?,?,?,?,?,?)", (nm, h, kind, t["tercile"], t["coins"], t["usd"], t["plus"]))
    for nm, arr in runs.items():
        daily = arr[:, L.R_PNL, :].sum(0)
        db.executemany("INSERT INTO tk729_day VALUES(?,?,?)", [(nm, i, float(v)) for i, v in enumerate(daily)])
        for h, (d0, d1) in HALF.items():
            tot = arr[:, L.R_PNL, d0:d1].sum(1)
            cyc = arr[:, L.R_NCYC, d0:d1].sum(1)
            ed = arr[:, L.R_NEDGE, d0:d1].sum(1)
            db.executemany("INSERT INTO tk729_coin VALUES(?,?,?,?,?,?,?)", [(nm, h, syms[i], float(tot[i]), int(cyc[i]), int(ed[i]), int(pres[i, d0:d1].sum())) for i in range(len(syms))])
    db.commit()
    db.close()


def fmt(x, nd=0):
    return "—" if x is None or (isinstance(x, float) and not math.isfinite(x)) else ("%.*f" % (nd, x))


def write_tables(out, R, M):
    L_ = []
    L_.append("# TK-729 таблицы (авто)\n")
    L_.append("verdict: **%s**\n" % R["verdict"])
    for d in R["dirs"]:
        L_.append("\n## %s → %s: %s (выбор по Шарпу на %s = %s; шумовой максимум из 6 = %s)\n" % (d["sel_half"], d["test_half"], d["name"], d["sel_half"], fmt(d["sel_sharpe"], 2), fmt(d["noise_bar_sharpe"], 2)))
        L_.append("checks: %s\n" % json.dumps(d["checks"], ensure_ascii=False, default=bool))
        L_.append("vs C0 (парная разница, $/сутки): %s\n" % json.dumps({b: [round(v["mean"], 2), round(v["lo"], 2), round(v["hi"], 2)] for b, v in d["vs_c0"].items()}))
        for ab, a in d["ablations"].items():
            L_.append("- %s: Σ %s, Шарп %s, Δ к AG %s $ (интервал 7: %s…%s; 21: %s…%s)\n" % (ab, fmt(a["usd"]), fmt(a["sharpe"], 2), fmt(a["delta_usd"]), fmt(a["delta_boot"][7]["lo"], 1), fmt(a["delta_boot"][7]["hi"], 1), fmt(a["delta_boot"][21]["lo"], 1), fmt(a["delta_boot"][21]["hi"], 1)))
    L_.append("\n## Все варианты по половинам\n")
    L_.append("| вариант | пол. | Σ $ | кругов | $/круг | $/мон-сут | Шарп | DD $ | DD % марж. | перехай макс/ср/p90 | мес.+ | мон.+ | лучшие10 | худшие10 | Σ край/круги/проч | инт.7 | инт.21 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    for (nm, h), m in sorted(M.items()):
        L_.append("| %s | %s | %s | %d | %s | %s | %s | %s | %s | %s/%s/%s | %d/%d | %d/%d | %s | %s | %s/%s/%s | %s…%s | %s…%s |\n" % (
            nm, h, fmt(m["usd"]), m["cycles"], fmt(m["usd_per_cycle"], 2), fmt(m["usd_per_coin_day"], 3), fmt(m["sharpe"], 2), fmt(m["dd"]), fmt(m["dd_pct"], 1), fmt(m["hw_max"]), fmt(m["hw_mean"], 1), fmt(m["hw_p90"]),
            m["months_plus"], m["months_n"], m["coins_plus"], m["coins_active"], fmt(m["best10"]), fmt(m["worst10"]), fmt(m["exit_usd"]), fmt(m["cyc_usd"]), fmt(m["other_usd"]),
            fmt(m["boot"][7]["lo"], 1), fmt(m["boot"][7]["hi"], 1), fmt(m["boot"][21]["lo"], 1), fmt(m["boot"][21]["hi"], 1)))
    open(os.path.join(out, "tables.md"), "w", encoding="utf-8").write("".join(L_))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("select", "final"))
    ap.add_argument("--out", default="/data/tk729")
    a = ap.parse_args()
    (select_step if a.cmd == "select" else final_step)(a.out)


if __name__ == "__main__":
    main()
