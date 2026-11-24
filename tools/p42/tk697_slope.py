#!/usr/bin/env python3
"""П-42 TK-697 (PS-1003, условие критика): допущение «линия `lx = 4` внутри минуты неподвижна» — замер без P&L.
Для каждой клетки `lx = 4` набора t697k (окно wa, обе стороны) движок гоняется отдельно с `P42_LEVELS=1` (дамп уровней: версия цены линии по минутам); для каждой сделки берётся версия цены линии
на минуте сигнала и соседняя минута — сдвиг линии внутри минуты за 30 с = |px(m+1) − px(m)| / 2 (линия берётся в середине минуты, ошибка ≤ ½ минуты наклона) в бп цены. Печатаются n, p50, p90, max, доля > 1 бп
по клетке, стороне и объединению; решение критика: p90 > 1 бп — `lx = 4` активной не бывает, пока линия не версионируется по точному времени.
  TK674_ROOT=/data/p42-tk697k TK674_UNI=c539 TK674_REBASE=1 TK674_LEVEL=1 TK680_SET=t697k TK674_BIN=<бинарь> python3 tk697_slope.py [--code /data/p42-tk697k/code]
Итог — <ROOT>/res/slope697.json."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--code", default="/data/p42-tk697k/code")
A = ap.parse_args()
sys.path.insert(0, A.code)
import numpy as np  # noqa: E402
import tk674_run as m  # noqa: E402

t, say = m.t, m.say


def load_segs(path):
    """{(sym, level): {минута: цена}} из `<side>-levels.csv` дампа (поля: sym, level, born, end, first_bar, segs, members — членов с запятыми не читаем)."""
    out = {}
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            sym, lev, _born, _end, _fb, segs = line.split(",", 6)[:6]
            out[(sym, int(lev))] = {int(a): int(b) for a, b in (x.split(":") for x in segs.split("|") if x)}
    return out


def drift_bp(segs, minute):
    """Сдвиг линии за 30 с на минуте `minute`, бп цены: соседняя минута вперёд, при её отсутствии — назад; None, если версий меньше двух."""
    px = segs.get(minute)
    if px is None:
        return None
    for nb in (minute + 1, minute - 1):
        if nb in segs and segs[nb] > 0:
            return abs(segs[nb] - px) / 2.0 / px * 1e4
    return None


def stat(v):
    v = np.asarray(v, dtype=float)
    if not len(v):
        return dict(n=0)
    return dict(n=int(len(v)), p50=float(np.percentile(v, 50)), p90=float(np.percentile(v, 90)), max=float(v.max()), share_gt1bp=float((v > 1.0).mean()))


def main():
    st, r5, _, _ = t._srv()
    m.engine_opts(r5)
    m._bin()
    uc = r5.Uni(m.UNI)
    w = {x.name: x for x in st.TRADE_WINDOWS}["wa"]
    res, pooled, miss = {}, {"L": [], "S": []}, 0
    for side in m.SIDES:
        lv = json.load(open(m.level_path(side), encoding="utf-8"))
        for ck, c in lv["cells"].items():
            if str(c["patch"].get("lx")) not in ("4", "4.0"):
                continue
            line = t.variant_line(r5, uc, side, c["patch"])
            out = os.path.join(m.ROOT, "slope", "wa-" + side, c["cid"])
            dumps = glob.glob(os.path.join(out, "**", "%s-levels.csv" % m.ENGNAME[side]), recursive=True)
            if not dumps:
                env = dict(m.one_side_env(side), P42_LEVELS="1")
                r5.tick(uc, w, [(c["cid"], line)], out, env, t.q("log", "slope697-%s-%s.log" % (side, c["cid"])))
                dumps = glob.glob(os.path.join(out, "**", "%s-levels.csv" % m.ENGNAME[side]), recursive=True)
            segs = load_segs(dumps[0])
            tr = glob.glob(os.path.join(out, "**", "%s-trades.csv" % m.ENGNAME[side]), recursive=True)[0]
            vals = []
            with open(tr, encoding="utf-8") as f:
                hdr = next(f).rstrip("\n").split(",")
                i_sym, i_lev, i_sig = hdr.index("sym"), hdr.index("level"), hdr.index("sig_min")
                for line_ in f:
                    p = line_.rstrip("\n").split(",")
                    d = drift_bp(segs.get((p[i_sym], int(p[i_lev])), {}), int(p[i_sig]))
                    if d is None:
                        miss += 1
                    else:
                        vals.append(d)
            res["%s %s" % (side, c["cid"])] = stat(vals)
            pooled[side] += vals
            say("slope697 %s %s: %s" % (side, c["cid"], json.dumps(res["%s %s" % (side, c["cid"])])))
    res["pooled L"], res["pooled S"], res["pooled all"] = stat(pooled["L"]), stat(pooled["S"]), stat(pooled["L"] + pooled["S"])
    res["no_version_trades"] = miss
    json.dump(res, open(t.q("res", "slope697.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("slope697 итог: " + json.dumps({k: v for k, v in res.items() if k.startswith("pooled")}))


if __name__ == "__main__":
    main()
