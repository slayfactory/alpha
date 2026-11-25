#!/usr/bin/env python3
"""TK-708: сборка `tk708-frozen.json` (`tk708_freeze.py build`) на синтетических результатах ворот против РЕАЛЬНОЙ цепочки tk626 → tk691 и файла отбора TK-674 репозитория: файл загружается `load_candidate5`,
охрана NaN и ворота следуют результатам, любое расхождение ворот останавливает сборку. Запуск: `python3 tools/p42/tests/test_tk708_freeze.py`."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import tk616_forward as fw  # noqa: E402
import tk708_feat as f7  # noqa: E402
import tk708_freeze as z  # noqa: E402
import tk708_gate as g  # noqa: E402

FD = os.path.dirname(os.path.abspath(fw.FREEZE))
WINS = ("wa", "wb", "wc")


@contextlib.contextmanager
def tmp():
    p = tempfile.mkdtemp(prefix="tk708z-")
    try:
        yield p
    finally:
        shutil.rmtree(p, ignore_errors=True)


def real_cells():
    return g.load_cells(argparse.Namespace(freeze_dir=FD))


def gates(share=0.05, mutate_feat=None, mutate_replay=None):
    """Результаты ворот feat / replay: все линии и окна пройдены, доля пустых `share` у каждого признака лонг-линий."""
    t674, _gate, cells = real_cells()
    b = t674["binary"]["freeze"]
    meta = {"engine_bin": {"path": b["path"], "md5": b["md5"], "sha256": b["sha256"]}, "tree_files": 4321,
            "feature_bins": {n: {"path": "/data/x/alpha-feat-" + n, "md5": ("%d" % (i + 1)) * 32, "sha256": ("%d" % (i + 4)) * 64} for i, n in enumerate(f7.FEAT_BIN_NAMES)}}
    gf = {"ok": True, "flow_mode": "flow", "meta": meta, "cells": {}}
    for i, c in cells.items():
        if c["side"] != "L":
            continue
        feats = [f for f, _, _ in c["filter"]]
        gf["cells"][i] = {"ok": True, "col": c["col"], "filter": c["text"], "max_daily_nan_share": {f: {"share": share, "day": "2026-07-03", "rows": 20, "nan": 1, "window": "wa"} for f in feats},
                          "windows": {w: {"ok": True, "rows": 500, "common": 480, "only_here": 0, "only_research": 20, "column_mismatch": 0, "not_in_parquet_among_common": 0, "value_mismatch": {f: 0 for f in feats}} for w in WINS}}
    gr = {"ok": True, "flow_mode": "flow", "meta": json.loads(json.dumps(meta)), "cells": {i: {"status": "read", "equal": True, "vid": c["vid"], "keys": c["keys"],
                                                                                          "windows": {w: {"equal": True, "files": {f: {"equal": True} for f in f7.GATE_FILES}} for w in WINS}} for i, c in cells.items()}}
    if mutate_feat:
        mutate_feat(gf)
    if mutate_replay:
        mutate_replay(gr)
    return gf, gr


def write(root, name, obj):
    p = os.path.join(root, name)
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return p


def build(root, gf, gr, out="tk708-frozen.json"):
    argv = ["build", "--freeze-dir", FD, "--gate-feat", write(root, "gate-feat.json", gf), "--gate-replay", write(root, "gate-replay.json", gr), "--out", os.path.join(root, out)]
    with contextlib.redirect_stdout(io.StringIO()):
        rc = z.main(argv)
    assert rc == 0
    return os.path.join(root, out)


def stops(root, needle, gf, gr):
    try:
        build(root, gf, gr, out="x-%d.json" % len(os.listdir(root)))
    except SystemExit as e:
        assert needle in str(e), "сообщение %r без %r" % (str(e), needle)
        return
    raise AssertionError("ожидалась остановка с %r" % needle)


def real_chain():
    fz0 = fw.load_freeze(fw.FREEZE)
    cp, cp2, cp3, cp4 = fw.candidate_path(fw.FREEZE), fw.candidate2_path(fw.FREEZE), fw.candidate3_path(fw.FREEZE), fw.candidate4_path(fw.FREEZE)
    c = fw.load_candidate(cp, fz0, fw.FREEZE)
    c2 = fw.load_candidate2(cp2, fz0, fw.FREEZE, c, cp)
    c3 = fw.load_candidate3(cp3, fz0, fw.FREEZE, c, cp, c2, cp2)
    c4 = fw.load_candidate4(cp4, fz0, fw.FREEZE, c, cp, c2, cp2, c3, cp3)
    fz = fw.apply_candidate3(fw.apply_candidate2(fw.apply_candidate(fz0, c), c2, True), c3)
    fz, _ = fw.apply_candidate4(fz, c, c4)
    return fz, c4, cp4


def test_built_file_loads_against_the_real_chain_with_the_gate_numbers():
    with tmp() as t:
        gf, gr = gates(share=0.0625)
        path = build(t, gf, gr)
        fz, c4, cp4 = real_chain()
        tp = fw.tk674_path(fw.FREEZE)
        c5 = fw.load_candidate5(path, fz, fw.FREEZE, c4, cp4, json.load(open(tp, encoding="utf-8")), tp)
        assert c5["version"] == "tk708-frozen-1" and list(c5["cells_run"]) == ["L1", "L2", "S1", "S2"] and "alpha" not in c5 and c5["flow_mode"] == "flow" and c5["f12_days"] == 80
        assert c5["nan_guard"]["max_daily_share"] == {"L1": {"big1800": 0.0625, "cons60": 0.0625, "vpin1h": 0.0625}, "L2": {"big1800": 0.0625, "vpin1h": 0.0625}}
        assert c5["cells_run"]["L1"]["col"] == "f354" and c5["cells_run"]["L2"]["col"] == "f216" and c5["cells_run"]["L1"]["keys"].endswith(" fa=f354 fo=lo fv=0.5") and c5["cells_run"]["S1"]["col"] == ""
        assert c5["feature_binaries"]["tox"] == {"path": "/data/x/alpha-feat-tox", "md5": "3" * 32, "sha256": "6" * 64} and c5["binary"]["md5"] == real_cells()[0]["binary"]["freeze"]["md5"]
        assert c5["gate"]["feat"]["ok"] and c5["gate"]["replay"]["ok"] and c5["gate"]["replay"]["tree_files"] == 4321 and set(c5["gate"]["feat"]["windows"]["L1"]) == set(WINS)
        assert fw.apply_candidate5(fz, c5) is fz and c5["base_freeze"]["sha256"] == fw.sha256_text(fw.FREEZE) and c5["tk674"]["sha256"] == fw.sha256_text(tp) and c5["prev_candidate4"]["sha256"] == fw.sha256_text(cp4)
        assert c5["nan_guard"]["detail"]["L1"]["cons60"]["window"] == "wa" and open(path, encoding="utf-8").read().endswith("}\n")


def test_every_gate_difference_stops_the_build():
    def feat(f):
        return lambda gf: f(gf)

    cases = [("gate-feat.json: ok", dict(mutate_feat=lambda gf: gf.__setitem__("ok", False))),
             ("gate-feat.json: линии", dict(mutate_feat=lambda gf: gf["cells"].pop("L2"))),
             ("столбец", dict(mutate_feat=lambda gf: gf["cells"]["L1"].__setitem__("col", "f999"))),
             ("окна", dict(mutate_feat=lambda gf: gf["cells"]["L1"]["windows"].pop("wc"))),
             ("только здесь 3", dict(mutate_feat=lambda gf: gf["cells"]["L2"]["windows"]["wb"].update(only_here=3, ok=False))),
             ("значения", dict(mutate_feat=lambda gf: gf["cells"]["L1"]["windows"]["wa"]["value_mismatch"].__setitem__("vpin1h", 2))),
             ("доли пустых", dict(mutate_feat=lambda gf: gf["cells"]["L1"]["max_daily_nan_share"].pop("cons60"))),
             ("доля вне [0; 1]", dict(mutate_feat=lambda gf: gf["cells"]["L1"]["max_daily_nan_share"]["big1800"].__setitem__("share", 1.2))),
             ("flow_mode", dict(mutate_replay=lambda gr: gr.__setitem__("flow_mode", "all"))),
             ("бинарь движка", dict(mutate_replay=lambda gr: gr["meta"]["engine_bin"].__setitem__("md5", "0" * 32))),
             ("бинарь движка", dict(mutate_feat=lambda gf: gf["meta"]["engine_bin"].__setitem__("sha256", "0" * 64))),
             ("бинари признаков", dict(mutate_replay=lambda gr: gr["meta"]["feature_bins"]["flow"].__setitem__("md5", "0" * 32))),
             ("gate-replay.json: ok", dict(mutate_replay=lambda gr: gr.__setitem__("ok", False))),
             ("gate-replay.json: линии", dict(mutate_replay=lambda gr: gr["cells"].pop("S2"))),
             ("статус", dict(mutate_replay=lambda gr: gr["cells"]["S1"].__setitem__("status", "skip"))),
             ("равна False", dict(mutate_replay=lambda gr: gr["cells"]["L1"].__setitem__("equal", False))),
             ("vid", dict(mutate_replay=lambda gr: gr["cells"]["L2"].__setitem__("vid", "0" * 12))),
             ("четыре файла", dict(mutate_replay=lambda gr: gr["cells"]["S2"]["windows"]["wb"]["files"].pop("daily.csv")))]
    for needle, kw in cases:
        with tmp() as t:
            gf, gr = gates(**kw)
            stops(t, needle, gf, gr)


def test_real_committed_file_loads_when_present():
    p = fw.candidate5_path(fw.FREEZE)
    if not os.path.exists(p):
        return
    fz, c4, cp4 = real_chain()
    tp = fw.tk674_path(fw.FREEZE)
    c5 = fw.load_candidate5(p, fz, fw.FREEZE, c4, cp4, json.load(open(tp, encoding="utf-8")), tp)
    assert list(c5["cells_run"]) == ["L1", "L2", "S1", "S2"] and c5["gate"]["feat"]["ok"] and c5["gate"]["replay"]["ok"]
    assert all(0 <= v <= 1 for x in c5["nan_guard"]["max_daily_share"].values() for v in x.values())


def main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        try:
            f()
            print("ok   ", n)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL ", n, "-", type(e).__name__, e)
            tb = traceback.extract_tb(e.__traceback__)
            print("       %s:%d  %s" % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].line))
    print("%d тестов, %d провалов" % (len(tests), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
