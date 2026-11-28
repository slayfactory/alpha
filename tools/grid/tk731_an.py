"""TK-731 (П-TK729 §8): анализ прогона — метрики В-169 по вариантам и половинам, R:R по циклам, убыток на стоп, выбор по Шарпу в обе стороны, вердикт по правилу §8, SQLite (В-179).

    python tk731_an.py final --out /data/tk731
Читает res/*.npz (tk731_run) и C0 из TK-729 (/data/tk729/res/base) для парной разницы «как с как». Решает правило §8 без изменений; все 8 вариантов показываются на обеих половинах.
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
import tk729_an as A  # noqa: E402
import tk729_lib as L  # noqa: E402
import tk731_lib as W  # noqa: E402

B729 = "/data/tk729/res/base"
HALF = A.HALF
DIRS = A.DIRS
NB = A.NB
FUND_RATE = 1e-4  # допущение: средняя ставка фондинга 0,01 % за 8 ч (базовая ставка Binance), платит лонг при > 0; описательно
TARGET_C0 = {"H1": -90410.0, "H2": -86806.0}  # Σ C0 по TK-729 (до доллара)
NEW8 = [v["name"] for v in W.variants() if v["name"] != "C0"]


def load(out: str):
    fs = sorted(glob.glob(os.path.join(out, "res", "*.npz")))
    syms = [os.path.basename(f)[:-4] for f in fs]
    runs = None
    arr, hist, present, turn, natr = [], None, [], [], []
    base_pnl, base_act = [], []
    for f in fs:
        d = np.load(f, allow_pickle=False)
        if runs is None:
            runs = [str(x) for x in d["runs"]]
            hist = np.zeros((len(runs), 3, 2, W.NBIN, 2))
        arr.append(d["out"])
        sz = 3 * 2 * W.NBIN
        idx = d["hist_idx"]
        v, r = idx // sz, idx % sz
        k, h, b = r // (2 * W.NBIN), (r // W.NBIN) % 2, r % W.NBIN
        np.add.at(hist, (v, k, h, b, 0), d["hist_n"])
        np.add.at(hist, (v, k, h, b, 1), d["hist_s"])
        present.append(d["present"])
        turn.append(float(d["turn"]))
        natr.append(float(d["natr"]))
        bb = np.load(os.path.join(B729, os.path.basename(f)), allow_pickle=False)
        assert str(bb["runs"][0]) == "C0"
        base_pnl.append(bb["out"][0, L.R_PNL])
        base_act.append(bb["out"][0, L.R_ACT])
    arr = np.stack(arr, axis=1)  # (runs, coins, NR2, 328)
    return dict(syms=syms, runs=runs, arr={nm: arr[i] for i, nm in enumerate(runs)}, hist=hist, present=np.stack(present), turn=np.array(turn), natr=np.array(natr),
                base_pnl=np.stack(base_pnl), base_act=np.stack(base_act))


def under_water_share(x) -> float:
    cum = np.cumsum(np.asarray(x, float))
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    return float((cum < peak - 1e-9).mean())


def half_metrics(a, D, d0, d1, hist_run):
    """a — (coins, NR2, 328) варианта; D — общие данные. Половина [d0; d1) суток отчёта."""
    pnl = a[:, W.R_PNL, d0:d1]
    daily = pnl.sum(0)
    coin_tot = pnl.sum(1)
    tot = float(pnl.sum())
    margin = A.MARGIN_PER_COIN * float(D["base_act"][:, d0:d1].sum(0).mean())
    dd = L.drawdown(daily)
    ent = a[:, W.R_ENT, d0:d1].sum(1)
    active = ent > 0
    order = np.argsort(coin_tot)
    best10, worst10 = float(coin_tot[order[-10:]].sum()), float(coin_tot[order[:10]].sum())
    mon = {m: float(daily[x - d0: y - d0].sum()) for m, x, y in A.months_in(d0, d1)}
    cn = float(a[:, W.R_CN, d0:d1].sum())
    cw = float(a[:, W.R_CW, d0:d1].sum())
    cws = float(a[:, W.R_CWS, d0:d1].sum())
    cls_ = float(a[:, W.R_CLS, d0:d1].sum())
    avg_win = cws / cw if cw else float("nan")
    avg_loss = cls_ / (cn - cw) if cn > cw else float("nan")
    rr = avg_win / abs(avg_loss) if (cw and cn > cw and avg_loss != 0) else float("nan")
    st = {}
    for kd, nm in ((0, "edge_grid"), (1, "own_stop"), (2, "edge_ab")):
        hn, hs = hist_run[kd, 0 if d0 == HALF["H1"][0] else 1, :, 0], hist_run[kd, 0 if d0 == HALF["H1"][0] else 1, :, 1]
        st[nm] = W.hist_stats(hn, hs)
    sgn = float(a[:, W.R_SGN, d0:d1].sum())
    res = dict(
        usd=tot, margin=margin, sharpe=L.sharpe_ann(daily), dd=dd["dd"], dd_pct=100.0 * dd["dd"] / margin if margin > 0 else float("nan"),
        hw_max=dd["hw_max"], hw_mean=dd["hw_mean"], hw_p90=dd["hw_p90"], under=under_water_share(daily), n_high=dd["n_high"],
        cycles=int(cn), cyc_win=int(cw), cyc_flag=int(a[:, W.R_CF, d0:d1].sum()), win_share=cw / cn if cn else float("nan"), avg_win=avg_win, avg_loss=avg_loss, rr=rr,
        be_share=1.0 / (1.0 + rr) if math.isfinite(rr) else float("nan"), cyc_usd=cws + cls_, cyc_gap=(cws + cls_) - tot,
        entries=int(ent.sum()), laps=int(a[:, W.R_LPN, d0:d1].sum()), laps_usd=float(a[:, W.R_LPU, d0:d1].sum()), edge_grid_n=int(a[:, W.R_EGN, d0:d1].sum()), edge_grid_usd=float(a[:, W.R_EGU, d0:d1].sum()),
        stop_n=int(a[:, W.R_STN, d0:d1].sum()), stop_usd=float(a[:, W.R_STU, d0:d1].sum()), edge_ab_n=int(a[:, W.R_EAN, d0:d1].sum()), edge_ab_usd=float(a[:, W.R_EAU, d0:d1].sum()),
        gap_fills=int(a[:, W.R_GAP, d0:d1].sum()), g2a=int(a[:, W.R_G2A, d0:d1].sum()), a2g=int(a[:, W.R_A2G, d0:d1].sum()),
        min_ab=int(a[:, W.R_MAB, d0:d1].sum()), min_gr=int(a[:, W.R_MGR, d0:d1].sum()), stops=st,
        funding_signed_usd=-FUND_RATE * sgn / 480.0, abs_notional_mean=float(a[:, W.R_ABS, d0:d1].sum()) / max(float(a[:, W.R_MAB, d0:d1].sum() + a[:, W.R_MGR, d0:d1].sum()), 1.0),
        coins_active=int(active.sum()), coins_plus=int(((coin_tot > 0) & active).sum()), best10=best10, worst10=worst10, ex_best10=tot - best10,
        months=mon, months_plus=int(sum(v > 0 for v in mon.values())), months_n=len(mon),
        terc_natr=A.tercile_table(coin_tot, D["natr"], active), terc_turn=A.tercile_table(coin_tot, D["turn"], active),
        coin_tot=coin_tot, daily=daily,
    )
    res["boot"] = {b: L.stat_block(daily, b, L.NBOOT, L.SEED) for b in NB}
    top = [(D["syms"][i], float(coin_tot[i])) for i in order[-10:][::-1]]
    bot = [(D["syms"][i], float(coin_tot[i])) for i in order[:10]]
    res["top10"], res["bot10"] = top, bot
    return res


def fmt(x, nd=0):
    return "—" if x is None or (isinstance(x, float) and not math.isfinite(x)) else ("%.*f" % (nd, x))


def final_step(out: str):
    D = load(out)
    runs = D["runs"]
    ncoin = len(D["syms"])
    # ── сверка кода: C0 (новое ядро) против TK-729 до доллара, по монетам и суткам
    c0 = D["arr"]["C0"]
    diff = np.abs(c0[:, W.R_PNL, :] - D["base_pnl"])
    checks = dict(coins=ncoin, c0_max_abs_diff_coin_day=float(diff.max()), c0_sum_H1=float(c0[:, W.R_PNL, HALF["H1"][0]:HALF["H1"][1]].sum()), c0_sum_H2=float(c0[:, W.R_PNL, HALF["H2"][0]:HALF["H2"][1]].sum()),
                  target=TARGET_C0)
    checks["c0_match_dollar"] = bool(all(abs(checks["c0_sum_" + h] - TARGET_C0[h]) < 1.0 for h in ("H1", "H2")))
    checks["c0_match_cent"] = bool(diff.max() < 1e-6)
    M = {}
    for nm in runs:
        for h, (d0, d1) in HALF.items():
            M[(nm, h)] = half_metrics(D["arr"][nm], D, d0, d1, D["hist"][runs.index(nm)])
    checks["cycle_sum_gap_max"] = float(max(abs(m["cyc_gap"]) for m in M.values()))
    # ── C0 из TK-729 как база для парной разницы (суточный ряд по половине)
    base_daily = {h: D["base_pnl"][:, d0:d1].sum(0) for h, (d0, d1) in HALF.items()}
    vs = {}
    for nm in NEW8:
        for h in HALF:
            vs[(nm, h)] = A.paired(M[(nm, h)]["daily"], base_daily[h])
    res = dict(syms=ncoin, checks=checks, dirs=[])
    ok_dirs = []
    for s_h, t_h in DIRS:
        sh = {nm: M[(nm, s_h)]["sharpe"] for nm in NEW8}
        best = max(NEW8, key=lambda k: sh[k])
        tm = M[(best, t_h)]
        pv, psum = vs[(best, t_h)]
        chk = A.check_plus(tm, pv)
        n_days = len(tm["daily"])
        res["dirs"].append(dict(sel_half=s_h, test_half=t_h, name=best, sel_sharpes=sh, sel_sharpe=sh[best], test_usd=tm["usd"], test_sharpe=tm["sharpe"], noise8=A.noise_bar(n_days, 8), noise95=A.noise_bar(n_days, 95),
                                noise8_sel=A.noise_bar(len(M[(best, s_h)]["daily"]), 8), noise95_sel=A.noise_bar(len(M[(best, s_h)]["daily"]), 95),
                                vs_c0={b: dict(mean=v["mean"], lo=v["lo"], hi=v["hi"]) for b, v in pv.items()}, vs_c0_usd=psum, checks=chk))
        ok_dirs.append(all(chk[k] for k in ("lo_gt0_7", "lo_gt0_21", "months_ge_2_3", "ex_best10_gt0", "vs_c0_lo_gt0_7", "vs_c0_lo_gt0_21")))
    disagree = any(r["checks"]["lo_gt0_7"] != r["checks"]["lo_gt0_21"] or r["checks"]["vs_c0_lo_gt0_7"] != r["checks"]["vs_c0_lo_gt0_21"] for r in res["dirs"])
    res["verdict"] = "плюс есть" if all(ok_dirs) else ("не ясно" if disagree else "плюса нет")
    # описательно: лучший по каждой половине (не решение) и сколько вариантов с Σ > 0 и нижней границей > 0
    res["desc"] = {h: dict(best_sharpe=max(NEW8, key=lambda k: M[(k, h)]["sharpe"]), n_pos_usd=int(sum(M[(k, h)]["usd"] > 0 for k in NEW8)),
                           n_lo_pos=int(sum(M[(k, h)]["boot"][7]["lo"] > 0 and M[(k, h)]["boot"][21]["lo"] > 0 for k in NEW8))) for h in HALF}
    res["metrics"] = {"%s|%s" % k: {kk: vv for kk, vv in v.items() if kk not in ("daily", "coin_tot")} for k, v in M.items()}
    res["vs_c0_all"] = {"%s|%s" % k: dict(usd=float(v[1]), boot={b: dict(mean=x["mean"], lo=x["lo"], hi=x["hi"]) for b, x in v[0].items()}) for k, v in vs.items()}
    json.dump(res, open(os.path.join(out, "result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    write_sqlite(out, D, M, vs)
    write_tables(out, res, M, vs)
    print("verdict:", res["verdict"], "| checks:", json.dumps(checks, ensure_ascii=False))


def write_sqlite(out, D, M, vs):
    path = os.path.join(out, "tk731.sqlite")
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE tk731_run(run TEXT, half TEXT, usd REAL, margin REAL, sharpe REAL, dd REAL, dd_pct REAL, hw_max REAL, hw_mean REAL, hw_p90 REAL, under REAL, cycles INT, cyc_win INT, cyc_flag INT, win_share REAL,
        avg_win REAL, avg_loss REAL, rr REAL, be_share REAL, entries INT, laps INT, laps_usd REAL, edge_grid_n INT, edge_grid_usd REAL, stop_n INT, stop_usd REAL, edge_ab_n INT, edge_ab_usd REAL, gap_fills INT, g2a INT, a2g INT,
        min_ab INT, min_gr INT, funding_signed_usd REAL, coins_active INT, coins_plus INT, best10 REAL, worst10 REAL, ex_best10 REAL, months_plus INT, months_n INT, lo7 REAL, hi7 REAL, lo21 REAL, hi21 REAL, PRIMARY KEY(run, half));
    CREATE TABLE tk731_month(run TEXT, half TEXT, month TEXT, usd REAL);
    CREATE TABLE tk731_tercile(run TEXT, half TEXT, kind TEXT, tercile TEXT, coins INT, usd REAL, plus INT);
    CREATE TABLE tk731_stop(run TEXT, half TEXT, kind TEXT, n INT, mean REAL, worst1pct REAL);
    CREATE TABLE tk731_vs_c0(run TEXT, half TEXT, usd REAL, block INT, mean REAL, lo REAL, hi REAL);
    CREATE TABLE tk731_day(run TEXT, day INT, usd REAL);
    CREATE TABLE tk731_coin(run TEXT, half TEXT, sym TEXT, usd REAL, cycles INT, entries INT);
    """)
    for (nm, h), m in M.items():
        db.execute("INSERT INTO tk731_run VALUES(%s)" % ",".join("?" * 44), (nm, h, m["usd"], m["margin"], m["sharpe"], m["dd"], m["dd_pct"], m["hw_max"], m["hw_mean"], m["hw_p90"], m["under"], m["cycles"], m["cyc_win"], m["cyc_flag"], m["win_share"],
                   m["avg_win"], m["avg_loss"], m["rr"], m["be_share"], m["entries"], m["laps"], m["laps_usd"], m["edge_grid_n"], m["edge_grid_usd"], m["stop_n"], m["stop_usd"], m["edge_ab_n"], m["edge_ab_usd"], m["gap_fills"], m["g2a"], m["a2g"],
                   m["min_ab"], m["min_gr"], m["funding_signed_usd"], m["coins_active"], m["coins_plus"], m["best10"], m["worst10"], m["ex_best10"], m["months_plus"], m["months_n"], m["boot"][7]["lo"], m["boot"][7]["hi"], m["boot"][21]["lo"], m["boot"][21]["hi"]))
        for mo, v in m["months"].items():
            db.execute("INSERT INTO tk731_month VALUES(?,?,?,?)", (nm, h, mo, v))
        for kind, key in (("natr30", "terc_natr"), ("turnover", "terc_turn")):
            for t in m[key]:
                db.execute("INSERT INTO tk731_tercile VALUES(?,?,?,?,?,?,?)", (nm, h, kind, t["tercile"], t["coins"], t["usd"], t["plus"]))
        for kd, s in m["stops"].items():
            db.execute("INSERT INTO tk731_stop VALUES(?,?,?,?,?,?)", (nm, h, kd, s["n"], s["mean"], s["worst"]))
        d0, d1 = HALF[h]
        a = D["arr"][nm]
        db.executemany("INSERT INTO tk731_coin VALUES(?,?,?,?,?,?)", [(nm, h, D["syms"][i], float(m["coin_tot"][i]), int(a[i, W.R_CN, d0:d1].sum()), int(a[i, W.R_ENT, d0:d1].sum())) for i in range(len(D["syms"]))])
    for (nm, h), (pv, psum) in vs.items():
        for b, v in pv.items():
            db.execute("INSERT INTO tk731_vs_c0 VALUES(?,?,?,?,?,?,?)", (nm, h, float(psum), b, v["mean"], v["lo"], v["hi"]))
    for nm, a in D["arr"].items():
        daily = a[:, W.R_PNL, :].sum(0)
        db.executemany("INSERT INTO tk731_day VALUES(?,?,?)", [(nm, i, float(v)) for i, v in enumerate(daily)])
    db.commit()
    db.close()


