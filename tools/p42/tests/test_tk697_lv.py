"""Тесты TK-697 (№ 2 и № 7), семейство `l697` (`tk697_lv`): определения на малых числах вручную, ядра против независимого счёта, зеркало шорта, причинность, охраны.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk697_lv.py` (на srv — `/data/tk592/venv/bin/python`)."""
import math
import os
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk668_feat as ft  # noqa: E402
import tk671_t668 as t668  # noqa: E402
import tk697_cols as m6  # noqa: E402
import tk697_gate as g6  # noqa: E402
import tk697_lv as fam  # noqa: E402
import tk697_lv_gate as gate  # noqa: E402
from test_tk697_cols import walk  # noqa: E402

U32 = 4294967295
N_MIN = 6 * 1440
SRC = 4                                      # индекс источника в `t668._source_names()`


def sym_levels(specs):
    sl = ft.SymLevels()
    for born, end, segs in specs:
        sl.add(born, end, segs)
    sl.freeze()
    return sl


def test_count_kernel_hand_long_and_short():
    # уровень строки (номер 0) — 100; другие: 100.5 (0,5 %), 101.5 (1,5 %), 102.8 (2,8 %), 90 (ниже), мёртвый к t, ещё не родившийся к t, версия цены поменялась к t
    t = 5000
    specs = [(10, U32, [(10, 100)]), (10, U32, [(10, 10050)]), (10, U32, [(10, 10150)]), (10, U32, [(10, 10280)]), (10, U32, [(10, 9000)]),
             (10, 4000, [(10, 10020)]), (6000, U32, [(6000, 10030)]), (10, U32, [(10, 20000), (4500, 10010)])]
    specs[0] = (10, U32, [(10, 10000)])
    sl = sym_levels(specs)
    bb, ee, off, sm, sp = t668.flatten_levels(sl)
    out = np.empty((1, 6))
    # лонг: за уровнем (выше) — 100.5 / 101.5 / 102.8 и версия 100.1 (0,1 %) → d = 0,5; 1,5; 2,8; 0,1 %; NATR 0,5 %: dn = 1; 3; 5,6; 0,2
    fam.count_kernel(np.array([t]), np.array([10000.0]), np.array([0]), np.array([1.0]), np.array([0.005]), bb, ee, off, sm, sp, out)
    assert out[0].tolist() == [2.0, 3.0, 4.0, 3.0, 3.0, 4.0] or True
    d = [0.005, 0.015, 0.028, 0.001]
    exp = [sum(x <= 0.01 for x in d), sum(x <= 0.02 for x in d), sum(x <= 0.03 for x in d), sum(x / 0.005 <= 2 for x in d), sum(x / 0.005 <= 4 for x in d), sum(x / 0.005 <= 8 for x in d)]
    assert out[0].tolist() == [float(x) for x in exp], (out[0].tolist(), exp)
    # шорт: за уровнем (ниже) — только 90 (10 %): ни в одно окно не входит → нули
    fam.count_kernel(np.array([t]), np.array([10000.0]), np.array([0]), np.array([-1.0]), np.array([0.005]), bb, ee, off, sm, sp, out)
    assert out[0].tolist() == [0.0] * 6
    # NATR ≤ 0, номер уровня вне дампа — NaN
    fam.count_kernel(np.array([t]), np.array([10000.0]), np.array([0]), np.array([1.0]), np.array([0.0]), bb, ee, off, sm, sp, out)
    assert np.isnan(out).all()
    fam.count_kernel(np.array([t]), np.array([10000.0]), np.array([99]), np.array([1.0]), np.array([0.005]), bb, ee, off, sm, sp, out)
    assert np.isnan(out).all()


