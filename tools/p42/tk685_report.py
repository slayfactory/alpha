#!/usr/bin/env python3
"""П-42 §13.76 (TK-685): стадии plots и report — графики п. 8 (matplotlib) и таблицы отчёта (markdown) по готовым h1 / h2 / path / trades.
Только описание готовых файлов; ничего не пересчитывает, половину 2 не читает заново (читает уже записанные h2/*.json)."""
from __future__ import annotations

import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk604_stats as st  # noqa: E402
import tk685_lib as lib  # noqa: E402
import tk685_run as R  # noqa: E402
import tk685_tables as TT  # noqa: E402

AUDIT = os.environ.get("TK685_AUDIT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "findings", "p42-features-audit-2026-11-22.md"))
SET_TITLE = {"L_A": "L_A · лонг, якорь s3", "S_A": "S_A · шорт, якорь s4", "L_W": "L_W · лонг, все 1-е касания s3", "S_W": "S_W · шорт, все 1-е касания s4"}
# палитра навыка dataviz (слоты 1 и 2: синий и оранжевый; поверхность и текст — токены)
SURFACE, INK, INK2, GRID, BLUE, ORANGE = "#fcfcfb", "#0b0b0b", "#52514e", "#e2e1dc", "#2a78d6", "#eb6834"
NBOOT = 2000
BLOCK = 4


def page_numbers():
    """имя столбца → № в описи TK-682 (страница «Признаки»); нет — «вне страницы»."""
    out = {}
    if os.path.exists(AUDIT):
        for line in open(AUDIT, encoding="utf-8"):
            m = re.match(r"\|\s*(\d+)\s*\|\s*`([^`]+)`", line)
            if m:
                out[m.group(2)] = int(m.group(1))
    return out


def tag(name, pn):
    return "%s (№ %d)" % (name, pn[name]) if name in pn else "%s (вне страницы)" % name


def md(df, digits=2):
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, (float, np.floating)):
                cells.append("—" if np.isnan(v) else ("%." + str(digits) + "f") % v)
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


# ───────────────────────────── графики ─────────────────────────────

def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def block_boot(day, vals, rng):
    """Среднее по строкам vals (n×K) с блочным бутстрепом по суткам (блок 4 суток): 2,5 и 97,5 % по NBOOT; возвращает (нижняя, верхняя)."""
    nb = st.NDAYS // BLOCK + 1
    blk = np.asarray(day) // BLOCK
    S = np.zeros((nb, vals.shape[1]))
    N = np.zeros(nb)
    for b in range(nb):
        m = blk == b
        N[b] = m.sum()
        S[b] = vals[m].sum(0)
    ix = rng.integers(0, nb, size=(NBOOT, nb))
    num, den = S[ix].sum(1), N[ix].sum(1)[:, None]
    mean = num / np.where(den > 0, den, np.nan)
    return np.nanquantile(mean, 0.025, axis=0), np.nanquantile(mean, 0.975, axis=0)


