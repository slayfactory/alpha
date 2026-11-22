#!/usr/bin/env python3
"""П-42, TK-683: гейт «байт в байт» новых бинарей против эталонного на выборке вариантов (клеток ещё нет — обе стороны считаются здесь).
  TK674_UNI=c539 TK674_ROOT=/data/p42-tk683/pair TK674_CAP_GB=2 python3 tk683_gate_pair.py --ref <эталонный бинарь> --bins <новый> [<новый> …] \
      --sample /data/p42-tk674c/gate-sample [--ftab <таблица>] [--wide 200] [--windows wa,wb,wc] [--tag s3] [--code /data/p42-tk674c/code]
Выборка — `<sample>/L.variants.txt`, `S.variants.txt` (строки «cid строка варианта»). Эталон считается без `P42_WIDE`, новые — с `--wide`.
Все файлы каждой клетки эталона сравниваются с файлами нового бинаря. Итог — <ROOT>/res/gatepair<tag>.json, код возврата 1 при различии.
Чужие каталоги только читаются; P&L не печатается."""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--code", default="/data/p42-tk674c/code")
ap.add_argument("--ref", required=True)
ap.add_argument("--bins", nargs="+", required=True)
ap.add_argument("--sample", required=True)
ap.add_argument("--wide", default="200")
ap.add_argument("--windows", default="wa,wb,wc")
ap.add_argument("--tag", default="")
ap.add_argument("--ftab", default="", help="таблица признаков (`--ftab` движка) для вариантов с фильтром по столбцу; `{ws}` в пути — окно-сторона")
A = ap.parse_args()
sys.path.insert(0, A.code)
import tk674_run as m  # noqa: E402

t, say = m.t, m.say


def compare(old, new):
    files = sorted(os.listdir(old))
    diff = [f for f in files if not (os.path.exists(os.path.join(new, f)) and filecmp.cmp(os.path.join(old, f), os.path.join(new, f), shallow=False))]
    return files, diff, sorted(set(os.listdir(new)) - set(files)) if os.path.isdir(new) else []


def main():
    st, r5, _, _ = t._srv()
    m.engine_opts(r5)
    import tk604_run as r4
    uc = r5.Uni("c539")
    wins = {w.name: w for w in st.TRADE_WINDOWS}
    res, bad = [], []

    def run(bin_, wide, w, side, vs, name):
        os.environ.pop("P42_WIDE", None)
        if wide:
            os.environ["P42_WIDE"] = wide
        r4.BIN = bin_
        out = os.path.join(m.ROOT, "gate", A.tag + name)
        r5.tick(uc, w, vs, out, m.one_side_env(side), t.q("log", "gatepair%s-%s.log" % (A.tag, name)),
                 ftab=A.ftab.format(ws=name[-4:]) if A.ftab else None)
        return out

    for side in m.SIDES:
        vf = os.path.join(A.sample, "%s.variants.txt" % side)
        if not os.path.exists(vf):
            continue
        vs = [tuple(x.rstrip("\n").split(" ", 1)) for x in open(vf, encoding="utf-8") if x.strip()]
        for wn in A.windows.split(","):
            ws = "%s-%s" % (wn, side)
            o_ref = run(A.ref, "", wins[wn], side, vs, "ref-" + ws)
            for b in A.bins:
                bn = os.path.basename(b)
                o_new = run(b, A.wide, wins[wn], side, vs, "%s-%s" % (bn, ws))
                n0 = len(bad)
                for cid, line in vs:
                    files, diff, extra = compare(os.path.join(o_ref, cid), os.path.join(o_new, cid))
                    res.append(dict(bin=bn, ws=ws, cid=cid, files=len(files), diff=diff, extra=extra))
                    if diff:
                        bad.append("%s %s/%s: %s" % (bn, ws, cid, ", ".join(diff)))
                say("gatepair %s %s: %d вариантов, %s" % (bn, ws, len(vs), "байт в байт" if len(bad) == n0 else "РАЗЛИЧИЯ"))
    json.dump(dict(ref=A.ref, ref_md5=m.bin_md5(A.ref), bins={os.path.basename(b): m.bin_md5(b) for b in A.bins}, wide=A.wide, sample=A.sample, cells=res, bad=bad),
              open(t.q("res", "gatepair%s.json" % A.tag), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("gatepair: клеток %d, файлов %d, клеток с новым файлом %d, различий %d%s" % (
        len(res), sum(r["files"] for r in res), sum(bool(r["extra"]) for r in res), len(bad), "".join("\n   " + x for x in bad[:20])))
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
