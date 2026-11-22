#!/usr/bin/env python3
"""П-42 TK-680: столбцы владельца для сводок единичек (критик thr_m27kv64m9h, 22.11): R:R, убыток на полный стоп и на стоп после тейка, худшая сделка.
  srv:    TK674_ROOT=<каталог набора> python3 tk680_owner.py owner <map674b1-L.json> <map674b1-S.json> <owner.json> [--only <приставки осей через запятую>]
          — из файлов сделок клеток активных осей и базы, без нового счёта
  srv:    TK674_ROOT=<каталог> TK674_UNI=c539 python3 tk680_owner.py anchors — якоря (клетка базы) стороны одной строкой из БД
  ПК:     python3 tk680_owner.py table <каталог с картами и owner.json> [--out файл] [--only <приставки>] [--sd <σ лонг;σ шорт>]
          — таблицы «активные оси» (обе стороны); --sd — σ Sharpe клеток другого набора: шум = больший σ × √(2 ln m)
Полный стоп — выход `stop` без тейков; стоп после тейка — выход `stop` при takes ≥ 1 (убыток = gross − fees)."""
import csv
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGNAME = {"L": "long", "S": "short"}
MARGIN = 250.0
ACCOUNT = 2500.0
NL = chr(10)


DYN_STOP = ("cw", "sl", "nx", "ls", "sn", "sg", "vf")      # ключи стопа не от фиксированного процента: размер убытка не различает «только A» и «A + B»


def _tokens(ck):
    return dict(t.split("=", 1) for t in ck.split(" ") if "=" in t)


def _stats(row, side, ck):
    """Зачётные сделки клетки — как `tk604_stats.splice`: по окнам, вход в сутках окна; сверка с числом сделок и Σ $ клетки.
    Разделённый вход (`e2` > 0 в ключе клетки, у шорта база): полные стопы делятся по размеру убытка на «A + B» (вся ставка) и «только A» (часть A, ≈ половина ставки);
    порог — 75 % убытка по цене стопа (убытки двугорбые: ≈ −10 и ≈ −20 $); при стопе не от фиксированного процента (`cw`, `sl`, `ls`, …) размер не разделяет — смесь."""
    import tk604_stats as st
    import tk674_stats as ms
    tk = _tokens(ck)
    split = float(tk.get("e2", 0)) > 0
    dyn = any(k in tk for k in DYN_STOP)
    cut = 0.75 * (500.0 * float(row["extra"]["lmax"]["s"]) / 100.0 + ms.COST_USD)
    full, after, nets, only_a, a_b = [], [], [], [], []
    for w in st.TRADE_WINDOWS:
        with open(os.path.join(row["extra"]["dirs"][w.name], "%s-trades.csv" % ENGNAME[side]), encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        d = st.abs_day(w, [int(r["entry_ms"]) for r in rows])
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        for i, t in enumerate(rows):
            if not (lo_i <= d[i] <= hi_i):
                continue
            net = float(t["gross"]) - float(t["fees"])
            nets.append(net)
            if t["kind"] == "stop":
                if int(float(t["takes"])) >= 1:
                    after.append(net)
                else:
                    full.append(net)
                    if split and not dyn:
                        (only_a if -net < cut else a_b).append(net)
    if len(nets) != row["trades"] or abs(sum(nets) - row["usd"]) > 1e-3:
        raise SystemExit("клетка %s: сделок %d / Σ %.4f из файлов ≠ %d / %.4f в БД" % (row["cell"], len(nets), sum(nets), row["trades"], row["usd"]))
    m = lambda v: sum(v) / len(v) if v else None
    return dict(split=split and not dyn, split_mixed=split and dyn, a_n=len(only_a), a_mean=m(only_a), ab_n=len(a_b), ab_mean=m(a_b),
                n=len(nets), full_n=len(full), full_mean=m(full), after_n=len(after), after_mean=m(after), after_loss_n=sum(x < 0 for x in after),
                after_loss_mean=m([x for x in after if x < 0]), worst=min(nets) if nets else None)


NONCAND = {"ew": "0"}                            # критик 22.11: `ew` = 0 — модель входа по минуте (упрощение, В-172), не кандидат: вне активных осей и пар
COMPANION = {"ew": ("e2", "0")}                  # §13.73.9: у шорта `ew` = 0 идёт с `e2` = 0 (правило движка) — рядом печатается клетка `e2` = 0 при базовом `ew`


def _companion(m, a, side):
    if side != "S" or a["axis"] not in COMPANION or not a.get("winner"):
        return None
    win = next(c for c in a["cells"] if c.get("ckey") == a["winner"]["ckey"])
    ax2, lab = COMPANION[a["axis"]]
    if win["label"] != "0":
        return None
    ob = next((x for x in m["axes"] if x["axis"] == ax2), None)
    return next((c for c in ob["cells"] if c.get("label") == lab and c.get("ckey")), None) if ob else None


def _pick(a, only):
    return a["active"] and (not only or a["axis"].startswith(tuple(only)))


def cmd_owner(map_l, map_s, out, only=None):
    sys.path.insert(0, HERE)
    import tk674_run as run
    store = run.chron_store(ro=True)
    res = {}
    for side, path in (("L", map_l), ("S", map_s)):
        m = json.load(open(path, encoding="utf-8"))
        want = {m["base"]["ckey"]: "база"}
        for a in m["axes"]:
            if _pick(a, only):
                want[a["winner"]["ckey"]] = a["axis"]
                comp = _companion(m, a, side)
                if comp:
                    want[comp["ckey"]] = a["axis"] + "+"
        rows = store.cells_lookup(ENGNAME[side], run.UNI, run.WIN, sorted(want), bins=run.eq_bins())
        res[side] = {}
        for ck, name in want.items():
            res[side].setdefault(name, {})[ck] = dict(_stats(rows[ck], side, ck), sharpe=rows[ck]["sharpe"], usd=rows[ck]["usd"], trades=rows[ck]["trades"])
        print(side, "ячеек", len(want), flush=True)
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)