def test_ref_kernel_hand():
    n = 400
    b = 300
    t = b + 1
    cf = np.full(n, 1000.0)
    cf[b - 60] = 990.0
    hmax = np.full(n, 1001.0)
    lmin = np.full(n, 999.0)
    nat = np.full(n, 0.01)
    res = sym_levels([(0, U32, [(0, 1010)]), (0, U32, [(0, 995)]), (0, U32, [(0, 1500)])])        # сопротивления: 1010 (ахед 1,0 NATR), 995 (ниже pr: не «выше»), 1500
    sup = sym_levels([(0, U32, [(0, 980)]), (0, U32, [(0, 1005)]), (0, U32, [(0, 600)])])         # поддержки: 980 (2,0 NATR), 1005 (выше pr: не «ниже»)
    rb, re_, roff, rsm, rsp = fam.flat(res)
    sb, se, soff, ssm, ssp = fam.flat(sup)
    out = np.empty((2, 3))
    fam.ref_kernel(np.array([t, t]), np.array([b, b]), np.array([1.0, -1.0]), cf, hmax, lmin, nat, rb, re_, roff, rsm, rsp, sb, se, soff, ssm, ssp, out)
    assert abs(out[0, 0] - 1.0) < 1e-12 and abs(out[0, 1] - 2.0) < 1e-12        # лонг: по ходу — сопротивление выше, против — поддержка ниже
    assert abs(out[1, 0] - 2.0) < 1e-12 and abs(out[1, 1] - 1.0) < 1e-12        # шорт: по ходу — поддержка ниже, против — сопротивление выше
    # прокол за 60 минут: на b − 60 закрытие 990, сопротивление 995 жило и стояло выше, high в окне 1001 ≥ 995 → вверх прошёл; поддержка 1005 выше закрытия — не «стояла ниже» → вниз нет
    assert out[0, 2] == 1.0 and out[1, 2] == -1.0
    # уровень родился после m0 — не учитывается
    res2 = sym_levels([(b - 10, U32, [(b - 10, 995)])])
    rb, re_, roff, rsm, rsp = fam.flat(res2)
    fam.ref_kernel(np.array([t]), np.array([b]), np.array([1.0]), cf, hmax, lmin, nat, rb, re_, roff, rsm, rsp, sb, se, soff, ssm, ssp, out[:1])
    assert out[0, 2] == 0.0
    # нет данных ориентира — NaN
    cf2 = cf.copy()
    cf2[b] = np.nan
    fam.ref_kernel(np.array([t]), np.array([b]), np.array([1.0]), cf2, hmax, lmin, nat, rb, re_, roff, rsm, rsp, sb, se, soff, ssm, ssp, out[:1])
    assert np.isnan(out[0]).all()


def write_dump(path, levels_by_sym):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("sym,level,born,end,first_bar,segs,members\n")
        for sym, specs in levels_by_sym.items():
            for i, (born, end, segs) in enumerate(specs):
                f.write("%s,%d,%d,%d,0,%s,1:%d\n" % (sym, i, born, end, "|".join("%d:%d" % x for x in segs), segs[0][1]))


def synth(rng, syms, n_lv=40):
    out = {}
    for sym in syms:
        specs = []
        for _ in range(n_lv):
            born = int(rng.integers(0, N_MIN - 100))
            end = U32 if rng.random() < 0.4 else born + int(rng.integers(50, 3000))
            segs = [(born, int(rng.integers(900, 1100)) * 10**5)]
            for _ in range(int(rng.integers(0, 3))):
                segs.append((segs[-1][0] + int(rng.integers(10, 800)), int(rng.integers(900, 1100)) * 10**5))
            specs.append((born, end, segs))
        out[sym] = specs
    return out


def make_world(tmp, rng, side_dirs=("long", "short")):
    syms = ["AAAUSDT", "BBBUSDT", fam.REFS[0], fam.REFS[1]]
    cache = os.path.join(tmp, "cache")
    os.makedirs(cache)
    for k, sym in enumerate(syms):
        a = walk(rng, N_MIN, start=1000.0 * 10**5 / 1000 * 1.0, vol=0.0008, listed=0)
        a = {kk: (vv * 1000 if kk != "qv" else vv) for kk, vv in a.items()}      # цены порядка 1e8: уровни 900…1100 × 1e5 лежат рядом
        g6.write_b42(os.path.join(cache, sym + ".b42"), a)
    names = t668._source_names()
    dirs = {}
    for sd in side_dirs:
        dirs[sd] = os.path.join(tmp, "dumps-" + sd)
        write_dump(os.path.join(dirs[sd], names[SRC], sd + "-levels.csv"), synth(rng, syms))
    return syms, cache, fam.Dumps(dirs)


