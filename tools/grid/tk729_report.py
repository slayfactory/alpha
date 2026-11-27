"""TK-729: сборка отчёта docs/findings/grid-minutes-2026-11-28.md из result.json (выход `tk729_an.py final`).

    python tk729_report.py result.json out.md
"""
from __future__ import annotations

import json
import sys

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov"]
MNAME = dict(jan="янв (с 21.01)", feb="фев", mar="мар", apr="апр", may="май", jun="июн", jul="июл", aug="авг", sep="сен", oct="окт", nov="ноя (по 24.11)")
HNAME = {"H1": "21.01–30.06", "H2": "01.07–24.11"}


def f(x, nd=0):
    return "—" if x is None or x != x else "{:,.{nd}f}".format(x, nd=nd).replace(",", " ")


def row(m, name, half):
    b7, b21 = m["boot"]["7"], m["boot"]["21"]
    return "| %s | %s | %s | %s | %s | %s | %s | %s | %s (%s %%) | %s / %s / %s | %d из %d | %d из %d | %s / %s | [%s; %s] / [%s; %s] |" % (
        name, HNAME[half], f(m["usd"]), f(m["cycles"]), f(m["usd_per_cycle"], 3), f(m["usd_per_coin_day"], 3), f(m["sharpe"], 2), f(m["margin"]), f(m["dd"]), f(m["dd_pct"], 1),
        f(m["hw_max"]), f(m["hw_mean"], 1), f(m["hw_p90"]), m["months_plus"], m["months_n"], m["coins_plus"], m["coins_active"], f(m["best10"]), f(m["worst10"]),
        f(b7["lo"], 1), f(b7["hi"], 1), f(b21["lo"], 1), f(b21["hi"], 1))


HEAD = ("| вариант | половина | Σ $ | кругов | $ на круг | $ на монету-сутки | Шарп | маржа $ | просадка $ (% маржи) | перехай, сутки (макс / ср / p90) | месяцев в плюсе | монет в плюсе | "
        "10 лучших / 10 худших, Σ $ | 95 % интервал $ в сутки, блок 7 / блок 21 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")


