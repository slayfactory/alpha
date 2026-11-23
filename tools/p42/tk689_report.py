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


def _ext_v(d):
    """Наибольшее по модулю Δ среди клеток оси (со знаком)."""
    return d["min"] if abs(d["min"]) >= abs(d["max"]) else d["max"]


def _ext(d, fmt):
    return "—" if not d else f(_ext_v(d), fmt, True)


def flags(a):
    out = []
    if a.get("dead"):
        out.append("мёртвый")
    if a.get("sign_stable") is False:
        out.append("знак меняется")
    if a.get("months_stable") is False:
        out.append("мес. %d/5" % a.get("months_same_sign", 0))
    if a.get("above_noise_sh"):
        out.append("наибольшее по модулю Δ Шарпа %s > шума (%s)" % (_ext(a.get("d_sh"), "%.2f"), "ухудшение" if _ext_v(a.get("d_sh")) < 0 else "улучшение"))
    if a.get("above_noise_usd"):
        out.append("наибольшее по модулю Δ Σ $ %s > шума (%s)" % (_ext(a.get("d_usd"), "%.0f"), "ухудшение" if _ext_v(a.get("d_usd")) < 0 else "улучшение"))
    if a.get("not_distinguishable_from_random"):
        out.append("как случайное выкидывание")
    elif a.get("null_applicable") is False:
        out.append("нул. модель н/п")
    return ", ".join(out)


HDR = "| Ось | № | n (сырых) | Δ Σ $ ср. [мин … макс] | Δ $/сд. | Δ сделок | Δ Шарп | Δ просадка $ ср. [мин … макс] | Δ просадка п.п. маржи | Δ перехай | rP | rD | группа П / У / итог | знак + | метки |"
SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def row(a, F):
    g = "%s / %s / **%s**" % (a["gP"], a["gD"], a["group"])
    return "| %s | %s | %d (%d) | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        ax_name(a["axis"]) + ("" if a["grid"] == "полная" else " ·отбор"), axis_no(a["axis"], F) or "—", a["n"], a["n_raw"], rng(a["d_usd"]),
        f((a["d_mean"] or {}).get("mean"), "%.2f", True), f((a["d_n"] or {}).get("mean"), "%.0f", True), f((a["d_sh"] or {}).get("mean"), "%.2f", True),
        rng(a["d_dd"]), f((a["d_ddp"] or {}).get("mean"), "%.1f", True), f((a["d_gap"] or {}).get("mean"), "%.1f", True), pct(a["rP"]), pct(a["rD"]), g, pct(a.get("share_pos")), flags(a))


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


def anchor_label(axis, meta, side):
    """Значение оси у якоря (точка якоря на оси x): только для осей из одного ключа; составные оси и фильтры «F:» — без точки."""
    if axis.startswith("F:") or axis in {n for n, _, _ in ti.RULES} or axis == "split":
        return None
    keys, _ = ti.parse_ckey(meta["anchors"][side]["ckey"])
    return ti.eff(keys, axis)


def axis_title(axis, side, F, extra):
    no = axis_no(axis, F)
    return "%s%s · %s · %s" % (ax_name(axis), " (№ %s)" % no if no else "", "лонг" if side == "long" else "шорт", extra)


