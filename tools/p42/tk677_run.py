#!/usr/bin/env python3
"""П-42, TK-677: раздельный вход (ключи движка `w2`, `e2t`, `s2`) — гейты нового бинаря и таблица риска. Клетки карты пар считает `tk674_run` (TK-674).
  python3 tk677_run.py gates   — G-bin, G-base (6 строк баз байт в байт), md5 бинаря → равные (res/eqbins674.json)
  python3 tk677_run.py risk    — проход с подробным выводом сделок (P42_DETAIL) по строкам риска; таблица убытка на стоп: «вошла только A» / «вошли A и B»
В таблице риска — только сторона убытка (стопы), доли заполнения лота 2 и разрыв цен входов; Σ P&L строк не печатается (клетки — после предрегистрации).
Доллары — ставка 500 $, маржа 250 $ (плечо 2); шорт — простая доходность 1 − c1/c0 (движок)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.environ.setdefault("TK674_ROOT", "/data/p42-tk677")
import tk674_run as m  # noqa: E402

t = m.t
BIN = ROOT + "/bin/alpha-tk677"
m.BIN = BIN
say = t.say
STAKE, MARGIN = 500.0, 250.0
ENG = m.ENGNAME
# строки риска: имя → правка базы стороны (выходные ключи: один проход сигналов на сторону)
RISK = {
    "L": [("base", {}), ("t1", {"e2t": 1}), ("t1c", {"e2t": 1, "s2": 1}), ("t1s3", {"e2t": 1, "s": 3}), ("t1w25", {"e2t": 1, "w2": 25}), ("t1w75", {"e2t": 1, "w2": 75})],
    "S": [("base", {}), ("t1", {"e2": 0, "e2t": 1}), ("t1c", {"e2": 0, "e2t": 1, "s2": 1}), ("t1s3", {"e2": 0, "e2t": 1, "s": 3}),
          ("t1w25", {"e2": 0, "e2t": 1, "w2": 25}), ("t1w75", {"e2": 0, "e2t": 1, "w2": 75})],
}


def cmd_gates(a):
    m.cmd_gates(a)
    eq = json.load(open("/data/p42-tk676/res/eqbins674.json", encoding="utf-8"))
    md = m.bin_md5(BIN)
    eq = [md] + [x for x in eq if x != md]
    json.dump(eq, open(t.q("res", "eqbins674.json"), "w", encoding="utf-8"))
    say("равные бинари: %s" % ", ".join(x[:8] for x in eq))


def lot_notional(legs):
    return sum(x[2] for x in legs) / 1e6


def stats(rows, stop_pct):
    """Сводка строки риска по сделкам подробного вывода."""
    n = len(rows)
    two = [r for r in rows if "lot2" in r]
    gaps = sorted(abs(r["lot2"]["entry_px_e8"] / r["entry_px_e8"] - 1) * 100 for r in two)
    out = dict(n=n, n_ab=len(two), n_a=n - len(two), gap_mean_pct=sum(gaps) / len(gaps) if gaps else 0.0,
               gap_p90_pct=gaps[int(0.9 * (len(gaps) - 1))] if gaps else 0.0, gap_max_pct=gaps[-1] if gaps else 0.0)
    for tag, sel in (("a", [r for r in rows if "lot2" not in r]), ("ab", two)):
        st = [r for r in sel if r["kind"] == "stop"]
        nets = [r["net"] for r in st]
        notional = [lot_notional(r["legs"]) + (lot_notional(r["lot2"]["legs"]) if "lot2" in r else 0.0) for r in st]
        out[tag] = dict(stops=len(st), share=len(st) / len(sel) if sel else 0.0, mean=sum(nets) / len(nets) if nets else 0.0, worst=min(nets) if nets else 0.0,
                        notional=sum(notional) / len(notional) if notional else 0.0)
    out["stop_pct"] = stop_pct
    return out


def cmd_risk(a):
    st, r5, _, _ = t._srv()
    m._bin()
    u = r5.Uni("h775")
    res, cost = {}, {}
    for side in a.sides.split(","):
        rows = {name: [] for name, _ in RISK[side]}
        lot2 = {name: [0, 0, 0, 0] for name, _ in RISK[side]}
        secs = 0.0
        for w in st.TRADE_WINDOWS:
            out = os.path.join(ROOT, "risk", "%s-%s" % (w.name, side))
            if not os.path.exists(out + "/DONE"):
                t0 = time.time()
                r5.tick(u, w, [(name, t.variant_line(r5, u, side, patch)) for name, patch in RISK[side]], out,
                        {"P42_SIDES": ENG[side], "P42_DETAIL": "1"}, t.q("log", "risk-%s-%s.log" % (w.name, side)))
                json.dump(time.time() - t0, open(out + "/secs.json", "w"))
                open(out + "/DONE", "w").close()
            secs += json.load(open(out + "/secs.json"))
            for name, _ in RISK[side]:
                d = os.path.join(out, name)
                with open(os.path.join(d, "%s-detail.jsonl" % ENG[side]), encoding="utf-8") as f:
                    rows[name] += [json.loads(x) for x in f if x.strip()]
                meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
                for blk in (meta if isinstance(meta, list) else [meta]):
                    for holder in [blk] + [v for v in blk.values() if isinstance(v, dict)]:
                        c = holder.get("lot2_[now,later,cancelled,rejected]")
                        if c:
                            lot2[name] = [x + y for x, y in zip(lot2[name], c)]
        res[side] = {}
        for name, patch in RISK[side]:
            s = stats(rows[name], float(patch.get("s", 5)))
            s["lot2"] = lot2[name]
            s["patch"] = patch
            res[side][name] = s
        cost[side] = dict(secs=secs, variants=len(RISK[side]))
    json.dump(dict(risk=res, cost=cost), open(t.q("res", "risk677.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    lines = ["# TK-677: риск раздельного входа — ставка 500 $, маржа 250 $ (плечо 2), измерено (h775, 01.07–13.11)", "",
             "Убыток — чистый P&L сделок, закрытых жёстким стопом (хотя бы один лот). «Только A» — лот 2 не вошёл; «A + B» — вошли оба.", ""]
    for side in res:
        lines += ["## %s" % m.SIDENAME[side], "",
                  "| строка | сделок | только A | A + B | лот 2: сразу / позже / отменён / отказ | разрыв входов, % цены: средн. / p90 / макс. | стопов «только A»: шт. / средн. $ / худший $ / % маржи | стопов «A + B»: шт. / средн. $ / худший $ / % маржи |",
                  "|---|---|---|---|---|---|---|---|"]
        for name, s in res[side].items():
            lines.append("| %s (%s) | %d | %d | %d | %s | %.3f / %.3f / %.3f | %d / %.2f / %.2f / %.1f | %d / %.2f / %.2f / %.1f |" % (
                name, " ".join("%s=%s" % kv for kv in s["patch"].items()) or "база", s["n"], s["n_a"], s["n_ab"], " / ".join(map(str, s["lot2"])),
                s["gap_mean_pct"], s["gap_p90_pct"], s["gap_max_pct"],
                s["a"]["stops"], s["a"]["mean"], s["a"]["worst"], -100 * s["a"]["worst"] / MARGIN,
                s["ab"]["stops"], s["ab"]["mean"], s["ab"]["worst"], -100 * s["ab"]["worst"] / MARGIN))
        lines += ["", "Время движка: %.0f с на %d строк × 3 окна." % (cost[side]["secs"], cost[side]["variants"]), ""]
    open(t.q("out", "risk677.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    say("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("gates")
    r = sp.add_parser("risk")
    r.add_argument("--sides", default="L,S")
    a = ap.parse_args()
    {"gates": cmd_gates, "risk": cmd_risk}[a.cmd](a)


if __name__ == "__main__":
    main()