def _f(x, nd=2, sign=True):
    return "—" if x is None or x != x else (("%+.*f" if sign else "%.*f") % (nd, x)).replace(".", ",")


def _full(own):
    if own.get("split"):
        return "A+B %s (%d); только A %s (%d)" % (_f(own["ab_mean"], 1), own["ab_n"], _f(own["a_mean"], 1), own["a_n"])
    if own.get("split_mixed"):
        return "смесь A / A+B %s (%d)" % (_f(own["full_mean"], 1), own["full_n"])
    return "%s (%d)" % (_f(own["full_mean"], 1), own["full_n"])


def _row(name, lab, w, own, bsh):
    """Строка таблицы: столбцы владельца (R:R, убытки на стоп, худшая сделка) — перед Σ $."""
    return "| %s | %s | %s | %s | %s (%d) | %s (%s %%) | %d | %s | %s | %s (%s) | %s (%s %%) | %d |" % (
        name, lab, _f(w["rr"], 2, False), _full(own), _f(own["after_loss_mean"], 1), own["after_loss_n"],
        _f(-w["worst"], 1), _f(-w["worst"] / MARGIN * 100, 1, False), w["n"], _f(w["usd"], 0), _f(w["mean_usd"]),
        _f(w["sharpe"], 2, False), _f(w["sharpe"] - bsh), _f(w["maxdd"], 0, False), _f(w["maxdd_pct"], 1, False), w["gap_longest"])


HEAD = ("| Ось | Значение | R:R | Убыток на полный стоп, $ (сд.) | Убыток на стоп после тейка, $ (сд.) | Худшая сделка, $ (% маржи) | Сделок | Σ $ | $ на сделку"
        " | Sharpe (Δ) | Просадка $ (% маржи) | Перехай, сут |")
SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|"


