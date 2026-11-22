#!/usr/bin/env python3
"""П-42, TK-688: гейт «байт в байт» единого бинаря (master + TK-683 + ключ e1t) при e1t = 0, только по крипте (c539, В-175).
  TK674_UNI=c539 TK674_ROOT=/data/p42-tk688 TK674_BIN=<новый бинарь> TK674_CAP_GB=2 python3 tk688_gate.py [--code /data/p42-tk674c/code]
А) якоря c539 TK-674 (`/data/p42-tk674c/cells`, бинарь TK-683 9387e0a9): тот же вариант на тех же окнах и сторонах — все файлы клетки байт в байт;
Б) выборка с ключами TK-681 (`nf`, `ev/ea/el/eh`, `natr_p=5`, `sm`) — прежний бинарь TK-681 (4e1fb286), и с `e2t` / `w2` / `mk` / `vf` — бинарь TK-683:
   обе стороны считаются на c539 теми же входами и сравниваются файл в файл (эталонов с TradFi нет — файлы удалены). Файл, которого нет у эталона (`lots.csv`
   до TK-677), — в `extra`, не различие. Чужие каталоги только читаются; P&L не печатается. Итог — <ROOT>/res/gate688.json, код возврата 1 при различии."""
from __future__ import annotations

import argparse
import filecmp
import glob
import json
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--code", default="/data/p42-tk674c/code")
ap.add_argument("--anchors", default="/data/p42-tk674c")
ap.add_argument("--ref681", default="/data/p42-tk681/bin/alpha-tk681.4e1fb286")
ap.add_argument("--ref683", default="/data/p42-tk683/bin/alpha-tk683-plain.9387e0a9")
ap.add_argument("--windows", default="wa", help="окна выборки Б (якоря А — все три)")
ap.add_argument("--no-ref", action="store_true", help="только якоря А")
ap.add_argument("--smoke", action="store_true", help="e1t = 1 / 2 / 3 на окне wa обеих сторон: счётчики заявок (<сторона>-maker.json) и число сделок, без P&L")
A = ap.parse_args()
sys.path.insert(0, A.code)
import tk674_run as m  # noqa: E402

t, say = m.t, m.say

# ключи дописываются в конец строки якоря: у движка действует последнее значение ключа
TK681 = ["natr_p=5 natr_min=0.3", "nf=1 natr_min=0.3", "nf=5 natr_min=0.5", "natr_min=0 ev=5 ea=0.5", "natr_min=0 ev=6 ea=0.5 el=0.1 eh=0.6", "sl=1 sm=0.2"]
TK683 = ["e2=0 ew=5 e2t=1", "e2=0 ew=5 e2t=2 w2=67", "e2=0 ew=5 e2t=3 w2=70 s2=1", "e2=0.05 w2=70", "mk=1", "mk=2", "mk=1 e2=0 e2t=1 w2=67", "vf=1 vs=1 va=1"]


def anchor_lines(side):
    """{окно: (каталог клеток эталона, строки [(cid, line)])} — единственная партия якоря `p…` окна-стороны."""
    out = {}
    for vf in sorted(glob.glob(os.path.join(A.anchors, "cells", "*-" + side, "p*.variants.txt"))):
        ws = os.path.basename(os.path.dirname(vf))
        out[ws] = (vf[:-len(".variants.txt")], [tuple(x.rstrip("\n").split(" ", 1)) for x in open(vf, encoding="utf-8") if x.strip()])
    return out


def compare(old, new):
    files = sorted(os.listdir(old))
    diff = [f for f in files if not (os.path.exists(os.path.join(new, f)) and filecmp.cmp(os.path.join(old, f), os.path.join(new, f), shallow=False))]
    return files, diff, sorted(set(os.listdir(new)) - set(files))


