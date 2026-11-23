#!/usr/bin/env python3
"""П-42, TK-680: singles.json для таблицы покрытия — по карте единичек каждого набора (основной L-321, история L-324, сетки L-325, контекст L-326) и стороне
два поля на имя ключа / столбца: «где» (значения оси) и «вердикт» (чтение карты: активна / не активна, шум отбора, $, Sharpe, просадка, перехай, R:R).
  python tools/p42/tk680_singles_json.py            — docs/findings/p42-titration-coverage-2026-11-22/singles.json (читает map674b1-{L,S}.json и подкаталоги hist/ grid/ ctx/)
  python tools/p42/tk680_singles_json.py --search   — search-values.json: «Перебор» для страницы «Признаки» (имя → строка значений единичек); дополняет файл, прежние строки не меняет
Доллары — ставка 500 $; описательно, найдено на этих данных; без бп и медиан."""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.normpath(os.path.join(HERE, "..", "..", "docs", "findings", "p42-titration-coverage-2026-11-22"))
SETS = [("main", "", "L-321", "PS-960"), ("hist", "hist/", "L-324", "PS-961"), ("grid", "grid/", "L-325", "PS-962"), ("ctx", "ctx/", "L-326", "PS-968"), ("t681", "t681/", "L-331", "PS-974"),
        ("t697", "c539/t697/", "L-394", "PS-1000"), ("t697b", "c539/t697b/", "L-395", "PS-1001")]       # TK-697: c539, поправки PS-1000 / PS-1001; вердикты L-394 / L-395
SIDE_RU = {"L": "Л", "S": "Ш"}
UNIT = {"btc_mv5": " %", "cosig15": " монет"}              # единицы порогов столбцов контекста
NO_PAGE = {"mk_btc_s60"}                                    # TK-697 t697b: столбца нет на странице «Признаки» (не из списка трейдера, № 1–362) — строки «Перебор» ему нет
NOTE = {"ew": "значение 0 — модель входа по минуте, упрощение, не кандидат (вне активных осей и пар)"}


def t681_names(axis):
    """Оси набора t681 → имена строк страницы: nf1…nf8 и контроль nf3 → nf; ev1…ev8 → ev и ea (источник и множитель — одни клетки); sm_sl / sm_cw / sm_nx → sm; прочие — как есть."""
    if re.match(r"^nf\d$", axis):
        return ["nf"]
    if re.match(r"^ev\d$", axis):
        return ["ev", "ea"]
    if axis.startswith("sm_"):
        return ["sm"]
    return [axis]


def num(x, nd=2, sign=False):
    return (("%+.*f" if sign else "%.*f") % (nd, x)).replace(".", ",").replace("-", "−")


SYM = {"le": "≤", "ge": "≥", "lt": "<"}


def lab(label, direction=None):
    """Подпись значения: q20 с направлением оси → «≥ q20»; le0 → «≤ 0»; ge1 → «≥ 1»; lt2 → «< 2»; для ключей — как есть."""
    m = re.match(r"^(le|ge|lt)([0-9.]+)$", str(label))
    if m:
        return "%s %s" % (SYM[m.group(1)], m.group(2).replace(".", ","))
    if direction and re.match(r"^q\d+$", str(label)):
        return "%s %s" % (SYM[direction], label)
    if direction and re.match(r"^v-?\d+$", str(label)):                # дискретный столбец: порог — само значение
        return "%s %s" % (SYM[direction], str(label)[1:])
    if direction and re.match(r"^z\d+$", str(label)):                  # t697b: порог 0 (знак хода BTC) — «≥ 0» / «≤ 0»
        return "%s %s" % (SYM[direction], str(label)[1:])
    return str(label).replace(".", ",")


def cut_text(d, labels, unit=""):
    """Значения одного направления столбца одной строкой: «≥ q20 / q40 / q60 / q80», «≤ 0 / 1 / 2», «< 2 / 3 / 4 монет»."""
    syms = [lab(v, d).split(" ", 1) for v in labels]
    sym = syms[0][0]
    assert all(x[0] == sym for x in syms), labels
    return "%s %s%s" % (sym, " / ".join(x[1] for x in syms), unit)


