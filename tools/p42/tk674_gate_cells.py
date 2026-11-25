#!/usr/bin/env python3
"""П-42 TK-674: шлюз заморозки кандидатов отбора (PS-1010 §7; `docs/research/freeze/tk674-frozen.json`) — на srv, в среде шага 4 (`source /data/p42-tk674c/env-s4.sh`, код `code-s4`), без записи в БД.
  gate    — каждая клетка-кандидат × три окна (wa / wb / wc) замороженным бинарём: файлы сделок / суток / монет / отказов байт в байт с клеткой в БД (каталог из архива партии), `trades_sig` = `tsig` заморозки; результат — `res/gate674-cells.json`
  overlap — пересечение сделок второго кандидата с первым на стороне (общих / сделок второго), по строкам БД, без движка (справка, порога нет)
  python3 tk674_gate_cells.py gate|overlap <tk674-frozen.json> [--out res/….json]
Окружение: `TK674_BIN` — замороженный бинарь (md5 и sha256 сверяются с файлом заморозки); `TK674_ROOT`, `TK674_UNI=c539` — из env-s4.sh."""
from __future__ import annotations

import argparse
import filecmp
import glob
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_axes as ax  # noqa: E402
import tk674_pack as pk  # noqa: E402
import tk674_run as run  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def rows_of(fz):
    """{id: (кандидат, строка БД)}; клетки нет в БД или `tsig` в БД ≠ заморозке — остановка."""
    store = run.chron_store(ro=True)
    bins = run.eq_bins()
    out = {}
    for side in "LS":
        cs = [c for c in fz["candidates"] if c["side"] == ("long" if side == "L" else "short")]
        rows = store.cells_lookup(run.SIDENAME[side], run.UNI, run.WIN, [c["ckey"] for c in cs], bins=bins)
        for c in cs:
            r = rows.get(c["ckey"])
            if r is None:
                raise SystemExit("%s: клетки нет в БД" % c["id"])
            if r["extra"].get("trades_sig") != c["tsig"]:
                raise SystemExit("%s: tsig в БД %s ≠ заморозка %s" % (c["id"], r["extra"].get("trades_sig"), c["tsig"]))
            out[c["id"]] = (c, r, side)
    return out


def batch_of(row):
    return os.path.basename(os.path.dirname(row["extra"]["dirs"]["wa"]))


def ftab_for(side, bname):
    """Суффикс таблиц фильтров партии `bname` (по файлам `res/batches674<SUF>-<сторона>.json`, FSUF = SUF без `ax.SUF2`): (путь таблиц, столбцы)."""
    hits = [p for p in glob.glob(os.path.join(run.ROOT, "res", "batches674*-%s.json" % side)) if bname in json.load(open(p, encoding="utf-8"))]
    if len(hits) != 1:
        raise SystemExit("партия %s: файлов индекса %d (%s)" % (bname, len(hits), hits))
    suf = re.match(r"batches674(.*)-%s\.json$" % side, os.path.basename(hits[0])).group(1)
    fsuf = suf[len(ax.SUF2):] if suf.startswith(ax.SUF2) else suf
    tab = os.path.join(run.ROOT, "ftab674" + fsuf)
    cols = os.path.join(run.ROOT, "res", "ftabcols674%s-%s.json" % (fsuf, side))
    if not (os.path.isdir(tab) and os.path.exists(cols)):
        raise SystemExit("партия %s: нет таблиц %s или столбцов %s" % (bname, tab, cols))
    return tab, cols


def trade_keys(row, side):
    """Множество сделок клетки (`монета:ключ входа`, как в `trades_sig`) по таблицам трёх окон."""
    st, p8 = run._W["st"], run._W["p8"]
    dirs = {w.name: (row["extra"]["dirs"][w.name], side) for w in st.TRADE_WINDOWS}
    tables, _ = p8.read_tables(run._have(dirs))
    ser = st.splice(tables, run._W["u"].coins(), daily=None)
    return {"%s:%d" % k for k in zip(ser.sym_name, [int(x) for x in ser.key_ms])}


def cmd_overlap(fz, a):
    run._winit()
    rs = rows_of(fz)
    res = {}
    for side, (c1, c2) in (("L", ("L1", "L2")), ("S", ("S1", "S2"))):
        k1, k2 = trade_keys(rs[c1][1], side), trade_keys(rs[c2][1], side)
        common = len(k1 & k2)
        res[side] = dict(first=c1, second=c2, trades_first=len(k1), trades_second=len(k2), common=common, share_of_second=round(common / len(k2), 4), share_of_first=round(common / len(k1), 4))
        print("%s: сделок (1) %d, (2) %d, общих %d → %.1f %% сделок (2), %.1f %% сделок (1)" % (side, len(k1), len(k2), common, 100 * common / len(k2), 100 * common / len(k1)))
    if a.out:
        json.dump(res, open(a.out, "w", encoding="utf-8", newline=chr(10)), ensure_ascii=False, indent=1)


