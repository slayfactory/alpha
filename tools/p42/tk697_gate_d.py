#!/usr/bin/env python3
"""П-42, TK-697 D: гейт «байт в байт» нового бинаря (№ 9 `tn`, № 1 `lx = 4`; ключи выключены) против бинаря l `c06f6674`, только по крипте (c539, В-175).
  TK674_UNI=c539 TK674_ROOT=/data/p42-tk697g TK674_BIN=<новый бинарь> TK674_CAP_GB=2 python3 tk697_gate_d.py [--code /data/p42-tk674c/code]
А) якоря c539 (строки `/data/p42-tk688/gate/A-<окно>-<сторона>.variants.txt`, те же, что у гейта TK-688): оба бинаря считают их на тех же окнах и сторонах — все файлы клетки байт в байт
   (каталоги эталонов TK-674 с батчей `p…` упакованы, сравнение — бинарь с бинарём, как требует решение координатора 23.11: «выключено = вывод бинаря l»);
Б) выборка с ключами TK-681 (`nf`, `ev/ea/el/eh`, `natr_p=5`, `sm`) и TK-683 (`e2t` / `w2` / `mk` / `vf`) на якоре окна wa: оба бинаря, файл в файл.
Файл, которого нет у эталона, — в `extra`, не различие. Чужие каталоги только читаются; P&L не печатается. Итог — <ROOT>/res/gate697d.json, код возврата 1 при различии."""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--code", default="/data/p42-tk674c/code")
ap.add_argument("--anchors", default="/data/p42-tk688/gate", help="строки якорей A-<окно>-<сторона>.variants.txt")
ap.add_argument("--ref", default="/data/p42-tk688/bin/alpha-tk688-plain.c06f6674", help="бинарь l: выключенные ключи дают его вывод")
ap.add_argument("--windows", default="wa", help="окна выборки Б (якоря А — все три)")
ap.add_argument("--no-ref", action="store_true", help="только якоря А")
A = ap.parse_args()
sys.path.insert(0, A.code)
import tk674_run as m  # noqa: E402

t, say = m.t, m.say

# ключи дописываются в конец строки якоря: у движка действует последнее значение ключа
TK681 = ["natr_p=5 natr_min=0.3", "nf=1 natr_min=0.3", "nf=5 natr_min=0.5", "natr_min=0 ev=5 ea=0.5", "natr_min=0 ev=6 ea=0.5 el=0.1 eh=0.6", "sl=1 sm=0.2"]
TK683 = ["e2=0 ew=5 e2t=1", "e2=0 ew=5 e2t=2 w2=67", "e2=0 ew=5 e2t=3 w2=70 s2=1", "e2=0.05 w2=70", "mk=1", "mk=2", "mk=1 e2=0 e2t=1 w2=67", "vf=1 vs=1 va=1"]


def anchor_lines(side):
    """{окно-сторона: строки [(cid, line)]} — якорь стороны (одна строка) по трём окнам."""
    out = {}
    for ws in ("wa-" + side, "wb-" + side, "wc-" + side):
        out[ws] = [tuple(x.strip().split(" ", 1)) for x in open(os.path.join(A.anchors, "A-%s.variants.txt" % ws), encoding="utf-8") if x.strip()]
    return out


def compare(old, new):
    files = sorted(os.listdir(old))
    diff = [f for f in files if not (os.path.exists(os.path.join(new, f)) and filecmp.cmp(os.path.join(old, f), os.path.join(new, f), shallow=False))]
    return files, diff, sorted(set(os.listdir(new)) - set(files))


def main():
    st, r5, _, _ = t._srv()
    m.engine_opts(r5)
    import tk604_run as r4
    new_bin = m.BIN
    uc = r5.Uni("c539")
    res, bad = [], []

    def run(bin_, w, side, vs, out, tag):
        r4.BIN = bin_
        r5.tick(uc, w, vs, out, m.one_side_env(side), t.q("log", "gate697d-%s.log" % tag))
        r4.BIN = new_bin

    wins = {w.name: w for w in st.TRADE_WINDOWS}
    for side in m.SIDES:
        anc = anchor_lines(side)
        for ws, vs in sorted(anc.items()):
            w = wins[ws.split("-")[0]]
            o_new, o_ref = os.path.join(m.ROOT, "gate", "A-new-" + ws), os.path.join(A.anchors, "A-" + ws)      # эталон — вывод c06f6674 гейта TK-688 (те же входы, пересчёта нет)
            run(new_bin, w, side, vs, o_new, "A-new-" + ws)
            for cid, line in vs:
                files, diff, extra = compare(os.path.join(o_ref, cid), os.path.join(o_new, cid))
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
            base = anc[ws][0][1]
            for kind, keys in (("tk681", TK681), ("tk683", TK683)):
                vs = [("%s_%d" % (kind, i), base + " " + k) for i, k in enumerate(keys)]
                o_new, o_ref = os.path.join(m.ROOT, "gate", "B-new-%s-%s" % (kind, ws)), os.path.join(A.anchors, "B-new-%s-%s" % (kind, ws))      # эталон — вывод c06f6674 гейта TK-688
                run(new_bin, wins[wn], side, vs, o_new, "B-new-%s-%s" % (kind, ws))
                for cid, line in vs:
                    files, diff, extra = compare(os.path.join(o_ref, cid), os.path.join(o_new, cid))
                    res.append(dict(kind=kind, ws=ws, cid=cid, line=line[-80:], files=len(files), diff=diff, extra=extra))
                    if diff:
                        bad.append("%s %s/%s: %s" % (kind, ws, cid, ", ".join(diff)))
                say("gate B %s %s: %d вариантов, %s" % (kind, ws, len(vs), "байт в байт" if not any(r["diff"] for r in res[-len(vs):]) else "РАЗЛИЧИЯ"))
    json.dump(dict(bin=new_bin, bin_md5=m.bin_md5(new_bin), ref=A.ref, cells=res, bad=bad),
              open(t.q("res", "gate697d.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("gate697d: клеток %d (якорей %d), файлов %d, клеток с новым файлом %d, различий %d%s" % (
        len(res), sum(r["kind"] == "anchor" for r in res), sum(r["files"] for r in res), sum(bool(r["extra"]) for r in res), len(bad), "".join("\n   " + x for x in bad[:20])))
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
