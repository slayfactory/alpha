#!/usr/bin/env python3
"""П-42, TK-679: приоритет кандидатов (ключи движка `pr`, `pw`) — гейты нового бинаря и счётчики «сколько сделок задето». Клетки карты считает `tk674_run` (TK-674).
  python3 tk679_run.py gates   — G-bin, G-base (6 строк баз байт в байт), md5 бинаря → равные (res/eqbins674.json)
  python3 tk679_run.py touch   — проход строк оси при p = 1, 2, 3; таблица счётчиков: окна, смены порядка, входы после окна, сделки, отличные от строки
                                 без приоритета с тем же p (ключ сделки: монета, уровень, время сигнала, время входа)
Из вывода клеток читаются только счётчики `meta.json` и ключ сделки; столбцы денег не читаются и не печатаются (клетки — после предрегистрации, в TK-674)."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.environ.setdefault("TK674_ROOT", "/data/p42-tk679")
import tk674_run as m  # noqa: E402

t = m.t
BIN = ROOT + "/bin/alpha-tk679"
m.BIN = BIN
say = t.say
ENG = m.ENGNAME
STOP = {"L": 3, "S": 4}                         # база' PS-955: стоп лонга 3 %, шорта 4 %
PS = (3, 2, 1)
PINF = 100000
FREE = "free"
SIGSX = "14:73"                                # дамп всех сигналов прохода (сутки сетки окна), §13.75
PRS = (0, 1, 2, 3)
PWS = (0, 1000, 5000, 60000)
KEY = ("sym", "level", "sig_ms", "entry_ms")
PRIO = "prio_[windows,multi,order_changed,late_fill,late_rej,same_ms_moved]"
REJ = "rej_[consumed,busy,coin_pause,global_pause,cap,no_outcome]"


def rows(side):
    """Строки прохода: имя → правка базы' стороны (ключи портфеля и стоп: один проход сигналов на сторону)."""
    out = []
    for p in PS:
        for pw in PWS:
            for pr in PRS:
                patch = {"s": STOP[side]}
                if p != 3:
                    patch["p"] = p
                if pr:
                    patch["pr"] = pr
                if pw:
                    patch["pw"] = pw
                out.append(("p%dr%dw%d" % (p, pr, pw), p, pr, pw, patch))
    # В-173: строка без потолка позиций — для разбора кластеров сигналов (своя предрегистрация); здесь по ней только счётчики
    out.append(("p%dr0w0" % PINF, PINF, 0, 0, {"s": STOP[side], "p": PINF}))
    # §13.75: то же без пауз монеты и общей паузы — сколько сигналов групп не вошло из-за пауз
    out.append((FREE, PINF, 0, 0, {"s": STOP[side], "p": PINF, "c": 0, "m": 1000000, "s_glob": 1000000, "ww": 0}))
    return out


def cmd_gates(a):
    m.cmd_gates(a)
    eq = json.load(open("/data/p42-tk677/res/eqbins674.json", encoding="utf-8"))
    md = m.bin_md5(BIN)
    eq = [md] + [x for x in eq if x != md]
    json.dump(eq, open(t.q("res", "eqbins674.json"), "w", encoding="utf-8"))
    say("равные бинари: %s" % ", ".join(x[:8] for x in eq))


def meta_of(d, side):
    meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
    for blk in (meta if isinstance(meta, list) else [meta]):
        for holder in [blk] + [v for v in blk.values() if isinstance(v, dict)]:
            if REJ in holder:
                return holder
    raise SystemExit("нет счётчиков в %s" % d)


def keys_of(d, side, w):
    """Ключи сделок зачётного диапазона окна (по времени входа)."""
    import tk604_feat as f4
    with open(os.path.join(d, "%s-trades.csv" % ENG[side]), encoding="utf-8", newline="") as f:
        return {tuple(r[k] for k in KEY) for r in csv.DictReader(f) if f4.in_range([int(r["entry_ms"]) + w.t0_ms], w)[0]}


