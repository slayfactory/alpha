#!/usr/bin/env python3
"""TK-674: доля зачётных сделок строки карты на пустых строках признака фильтра (NaN или нет строки таблицы), по столбцам и окнам, и Σ $ / Шарп с этими сделками и без них (PS-993 д; правило критика 23.11).
  srv (код code-xbc, окружение /tmp/env3.sh): TK674_ROOT=/data/p42-tk674c TK674_SIDES=S python3 tk674_nanshare.py <pair-map*.md> [--out файл.md] [--extra cid ...]
Строки — таблицы А / Б / синергия отчёта карты (`tk674_mapmd.py`) плюс `--extra`; клетки — из корня TK674_ROOT (в т. ч. запакованные партии, `ensure_cell`). Источники пустоты — `tk674_addendum.col_empty`:
cc_* / bd_* — fam TK-690 / xb, sac10 / sac60 / accdv1_300 — сырой `fast.csv`, столбцы TK-671 — parquet (нет строки = пусто); столбцы таблиц TK-654 / TK-638 и отношения здесь не считаются (помечены)."""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_addendum as ad  # noqa: E402
import tk674_emptycols as ec  # noqa: E402

R = os.environ.get("TK674_ROOT", "/data/p42-tk674c")


def nm_map(root):
    """{cid: хеш каталога партии} по планам / уровням `res/(plan|level)674<суффикс>-S.json` и `batches674<суффикс>-S.json` (объединения `…u` пропускаются)."""
    out = {}
    for f in sorted(glob.glob(os.path.join(root, "res", "*674*-S.json"))):
        m = re.match(r"^(?:plan|level)674(.+)-S\.json$", os.path.basename(f))
        if not m or m.group(1).endswith("u"):
            continue
        bf = os.path.join(root, "res", "batches674%s-S.json" % m.group(1))
        if not os.path.exists(bf):
            continue
        cells = json.load(open(f, encoding="utf-8"))["cells"]
        items = cells if isinstance(cells, dict) else {c.get("ckey", i): c for i, c in enumerate(cells)}
        for h, cks in json.load(open(bf, encoding="utf-8")).items():
            for k in cks:
                if k in items:
                    out.setdefault(items[k]["cid"], h)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("--out")
    ap.add_argument("--extra", nargs="*", default=[])
    a = ap.parse_args()
    mr = ad.md_rows(a.md)
    secs = {}
    for sec, nm, ln in mr:
        secs.setdefault(nm, []).append(sec)
    for nm in a.extra:
        secs.setdefault(nm, []).append("доп.")
    cols_all = {c for nm in secs for c in ad.cols_of(nm)}
    nk = {w: ad.nan_keys(w) for w in ad.WIN}
    fz = {w: ad.fast_nan(w) for w in ad.WIN}
    t671 = ad.t671_empty(sorted(c for c in cols_all if c in ec.AXIS_COLS and not c.startswith(("cc_", "bd_"))))
    nm_of = nm_map(R)
    out = []
    p = out.append
    p("| раздел | клетка | сделок | столбец (источник): X на пустых строках, wa / wb / wc | на пустых строках всего | Σ $ с / без них | Шарп с / без них |")
    p("|---|---|---|---|---|---|---|")
    summ = {}
    for nm, ss in secs.items():
        cs = ad.cols_of(nm)
        tr = ad.trades_of(R, nm, nm_of.get(nm))
        cells, ems = [], []
        for c in cs:
            src, em = ad.col_empty(c, nk, fz, t671)
            if em is None or not tr:
                cells.append("%s (%s): —" % (c, src.split(" (")[0]))
                continue
            ems.append(em)
            x = {w: (len(tr[w]), sum(1 for k in tr[w] if em(w, k))) for w in ad.WIN}
            cells.append("%s (%s): %s" % (c, src, ad.fmt_x(x)))
            pc = [100.0 * k / n for n, k in x.values() if n]
            s = summ.setdefault(c, dict(rows=0, gt0=0, gt50=0, mx=0.0))
            s["rows"] += 1
            s["gt0"] += any(v > 0 for v in pc)
            s["gt50"] += any(v > 50 for v in pc)
            s["mx"] = max([s["mx"]] + pc)
        n_tr = sum(len(v) for v in tr.values()) if tr else 0
        if ems:
            r = ec.without_cells("S", [nm], lambda w, sym, ms, c, ems=ems: any(e(w, (sym, ms)) for e in ems), R).get("S:" + nm, {})
            tail = ("%d (%.0f %%) | %+.1f / %+.1f | %.2f / %.2f" % (r["n_empty"], 100.0 * r["n_empty"] / max(r["n"], 1), r["usd"], r["usd_without"], r["sharpe"], r["sharpe_without"])
                    if r and "error" not in r else "нет каталога | | ")
        else:
            tail = "— | | "
        p("| %s | `%s` | %d | %s | %s |" % ("+".join(ss), nm, n_tr, "<br>".join(cells) if cells else "без фильтра по столбцу", tail))
    p("")
    p("| столбец | строк | X > 0 в каком-либо окне | X > 50 % в каком-либо окне | максимум X, % |")
    p("|---|---|---|---|---|")
    for c, s in sorted(summ.items(), key=lambda kv: (-kv[1]["gt50"], -kv[1]["gt0"], kv[0])):
        p("| %s | %d | %d | %d | %.1f |" % (c, s["rows"], s["gt0"], s["gt50"], s["mx"]))
    txt = "\n".join(out) + "\n"
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
