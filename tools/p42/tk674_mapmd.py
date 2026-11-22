#!/usr/bin/env python3
"""П-42 §13.73.1 (TK-674): отчёт карты пар `pair-map.md` из `map674-<сторона>.json` (стадия `tk674_run.py map`). Без БД и без счёта.
  python3 tk674_mapmd.py <каталог с map674[b]-L.json, map674[b]-S.json> [--suf b] [--out <файл.md>]
Таблицы — одна строка на клетку (пары с той же клеткой — колонкой); первыми R:R и убыток на стоп; рядом худшая сделка, Σ $, $ на сделку, число сделок,
Шарп, просадка, перехай; при базе стопа ≤ 4 % (`--suf b`) — справочная колонка «та же клетка при стопе 5 %». Без бп и без медиан."""
from __future__ import annotations

import argparse
import json
import os

MARGIN, TOP, ALPHA = 250.0, 15, 0.05
SIDE_RU = {"L": "Лонг", "S": "Шорт"}


def f0(x):
    return ("%+.0f" % x).replace("-", "−")


def f2(x):
    return ("%.2f" % x).replace(".", ",")


def sg(x):
    return ("%+.2f" % x).replace(".", ",").replace("-", "−")


def f1(x):
    return ("%.1f" % x).replace(".", ",")


def stop_s(v):
    return ("%g" % v).replace(".", ",")


def loss_cols(c):
    """R:R · стоп · убыток по цене стопа · худшая сделка — первые колонки любой строки."""
    return "%s | %s | %s (%s %%) | %s (%s %%) | %d" % (
        f2(c["rr"]), stop_s(c["s"]), f2(c["lstop"]), f1(c["lstop"] / MARGIN * 100), f2(c["worst"]), f1(c["worst"] / MARGIN * 100), c["n_over"])


def money_cols(c, gain=True):
    tail = " (хвост %d)" % c["tail"] if c.get("tail") else ""
    return "%s | %s | %d | %s%s | %.0f (%.0f %%) | %d / %s%s" % (
        f0(c["usd"]), sg(c["mean_usd"]), c["n"], f2(c["sharpe"]), (" (%s)" % sg(c["gain"])) if gain else "", c["maxdd"], c["maxdd_pct"], c["gap_longest"], f1(c["gap_mean"]), tail)


def syn_s(c):
    sy = c.get("syn")
    if not sy:
        return "— (одна ось)" if c.get("moved", 2) < 2 else "—"
    return "%s [%s; %s]" % (f0(sy["usd"]), f0(sy["lo"]), f0(sy["hi"]))


def ref5_s(c):
    r = c.get("ref5")
    if not r:
        return "нет в БД"
    return "Σ %s, Шарп %s, худшая сделка %s" % (f0(r["usd"]), f2(r["sharpe"]), f2(r["worst"]))


def dedupe(pairs, which):
    """Строки таблицы: одна на клетку (ckey), по убыванию прироста Шарпа; пары с этой клеткой — списком."""
    by = {}
    for p in pairs:
        c = p[which]
        if not c:
            continue
        row = by.setdefault(c["ckey"], dict(c=c, pairs=[]))
        row["pairs"].append(p["pair"].replace("|", " × "))
        if c.get("syn") and not row["c"].get("syn"):
            row["c"] = c
    return sorted(by.values(), key=lambda r: -r["c"]["gain"])


def holm(ps, alpha=ALPHA):
    """Число гипотез, отвергнутых по Холму (односторонние p, по возрастанию, порог α / (M − i))."""
    k = 0
    for i, p in enumerate(sorted(ps)):
        if p > alpha / (len(ps) - i):
            break
        k += 1
    return k


HEAD = "| клетка | R:R | стоп, % | убыток по цене стопа $ (% маржи) | худшая сделка $ (% маржи) | сделок хуже цены стопа | Σ $ | $ на сделку | сделок | Шарп (прирост) | просадка $ (% маржи) | перехай, сут.: самый долгий / средний | синергия Σ $ [95 %] | пар с этой клеткой |"


def table(rows, rebase, n=TOP):
    out = [HEAD + (" та же клетка при стопе 5 % (справка) |" if rebase else ""), "|" + "---|" * (14 + int(rebase))]
    for r in rows[:n]:
        c = r["c"]
        prs = "%d: %s" % (len(r["pairs"]), ", ".join(r["pairs"][:4]) + (" …" if len(r["pairs"]) > 4 else ""))
        out.append("| `%s` | %s | %s | %s | %s |%s" % (c["cid"], loss_cols(c), money_cols(c), syn_s(c), prs, (" %s |" % ref5_s(c)) if rebase else ""))
    return out