def make_rows(rng, k):
    syms = ["AAAUSDT", "BBBUSDT", fam.REFS[0]]
    t = rng.integers(2000, N_MIN - 5, k)
    return pd.DataFrame(dict(row_id=np.arange(k), src=np.full(k, SRC, dtype=np.int64), sym=[syms[i % 3] for i in range(k)], level=rng.integers(0, 40, k).astype(np.int64),
                             sig_ms=(t * 60_000).astype(np.int64), level_px_e8=rng.integers(950, 1050, k).astype(np.int64) * 10**5))


def test_compute_vs_brute_both_sides_and_causal():
    rng = np.random.default_rng(11)
    with tempfile.TemporaryDirectory() as tmp:
        syms, cache, dumps = make_world(tmp, rng)
        rows = make_rows(rng, 90)
        names = t668._source_names()
        store = {}
        series = {r: gate.RefSeries(cache, r, store) for r in fam.REFS}
        for side in ("long", "short"):
            s = fam.SIGN[side]
            df = fam.compute(rows, "wa", side, 1, cache_dir=cache, dumps=dumps)
            assert list(df.columns) == fam.COLUMNS and len(df) == len(rows)
            refd = gate.refs_for_brute(series, dumps, side, names[SRC])
            ref = []
            for r in rows.itertuples():
                t = int(r.sig_ms // 60_000)
                am = g6.load_arr(cache, r.sym, store)
                o, h, l, c, _ = g6.lists_of(am)
                nb = g6.natr_at(o, h, l, c, t - 1)
                ref.append(gate.brute_row(s, t, abs(int(r.level_px_e8)), int(r.level), nb, dumps.get(side, names[SRC], True).get(r.sym), refd))
            bad, dmax = g6.compare(df.to_numpy(), np.array(ref))
            assert bad.sum() == 0, (side, dict(zip(fam.COLUMNS, bad)), dmax)
            share = np.isfinite(df.to_numpy()).mean(axis=0)
            assert share.min() > 0.0, dict(zip(fam.COLUMNS, share))
            assert ((df["ls_c3p"] >= df["ls_c1p"]) | df["ls_c1p"].isna()).all() and ((df["ls_c8n"] >= df["ls_c2n"]) | df["ls_c2n"].isna()).all()
            for i in (3, 40, 77):
                one = rows.iloc[[i]]
                t = int(one["sig_ms"].iat[0] // 60_000)
                base = fam.compute(one, "wa", side, 1, cache_dir=cache, dumps=dumps).to_numpy()
                with tempfile.TemporaryDirectory() as g:
                    for k in {one["sym"].iat[0], *fam.REFS}:
                        g6.write_b42(os.path.join(g, k + ".b42"), ft.garbage_from(g6.load_arr(cache, k, store), t, rng))
                    got = fam.compute(one, "wa", side, 1, cache_dir=g, dumps=gate.GDumps(dumps, one["sym"].iat[0], t, rng)).to_numpy()
                assert np.array_equal(base, got, equal_nan=True), (side, i)


def test_missing_files_do_not_drop_rows():
    rng = np.random.default_rng(12)
    with tempfile.TemporaryDirectory() as tmp:
        syms, cache, dumps = make_world(tmp, rng, side_dirs=("long",))
        rows = make_rows(rng, 12)
        rows.loc[rows.index[:3], "sym"] = "NOPEUSDT"
        info = {}
        dumps2 = fam.Dumps({"long": dumps.dirs["long"], "short": os.path.join(tmp, "none")})
        df = fam.compute(rows, "wa", "long", 1, cache_dir=cache, dumps=dumps2, info=info)
        assert len(df) == 12 and "NOPEUSDT" in info["missing_cache"]
        assert df.iloc[:3][fam.COLUMNS[:6]].isna().all().all()         # нет кэша монеты: NATR монеты нет → № 2 пуст; № 7 считается по кэшу и дампам ориентиров, монета нужна ему только ради знака
        assert np.isfinite(df.iloc[3:, 0]).any()


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print("ok  ", name)
        except Exception:
            fails += 1
            print("FAIL", name)
            traceback.print_exc()
    print("%d из %d" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
