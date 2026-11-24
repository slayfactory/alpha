import json, sys
BASE = "docs/findings/p42-titration-coverage-2026-11-22/c539/"
def f(x, d=0):
    s = ("%+.*f" % (d, x)).replace(".", ",").replace("-", "−") if False else ("%+.*f" % (d, x)).replace(".", ",")
    return s
def g(c):
    gd = c["guards"]
    return ("да" if all(gd[x] for x in "abcd") else "нет: " + "".join(x for x in "abcd" if not gd[x])) + (", плато" if gd["g"] else "")
def num(x):
    s = ("%g" % x).replace(".", ",")
    return s
def row(setn, ax, c):
    thr = c.get("thr")
    lab = "%s (%s)" % (c["label"], num(thr)) if thr is not None else c["label"]
    return (thr if thr is not None else 0, "| %s | `%s` | %s | %d | %s | %s | %s | %s (%s %%) | %d / %.1f / %d | %s |" % (
        setn, ax, lab, c["n"], f(c["usd"]), f(c["mean_usd"], 2), ("%.2f" % c["sharpe"]).replace(".", ","), "%d" % c["maxdd"], ("%.1f" % c["maxdd_pct"]).replace(".", ","),
        c["gap_longest"], c["gap_mean"], c["tail"], g(c)))
out = []
names = {"L": "Лонг", "S": "Шорт"}
for side in "LS":
    new = json.load(open(BASE + "t701/map674b1-%s.json" % side, encoding="utf-8"))
    old = json.load(open(BASE + "main/map674b1-%s.json" % side, encoding="utf-8"))
    b = new["base"]; bk = b["kpi"]
    out.append("**%s** (якорь: %d сд., %s $, %s $/сд., Sharpe %s, просадка %d $ (%s %%), перехай %d / %.1f / %d, R:R см. вердикт):\n" % (names[side], b["n"], f(b["usd"]), f(b["mean_usd"], 2), ("%.2f" % b["sharpe"]).replace(".", ","), b["maxdd"], ("%.1f" % b["maxdd_pct"]).replace(".", ","), bk["gap_longest"], bk["gap_mean"], bk["gap_tail"]))
    out.append("| Набор | Ось | Порог | Сд. | Σ $ | $/сд. | Sharpe | Просадка $ (% маржи) | Перехай: макс. / ср. / хвост, сут | Охраны |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for col in ("tfv60", "pre_n5", "oi_c1"):
        for d in ("le", "ge"):
            ax = "%s.%s" % (col, d)
            rows = []
            for setn, mm in (("t701 (c539)", new), ("main TK-680 (22.11)", old)):
                for a in mm["axes"]:
                    if a["axis"] == ax:
                        for c in a["cells"]:
                            if not c.get("is_base"):
                                rows.append(row(setn, ax, c))
            for _, r in sorted(rows, key=lambda t: t[0]):
                out.append(r)
    out.append("")
sys.stdout.write("\n".join(out))
