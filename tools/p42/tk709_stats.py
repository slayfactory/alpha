#!/usr/bin/env python3
"""TK-709 (протокол P-TK709 §6–§7, поправка PS-1019): метрики 9 клеток по периодам А/Б, блочный бутстреп по суткам, Холм, концентрация, справочные R1–R4, R2 (безубыточная добавка), «тонко» по R3, ёмкость.
Запуск (srv): python3 tk709_stats.py <БД> <run_id сделок> <run_id событий> [выходной json]
Пишет `cells(run_id, variant, x, h, period, metric, value)` и `tests(run_id, x, h, period, …)` (повтор с тем же run_id — отказ) и печатает вердикт по правилу §7.
Определения: суточный P&L — по дню выхода (UTC), нулевые сутки включены; А = 01.01–30.06 (181 сут), Б = 01.07–22.11 (145 сут); Шарп = mean/sd·√365; маржа 250 $ (В-169)."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk663_shape as sh  # noqa: E402

XS = (0.05, 0.10, 0.20)
HS = (5, 15, 60)
PERIODS = {"A": ("2026-01-01", "2026-06-30"), "B": ("2026-07-01", "2026-11-22")}
MARGIN = 250.0
NOTIONAL = 500.0
FEE = 0.000315
B_BOOT = 10_000
SEED = 709
MIN_DAYS = 30
SHARE_MAX = 0.5
THIN_WARN = 0.20


def days_of(period):
    a, b = (dt.date.fromisoformat(x) for x in PERIODS[period])
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def daily(df, period):
    idx = days_of(period)
    s = df.groupby("day")["pnl"].sum() if len(df) else pd.Series(dtype=float)
    return s.reindex(idx, fill_value=0.0).to_numpy(float), idx


def sharpe(d):
    sd = float(np.std(d, ddof=1)) if len(d) > 1 else 0.0
    return float(d.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0


def perehai(d):
    r = sh.uw(d)
    cum, peak = r["cum"], r["peak"]
    prev = np.concatenate([[0.0], peak[:-1]])
    hi = np.nonzero(cum > prev + sh.EPS)[0]
    gaps = np.diff(np.concatenate([[-1], hi])) if len(hi) else np.array([len(d)])
    return {"gap_longest": int(r["gap_longest"]), "gap_mean": float(r["gap_mean"]) if not math.isnan(r["gap_mean"]) else float("nan"),
            "gap_p90": float(np.percentile(gaps, 90)), "tail": int(r["tail"]), "under_share": float(r["under_share"]), "maxdd": float(r["maxdd"]),
            "dd": r["dd"], "highs": int(r["highs"])}


def boot_p(d, rng):
    """Круговой блочный бутстреп среднего суточного P&L: (p двусторонняя, ci_lo, ci_hi, mean)."""
    n = len(d)
    L = max(1, math.ceil(n ** (1 / 3)))
    nb = math.ceil(n / L)
    starts = rng.integers(0, n, size=(B_BOOT, nb))
    idx = ((starts[:, :, None] + np.arange(L)[None, None, :]) % n).reshape(B_BOOT, nb * L)[:, :n]
    m = d[idx].mean(axis=1)
    p_lo, p_hi = float((m <= 0).mean()), float((m >= 0).mean())
    return min(1.0, 2 * min(p_lo, p_hi)), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)), float(d.mean())


def holm(ps):
    """ps — список p; скорректированные по Холму в исходном порядке."""
    k = len(ps)
    order = np.argsort(ps)
    adj = np.empty(k)
    run = 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (k - r) * ps[i]))
        adj[i] = run
    return adj.tolist()


def peak_concurrent(df):
    """(макс. одновременных позиций, сутки пика) по интервалам [t0, t1]."""
    if not len(df):
        return 0, ""
    ev = np.concatenate([np.stack([df["t0"].to_numpy(), np.ones(len(df))], 1), np.stack([df["t1"].to_numpy(), -np.ones(len(df))], 1)])
    o = np.lexsort((ev[:, 1], ev[:, 0]))                   # при равенстве времени выход (−1) раньше входа (+1)
    c = np.cumsum(ev[o, 1])
    k = int(np.argmax(c))
    day = dt.datetime.fromtimestamp(ev[o][k, 0] / 1000, dt.timezone.utc).date().isoformat()
    return int(c[k]), day


def breakeven_bps(df):
    """Добавка к цене входа (бп), при которой Σ P&L сделок = 0; None — вне [−200; 5000]."""
    if not len(df):
        return None
    e0, e1 = df["e0"].to_numpy(), df["e1"].to_numpy()

    def total(bp):
        g = e1 / (e0 * (1 + bp / 1e4)) - 1.0
        return float((NOTIONAL * g - NOTIONAL * FEE - NOTIONAL * (1.0 + g) * FEE).sum())

    lo, hi = -200.0, 5000.0
    if total(lo) <= 0:
        return None
    if total(hi) > 0:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if total(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def cell_metrics(df, period, ev=None):
    d, idx = daily(df, period)
    out = {"n": len(df), "coins": int(df["sym"].nunique()) if len(df) else 0, "days_with": int((df.groupby("day").size() > 0).sum()) if len(df) else 0,
           "sum_usd": float(df["pnl"].sum()) if len(df) else 0.0, "mean_usd": float(df["pnl"].mean()) if len(df) else float("nan"),
           "hit": float((df["pnl"] > 0).mean()) if len(df) else float("nan"), "gross_pct": float(df["gross"].mean() * 100) if len(df) else float("nan"),
           "sharpe": sharpe(d), "mean_daily": float(d.mean())}
    if len(df):
        win, loss = df.loc[df["pnl"] > 0, "pnl"], df.loc[df["pnl"] <= 0, "pnl"]
        out["rr"] = float(win.mean() / -loss.mean()) if len(win) and len(loss) and loss.mean() < 0 else float("nan")
        out["worst_usd"] = float(df["pnl"].min())
        out["worst_pct_margin"] = float(df["pnl"].min() / MARGIN * 100)
        out["mae_mean_pct"] = float(df["mae"].mean() * 100)
        out["mae_le5"] = float((df["mae"] <= -0.05).mean())
        out["mae_le10"] = float((df["mae"] <= -0.10).mean())
        out["delay_mean_ms"] = float(df["delay_ms"].mean())
        out["delay_p90_ms"] = float(df["delay_ms"].quantile(0.9))
        for k, nm in ((1, "exit_ext"), (2, "exit_part"), (3, "exit_last")):
            out[nm] = int((df["exit_status"] == k).sum())
        pk, pkday = peak_concurrent(df)
        out["max_concurrent"], out["peak_usd"], out["peak_day"] = pk, pk * NOTIONAL, pkday
    uwm = perehai(d)
    out["maxdd_usd"], out["maxdd_pct_margin"] = uwm["maxdd"], uwm["maxdd"] / MARGIN * 100
    out["ph_longest"], out["ph_mean"], out["ph_p90"], out["ph_tail"], out["under_share"] = uwm["gap_longest"], uwm["gap_mean"], uwm["gap_p90"], uwm["tail"], uwm["under_share"]
    if uwm["dd"]:
        dd = uwm["dd"]
        out["dd_peak_day"] = idx[dd["peak"]] if dd["peak"] >= 0 else idx[0]
        out["dd_trough_day"] = idx[dd["trough"]]
    return out, d


def concentration(df, period):
    """Доли двух лучших монет / суток в Σ $ и Σ после их удаления."""
    tot = float(df["pnl"].sum())
    if not len(df) or tot <= 0:
        return {"coin2_share": float("nan"), "day2_share": float("nan"), "sum_wo_coin2": tot, "sum_wo_day2": tot}
    c = df.groupby("sym")["pnl"].sum().sort_values(ascending=False)
    dd = df.groupby("day")["pnl"].sum().sort_values(ascending=False)
    c2, d2 = float(c.head(2).sum()), float(dd.head(2).sum())
    return {"coin2_share": c2 / tot, "day2_share": d2 / tot, "sum_wo_coin2": tot - c2, "sum_wo_day2": tot - d2}


def main():
    db, run, evrun = sys.argv[1], sys.argv[2], sys.argv[3]
    outj = sys.argv[4] if len(sys.argv) > 4 else None
    con = sqlite3.connect(db)
    con.execute("create table if not exists cells(run_id text, variant text, x real, h integer, period text, metric text, value text)")
    con.execute("create table if not exists tests(run_id text, x real, h integer, period text, mean_daily real, ci_lo real, ci_hi real, p_two real, p_holm real, days_with integer, ok_sign integer, ok_days integer, ok_boot integer, ok_conc integer, coin2_share real, day2_share real, sum_wo_coin2 real, sum_wo_day2 real)")
    if con.execute("select count(*) from tests where run_id=?", (run,)).fetchone()[0]:
        raise SystemExit("run_id уже есть: " + run)
    tr = pd.read_sql_query("select * from trades where run_id=?", con, params=(run,))
    ev = pd.read_sql_query("select * from events where run_id=?", con, params=(evrun,))
    cnt = pd.read_sql_query("select * from counts where run_id=?", con, params=(run,))
    rng = np.random.default_rng(SEED)
    cells, tests = [], []
    # --- метрики всех вариантов × клеток × периодов
    daily_main = {}
    for variant in sorted(tr["variant"].unique()):
        for x in XS:
            for h in HS:
                for per in PERIODS:
                    df = tr[(tr["variant"] == variant) & (tr["x"] == x) & (tr["h"] == h) & (tr["period"] == per)]
                    m, d = cell_metrics(df, per)
                    if variant == "main":
                        daily_main[(x, h, per)] = d
                        m.update(concentration(df, per))
                        # возврат к индексу (по событиям, вошедшим в сделки)
                        e = ev[(ev["x"] == x)]
                        k = df[["sym", "t_event"]].merge(e[["sym", "t_ms", "p_open_next", f"p_end{h}"]], left_on=["sym", "t_event"], right_on=["sym", "t_ms"], how="left")
                        f = (k[f"p_end{h}"] - k["p_open_next"]) / k["p_open_next"].abs()
                        f = f.dropna()
                        m["f_mean"], m["f_ge_half"], m["f_gt0"] = (float(f.mean()), float((f >= 0.5).mean()), float((f > 0).mean())) if len(f) else (float("nan"),) * 3
                        be = breakeven_bps(df)
                        m["breakeven_extra_bps"] = be if be is not None else "none"
                        for bp in (10, 30, 100):
                            e0, e1 = df["e0"].to_numpy(), df["e1"].to_numpy()
                            g = e1 / (e0 * (1 + bp / 1e4)) - 1.0
                            m[f"sum_usd_extra{bp}"] = float((NOTIONAL * g - NOTIONAL * FEE - NOTIONAL * (1.0 + g) * FEE).sum()) if len(df) else 0.0
                    if variant == "R3":
                        th = df[df["thin_main"] == 1]
                        m["thin_n"] = len(th)
                        m["thin_sum_usd"] = float(th["pnl"].sum()) if len(th) else 0.0
                        m["thin_mean_usd"] = float(th["pnl"].mean()) if len(th) else float("nan")
                    for kk, vv in m.items():
                        cells.append((run, variant, x, h, per, kk, str(vv)))
    # --- счётчики охвата
    for r in cnt.itertuples():
        cells.append((run, r.variant, r.x, r.h, "all", "count_" + r.reason, str(r.n)))
    # --- тесты клеток (main): бутстреп, Холм по 9 в периоде
    res = {}
    for per in PERIODS:
        keys = [(x, h) for x in XS for h in HS]
        stats = {k: boot_p(daily_main[(k[0], k[1], per)], rng) for k in keys}
        adj = holm([stats[k][0] for k in keys])
        for k, a in zip(keys, adj):
            df = tr[(tr["variant"] == "main") & (tr["x"] == k[0]) & (tr["h"] == k[1]) & (tr["period"] == per)]
            p2, lo, hi, mean = stats[k]
            conc = concentration(df, per)
            dw = int((df.groupby("day").size() > 0).sum()) if len(df) else 0
            ok_sign = int(float(df["pnl"].sum()) > 0 and mean > 0)
            ok_days = int(dw >= MIN_DAYS)
            ok_boot = int(a < 0.05 and mean > 0)
            ok_conc = int(ok_sign and conc["coin2_share"] <= SHARE_MAX and conc["day2_share"] <= SHARE_MAX and conc["sum_wo_coin2"] > 0 and conc["sum_wo_day2"] > 0)
            res[(k, per)] = dict(p2=p2, adj=a, lo=lo, hi=hi, mean=mean, dw=dw, ok_sign=ok_sign, ok_days=ok_days, ok_boot=ok_boot, ok_conc=ok_conc, **conc)
            tests.append((run, k[0], k[1], per, mean, lo, hi, p2, a, dw, ok_sign, ok_days, ok_boot, ok_conc, conc["coin2_share"], conc["day2_share"], conc["sum_wo_coin2"], conc["sum_wo_day2"]))
    con.executemany("insert into cells values (?,?,?,?,?,?,?)", cells)
    con.executemany("insert into tests values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tests)
    con.commit()
    # --- вердикт по §7
    verdict = {}
    for x in XS:
        for h in HS:
            k = (x, h)
            ok = all(res[(k, p)]["ok_sign"] and res[(k, p)]["ok_days"] and res[(k, p)]["ok_boot"] and res[(k, p)]["ok_conc"] for p in PERIODS)
            r4 = {}
            if ok:
                for v in ("R4_1s", "R4_5s"):
                    r4[v] = [float(tr[(tr["variant"] == v) & (tr["x"] == x) & (tr["h"] == h) & (tr["period"] == p)]["pnl"].sum()) for p in PERIODS]
            c = cnt[(cnt["variant"] == "main") & (cnt["x"] == x) & (cnt["h"] == h)]
            th, trd = int(c[c["reason"] == "thin"]["n"].sum()), int(c[c["reason"] == "traded"]["n"].sum())
            thin_share = th / (th + trd) if th + trd else float("nan")
            verdict[f"{x}|{h}"] = {"pass": bool(ok), "R4": r4, "thin_share_all_periods": thin_share,
                                   "A": {kk: res[(k, "A")][kk] for kk in ("mean", "lo", "hi", "adj", "dw", "ok_sign", "ok_days", "ok_boot", "ok_conc", "coin2_share", "day2_share")},
                                   "B": {kk: res[(k, "B")][kk] for kk in ("mean", "lo", "hi", "adj", "dw", "ok_sign", "ok_days", "ok_boot", "ok_conc", "coin2_share", "day2_share")}}
    npass = sum(1 for v in verdict.values() if v["pass"])
    gate = {}
    for x in XS:
        for h in HS:
            g = [tr[(tr["variant"] == "main") & (tr["x"] == x) & (tr["h"] == h) & (tr["period"] == p)]["gross"].mean() for p in PERIODS]
            gate[f"{x}|{h}"] = [float(v) if v == v else float("nan") for v in g]
    signal = any(all(v == v and v > 0 for v in g) for g in gate.values())
    out = {"run": run, "gate_signal": signal, "gross_mean_by_cell_A_B": gate, "passed_cells": npass if signal else 0, "verdict": verdict}
    if outj:
        json.dump(out, open(outj, "w"), ensure_ascii=False, indent=1, default=str)
    print("gate signal (gross mean > 0 in A and B for some cell):", signal)
    print("passed", npass, "of 9")
    for k, v in verdict.items():
        print(k, "PASS" if v["pass"] else "no", "A mean/day %.2f p_adj %.3f" % (v["A"]["mean"], v["A"]["adj"]), "B mean/day %.2f p_adj %.3f" % (v["B"]["mean"], v["B"]["adj"]))


if __name__ == "__main__":
    main()
