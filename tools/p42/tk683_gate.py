#!/usr/bin/env python3
"""П-42, TK-683: гейт «байт в байт» ускоренного бинаря движка перед подменой между партиями карты TK-674.
  TK674_ROOT=/data/p42-tk683 TK674_BIN=<новый бинарь> python3 tk683_gate.py [--per-batch 4] [--threads 2] [--cells-root /data/p42-tk674]
1) G-base: 6 строк баз (3 окна × L, S) = готовым базам h775 (`tk674_run.cmd_gates`) → <ROOT>/res/gbase674.json (его берёт TK674_GBASE);
2) выборка уже посчитанных клеток карты: на окно и сторону — две последние партии без фильтра с вариантами `e2t` / `mk` (путь `tick_at`),
   самая старая партия без фильтра (прежний бинарь) и последняя партия с фильтром, чья таблица `--ftab` определена однозначно и с тех пор не переписана; клетка пересчитывается новым бинарём на полной вселенной,
   все файлы каталога клетки сравниваются с посчитанными. Итог — <ROOT>/res/gate683.json, код возврата 1 при любом различии.
Чужие каталоги только читаются. P&L не печатается."""
from __future__ import annotations

import argparse
import filecmp
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_run as m  # noqa: E402

t, say = m.t, m.say


def last(line, key):
    v = re.findall(r" %s=(\S+)" % key, line)
    return float(v[-1]) if v else 0.0


def tick_at_line(line):
    return last(line, "e2t") > 0 or last(line, "mk") > 0


_IDX = {}


def ftab_for(cells_root, ws, nm, lines, marker_mtime):
    """Таблица фильтров партии: та `ftab674<уровень>`, чья карта «смысл фильтра → столбец» (res/ftabcols674*) даёт столбцы `fa` всех строк партии
    (ключи клеток партии — res/batches674*); годится, только если единственная и с отметки готовности партии не переписана."""
    side = ws.split("-")[1]
    if side not in _IDX:
        _IDX[side] = {}
        for f in glob.glob(os.path.join(cells_root, "res", "batches674*-%s.json" % side)):
            _IDX[side].update(json.load(open(f, encoding="utf-8")))
    cks = _IDX[side].get(nm)
    if not cks or len(cks) != len(lines) or not all("F=" in k for k in cks):
        return None
    fa = [re.findall(r" fa=(\S+)", line)[-1] for _, line in lines]
    ok = []
    for f in glob.glob(os.path.join(cells_root, "res", "ftabcols674*-%s.json" % side)):
        cols = json.load(open(f, encoding="utf-8"))
        if all(cols.get(k.split("F=", 1)[1]) == c for k, c in zip(cks, fa)):
            ft = os.path.join(cells_root, "ftab674" + os.path.basename(f)[len("ftabcols674"):-len("-%s.json" % side)], ws + ".csv")
            if os.path.exists(ft) and max(os.path.getmtime(ft), os.path.getmtime(f)) < marker_mtime:
                ok.append(ft)
    return ok[0] if len(ok) == 1 else None


def pick(cells_root, ws, per_batch):
    """[(партия, ftab | None, [(cid, строка)])] для окна-стороны `ws`."""
    marks = sorted((os.path.getmtime(f), os.path.basename(f)[len(ws) + 1:]) for f in glob.glob(os.path.join(cells_root, "done", ws + "-*")))
    lines = {}

    def load(nm):
        if nm not in lines:
            vf = os.path.join(cells_root, "cells", ws, nm + ".variants.txt")
            lines[nm] = [tuple(x.rstrip("\n").split(" ", 1)) for x in open(vf, encoding="utf-8") if x.strip()] if os.path.exists(vf) else []
        return lines[nm]

    def some(nm):
        ls = [x for x in load(nm) if os.path.isdir(os.path.join(cells_root, "cells", ws, nm, x[0]))]
        hot = [x for x in ls if tick_at_line(x[1])]
        cold = [x for x in ls if not tick_at_line(x[1])]
        return hot[:max(per_batch - 1, 1)] + cold[:1] if hot else cold[:per_batch]

    out, seen = [], set()
    plain = [(mt, nm) for mt, nm in marks if nm.startswith("p")]
    hot = [(mt, nm) for mt, nm in plain if any(tick_at_line(x[1]) for x in load(nm))]
    for mt, nm in hot[-2:] + plain[:1]:
        if nm not in seen:
            seen.add(nm)
            out.append((nm, None, some(nm)))
    for mt, nm in reversed([x for x in marks if x[1].startswith("f")]):
        ft = ftab_for(cells_root, ws, nm, load(nm), mt)
        if ft:
            out.append((nm, ft, some(nm)))
            break
    return [x for x in out if x[2]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-batch", type=int, default=4)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--cells-root", default="/data/p42-tk674")
    ap.add_argument("--cap-gb", type=int, default=2, help="память движка под сутки сделок (`--cap-gb`; у движка по умолчанию 24 ГБ); на результат не влияет")
    a = ap.parse_args()
    if os.path.realpath(m.ROOT) == os.path.realpath(a.cells_root):
        raise SystemExit("TK674_ROOT гейта должен отличаться от каталога карты (чужие файлы не перезаписываются)")
    st, r5, _, _ = t._srv()
    import tk604_run as r4
    r4.THREADS = a.threads
    r5.TICK_STORE = list(r5.TICK_STORE) + ["--cap-gb", str(a.cap_gb)]
    m.cmd_gates(a)                              # G-base: при различии — выход с кодом 1
    u = r5.Uni(m.UNI)
    res, bad, n = [], [], 0
    for w in st.TRADE_WINDOWS:
        for side in m.SIDES:
            ws = "%s-%s" % (w.name, side)
            for nm, ftab, vs in pick(a.cells_root, ws, a.per_batch):
                out = os.path.join(m.ROOT, "gate", ws, nm)
                r5.tick(u, w, vs, out, m.one_side_env(side), t.q("log", "gate-%s-%s.log" % (ws, nm)), ftab=ftab)
                for cid, line in vs:
                    old, new = os.path.join(a.cells_root, "cells", ws, nm, cid), os.path.join(out, cid)
                    files = sorted(os.listdir(old))     # файл, которого у прежнего бинаря не было (lots.csv до TK-677), — в `extra`, не различие
                    diff = [f for f in files if not (os.path.exists(os.path.join(new, f)) and filecmp.cmp(os.path.join(old, f), os.path.join(new, f), shallow=False))]
                    n += 1
                    res.append(dict(ws=ws, batch=nm, cid=cid, tick_at=tick_at_line(line), filt=bool(ftab), files=len(files), diff=diff,
                                    extra=sorted(set(os.listdir(new)) - set(files))))
                    if diff:
                        bad.append("%s/%s/%s: %s" % (ws, nm, cid, ", ".join(diff)))
                say("gate %s %s: клеток %d, %s" % (ws, nm, len(vs), "байт в байт" if not any(r["diff"] for r in res[-len(vs):]) else "РАЗЛИЧИЯ"))
    json.dump(dict(bin=m.BIN, bin_md5=m.bin_md5(m.BIN), bin_sha256=r4.sha256(m.BIN), cells=res, bad=bad),
              open(t.q("res", "gate683.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("gate683: клеток %d (из них с tick_at %d, с фильтром %d), файлов %d, клеток с новым файлом %d, различий %d%s" % (
        n, sum(r["tick_at"] for r in res), sum(r["filt"] for r in res), sum(r["files"] for r in res), sum(bool(r["extra"]) for r in res), len(bad), "".join("\n   " + x for x in bad[:20])))
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
