#!/usr/bin/env python3
"""TK-708: ворота четырёх линий TK-674 в проверке вперёд (на srv, юнитом; сутки ≤ 13.11, окна wa / wb / wc, c539, замороженные бинари). Источник ключей и фильтров — `tk674-frozen.json`, эталон — `tk674-gate-cells.json`.

  python3 tk708_gate.py feat   --bin <357a66c1> --f12-bin … --flow-bin … --tox-bin … [--windows wa,wb,wc] [--cells L1,L2] [--flow-mode flow|all] --root /data/p42-tk708/gate-feat --out gate-feat.json
      построитель признаков вперёд (`tk708_feat.build_filter_table`) на тех же окнах и потоках: (1) ключи (sym, sig_ms) таблицы исследования и построителя — общие / только там / только здесь (печать);
      (2) значения cons60 / big1800 / vpin1h = parquet TK-671 ПО БИТАМ float32 на строках parquet (пустое = пустому; строки вне parquet исследование читало с пустыми big1800 / vpin1h, cons60 — из таблицы TK-638: их значения не сравниваются, составной столбец считается так же — `research_keys`); (3) составной столбец = столбец таблицы исследования (`fa` клетки из `tk674-gate-cells.json`) на общих ключах — равен;
      (4) максимальная суточная доля пустых значений по признакам линии на зачётных сутках (порог охраны NaN вперёд, решение координатора). Ворота пройдены: нет ключей «только здесь», (2) и (3) без расхождений.
  python3 tk708_gate.py replay --bin … --f12-bin … --flow-bin … --tox-bin … --root /data/p42-tk708/gate-replay --out gate-replay.json
      прогон вперёд по прошлому окну: `tk708_feat.run_cells` (охрана NaN выключена) → sha256 `{long,short}-{trades,daily,coinday,entryrej}.csv` = `tk674-gate-cells.json` у каждой линии и окна."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402
import tk616_forward as fw  # noqa: E402
import tk708_feat as f708  # noqa: E402

FREEZE_DIR = os.path.join(HERE, "..", "..", "docs", "research", "freeze")
PARQUET = "/data/p42-tk671/data/long.parquet"
RES_FEATS = f708.FEATS


def load_cells(a):
    t674 = json.load(open(os.path.join(a.freeze_dir, fw.T674_NAME), encoding="utf-8"))
    gate = json.load(open(os.path.join(a.freeze_dir, "tk674-gate-cells.json"), encoding="utf-8"))
    cols = {}
    for i, c in gate["cells"].items():
        m = fa_of(c.get("line", ""))
        if m:
            cols[i] = m
    cells = f708.cells_of(t674, cols)
    for i, c in cells.items():
        if c["ckey"] != gate["cells"][i]["ckey"]:
            raise rf.GuardError("линия %s: ключ клетки в %s ≠ шлюзу %s" % (i, fw.T674_NAME, "tk674-gate-cells.json"))
    return t674, gate, cells


def fa_of(line):
    for tok in line.split():
        if tok.startswith("fa="):
            return tok[3:]
    return ""


def setup(a, wins):
    """Деревья окон (ссылки), пути, бинари; бинарь движка = замороженному (`binary.freeze` TK-674)."""
    t674, gate, cells = load_cells(a)
    b = t674["binary"]["freeze"]
    sha, md5 = rr.file_hashes(a.bin)
    if md5 != b["md5"] or sha != b["sha256"]:
        raise rf.GuardError("бинарь движка %s: md5 %s / sha256 %s… ≠ замороженным %s / %s…" % (a.bin, md5, sha[:12], b["md5"], b["sha256"][:12]))
    os.makedirs(a.root, exist_ok=True)
    for w in wins:
        link, src = os.path.join(a.root, w.name), os.path.join(a.windows_root, w.name)
        if not os.path.lexists(link):
            os.symlink(src, link)
        elif os.path.realpath(link) != os.path.realpath(src):
            raise rf.GuardError("%s указывает не на %s" % (link, src))
    ntree = fw.check_tree_days(a.root, wins, fw.d(fw.LISTING_AFTER))
    paths = rr.Paths(a.root, tick_store=a.tick_store, sig_cache="", cap_gb=a.cap_gb)
    text = rr.read_symbols(a.symbols_file)[0]
    bins = {n: getattr(a, n + "_bin") for n in f708.FEAT_BIN_NAMES}
    hashes = {}
    for n, p in bins.items():
        if not p or not os.path.exists(p):
            raise rf.GuardError("--%s-bin: нет файла %r" % (n, p))
        s, m = rr.file_hashes(p)
        hashes[n] = {"path": p, "md5": m, "sha256": s}
    return t674, gate, cells, paths, text, bins, {"tree_files": ntree, "engine_bin": {"path": a.bin, "md5": md5, "sha256": sha}, "feature_bins": hashes}


def pick_windows(a):
    names = a.windows.split(",") if a.windows else [w.name for w in rf.WINDOWS]
    return [w for w in rf.WINDOWS if w.name in names]


def same_f32(x, y):
    """Равны по битам float32; пустое (NaN) = пустому (полезная нагрузка NaN не сравнивается)."""
    x, y = np.asarray(x, dtype=np.float32), np.asarray(y, dtype=np.float32)
    nx, ny = np.isnan(x), np.isnan(y)
    return (nx & ny) | (~nx & ~ny & (x.view(np.uint32) == y.view(np.uint32)))


def parquet_values(path):
    """{окно: {(монета, sig_ms): (cons60, big1800, vpin1h) float32}} — строки с `meta_base_pass > 0`, при повторе — с `meta_base_pass == 1` (как `tk680_singles.add_columns`)."""
    import pandas as pd
    df = pd.read_parquet(path, columns=["win", "sym", "meta_sig_ms", "meta_base_pass"] + list(RES_FEATS))
    df = df[df["meta_base_pass"] > 0].copy()
    df["win"], df["sym"] = df["win"].astype(str), df["sym"].astype(str)
    df["_pri"] = (df["meta_base_pass"] != 1).astype(int)
    df = df.sort_values("_pri", kind="stable").drop_duplicates(["win", "sym", "meta_sig_ms"])
    out = {}
    for w, g in df.groupby("win"):
        out[w] = {(s, int(m)): tuple(v) for s, m, v in zip(g["sym"], g["meta_sig_ms"], zip(*[g[f].to_numpy(np.float32) for f in RES_FEATS]))}
    return out


def day_table(vals, feat, ms, off, lohi):
    """[[сутки, строк, пустых]] по суткам сигнала + (максимальная доля, сутки, строк, пустых) на зачётных сутках."""
    st = f708.day_stats(vals, [feat], ms, off)
    rows = [[d_.isoformat(), v["rows"], v[feat]] for d_, v in sorted(st.items())]
    best = max(((v[feat] / v["rows"], d_, v["rows"], v[feat]) for d_, v in st.items() if lohi[0] <= d_ <= lohi[1]), default=(0.0, None, 0, 0))
    return rows, {"share": best[0], "day": best[1].isoformat() if best[1] else None, "rows": best[2], "nan": best[3]}


def cmd_feat(a):
    wins = pick_windows(a)
    t674, gate, cells, paths, text, bins, meta = setup(a, wins)
    eng = rr.Engine(paths, rr.SubprocessRunner(), text, a.bin, a.threads, None, ensure_tables=lambda needs: None, windows=tuple(wins), prefix="g")
    pq = parquet_values(a.parquet)
    rkeys = {w_: set(d_) for w_, d_ in pq.items()}
    res = {"meta": meta, "flow_mode": a.flow_mode, "cells": {}, "ok": True}
    for i, c in cells.items():
        if c["side"] != "L" or (a.cells and i not in a.cells.split(",")):
            continue
        feats = [f for f, _, _ in c["filter"]]
        rc_ = res["cells"][i] = {"filter": c["text"], "col": c["col"], "windows": {}, "max_daily_nan_share": {f: {"share": 0.0} for f in feats}}
        need = rr.Engine.stream_need(f708.req_of(c))
        ref_dir, ref_col = gate["cells"][i]["ftab"], c["col"]
        for w in wins:
            info, vals, syms, ms = f708.build_filter_table(paths, rr.SubprocessRunner(), eng, w, need, c, bins, a.threads, a.tick_store, a.days, (w.lod, w.hid), None, a.flow_mode, a.reuse, rkeys)
            mine = {(s, int(m)): k for k, (s, m) in enumerate(zip(syms, ms))}
            rsyms, rms, rcols = rr.rfx.read_ftab(os.path.join(ref_dir, "%s-L.csv" % w.name))
            if ref_col not in rcols:
                raise rf.GuardError("таблица исследования %s: нет столбца %s" % (ref_dir, ref_col))
            ref = {(s, int(m)): k for k, (s, m) in enumerate(zip(rsyms, rms))}
            common = sorted(set(mine) & set(ref))
            mine_col = np.array(_read_col(paths.need_tab(w.name, need), len(syms)), dtype=np.float64)
            bad_col = [k for k in common if mine_col[mine[k]] != rcols[ref_col][ref[k]]]
            pqw = pq.get(w.name, {})
            val_bad = {f: [] for f in feats}
            absent = 0
            for k in common:
                p = pqw.get(k)
                if p is None:
                    absent += 1
                    continue
                for j, f in enumerate(RES_FEATS):
                    if f in feats and not same_f32(vals[f][mine[k]], p[j]):
                        val_bad[f].append((k[0], k[1], float(vals[f][mine[k]]), float(p[j])))
            only_here = sorted(set(mine) - set(ref))
            only_ref = len(set(ref) - set(mine))
            off = f708.t0_ms(w.t0d)
            wx = {"rows": len(syms), "common": len(common), "only_here": len(only_here), "only_research": only_ref, "only_here_sample": [list(k) for k in only_here[:5]],
                  "column_mismatch": len(bad_col), "column_mismatch_sample": [list(k) for k in bad_col[:5]], "not_in_parquet_among_common": absent,
                  "value_mismatch": {f: len(v) for f, v in val_bad.items()}, "value_mismatch_sample": {f: v[:5] for f, v in val_bad.items()},
                  "excluded": info["excluded"], "nan": info["nan"], "ftab_sha256": info["ftab_sha256"], "rows_on_missing_day": info["rows_on_missing_day"],
                  "rows_on_missing_prev_day": info["rows_on_missing_prev_day"], "f12": info["f12"]}
            wx["ok"] = not only_here and not bad_col and not any(val_bad.values())
            wx["daily"] = {}
            for f in feats:
                rows, best = day_table(vals, f, ms, off, (w.lod, w.hid))
                wx["daily"][f] = {"days": rows, "max_credited": best}
                if best["share"] >= rc_["max_daily_nan_share"][f]["share"]:
                    rc_["max_daily_nan_share"][f] = dict(best, window=w.name)
            rc_["windows"][w.name] = wx
            print("feat %s %s: строк %d, общих %d, только здесь %d, только там %d, столбец ≠ %d, значения ≠ %s, вне parquet %d" % (
                i, w.name, len(syms), len(common), len(only_here), only_ref, len(bad_col), wx["value_mismatch"], absent), flush=True)
        rc_["ok"] = all(x["ok"] for x in rc_["windows"].values())
        res["ok"] = res["ok"] and rc_["ok"]
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print("G-708 feat: %s (%s)" % ("ПРОЙДЕНЫ" if res["ok"] else "НЕ ПРОЙДЕНЫ", a.out))
    return 0 if res["ok"] else 3


def _read_col(path, n):
    """Столбец «проходит» файла `ftab.csv` построителя (3-й столбец) в порядке файла: [bool]."""
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        next(f)
        for line in f:
            out.append(line.rstrip("\n").rsplit(",", 1)[1] == "1.0")
    if len(out) != n:
        raise rf.GuardError("ftab.csv %s: строк %d ≠ %d" % (path, len(out), n))
    return out


def cmd_replay(a):
    wins = pick_windows(a)
    t674, gate, cells, paths, text, bins, meta = setup(a, wins)
    if a.cells:
        cells = {i: c for i, c in cells.items() if i in a.cells.split(",")}
    pq = parquet_values(a.parquet)
    tabs, info = f708.run_cells(paths, rr.SubprocessRunner(), text, a.bin, a.threads, wins, cells, bins, a.tick_store, lambda w: (w.lod, w.hid), days=a.days, nan_guard=None, flow_mode=a.flow_mode, reuse=a.reuse, research_keys={w_: set(d_) for w_, d_ in pq.items()})
    cmp_ = f708.compare_gate(paths, {i: c for i, c in cells.items() if i in tabs}, wins, gate)
    res = {"meta": meta, "flow_mode": a.flow_mode, "info": info, "cells": {}, "ok": False}
    for i in cells:
        files = cmp_.get(i)
        res["cells"][i] = {"status": info[i]["status"], "vid": cells[i]["vid"], "keys": cells[i]["keys"], "windows": {n: {"files": f, "equal": all(v["equal"] for v in f.values())} for n, f in (files or {}).items()},
                           "equal": bool(files) and all(v["equal"] for f in files.values() for v in f.values())}
    res["ok"] = all(v["equal"] for v in res["cells"].values())
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print("G-708 replay: %s (%s); %s" % ("ПРОЙДЕНЫ" if res["ok"] else "НЕ ПРОЙДЕНЫ", ", ".join("%s %s" % (i, "=" if v["equal"] else "≠ (" + info[i]["status"] + ")") for i, v in res["cells"].items()), a.out))
    for i, x in info.items():
        if x["status"] != "read":
            print("  %s не прочитана: %s" % (i, x["reason"]))
    return 0 if res["ok"] else 3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["feat", "replay"])
    ap.add_argument("--freeze-dir", default=FREEZE_DIR)
    ap.add_argument("--bin", required=True)
    ap.add_argument("--f12-bin", default="")
    ap.add_argument("--flow-bin", default="")
    ap.add_argument("--tox-bin", default="")
    ap.add_argument("--flow-mode", default="flow", choices=["flow", "all"])
    ap.add_argument("--days", type=int, default=f708.F12_DAYS)
    ap.add_argument("--root", required=True)
    ap.add_argument("--windows-root", default="/data/p42-tk621")
    ap.add_argument("--symbols-file", default="/data/p42-tk621/universe/symbols-crypto.txt")
    ap.add_argument("--tick-store", default="")
    ap.add_argument("--parquet", default=PARQUET)
    ap.add_argument("--windows", default="")
    ap.add_argument("--cells", default="")
    ap.add_argument("--reuse", action="store_true", help="дамп потока и выходы alpha-feat с меткой `.ok` в `--root` не пересчитываются (только ворота: feat после replay на том же корне)")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--cap-gb", type=int, default=0, help="`--cap-gb` движка (память под сутки сделок; по умолчанию у движка 24 ГБ → 26–33 ГБ на процесс); результат не меняет, TK-683")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        return cmd_feat(a) if a.cmd == "feat" else cmd_replay(a)
    except rf.GuardError as e:
        print("ОСТАНОВКА (охрана): %s" % e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
