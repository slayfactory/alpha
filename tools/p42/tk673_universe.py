#!/usr/bin/env python3
"""TK-673 этап 2: что уже посчитано — сводка по готовым клеткам TK-643/649/650/652/655/657/660 (`res/cells*.json`, поле `kpi.longest` = самый длинный перехай, считался в тех же прогонах, но в вердиктах не участвовал).
Только чтение готовых JSON (движок не запускался, В-213). Выход: universe.md, universe.json. Запуск на srv: /opt/venv/bin/python tk673_universe.py <каталог выхода>"""
import json
import os
import sys

import numpy as np

SRC = [("TK-643", "/data/p42-tk643/res/cells43-%s.json", "LS"), ("TK-649", "/data/p42-tk649/res/cells49-%s.json", "LS"), ("TK-650", "/data/p42-tk650/res/cells50-%s.json", "LS"),
       ("TK-652", "/data/p42-tk652/res/cells52-%s.json", "LS"), ("TK-655", "/data/p42-tk655/res/cells55-%s.json", "LS"), ("TK-657", "/data/p42-tk657/res/cells57-%s.json", "S"),
       ("TK-660", "/data/p42-tk660/res/cells60-%s.json", "S")]


def num(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    return (("{:+,.%df}" if sign else "{:,.%df}") % nd).format(x).replace(",", " ").replace(".", ",").replace("-", "−")


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    rows, allc, J = [], [], {}
    for tk, pat, sides in SRC:
        for sd in sides:
            p = pat % sd
            if not os.path.exists(p):
                continue
            d = json.load(open(p, encoding="utf-8"))
            b = d["base"]
            cells = {k: v for k, v in d["cells"].items() if isinstance(v, dict) and "sharpe" in v and "kpi" in v}
            # ворота «не пустая клетка»: n ≥ 70 (как п. 7 (а) TK-643)
            ok = {k: v for k, v in cells.items() if v.get("n", 0) >= 70}
            sh_ = np.array([v["sharpe"] for v in ok.values()])
            lg = np.array([v["kpi"]["longest"] for v in ok.values()], float)
            bs, bl = b["sharpe"], b["kpi"]["longest"]
            ks = max(ok, key=lambda k: ok[k]["sharpe"])
            kl = min(ok, key=lambda k: (ok[k]["kpi"]["longest"], -ok[k]["sharpe"]))
            sc = 365 / np.maximum(sh_, 0.3) ** 2
            r = float(np.corrcoef(sc, lg)[0, 1]) if len(ok) > 3 else float("nan")
            row = dict(tk=tk, side=sd, cells=len(cells), cells_n70=len(ok), base_sharpe=bs, base_longest=bl, base_sum=b["sum"], base_n=b["n"], base_maxdd=b["maxdd"],
                       best_sharpe=dict(cell=ks, sharpe=ok[ks]["sharpe"], longest=ok[ks]["kpi"]["longest"], sum=ok[ks]["sum"], n=ok[ks]["n"], maxdd=ok[ks]["maxdd"]),
                       min_longest=dict(cell=kl, sharpe=ok[kl]["sharpe"], longest=ok[kl]["kpi"]["longest"], sum=ok[kl]["sum"], n=ok[kl]["n"], maxdd=ok[kl]["maxdd"]),
                       share_le10=float((lg <= 10).mean()) if len(lg) else float("nan"), share_shorter_than_base=float((lg < bl).mean()) if len(lg) else float("nan"), corr_longest_scale=r,
                       longest_min=float(lg.min()), longest_max=float(lg.max()), sharpe_max=float(sh_.max()))
            rows.append(row)
            for k, v in ok.items():
                allc.append((tk, sd, k, v["n"], v["sum"], v["sharpe"], v["maxdd"], v["kpi"]["longest"]))
            J["%s-%s" % (tk, sd)] = row
    md = ["# Сводка готовых клеток по перехаю (TK-673 этап 2; `kpi.longest` из `res/cells*.json`, клетки с n ≥ 70)\n",
          "`kpi.longest` — самый длинный перехай в сутках, как его считали исполнители TK-643…660 (на 1 сутки может отличаться от определения TK-673: у базы S 20 против 21); в вердиктах не участвовал. «Лучший Шарп» и «самый короткий перехай» — выбор максимума по клеткам на тех же данных (**найдено на этих данных**). r — корреляция перехая с масштабом 365 ÷ Шарп² по клеткам.\n",
          "| источник | сторона | клеток (n ≥ 70) | база: Шарп · перехай · Σ $ · просадка $ | лучший Шарп: клетка · Шарп · перехай · Σ $ | самый короткий перехай: клетка · перехай · Шарп · Σ $ | клеток с перехаем ≤ 10 | короче базы | r |", "|---|---|---:|---|---|---|---:|---:|---:|"]
    for r in rows:
        bsr, mlr = r["best_sharpe"], r["min_longest"]
        md.append("| %s | %s | %d (%d) | %s · %d · %s · %s | %s · %s · %d · %s | %s · %d · %s · %s | %s | %s | %s |" % (
            r["tk"], r["side"], r["cells"], r["cells_n70"], num(r["base_sharpe"]), r["base_longest"], num(r["base_sum"], 0, True), num(r["base_maxdd"], 0),
            bsr["cell"], num(bsr["sharpe"]), bsr["longest"], num(bsr["sum"], 0, True), mlr["cell"], mlr["longest"], num(mlr["sharpe"]), num(mlr["sum"], 0, True),
            "%d %%" % round(r["share_le10"] * 100), "%d %%" % round(r["share_shorter_than_base"] * 100), num(r["corr_longest_scale"], 2)))
    a = np.array([[c[5], c[7]] for c in allc], float)
    md += ["", "Все клетки вместе (%d): Шарп максимум %s, перехай минимум %d, клеток с перехаем ≤ 10 — %d, ≤ 15 — %d, ≤ 20 — %d." % (len(allc), num(a[:, 0].max()), int(a[:, 1].min()), int((a[:, 1] <= 10).sum()), int((a[:, 1] <= 15).sum()), int((a[:, 1] <= 20).sum()))]
    best = sorted(allc, key=lambda c: (c[7], -c[5]))[:12]
    md += ["", "12 клеток с самым коротким перехаем (клетки n ≥ 70; найдено на этих данных):", "", "| источник | сторона | клетка | сделок | Σ $ | Шарп | просадка $ | перехай |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for c in best:
        md.append("| %s | %s | %s | %d | %s | %s | %s | %d |" % (c[0], c[1], c[2], c[3], num(c[4], 0, True), num(c[5]), num(c[6], 0), c[7]))
    open(os.path.join(out, "universe.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    json.dump(dict(rows=J, n_cells=len(allc)), open(os.path.join(out, "universe.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print("\n".join(md))


if __name__ == "__main__":
    main()
