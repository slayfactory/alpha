#!/usr/bin/env python3
"""П-42 (TK-708, L-428 / L-434): сборка `tk708-frozen.json` — шестое звено цепочки tk626 → tk638 → tk641 → tk670 → tk691 → tk708: четыре линии TK-674 (L1, L2 — лонг с фильтром признаков, S1, S2 — шорт) в проверке вперёд на c539.
Файл собирается из готовых результатов ворот `tk708_gate.py feat` и `tk708_gate.py replay` (на srv) и `tk674-frozen.json` / `tk674-gate-cells.json` (не меняются); суток ≥ 14.11 не читает.
  python3 tk708_freeze.py merge-replay --parts gate-replay-wa.json gate-replay-wb.json gate-replay-wc.json --out gate-replay.json
  python3 tk708_freeze.py build --gate-feat gate-feat.json --gate-replay gate-replay.json [--freeze-dir docs/research/freeze] [--feat-srv-path …] [--replay-srv-path …] --out docs/research/freeze/tk708-frozen.json"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk616_forward as fw  # noqa: E402
import tk708_feat as f708  # noqa: E402
import tk708_gate as g708  # noqa: E402

WINDOWS = ("wa", "wb", "wc")
FEAT_MISMATCH = ("only_here", "column_mismatch")


def load(path):
    return json.load(open(path, encoding="utf-8"))


def link_of(freeze_dir, name, version, note=None):
    p = os.path.join(freeze_dir, name)
    d = {"file": "docs/research/freeze/" + name, "version": version, "sha256": fw.sha256_text(p), "untouched": True}
    if note:
        d["note"] = note
    return d


def file_ref(path, srv_path):
    return {"path": srv_path or os.path.abspath(path), "sha256": fw.sha256_text(path)}


def stop(msg):
    raise SystemExit("STOP: " + msg)


def check_feat(gf, cells):
    """Ворота feat: ok, три окна, линии L1 / L2, ни одного расхождения (ключи «только здесь», столбец, значения). → {линия: {признак: макс. суточная доля}}."""
    if gf.get("ok") is not True:
        stop("gate-feat.json: ok ≠ true")
    longs = {i: c for i, c in cells.items() if c["side"] == "L"}
    if set(gf["cells"]) != set(longs):
        stop("gate-feat.json: линии %s ≠ лонг-линиям %s" % (sorted(gf["cells"]), sorted(longs)))
    share = {}
    for i, c in longs.items():
        x = gf["cells"][i]
        if x.get("ok") is not True or x["col"] != c["col"] or x["filter"] != c["text"] or sorted(x["windows"]) != sorted(WINDOWS):
            stop("gate-feat.json: линия %s: ok %s, столбец %s (нужен %s), окна %s" % (i, x.get("ok"), x.get("col"), c["col"], sorted(x["windows"])))
        for wn, wx in x["windows"].items():
            if not wx["ok"] or wx["only_here"] or wx["column_mismatch"] or any(wx["value_mismatch"].values()):
                stop("gate-feat.json: линия %s, окно %s: только здесь %s, столбец ≠ %s, значения ≠ %s" % (i, wn, wx["only_here"], wx["column_mismatch"], wx["value_mismatch"]))
        feats = [f for f, _, _ in c["filter"]]
        if set(x["max_daily_nan_share"]) != set(feats):
            stop("gate-feat.json: линия %s: доли пустых по %s ≠ признакам фильтра %s" % (i, sorted(x["max_daily_nan_share"]), feats))
        share[i] = {f: float(x["max_daily_nan_share"][f]["share"]) for f in feats}
        if any(not 0.0 <= v <= 1.0 for v in share[i].values()):
            stop("gate-feat.json: линия %s: доля вне [0; 1]: %s" % (i, share[i]))
    return share


def check_replay(gr, cells):
    """Ворота replay: ok; все четыре линии прочитаны и равны (sha256 четырёх файлов × три окна); ключи и vid линий = заморозке."""
    if gr.get("ok") is not True:
        stop("gate-replay.json: ok ≠ true")
    if set(gr["cells"]) != set(cells):
        stop("gate-replay.json: линии %s ≠ %s" % (sorted(gr["cells"]), sorted(cells)))
    for i, c in cells.items():
        x = gr["cells"][i]
        if x["status"] != "read" or x["equal"] is not True or x["vid"] != c["vid"] or x["keys"] != c["keys"] or sorted(x["windows"]) != sorted(WINDOWS):
            stop("gate-replay.json: линия %s: статус %s, равна %s, vid %s (нужен %s), окна %s" % (i, x["status"], x["equal"], x["vid"], c["vid"], sorted(x["windows"])))
        if any(len(w["files"]) != 4 or not w["equal"] for w in x["windows"].values()):
            stop("gate-replay.json: линия %s: не четыре файла × равны на каждом окне" % i)


def build(a):
    fd = a.freeze_dir
    t674 = load(os.path.join(fd, fw.T674_NAME))
    _t, _g, cells = g708.load_cells(argparse.Namespace(freeze_dir=fd))
    gf, gr = load(a.gate_feat), load(a.gate_replay)
    if gf["flow_mode"] != gr["flow_mode"]:
        stop("flow_mode ворот feat (%s) ≠ replay (%s)" % (gf["flow_mode"], gr["flow_mode"]))
    b = t674["binary"]["freeze"]
    for name, g in (("feat", gf), ("replay", gr)):
        e = g["meta"]["engine_bin"]
        if e["md5"] != b["md5"] or e["sha256"] != b["sha256"]:
            stop("gate-%s.json: бинарь движка %s ≠ замороженному %s" % (name, e["md5"], b["md5"]))
    fbf, fbr = gf["meta"]["feature_bins"], gr["meta"]["feature_bins"]
    if set(fbf) != set(f708.FEAT_BIN_NAMES) or any(fbf[n]["md5"] != fbr[n]["md5"] or fbf[n]["sha256"] != fbr[n]["sha256"] for n in fbf):
        stop("бинари признаков ворот feat и replay различаются (или нет одного из %s)" % list(f708.FEAT_BIN_NAMES))
    share = check_feat(gf, cells)
    check_replay(gr, cells)
    detail = {i: {f: {"max_credited_share": gf["cells"][i]["max_daily_nan_share"][f]["share"], "day": gf["cells"][i]["max_daily_nan_share"][f]["day"],
                      "window": gf["cells"][i]["max_daily_nan_share"][f].get("window"), "rows": gf["cells"][i]["max_daily_nan_share"][f]["rows"], "nan": gf["cells"][i]["max_daily_nan_share"][f]["nan"]}
                  for f in share[i]} for i in share}
    out = {
        "version": fw.CAND5_VERSION,
        "protocol": "TK-708 (L-428, L-434): четыре линии TK-674 (L1, L2 — лонг с фильтром big1800 / cons60 / vpin1h, S1, S2 — шорт) в проверке вперёд; читаются на c539 теми же ключами движка и тем же бинарём, что в шлюзе TK-674; "
                    "признаки лонг-линий строятся вперёд `tk708_feat.py` (дамп потока → alpha-feat f12 → flow → tox → составной столбец) и сверены с таблицей исследования и parquet TK-671 (ворота feat) и с выходами шлюза (ворота replay)",
        "decision": "α новых линий нет (α = 0, описательно): решение координатора `thr_ty6ae9r46y` (25.11.2026) — линии TK-674 печатаются на точках без α и без слов «подтверждено / опровергнуто»; поправка α — только решением координатора до чтения точки; "
                    "охрана NaN жёсткая: линия на сутки «нет данных», если суточная доля пустых значений признака среди кандидатов суток выше максимальной суточной доли исследования (`nan_guard`)",
        "read_before_commit": "суток ≥ 14.11 не читалось: ворота feat и replay идут по окнам wa / wb / wc (сутки ≤ 13.11, `check_tree_days`), файл собран из результатов этих ворот и файлов TK-674",
        "base_freeze": link_of(fd, "tk626-frozen.json", "tk626-frozen-1"),
        "prev_candidate4": link_of(fd, fw.CAND4_NAME, fw.CAND4_VERSION, "решающая и читаемая вселенная `crypto` (c539), N лонга, возврат α — этого файла; линии TK-674 их не меняют"),
        "tk674": link_of(fd, fw.T674_NAME, fw.T674_VERSION, "ключи линий, фильтры, бинарь движка — единственный источник; файл не меняется; шлюз — tk674-gate-cells.json (sha256 %s)" % fw.sha256_text(os.path.join(fd, "tk674-gate-cells.json"))),
        "binary": {"path": b["path"], "md5": b["md5"], "sha256": b["sha256"]},
        "binary_note": "бинарь движка линий = `binary.freeze` файла tk674-frozen.json; клеточные бинари tk626 / tk638 / tk641 / tk670 не меняются",
        "feature_binaries": {n: {"path": v["path"], "md5": v["md5"], "sha256": v["sha256"]} for n, v in fbf.items()},
        "f12_days": a.f12_days, "flow_mode": gf["flow_mode"],
        "cells": {i: {k: c[k] for k in fw.CAND5_CELL_FIELDS} | {"filter": [[f, op, thr] for f, op, thr in c["filter"]]} for i, c in cells.items()},
        "cells_note": "L1 / L2 — лонг, ключи движка + `fa=<столбец> fo=lo fv=0.5` (столбец «проходит» из составного правила `tk674_run._pass`: пусто проходит); S1 / S2 — шорт, только ключи движка (таблиц нет); имена столбцов — как в шлюзе TK-674",
        "nan_guard": {"max_daily_share": share, "detail": detail,
                      "rule": "порог = максимальная суточная доля пустых значений признака среди кандидатов на ЗАЧЁТНЫХ сутках трёх окон исследования (ворота feat); на точке: доля пустых среди кандидатов суток > порога → линия на эти сутки «нет данных» (все кандидаты суток исключены, сделок нет); сутки и доли печатаются"},
        "gate": {"feat": {"ok": True, "result": file_ref(a.gate_feat, a.feat_srv_path)["path"], "result_sha256": file_ref(a.gate_feat, a.feat_srv_path)["sha256"],
                          "description": "ключи (sym, sig_ms) построителя вперёд ⊇ таблицы исследования (только здесь = 0), составной столбец равен таблице исследования, значения cons60 / big1800 / vpin1h равны parquet TK-671 по битам float32",
                          "windows": {i: {wn: {k: wx[k] for k in ("rows", "common", "only_here", "only_research", "column_mismatch", "not_in_parquet_among_common")} | {"value_mismatch": wx["value_mismatch"]}
                                          for wn, wx in gf["cells"][i]["windows"].items()} for i in gf["cells"]}},
                 "replay": {"ok": True, "result": file_ref(a.gate_replay, a.replay_srv_path)["path"], "result_sha256": file_ref(a.gate_replay, a.replay_srv_path)["sha256"],
                            "description": "прогон вперёд по прошлым окнам wa / wb / wc (c539, замороженные бинари): sha256 `{long,short}-{trades,daily,coinday,entryrej}.csv` четырёх линий = шлюзу TK-674 (`tk674-gate-cells.json`)",
                            "tree_files": gr["meta"]["tree_files"], "cells": {i: {"vid": c["vid"], "equal": c["equal"]} for i, c in gr["cells"].items()}}},
        "alpha_note": "α нет (α = 0): клетки `T674_L1` … `T674_S2` на точках печатаются «описательно»; Σ α заморозки не меняется (0,05)",
        "reading_rule": {"one_reading": "одно прочтение на точку: линии идут тем же прогоном `run_point`, что клетки пакета (маркер `READ-<точка>.started` — до движка; бинарь линий и три alpha-feat сверены ДО маркера)",
                         "skip": "любая охрана линии (бинарь признаков, ключи f12, locate_fail / natr_mismatch, no_key, сбой инструмента) — линия не читается, причина печатается, остальные идут",
                         "points": "30.11 (входы ≤ 26.11), 31.12, 31.01.2027, 28.02, 31.03.2027 — как у цепочки; префикс-охрана точек действует и для линий"},
        "tool_note": "`tk616_forward.py` с шестым звеном (`load_candidate5` / `apply_candidate5` / `resolve_candidate5`, `replay-tk708`), `tk708_feat.py`, `tk708_gate.py` и тесты `tests/test_tk708.py` — коммитом ДО чтения L1 (30.11); правил этого файла не меняет",
        "ref": "kb/task/TK-708.md; kb/task/TK-674.md; docs/research/freeze/tk674-frozen.json, tk674-gate-cells.json",
    }
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, ensure_ascii=False, default=float)
        f.write("\n")
    print("записан %s: линии %s, flow_mode %s, охрана NaN %s" % (a.out, list(cells), gf["flow_mode"], share))
    return 0


def merge_replay(parts):
    """Один результат ворот replay из частей по окнам (`tk708_gate.py replay --windows <окно>` — движок счёта на одно окно умещается в память srv, на три сразу — нет). Части: одно окно, тот же `meta` (деревья, бинари), режим потока, линии, ключи и vid; окна не повторяются.
    → результат ворот в формате `replay` (все окна). Любое расхождение частей — STOP."""
    first = parts[0]
    rest = lambda pt: {k: v for k, v in pt["meta"].items() if k != "tree_files"}   # noqa: E731  — число файлов деревьев своё у каждого окна: в результате — сумма
    out = {"meta": dict(first["meta"], tree_files=sum(int(pt["meta"]["tree_files"]) for pt in parts)), "flow_mode": first["flow_mode"], "info": {}, "cells": {}, "ok": True}
    for pt in parts:
        if rest(pt) != rest(first) or pt["flow_mode"] != first["flow_mode"] or set(pt["cells"]) != set(first["cells"]):
            stop("части replay: meta / режим потока / линии разные")
        for i, x in pt["cells"].items():
            y = out["cells"].setdefault(i, {"status": "read", "vid": x["vid"], "keys": x["keys"], "windows": {}, "equal": True})
            if x["vid"] != y["vid"] or x["keys"] != y["keys"]:
                stop("части replay: линия %s: vid / ключи разные" % i)
            dup = set(x["windows"]) & set(y["windows"])
            if dup:
                stop("части replay: линия %s: окно %s повторяется" % (i, sorted(dup)))
            y["windows"].update(x["windows"])
            y["status"] = y["status"] if y["status"] != "read" else x["status"]
            y["equal"] = y["equal"] and x["equal"]
            f = pt["info"][i]
            g = out["info"].setdefault(i, dict(f, windows={}))
            g["windows"].update(f.get("windows", {}))
            if f["status"] != "read":
                g["status"], g["reason"] = f["status"], f["reason"]
    out["ok"] = all(v["equal"] and v["status"] == "read" for v in out["cells"].values())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "merge-replay"])
    ap.add_argument("--parts", nargs="+", default=[], help="merge-replay: gate-replay-<окно>.json")
    ap.add_argument("--freeze-dir", default=os.path.join(HERE, "..", "..", "docs", "research", "freeze"))
    ap.add_argument("--gate-feat", default="")
    ap.add_argument("--gate-replay", default="")
    ap.add_argument("--feat-srv-path", default="", help="путь gate-feat.json на srv (по умолчанию — переданный)")
    ap.add_argument("--replay-srv-path", default="")
    ap.add_argument("--f12-days", type=int, default=f708.F12_DAYS)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "merge-replay":
        if len(a.parts) < 2:
            stop("merge-replay: нужно ≥ 2 частей (--parts)")
        res = merge_replay([load(x) for x in a.parts])
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
        print("replay (части %d): %s, окна %s -> %s" % (len(a.parts), "ok" if res["ok"] else "НЕ ok", sorted({w for x in res["cells"].values() for w in x["windows"]}), a.out))
        return 0 if res["ok"] else 3
    if not a.gate_feat or not a.gate_replay:
        stop("build: нужны --gate-feat и --gate-replay")
    return build(a)


if __name__ == "__main__":
    sys.exit(main())