def plot_paths(outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    xs = [0] + list(lib.CHECK_MIN)
    rng = np.random.default_rng(685)
    fig, axes = plt.subplots(len(R.SETS), 3, figsize=(13, 12), sharex=True, facecolor=SURFACE)
    for i, name in enumerate(R.SETS):
        pa = TT.path_frame(name)
        pa = pa[pa["tape_ok"]]
        mfe = pa[["mfe_k%d" % m for m in lib.CHECK_MIN]].to_numpy(float)
        mae = pa[["mae_k%d" % m for m in lib.CHECK_MIN]].to_numpy(float)
        lo_y = hi_y = 0.0
        for j, g in enumerate((lib.LOSS, lib.ZERO, lib.PROFIT)):
            ax = axes[i, j]
            _style(ax)
            s = (pa["group"] == g).to_numpy()
            day = pa["day_in"].to_numpy()[s]
            for arr, sign, color, lab in ((mfe[s], 1, BLUE, "ход в пользу (MFE)"), (mae[s], -1, ORANGE, "ход против (−MAE)")):
                m = np.concatenate([[0.0], arr.mean(0)]) * sign
                a, b = block_boot(day, arr, rng)
                a, b = np.concatenate([[0.0], a]) * sign, np.concatenate([[0.0], b]) * sign
                ax.fill_between(xs, np.minimum(a, b), np.maximum(a, b), color=color, alpha=0.18, linewidth=0)
                ax.plot(xs, m, color=color, linewidth=2, label=lab)
            ax.axhline(0, color=INK2, linewidth=0.8)
            ax.set_title("%s · %s · n=%d" % (name, lib.GROUP_NAMES[g], int(s.sum())), color=INK, fontsize=10, loc="left")
            if i == len(R.SETS) - 1:
                ax.set_xlabel("минут после входа", color=INK2, fontsize=9)
            if j == 0:
                ax.set_ylabel(SET_TITLE[name] + "\n\\$ на ставку 500 \\$", color=INK2, fontsize=8)
            lo_y, hi_y = min(lo_y, ax.get_ylim()[0]), max(hi_y, ax.get_ylim()[1])
        for j in range(3):
            axes[i, j].set_ylim(lo_y, hi_y)
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.suptitle("Путь сделки по группам исхода: среднее бегущих MFE и MAE (полоса — 95 %, блочный бутстреп по суткам)", color=INK, fontsize=11, y=0.992)
    fig.legend(h, l, loc="upper center", bbox_to_anchor=(0.5, 0.968), ncol=2, frameon=False, fontsize=10, labelcolor=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    p = os.path.join(outdir, "paths.png")
    fig.savefig(p, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return p


def plot_auc_diag(outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    panels = []
    for name in R.SETS:
        fp1 = R.p("h1", name + ".json")
        if not os.path.exists(fp1):
            continue
        h1, h2 = json.load(open(fp1, encoding="utf-8")), json.load(open(R.p("h2", name + ".json"), encoding="utf-8"))
        for c in R.CONTRASTS:
            a, b = h1["contrasts"][c], h2["contrasts"][c]
            if a.get("testable") and "auc2" in b:
                panels.append((name, c, a, b))
    fig, axes = plt.subplots(1, len(panels), figsize=(4.6 * len(panels), 4.6), facecolor=SURFACE, squeeze=False)
    for ax, (name, c, a, b) in zip(axes[0], panels):
        _style(ax)
        x = np.array(a["auc1"], float) - 0.5
        y = np.array(b["auc2"], float) - 0.5
        sig = float(np.nanmedian(np.array(b["sigma2"], float)))
        ax.axvspan(-a["T"], a["T"], color=BLUE, alpha=0.10, linewidth=0)
        ax.axhspan(-1.96 * sig, 1.96 * sig, color=ORANGE, alpha=0.10, linewidth=0)
        ax.scatter(x, y, s=14, color=BLUE, edgecolor=SURFACE, linewidth=0.6, alpha=0.9)
        ax.axhline(0, color=INK2, linewidth=0.8)
        ax.axvline(0, color=INK2, linewidth=0.8)
        ax.set_title("%s · %s: AUC−0,5, половина 1 → 2" % (name, "C1 убыток/прибыль" if c == "C1" else "C2 ноль/остальные"), color=INK, fontsize=9, loc="left")
        ax.set_xlabel("половина 1 (синяя полоса — порог шума T = %.3f)" % a["T"], color=INK2, fontsize=8)
        ax.set_ylabel("половина 2 (оранжевая полоса — ±1,96σ ≈ %.3f)" % (1.96 * sig), color=INK2, fontsize=8)
    fig.tight_layout()
    p = os.path.join(outdir, "auc-diag.png")
    fig.savefig(p, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return p


def stage_plots():
    outdir = R.p("plots")
    os.makedirs(outdir, exist_ok=True)
    print(plot_paths(outdir))
    print(plot_auc_diag(outdir))
    conf = []
    for name in R.SETS:
        fp = R.p("h2", name + ".json")
        if os.path.exists(fp):
            h2 = json.load(open(fp, encoding="utf-8"))
            conf += [(name, c, r["name"]) for c, v in h2["contrasts"].items() for r in v.get("candidates", []) if r.get("confirmed")]
    print("подтверждённых признаков: %d — графики признаков %s" % (len(conf), "нужны" if conf else "не строятся (нет подтверждённых)"))


# ───────────────────────────── таблицы отчёта ─────────────────────────────

def metrics_rows():
    rows = []
    for name in R.SETS:
        df = R.load_trades(name)
        for h, lab in ((1, "половина 1 (01.07–06.09)"), (2, "половина 2 (07.09–13.11)"), (0, "всё окно")):
            if h == 0:
                m = lib.set_metrics(df["day_out"].to_numpy(), df["net"].to_numpy())
            else:
                m = R.half_metrics(df, h)
            rows.append({"набор": name, "период": lab, "сделок": m["n"], "Σ $": m["sum"], "$ на сделку": m["mean"], "Шарп": m["sharpe"], "просадка $": m["maxdd"],
                         "просадка % маржи": m["maxdd_pct"], "перехай, сут (длинный / средний / хвост)": "%d / %.1f / %d" % (m["gap_longest"], m["gap_mean"], m["tail"]), "R:R": m["rr"]})
    return pd.DataFrame(rows)


def groups_rows():
    rows = []
    for name in R.SETS:
        df = R.load_trades(name)
        for h, lab in ((1, "половина 1"), (2, "половина 2"), (0, "всё окно")):
            sub = df if h == 0 else df[df["half"] == h]
            for r in R.group_table(sub):
                rows.append({"набор": name, "период": lab, "группа": r["group"], "сделок": r["n"], "доля, %": 100 * r["share"], "Σ $": r["sum"], "$ на сделку": r["mean"]})
    return pd.DataFrame(rows)


def gross_rows():
    rows = []
    for name in R.SETS:
        df = R.load_trades(name)
        for h, lab in ((1, "половина 1"), (2, "половина 2"), (0, "всё окно")):
            sub = df if h == 0 else df[df["half"] == h]
            rows.append({"набор": name, "период": lab, "по net: убыток / около нуля / прибыль": " / ".join(str(int((sub["group"] == g).sum())) for g in (0, 1, 2)),
                         "по gross (прежнее правило): убыток / около нуля / прибыль": " / ".join(str(int((sub["group_gross"] == g).sum())) for g in (0, 1, 2))})
    return pd.DataFrame(rows)


def kinds_rows():
    rows = []
    for name in R.SETS:
        df = R.load_trades(name)
        for g in (0, 1, 2):
            s = df[df["group"] == g]
            for k, x in s.groupby("kind"):
                rows.append({"набор": name, "группа": lib.GROUP_NAMES[g], "вид выхода": k, "сделок": len(x), "Σ $": float(x["net"].sum())})
    return pd.DataFrame(rows)


def path_rows():
    out = []
    for name in R.SETS:
        pa = TT.path_frame(name)
        for h, lab in ((1, "половина 1"), (2, "половина 2"), (None, "всё окно")):
            t = TT.path_table(pa, h)
            t.insert(0, "период", lab)
            t.insert(0, "набор", name)
            out.append(t)
    return pd.concat(out, ignore_index=True)


def checkpoint_rows():
    """Среднее бегущих MFE / MAE ($) на отметках 1, 3, 5, 10, 30, 60 мин по группам (сделки с лентой, всё окно) — числа под графиком путей."""
    rows = []
    for name in R.SETS:
        pa = TT.path_frame(name)
        pa = pa[pa["tape_ok"]]
        for g in (lib.LOSS, lib.ZERO, lib.PROFIT):
            s = pa[pa["group"] == g]
            r = {"набор": name, "группа": lib.GROUP_NAMES[g], "сделок": len(s)}
            for m in (1, 3, 5, 10, 30, 60):
                r["MFE %d" % m] = float(s["mfe_k%d" % m].mean())
            for m in (1, 3, 5, 10, 30, 60):
                r["MAE %d" % m] = float(s["mae_k%d" % m].mean())
            rows.append(r)
    return pd.DataFrame(rows)


def stage_report():
    pn = page_numbers()
    L = []
    gate = json.load(open(R.p("path", "gate.json"), encoding="utf-8"))
    join = json.load(open(R.p("feat", "join.json"), encoding="utf-8"))
    L.append("## Ворота\n")
    rows = []
    for name in R.SETS:
        g, j = gate[name], join[name]
        rows.append({"набор": name, "G-ref (n, Σ)": "сошлись", "G-tape": "%d / %d = %.2f %%" % (g["tape_ok"], g["n"], 100 * g["g_tape"]),
                     "G-tape-entry (%s)" % g["entry_check"]: "%.2f %%" % (100 * g["g_entry"]),
                     "G-join": "%d / %d = %.2f %% (%s)" % (j["joined"], j["n"], 100 * j["cover"], "прошёл" if j["gate"] else "НЕ прошёл"),
                     "строка матрицы: без дубля / по цене / нет строки": "%d / %d / %d" % (j["rules"]["без дубля"], j["rules"]["по цене"], j["rules"]["нет строки"])})
    L.append(md(pd.DataFrame(rows)))
    L.append("\n## Метрики наборов\n")
    L.append(md(metrics_rows()))
    L.append("\n## Размеры групп по правилу net и по gross\n")
    L.append(md(gross_rows()))
    L.append("\n## Группы исхода: сделок, Σ $, $ на сделку\n")
    L.append(md(groups_rows()))
    L.append("\n## Вид выхода × группа (всё окно)\n")
    L.append(md(kinds_rows()))
    L.append("\n## Путь сделки по группам (сделки с лентой)\n")
    L.append(md(path_rows()))
    L.append("\n## Бегущие MFE и MAE по группам на отметках (среднее, $ на ставку 500 $; число — минуты после входа)\n")
    L.append(md(checkpoint_rows()))
    L.append("\n## Лента: исключённые сделки\n")
    ex = []
    for name in R.SETS:
        g = gate[name]
        ex.append({"набор": name, "исключено": g["excluded"], "по группам (всё окно): убыток / около нуля / прибыль": " / ".join(str(x) for x in g["excluded_by_group"]["all"]),
                   "монет": len(g["excluded_coins"]), "монета-суток": len(g["coin_days"])})
    L.append(md(pd.DataFrame(ex)))
    L.append("\nМонета, дата, число сделок без ленты (по наборам):\n")
    for name in R.SETS:
        cd = gate[name]["coin_days"]
        L.append("- %s: %s" % (name, "; ".join("%s %s ×%d" % (r["sym"], r["date"], r["trades"]) for r in cd) if cd else "—"))
    L.append("\n## Признаки: половина 1 → половина 2\n")
    rows = []
    trees = []
    for name in R.SETS:
        fp = R.p("h1", name + ".json")
        if not os.path.exists(fp):
            continue
        h1, h2 = json.load(open(fp, encoding="utf-8")), json.load(open(R.p("h2", name + ".json"), encoding="utf-8"))
        for c in R.CONTRASTS:
            a, b = h1["contrasts"][c], h2["contrasts"][c]
            dev = np.abs(np.array(a["auc1"], float) - 0.5) if a.get("testable") else None
            best = ""
            if dev is not None:
                j = int(np.nanargmax(dev))
                best = "%s: AUC1 %.3f%s" % (tag(h1["eligible"][j], pn), a["auc1"][j], (", AUC2 %.3f" % b["auc2"][j]) if "auc2" in b else "")
            rows.append({"набор": name, "контраст": "C1 убыток / прибыль" if c == "C1" else "C2 около нуля / остальные", "метка 1 / всего (пол. 1)": "%d / %d" % (a["n1"], a["n"]),
                         "метка 1 / всего (пол. 2)": "%d / %d" % (b["n1"], b["n"]), "m_f": h1["m_f"], "порог T": ("%.4f" % a["T"]) if a.get("testable") else "не тестируется (группа < 30)",
                         "кандидатов c": len(a.get("candidates", [])) if a.get("testable") else "—", "подтверждено": b.get("n_confirmed", "—") if a.get("testable") else "—", "самый сильный на половине 1": best})
        if "tree" in h1:
            tr, t2 = h1["tree"], h2["tree"]
            trees.append((name, tr, t2, h1["catboost"], h2["catboost"], h1["eligible"]))
    L.append(md(pd.DataFrame(rows)))
    L.append("\n## Дерево глубины 3 и CatBoost (контраст C1)\n")
    for name, tr, t2, c1, c2, names in trees:
        L.append("**%s.** AUC обучения (пол. 1) %.3f; AUC половины 2 %.3f; порог 0,5 + z·σ_null = %.3f (σ_null %.4f) → %s. Правила (листья — доля убытков):\n" % (
            name, tr["auc1"], t2["auc2"], t2["threshold"], t2["sigma_null"], "подтверждено" if t2["confirmed"] else "не подтвердилось"))
        L.append("```\n" + "\n".join(re.sub(r"(\b[A-Za-z_][A-Za-z0-9_]*)( [≤>])", lambda m: m.group(1) + m.group(2), ln) for ln in tr["rules"]) + "\n```")
        lv = pd.DataFrame([{"лист": k, "сделок пол. 1": v["n_h1"], "доля убытков пол. 1": v["loss_share_h1"], "сделок пол. 2": v["n_h2"], "доля убытков пол. 2": v["loss_share_h2"]} for k, v in t2["leaves"].items()])
        L.append(md(lv, 3))
        L.append("\nCatBoost (справочно, без вердикта): AUC половины 2 %.3f; модели на перемешанных метках %.3f ± %.3f; перекрытие топ-20 mean|SHAP| половин %d из 20, ранговая корреляция %.2f. Топ-5 половины 1: %s.\n" % (
            c2["auc2"], c2["null_mean"], c2["sigma_null"], c2["top20_overlap"], c2["rank_corr"], ", ".join(tag(x, pn) for x in c1["top_h1"][:5])))
    with open(R.p("tables.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("таблицы: %s (%d строк)" % (R.p("tables.md"), len(L)))
