"""П-42 §13.71 (TK-671): тесты склейки меток `tk671_data.attach_labels`. Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk671_data.py`."""
import os
import sys
import traceback

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tk671_data as d  # noqa: E402


def rows():
    return pd.DataFrame({"meta_src": [0, 0, 1, 1, 4], "meta_sym": ["A", "A", "B", "B", "A"], "meta_level": [7, 8, 7, 7, 7], "meta_sig_ms": [100, 100, 100, 200, 100]}, index=[10, 11, 12, 13, 14])


def trades(sym, level, sig, net, kind="stop"):
    n = len(sym)
    return pd.DataFrame({"sym": sym, "level": level, "sig_ms": sig, "entry_ms": np.array(sig) + 4, "exit_ms": np.array(sig) + 1000, "entry_px_e8": np.arange(n) + 5, "kind": [kind] * n, "net": net})


def test_join_by_src_sym_level_sigms():
    t0 = {0: trades(["A", "A"], [7, 8], [100, 100], [1.5, -2.0]), 1: trades(["B"], [7], [200], [3.0], "take")}
    y = d.attach_labels(rows(), t0, 1000)
    assert list(y["y_has"]) == [True, True, False, True, False]                   # (1,B,7,100) нет сделки; источник 4 без файла
    assert list(y.loc[[10, 11, 13], "y_net"]) == [1.5, -2.0, 3.0]
    assert y.loc[10, "y_entry_ms"] == 100 + 4 + 1000 and y.loc[10, "y_exit_ms"] == 100 + 1000 + 1000
    assert list(y.loc[[10, 13], "y_stop"]) == [1.0, 0.0] and np.isnan(y.loc[12, "y_stop"])
    assert y.loc[12, "y_net"] != y.loc[12, "y_net"] and y.loc[12, "y_exit_ms"] == -1


def test_other_source_trade_not_used():
    t0 = {1: trades(["A"], [7], [100], [9.0])}                                      # сделка источника 1 по ключу строки источника 0 — метки строке 10 не даёт
    y = d.attach_labels(rows(), t0, 0)
    assert not y.loc[10, "y_has"] and not y.loc[11, "y_has"]


def test_duplicate_keys_refused():
    t0 = {0: trades(["A", "A"], [7, 7], [100, 100], [1.0, 2.0])}
    try:
        d.attach_labels(rows(), t0, 0)
    except AssertionError:
        return
    raise AssertionError("дубли ключа должны отвергаться")


def test_glab_corrected_definition(tmp=None):
    """glab: найдено = есть кандидат по (sym, sig_ms, entry_px_e8); точно = среди кандидатов совпали net/entry/exit/kind; сделка вне зачётного диапазона не считается; ненайденная — в `missing` без net."""
    import tempfile
    w = d.WINS["wa"]
    lo_ms = (w.lod - w.t0d).days * 86_400_000
    rows_ = pd.DataFrame({"win": "wa", "sym": ["A", "A", "A", "B"], "meta_sig_ms": [lo_ms + 10, lo_ms + 10, lo_ms + 20, lo_ms + 30], "meta_src": [0, 1, 0, 0],
                          "meta_level": [7, 8, 7, 7], "y_has": [True, True, True, False], "y_net": [1.0, 5.0, 2.0, np.nan],
                          "y_entry_ms": [lo_ms + 14 + w.t0_ms, lo_ms + 14 + w.t0_ms, lo_ms + 24 + w.t0_ms, -1], "y_exit_ms": [lo_ms + 99 + w.t0_ms] * 3 + [-1],
                          "y_entry_px_e8": [500, 500, 600, -1], "y_stop": [1.0, 0.0, 1.0, np.nan]})
    tr = pd.DataFrame({"sym": ["A", "A", "B", "A"], "level": [7, 7, 7, 7], "sig_ms": [lo_ms + 10, lo_ms + 20, lo_ms + 30, lo_ms - 5], "entry_ms": [lo_ms + 14, lo_ms + 24, lo_ms + 34, 1],
                       "exit_ms": [lo_ms + 99] * 3 + [2], "entry_px_e8": [500, 600, 700, 800], "kind": ["stop", "stop", "stop", "stop"], "net": [1.0, 2.0, 3.0, 4.0]})
    with tempfile.TemporaryDirectory() as td:
        rows_.to_parquet(f"{td}/long.parquet")
        for win in d.WINS:
            os.makedirs(f"{td}/tw/{win}/out/L_ind")
            (tr if win == "wa" else tr.iloc[:0]).to_csv(f"{td}/tw/{win}/out/L_ind/long-trades.csv", index=False)
        old_read = d.read_trades
        d.read_trades = lambda path, cols=None: pd.read_csv(path)
        try:
            d.glab("long", td, f"{td}/tw")
        finally:
            d.read_trades = old_read
        import json
        r = json.load(open(f"{td}/long.glab.json"))
    c = r["cells"]["L_ind"]
    assert c["twin_trades"] == 3 and c["found"] == 2 and c["exact"] == 2              # сделка вне диапазона (sig_ms < lo) не считается; кандидаты двух источников у (A, lo+10): точное совпадение среди них
    assert len(r["missing"]) == 1 and r["missing"][0]["sym"] == "B" and r["missing"][0]["rows_same_sym_sig"] == 1 and r["missing"][0]["rows_labeled"] == 0 and "net" not in r["missing"][0]
    assert not r["pass"]                                                               # found < 200


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print("ok  ", n)
        except Exception:
            bad += 1
            print("FAIL", n)
            traceback.print_exc()
    print("%d тестов, провалов %d" % (len(tests), bad))
    sys.exit(1 if bad else 0)