def cmd_table(d, out=None, only=None, sd_ref=None):
    ow = json.load(open(os.path.join(d, "owner.json"), encoding="utf-8"))
    L = []
    for side, sname in (("L", "Лонг"), ("S", "Шорт")):
        m = json.load(open(os.path.join(d, "map674b1-%s.json" % side), encoding="utf-8"))
        b = m["base"]
        act = sorted([a for a in m["axes"] if _pick(a, only)], key=lambda a: -a["gain"])
        grid = any("old_active" in a for a in m["axes"])
        cols = {}
        for a in act:
            if "." in a["axis"]:
                cols.setdefault(a["axis"].split(".")[0], []).append(a["axis"])
        both = {x for v in cols.values() if len(v) > 1 for x in v}
        win_lab = lambda a: next(c for c in a["cells"] if c.get("ckey") == a["winner"]["ckey"])["label"]
        nc = [a for a in act if a["axis"] in NONCAND and win_lab(a) == NONCAND[a["axis"]]]
        act = [a for a in act if a not in nc]
        sd = max(m["sharpe_sd"], sd_ref[side]) if sd_ref else m["sharpe_sd"]
        noise = sd * math.sqrt(2 * math.log(max(m["m"], 2)))
        sel = [a for a in m["axes"] if not only or a["axis"].startswith(tuple(only))]
        gains = [a["gain"] for a in sel if a["gain"] is not None and a not in nc]
        n_key, n_col = sum(a["kind"] == "key" for a in act), sum(a["kind"] == "col" for a in act)
        L.append("%s: осей набора %d, активных %d (ключи %d, столбцы %d); m = %d, σ Sharpe клеток %s → шум ≈ %s Sharpe; наибольший прирост среди осей набора %s — %s шума. "
                 "Якорь — база %s. Худшая сделка и убытки на стоп — $ при ставке 500 $; в скобках — число сделок; «стоп после тейка» — только убыточные; "
                 "у разделённого входа (шорт, e2 > 0) полный стоп — «A + B» (вся ставка) и «только A» (часть A, ≈ половина ставки; по размеру убытка, порог 75 %% полного); "
                 "Sharpe в скобках — прирост над базой." % (
                     sname, len(sel), len(act), n_key, n_col, m["m"], ("%.2f" % sd).replace(".", ","), ("%.2f" % noise).replace(".", ","),
                     _f(max(gains), 2) if gains else "—", "ниже" if not gains or max(gains) < noise else "выше", sname.lower()))
        L.append("")
        if not act:
            L.append("Активных осей нет.")
            L.append("")
            continue
        L.append(HEAD + (" По прежней сетке |" if grid else ""))
        L.append(SEP + ("---|" if grid else ""))
        bo = ow[side]["база"][b["ckey"]]
        bc = next(c for a in m["axes"] for c in a["cells"] if c.get("is_base"))
        L.append(_row("**база (якорь)**", "", bc, bo, b["sharpe"]) + (" |" if grid else ""))
        for a in act:
            w = a["winner"]
            c = next(c for c in a["cells"] if c.get("ckey") == w["ckey"])
            o = ow[side][a["axis"]][w["ckey"]]
            nm = "`%s`%s" % (a["axis"], " ⚑" if a["axis"] in both else "")
            old = (" %s |" % ("активная (%s)" % a["old_winner"] if a.get("old_active") else "не активная")) if grid else ""
            L.append(_row(nm, c["label"], w, o, b["sharpe"]) + old)
        L.append("")
        if nc:
            L.append("Не кандидаты — `%s` = %s: модель входа по минуте (упрощение настоящего входа по ленте, В-172), не параметр торговли; вне активных осей и пар; в число осей набора не входят. По прежней сетке (5 / 20 / 100 мс) ось `ew`: %s." % (
                nc[0]["axis"], NONCAND[nc[0]["axis"]], "активная (%s) — как в основном наборе" % nc[0].get("old_winner") if nc[0].get("old_active") else "не активная"))
            L.append("")
            L.append(HEAD + (" По прежней сетке |" if grid else ""))
            L.append(SEP + ("---|" if grid else ""))
            for a in nc:
                w = a["winner"]
                c = next(c for c in a["cells"] if c.get("ckey") == w["ckey"])
                L.append(_row("`%s` ⛔ упрощение, не кандидат" % a["axis"], c["label"], w, ow[side][a["axis"]][w["ckey"]], b["sharpe"]) + (" |" if grid else ""))
                comp = _companion(m, a, side)
                if comp:
                    L.append(_row("`e2` при `ew` = 5 — для сравнения с `ew` = 0 (`ew` = 0 идёт с `e2` = 0)", comp["label"], comp, ow[side][a["axis"] + "+"][comp["ckey"]], b["sharpe"]) + (" |" if grid else ""))
            L.append("")
        if both:
            L.append("⚑ — активны обе стороны порога одного столбца (%s): признак шума, а не направления; по правилу идут в пары, но как одна находка, не две." % "; ".join(
                "`%s`" % k + " (" + ", ".join(v) + ")" for k, v in sorted(cols.items()) if len(v) > 1))
            L.append("")
        pa = next((a for a in act if a["axis"] == "p"), None)
        if pa is not None:
            c = next(c for c in pa["cells"] if c.get("ckey") == pa["winner"]["ckey"])
            L.append("Ось `p` (до N позиций одновременно, маржа в работе): p = %s — максимум одновременно открытых позиций — %d, маржа %d $ = %s %% счёта 2 500 $; просадка %s $ = %s %% счёта (база: %s %% счёта)." % (
                c["label"], c["conc"], c["conc_margin"], _f(c["conc_margin_pct"], 0, False), _f(c["maxdd"], 0, False), _f(c["maxdd_acct_pct"], 1, False),
                _f(b["maxdd"] / ACCOUNT * 100, 1, False)))
            L.append("")
    txt = NL.join(L) + NL
    if out:
        open(out, "w", encoding="utf-8", newline=NL).write(txt)
    else:
        sys.stdout.write(txt)


