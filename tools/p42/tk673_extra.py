#!/usr/bin/env python3
"""TK-673 этап 1б/2: (1) ожидание перехая от длины окна при Шарпе S (нуль «независимые нормальные сутки»); (2) D_S по неделям — трейл / стопы / rt против размаха BTC и кучность стопов (перестановка видов выходов между сделками при тех же временах).
Запуск на srv: /opt/venv/bin/python tk673_extra.py <каталог trades-*.csv и btc-days.csv> <каталог выхода>"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk673_gap as g  # noqa: E402

ND = 136
rng = np.random.default_rng(6731)


def nrm(x, nd=1, sign=False):
    return g.num(x, nd, sign)


def calib():
    rows = []
    for S in (2.0, 4.1, 5.0, 8.0):
        for W in (14, 30, 61, 92, 136, 365):
            X = rng.normal(S / np.sqrt(365), 1.0, size=(4000, W))
            gap, under, _, highs = g.vstats(X)
            rows.append([nrm(S), W, nrm(gap.mean()), nrm(g.pq(gap, 90), 0), "%d %%" % round(float((gap >= W).mean()) * 100), "%d %%" % round(float(under.mean()) * 100)])
    return g.table(["годовой Шарп", "окно, сут.", "самый длинный перехай: среднее", "p90", "доля окон без единого нового максимума", "доля суток под водой"], rows)


def weeks(indir):
    T = pd.read_csv(os.path.join(indir, "trades-D_S.csv"))
    B = pd.read_csv(os.path.join(indir, "btc-days.csv")).sort_values("day")
    T["wk"] = T.day_out // 7
    B["wk"] = B.day // 7
    gap0, gap1 = 50, 104                         # 20.08–13.10 (сутки от 01.07)
    rows = []
    for w, d in T.groupby("wk"):
        b = B[B.wk == w]
        k = d.kind
        rows.append(dict(wk=int(w), d0=int(w * 7), n=len(d), trail=int((k == "trail").sum()), rt=int((k == "rt").sum()), stop=int(k.isin(["stop", "nt"]).sum()), sum=float(d.pnl.sum()),
                         win=float((d.pnl > 0).mean()), rng=float(b.btc_range.mean()), absmv=float(b.btc_move.abs().mean())))
    W = pd.DataFrame(W if False else rows)
    cor = {c: float(np.corrcoef(W[c], W["rng"])[0, 1]) for c in ("trail", "rt", "stop", "sum", "n")}
    inside = W[(W.d0 >= gap0) & (W.d0 + 6 <= gap1)]
    outside = W[(W.d0 + 6 < gap0) | (W.d0 > gap1)]
    # кучность стопов: перестановка видов выходов между сделками при тех же временах закрытия; статистики — индекс дисперсии суточного числа стопов и максимум стопов в окне 55 суток
    isstop = T.kind.isin(["stop", "nt"]).to_numpy()
    d = T.day_out.to_numpy()

    def stat(flags):
        c = np.bincount(d[flags], minlength=ND)
        cs = np.concatenate([[0], np.cumsum(c)])
        m55 = (cs[55:] - cs[:-55]).max()
        return c.var(ddof=1) / max(c.mean(), 1e-9), int(m55)
    obs = stat(isstop)
    sims = np.array([stat(rng.permutation(isstop)) for _ in range(5000)])
    inside_gap_stops = int(((d >= gap0 + 1) & (d <= gap1 - 1) & isstop).sum())
    return W, cor, inside, outside, obs, sims, inside_gap_stops, int(isstop.sum())


def main():
    indir, outdir = sys.argv[1:3]
    os.makedirs(outdir, exist_ok=True)
    md = ["# TK-673 этап 1б (сгенерировано `tools/p42/tk673_extra.py`)\n", "## 1. Ожидаемый перехай от длины окна (независимые нормальные сутки, 4 000 прогонов на точку)\n"]
    md += calib()
    W, cor, inside, outside, obs, sims, ig, ns = weeks(indir)
    md += ["## 2. D_S по неделям (неделя = 7 суток от 01.07; сутки по закрытию)\n",
           "Зазор D_S 20.08–13.10 = сутки 50–104 (недели 8–14 целиком). Размах BTC — средний (high−low)/close суток, %.\n"]
    md += g.table(["нед.", "с", "сделок", "трейл", "rt", "стопов", "Σ, $", "доля плюсов", "размах BTC, %", "|ход BTC|, %"],
                  [[int(r.wk), g.ds(r.d0), int(r.n), int(r.trail), int(r.rt), int(r.stop), nrm(r["sum"], 0, True), "%d %%" % round(r.win * 100), nrm(r.rng, 1), nrm(r.absmv, 1)] for _, r in W.iterrows()])
    md += ["Недели целиком внутри зазора (%d): трейл %s в неделю, стопов %s, Σ %s $/нед., размах BTC %s %%; вне зазора (%d недель): трейл %s, стопов %s, Σ %s $/нед., размах BTC %s %%." % (
        len(inside), nrm(inside.trail.mean()), nrm(inside.stop.mean()), nrm(inside["sum"].mean(), 0, True), nrm(inside.rng.mean()), len(outside), nrm(outside.trail.mean()), nrm(outside.stop.mean()), nrm(outside["sum"].mean(), 0, True), nrm(outside.rng.mean())), "",
          "Корреляция недельных чисел с размахом BTC за неделю (по %d неделям): трейл %s, rt %s, стопы %s, Σ %s, всего сделок %s." % (len(W), *[nrm(cor[c], 2, True) for c in ("trail", "rt", "stop", "sum", "n")]), ""]
    md += ["Кучность стопов D_S (%d стопов): индекс дисперсии суточного числа %s (перестановка видов выходов между сделками: среднее %s, p95 %s, P(≥ факт) %s); максимум стопов в любом окне 55 суток %d (перестановка: среднее %s, p95 %s, P(≥ факт) %s); в зазоре (сутки 51–103) — %d стопов." % (
        ns, nrm(obs[0], 2), nrm(sims[:, 0].mean(), 2), nrm(np.percentile(sims[:, 0], 95), 2), "%d %%" % round(float((sims[:, 0] >= obs[0] - 1e-9).mean()) * 100), obs[1], nrm(sims[:, 1].mean()), nrm(np.percentile(sims[:, 1], 95), 0),
        "%d %%" % round(float((sims[:, 1] >= obs[1]).mean()) * 100), ig), ""]
    open(os.path.join(outdir, "extra.md"), "w", encoding="utf-8").write("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
