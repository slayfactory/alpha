#!/usr/bin/env python3
"""TK-691 (§13.77, PS-988): `tk691_n24.py` (порог N лонга на c539 по правилу tk638) и `tk691_gate.py` (ворота c539: охраны, согласованность `mk2`). Синтетика; цепочка заморозок — реальные файлы репозитория.
Движок-ворота (`ds`, `n24`) на синтетике не запускаются — только их охраны до запуска; сами прогоны — юнит на srv. Запуск: `python3 tools/p42/tests/test_tk691_gate.py`."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import retitr_full as rf  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk616_forward as fw  # noqa: E402
import tk638_run as t8  # noqa: E402
import tk691_gate as g  # noqa: E402
import tk691_n24 as n24  # noqa: E402

FREEZE_DIR = os.path.dirname(os.path.abspath(fw.FREEZE))
DAY = 86_400_000


@contextlib.contextmanager
def tmp():
    p = tempfile.mkdtemp(prefix="tk691g-")
    try:
        yield p
    finally:
        shutil.rmtree(p, ignore_errors=True)


def write_counts(root, rows_of):
    """`<root>/h775/<окно>-L.csv` (выход `alpha-feat tcount`); rows_of(окно) = [(монета, sig_ms, n60, n24, m60, m24)]."""
    os.makedirs(os.path.join(root, "h775"))
    for w in st.TRADE_WINDOWS:
        with open(os.path.join(root, "h775", "%s-L.csv" % w.name), "w", encoding="utf-8") as f:
            f.write("sym,sig_ms,n60,n24,m60,m24\n")
            for r in rows_of(w):
                f.write("%s,%d,%d,%d,%d,%d\n" % r)


def rows_for(w):
    """Три сигнала на монету в зачётном диапазоне окна (сутки зачёта + k), один — до зачёта (в порог не входит), у `ZZZUSDT` m24 = 1 (исключён)."""
    lo = (w.lod - w.t0d).days * DAY
    out = []
    for i, c in enumerate(("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "TRFUSDT")):
        for k in range(3):
            out.append((c, lo + k * 3_600_000 + i * 60_000, 1000, (i + 1) * 100_000 + k * 7_777, 0, 0))
        out.append((c, 5 * 3_600_000, 1000, 9_999_999, 0, 0))                                # до зачётного диапазона
    out.append(("ZZZUSDT", lo + 1, 1000, 0, 0, 1))
    return out


def test_n24_threshold_is_the_frozen_rule_on_the_coin_subset():
    with tmp() as t:
        write_counts(os.path.join(t, "feat"), rows_for)
        syms = os.path.join(t, "symbols.txt")
        open(syms, "w", encoding="utf-8").write("AAAUSDT,BBBUSDT,CCCUSDT,DDDUSDT\n")
        out_sub, out_all = os.path.join(t, "sub.json"), os.path.join(t, "all.json")
        assert n24.main(["--symbols", syms, "--counts-root", os.path.join(t, "feat"), "--out", out_sub]) == 0
        assert n24.main(["--counts-root", os.path.join(t, "feat"), "--out", out_all]) == 0
        sub, full = json.load(open(out_sub, encoding="utf-8")), json.load(open(out_all, encoding="utf-8"))
        vals = [(i + 1) * 100_000 + k * 7_777 for i in range(4) for k in range(3)]                  # зачётные, m24 = 0, монеты списка
        exp_raw = float(np.quantile(np.array(vals * 3, dtype=float), 0.6, method="linear"))
        assert sub["n_scored"] == 36 and abs(sub["q_raw_0.60"] - exp_raw) < 1e-6 and sub["N"] == float("%.2g" % exp_raw)
        assert sub["n_symbols"] == 4 and sub["universe"] == "symbols.txt" and sub["symbols_sha256"] == n24.sha(syms) and sub["m24_rows"] == {w.name: 0 for w in st.TRADE_WINDOWS}
        assert full["n_scored"] == 45 and full["n_symbols"] is None and full["N"] != sub["N"]                      # монета вне списка двигает порог — фильтр по списку действует
        assert full["inputs"]["wa"]["rows"] == full["inputs"]["wa"]["rows_kept"] and sub["inputs"]["wa"]["rows_kept"] < sub["inputs"]["wa"]["rows"]
        assert sub["N"] == t8.compute_thresholds(n24.tabs_of(os.path.join(t, "feat"), n24.read_symbols(syms))[0])["N24"]["q"]["0.60"]


def test_n24_reads_symbols_like_the_engine_commas_and_newlines():
    with tmp() as t:
        p = os.path.join(t, "s.txt")
        open(p, "w", encoding="utf-8").write("AAAUSDT,BBBUSDT\nCCCUSDT,\n")
        assert n24.read_symbols(p) == {"AAAUSDT", "BBBUSDT", "CCCUSDT"}


def test_chain_loader_stops_on_a_missing_file_and_main_returns_code_3():
    with tmp() as t:
        g.paths_of(FREEZE_DIR)
        for name in os.listdir(FREEZE_DIR):
            if name in ("tk626-frozen.json", fw.CAND_NAME, fw.CAND2_NAME):
                shutil.copy(os.path.join(FREEZE_DIR, name), t)
        try:
            g.paths_of(t)                                                                           # нет tk670-frozen.json
            raise AssertionError("ожидалась охрана")
        except g.GuardError as e:
            assert "нет файла заморозки" in str(e)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert g.main(["mk2", "--freeze-dir", t, "--gmk-root", os.path.join(t, "gmk")]) == 3
        assert "ОСТАНОВКА (охрана)" in err.getvalue()


def test_mk2_agreement_compares_every_file_byte_for_byte_against_the_tk674_cell():
    with tmp() as t:
        p, fz, c1, c2, c3 = g.load_chain(FREEZE_DIR)
        vid = rf.Req("L", c2["cells"][fw.CAND2_CELL]["keys"], False).vid
        ref_root = os.path.join(t, "ref", "%s")
        old_ref, g.MK2_REF = g.MK2_REF, ref_root
        try:
            for w in rf.WINDOWS:
                for base in (os.path.join(t, "gmk", "out", w.name, vid), ref_root % w.name):
                    os.makedirs(base)
                    for f in g.FILES_MK2:
                        open(os.path.join(base, f), "w", encoding="utf-8").write("%s %s\n" % (w.name, f))
            with contextlib.redirect_stdout(io.StringIO()):
                assert g.main(["mk2", "--freeze-dir", FREEZE_DIR, "--gmk-root", os.path.join(t, "gmk")]) == 0
            res = json.load(open(os.path.join(t, "gmk", "g626c-eq.json"), encoding="utf-8"))
            assert res["ok"] is True and res["vid"] == vid and set(res["windows"]) == {w.name for w in rf.WINDOWS}
            open(os.path.join(t, "gmk", "out", "wb", vid, "long-trades.csv"), "a").write("x\n")      # один байт различия
            os.remove(os.path.join(t, "gmk", "out", "wc", vid, "long-maker.json"))                   # и один отсутствующий файл
            with contextlib.redirect_stdout(io.StringIO()):
                assert g.main(["mk2", "--freeze-dir", FREEZE_DIR, "--gmk-root", os.path.join(t, "gmk")]) == 3
            res = json.load(open(os.path.join(t, "gmk", "g626c-eq.json"), encoding="utf-8"))
            assert res["ok"] is False and res["windows"]["wa"]["equal"] is True and res["windows"]["wb"]["files"]["long-trades.csv"]["equal"] is False
            assert res["windows"]["wc"]["files"]["long-maker.json"]["sha256"] is None and res["windows"]["wc"]["equal"] is False
        finally:
            g.MK2_REF = old_ref


def test_n24_gate_stops_on_a_foreign_binary_before_any_engine_run():
    with tmp() as t:
        fake = os.path.join(t, "alpha-mk")
        open(fake, "wb").write(b"not the frozen binary")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert g.main(["n24", "--freeze-dir", FREEZE_DIR, "--bin", fake, "--root", os.path.join(t, "g638a")]) == 3
        assert "ОСТАНОВКА (охрана)" in err.getvalue() and not os.path.exists(os.path.join(t, "g638a", "gate.json"))


def test_ds_and_n24_gates_pin_the_frozen_candidate_cells_and_the_tk674_references():
    p, fz, c1, c2, c3 = g.load_chain(FREEZE_DIR)
    assert c1["filter"]["N"] == 220000 and c1["filter"]["name"] == "N24_q60" and g.CELLS674.endswith("/cells")
    assert g.MK2_REF % "wa" == "/data/p42-tk674c/cells/wa-L/pc8c770ba7b/mk2" and g.LX1_REF % "wc" == "/data/p42-tk674c/cells/wc-S/p75fc245d9a/lx1"
    assert g.MK2_REF.count("%s") == 1 and g.LX1_REF.count("%s") == 1
    assert rf.Req("L", c2["cells"][fw.CAND2_CELL]["keys"], False).vid == c2["cells"][fw.CAND2_CELL]["vid"]
    assert fw.ds_keys(fz["cells"]["S_base"]["keys"]) == c3["cells"][fw.CAND3_CELL]["keys"]


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