def cmd_gate(fz, a):
    st, r5, _, _ = run.t._srv()
    run.engine_opts(r5)
    import tk604_run as r4
    r4.BIN = run.BIN                                                  # r5.tick берёт r4.BIN при вызове; `run._bin()` не зовём — он пишет res/bin674.json боевого каталога
    sha = r4.sha256(run.BIN)
    fb = fz["binary"]["freeze"]
    if run.bin_md5() != fb["md5"] or sha != fb["sha256"]:
        raise SystemExit("бинарь %s: md5 %s / sha256 %s ≠ заморозка (%s / %s)" % (run.BIN, run.bin_md5(), sha, fb["md5"], fb["sha256"]))
    run._winit()
    u = run._W["u"]
    rs = rows_of(fz)
    res = dict(binary=dict(path=run.BIN, md5=run.bin_md5(), sha256=sha), code={n: sha256(os.path.join(os.path.dirname(os.path.abspath(run.__file__)), n)) for n in ("tk674_run.py", "tk674_axes.py", "tk604_run.py", "tk625_run.py")},
               thr674=sha256(run.thr_path()), freeze_sha256=sha256(a.freeze), cells={})
    for cid_, (c, row, side) in rs.items():
        ex, patch = row["extra"], row["extra"]["patch"]
        filt = [tuple(x) for x in ex["filt"]]
        line = run.t.variant_line(r5, u, side, patch)
        if line != ex["line"]:
            raise SystemExit("%s: строка варианта %r ≠ строка в БД %r" % (c["id"], line, ex["line"]))
        ftab = cols = None
        if filt:
            tabdir, colf = ftab_for(side, batch_of(row))
            cols = json.load(open(colf, encoding="utf-8"))
            line += " fa=%s fo=lo fv=%g" % (cols[c["ckey"].split("F=", 1)[1]], ax.FV_PASS)
        rec = dict(ckey=c["ckey"], cid=c["cid"], line=line, ftab=tabdir if filt else None, windows={})
        for w in st.TRADE_WINDOWS:
            out = os.path.join(run.ROOT, "cells", "%s-%s" % (w.name, side), "gate674")
            os.makedirs(out, exist_ok=True)
            ft = os.path.join(tabdir, "%s-%s.csv" % (w.name, side)) if filt else None
            r5.tick(u, w, [(c["cid"], line)], out, run.one_side_env(side), run.t.q("log", "gate674-%s-%s.log" % (w.name, side)), ftab=ft)
            old = pk.ensure_cell(ex["dirs"][w.name])
            files = {}
            for f in run.CMP_FILES:
                nm = "%s-%s" % (run.SIDENAME[side], f)
                p_old, p_new = os.path.join(old, nm), os.path.join(out, c["cid"], nm)
                files[f] = dict(equal=filecmp.cmp(p_old, p_new, shallow=False), sha256=sha256(p_new))
            rec["windows"][w.name] = dict(files=files, equal=all(v["equal"] for v in files.values()))
        job = run._row_job((side, c["ckey"], c["cid"], "gate674", line, patch, filt, run.eff_flags(side, patch)))
        m = job.get("m")
        rec["metrics"] = None if m is None else dict(trades=m["n"], usd=round(m["usd"], 6), sharpe=round(m["sharpe"], 6), trades_sig=m["trades_sig"])
        rec["errs"] = job.get("errs", []) + ([job["error"]] if job.get("error") else [])
        rec["tsig_equal"] = m is not None and m["trades_sig"] == c["tsig"] == ex["trades_sig"]
        rec["ok"] = rec["tsig_equal"] and not rec["errs"] and all(v["equal"] for v in rec["windows"].values()) and m["n"] == ex["n"] and abs(m["usd"] - ex["usd"]) < 1e-6
        res["cells"][c["id"]] = rec
        print("%s %s: окна %s; tsig %s; n %s / %s; Σ %s / %s → %s" % (c["id"], c["cid"], ", ".join("%s %s" % (w, "байт в байт" if v["equal"] else "РАЗЛИЧИЯ") for w, v in rec["windows"].items()),
                                                                     "равен" if rec["tsig_equal"] else "РАЗЛИЧАЕТСЯ", m and m["n"], ex["n"], m and round(m["usd"], 2), round(ex["usd"], 2), "ok" if rec["ok"] else "НЕ ПРОЙДЕН"))
    res["passed"] = all(v["ok"] for v in res["cells"].values())
    if a.out:
        json.dump(res, open(a.out, "w", encoding="utf-8", newline=chr(10)), ensure_ascii=False, indent=1)
    print("шлюз заморозки:", "ПРОЙДЕН" if res["passed"] else "НЕ ПРОЙДЕН")
    if not res["passed"]:
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=("gate", "overlap"))
    ap.add_argument("freeze")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    fz = json.load(open(a.freeze, encoding="utf-8"))
    (cmd_gate if a.mode == "gate" else cmd_overlap)(fz, a)


if __name__ == "__main__":
    main()