def cmd_anchors():
    """srv: якоря стороны (клетка базы) из БД — сделки, Σ $, $ на сделку, Sharpe, просадка, перехай, R:R, убыток на полный стоп, худшая сделка; по одной строке на сторону."""
    import tk674_axes as ax
    import tk674_run as run
    store = run.chron_store(ro=True)
    for side in ax.SIDES:
        ck = ax.base_ckey(side)
        row = store.cells_lookup(ENGNAME[side], run.UNI, run.WIN, [ck], bins=run.eq_bins())[ck]
        bx, own, k = row["extra"], _stats(row, side, ck), row["extra"]["kpi"]
        lf = run.loss_fields(bx)
        print("%s %s стоп %g %%: %d сд., Σ %s $, %s $ на сд., Sharpe %s, просадка %s $ (%s %% маржи), перехай %d / %s / p90 %s / хвост %s сут, R:R %s, убыток на полный стоп %s $ (%d), худшая %s $, ставка 500 $" % (
            run.UNI, "лонг" if side == "L" else "шорт", lf["s"], row["trades"], _f(row["usd"], 0), _f(row["mean_usd"]), _f(row["sharpe"], 2, False), _f(bx["maxdd"], 0, False), _f(bx["maxdd_pct"], 1, False),
            k["gap_longest"], _f(k["gap_mean"], 1, False), _f(k["p90"], 1, False), _f(k["gap_tail"], 1, False), _f(bx["rr"]["rr"], 2, False), _f(own["full_mean"], 1), own["full_n"], _f(-lf["worst"], 1)))


def _opt(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    only = _opt("--only").split(",") if _opt("--only") else None
    if cmd == "owner" and len(sys.argv) >= 5:
        cmd_owner(*sys.argv[2:5], only=only)
    elif cmd == "anchors":
        cmd_anchors()
    elif cmd == "table" and len(sys.argv) >= 3:
        sd = _opt("--sd")
        cmd_table(sys.argv[2], _opt("--out"), only, dict(zip("LS", map(float, sd.replace(",", ".").split(";")))) if sd else None)
    else:
        raise SystemExit(__doc__)