def cmd_touch(a):
    st, r5, _, _ = t._srv()
    m._bin()
    u = r5.Uni("h775")
    res, cost = {}, {}
    for side in a.sides.split(","):
        rws = rows(side)
        acc = {name: dict(p=p, pr=pr, pw=pw, trades=0, scored=0, cap=0, prio=[0] * 6, d_ctrl=0, d_base=0) for name, p, pr, pw, _ in rws}
        secs = 0.0
        for w in st.TRADE_WINDOWS:
            out = os.path.join(ROOT, "touch", "%s-%s" % (w.name, side))
            if not os.path.exists(out + "/DONE"):
                t0 = time.time()
                r5.tick(u, w, [(name, t.variant_line(r5, u, side, patch)) for name, _, _, _, patch in rws], out,
                        {"P42_SIDES": ENG[side], "P42_SIGSX": SIGSX}, t.q("log", "touch-%s-%s.log" % (w.name, side)))
                json.dump(time.time() - t0, open(out + "/secs.json", "w"))
                open(out + "/DONE", "w").close()
            secs += json.load(open(out + "/secs.json"))
            ks = {name: keys_of(os.path.join(out, name), side, w) for name, _, _, _, _ in rws}
            for name, p, pr, pw, _ in rws:
                h = meta_of(os.path.join(out, name), side)
                c = acc[name]
                c["trades"] += h["trades"]
                c["scored"] += len(ks[name])
                c["cap"] += h[REJ][4]
                c["prio"] = [x + y for x, y in zip(c["prio"], h.get(PRIO, [0] * 6))]
                # контроль — прежний порядок с тем же окном и тем же p; база — без приоритета с тем же p
                c["d_ctrl"] += len(ks[name] ^ ks["p%dr0w%d" % (p, pw)])
                c["d_base"] += len(ks[name] ^ ks["p%dr0w0" % p])
        res[side] = acc
        cost[side] = dict(secs=secs, variants=len(rws))
    json.dump(dict(touch=res, cost=cost), open(t.q("res", "touch679.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    lines = ["# TK-679: приоритет кандидатов — сколько сделок задето (h775, 3 окна, база' PS-955: стоп лонга 3 %, шорта 4 %; измерено; деньги не читались)", "",
             "Сделки и «≠» — в зачётных диапазонах окон (по времени входа); счётчики движка (сделок всего, отказы, окна) — по окнам целиком.",
             "«≠ контроль» — сделок (ключ: монета, уровень, время сигнала, время входа), которых нет в одной из двух строк: эта и строка с прежним порядком",
             "(`pr=0`) при том же окне и том же `p`; «≠ без приоритета» — то же против строки `pr=0 pw=0` с тем же `p`.", ""]
    for side in res:
        lines += ["## %s" % m.SIDENAME[side], "",
                  "| строка | p | pr | pw, мс | сделок в зачёте | сделок всего | отказов «потолок» | окон | окон с ≥ 2 кандидатами | смен порядка | входов после окна | отказов после окна | переставлено в одной мс | ≠ контроль | ≠ без приоритета |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, c in res[side].items():
            lines.append("| %s | %d | %d | %d | %d | %d | %d | %s | %d | %d |" % (name, c["p"], c["pr"], c["pw"], c["scored"], c["trades"], c["cap"], " | ".join(map(str, c["prio"])), c["d_ctrl"], c["d_base"]))
        lines += ["", "Время движка: %.0f с на %d строк × 3 окна." % (cost[side]["secs"], cost[side]["variants"]), ""]
    open(t.q("out", "touch679.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    say("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("gates")
    r = sp.add_parser("touch")
    r.add_argument("--sides", default="L,S")
    a = ap.parse_args()
    {"gates": cmd_gates, "touch": cmd_touch}[a.cmd](a)


if __name__ == "__main__":
    main()