def load():
    out = {}
    for sk, sub, entry, ps in SETS:
        if not os.path.exists(os.path.join(DIR, sub, "map674b1-L.json")):      # карты набора лежат не во всех ветках (t681 — на srv): набор пропускается
            continue
        out[sk] = {s: json.load(open(os.path.join(DIR, sub, "map674b1-%s.json" % s), encoding="utf-8")) for s in "LS"}
    return out


def titrated(sk, a):
    """Ось набора, у которой есть хотя бы одна клетка вне базы с P&L; карта набора «история» несёт и ключи основного набора — им здесь не место."""
    if sk in ("hist", "ctx") and a["kind"] == "key":
        return False
    return any(not c.get("is_base") and "invalid" not in c for c in a["cells"])


def side_text(m, a):
    """Чтение одной оси стороны: (текст, прирост Sharpe активной оси | None). Неактивная ось — одним словом; подробности — в singles-summary*.md."""
    cells = [c for c in a["cells"] if not c.get("is_base") and "invalid" not in c]
    same = set(m["same_as_base"])
    if cells and all(c["cid"] in same for c in cells):
        return "клетки равны базе — ключ при базовых значениях не действует", None
    old = "" if "old_active" not in a else "; по прежней сетке — " + ("активна (%s)" % lab(a["old_winner"]) if a["old_active"] else "не активна")
    w = a.get("winner")
    if a["active"] and w:
        l = next((c["label"] for c in a["cells"] if c.get("ckey") == w["ckey"]), "?")
        return "активна %s — %d сд., %s $ (%s $/сд.), Sharpe %s (%s), просадка %s $ (%s %% маржи), перехай %d сут., R:R %s%s" % (
            lab(l, a["axis"].split(".")[1] if "." in a["axis"] else None), w["n"], num(w["usd"], 0, True), num(w["mean_usd"], 2, True), num(w["sharpe"]),
            num(w["gain"], 2, True), num(w["maxdd"], 0), num(w["maxdd_pct"], 1), w["gap_longest"], num(w["rr"]), old), w["gain"]
    return "не активна" + old, None


def values(axes, kind, sk=""):
    """«Перебор»: значения оси; у столбцов — по направлениям (порог кандидата базы: ≥ — оставить не ниже, ≤ — не выше)."""
    if kind == "key":
        one = lambda a: " / ".join(lab(c["label"]) + ("*" if c.get("is_base") else "") for c in a["cells"] if "invalid" not in c)
        return one(axes[0]) if len(axes) == 1 else "; ".join("%s: %s" % (a["axis"], one(a)) for a in sorted(axes, key=lambda x: x["axis"]))
    parts = []
    for a in sorted(axes, key=lambda x: x["axis"]):          # ≥ раньше ≤ (ge < le)
        n, d = a["axis"].split(".")
        vs = [c["label"] for c in a["cells"] if not c.get("is_base") and c["label"] != "off" and "invalid" not in c]
        parts.append(cut_text(d, vs, UNIT.get(n, "") if sk == "ctx" else ""))
    return "; ".join(parts)


