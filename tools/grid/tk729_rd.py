"""TK-729 §5 (PS-1065): анализ фазы rd — разметка 5.1 (где деньги по режиму и стороне), выбор RD(τ, BTC) по Шарпу на одной половине и проверка на другой, сравнение с замороженным MR §4 (парная суточная разница), правило §2.

    python tk729_rd.py --out /data/tk729        # → result_rd.json, tables_rd.md, таблицы в tk729.sqlite (tk729rd_*)
    python tk729_rd.py --out /data/tk729 --desc # только 5.1 (описательно, без выбора и правила)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_an as A  # noqa: E402
import tk729_cfg as C  # noqa: E402
import tk729_lib as L  # noqa: E402

RD = [v["name"] for v in C.rd_variants() if v["kind"] == "RD"]
T51 = [v["name"] for v in C.rd_variants() if v["kind"] == "T51"]
FZ = "ABf_MR_c1.5_h120"  # замороженный MR §4 (тот же MR(1,5; 120), одинаков в обоих направлениях выбора §3)
MR = "MR_c1.5_h120"
RN = ("вниз", "флэт", "вверх")
SN = ("лонг", "шорт")
HN = {"H1": "21.01–30.06", "H2": "01.07–24.11"}
TRIALS = 23  # 19 + 4 (PS-1065)


def f(x, nd=0):
    return "—" if x is None or x != x else "{:,.{nd}f}".format(x, nd=nd).replace(",", " ")


def attr(arr, d0, d1):
    a = arr[:, L.A0: L.A0 + 36, d0:d1].sum(axis=(0, 2)).reshape(3, 2, 6)
    ep = arr[:, L.AEP: L.AEP + 3, d0:d1].sum(axis=(0, 2))
    return a, ep


def attr_rows(nm, h, arr, d0, d1):
    a, ep = attr(arr, d0, d1)
    rows = []
    for ri in range(3):
        for sd in range(2):
            v = a[ri, sd]
            rows.append(dict(run=nm, half=h, regime=RN[ri], side=SN[sd], usd=float(v[0]), cyc_usd=float(v[1]), exit_usd=float(v[2]), n_cyc=int(round(v[3])), n_edge=int(round(v[4])), n_other=int(round(v[5])), episodes=int(round(ep[ri]))))
    return rows


def md_attr(rows):
    L_ = ["| прогон | половина | режим при первом исполнении | сторона | Σ $ | кругов | Σ кругов $ | выходов за край | прочих выходов (время / против режима) | Σ выходов $ | эпизодов (режим) |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L_.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (r["run"], HN[r["half"]], r["regime"], r["side"], f(r["usd"]), f(r["n_cyc"]), f(r["cyc_usd"]), f(r["n_edge"]), f(r["n_other"]), f(r["exit_usd"]), f(r["episodes"]) if r["side"] == "лонг" else ""))
    return "\n".join(L_)


def md_attr_sum(rows):
    """Сводка 5.1: по режиму (обе стороны) и по стороне (все режимы): Σ $, кругов, выходов за край."""
    keyset = []
    for r in rows:
        k = (r["run"], r["half"])
        if k not in keyset:
            keyset.append(k)
    L_ = ["| прогон | половина | Σ вниз | Σ флэт | Σ вверх | Σ лонг | Σ шорт | эпизодов вниз / флэт / вверх | выходов за край: вниз / флэт / вверх |", "|---|---|---|---|---|---|---|---|---|"]
    for run, h in keyset:
        rr = [r for r in rows if r["run"] == run and r["half"] == h]
        by = lambda key, val, m: sum(r[m] for r in rr if r[key] == val)
        L_.append("| %s | %s | %s | %s | %s | %s | %s | %s / %s / %s | %s / %s / %s |" % (
            run, HN[h], *[f(by("regime", g, "usd")) for g in RN], *[f(by("side", s, "usd")) for s in SN],
            *[f(next(r["episodes"] for r in rr if r["regime"] == g)) for g in RN], *[f(by("regime", g, "n_edge")) for g in RN]))
    return "\n".join(L_)


def run_row(nm, h, m, fz_paired=None):
    b7, b21 = m["boot"][7], m["boot"][21]
    s = "| %s | %s | %s | %s | %s | %s | %s | %s / %s / %s | %d из %d | %s (%s %%) | [%s; %s] / [%s; %s] |" % (
        nm, HN[h], f(m["usd"]), f(m["cycles"]), f(m["cyc_usd"]), f(m["exit_usd"]), f(m["sharpe"], 2), f(m["n_edge"]), f(m["n_time"]), f(m["n_reg"]), m["months_plus"], m["months_n"], f(m["dd"]), f(m["dd_pct"], 2),
        f(b7["lo"], 1), f(b7["hi"], 1), f(b21["lo"], 1), f(b21["hi"], 1))
    if fz_paired:
        d7, d21, dsum = fz_paired
        s += " %s | [%s; %s] / [%s; %s] |" % (f(dsum), f(d7["lo"], 2), f(d7["hi"], 2), f(d21["lo"], 2), f(d21["hi"], 2))
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/data/tk729")
    ap.add_argument("--desc", action="store_true")
    a = ap.parse_args()
    out = a.out
    runs, syms, pres, turn, natr = A.load_res(out, ("base", "frz", "rd"))
    R = {"syms": len(syms), "t51": [], "rd": {}, "dirs": []}
    md = []
    # ── 5.1: разметка (описательно)
    rows51 = []
    for nm in T51:
        for h, (d0, d1) in A.HALF.items():
            rows51 += attr_rows(nm, h, runs[nm], d0, d1)
    R["t51"] = rows51
    # проверка: торговля T51-прогонов совпадает с уже посчитанными MR / MR-заморозкой (первые 9 строк ядра)
    chk = {}
    for nm, ref in (("T51_MR_t1", MR), ("T51_MR_t2", MR), ("T51_FZ_t1", FZ), ("T51_FZ_t2", FZ)):
        chk[nm] = float(np.abs(runs[nm][:, :9, :] - runs[ref][:, :9, :]).max())
    R["t51_regression_maxdiff"] = chk
    md.append("### 5.1. Где деньги: режим при первом исполнении из нуля и сторона (описательно)\n")
    md.append("Прогоны — те же MR(1,5; 120) и MR(1,5; 120) с замороженным центром (торговля не менялась: максимальное расхождение с прежним счётом по 9 строкам ядра на 539 монетах %s); режим — метка по §5.2 (τ = 1 и τ = 2, без BTC); сумма по строкам = Σ кругов + Σ выходов.\n" % json.dumps({k: round(v, 12) for k, v in chk.items()}))
    md.append(md_attr_sum(rows51) + "\n")
    md.append(md_attr(rows51) + "\n")
    if a.desc:
        open(os.path.join(out, "tables_rd51.md"), "w", encoding="utf-8").write("\n".join(md))
        json.dump(R, open(os.path.join(out, "result_rd51.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("5.1 done")
        return
    # ── 5.3–5.4
    M = {}
    for nm in RD + [FZ, MR, "C0"]:
        for h, (d0, d1) in A.HALF.items():
            M[(nm, h)] = A.half_metrics(runs[nm], pres, turn, natr, d0, d1)
            M[(nm, h)]["n_reg"] = int(round(runs[nm][:, L.R_NREG, d0:d1].sum())) if runs[nm].shape[1] > L.R_NREG else 0
    paired_fz = {}
    for nm in RD:
        for h in A.HALF:
            d, dsum = A.paired(M[(nm, h)]["daily"], M[(FZ, h)]["daily"])
            paired_fz[(nm, h)] = (d[7], d[21], dsum)
    md.append("### 5.3–5.4. Бот RD(τ, BTC) и выбор\n")
    head = "| вариант | половина | Σ $ | кругов | Σ кругов $ | Σ выходов $ | Шарп | выходов за край / по времени / против режима | месяцев в плюсе | просадка $ (% маржи) | 95 % интервал Σ в сутки, блок 7 / 21 | Δ к замороженному MR $ | интервал Δ в сутки, блок 7 / 21 |"
    md.append(head + "\n" + "|---|" * 1 + "---|" * 12)
    for nm in [FZ] + RD:
        for h in A.HALF:
            md.append(run_row(nm, h, M[(nm, h)], None if nm == FZ else paired_fz[(nm, h)]).rstrip("|") + ("" if nm != FZ else " — | — |"))
    cw = {h: M[(FZ, h)]["mean_coins_in_work"] for h in A.HALF}
    md.append("\nПросадка в % — от маржи 250 $ × среднее число монет в работе: у замороженного MR %s (21.01–30.06) и %s (01.07–24.11) монет в работе (маржа %s $ и %s $); у вариантов RD — своё число монет в работе (строка «маржа» в `tk729rd_run`).\n" % (f(cw["H1"], 0), f(cw["H2"], 0), f(M[(FZ, "H1")]["margin"]), f(M[(FZ, "H2")]["margin"])))
    # выбор
    for s_h, t_h in A.DIRS:
        d0, d1 = A.HALF[s_h]
        sh = {nm: L.sharpe_ann(runs[nm][:, L.R_PNL, d0:d1].sum(0)) for nm in RD}
        best = max(RD, key=lambda k: sh[k])
        tm, fz = M[(best, t_h)], M[(FZ, t_h)]
        vs, vs_sum = A.paired(tm["daily"], fz["daily"])
        ck = A.check_plus(tm, vs)  # vs_c0_* здесь — против замороженного MR
        nd = len(tm["daily"])
        R["dirs"].append(dict(sel_half=s_h, test_half=t_h, name=best, sel_sharpes=sh, test_usd=tm["usd"], test_sharpe=tm["sharpe"], fz_usd=fz["usd"], fz_sharpe=fz["sharpe"],
                              vs_fz={b: dict(mean=v["mean"], lo=v["lo"], hi=v["hi"]) for b, v in vs.items()}, vs_fz_usd=vs_sum, checks=ck,
                              noise_bar_4=A.noise_bar(nd, 4), noise_bar_23=A.noise_bar(nd, TRIALS), months=tm["months"], months_fz=fz["months"], months_plus=tm["months_plus"], months_n=tm["months_n"], ex_best10=tm["ex_best10"],
                              n_reg=tm["n_reg"], attr=attr_rows(best, t_h, runs[best], *A.HALF[t_h])))
    ok = [all(d["checks"][k] for k in ("lo_gt0_7", "lo_gt0_21", "months_ge_2_3", "ex_best10_gt0", "vs_c0_lo_gt0_7", "vs_c0_lo_gt0_21")) for d in R["dirs"]]
    disagree = any(d["checks"]["lo_gt0_7"] != d["checks"]["lo_gt0_21"] or d["checks"]["vs_c0_lo_gt0_7"] != d["checks"]["vs_c0_lo_gt0_21"] for d in R["dirs"])
    R["verdict"] = "плюс есть" if all(ok) else ("не ясно" if disagree else "плюса нет")
    yn = lambda b: "да" if b else "нет"
    md.append("\n**Вердикт по правилу §2 (сравнение — с замороженным MR §4): «%s».**\n" % R["verdict"])
    md.append("| направление | выбранный RD (Шарп на выборочной половине) | Σ $ / Шарп на проверке | замороженный MR на той же половине | нижняя граница Σ в сутки > 0 (блок 7 / 21) | месяцев в плюсе ≥ ⅔ | без 10 лучших монет Σ > 0 | RD лучше замороженного MR (нижняя граница парной разницы > 0, блок 7 / 21) |\n|---|---|---|---|---|---|---|---|")
    for d in R["dirs"]:
        c = d["checks"]
        md.append("| %s → %s | %s (%s) | %s / %s | %s / %s | %s / %s [%s; %s] / [%s; %s] | %s (%d из %d, нужно %d) | %s (%s $) | %s / %s (Δ %s $ за половину, [%s; %s] / [%s; %s] в сутки) |" % (
            HN[d["sel_half"]], HN[d["test_half"]], d["name"], f(d["sel_sharpes"][d["name"]], 2), f(d["test_usd"]), f(d["test_sharpe"], 2), f(d["fz_usd"]), f(d["fz_sharpe"], 2),
            yn(c["lo_gt0_7"]), yn(c["lo_gt0_21"]), f(M[(d["name"], d["test_half"])]["boot"][7]["lo"], 1), f(M[(d["name"], d["test_half"])]["boot"][7]["hi"], 1), f(M[(d["name"], d["test_half"])]["boot"][21]["lo"], 1), f(M[(d["name"], d["test_half"])]["boot"][21]["hi"], 1),
            yn(c["months_ge_2_3"]), d["months_plus"], d["months_n"], c["months_need"], yn(c["ex_best10_gt0"]), f(d["ex_best10"]), yn(c["vs_c0_lo_gt0_7"]), yn(c["vs_c0_lo_gt0_21"]), f(d["vs_fz_usd"]),
            f(d["vs_fz"][7]["lo"], 2), f(d["vs_fz"][7]["hi"], 2), f(d["vs_fz"][21]["lo"], 2), f(d["vs_fz"][21]["hi"], 2)))
    md.append("\nШумовой максимум годового Шарпа: из 4 вариантов выбора %s и %s, из %d испытаний %s и %s (направления %s и %s).\n" % (
        f(R["dirs"][0]["noise_bar_4"], 2), f(R["dirs"][1]["noise_bar_4"], 2), TRIALS, f(R["dirs"][0]["noise_bar_23"], 2), f(R["dirs"][1]["noise_bar_23"], 2), HN[R["dirs"][0]["test_half"]], HN[R["dirs"][1]["test_half"]]))
    md.append("Куда ушли деньги у выбранных RD (метка = режим торговли):\n")
    ar = []
    for d in R["dirs"]:
        ar += d["attr"]
    md.append(md_attr_sum(ar) + "\n")
    md.append(md_attr(ar) + "\n")
    # по месяцам
    MN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov"]
    md.append("По месяцам, Σ $ (проверочная половина; январь — с 21.01):\n")
    md.append("| прогон | половина | " + " | ".join(MN) + " |\n|---|---|" + "---|" * len(MN))
    for d in R["dirs"]:
        for nm, mm in ((FZ, d["months_fz"]), (d["name"], d["months"])):
            md.append("| %s | %s | %s |" % (nm, HN[d["test_half"]], " | ".join(f(mm[m]) if m in mm else "" for m in MN)))
    R["metrics"] = {"%s|%s" % k: {kk: vv for kk, vv in v.items() if kk != "daily"} for k, v in M.items()}
    json.dump(R, open(os.path.join(out, "result_rd.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    open(os.path.join(out, "tables_rd.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    # SQLite (В-179)
    db = sqlite3.connect(os.path.join(out, "tk729.sqlite"))
    db.executescript("""DROP TABLE IF EXISTS tk729rd_run; DROP TABLE IF EXISTS tk729rd_attr; DROP TABLE IF EXISTS tk729rd_day;
    CREATE TABLE tk729rd_run(run TEXT, half TEXT, usd REAL, cycles INT, cyc_usd REAL, exit_usd REAL, sharpe REAL, n_edge INT, n_time INT, n_reg INT, margin REAL, mean_coins REAL, dd REAL, dd_pct REAL, months_plus INT, months_n INT, ex_best10 REAL, lo7 REAL, hi7 REAL, lo21 REAL, hi21 REAL, PRIMARY KEY(run, half));
    CREATE TABLE tk729rd_attr(run TEXT, half TEXT, regime TEXT, side TEXT, usd REAL, cyc_usd REAL, exit_usd REAL, n_cyc INT, n_edge INT, n_other INT, episodes INT);
    CREATE TABLE tk729rd_day(run TEXT, day INT, usd REAL);""")
    for (nm, h), m in M.items():
        db.execute("INSERT INTO tk729rd_run VALUES(%s)" % ",".join("?" * 21), (nm, h, m["usd"], m["cycles"], m["cyc_usd"], m["exit_usd"], m["sharpe"], m["n_edge"], m["n_time"], m["n_reg"], m["margin"], m["mean_coins_in_work"], m["dd"], m["dd_pct"],
                   m["months_plus"], m["months_n"], m["ex_best10"], m["boot"][7]["lo"], m["boot"][7]["hi"], m["boot"][21]["lo"], m["boot"][21]["hi"]))
    for r in rows51:
        db.execute("INSERT INTO tk729rd_attr VALUES(?,?,?,?,?,?,?,?,?,?,?)", (r["run"], r["half"], r["regime"], r["side"], r["usd"], r["cyc_usd"], r["exit_usd"], r["n_cyc"], r["n_edge"], r["n_other"], r["episodes"]))
    for nm in RD:
        for h, (d0, d1) in A.HALF.items():
            for r in attr_rows(nm, h, runs[nm], d0, d1):
                db.execute("INSERT INTO tk729rd_attr VALUES(?,?,?,?,?,?,?,?,?,?,?)", (r["run"], r["half"], r["regime"], r["side"], r["usd"], r["cyc_usd"], r["exit_usd"], r["n_cyc"], r["n_edge"], r["n_other"], r["episodes"]))
    for nm in RD + T51:
        daily = runs[nm][:, L.R_PNL, :].sum(0)
        db.executemany("INSERT INTO tk729rd_day VALUES(?,?,?)", [(nm, i, float(v)) for i, v in enumerate(daily)])
    db.commit()
    db.close()
    print("verdict:", R["verdict"])


if __name__ == "__main__":
    main()
