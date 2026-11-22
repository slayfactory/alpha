#!/usr/bin/env python3
"""П-42 TK-680: столбцы владельца для сводки единичек (критик thr_m27kv64m9h, 22.11): R:R, убыток на полный стоп и на стоп после тейка, худшая сделка.
  srv:    TK674_ROOT=<каталог набора> python3 tk680_owner.py owner <map674b1-L.json> <map674b1-S.json> <owner.json>   — из файлов сделок клеток активных осей и базы, без нового счёта
  ПК:     python3 tk680_owner.py table <каталог с картами и owner.json> [--out файл]                                  — таблицы «активные оси» (обе стороны)
Полный стоп — выход `stop` без тейков; стоп после тейка — выход `stop` при takes ≥ 1 (убыток = gross − fees)."""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGNAME = {"L": "long", "S": "short"}
MARGIN = 250.0
ACCOUNT = 2500.0


def _stats(row, side):
    """Зачётные сделки клетки — как `tk604_stats.splice`: по окнам, вход в сутках окна; сверка с числом сделок и Σ $ клетки."""
    import tk604_stats as st
    full, after, nets = [], [], []
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
                (after if int(float(t["takes"])) >= 1 else full).append(net)
    if len(nets) != row["trades"] or abs(sum(nets) - row["usd"]) > 1e-3:
        raise SystemExit("клетка %s: сделок %d / Σ %.4f из файлов ≠ %d / %.4f в БД" % (row["cell"], len(nets), sum(nets), row["trades"], row["usd"]))
    m = lambda v: sum(v) / len(v) if v else None
    return dict(n=len(nets), full_n=len(full), full_mean=m(full), after_n=len(after), after_mean=m(after), after_loss_n=sum(x < 0 for x in after),
                after_loss_mean=m([x for x in after if x < 0]), worst=min(nets) if nets else None)


def cmd_owner(map_l, map_s, out):
    sys.path.insert(0, HERE)
    import tk674_run as run
    store = run.chron_store(ro=True)
    res = {}
    for side, path in (("L", map_l), ("S", map_s)):
        m = json.load(open(path, encoding="utf-8"))
        want = {m["base"]["ckey"]: "база"}
        for a in m["axes"]:
            if a["active"]:
                want[a["winner"]["ckey"]] = a["axis"]
        rows = store.cells_lookup(ENGNAME[side], run.UNI, run.WIN, sorted(want), bins=run.eq_bins())
        res[side] = {}
        for ck, name in want.items():
            res[side].setdefault(name, {})[ck] = dict(_stats(rows[ck], side), sharpe=rows[ck]["sharpe"], usd=rows[ck]["usd"], trades=rows[ck]["trades"])
        print(side, "ячеек", len(want), flush=True)
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)


def _f(x, nd=2, sign=True):
    return "—" if x is None or x != x else (("%+.*f" if sign else "%.*f") % (nd, x)).replace(".", ",")


def _row(name, lab, w, own, bsh):
    """Строка таблицы: столбцы владельца (R:R, убытки на стоп, худшая сделка) — перед Σ $."""
    return "| %s | %s | %s | %s (%d) | %s (%d) | %s (%s %%) | %d | %s | %s | %s (%s) | %s (%s %%) | %d |" % (
        name, lab, _f(w["rr"], 2, False), _f(own["full_mean"], 1), own["full_n"], _f(own["after_loss_mean"], 1), own["after_loss_n"],
        _f(-w["worst"], 1), _f(-w["worst"] / MARGIN * 100, 1, False), w["n"], _f(w["usd"], 0), _f(w["mean_usd"]),
        _f(w["sharpe"], 2, False), _f(w["sharpe"] - bsh), _f(w["maxdd"], 0, False), _f(w["maxdd_pct"], 1, False), w["gap_longest"])


def cmd_table(d, out=None):
    ow = json.load(open(os.path.join(d, "owner.json"), encoding="utf-8"))
    L = []
    for side, sname in (("L", "Лонг"), ("S", "Шорт")):
        m = json.load(open(os.path.join(d, "map674b1-%s.json" % side), encoding="utf-8"))
        b = m["base"]
        act = sorted([a for a in m["axes"] if a["active"]], key=lambda a: -a["gain"])
        cols = {}
        for a in act:
            if "." in a["axis"]:
                cols.setdefault(a["axis"].split(".")[0], []).append(a["axis"])
        both = {x for v in cols.values() if len(v) > 1 for x in v}
        L.append("%s — %d осей (ключи %d, столбцы %d); якорь — база %s. Худшая сделка и убытки на стоп — $ при ставке 500 $; в скобках — число сделок; «стоп после тейка» — только убыточные; Sharpe в скобках — прирост над базой.\n" % (
            sname, len(act), sum(a["kind"] == "key" for a in act), sum(a["kind"] == "col" for a in act), sname.lower()))
        L.append("| Ось | Значение | R:R | Убыток на полный стоп, $ (сд.) | Убыток на стоп после тейка, $ (сд.) | Худшая сделка, $ (% маржи) | Сделок | Σ $ | $ на сделку | Sharpe (Δ) | Просадка $ (% маржи) | Перехай, сут |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
        bo = ow[side]["база"][b["ckey"]]
        bc = next(c for a in m["axes"] for c in a["cells"] if c.get("is_base"))
        L.append(_row("**база (якорь)**", "", bc, bo, b["sharpe"]))
        for a in act:
            w = a["winner"]
            c = next(c for c in a["cells"] if c.get("ckey") == w["ckey"])
            o = ow[side][a["axis"]][w["ckey"]]
            nm = "`%s`%s" % (a["axis"], " ⚑" if a["axis"] in both else "")
            L.append(_row(nm, c["label"], w, o, b["sharpe"]))
        L.append("")
        if both:
            L.append("⚑ — активны обе стороны порога одного столбца (%s): признак шума, а не направления; по правилу идут в пары, но как одна находка, не две." % "; ".join(
                "`%s`" % k + " (" + ", ".join(v) + ")" for k, v in sorted(cols.items()) if len(v) > 1))
            L.append("")
        pa = next((a for a in act if a["axis"] == "p"), None)
        if pa is not None:
            c = next(c for c in pa["cells"] if c.get("ckey") == pa["winner"]["ckey"])
            L.append("Ось `p` (до N позиций одновременно, маржа в работе): p = %s — одновременно открыто до %d позиций, %d $ маржи = %s %% счёта 2 500 $; просадка %s $ = %s %% счёта (база: %s %% счёта)." % (
                c["label"], c["conc"], c["conc_margin"], _f(c["conc_margin_pct"], 0, False), _f(c["maxdd"], 0, False), _f(c["maxdd_acct_pct"], 1, False),
                _f(b["maxdd"] / ACCOUNT * 100, 1, False)))
            L.append("")
    txt = "\n".join(L) + "\n"
    if out:
        open(out, "w", encoding="utf-8", newline="\n").write(txt)
    else:
        sys.stdout.write(txt)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "owner" and len(sys.argv) == 5:
        cmd_owner(*sys.argv[2:5])
    elif cmd == "table" and len(sys.argv) >= 3:
        cmd_table(sys.argv[2], sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None)
    else:
        raise SystemExit(__doc__)
