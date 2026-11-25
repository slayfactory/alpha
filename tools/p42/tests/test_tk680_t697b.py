"""TK-697 (вторая поправка): набор t697b — два столбца рынка `btc_mv15`, `mk_btc_s60` (`tk680_axes.T697B_*`, `tk680_singles.t697b_join`, порог 0). Запуск: python -m pytest tools/p42/tests/test_tk680_t697b.py"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

P42 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def run(code, **env):
    e = dict(os.environ, TK674_REBASE="1", PYTHONUTF8="1", TK680_SET="t697b", **env)
    r = subprocess.run([sys.executable, "-c", "import sys, json; sys.path.insert(0, %r)\n" % P42 + textwrap.dedent(code)], env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_names_and_lists():
    names, cover, nan, cl, eng, thr = run("""
        import tk680_axes as a, tk680_singles as s
        print(json.dumps([a.T697B_NAMES, a.COVER_MIN, a.nan_cols() == a.T697B_NAMES, a.column_list([]), a.engine_axes("L"), s.THR_NAME]))
    """)
    assert names == ["btc_mv15", "mk_btc_s60"] and cover == 0.7 and nan and eng == [] and thr == "thr680q.json"
    assert [[x[0], x[1], list(x[2])] for x in cl] == [["btc_mv15", "btc_mv15", ["le", "ge"]], ["mk_btc_s60", "mk_btc_s60", ["le", "ge"]]]


def test_thresholds_zero_added_sorted():
    out = run("""
        import numpy as np
        import tk680_singles as s
        v = np.arange(-200.0, 301.0)               # q20 = -100, q40 = 0 → порог 0 совпадает с квантилем (не повторяется)
        w = np.arange(-300.0, 201.0)               # q60 = 0 → тоже квантиль
        x = np.arange(-100.0, 901.0)               # все квантили положительные: нуль добавляется, первым
        y = np.arange(-900.0, 101.0)               # все квантили отрицательные: нуль добавляется, последним
        print(json.dumps([s._thresholds(v, "btc_mv15"), s._thresholds(w, "mk_btc_s60"), s._thresholds(x, "btc_mv15"), s._thresholds(y, "btc_mv15")]))
    """)
    v, w, x, y = out
    assert [t[0] for t in v[0]] == ["q20", "q40", "q60", "q80"] and v[0] == v[1]                         # q40 = 0 — нуль не дублируется
    assert [t[0] for t in w[0]] == ["q20", "q40", "q60", "q80"]
    assert [t[0] for t in x[0]] == ["z0", "q20", "q40", "q60", "q80"] and x[0][0][1] == 0.0 and x[0] == x[1]
    assert [t[0] for t in y[0]] == ["q20", "q40", "q60", "q80", "z0"] and y[0][-1][1] == 0.0
    for z in (x, y):
        assert [t[1] for t in z[0]] == sorted(t[1] for t in z[0])                                         # по возрастанию порога (соседи плато)


def test_t697b_join_keys(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    sys.path.insert(0, P42)
    os.environ["TK680_SET"] = "t697b"
    import tk680_axes as a
    import tk680_singles as s
    for w in a.T681_WINS:
        pd.DataFrame(dict(sym=["AAA", "BBB"], sig_ms=[100, 200], btc_mv15=[1.5, float("nan")], mk_btc_s60=[0.01, -0.02]), index=pd.Index([-1, -2], name="row_id")).to_parquet(tmp_path / ("b-%s-long.parquet" % w))
    a.T697B_FAM = str(tmp_path / "b-%s-%s.parquet")
    out = s.t697b_join("L")
    assert len(out) == 6 and set(out["win"]) == {"wa", "wb", "wc"} and (out["meta_base_pass"] == 1).all()
    assert list(out.columns) == ["sym", "meta_sig_ms", "btc_mv15", "mk_btc_s60", "win", "meta_base_pass"]
    assert out[(out["win"] == "wa") & (out["sym"] == "AAA")]["btc_mv15"].iloc[0] == 1.5 and out[(out["win"] == "wb") & (out["sym"] == "BBB")]["btc_mv15"].isna().all()
    pd.DataFrame(dict(sym=["AAA", "AAA"], sig_ms=[100, 100], btc_mv15=[1.0, 2.0], mk_btc_s60=[0.0, 0.0])).to_parquet(tmp_path / "b-wa-long.parquet")      # дубль ключа — ошибка
    with pytest.raises(SystemExit):
        s.t697b_join("L")
    pd.DataFrame(dict(sym=["AAA"], sig_ms=[100], mk_btc_s60=[0.0], btc_mv15=[1.0])).to_parquet(tmp_path / "b-wa-long.parquet")                          # порядок столбцов не тот — ошибка
    with pytest.raises(SystemExit):
        s.t697b_join("L")
