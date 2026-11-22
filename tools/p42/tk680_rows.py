#!/usr/bin/env python3
"""П-42 TK-680 шаг 2: полные строки осей `e1t` и `w2` (владелец 22.11) — сделки, Σ $, $ на сделку, Sharpe, просадка, перехай, R:R,
убыток на полный стоп, доля исполнения лимитной заявки; против якоря стороны. Без нового счёта: сделки и счётчики движка из каталогов клеток.
  srv: TK674_ROOT=<каталог набора> TK674_UNI=c539 python3 tk680_rows.py run <map674b1-L.json> <map674b1-S.json> <rows.json> [--axes e1t,w2]
  ПК:  python3 tk680_rows.py md <rows.json> [--out файл.md]
Доля исполнения — по зачётным суткам (как сделки): `e1t` — заявок исполнено / поставлено (`<сторона>-trades.csv` + `<сторона>-entryrej.csv`: не исполнено, снято уровнем);
`w2` — лот 2 исполнен / лот 1 исполнен (`<сторона>-lots.csv`, `lot2` = 1); лот 1 — по сигналу, всегда исполняется."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGNAME = {"L": "long", "S": "short"}
MARGIN = 250.0


def _fills(dirs, side):
    """Заявки и исполнения — только в зачётных сутках окон (как сделки): счётчики движка (`maker.json`, `meta.json`) идут по всем суткам окна, включая
    разогрев первых 14 суток каждого окна, поэтому больше числа зачётных сделок. `mk` = [заявок, исполнено, не исполнено, снято уровнем]; `lot2` = [лот 2 исполнен, лот 1 исполнен]."""
    import csv
    import tk604_stats as st
    mk, lot2 = [0, 0, 0, 0], [0, 0]
    raw_mk = [0, 0, 0, 0]
    have_mk = have_l2 = False
    for w in st.TRADE_WINDOWS:
        d = dirs[w.name]
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        inr = lambda ms: [lo_i <= int(x) <= hi_i for x in st.abs_day(w, ms)]
        tr = list(csv.DictReader(open(os.path.join(d, "%s-trades.csv" % ENGNAME[side]), encoding="utf-8", newline="")))
        ok = inr([int(r["entry_ms"]) for r in tr])
        p = os.path.join(d, "%s-maker.json" % ENGNAME[side])
        if os.path.exists(p):
            have_mk = True
            j = json.load(open(p, encoding="utf-8"))
            raw_mk = [raw_mk[0] + j["orders"], raw_mk[1] + j["filled"], raw_mk[2] + j["unfilled"], raw_mk[3] + j["touched"]]
            rej = list(csv.DictReader(open(os.path.join(d, "%s-entryrej.csv" % ENGNAME[side]), encoding="utf-8", newline="")))
            rin = inr([int(r["sig_ms"]) for r in rej]) if rej else []
            un = sum(1 for r, k in zip(rej, rin) if k and r["why"] == "unfilled")
            to = sum(1 for r, k in zip(rej, rin) if k and r["why"] == "touched")
            fl = sum(ok)
            mk = [mk[0] + fl + un + to, mk[1] + fl, mk[2] + un, mk[3] + to]
        lp = os.path.join(d, "%s-lots.csv" % ENGNAME[side])
        if os.path.exists(lp):
            have_l2 = True
            lots = list(csv.DictReader(open(lp, encoding="utf-8", newline="")))
            lk = inr([int(r["sig_ms"]) for r in lots]) if lots else []
            lot2 = [lot2[0] + sum(1 for r, k in zip(lots, lk) if k and int(r["lot2"]) == 1), lot2[1] + sum(lk)]
    return dict(mk=mk if have_mk else None, lot2=lot2 if have_l2 else None, raw_mk=raw_mk if have_mk else None)


def cmd_run(map_l, map_s, out, axes):
    sys.path.insert(0, HERE)
    import tk674_run as run
    import tk680_owner as ow
    store = run.chron_store(ro=True)
    res = {}
    for side, path in (("L", map_l), ("S", map_s)):
        m = json.load(open(path, encoding="utf-8"))
        b = m["base"]
        bc = next(c for a in m["axes"] for c in a["cells"] if c.get("is_base"))        # клетка базы с R:R — в картах осей, не в `base`
        want = {b["ckey"]: ("база", "база", dict(bc, label="база"))}
        for a in m["axes"]:
            if a["axis"] in axes:
                for c in a["cells"]:
                    if c.get("ckey") and not c.get("is_base"):
                        want[c["ckey"]] = (a["axis"], c["label"], c)
        rows = store.cells_lookup(ENGNAME[side], run.UNI, run.WIN, sorted(want), bins=run.eq_bins())
        res[side] = []
        for ck, (axis, lab, c) in want.items():
            r = rows[ck]
            own = ow._stats(r, side, ck)
            kpi = c.get("kpi") or c
            rec = dict(axis=axis, label=lab, ckey=ck, n=r["trades"], usd=r["usd"], mean_usd=r["usd"] / r["trades"] if r["trades"] else None, sharpe=r["sharpe"],
                       maxdd=c["maxdd"], maxdd_pct=c["maxdd_pct"], gap_longest=kpi["gap_longest"], gap_mean=kpi["gap_mean"], p90=kpi["p90"], tail=kpi.get("gap_tail", kpi.get("tail")),
                       rr=c.get("rr"), lstop=c.get("lstop", b.get("lstop")), own=own, **_fills(r["extra"]["dirs"], side))
            if rec["mk"] and rec["mk"][1] != r["trades"]:
                raise SystemExit("клетка %s: исполнено %d ≠ сделок %d" % (r["cell"], rec["mk"][1], r["trades"]))
            if rec["lot2"] and rec["lot2"][1] != r["trades"]:
                raise SystemExit("клетка %s: лотов 1 %d ≠ сделок %d" % (r["cell"], rec["lot2"][1], r["trades"]))
            res[side].append(rec)
        print(side, "строк", len(res[side]), flush=True)
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)


def _f(x, nd=2, sign=True):
    return "—" if x is None or x != x else (("%+.*f" if sign else "%.*f") % (nd, x)).replace(".", ",")


def _fill(rec):
    if rec.get("mk"):
        o, f = rec["mk"][0], rec["mk"][1]
        return "%s %% (%d из %d)" % (_f(100.0 * f / o, 0, False), f, o) if o else "—"
    if rec.get("lot2"):
        f, t = rec["lot2"]
        return "лот 2: %s %% (%d из %d)" % (_f(100.0 * f / t, 0, False), f, t) if t else "—"
    return "—"


def cmd_md(path, out=None):
    sys.path.insert(0, HERE)
    import tk680_owner as ow
    d = json.load(open(path, encoding="utf-8"))
    L = []
    for side, sname in (("L", "Лонг"), ("S", "Шорт")):
        rows = d[side]
        base = next(r for r in rows if r["axis"] == "база")
        L.append("## %s\n" % sname)
        L.append("| Ось | Значение | Сделок | Σ $ | $ на сделку | Sharpe (Δ) | Просадка $ (% маржи) | Перехай: самый длинный / средний / p90 / хвост, сут | R:R | Убыток на полный стоп, $ (сд.); по карте | Доля исполнения лимитной заявки |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in sorted(rows, key=lambda r: (r["axis"] != "база", r["axis"], r["label"])):
            L.append("| %s | %s | %d | %s | %s | %s (%s) | %s (%s %%) | %d / %s / %s / %d | %s | %s; %s | %s |" % (
                r["axis"], r["label"], r["n"], _f(r["usd"], 0), _f(r["mean_usd"]), _f(r["sharpe"], 2, False), _f(r["sharpe"] - base["sharpe"]),
                _f(r["maxdd"], 0, False), _f(r["maxdd_pct"], 1, False), r["gap_longest"], _f(r["gap_mean"], 1, False), _f(r["p90"], 0, False), r["tail"],
                _f(r["rr"], 2, False), ow._full(r["own"]), _f(r["lstop"], 1, False), _fill(r)))
        L.append("")
    s = "\n".join(L)
    if out:
        open(out, "w", encoding="utf-8").write(s + "\n")
    else:
        print(s)


def _opt(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "run" and len(sys.argv) >= 5:
        cmd_run(sys.argv[2], sys.argv[3], sys.argv[4], (_opt("--axes") or "e1t,w2").split(","))
    elif cmd == "md" and len(sys.argv) >= 3:
        cmd_md(sys.argv[2], _opt("--out"))
    else:
        raise SystemExit(__doc__)