def write_tables(out, R, M, vs):
    T = ["# TK-731 таблицы (авто)\n\nverdict: **%s**\n\nсверка: %s\n" % (R["verdict"], json.dumps(R["checks"], ensure_ascii=False))]
    for d in R["dirs"]:
        T.append("\n## %s → %s: %s (Шарп выбора %s; шум максимума: из 8 = %s, из 95 = %s на проверочной половине)\n" % (d["sel_half"], d["test_half"], d["name"], fmt(d["sel_sharpe"], 2), fmt(d["noise8"], 2), fmt(d["noise95"], 2)))
        T.append("checks: %s\n\nvs C0 ($/сутки): %s\n" % (json.dumps(d["checks"], ensure_ascii=False, default=bool), json.dumps({b: [round(v["mean"], 2), round(v["lo"], 2), round(v["hi"], 2)] for b, v in d["vs_c0"].items()})))
    T.append("\n## Все варианты по половинам\n\n| вариант | пол. | Σ $ | циклов | доля приб. | ср. плюс $ | ср. минус $ | R:R | безуб. доля | Шарп | DD $ | DD % марж. | перехай макс/ср/p90 | под водой | мес.+ | мон.+ | лучшие10 | худшие10 | инт.7 | инт.21 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    for (nm, h), m in sorted(M.items()):
        T.append("| %s | %s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s/%s/%s | %s | %d/%d | %d/%d | %s | %s | %s…%s | %s…%s |\n" % (
            nm, h, fmt(m["usd"]), m["cycles"], fmt(m["win_share"] * 100, 1), fmt(m["avg_win"], 3), fmt(m["avg_loss"], 3), fmt(m["rr"], 3), fmt(m["be_share"] * 100, 1), fmt(m["sharpe"], 2), fmt(m["dd"]), fmt(m["dd_pct"], 1),
            fmt(m["hw_max"]), fmt(m["hw_mean"], 1), fmt(m["hw_p90"]), fmt(m["under"] * 100, 0), m["months_plus"], m["months_n"], m["coins_plus"], m["coins_active"], fmt(m["best10"]), fmt(m["worst10"]),
            fmt(m["boot"][7]["lo"], 1), fmt(m["boot"][7]["hi"], 1), fmt(m["boot"][21]["lo"], 1), fmt(m["boot"][21]["hi"], 1)))
    T.append("\n## Убыток на стоп ($)\n\n| вариант | пол. | вид | число | среднее | худший 1 % |\n|---|---|---|---|---|---|\n")
    for (nm, h), m in sorted(M.items()):
        for kd, s in m["stops"].items():
            if s["n"]:
                T.append("| %s | %s | %s | %d | %s | %s |\n" % (nm, h, kd, s["n"], fmt(s["mean"], 3), fmt(s["worst"], 3)))
    T.append("\n## Разница с C0 (TK-729), $/сутки: среднее [нижняя; верхняя] при блоках 7 и 21\n\n| вариант | пол. | Σ разницы $ | блок 7 | блок 21 |\n|---|---|---|---|---|\n")
    for (nm, h), (pv, psum) in sorted(vs.items()):
        T.append("| %s | %s | %s | %s [%s; %s] | %s [%s; %s] |\n" % (nm, h, fmt(psum), fmt(pv[7]["mean"], 2), fmt(pv[7]["lo"], 2), fmt(pv[7]["hi"], 2), fmt(pv[21]["mean"], 2), fmt(pv[21]["lo"], 2), fmt(pv[21]["hi"], 2)))
    open(os.path.join(out, "tables.md"), "w", encoding="utf-8").write("".join(T))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("final",))
    ap.add_argument("--out", default="/data/tk731")
    a = ap.parse_args()
    final_step(a.out)


if __name__ == "__main__":
    main()