def side_md(d):
    side, b, rebase = d["side"], d["base"], d.get("rebase", False)
    pairs = d["pairs"]
    wins = [p["winner"] for p in pairs if p["winner"]]
    lt5 = sum(1 for w in wins if w["s"] < 5)
    A, B = dedupe(pairs, "winner"), dedupe(pairs, "both_moved")
    topA = A[:TOP]
    out = ["## %s" % SIDE_RU[side], ""]
    out.append("**Стоп.** База стороны — стоп %s %%: R:R %s, убыток по цене стопа %s $ (%s %% маржи), худшая сделка %s $ (%s %%), сделок хуже цены стопа %d." % (
        stop_s(b["s"]), f2(b["rr"]), f2(b["lstop"]), f1(b["lstop"] / MARGIN * 100), f2(b["worst"]), f1(b["worst"] / MARGIN * 100), b["n_over"]))
    if rebase:
        out.append("Стоп 5 % — только справочная колонка (В-171): не победитель, не якорь, не сосед плато. Победителей пар всего %d, у всех стоп ≤ 4 %%." % len(wins))
    else:
        out.append("**Почти все победители пар — со стопом 5 %%, как и база:** стоп ниже 5 %% у %d из %d победителей пар (в первых %d строках таблицы А — у %d). По решению В-171 стоп 5 %% без разделённого входа не торгуется: эта карта — справка, рабочая карта — с базой стопа ≤ 4 %% (поправка §13.73.2)." % (
            lt5, len(wins), len(topA), sum(1 for r in topA if r["c"]["s"] < 5)))
    out += ["", "База: Σ %s $, $ на сделку %s, сделок %d, Шарп %s, просадка %.0f $ (%.0f %% маржи), перехай %d сут. (средний %s, хвост %d)." % (
        f0(b["usd"]), sg(b["mean_usd"]), b["n"], f2(b["sharpe"]), b["maxdd"], b["maxdd_pct"], b["kpi"]["gap_longest"], f1(b["kpi"]["gap_mean"]), b["kpi"]["gap_tail"]),
        "Клеток карты %d; пар %d, с победителем %d, разных клеток-победителей %d. Счётчик испытаний m = %d (все клетки стороны в БД), σ Шарпа клеток %s, ожидаемый максимум шума ≈ %s Шарпа (верхняя оценка: клетки зависимы). Одинаковые сделки у разных заплаток: %d групп / %d клеток." % (
            d["n_cells"], len(pairs), len(wins), len(A), d["m"], f2(d["sharpe_sd"]), f2(d["noise_max"]), d["g_dup"]["groups"], d["g_dup"]["cells"]), ""]
    out += ["### А. Победители пар (правило предрегистрации): %d разных клеток с наибольшим приростом Шарпа" % min(TOP, len(A)), ""] + table(A, rebase) + [""]
    out += ["### Б. Лучшая охранённая клетка пары с обеими осями сдвинутыми (справка, те же клетки): %d разных клеток" % min(TOP, len(B)), ""] + table(B, rebase) + [""]
    syn = [p["both_moved"] for p in pairs if p["both_moved"] and p["both_moved"].get("syn")]
    pos = [c for c in syn if c["syn"]["lo"] > 0]
    M = len(syn)
    k_holm = holm([c["syn"]["p_le0"] for c in syn]) if syn else 0
    out.append("### Синергия пар")
    out.append("")
    out.append("Синергия = Σ(a,b) − Σ(a,0) − Σ(0,b) + Σ(0,0), интервал — блочный бутстреп по суткам. **Множественность:** интервал выше нуля у %d из %d пар; при %d проверках случайно ожидается ≈ %.0f (2,5 %% с каждой стороны); после поправки Холма по %d парам (α = 0,05) остаётся %d. Найдено на этих данных — список ниже читается как описание, не как доказательство." % (
        len(pos), M, M, 0.025 * M, M, k_holm))
    out.append("")
    rows = dedupe([dict(pair=p["pair"], both_moved=p["both_moved"]) for p in pairs if p["both_moved"] and p["both_moved"].get("syn") and p["both_moved"]["syn"]["lo"] > 0], "both_moved")
    out += table(rows, rebase, n=len(rows)) + [""]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dir")
    ap.add_argument("--suf", default="")
    ap.add_argument("--out")
    a = ap.parse_args()
    ds = [json.load(open(os.path.join(a.dir, "map674%s-%s.json" % (a.suf, s)), encoding="utf-8")) for s in "LS"]
    rebase = ds[0].get("rebase", False)
    out = ["# П-42 §13.73.%s — карта всех совместимых пар осей линии%s (TK-674)" % ("2" if rebase else "1", ", база стопа ≤ 4 %" if rebase else ""), ""]
    out.append("Окно 01.07–13.11 (136 суток), h775, ставка 500 $, маржа 250 $. Шарп — годовой по суточному ряду Σ $; перехай — сутки между новыми максимумами P&L. "
               "«Убыток по цене стопа» = 500 $ × стоп + 0,415 $ издержек — это не предел убытка: при пробое стопа сделка закрывается хуже, худшая сделка клетки напечатана рядом. "
               "**Найдено на этих данных; ждёт января–июня 2026.** Охраны (а)–(д) и плато (г) — §13.73 (PS-950, PS-951)%s. Торгуется одна линия — строки не складываются. "
               "Полная карта — `map674%s-L.json`, `map674%s-S.json` рядом; клетки — БД летописи (`chron.py cells-lookup`)." % (
                   "; база стопа и справочная колонка 5 % — поправка §13.73.2" if rebase else "", a.suf, a.suf))
    out.append("")
    for d in ds:
        out += side_md(d)
    text = "\n".join(out).rstrip() + "\n"
    path = a.out or os.path.join(a.dir, "pair-map%s.md" % a.suf)
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    print("записано %s: строк %d" % (path, text.count("\n")))


if __name__ == "__main__":
    main()