def smoke(st, r5, r4, uc):
    """Ключ e1t на настоящем бинаре: заявки / заполнено / не заполнено / сорвано уровнем и число сделок (строк trades.csv − 1)."""
    w = {x.name: x for x in st.TRADE_WINDOWS}["wa"]
    rows = []
    for side in m.SIDES:
        base = anchor_lines(side)["wa-" + side][1][0][1]
        vs = [("e1t%d" % n, base + " e2=0 e1t=%d" % n) for n in (1, 2, 3)]
        out = os.path.join(m.ROOT, "gate", "smoke-wa-" + side)
        r4.BIN = m.BIN
        r5.tick(uc, w, vs, out, m.one_side_env(side), t.q("log", "gate688-smoke-%s.log" % side))
        for cid, _ in vs:
            d = os.path.join(out, cid)
            mk = json.load(open(os.path.join(d, "%s-maker.json" % m.ENGNAME[side]), encoding="utf-8"))
            n = sum(1 for _ in open(os.path.join(d, "%s-trades.csv" % m.ENGNAME[side]), encoding="utf-8")) - 1
            rows.append(dict(side=side, cid=cid, orders=mk["orders"], filled=mk["filled"], unfilled=mk["unfilled"], touched=mk["touched"], notick=mk["notick"], trades=n))
            say("smoke %s %s: заявок %d, заполнено %d, не заполнено %d, снято уровнем %d, без тика %d, сделок %d" % (side, cid, mk["orders"], mk["filled"], mk["unfilled"], mk["touched"], mk["notick"], n))
    json.dump(rows, open(t.q("res", "smoke688.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    st, r5, _, _ = t._srv()
    m.engine_opts(r5)
    import tk604_run as r4
    new_bin = m.BIN
    uc = r5.Uni("c539")
    if A.smoke:
        return smoke(st, r5, r4, uc)
    res, bad = [], []

    def run(bin_, w, side, vs, out, tag):
        r4.BIN = bin_
        r5.tick(uc, w, vs, out, m.one_side_env(side), t.q("log", "gate688-%s.log" % tag))
        r4.BIN = new_bin

    wins = {w.name: w for w in st.TRADE_WINDOWS}
    for side in m.SIDES:
        anc = anchor_lines(side)
        for ws, (refdir, vs) in sorted(anc.items()):
            w = wins[ws.split("-")[0]]
            out = os.path.join(m.ROOT, "gate", "A-" + ws)
            run(new_bin, w, side, vs, out, "A-" + ws)
            for cid, line in vs:
                files, diff, extra = compare(os.path.join(refdir, cid), os.path.join(out, cid))
                res.append(dict(kind="anchor", ws=ws, cid=cid, files=len(files), diff=diff, extra=extra))
                if diff:
                    bad.append("anchor %s/%s: %s" % (ws, cid, ", ".join(diff)))
            say("gate A %s: якорь, %s" % (ws, "байт в байт" if not res[-1]["diff"] else "РАЗЛИЧИЯ"))
        if A.no_ref:
            continue
        for wn in A.windows.split(","):
            ws = "%s-%s" % (wn, side)
            if ws not in anc:
                continue
            base = anc[ws][1][0][1]
            for kind, ref, keys in (("tk681", A.ref681, TK681), ("tk683", A.ref683, TK683)):
                vs = [("%s_%d" % (kind, i), base + " " + k) for i, k in enumerate(keys)]
                o_new, o_ref = os.path.join(m.ROOT, "gate", "B-new-%s-%s" % (kind, ws)), os.path.join(m.ROOT, "gate", "B-ref-%s-%s" % (kind, ws))
                run(new_bin, wins[wn], side, vs, o_new, "B-new-%s-%s" % (kind, ws))
                run(ref, wins[wn], side, vs, o_ref, "B-ref-%s-%s" % (kind, ws))
                for cid, line in vs:
                    files, diff, extra = compare(os.path.join(o_ref, cid), os.path.join(o_new, cid))
                    res.append(dict(kind=kind, ws=ws, cid=cid, line=line[-80:], files=len(files), diff=diff, extra=extra))
                    if diff:
                        bad.append("%s %s/%s: %s" % (kind, ws, cid, ", ".join(diff)))
                say("gate B %s %s: %d вариантов, %s" % (kind, ws, len(vs), "байт в байт" if not any(r["diff"] for r in res[-len(vs):]) else "РАЗЛИЧИЯ"))
    json.dump(dict(bin=new_bin, bin_md5=m.bin_md5(new_bin), refs=dict(tk681=A.ref681, tk683=A.ref683), cells=res, bad=bad),
              open(t.q("res", "gate688.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("gate688: клеток %d (якорей %d), файлов %d, клеток с новым файлом %d, различий %d%s" % (
        len(res), sum(r["kind"] == "anchor" for r in res), sum(r["files"] for r in res), sum(bool(r["extra"]) for r in res), len(bad), "".join("\n   " + x for x in bad[:20])))
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
