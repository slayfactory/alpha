"""TK-701 (единички): набор t701 — столбцы `tfv60`, `pre_n5`, `oi_c1` (`tk680_axes.T701_*`, `tk680_singles.t701_join`, пороги — только квантили, нуля нет). Запуск: python -m pytest tools/p42/tests/test_tk680_t701.py"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

P42 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def run(code, **env):
    e = dict(os.environ, TK674_REBASE="1", PYTHONUTF8="1", TK680_SET="t701", **env)
    r = subprocess.run([sys.executable, "-c", "import sys, json; sys.path.insert(0, %r)\n" % P42 + textwrap.dedent(code)], env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_names_and_lists():
    names, cover, nan, cl, eng, thr = run("""
        import tk680_axes as a, tk680_singles as s
        print(json.dumps([a.T701_NAMES, a.COVER_MIN, a.nan_cols() == a.T701_NAMES, a.column_list([]), a.engine_axes("L"), s.THR_NAME]))
    """)
    assert names == ["tfv60", "pre_n5", "oi_c1"] and cover == 0.7 and nan and eng == [] and thr == "thr680r.json"
    assert [[x[0], x[1], list(x[2])] for x in cl] == [[c, c, ["le", "ge"]] for c in names]


def test_thresholds_quantiles_only():
    out = run("""
        import numpy as np
        import tk680_singles as s
        v = np.arange(-200.0, 301.0)               # нуль внутри диапазона, но порога 0 нет: только q20 / q40 / q60 / q80
        x = np.arange(-100.0, 901.0)               # все квантили положительные — нуль не добавляется
        print(json.dumps([s._thresholds(v, "pre_n5"), s._thresholds(x, "tfv60")]))
    """)
    v, x = out
    for z in (v, x):
        assert [t[0] for t in z[0]] == ["q20", "q40", "q60", "q80"] and z[0] == z[1]
        assert [t[1] for t in z[0]] == sorted(t[1] for t in z[0])
    assert [t[1] for t in v[0]] == [-100.0, 0.0, 100.0, 200.0] and [t[1] for t in x[0]] == [100.0, 300.0, 500.0, 700.0]


def test_t701_join_keys(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    sys.path.insert(0, P42)
    os.environ["TK680_SET"] = "t701"
    import tk680_axes as a
    import tk680_singles as s
    for w in a.T681_WINS:
        pd.DataFrame(dict(sym=["AAA", "BBB"], sig_ms=[100, 200], tfv60=[0.5, float("nan")], pre_n5=[0.1, -0.2], oi_c1=[3.0, 4.0]), index=pd.Index([-1, -2], name="row_id")).to_parquet(tmp_path / ("b-%s-long.parquet" % w))
    a.T701_FAM = str(tmp_path / "b-%s-%s.parquet")
    out = s.t701_join("L")
    assert len(out) == 6 and set(out["win"]) == {"wa", "wb", "wc"} and (out["meta_base_pass"] == 1).all()
    assert list(out.columns) == ["sym", "meta_sig_ms", "tfv60", "pre_n5", "oi_c1", "win", "meta_base_pass"]
    assert out[(out["win"] == "wa") & (out["sym"] == "AAA")]["tfv60"].iloc[0] == 0.5 and out[(out["win"] == "wb") & (out["sym"] == "BBB")]["tfv60"].isna().all()
    pd.DataFrame(dict(sym=["AAA", "AAA"], sig_ms=[100, 100], tfv60=[1.0, 2.0], pre_n5=[0.0, 0.0], oi_c1=[0.0, 0.0])).to_parquet(tmp_path / "b-wa-long.parquet")      # дубль ключа — ошибка
    with pytest.raises(SystemExit):
        s.t701_join("L")
    pd.DataFrame(dict(sym=["AAA"], sig_ms=[100], pre_n5=[0.0], tfv60=[1.0], oi_c1=[0.0])).to_parquet(tmp_path / "b-wa-long.parquet")                                  # порядок столбцов не тот — ошибка
    with pytest.raises(SystemExit):
        s.t701_join("L")