def main(src, dst):
    R = json.load(open(src, encoding="utf-8"))
    M = R["metrics"]
    g = lambda n, h: M["%s|%s" % (n, h)]
    L_ = []
    A = L_.append
    A("# Грид на минутках c539, 01.01–24.11.2026: прикидка (TK-729)\n")
    A("Описательная прикидка, не отбор. Все числа — «найдено на этих данных». Протокол — летопись P-TK729 §1–§3 (PS-1061…1063), уточнения — L-483. Торгового решения из отчёта нет; даже при «плюс есть» дальше нужен протокол на тиках.\n")
    A("## Ответ\n")
    A("**Вердикт по правилу: «%s».**\n" % R["verdict"])
    for d in R["dirs"]:
        t = g(d["name"], d["test_half"])
        c0 = g("C0", d["test_half"])
        A("- Выбор на %s → проверка на %s: %s; Σ %s $, Шарп %s, месяцев в плюсе %d из %d; деревянная сетка C0 на той же половине: Σ %s $, Шарп %s, месяцев в плюсе %d из %d." % (
            HNAME[d["sel_half"]], HNAME[d["test_half"]], d["name"], f(t["usd"]), f(t["sharpe"], 2), t["months_plus"], t["months_n"], f(c0["usd"]), f(c0["sharpe"], 2), c0["months_plus"], c0["months_n"]))
    A("")
    A("Условия «плюс есть» (проверочная половина, оба направления):\n")
    A("| направление | нижняя граница Σ в сутки > 0 (блок 7 / 21) | месяцев в плюсе ≥ ⅔ | без 10 лучших монет Σ > 0 | MR лучше C0 (нижняя граница парной разницы > 0, блок 7 / 21) |\n|---|---|---|---|---|")
    yn = lambda b: "да" if b else "нет"
    for d in R["dirs"]:
        c, t = d["checks"], g(d["name"], d["test_half"])
        A("| %s → %s | %s / %s (интервалы [%s; %s] / [%s; %s]) | %s (%d из %d, нужно %d) | %s (Σ без 10 лучших %s $) | %s / %s (разница %s $/сутки, [%s; %s] / [%s; %s]) |" % (
            HNAME[d["sel_half"]], HNAME[d["test_half"]], yn(c["lo_gt0_7"]), yn(c["lo_gt0_21"]), f(t["boot"]["7"]["lo"], 1), f(t["boot"]["7"]["hi"], 1), f(t["boot"]["21"]["lo"], 1), f(t["boot"]["21"]["hi"], 1),
            yn(c["months_ge_2_3"]), t["months_plus"], t["months_n"], c["months_need"], yn(c["ex_best10_gt0"]), f(t["ex_best10"]), yn(c["vs_c0_lo_gt0_7"]), yn(c["vs_c0_lo_gt0_21"]),
            f(d["vs_c0"]["7"]["mean"], 1), f(d["vs_c0"]["7"]["lo"], 1), f(d["vs_c0"]["7"]["hi"], 1), f(d["vs_c0"]["21"]["lo"], 1), f(d["vs_c0"]["21"]["hi"], 1)))
    A("")
    A("## Метрики (В-169: Σ $, Шарп, просадка и перехай — рядом)\n")
    A(HEAD)
    names = ["C0"] + [k for k in sorted({x.split("|")[0] for x in M}) if k.startswith("MR_")]
    for n in names:
        for h in ("H1", "H2"):
            A(row(g(n, h), n, h))
    A("\nШарп — годовой из суточного Σ по портфелю (√365). Суточный Σ — с оценкой открытого запаса по последнему закрытию суток. Просадка — по накопленному суточному Σ. Перехай — сутки между новыми максимумами накопленного Σ.\n")
    A("Выбор по Шарпу на выборочной половине (годовой): " + "; ".join("%s → %s: %s (шумовой максимум Шарпа из 6 вариантов ≈ %s, из 17 ≈ %s)" % (HNAME[d["sel_half"]], d["name"], f(d["sel_sharpe"], 2), f(d["noise_bar_sharpe"], 2), f(d["noise_bar_sharpe_17"], 2)) for d in R["dirs"]) + ".\n")
    A("## Откуда Σ: круги против выходов\n")
    A("| вариант | половина | Σ кругов $ | Σ выходов (край + время) $ | прочее $ | выходов за край | выходов по времени | входов | из них как тейкер (ордер выше рынка) |\n|---|---|---|---|---|---|---|---|---|")
    for n in names:
        for h in ("H1", "H2"):
            m = g(n, h)
            A("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (n, HNAME[h], f(m["cyc_usd"]), f(m["exit_usd"]), f(m["other_usd"]), f(m["n_edge"]), f(m["n_time"]), f(m["opens"]), f(m["n_mkt"])))
    A("\n«Прочее» — незакрытый запас на концах половин и комиссии входов открытых лотов.\n")
    A("## По месяцам (Σ $)\n")
    sel_names = sorted({d["name"] for d in R["dirs"]})
    A("| вариант | " + " | ".join(MNAME[m] for m in MONTHS) + " |\n|---|" + "---|" * len(MONTHS))
    for n in ["C0"] + sel_names:
        cells = []
        for mo in MONTHS:
            v = None
            for h in ("H1", "H2"):
                mm = g(n, h)["months"]
                if mo in mm:
                    v = mm[mo]
            cells.append(f(v))
        A("| %s | %s |" % (n, " | ".join(cells)))
    A("")
    A("## Разрезы выбранного MR и C0 (Σ $ по монетам; монет / из них в плюсе)\n")
    for kind, key in (("волатильность (NATR30, среднее по монете)", "terc_natr"), ("оборот (средний суточный)", "terc_turn")):
        A("**%s**\n" % kind)
        A("| вариант | половина | низ | середина | верх |\n|---|---|---|---|---|")
        for n in ["C0"] + sel_names:
            for h in ("H1", "H2"):
                tt = g(n, h)[key]
                A("| %s | %s | %s |" % (n, HNAME[h], " | ".join("%s (%d / %d)" % (f(x["usd"]), x["coins"], x["plus"]) for x in tt)))
        A("")
    A("## Абляции (при выбранных (c, HLmax); Δ к полному MR на проверочной половине)\n")
    A("| направление | абляция | Σ $ | Шарп | 95 % интервал Σ в сутки $, блок 7 / 21 | месяцев в плюсе | Δ Σ к полному $ | Δ в сутки $, интервал блок 7 | интервал блок 21 |\n|---|---|---|---|---|---|---|---|---|")
    texts = {"ABa": "центр = среднее AR(1) вместо Калмана", "ABb": "без наклона по запасу", "ABc": "объёмы растут к краю", "ABd": "без условия п. 1 (все монеты)", "ABe": "без фильтров шока и датчика срыва"}
    for d in R["dirs"]:
        for ab, a in d["ablations"].items():
            am = g(a["name"], d["test_half"])
            A("| %s → %s | (%s) %s | %s | %s | [%s; %s] / [%s; %s] | %d из %d | %s | [%s; %s] | [%s; %s] |" % (
                HNAME[d["sel_half"]], HNAME[d["test_half"]], ab[2:], texts[ab], f(a["usd"]), f(a["sharpe"], 2), f(am["boot"]["7"]["lo"], 1), f(am["boot"]["7"]["hi"], 1), f(am["boot"]["21"]["lo"], 1),
                f(am["boot"]["21"]["hi"], 1), am["months_plus"], am["months_n"], f(a["delta_usd"]), f(a["delta_boot"]["7"]["lo"], 2), f(a["delta_boot"]["7"]["hi"], 2), f(a["delta_boot"]["21"]["lo"], 2), f(a["delta_boot"]["21"]["hi"], 2)))
    A("")
    open(dst, "w", encoding="utf-8").write("\n".join(L_) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
