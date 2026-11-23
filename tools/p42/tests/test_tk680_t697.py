"""TK-697: набор t697 — единички столбцов из разбора сделок трейдера пробоев (`tk680_axes.T697_*`, `tk680_singles.t697_join`, пороги). Запуск: python -m pytest tools/p42/tests/test_tk680_t697.py."""
import json
import os
import subprocess
import sys
import textwrap

import pytest

P42 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def run(code, **env):
    e = dict(os.environ, TK674_REBASE="1", PYTHONUTF8="1", TK680_SET="t697", **env)
    r = subprocess.run([sys.executable, "-c", "import sys, json; sys.path.insert(0, %r)\n" % P42 + textwrap.dedent(code)], env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_names_and_families():
    n = run("import tk680_axes as a; print(json.dumps([a.T697_NAMES, {k: len(v) for k, v in a.T697_FAMS.items()}, a.COVER_MIN, a.nan_cols() == a.T697_NAMES]))")
    names, sizes, cover, nan = n
    assert len(names) == 43 == len(set(names)) and sizes == {"m697": 16, "h697": 12, "l697": 12} and cover == 0.7 and nan
    assert names[-3:] == ["mk_rel_s60", "mk_rel_s240", "mk_rel_s1440"]


def test_family_names_match_modules():
    pytest.importorskip("numba")
    sys.path.insert(0, P42)
    import tk697_cols as m
    import tk697_hl as h
    import tk697_lv as lv
    os.environ["TK680_SET"] = "t697"
    import tk680_axes as a
    assert a.T697_FAMS["m697"] == list(m.COLUMNS) and a.T697_FAMS["h697"] == list(h.COLUMNS) and a.T697_FAMS["l697"] == list(lv.COLUMNS)


def test_column_list_bypasses_horizon_rule():
    cl = run("import tk680_axes as a; print(json.dumps(a.column_list([])))")
    assert len(cl) == 43 and all(tuple(x[2]) == ("le", "ge") and x[0] == x[1] for x in cl)
    assert {"pm_p72", "hl_hi_p365", "mk_rel_s1440", "mk_rel_sd"} <= {x[0] for x in cl}      # окна > 60 мин — исключение поправки


def test_thresholds():
    out = run("""
        import numpy as np
        import tk680_singles as s
        v = np.linspace(0.0, 10.0, 1001)
        cnt = s._thresholds(np.array([0.0, 1.0, 2.0, 5.0]), "ls_c1p")
        nat = s._thresholds(np.linspace(0.0, 50.0, 1001), "bl_btc_ahead_n")
        plain = s._thresholds(v, "pm_p24")
        flag = s._thresholds(np.array([-1.0, 0.0, 1.0] * 5), "bl_btc_brk60")
        zero = s._thresholds(np.array([0.0] * 700 + list(np.linspace(0.1, 3, 300))), "sw_d15")
        print(json.dumps([cnt, nat, plain, flag, zero]))
    """)
    cnt, nat, plain, flag, zero = out
    assert [x[1] for x in cnt[0]] == [0.0, 1.0, 2.0] and [x[1] for x in cnt[1]] == [1.0, 2.0, 3.0]
    assert [x[0] for x in nat[0]] == ["n1", "n2", "n4", "q20", "q40", "q60", "q80"] and nat[0] == nat[1]       # 1 / 2 / 4 NATR критика + квантили, по возрастанию порога
    assert [x[0] for x in plain[0]] == ["q20", "q40", "q60", "q80"] and plain[0] == plain[1]
    assert [x[1] for x in flag[0]] == [-1.0, 0.0] and [x[1] for x in flag[1]] == [0.0, 1.0]
    assert [x[1] for x in zero[0]] == sorted({x[1] for x in zero[0]}) and zero[0][0][1] == 0.0 and len(zero[0]) < 4        # пороги с равным значением не повторяются


def test_t697_join_key_checks(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    sys.path.insert(0, P42)
    os.environ["TK680_SET"] = "t697"
    import tk680_axes as a
    import tk680_singles as s
    for w in a.T681_WINS:
        pd.DataFrame(dict(row_id=[5, 9], sym=["AAA", "BBB"], sig_ms=[100, 200])).to_parquet(tmp_path / ("rows-%s-long.parquet" % w))
        for f, cols in a.T697_FAMS.items():
            pd.DataFrame({c: [float(i), float("nan")] for i, c in enumerate(cols)}, index=pd.Index([5, 9], name="row_id")).to_parquet(tmp_path / ("%s-%s-long.parquet" % (f, w)))
    a.T697_ROWS = str(tmp_path / "rows-%s-%s.parquet")
    a.T697_FAM = str(tmp_path / "%s-%s-%s.parquet")
    df = pd.DataFrame(dict(win=["wa", "wb"], row_id=[5, 9], sym=["AAA", "BBB"], meta_sig_ms=[100, 200]))
    out = s.t697_join("L", df)
    assert list(out["sw_d15"].isna()) == [False, True] and float(out["rn_n"].iloc[0]) == 15.0 and float(out["hl_lo_n365"].iloc[0]) == 11.0 and float(out["bl_eth_brk60"].iloc[0]) == 11.0
    with pytest.raises(SystemExit):
        s.t697_join("L", pd.DataFrame(dict(win=["wa"], row_id=[5], sym=["ZZZ"], meta_sig_ms=[100])))
