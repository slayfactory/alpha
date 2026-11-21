#!/usr/bin/env python3
"""TK-673 этап 1 — почему «перехай» одной линии 20+ суток (описание готовых сделок 6 линий, h775, 01.07–13.11, 136 суток, 500 $, после комиссий).
Сделки — `trades-<линия>.csv` из `tk673_trades.py` (движок не запускался, В-213). Сутки — UTC по закрытию; новый максимум = эквити конца суток строго выше прежнего пика (старт 0); зазор — сутки между соседними максимумами (`tk663_shape.uw`).
1. Нули: (А) перестановка P&L между сделками линии при тех же временах закрытия (Σ фиксирована); (Б) круговой блочный бутстреп по суткам (блок 6 = ⌈136^{1/3}⌉; Σ плавает); (В) перестановка суток (чистая арифметика).
   Эталон «чистая арифметика»: независимые нормальные сутки при годовом Шарпе S, зазор от S.
2. Чувствительность: по одному рычагу — +$ на сделку, доля плюсов, R:R (масштаб плюсов), масштаб минусов, сделок в сутки (×m); сделки — из собственных пулов линии (плюсы / минусы), число сделок по суткам — как у линии.
3. Анатомия трёх самых длинных зазоров каждой линии.
Запуск на srv: /opt/venv/bin/python tk673_gap.py <каталог trades-*.csv> <каталог выхода>   (NSIM — число прогонов нуля, по умолчанию 10000)
Выход: tables.md, summary.json, gaps.csv. Только описание, без вердикта."""
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk663_shape as sh  # noqa: E402

uw, num, pct, table = sh.uw, sh.num, sh.pct, sh.table
LINES = ["S_base", "S_mk1", "D_S", "L_base", "L_n24", "L_mk2"]
ND = 136
D0 = dt.date(2026, 7, 1)
EPS = 1e-9
SEED = 673
NSIM = int(os.environ.get("NSIM", 10000))
RSENS = int(os.environ.get("RSENS", 1500))
BLOCK = 6
STOP_KINDS = ("stop", "nt")
TARGETS = (10, 5)


def ds(d):
    return (D0 + dt.timedelta(days=int(d))).strftime("%d.%m")


def vstats(X):
    """Построчно (R×ND): самый длинный зазор между новыми максимумами, доля суток под водой, макс. просадка, число новых максимумов. Равно `uw` на каждой строке (selftest)."""
    R, n = X.shape
    cum = np.cumsum(X, axis=1)
    peak = np.maximum(np.maximum.accumulate(cum, axis=1), 0.0)
    prev = np.concatenate([np.zeros((R, 1)), peak[:, :-1]], axis=1)
    new = cum > prev + EPS
    last = np.maximum.accumulate(np.where(new, np.arange(n)[None, :], -1), axis=1)
    lastprev = np.concatenate([np.full((R, 1), -1), last[:, :-1]], axis=1)
    gap = (np.arange(n)[None, :] - lastprev).max(axis=1)
    return gap, (cum < peak - EPS).mean(axis=1), (peak - cum).max(axis=1), new.sum(axis=1)


def selftest():
    rng = np.random.default_rng(1)
    X = rng.normal(0.3, 1.0, size=(200, ND))
    g, u, d, h = vstats(X)
    for r in range(200):
        a = uw(X[r])
        assert a["gap_longest"] == g[r] and abs(a["under_share"] - u[r]) < 1e-12 and abs(a["maxdd"] - d[r]) < 1e-9 and a["highs"] == h[r], r
    sh.selftest()


def skew(x):
    x = np.asarray(x, float)
    s = x.std()
    return float(((x - x.mean()) ** 3).mean() / s ** 3) if s > 0 else 0.0


def pq(a, q):
    return float(np.percentile(a, q))


# ───────────────────────────── нули ─────────────────────────────

def null_trade_perm(df, R, rng):
    pnl, d = df.pnl.to_numpy(), df.day_out.to_numpy()
    X = np.empty((R, ND))
    for r in range(R):
        X[r] = np.bincount(d, weights=rng.permutation(pnl), minlength=ND)
    return X


