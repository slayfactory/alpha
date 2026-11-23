#!/usr/bin/env python3
"""TK-691: сборка `tk691-frozen.json` (`tk691_freeze.py build`) на синтетических результатах ворот против РЕАЛЬНОЙ цепочки tk626 → tk670 репозитория: файл загружается `load_candidate4`, α и `not_read` следуют исходу
ворот, расхождение согласованности `mk2` останавливает сборку. Запуск: `python3 tools/p42/tests/test_tk691_freeze.py`."""
from __future__ import annotations

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
import tk691_freeze as z  # noqa: E402

FD = os.path.dirname(os.path.abspath(fw.FREEZE))


@contextlib.contextmanager
def tmp():
    p = tempfile.mkdtemp(prefix="tk691z-")
    try:
        yield p
    finally:
        shutil.rmtree(p, ignore_errors=True)


def w(root, name, obj):
    p = os.path.join(root, name)
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return p


def inputs(root, n24_ok=True, mk2_pass=True, ds_ok=True, eq_ok=True):
    trees = {n: {"t0": "2026-06-17", "data_to": "2026-08-29", "aggtrades": {"coins": 539}, "klines": {"coins": 539}} for n in ("wa", "wb", "wc")}
    proof = {"ok": True, "now_utc": "2026-11-23T06:00:00Z", "last_day": "2026-11-13", "trees": trees, "read_markers": {"dirs": {}, "markers": []}, "tk691_results": {"by_top_dir": {}, "names_with_day_after_13_11": []}}
    n24 = {"universe": "symbols-crypto.txt", "N": 250000.0, "q_raw_0.60": 252755.2, "n_scored": 614, "n_all_rows": 861}
    n24h = {"universe": "all (h775, без фильтра)", "N": 220000.0, "q_raw_0.60": 217550.8, "n_scored": 914, "n_all_rows": 1000}
    g638 = {"ok": n24_ok, "N": 250000.0, "guards": {"no_key": 0, "m24_share": 0.0}}
    gmk = {"gmk": {"L_mk2": {"pass": mk2_pass, "g1": True, "g2": mk2_pass, "g3": True, "p": 0.01, "holm_threshold": 0.0125, "positive_months": 4, "counted_months": 5, "sum": 100.0, "n": 300}}}
    return dict(proof=w(root, "proof.json", proof), n24=w(root, "n24c.json", n24), n24_h775=w(root, "n24h.json", n24h), g638a=w(root, "g638a.json", g638), g670a=w(root, "g670a.json", {"ok": ds_ok}),
                g626c=w(root, "gmk.json", gmk), g626c_eq=w(root, "eq.json", {"ok": eq_ok}), describe=w(root, "desc.json", {"L_base": {"n": 298}}))


def argv_of(root, ins, name="tk691-frozen.json"):
    return ["build", "--freeze-dir", FD, "--out", os.path.join(root, name)] + sum((["--" + k.replace("_", "-"), v] for k, v in ins.items()), [])


def build(root, **kw):
    argv = argv_of(root, inputs(root, **kw))
    with contextlib.redirect_stdout(io.StringIO()):
        rc = z.main(argv)
    assert rc == 0
    return argv[argv.index("--out") + 1]


def load_chain():
    fz = fw.load_freeze(fw.FREEZE)
    cp, cp2, cp3 = fw.candidate_path(fw.FREEZE), fw.candidate2_path(fw.FREEZE), fw.candidate3_path(fw.FREEZE)
    c = fw.load_candidate(cp, fz, fw.FREEZE)
    c2 = fw.load_candidate2(cp2, fz, fw.FREEZE, c, cp)
    c3 = fw.load_candidate3(cp3, fz, fw.FREEZE, c, cp, c2, cp2)
    return fz, c, cp, c2, cp2, c3, cp3


def test_built_file_loads_against_the_real_chain_with_all_gates_passed():
    with tmp() as t:
        out = build(t)
        fz, c, cp, c2, cp2, c3, cp3 = load_chain()
        c4 = fw.load_candidate4(out, fz, fw.FREEZE, c, cp, c2, cp2, c3, cp3)
        assert c4["not_read"] == [] and c4["alpha"] == c3["alpha"] and c4["universe"]["read"] == ["crypto"] and c4["universe"]["decision"] == "crypto"
        assert c4["filter"]["N"] == 250000.0 and c4["filter"]["name"] == "N24_q60" and c4["ds_binary"] == c3["ds_binary"]
        assert c4["prev_candidate3"]["sha256"] == fw.sha256_text(cp3) and set(c4["gates"]) == {"L_n24", "L_mk2", "D_S"}
        assert c4["anchors"]["h775"]["L_mk2"]["trades"] == 327 and c4["anchors"]["h775"]["L_n24"]["trades"] == 181
        raw = open(out, "rb").read()
        assert raw.endswith(b"\n") and b"\r" not in raw


def test_failed_gates_mark_cells_not_read_and_return_alpha():
    with tmp() as t:
        out = build(t, n24_ok=False, mk2_pass=False, ds_ok=True)
        fz, c, cp, c2, cp2, c3, cp3 = load_chain()
        c4 = fw.load_candidate4(out, fz, fw.FREEZE, c, cp, c2, cp2, c3, cp3)
        assert c4["not_read"] == ["L_n24", "L_mk2"] and abs(c4["alpha"]["S_base"] - 0.03) < 1e-12 and c4["alpha"]["D_S"] == 0.005 and c4["alpha"]["L_n24"] == 0.0
    with tmp() as t:
        out = build(t, ds_ok=False)
        fz, c, cp, c2, cp2, c3, cp3 = load_chain()
        c4 = fw.load_candidate4(out, fz, fw.FREEZE, c, cp, c2, cp2, c3, cp3)
        assert c4["not_read"] == ["D_S"] and abs(c4["alpha"]["S_mk1"] - 0.01) < 1e-12 and c4["alpha"]["D_S"] == 0.0 and abs(sum(c4["alpha"].values()) - 0.05) < 1e-12


def test_build_stops_on_mk2_disagreement_with_the_tk674_db_and_on_a_wrong_n_table():
    with tmp() as t:
        try:
            z.main(argv_of(t, inputs(t, eq_ok=False), "x.json"))
            raise AssertionError("ожидалась остановка")
        except SystemExit as e:
            assert "согласованность mk2" in str(e) and not os.path.exists(os.path.join(t, "x.json"))
    with tmp() as t:
        ins = inputs(t)
        json.dump({"universe": "symbols-crypto.txt", "N": 220000.0, "q_raw_0.60": 1.0, "n_scored": 1, "n_all_rows": 1}, open(ins["n24"], "w"))
        try:
            z.main(argv_of(t, ins, "x.json"))
            raise AssertionError("ожидалась остановка")
        except SystemExit as e:
            assert "n24-c539.json" in str(e)


def test_inventory_lists_names_only_and_flags_days_after_13_11():
    with tmp() as t:
        os.makedirs(os.path.join(t, "g638a", "x"))
        os.makedirs(os.path.join(t, "code"))
        open(os.path.join(t, "g638a", "x", "a.csv"), "w").write("1\n")
        open(os.path.join(t, "code", "late-2026-11-20.txt"), "w").write("1\n")
        inv = z.inventory(t)
        assert inv["files"] == 1 and list(inv["by_top_dir"]) == ["g638a"] and inv["names_with_day_after_13_11"] == []
        open(os.path.join(t, "g638a", "x", "klines-2026-11-14.zip"), "w").write("")
        assert z.inventory(t)["names_with_day_after_13_11"] == ["g638a/x/klines-2026-11-14.zip"]


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
