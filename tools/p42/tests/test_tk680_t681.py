"""TK-680 §13.73.14: набор t681 — оси единичек ключей и столбцов TK-681 (`tk680_axes.t681_axes`, `FAST_NAMES`), ключи клеток, fast_join. Запуск: python3 -m pytest tools/p42/tests/test_tk680_t681.py"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

P42 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
VOLQ = os.environ.get("TK681_VOLQ") or os.path.join(P42, "..", "..", "docs", "findings", "p42-titration-coverage-2026-11-22", "t681", "volq681.json")


def run(code, **env):
    e = dict(os.environ, TK674_REBASE="1", TK681_VOLQ=VOLQ, PYTHONUTF8="1", **env)
    r = subprocess.run([sys.executable, "-c", "import sys, json; sys.path.insert(0, %r)\n" % P42 + textwrap.dedent(code)], env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_fast_names_50_in_table_order():
    n = run("import tk680_axes as a; print(json.dumps(a.FAST_NAMES))", TK680_SET="t681")
    assert len(n) == 50 == len(set(n))
    assert n[:3] == ["tfv30", "tfn30", "vpin300"] and n[17] == "amih5m" and n[18:22] == ["accn1_60", "accv1_60", "accdn1_60", "accdv1_60"] and n[-1] == "accdv30_300"


def test_ea_is_base_e_over_median_times_multiplier():
    r = run("""
        import tk680_axes as a
        vq = a.t681_volq()
        print(json.dumps({s: {k: [a.t681_ea(vq, s, k, x) for x in a.T681_EA_MULT] for k in range(1, 9)} for s in 'LS'}))""", TK680_SET="t681")
    assert r["L"]["3"] == [0.794, 1.587, 3.175] and r["S"]["3"] == [0.397, 0.794, 1.587]       # 0,3 / 0,189 и 0,15 / 0,189
    assert r["L"]["8"][1] == 0.281 and r["S"]["5"] == [0.3, 0.6, 1.2]
    for s, e0 in (("L", 0.3), ("S", 0.15)):
        q50 = {1: 0.179, 2: 0.185, 3: 0.189, 4: 0.192, 5: 0.25, 6: 0.486, 7: 0.724, 8: 1.068}
        for k, v in r[s].items():
            assert abs(v[1] * q50[int(k)] - e0) < 0.0006 * q50[int(k)] + 1e-9    # ea хранится с точностью 0,001


def test_axes_shape_and_cells():
    r = run("""
        import tk680_axes as a
        out = {}
        for side in 'LS':
            ax_ = a.engine_axes(side)
            cells = {}
            for name, vals, base in ax_:
                for lab, patch in vals:
                    if lab != base:
                        cells.setdefault(a.make_cell(side, name, lab, patch)['ckey'], name)
            out[side] = dict(names=[x[0] for x in ax_], n=len(cells), bases=[x[2] for x in ax_])
        print(json.dumps(out))""", TK680_SET="t681")
    for s in "LS":
        names = r[s]["names"]
        assert len(names) == 22 and names[0] == "natr_p" and "nf3" in names and "nf2" in names and "nf1" in names and "nf4" in names and "nf8" in names
        assert names.count("ev1") == 1 and [n for n in names if n.startswith("ev")] == ["ev%d" % k for k in range(1, 9)]
        assert [n for n in names if n.startswith("sm_")] == ["sm_sl", "sm_cw", "sm_nx"]
        assert set(r[s]["bases"]) == {"off", "30"}
        assert r[s]["n"] == 68          # 70 значений вне базы − 2 общие клетки el = 0,05 / eh = 1 (= ev3 × 1)


def test_nf_thresholds_equal_volq_match():
    r = run("""
        import tk680_axes as a
        d = dict((n, v) for n, v, _ in a.engine_axes('L'))
        print(json.dumps({k: [p.get('natr_min') for lab, p in d['nf%d' % k] if lab != 'off'] for k in (1, 2, 4, 5, 6, 7, 8)}))""", TK680_SET="t681")
    assert r["1"] == [0.19, 0.39, 0.6] and r["5"] == [0.27, 0.54, 0.83] and r["8"] == [1.13, 2.19, 3.24] and r["2"] == r["4"] == [0.2, 0.4, 0.6]


def test_key_registration_and_parents_equal_main_set():
    new = run("""
        import tk674_axes as ax, tk680_axes as a
        line = ax.BASE_LINE('L')
        print(json.dumps(dict(c=ax.canon(line + ' nf=1 ev=3 ea=1.587 el=0.05 eh=1 sm=0'), sig=[k for k in ('nf', 'ev', 'ea', 'el', 'eh', 'sm') if k in ax.SIG_KEYS],
                              sm=a.make_cell('L', 'sm_sl', 'sm0.1', dict(sl=3.5, sm=0.1))['ckey'], parents=[a.make_cell('L', 'sm_' + n, 'sm0', p)['ckey'] for n, p in a.T681_SM_PARENT])))""", TK680_SET="t681")
    assert "el=" not in new["c"] and "eh=" not in new["c"] and "sm=" not in new["c"] and "nf=1" in new["c"] and "ea=1.587" in new["c"]
    assert new["sig"] == ["nf", "ev", "ea", "el", "eh"] and "sm=0.1" in new["sm"]
    main = run("""
        import tk680_axes as a
        d = dict((n, v) for n, v, _ in a.engine_axes('L'))
        pick = lambda n, lab: dict(d[n])[lab]
        print(json.dumps([a.make_cell('L', n, lab, pick(n, lab))['ckey'] for n, lab in (('sl', '3.5'), ('cw', 'w30m0.5'), ('nx', '3.5'))]))""")
    assert main == new["parents"]       # родители sm — те же клетки, что в основном наборе (в БД уже есть)


def test_fast_join_key_checks(tmp_path):
    try:
        import pandas as pd
    except Exception:
        pytest.skip("pandas недоступен на этой машине (проверено на srv)")
    sys.path.insert(0, P42)
    os.environ["TK680_SET"] = "t681"
    import tk680_axes as a
    import tk680_singles as s
    names = a.FAST_NAMES
    for w in a.T681_WINS:
        d = tmp_path / ("%s-long" % w)
        d.mkdir()
        pd.DataFrame(dict(row_id=[5, 9], sym=["AAA", "BBB"], sig_ms=[100, 200], level_px_e8=[1, 2])).to_csv(d / "eps-key.csv", index=False)
        fc = pd.DataFrame(dict(sym=["AAA", "BBB"], sig_ms=[100, 200], **{n: [float(i), float("nan")] for i, n in enumerate(names)}))
        fc.to_csv(d / "fast.csv", index=False)
    a.T681_FASTDIR = str(tmp_path / "%s-%s")
    df = pd.DataFrame(dict(win=["wa", "wb"], row_id=[5, 9], sym=["AAA", "BBB"], meta_sig_ms=[100, 200]))
    out = s.fast_join("L", df)
    assert list(out["tfv30"].isna()) == [False, True] and float(out["tfv30"].iloc[0]) == 0.0 and float(out["accdv30_300"].iloc[0]) == 49.0
    bad = pd.DataFrame(dict(win=["wa"], row_id=[5], sym=["ZZZ"], meta_sig_ms=[100]))
    with pytest.raises(SystemExit):
        s.fast_join("L", bad)