def null_block(sd, R, rng, L=BLOCK):
    nb = -(-ND // L)
    st = rng.integers(0, ND, size=(R, nb))
    idx = ((st[:, :, None] + np.arange(L)[None, None, :]) % ND).reshape(R, nb * L)[:, :ND]
    return sd[idx]


def null_dayperm(sd, R, rng):
    return sd[np.argsort(rng.random((R, ND)), axis=1)]


def null_row(X, obs):
    g, u, d, h = vstats(X)
    return dict(gap_mean=float(g.mean()), gap_p10=pq(g, 10), gap_p90=pq(g, 90), gap_ge=float((g >= obs["gap_longest"]).mean()),
                under_mean=float(u.mean()), under_p10=pq(u, 10), under_p90=pq(u, 90), under_ge=float((u >= obs["under_share"] - 1e-12).mean()),
                dd_mean=float(d.mean()), dd_p90=pq(d, 90), dd_ge=float((d >= obs["maxdd"] - 1e-9).mean()), highs_mean=float(h.mean()))


def sharpe_curve(rng, R=4000):
    """Независимые нормальные сутки, годовой Шарп S: зазор за 136 суток."""
    rows = []
    for S in np.arange(1.0, 30.01, 0.5):
        X = rng.normal(S / np.sqrt(365), 1.0, size=(R, ND))
        g = vstats(X)[0]
        rows.append((float(S), float(g.mean()), pq(g, 90)))
    return rows


def curve_req(curve, T, col):
    for S, m, p in curve:
        if (m if col == "mean" else p) <= T:
            return S
    return None


# ───────────────────────────── рычаги ─────────────────────────────

def sim_X(rng, counts, pw, wins, losses, delta=0.0, wscale=1.0, lscale=1.0, R=RSENS):
    K = int(counts.sum())
    isw = rng.random((R, K)) < pw
    V = np.where(isw, wins[rng.integers(0, len(wins), size=(R, K))] * wscale, losses[rng.integers(0, len(losses), size=(R, K))] * lscale) + delta
    cs = np.concatenate([np.zeros((R, 1)), np.cumsum(V, axis=1)], axis=1)
    b = np.concatenate([[0], np.cumsum(counts)])
    return np.diff(cs[:, b], axis=1)


def scaled_counts(counts, m):
    c = np.round(np.cumsum(counts) * m).astype(int)
    return np.diff(np.concatenate([[0], c]))


def sens_eval(X):
    g = vstats(X)[0]
    day_mean, day_sd = X.mean(axis=1), X.std(axis=1, ddof=1)
    sharpe = np.where(day_sd > 0, day_mean / np.where(day_sd > 0, day_sd, 1) * np.sqrt(365), 0.0)
    return dict(gap_mean=float(g.mean()), gap_p90=pq(g, 90), sigma=float(X.sum(axis=1).mean()), sharpe=float(sharpe.mean()))


def find_req(grid, vals, col, T):
    """Первое значение рычага, при котором статистика зазора (mean / p90) ≤ T; линейная интерполяция между соседями сетки. None — не достигнуто."""
    st = [v["gap_mean"] if col == "mean" else v["gap_p90"] for v in vals]
    for i, s in enumerate(st):
        if s <= T:
            if i == 0:
                return grid[0]
            x0, x1, s0, s1 = grid[i - 1], grid[i], st[i - 1], s
            return float(x0 + (x1 - x0) * (s0 - T) / (s0 - s1)) if s0 != s1 else float(x1)
    return None


def levers(rng, df):
    pnl = df.pnl.to_numpy()
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    counts = np.bincount(df.day_out, minlength=ND)
    n, pw0, mu0 = len(pnl), float((pnl > 0).mean()), float(pnl.mean())
    mw, ml = float(wins.mean()), float(losses.mean())
    out = {}
    # 1. +$ на сделку: итоговое среднее = mu0 × f
    fg = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 15, 20]
    vals = [sens_eval(sim_X(rng, counts, pw0, wins, losses, delta=mu0 * (f - 1))) for f in fg]
    out["mean_trade"] = ([mu0 * f for f in fg], vals)
    # 2. доля плюсов
    pg = [pw0] + [x for x in np.arange(0.30, 0.9501, 0.05) if x > pw0 + 0.005]
    vals = [sens_eval(sim_X(rng, counts, p, wins, losses)) for p in pg]
    out["win_share"] = (pg, vals)
    # 3. R:R (масштаб плюсов, убыток как есть)
    wg = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 15]
    vals = [sens_eval(sim_X(rng, counts, pw0, wins, losses, wscale=w)) for w in wg]
    out["rr"] = ([mw * w / abs(ml) for w in wg], vals)
    # 4. масштаб минусов (средний убыток)
    lg = [1, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0]
    vals = [sens_eval(sim_X(rng, counts, pw0, wins, losses, lscale=x)) for x in lg]
    out["loss_scale"] = ([ml * x for x in lg], vals)
    # 5. сделок в сутки ×m
    mg = [1, 1.5, 2, 3, 4, 6, 8, 12, 16]
    vals = [sens_eval(sim_X(rng, scaled_counts(counts, m), pw0, wins, losses, R=600)) for m in mg]
    out["freq"] = ([n / ND * m for m in mg], vals)
    out["_base"] = dict(n=n, pw0=pw0, mu0=mu0, mw=mw, ml=ml, rr0=mw / abs(ml), per_day=n / ND)
    return out