def plot_level1(side, axis, obs, meta, res, path, F=None):
    plt = _plt()
    A = meta["anchors"][side]
    mine = [o for o in obs if o["axis"] == axis and o["grid"] == "полная"]
    if not mine:
        return None
    al = anchor_label(axis, meta, side)
    labs = order_labels([o["label"] for o in mine] + ([al] if al is not None else []))
    fig, axs = plt.subplots(2, 3, figsize=(9, 4.6))
    noise = {"usd": res.get("noise_usd"), "sh": res.get("noise_sh")}
    anc = {"usd": A["usd"], "mean": A["usd"] / A["n"], "n": A["n"], "sh": A["sh"], "dd": A["dd"], "gap": A["gap"]}
    for ax_, (m, name, fmt) in zip(axs.flat, MET):
        xs = {lab: [o["c_" + m] for o in mine if o["label"] == lab and o["c_" + m] is not None] for lab in labs}
        x = np.arange(len(labs))
        y = [anc[m] if lab == al else (np.mean(xs[lab]) if xs[lab] else np.nan) for lab in labs]
        ax_.axhline(anc[m], color=DARK, lw=0.9, ls="--")
        if noise.get(m):
            ax_.axhspan(anc[m] - noise[m], anc[m] + noise[m], color=GREY, alpha=0.18, lw=0)
        ax_.plot(x, y, "-o", color=ACCENT, ms=3.5, lw=1.4)
        if al is not None:
            ia = labs.index(al)
            ax_.plot([ia], [anc[m]], "D", color=DARK, ms=6, zorder=5)
        ax_.set_xticks(x)
        ax_.set_xticklabels(labs, rotation=60 if len(labs) > 5 else 0, ha="right" if len(labs) > 5 else "center")
        ax_.set_title(name)
        lo, hi = np.nanmin(y + [anc[m]]), np.nanmax(y + [anc[m]])
        pad = (hi - lo) * 0.15 or 1
        ax_.set_ylim(lo - pad, hi + pad)
    fig.suptitle(axis_title(axis, side, F or {}, "соло; пунктир — якорь" + (", ромб — значение якоря" if al is not None else "") + "; серая полоса — шум"), fontsize=9, color=DARK)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_level_k(side, axis, k, obs, path, metric="usd", maxp=24, F=None, meta=None):
    """Уровни ≥ 2: эффект значения оси (Δ против опоры) для каждого партнёра — среднее по значениям партнёра и полоса min … max."""
    plt = _plt()
    mine = [o for o in obs if o["axis"] == axis and not o["dup"] and o["grid"] == "полная" and o["others"] and o["d_" + metric] is not None]
    if not mine:
        return None
    by = defaultdict(list)
    for o in mine:
        by["+".join(sorted(ax_name(x) for x in o["others"]))].append(o)
    partners = sorted(by, key=lambda p: -len(by[p]))[:maxp]
    al = anchor_label(axis, meta, side) if meta else None
    labs = order_labels([o["label"] for o in mine] + ([al] if al is not None else []))
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
            v = [o["d_" + metric] for o in by[p] if o["label"] == lab] if lab != al else [0.0]
            mu.append(np.mean(v) if v else np.nan)
            mn.append(np.min(v) if v else np.nan)
            mx.append(np.max(v) if v else np.nan)
        x = np.arange(len(labs))
        ax_.fill_between(x, mn, mx, color=ACCENT, alpha=0.18, lw=0)
        ax_.plot(x, mu, "-o", color=ACCENT, ms=2.5, lw=1.2)
        ax_.axhline(0, color=DARK, lw=0.7, ls="--")
        if al is not None:
            ax_.plot([labs.index(al)], [0], "D", color=DARK, ms=4, zorder=5)
        ax_.set_ylim(lo - pad, hi + pad)
        ax_.set_title(p, fontsize=7)
        ax_.set_xticks(x)
        last = i + cols >= len(partners)
        ax_.set_xticklabels(labs if last else [], rotation=60, ha="right", fontsize=6)
    for j in range(len(partners), rows * cols):
        axs[j // cols][j % cols].axis("off")
    fig.suptitle(axis_title(axis, side, F or {}, "уровень %d · Δ %s против опоры, по партнёрам" % (k, "Σ $" if metric == "usd" else "просадки $") + ("; ромб — значение якоря" if al is not None else "")), fontsize=9, color=DARK)
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
def null_class(a):
    """Что нулевая модель (случайное выкидывание сделок) говорит об оси: «за пределами» / «неотличимо» / «н/п»."""
    if a.get("not_distinguishable_from_random"):
        return "неотличимо от случайного"
    if a.get("null_applicable"):
        return "за пределами нулевой"
    return "нулевая н/п"


def group_counts(res):
    out = defaultdict(lambda: defaultdict(int))
    for (side, k), r in res.items():
        for a in r["axes"]:
            c = out[(side, k, a["grid"])]
            c[a["group"]] += 1
            if a.get("dead"):
                c["мёртвых в «ничего»" if a["group"] == "ничего" else "мёртвых в «не оценена»"] += 1
            if a["group"] == "много":
                c["много: " + null_class(a)] += 1
    return out


def natr_section(res, obs, meta, F, plots_dir, rel_plots, make_plots=True):
    L = ["## 1. NATR-оси (первым: «natr везде влияет на прибыль или убыток?»)", "",
         "Состав оси NATR задан до просмотра (карточка TK-689). Одна шкала со всеми остальными осями (раздел 3). Описательно, найдено на этих данных. "
         "Пометка «наибольшее по модулю Δ > шума» — сравнение величины с σ·√(2 ln m), оценкой наибольшего подъёма от отбора среди m клеток; для ухудшений это только сравнение величины, не вывод о значимости.", ""]
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
    for side, sname in SIDES:
        for k in (3, 4):
            n = meta["levels_in_db"].get(side, {}).get(str(k), 0)
            r = res.get((side, k))
            if n < 20 or not r or not any(x["axis"] in NATR_AXES for x in r["axes"]):
                L.append("- %s, уровень %d (%s): в БД клеток %d — NATR-оси не оценены (карта этого уровня ещё не записана)." % (sname, k, "тройки" if k == 3 else "четвёрки", n))
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
                        p = plot_level1(side, axis, o, meta, r, base + ".png", F)
                        if p:
                            L.append("- %s · соло · %s: [%s](%s)" % (sname, ax_name(axis), os.path.basename(p), rel_plots + "/" + os.path.basename(p)))
                    else:
                        for metric, suf in (("usd", "usd"), ("dd", "dd")):
                            p = plot_level_k(side, axis, k, o, base + "-" + suf + ".png", metric, F=F, meta=meta)
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


def data_section(res, meta):
    L = ["## 2. Данные и что покрыто", "",
         "База: летопись, c539 (539 монет), окно %s, ставка 500 $; якорь — клетка L-334. Клетки по уровням (число осей, отличающихся от якоря), без исключённых:" % meta["win"], "",
         "| сторона | уровень | клеток в БД | посчитано (клеток) | из них полная сетка / отбор |", "|---|---|---|---|---|"]
    for side, sname in SIDES:
        for k, n in sorted(meta["levels_in_db"].get(side, {}).items(), key=lambda x: int(x[0])):
            if int(k) == 0:
                continue
            r = res.get((side, int(k)))
            L.append("| %s | %s | %d | %s | %s |" % (sname, k, n, r["n_cells"] if r else "не считалось", ("%d / %d" % (r["by_grid"]["полная"], r["by_grid"]["отбор"])) if r else "—"))
    ex = meta.get("excluded", {})
    L += ["", "Исключено из эффектов (стоп 5 % — В-173, вход минутной свечой ew = 0 — В-172): " + (", ".join("%s %d" % (k.replace(":", " "), v) for k, v in sorted(ex.items())) or "ничего"),
          "Повторы ключа в БД: %d (с разными числами: %d). Окно счёта — все доступные данные." % (meta["dup_keys"], meta["dup_diff"]), ""]
    return L


def groups_section(res):
    L = ["## 3. Оси по группам влияния", "",
         "Описательно, найдено на этих данных. «Много» значит «клетки оси заметно меняют набор сделок и результат», не «ось работает»: размер r измеряет в основном потерю сделок. "
         "Нулевая модель — случайное выкидывание такого же числа сделок: «за пределами нулевой» — r больше 95-го процентиля случайного выкидывания; «неотличимо» — не больше; «нулевая н/п» — P&L общих сделок клеток не совпадает (оси меняют выходы / вход) или нулевая модель не считалась. "
         "Размер влияния r = max(|Δ Σ $| / |Σ $ якоря|, |Δ просадки| / просадка якоря), среднее по клеткам оси; «много» r ≥ 0,30, «мало» 0,05 ≤ r < 0,30, «ничего» r < 0,05 или клетки не меняют сделок («мёртвая»). Порог выбран до просмотра.", "",
         "| сторона | уровень | сетка | осей | много | из них за пределами нулевой | неотличимо от случайного | нулевая н/п | мало | ничего | из «ничего» мёртвых (клетки не меняют сделок) | не оценена (n < 2) | из «не оценена» мёртвых |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    gc = group_counts(res)
    for side, sname in SIDES:
        for k in sorted({k for (s_, k) in res if s_ == side}):
            for g in ("полная", "отбор"):
                c = gc.get((side, k, g))
                if not c:
                    continue
                L.append("| %s | %d | %s | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d |" % (
                    sname, k, g, sum(c[x] for x in ("много", "мало", "ничего", "не оценена")), c["много"], c["много: за пределами нулевой"], c["много: неотличимо от случайного"],
                    c["много: нулевая н/п"], c["мало"], c["ничего"], c["мёртвых в «ничего»"], c["не оценена"], c["мёртвых в «не оценена»"]))
    L.append("")
    return L


def top_section(res, F, n=5):
    L = ["## 4. Топ-5 осей по влиянию на прибыль и на просадку", "",
         "Описательно, найдено на этих данных. Ось и её средний Δ против опоры (Σ $, просадка $); отбор по размеру |Δ| (rP — по прибыли, rD — по просадке). «Отбор» — клетки, отобранные по результату.", ""]
    for side, sname in SIDES:
        for k in sorted({k for (s_, k) in res if s_ == side}):
            for g in ("полная", "отбор"):
                axs = [a for a in res[(side, k)]["axes"] if a["grid"] == g and a["group"] != "не оценена" and not a.get("dead")]
                if not axs:
                    continue
                L += ["**%s · уровень %d · %s**" % (sname, k, g), "", "| по | ось (№) | n | Δ Σ $ ср. | Δ просадка $ ср. | Δ просадка п.п. маржи | Δ Шарп ср. | r (по прибыли / по просадке) | группа |", "|---|---|---|---|---|---|---|---|---|"]
                for key, nm in (("rP", "прибыли"), ("rD", "просадке")):
                    for a in sorted(axs, key=lambda a: -(a[key] or 0))[:n]:
                        L.append("| %s | %s (%s) | %d | %s | %s | %s | %s | %s / %s | %s |" % (nm, ax_name(a["axis"]), axis_no(a["axis"], F) or "—", a["n"], f((a["d_usd"] or {}).get("mean"), "%.0f", True),
                                                                                  f((a["d_dd"] or {}).get("mean"), "%.1f", True), f((a["d_ddp"] or {}).get("mean"), "%.1f", True),
                                                                                  f((a["d_sh"] or {}).get("mean"), "%.2f", True), pct(a["rP"]), pct(a["rD"]), a["group"]))
                L.append("")
    return L


def shift_section(res):
    L = ["## 5. Смена группы оси между уровнями (полная сетка)", "", "Описательно, найдено на этих данных. Строка — группа на уровне k, столбец — группа той же оси на уровне k + 1 (число осей); внизу — оси, сменившие группу.", ""]
    G = ("много", "мало", "ничего")
    for side, sname in SIDES:
        for k in sorted({k for (s_, k) in res if s_ == side}):
            if (side, k + 1) not in res:
                continue
            a = {x["axis"]: x["group"] for x in res[(side, k)]["axes"] if x["grid"] == "полная"}
            b = {x["axis"]: x["group"] for x in res[(side, k + 1)]["axes"] if x["grid"] == "полная"}
            common = sorted(set(a) & set(b))
            L += ["**%s · %d → %d** (общих осей %d)" % (sname, k, k + 1, len(common)), "", "| | " + " | ".join(G) + " |", "|---|---|---|---|"]
            for x in G:
                L.append("| %s | %s |" % (x, " | ".join(str(sum(1 for t in common if a[t] == x and b[t] == y)) for y in G)))
            ch = [(t, a[t], b[t]) for t in common if a[t] != b[t] and a[t] in G and b[t] in G]
            L += ["", "Сменили группу (%d): " % len(ch) + ", ".join("%s %s→%s" % (ax_name(t), x, y) for t, x, y in ch[:40]) + (" …" if len(ch) > 40 else ""), ""]
    return L


def cluster_section(res):
    L = ["## 6. Семьи осей (кластеры по сделкам)", "",
         "Описательно, найдено на этих данных. Близость осей — среднее Жаккара сделок, затронутых клеткой с осью против опоры, по общим опорам и парам значений; семья — среднее связывание (UPGMA), средняя близость ≥ 0,5; "
         "уровни ≥ 2 — не меньше 3 общих опор. В кластеры идут оси «много» / «мало» со средним числом затронутых сделок ≥ 10. Представитель — ось с наибольшим r.", "",
         "| сторона | уровень | сетка | осей в кластеризации | малых | пар с близостью | семей | размеры семей |", "|---|---|---|---|---|---|---|---|"]
    det = []
    for side, sname in SIDES:
        for k in sorted({k for (s_, k) in res if s_ == side}):
            cl = res[(side, k)].get("clusters")
            cl = cl if isinstance(cl, dict) else {}
            rr = {(a["axis"], a["grid"]): a["r"] for a in res[(side, k)]["axes"]}
            for g, c in cl.items():
                if not isinstance(c, dict) or "names" not in c:
                    continue
                L.append("| %s | %d | %s | %d | %d | %d | %d | %s |" % (sname, k, g, len(c["names"]), len(c["small"]), c["pairs"], len(c["families"]),
                                                                       ", ".join(str(len(x)) for x in sorted(c["families"], key=len, reverse=True)) or "—"))
                for fam in sorted(c["families"], key=len, reverse=True):
                    rep = max(fam, key=lambda x: rr.get((x, g)) or 0)
                    det.append("- %s · уровень %d · %s · %d осей · представитель **%s**: %s" % (sname, k, g, len(fam), ax_name(rep), ", ".join(ax_name(x) for x in fam)))
    return L + [""] + det + [""]


def interaction_section(res):
    L = ["## 7. Взаимодействия (чистая интерактивность порядка k)", "",
         "Описательно, найдено на этих данных. Σ (−1)^(k−|T|) · P&L подклеток; интервал — блочный бутстреп по суткам (блок 5, B = 20 000, 95 %). "
         "Доля интервалов целиком > 0 (или < 0) больше 2,5 % — больше, чем ожидалось бы при независимых клетках; клетки делят сутки и сделки, они зависимы — это не проверка. "
         "Клетки «отбор» отобраны по результату — их интервалы смещены по построению, считаются отдельно. "
         "Положительное взаимодействие у убыточной клетки (например lx=3 × natr_min=0.8: +661 $ при Σ клетки −412 $) значит «менее плохо, чем сумма двух осей по отдельности», а не прибыль.", "",
         "| сторона | уровень | сетка | клеток | интервал целиком > 0 | интервал целиком < 0 | ожидание при шуме (2,5 %) |", "|---|---|---|---|---|---|---|"]
    det = []
    for side, sname in SIDES:
        for k in sorted({k for (s_, k) in res if s_ == side and k >= 2}):
            ints = [x for x in res[(side, k)]["interactions"] if x.get("lo") is not None]
            for g in ("полная", "отбор"):
                v = [x for x in ints if x["grid"] == g]
                if not v:
                    continue
                pos, neg = [x for x in v if x["lo"] > 0], [x for x in v if x["hi"] < 0]
                L.append("| %s | %d | %s | %d | %d (%.1f %%) | %d (%.1f %%) | %.0f |" % (sname, k, g, len(v), len(pos), 100 * len(pos) / len(v), len(neg), 100 * len(neg) / len(v), 0.025 * len(v)))
                if g == "полная":
                    for title, lst, rev in (("плюс", pos, True), ("минус", neg, False)):
                        top = sorted(lst, key=lambda x: x["usd"], reverse=rev)[:8]
                        if top:
                            det += ["**%s · уровень %d · полная · топ-8 %s**" % (sname, k, title), "", "| оси (значения) | Σ взаимодействия $ [95 %] | Σ $ клетки | сделок |", "|---|---|---|---|"]
                            det += ["| %s | %+.0f [%+.0f … %+.0f] | %+.0f | %d |" % (", ".join("%s=%s" % (ax_name(a), b) for a, b in sorted(x["axes"].items())), x["usd"], x["lo"], x["hi"],
                                                                                       x["usd_cell"], x["n"]) for x in top]
                            det.append("")
    return L + [""] + det


def summary_section(res, meta):
    gc = group_counts(res)
    L = ["## Сводка", "", "«Много» — клетки оси заметно меняют набор сделок и результат (r ≥ 0,30), не «ось работает»; в скобках — что говорит нулевая модель (случайное выкидывание сделок).", ""]
    for side, sname in SIDES:
        for k in sorted({k for (s_, k) in res if s_ == side and res[(s_, k)]["axes"]}):
            for g in ("полная", "отбор"):
                c = gc.get((side, k, g))
                if not c:
                    continue
                L.append("- %s, уровень %d, %s: осей %d — много %d (за пределами нулевой модели %d, неотличимо от случайного выкидывания %d, нулевая н/п %d), мало %d, ничего %d (из них мёртвых %d), "
                         "не оценено (n < 2) %d (из них мёртвых %d)." % (
                    sname, k, g, sum(c[x] for x in ("много", "мало", "ничего", "не оценена")), c["много"], c["много: за пределами нулевой"], c["много: неотличимо от случайного"],
                    c["много: нулевая н/п"], c["мало"], c["ничего"], c["мёртвых в «ничего»"], c["не оценена"], c["мёртвых в «не оценена»"]))
    for side, sname in SIDES:
        for k in sorted({k for (s_, k) in res if s_ == side and k >= 2}):
            ints = [x for x in res[(side, k)]["interactions"] if x.get("lo") is not None and x["grid"] == "полная"]
            if ints:
                L.append("- %s, уровень %d, взаимодействия (полная сетка): %d клеток, интервал целиком > 0 у %.1f %%, < 0 у %.1f %% (при независимых клетках ожидалось бы по 2,5 %%; клетки зависимы — это не проверка)." % (
                    sname, k, len(ints), 100 * sum(1 for x in ints if x["lo"] > 0) / len(ints), 100 * sum(1 for x in ints if x["hi"] < 0) / len(ints)))
    for side, sname in SIDES:
        for k in sorted({k for (s_, k) in res if s_ == side}):
            cl = res[(side, k)].get("clusters")
            if isinstance(cl, dict) and cl.get("полная"):
                c = cl["полная"]
                L.append("- %s, уровень %d, семьи осей (полная сетка): %d, размеры %s." % (sname, k, len(c["families"]), ", ".join(str(len(x)) for x in sorted(c["families"], key=len, reverse=True)) or "—"))
    return L + [""]


def uncovered_section(res, meta):
    L = ["## 8. Что не покрыто", ""]
    for side, sname in SIDES:
        lv = meta["levels_in_db"].get(side, {})
        for k in (3, 4):
            n = lv.get(str(k), 0)
            if (side, k) not in res or n < 20:
                L.append("- %s, уровень %d: в БД клеток %d — оценка осей невозможна (нужна карта соответствующего уровня, цепочка TK-674)." % (sname, k, n))
    L += ["- Нулевая модель применима только там, где P&L общих сделок клеток совпадает (отбор / добор); оси, меняющие выходы или вход, — «нул. модель н/п».",
          "- Эффекты — описание на этих данных; прогноза «стоит / не стоит» отчёт не даёт.", ""]
    return L


def write_tables(res, meta, F, tdir):
    os.makedirs(tdir, exist_ok=True)
    for (side, k), r in sorted(res.items()):
        L = ["# %s, уровень %d — все оси (%s)" % (dict(SIDES)[side], k, anchor_line(meta, side)), ""] + level_table(r, F) + [""]
        open(os.path.join(tdir, "tables-%s-L%d.md" % (side, k)), "w", encoding="utf-8").write("\n".join(L))


def natr_compact(res, meta, F):
    """Короткая таблица NATR-осей по сторонам и уровням (для сообщения координатору): ось, n, Δ Σ $, Δ просадка $ (п.п. маржи), Δ Шарп, группа."""
    L = []
    for side, sname in SIDES:
        for k in sorted({k for (s_, k) in res if s_ == side}):
            axs = sorted((a for a in res[(side, k)]["axes"] if a["axis"] in NATR_AXES and a["group"] != "не оценена"), key=lambda a: -(a["r"] or 0))
            if not axs:
                continue
            L += ["**%s · уровень %d** (%s)" % (sname, k, anchor_line(meta, side)), "", "| ось (№) | n | Δ Σ $ | Δ просадка $ (п.п.) | Δ Шарп | группа |", "|---|---|---|---|---|---|"]
            for a in axs:
                L.append("| %s%s (%s) | %d | %s | %s (%s) | %s | %s |" % (ax_name(a["axis"]), "" if a["grid"] == "полная" else " ·отбор", axis_no(a["axis"], F) or "—", a["n"],
                                                                   f((a["d_usd"] or {}).get("mean"), "%.0f", True), f((a["d_dd"] or {}).get("mean"), "%.0f", True),
                                                                   f((a["d_ddp"] or {}).get("mean"), "%.0f", True), f((a["d_sh"] or {}).get("mean"), "%.2f", True), a["group"]))
            L.append("")
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
    os.makedirs(a.tables, exist_ok=True)
    open(os.path.join(a.tables, "natr-compact.md"), "w", encoding="utf-8").write("\n".join(natr_compact(res, meta, F)) + "\n")
    if a.only == "natr":
        open(a.md, "w", encoding="utf-8").write("\n".join(L) + "\n")
        return
    L = ["# P-42: влияние осей на прибыль и просадку, кластеры (TK-689)", "",
         "Описательно, найдено на этих данных. Деньги — $ при ставке 500 $; Шарп годовой по суточному ряду; перехай — сутки между новыми максимумами P&L.", ""] + summary_section(res, meta) + L
    L += data_section(res, meta) + groups_section(res) + top_section(res, F) + shift_section(res) + cluster_section(res) + interaction_section(res) + uncovered_section(res, meta)
    write_tables(res, meta, F, a.tables)
    open(a.md, "w", encoding="utf-8").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
