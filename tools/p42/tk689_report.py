#!/usr/bin/env python3
"""TK-689: отчёт и графики по результатам `tk689_influence.py compute` (каталоги уровней). Таблицы, раздел NATR первым, графики PNG (один акцентный цвет).
    python3 tk689_report.py --inp DIR[,DIR…] --features ml_features.json --md OUT.md --tables TABLES_DIR --plots PLOTS_DIR [--only natr]"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk689_influence as ti  # noqa: E402

ACCENT, GREY, DARK = "#1f6feb", "#9aa0a6", "#3c4043"
SIDES = (("long", "Лонг"), ("short", "Шорт"))
NATR_AXES = ("natr_min", "natr_p", "nf", "fg", "xb", "ev", "ls", "sl", "sn", "nx", "chan", "vf", "vs", "va", "vb", "vp", "v0", "vm", "vz", "vr",
             "t_low", "t_high", "a", "rr", "natr_exit_p", "ns", "F:natr_bp", "F:VOL")
LEAD = {name: sorted(leads)[0] for name, leads, _ in ti.RULES}
LEAD["split"] = "e2t"
LEAD["chan"] = "ch"
LEAD["sn"] = "sn"
LEAD["ev"] = "ev"
MET = (("usd", "Σ $", "%.0f"), ("mean", "$ на сделку", "%.2f"), ("n", "сделок", "%.0f"), ("sh", "Шарп", "%.2f"), ("dd", "просадка $", "%.0f"), ("gap", "перехай, сут.", "%.0f"))


def load(dirs):
    res, obs, meta = {}, {}, {}
    for d in dirs:
        for f in glob.glob(os.path.join(d, "level-*-*.json")):
            _, side, k = os.path.basename(f)[:-5].split("-")
            res[(side, int(k))] = json.load(open(f, encoding="utf-8"))
            obs[(side, int(k))] = json.load(open(os.path.join(d, "obs-%s-%s.json" % (side, k)), encoding="utf-8"))
        if os.path.exists(os.path.join(d, "meta.json")):
            meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
    return res, obs, meta


def feat_numbers(path):
    d = json.load(open(path, encoding="utf-8"))
    return {f["name"]: f["n"] for s in d["sets"] for g in s["groups"] for f in g["features"]}


def axis_no(axis, F):
    if axis.startswith("F:"):
        return F.get(axis[2:])
    return F.get(axis) or F.get(LEAD.get(axis, axis))


def ax_name(axis):
    return axis[2:] if axis.startswith("F:") else axis


def f(x, fmt="%.1f", sign=False):
    if x is None:
        return "—"
    s = fmt % x
    return ("+" + s) if sign and x > 0 else s


def rng(d, fmt="%.1f"):
    if not d:
        return "—"
    return "%s [%s … %s]" % (f(d["mean"], fmt, True), f(d["min"], fmt), f(d["max"], fmt))


def pct(x):
    return "—" if x is None else "%.0f %%" % (100 * x)


def flags(a):
    out = []
    if a.get("dead"):
        out.append("мёртвый")
    if a.get("sign_stable") is False:
        out.append("знак меняется")
    if a.get("months_stable") is False:
        out.append("мес. %d/5" % a.get("months_same_sign", 0))
    if a.get("above_noise_sh"):
        out.append("Δ Шарпа > шума")
    if a.get("above_noise_usd"):
        out.append("Δ $ > шума")
    if a.get("not_distinguishable_from_random"):
        out.append("как случайное выкидывание")
    elif a.get("null_applicable") is False:
        out.append("нул. модель н/п")
    return ", ".join(out)


HDR = "| Ось | № | n (сырых) | Δ Σ $ ср. [мин … макс] | Δ $/сд. | Δ сделок | Δ Шарп | Δ просадка $ ср. [мин … макс] | Δ перехай | rP | rD | группа П / У / итог | знак + | метки |"
SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def row(a, F):
    g = "%s / %s / **%s**" % (a["gP"], a["gD"], a["group"])
    return "| %s | %s | %d (%d) | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        ax_name(a["axis"]) + ("" if a["grid"] == "полная" else " ·отбор"), axis_no(a["axis"], F) or "—", a["n"], a["n_raw"], rng(a["d_usd"]),
        f((a["d_mean"] or {}).get("mean"), "%.2f", True), f((a["d_n"] or {}).get("mean"), "%.0f", True), f((a["d_sh"] or {}).get("mean"), "%.2f", True),
        rng(a["d_dd"]), f((a["d_gap"] or {}).get("mean"), "%.1f", True), pct(a["rP"]), pct(a["rD"]), g, pct(a.get("share_pos")), flags(a))


def level_table(r, F, only=None, order="r"):
    ax = [a for a in r["axes"] if (only is None or a["axis"] in only)]
    ax.sort(key=lambda a: -(a["r"] if a["r"] is not None else -1))
    return [HDR, SEP] + [row(a, F) for a in ax]


def anchor_line(meta, side):
    a = meta["anchors"][side]
    return "якорь: %d сд., Σ %+.0f $, Шарп %.2f, просадка %.0f $, перехай %s сут." % (a["n"], a["usd"], a["sh"], a["dd"], a["gap"])


# ---- графики -----------------------------------------------------------------------------------------------------------------------
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": DARK, "axes.labelcolor": DARK,
                         "xtick.color": DARK, "ytick.color": DARK, "axes.titlesize": 9, "figure.dpi": 110})
    return plt


def order_labels(labs):
    return sorted(set(labs), key=lambda s: (ti.label_num(s), s))


def plot_level1(side, axis, obs, meta, res, path):
    plt = _plt()
    A = meta["anchors"][side]
    mine = [o for o in obs if o["axis"] == axis and o["grid"] == "полная"]
    if not mine:
        return None
    labs = order_labels(o["label"] for o in mine)
    fig, axs = plt.subplots(2, 3, figsize=(9, 4.6))
    noise = {"usd": res.get("noise_usd"), "sh": res.get("noise_sh")}
    anc = {"usd": A["usd"], "mean": A["usd"] / A["n"], "n": A["n"], "sh": A["sh"], "dd": A["dd"], "gap": A["gap"]}
    for ax_, (m, name, fmt) in zip(axs.flat, MET):
        xs = {lab: [o["c_" + m] for o in mine if o["label"] == lab and o["c_" + m] is not None] for lab in labs}
        x = np.arange(len(labs))
        y = [np.mean(xs[lab]) if xs[lab] else np.nan for lab in labs]
        ax_.plot(x, y, "-o", color=ACCENT, ms=3.5, lw=1.4)
        ax_.axhline(anc[m], color=DARK, lw=0.9, ls="--")
        if noise.get(m):
            ax_.axhspan(anc[m] - noise[m], anc[m] + noise[m], color=GREY, alpha=0.18, lw=0)
        ax_.set_xticks(x)
        ax_.set_xticklabels(labs, rotation=60 if len(labs) > 5 else 0, ha="right" if len(labs) > 5 else "center")
        ax_.set_title(name)
        lo, hi = np.nanmin(y + [anc[m]]), np.nanmax(y + [anc[m]])
        pad = (hi - lo) * 0.15 or 1
        ax_.set_ylim(lo - pad, hi + pad)
    fig.suptitle("%s · %s · соло" % (ax_name(axis), "лонг" if side == "long" else "шорт"), fontsize=9, color=DARK)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_level_k(side, axis, k, obs, path, metric="usd", maxp=24):
    """Уровни ≥ 2: эффект значения оси (Δ против опоры) для каждого партнёра — среднее по значениям партнёра и полоса min … max."""
    plt = _plt()
    mine = [o for o in obs if o["axis"] == axis and not o["dup"] and o["grid"] == "полная" and o["others"] and o["d_" + metric] is not None]
    if not mine:
        return None
    by = defaultdict(list)
    for o in mine:
        by["+".join(sorted(ax_name(x) for x in o["others"]))].append(o)
    partners = sorted(by, key=lambda p: -len(by[p]))[:maxp]
    labs = order_labels(o["label"] for o in mine)
    cols = 4
    rows = -(-len(partners) // cols)
    fig, axs = plt.subplots(rows, cols, figsize=(2.6 * cols, 1.9 * rows), squeeze=False)
    allv = [o["d_" + metric] for p in partners for o in by[p]]
    lo, hi = min(allv + [0]), max(allv + [0])
    pad = (hi - lo) * 0.08 or 1
    for i, p in enumerate(partners):
        ax_ = axs[i // cols][i % cols]
        mu, mn, mx = [], [], []
        for lab in labs:
            v = [o["d_" + metric] for o in by[p] if o["label"] == lab]
            mu.append(np.mean(v) if v else np.nan)
            mn.append(np.min(v) if v else np.nan)
            mx.append(np.max(v) if v else np.nan)
        x = np.arange(len(labs))
        ax_.fill_between(x, mn, mx, color=ACCENT, alpha=0.18, lw=0)
        ax_.plot(x, mu, "-o", color=ACCENT, ms=2.5, lw=1.2)
        ax_.axhline(0, color=DARK, lw=0.7, ls="--")
        ax_.set_ylim(lo - pad, hi + pad)
        ax_.set_title(p, fontsize=7)
        ax_.set_xticks(x)
        ax_.set_xticklabels(labs if i // cols == rows - 1 else [], rotation=60, ha="right", fontsize=6)
    for j in range(len(partners), rows * cols):
        axs[j // cols][j % cols].axis("off")
    fig.suptitle("%s · %s · уровень %d · Δ %s" % (ax_name(axis), "лонг" if side == "long" else "шорт", k, "Σ $" if metric == "usd" else "просадки $"), fontsize=9, color=DARK)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def value_table(side, axis, obs, meta):
    """Уровень 1: значение оси → число сделок, Σ $, $/сд., Шарп, просадка, перехай, R:R (по строкам БД), с якорем."""
    mine = [o for o in obs if o["axis"] == axis and o["grid"] == "полная"]
    A = meta["anchors"][side]
    L = ["| значение | сделок | Σ $ | $/сд. | Шарп | просадка $ | перехай | R:R |", "|---|---|---|---|---|---|---|---|",
         "| якорь | %d | %+.0f | %.2f | %.2f | %.0f | %s | — |" % (A["n"], A["usd"], A["usd"] / A["n"], A["sh"], A["dd"], A["gap"])]
    for lab in order_labels(o["label"] for o in mine):
        o = [x for x in mine if x["label"] == lab][0]
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (lab, f(o["c_n"], "%.0f"), f(o["c_usd"], "%.0f", True), f(o["c_mean"], "%.2f"), f(o["c_sh"], "%.2f"),
                                                           f(o["c_dd"], "%.0f"), f(o["c_gap"], "%.0f"), f(o["c_rr"], "%.2f")))
    return L


# ---- отчёт -------------------------------------------------------------------------------------------------------------------------
def group_counts(res):
    out = defaultdict(lambda: defaultdict(int))
    for (side, k), r in res.items():
        for a in r["axes"]:
            out[(side, k, a["grid"])][a["group"]] += 1
            if a.get("dead"):
                out[(side, k, a["grid"])]["мёртвых"] += 1
    return out


def natr_section(res, obs, meta, F, plots_dir, rel_plots, make_plots=True):
    L = ["## 1. NATR-оси (первым: «natr везде влияет на прибыль или убыток?»)", "",
         "Состав оси NATR задан до просмотра (карточка TK-689). Одна шкала со всеми остальными осями (раздел 2). Описательно, найдено на этих данных.", ""]
    levels = sorted({k for (_, k) in res})
    for side, sname in SIDES:
        for k in levels:
            r = res.get((side, k))
            if not r:
                continue
            axs = [a for a in r["axes"] if a["axis"] in NATR_AXES]
            if not axs:
                continue
            L += ["### %s, уровень %d (%s; шум Шарпа ±%.2f, Σ $ ±%.0f $; σ клеток уровня %.2f / %.0f $, m = %d)" % (
                sname, k, anchor_line(meta, side), r["noise_sh"] or 0, r["noise_usd"] or 0, r["sigma_sh"] or 0, r["sigma_usd"] or 0, r["m"]), ""]
            L += level_table(r, F, set(NATR_AXES))
            L.append("")
            if k > 1:
                L.append("Клетки уровня %d: полная сетка %d, отбор по результату %d. %s" % (
                    k, r["by_grid"]["полная"], r["by_grid"]["отбор"], "" if r["by_grid"]["отбор"] == 0 else "Оси отобраны по результату на этих данных; эффект — условный."))
                L.append("")
    if make_plots:
        os.makedirs(plots_dir, exist_ok=True)
        L += ["### Графики", ""]
        for side, sname in SIDES:
            for k in levels:
                o = obs.get((side, k), [])
                r = res.get((side, k))
                names = sorted({x["axis"] for x in o if x["axis"] in NATR_AXES})
                for axis in names:
                    base = os.path.join(plots_dir, "%s-L%d-%s" % (side, k, ax_name(axis).replace("/", "_")))
                    if k == 1:
                        p = plot_level1(side, axis, o, meta, r, base + ".png")
                        if p:
                            L.append("- %s · соло · %s: [%s](%s)" % (sname, ax_name(axis), os.path.basename(p), rel_plots + "/" + os.path.basename(p)))
                    else:
                        for metric, suf in (("usd", "usd"), ("dd", "dd")):
                            p = plot_level_k(side, axis, k, o, base + "-" + suf + ".png", metric)
                            if p:
                                L.append("- %s · уровень %d · %s · Δ %s: [%s](%s)" % (sname, k, ax_name(axis), "Σ $" if metric == "usd" else "просадки", os.path.basename(p),
                                                                                     rel_plots + "/" + os.path.basename(p)))
        L.append("")
        L += ["### Значения осей NATR на уровне 1 (все остальные ключи — у якоря)", ""]
        for side, sname in SIDES:
            o = obs.get((side, 1), [])
            for axis in sorted({x["axis"] for x in o if x["axis"] in NATR_AXES}):
                L += ["**%s · %s (№ %s)**" % (sname, ax_name(axis), axis_no(axis, F) or "—"), ""] + value_table(side, axis, o, meta) + [""]
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", required=True)
    ap.add_argument("--features", required=True)
    ap.add_argument("--md", required=True)
    ap.add_argument("--tables", required=True)
    ap.add_argument("--plots", required=True)
    ap.add_argument("--rel-plots", default="p42-axis-influence-2026-11-23")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    res, obs, meta = load(a.inp.split(","))
    F = feat_numbers(a.features)
    L = natr_section(res, obs, meta, F, a.plots, a.rel_plots)
    if a.only == "natr":
        open(a.md, "w", encoding="utf-8").write("\n".join(L) + "\n")
        return
    open(a.md, "w", encoding="utf-8").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