# ───────────────────────────── анатомия зазоров ─────────────────────────────

def kinds_str(g, maxn=5):
    if len(g) == 0:
        return "—"
    t = g.groupby("kind").pnl.agg(["count", "sum"]).sort_values("sum")
    return "; ".join("%s %d / %s $" % (k, int(r["count"]), num(r["sum"], 1, True)) for k, r in t.head(maxn).iterrows())


def anatomy(name, df, sd, btc_mv, top=3):
    a = uw(sd)
    cum, peak = a["cum"], a["peak"]
    hi = np.nonzero(cum > np.concatenate([[0.0], peak[:-1]]) + EPS)[0]
    pts = np.concatenate([[-1], hi]).astype(int)
    segs = [(int(pts[i]), int(pts[i + 1]), True) for i in range(len(pts) - 1)] + [(int(pts[-1]), ND - 1, False)]
    lens = [e - s for s, e, c in segs]
    order = sorted(range(len(segs)), key=lambda i: (-lens[i], i))[:top]
    rank_tr = df.pnl.rank(ascending=False, method="min")
    rank_day = pd.Series(sd).rank(ascending=False, method="min")
    mu_day = float(sd.mean())
    rows = []
    for rk, i in enumerate(order, 1):
        s0, e, closed = segs[i]
        d_in0, d_in1 = s0 + 1, (e - 1 if closed else e)           # сутки под водой
        gap_days = (s0 + 1, e)
        ins = df[(df.day_out >= d_in0) & (df.day_out <= d_in1)]
        outs = df[(df.day_out < s0 + 1) | (df.day_out > e)]
        n_in_days = max(d_in1 - d_in0 + 1, 1)
        prev_peak = float(peak[s0 - 1]) if s0 >= 1 else 0.0
        top_day = df[df.day_out == s0] if s0 >= 0 else df.iloc[0:0]
        t = top_day.loc[top_day.pnl.idxmax()] if len(top_day) else None
        under = peak[s0 + 1:e + (0 if closed else 1)] - cum[s0 + 1:e + (0 if closed else 1)] if e > s0 else np.array([0.0])
        depth = float(under.max()) if len(under) else 0.0
        tr_day = s0 + 1 + int(np.argmax(under)) if len(under) else s0
        wt = ins.loc[ins.pnl.idxmin()] if len(ins) else None
        fall = sd[s0 + 1:tr_day + 1]
        wd = float(fall.min()) if len(fall) else 0.0
        stops = ins[ins.kind.isin(STOP_KINDS)]
        rt_sum = float(ins[ins.kind == "rt"].pnl.sum())
        loss_sum = float(ins[ins.pnl < 0].pnl.sum())
        mv = btc_mv[d_in0:e + 1] if e >= d_in0 else np.array([])
        mv = mv[~np.isnan(mv)]
        coin_loss = ins[ins.pnl < 0].groupby("sym").pnl.sum().sort_values().head(3)
        flags = []
        if t is not None and rank_tr[t.name] <= 10:
            flags.append("выброс на пике")
        if depth > 0 and abs(float(stops.pnl.sum())) >= 0.5 * depth:
            flags.append("стопы ≥ 50 % глубины")
        if loss_sum < 0 and rt_sum <= 0.5 * loss_sum:
            flags.append("rt ≥ 50 % минусов")
        rows.append(dict(
            line=name, rank=rk, len=lens[i], start=ds(s0 + 1), end=ds(e), closed=closed, peak_day=(ds(s0) if s0 >= 0 else "старт"),
            step=float(cum[s0] - prev_peak) if s0 >= 0 else 0.0, step_days=(float(cum[s0] - prev_peak) / mu_day if s0 >= 0 and mu_day > 0 else 0.0),
            day_a_pnl=float(sd[s0]) if s0 >= 0 else 0.0, day_a_rank=int(rank_day[s0]) if s0 >= 0 else 0,
            top_trade=("%s %s %s $ (№%d из %d)" % (t["sym"], t["kind"], num(t["pnl"], 1, True), int(rank_tr[t.name]), len(df))) if t is not None else "—",
            depth=depth, depth_pct=depth / 250 * 100, fall_days=tr_day - s0, rec_days=(e - tr_day) if closed else None,
            worst_trade=("%s %s %s $" % (wt["sym"], wt["kind"], num(wt["pnl"], 1, True))) if wt is not None else "—",
            worst_trade_share=(abs(float(wt["pnl"])) / depth if (wt is not None and depth > 0) else 0.0), worst_day_share=(abs(wd) / depth if depth > 0 else 0.0),
            n_in=len(ins), per_day_in=len(ins) / n_in_days, per_day_out=len(outs) / max(ND - (e - s0), 1),
            win_in=float((ins.pnl > 0).mean()) if len(ins) else float("nan"), win_out=float((outs.pnl > 0).mean()) if len(outs) else float("nan"),
            sum_in=float(ins.pnl.sum()), mean_in=float(ins.pnl.mean()) if len(ins) else float("nan"), mean_out=float(outs.pnl.mean()) if len(outs) else float("nan"),
            kinds_in=kinds_str(ins), stops_n=len(stops), stops_sum=float(stops.pnl.sum()), rt_sum=rt_sum,
            btc_move=(float(np.prod(1 + mv / 100) - 1) * 100 if len(mv) else float("nan")),
            btc_up=int((mv >= 1).sum()), btc_flat=int((np.abs(mv) < 1).sum()), btc_down=int((mv <= -1).sum()),
            coins_in=int(ins.sym.nunique()), top_loss_coins="; ".join("%s %s $" % (k, num(v, 1, True)) for k, v in coin_loss.items()) or "—",
            closer_pnl=float(sd[e]) if closed else float("nan"),
            closer_trade=("%s %s %s $" % ((lambda c: (c["sym"], c["kind"], num(c["pnl"], 1, True)))(df[df.day_out == e].loc[df[df.day_out == e].pnl.idxmax()]))) if closed and (df.day_out == e).any() else "—",
            flags=", ".join(flags) or "—"))
    return rows