def build():
    maps = load()
    names = {}                                                     # имя → {набор: {сторона: [оси]}}
    for sk, sub, entry, ps in SETS:
        for side in "LS" if sk in maps else "":
            m = maps[sk][side]
            for a in m["axes"]:
                if not titrated(sk, a):
                    continue
                for n in ([a["axis"].split(".")[0]] if a["kind"] == "col" else (t681_names(a["axis"]) if sk == "t681" else [a["axis"]])):
                    names.setdefault(n, {}).setdefault(sk, {}).setdefault(side, []).append(a)
    res = {}
    for n, per in sorted(names.items()):
        where, verdict = [], []
        for sk, sub, entry, ps in SETS:
            if sk not in per:
                continue
            sides = per[sk]
            kind = next(iter(sides.values()))[0]["kind"]
            wtxt = {}
            for side, axes in sides.items():
                wtxt[side] = values(axes, kind, sk)
            same = len(set(wtxt.values())) == 1
            w = next(iter(wtxt.values())) if same else "; ".join("%s: %s" % (SIDE_RU[s], t) for s, t in wtxt.items())
            if kind == "key":
                base = next(iter(sides.values()))[0]["base"]
                w = "значения %s (* — база %s)" % (w, str(base).replace(".", ","))
            else:
                w = "срез кандидатов базы по порогу, значения: %s" % w
            tag = {"main": "единички", "hist": "семейство ok10_*, пороги — числа пробоев", "grid": "сетка шире прежней", "ctx": "ось контекста", "t681": "ключи и столбцы TK-681",
                   "t697": "столбцы разбора сделок трейдера", "t697b": "ось хода BTC, выбрана после просмотра P&L TK-698 на этих данных"}[sk]
            where.append("%s %s (%s), %s, стоп Л 3 %% / Ш 4 %%, %s: %s" % ((("TK-697", entry, ps, "c539") if sk.startswith("t697") else ("TK-680", entry, ps, "h775")) + (tag, w)))
            sv, gains = [], []
            for side in "LS":
                axes = sides.get(side)
                if not axes:
                    continue
                res_ax = [(a, *side_text(maps[sk][side], a)) for a in sorted(axes, key=lambda x: x["axis"])]
                act = [r for r in res_ax if not r[1].startswith("не активна")]
                texts = []
                for a, t, g in (act or res_ax[:1]):                # неактивные оси стороны — одним «не активна»; активные — каждая
                    texts.append(("%s %s" % (a["axis"], t)) if len(axes) > 1 and act else t)
                    if g is not None:
                        gains.append((g, maps[sk][side]["noise_max"]))
                sv.append("%s: %s" % (SIDE_RU[side], "; ".join(texts)))
            noise = "Л %s / Ш %s" % (num(maps[sk]["L"]["noise_max"]), num(maps[sk]["S"]["noise_max"]))
            above = any(g > nz for g, nz in gains)
            verdict.append("%s: %s шума отбора (%s Sharpe). %s" % (entry, "ВЫШЕ" if above else "ниже", noise, "; ".join(sv)))
        v = "описательно, найдено на этих данных. " + " | ".join(verdict)
        if n in NOTE:
            v += ". " + NOTE[n]
        res[n] = {"where": " ; ".join(where).replace(" ; ", "; "), "verdict": v}
        if all("клетки равны базе" in t for t in verdict if True) and "активна" not in v:
            res[n]["keep_status"] = True
    return res, names


def search_values(names, maps):
    """«Перебор» для вкладки: значения единичек по имени (ключи — без базового значения; столбцы — по направлениям)."""
    out = {}
    for n, per in sorted(names.items()):
        if n in NO_PAGE:
            continue
        sk = next(s for s, *_ in SETS if s in per)               # первый набор, где ось титровалась
        sides = per[sk]
        kind = next(iter(sides.values()))[0]["kind"]
        txt = {s: values(ax, kind, sk).replace("*", "") for s, ax in sides.items()}
        out[n] = next(iter(txt.values())) if len(set(txt.values())) == 1 or len(txt) == 1 else "; ".join("%s: %s" % (SIDE_RU[s], v) for s, v in sorted(txt.items()))     # стороны различаются (ls_c2n) — по сторонам
    return out


def main():
    res, names = build()
    path = os.path.join(DIR, "singles.json")
    if "--search" in sys.argv:
        path = os.path.join(DIR, "search-values.json")
        res = search_values(names, load())
        if os.path.exists(path):                                 # дополняет файл: прежние строки (в том числе наборов без карт в этой ветке) не меняются
            old = json.load(open(path, encoding="utf-8"))
            res = {**res, **old}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(os.path.relpath(path), len(res), "имён")


if __name__ == "__main__":
    main()