# ───────────────────────────── основной счёт ─────────────────────────────

def main():
    indir, outdir = sys.argv[1:3]
    selftest()
    os.makedirs(outdir, exist_ok=True)
    rng = np.random.default_rng(SEED)
    T = {k: pd.read_csv(os.path.join(indir, "trades-%s.csv" % k)) for k in LINES}
    btc = pd.read_csv(os.path.join(indir, "btc-days.csv"))
    btc_mv = btc.sort_values("day").btc_move.to_numpy()
    assert len(btc_mv) == ND
    exp = {"S_base": (319, 223.14), "S_mk1": (272, 199.77), "D_S": (1237, 725.53), "L_base": (428, 193.17), "L_n24": (181, 294.31), "L_mk2": (327, 180.83)}
    for k, (n, s) in exp.items():
        assert len(T[k]) == n and abs(T[k].pnl.sum() - s) < 0.01, ("сверка", k, len(T[k]), T[k].pnl.sum())
        assert T[k].day_out.between(0, ND - 1).all()
    md, P = [], None
    P = md.append
    S = {}
    SD = {k: np.bincount(v.day_out, weights=v.pnl, minlength=ND) for k, v in T.items()}
    OBS = {k: uw(SD[k]) for k in LINES}
    curve = sharpe_curve(rng)
    S["sharpe_curve"] = curve
    S["curve_req"] = {"mean≤%d" % t: curve_req(curve, t, "mean") for t in TARGETS} | {"p90≤%d" % t: curve_req(curve, t, "p90") for t in TARGETS}

    P("# Таблицы TK-673 этап 1 (сгенерировано `tools/p42/tk673_gap.py`; h775, 01.07–13.11, 136 суток, 500 $, после комиссий, без фандинга)\n")
    P("Сутки — UTC по закрытию; зазор — сутки между новыми максимумами эквити (первый — от старта 0, последний — хвост до 13.11); нули: %d прогонов, seed %d.\n" % (NSIM, SEED))

    # 0. сводка и параметры
    rows, rows2 = [], []
    for k in LINES:
        df, sd, a = T[k], SD[k], OBS[k]
        pnl = df.pnl.to_numpy()
        w, l = pnl[pnl > 0], pnl[pnl <= 0]
        sharp = sh.sharpe(sd)
        S[k] = dict(n=len(df), sum=float(pnl.sum()), mean=float(pnl.mean()), sd=float(pnl.std(ddof=1)), skew=skew(pnl), win=float((pnl > 0).mean()), mw=float(w.mean()), ml=float(l.mean()),
                    rr=float(w.mean() / abs(l.mean())), per_day=len(df) / ND, no_trade_days=float((np.bincount(df.day_out, minlength=ND) == 0).mean()), day_mean=float(sd.mean()), day_sd=float(sd.std(ddof=1)), day_skew=skew(sd),
                    sharpe=sharp, sharpe_scale=365 / sharp ** 2, gap=a["gap_longest"], gap_seg=[ds(a["gap_seg"][0]), ds(a["gap_seg"][1])], gap_mean=a["gap_mean"], tail=a["tail"], highs=a["highs"], under=a["under_share"], maxdd=a["maxdd"],
                    maxdd_pct=a["maxdd"] / 250 * 100, dd_peak=ds(a["dd"]["peak"] + 1) if a["dd"]["peak"] >= 0 else "старт", dd_trough=ds(a["dd"]["trough"]))
        s = S[k]
        rows.append([k, s["n"], num(s["sum"], 0, True), num(s["mean"], 2, True), num(s["sharpe"], 2), num(s["maxdd"], 0) + " (" + num(s["maxdd_pct"], 0) + " %)", s["gap"], "%s–%s" % tuple(s["gap_seg"]), num(s["gap_mean"], 1), s["tail"], pct(s["under"])])
        rows2.append([k, num(s["per_day"], 2), pct(s["no_trade_days"]), num(s["sd"], 2), num(s["skew"], 1), pct(s["win"]), num(s["rr"], 2), num(s["day_mean"], 2, True), num(s["day_sd"], 2), num(s["day_skew"], 1), num(s["sharpe_scale"], 0)])
    P("## 0. Сводка линий (метрики В-169)\n")
    P("Макс. просадка — $ (% маржи 250 $); хвост — суток от последнего максимума до 13.11 (открытый зазор).\n")
    P("\n".join(table(["линия", "сделок", "Σ, $", "на сделку, $", "Шарп", "макс. просадка", "самый длинный перехай, сут.", "где", "средний, сут.", "хвост, сут.", "доля суток под водой"], rows)))
    P("## 0б. Параметры линии для арифметики\n")
    P("сут. μ, σ, асимметрия — по суточным суммам по закрытию; «365/Шарп²» — масштаб зазора в сутках для независимых суток (время, за которое дрейф μ превышает шум σ).\n")
    P("\n".join(table(["линия", "сделок в сутки", "суток без сделок", "σ сделки, $", "асимметрия сделки", "доля плюсов", "R:R", "μ суток, $", "σ суток, $", "асимметрия суток", "365/Шарп², сут."], rows2)))

    # 1. нули
    P("## 1. Арифметика ли это: нули\n")
    P("(А) перестановка P&L между сделками линии при тех же временах закрытия, Σ фиксирована; (Б) круговой блочный бутстреп суток, блок %d, Σ плавает; (В) перестановка суток. «P(≥ факт)» — доля прогонов нуля с зазором (долей под водой, просадкой) не меньше наблюдённого; среднее и p10–p90 по прогонам нуля. Эталон «независимые нормальные сутки при том же Шарпе» — таблица 1б.\n" % BLOCK)
    nulls = {}
    for k in LINES:
        o = OBS[k]
        obs = dict(gap_longest=o["gap_longest"], under_share=o["under_share"], maxdd=o["maxdd"])
        nulls[k] = {"A": null_row(null_trade_perm(T[k], NSIM, rng), obs), "B": null_row(null_block(SD[k], NSIM, rng), obs), "C": null_row(null_dayperm(SD[k], NSIM, rng), obs)}
    S["nulls"] = nulls
    names = {"A": "(А) сделки", "B": "(Б) блоки суток", "C": "(В) сутки"}
    rows = []
    for k in LINES:
        for z in "ABC":
            r = nulls[k][z]
            rows.append([k if z == "A" else "", names[z], OBS[k]["gap_longest"], "%s (%s–%s)" % (num(r["gap_mean"], 1), num(r["gap_p10"], 0), num(r["gap_p90"], 0)), pct(r["gap_ge"]),
                         pct(OBS[k]["under_share"]), "%s (%s–%s)" % (pct(r["under_mean"]), pct(r["under_p10"]), pct(r["under_p90"])), pct(r["under_ge"]),
                         num(OBS[k]["maxdd"], 0), "%s (p90 %s)" % (num(r["dd_mean"], 0), num(r["dd_p90"], 0)), pct(r["dd_ge"])])
    P("\n".join(table(["линия", "нуль", "перехай факт, сут.", "перехай нуль: среднее (p10–p90)", "P(≥ факт)", "под водой факт", "под водой нуль: среднее (p10–p90)", "P(≥ факт)", "просадка факт, $", "просадка нуль: среднее (p90), $", "P(≥ факт)"], rows)))
    P("### 1б. Эталон: независимые нормальные сутки, 136 суток, годовой Шарп S → перехай\n")
    rows = [[num(c[0], 1), num(c[1], 1), num(c[2], 0)] for c in curve if c[0] in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 20, 25, 30)]
    P("\n".join(table(["годовой Шарп S", "перехай: среднее, сут.", "перехай p90, сут."], rows)))
    P("Нужный Шарп (независимые нормальные сутки): среднее ≤ 10 сут. — S ≥ %s, p90 ≤ 10 — S ≥ %s, среднее ≤ 5 — S ≥ %s, p90 ≤ 5 — S ≥ %s.\n" % tuple(num(S["curve_req"][k], 1) if S["curve_req"][k] else "не достигнут до 30" for k in ("mean≤10", "p90≤10", "mean≤5", "p90≤5")))

    # 2. рычаги
    P("## 2. Что нужно, чтобы перехай ≤ 10 и ≤ 5 суток (по одному рычагу)\n")
    P("Модель: сделки линии — независимые выборки из её собственных пулов плюсов и минусов (число сделок по суткам — как у линии, для ×m — масштаб); %d прогонов на точку сетки (×m: 600). «Нужно» — первое значение рычага, при котором среднее (или p90) самого длинного зазора ≤ T, линейная интерполяция по сетке; «—» — не достигнуто на сетке. Рычаги не комбинируются. «Базовая» строка — модель без изменения (проверка: близка к нулю (А)).\n" % RSENS)
    LEV = {"mean_trade": ("на сделку, $", 2, "среднее на сделку"), "win_share": ("доля плюсов", None, "доля плюсов"), "rr": ("R:R", 2, "R:R"), "loss_scale": ("средний убыток, $", 2, "средний убыток"), "freq": ("сделок в сутки", 1, "сделок в сутки")}
    sens = {}
    rows, rows_curve = [], []
    for k in LINES:
        lv = levers(rng, T[k])
        b = lv.pop("_base")
        sens[k] = {"base": b}
        for lk, (grid, vals) in lv.items():
            req = {"%s≤%d" % (c, t): find_req(grid, vals, c, t) for t in TARGETS for c in ("mean", "p90")}
            sens[k][lk] = dict(grid=[float(x) for x in grid], vals=vals, req=req)
            unit, nd, label = LEV[lk]
            f = (lambda x, nd=nd: pct(x) if nd is None else num(x, nd)) if x_is_pct(lk) else (lambda x, nd=nd: num(x, nd))
            base_v = grid[0]
            v0 = vals[0]
            rows.append([k if lk == "mean_trade" else "", label, f(base_v), "%s / %s" % (num(v0["gap_mean"], 1), num(v0["gap_p90"], 0))] + [f(req["%s≤%d" % (c, t)]) if req["%s≤%d" % (c, t)] is not None else "—" for t in TARGETS for c in ("mean", "p90")])
        # кривые отклика
    P("\n".join(table(["линия", "рычаг", "сейчас", "перехай модели сейчас: среднее / p90", "нужно: среднее ≤ 10", "нужно: p90 ≤ 10", "нужно: среднее ≤ 5", "нужно: p90 ≤ 5"], rows)))
    P("### 2б. Кривые отклика (сетка рычага → перехай модели среднее / p90, Σ за 136 сут., годовой Шарп модели)\n")
    for k in LINES:
        for lk in ("mean_trade", "win_share", "rr", "loss_scale", "freq"):
            unit, nd, label = LEV[lk]
            d = sens[k][lk]
            f = (lambda x, nd=nd: pct(x) if nd is None else num(x, nd)) if x_is_pct(lk) else (lambda x, nd=nd: num(x, nd))
            P("**%s — %s**: " % (k, label) + "; ".join("%s → %s / %s, Σ %s $, Ш %s" % (f(g), num(v["gap_mean"], 1), num(v["gap_p90"], 0), num(v["sigma"], 0, True), num(v["sharpe"], 1)) for g, v in zip(d["grid"], d["vals"])) + "\n")
    S["sens"] = sens

    # 3. анатомия
    P("## 3. Анатомия трёх самых длинных зазоров каждой линии\n")
    P("Зазор = от суток пика a (последний максимум) до суток e нового максимума; «под водой» — сутки a+1…e−1 (у открытого хвоста до 13.11). Шаг пика — на сколько сутки a подняли максимум (в сутках μ линии). «№ из N» — ранг сделки по P&L среди всех сделок линии. Метки (описательные, пороги заданы здесь): «выброс на пике» — крупнейшая сделка суток a в топ-10 сделок линии; «стопы ≥ 50 % глубины» — |Σ стопов под водой| ≥ 0,5 глубины; «rt ≥ 50 % минусов» — Σ выходов `rt` под водой ≥ половины Σ убыточных сделок.\n")
    allg = []
    for k in LINES:
        allg += anatomy(k, T[k], SD[k], btc_mv)
    G = pd.DataFrame(allg)
    S["gaps"] = allg
    rows = [[r.line, r["rank"], r.len, "%s–%s%s" % (r.start, r.end, "" if r.closed else " (открыт)"), r.peak_day, "%s $ (%s сут.)" % (num(r.step, 1, True), num(r.step_days, 1)), r.top_trade,
             "%s $ (%s %%)" % (num(r.depth, 1), num(r.depth_pct, 1)), "%d / %s" % (r.fall_days, "—" if r.rec_days is None else r.rec_days), r["flags"]] for _, r in G.iterrows()]
    P("\n".join(table(["линия", "№", "длина, сут.", "даты", "пик", "шаг пика", "крупнейшая сделка суток пика", "глубина $ (% маржи)", "падение / восстановление, сут.", "метки"], rows)))
    rows = [[r.line, r["rank"], r.len, "%d · %s · %s" % (r.n_in, num(r.per_day_in, 2), pct(r.win_in)), "%s · %s" % (num(r.per_day_out, 2), pct(r.win_out)), num(r.mean_in, 2, True) + " / " + num(r.mean_out, 2, True),
             "%s %% (↑%d →%d ↓%d)" % (num(r.btc_move, 1, True), r.btc_up, r.btc_flat, r.btc_down), r.coins_in, r.kinds_in] for _, r in G.iterrows()]
    P("### 3б. Что внутри зазора\n")
    P("Сделок · в сутки · доля плюсов под водой; вне зазора: в сутки · доля плюсов; средний P&L сделки внутри / вне; ход BTC за зазор (↑ суток ≥ +1 %, → боковик, ↓ ≤ −1 %); монет; P&L по видам выхода под водой (сделок / Σ $).\n")
    P("\n".join(table(["линия", "№", "длина", "внутри: сделок · в сутки · плюсов", "вне: в сутки · плюсов", "$/сделку внутри / вне", "BTC за зазор", "монет", "виды выходов внутри"], rows)))
    rows = [[r.line, r["rank"], r.worst_trade, pct(r.worst_trade_share), pct(r.worst_day_share), "%d / %s $" % (r.stops_n, num(r.stops_sum, 1, True)), num(r.rt_sum, 1, True), r.top_loss_coins, "%s %s" % (num(r.closer_pnl, 1, True) if r.closer_pnl == r.closer_pnl else "—", r.closer_trade)] for _, r in G.iterrows()]
    P("### 3в. Чем набрана глубина и чем зазор закрыт\n")
    P("\n".join(table(["линия", "№", "худшая сделка под водой", "её доля глубины", "худшие сутки падения / глубина", "стопов · Σ $", "Σ rt под водой, $", "монеты с наибольшим минусом", "сутки закрытия зазора: P&L · крупнейшая сделка"], rows)))
    cnt = dict(n=len(G), outlier=int(G["flags"].str.contains("выброс").sum()), stop=int(G["flags"].str.contains("стопы").sum()), rt=int(G["flags"].str.contains("rt").sum()),
               freq_lower=int((G.per_day_in < G.per_day_out).sum()), win_lower=int((G.win_in < G.win_out).sum()), btc_neg=int((G.btc_move < 0).sum()))
    S["gap_counts"] = cnt
    P("Общее по %d зазорам: метка «выброс на пике» — %d, «стопы ≥ 50 %% глубины» — %d, «rt ≥ 50 %% минусов» — %d; сделок в сутки под водой меньше, чем вне — в %d; доля плюсов под водой ниже, чем вне — в %d; BTC за зазор отрицателен — в %d.\n" % (cnt["n"], cnt["outlier"], cnt["stop"], cnt["rt"], cnt["freq_lower"], cnt["win_lower"], cnt["btc_neg"]))

    # 4. виды выходов
    P("## 4. Виды выходов по линиям (вся выборка)\n")
    rows = []
    for k in LINES:
        t = T[k].groupby("kind").pnl.agg(["count", "sum", "mean"])
        t["win"] = T[k].groupby("kind").pnl.apply(lambda x: (x > 0).mean())
        t["hold"] = T[k].groupby("kind").hold_min.mean()
        for kd, r in t.sort_values("sum").iterrows():
            rows.append([k if kd == t.sort_values("sum").index[0] else "", kd, int(r["count"]), num(r["sum"], 1, True), num(r["mean"], 2, True), pct(r["win"]), num(r["hold"] / 60, 1)])
    P("\n".join(table(["линия", "вид выхода", "сделок", "Σ, $", "на сделку, $", "доля плюсов", "держание, ч"], rows)))

    open(os.path.join(outdir, "tables.md"), "w", encoding="utf-8").write("\n".join(md))
    json.dump(S, open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    G.to_csv(os.path.join(outdir, "gaps.csv"), index=False)
    print("done", outdir)


def x_is_pct(lk):
    return lk == "win_share"


if __name__ == "__main__":
    main()
