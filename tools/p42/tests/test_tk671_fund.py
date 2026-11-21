"""Тесты §13.71 (TK-671), семейство `fund` (`tk671_fund.py`): R (круглое), F (фандинг), P (премия), C (слияние с 240-минутными уровнями) против оригинальных скалярных функций `tk666_feat`,
исключение собственного 240-уровня, причинность, охрана 14.11, NaN без данных. Синтетика (временные файлы), данных srv нет.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk671_fund.py` — выполняет все test_* и печатает итог."""
import csv
import io
import json
import os
import sys
import tempfile
import traceback
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk604_stats as st  # noqa: E402
import tk666_feat as ft  # noqa: E402
import tk671_fund as fd  # noqa: E402

WC = {x.name: x for x in st.TRADE_WINDOWS}["wc"]           # T0 = 2026-09-01 00:00 UTC = 1 788 220 800 000
T0 = WC.t0_ms
NAN = float("nan")


def same(a, b):
    return np.array_equal(np.asarray(a, dtype=float), np.asarray(b, dtype=float), equal_nan=True)


# ---- R ---------------------------------------------------------------------------------------------------------------------------------------

def test_round_vec_hand_computed():
    rho, mant = fd.round_vec([100_000_000, 150_000_000, 153_000_000, 1_664_000], [1_000_000] * 4)
    # p = 1,00 → шаг 10^7 (0,10): k = 10, dist 0; p = 1,50: dist 0; p = 1,53: k = 15, dist 3e6 → ρ = 3e6 · 1e8 / (1e6 · 1,53e8) = 3/1,53
    assert rho[0] == 0.0 and mant[0] == 1.0 and rho[1] == 0.0 and mant[1] == 1.5
    assert rho[2] == 3e6 * 1e8 / (1e6 * 153_000_000) and abs(rho[2] - 3 / 1.53) < 1e-12 and mant[2] == 1.53
    # p = 0,01664 (1 664 000 e8, 7 цифр): шаг 10^5, k = (1 664 000 + 50 000) // 100 000 = 17, dist = 36 000
    assert abs(rho[3] - 36_000 * 1e8 / (1e6 * 1_664_000)) < 1e-9 and mant[3] == 1.664


def test_round_vec_equals_scalar_original_and_edge():
    rng = np.random.default_rng(1)
    px = np.concatenate([rng.integers(-10 ** 13, 10 ** 13, size=3000), [0, 1, 9, 10, 11, 99, 100, 10 ** 15, 10 ** 17, 9 * 10 ** 17, -5, -123456789]])
    nat = np.concatenate([rng.integers(-5, 10 ** 8, size=3000), [0, 1, 10 ** 8, -1, 5, 5, 5, 5, 5, 5, 5, 7]])
    rho, mant = fd.round_vec(px, nat)
    for i in range(len(px)):
        r, m = ft.round_feat(px[i], nat[i])
        assert (np.isnan(r) and np.isnan(rho[i]) and np.isnan(m) and np.isnan(mant[i])) or (r == rho[i] and m == mant[i]), (i, px[i], nat[i], r, rho[i], m, mant[i])


# ---- F ---------------------------------------------------------------------------------------------------------------------------------------

def test_funding_vec_equals_scalar_original():
    t = np.array([0, 8, 16, 40, 48], dtype=np.int64) * 3_600_000 + np.array([5, 0, 7, 0, 3])
    inter = np.array([8.0, 8.0, 8.0, 8.0, 4.0])
    rate8 = np.array([1e-4, 2e-4, -3e-4, 5e-5, 1e-3])
    ms = np.concatenate([t, t + 1, t - 1, t + 16 * 3_600_000, t + 16 * 3_600_000 + 1, [-5, 0, 10 ** 12]]).astype(np.int64)
    f, a = fd.funding_vec(t, rate8, inter, ms)
    for i, x in enumerate(ms):
        ff, aa = ft.funding_at(t, rate8, inter, int(x))
        assert (np.isnan(ff) and np.isnan(f[i])) or ff == f[i], (i, x, ff, f[i])
        assert (np.isnan(aa) and np.isnan(a[i])) or aa == a[i], (i, x, aa, a[i])
    f, a = fd.funding_vec(np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0), np.array([1, 2], dtype=np.int64))
    assert np.isnan(f).all() and np.isnan(a).all()
    # руками: ровно через 16 ч после выплаты (2 интервала по 8 ч) ставка ещё действует (возраст не больше 2 интервалов), на 1 мс позже — NaN, возраст остаётся
    ff, aa = fd.funding_vec(t[:1], rate8[:1], inter[:1], np.array([t[0] + 16 * 3_600_000, t[0] + 16 * 3_600_000 + 1], dtype=np.int64))
    assert ff[0] == 1e-4 and np.isnan(ff[1]) and aa[0] == 16.0 and aa[1] > 16.0


# ---- P ---------------------------------------------------------------------------------------------------------------------------------------

def write_prem_day(root, sym, day, opens, closes, header=True):
    os.makedirs(os.path.join(root, sym), exist_ok=True)
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"] if header else []
    for o, c in zip(opens, closes):
        lines.append("%d,0,0,0,%.8f,0,%d,0,0,0,0,0" % (o, c, o + 59_999))
    with zipfile.ZipFile(os.path.join(root, sym, "%s-1m-%s.zip" % (sym, day)), "w") as z:
        z.writestr("%s-1m-%s.csv" % (sym, day), "\n".join(lines) + "\n")


def test_prem_vec_equals_scalar_original_with_gaps():
    day0 = T0 + 19 * 86_400_000                                  # 2026-09-20 00:00 UTC
    with tempfile.TemporaryDirectory() as tmp:
        old = (ft.PREM_OLD, ft.PREM_NEW, fd.PREM_ROOTS)
        ft.PREM_OLD, ft.PREM_NEW, fd.PREM_ROOTS = os.path.join(tmp, "a"), os.path.join(tmp, "b"), (os.path.join(tmp, "a"), os.path.join(tmp, "b"))
        try:
            rng = np.random.default_rng(3)
            for d, (day_open) in enumerate([day0 - 86_400_000, day0]):                       # 19.09 и 20.09; в 20.09 нет свечей 00:10–00:14 и 12:00–12:30; 21.09 файла нет
                opens = day_open + 60_000 * np.arange(1440)
                keep = np.ones(1440, bool)
                if d == 1:
                    keep[10:15] = False
                    keep[720:751] = False
                write_prem_day(ft.PREM_OLD, "AAAUSDT", "2026-09-%d" % (19 + d), opens[keep], rng.normal(0, 1e-3, int(keep.sum())))
            ms = np.sort(np.concatenate([day0 + rng.integers(-3600_000, 2 * 86_400_000, size=400), day0 + 60_000 * np.arange(0, 40), day0 + 60_000 * np.arange(715, 760) + 1,
                                         day0 + 86_400_000 + 60_000 * np.arange(0, 8)])).astype(np.int64)
            days = ["2026-09-19", "2026-09-20", "2026-09-21"]
            o, c, miss = fd.load_prem("AAAUSDT", days)
            assert miss == ["2026-09-21"]
            p, a = fd.prem_vec(o, c, ms)
            pr = ft.Prem()
            for i, x in enumerate(ms):
                pp, aa = pr.at("AAAUSDT", int(x))
                assert (np.isnan(pp) and np.isnan(p[i])) or (pp == p[i] and aa == a[i]), (i, x, pp, p[i], aa, a[i])
            assert np.isnan(p).any() and np.isfinite(p).any()
        finally:
            ft.PREM_OLD, ft.PREM_NEW, fd.PREM_ROOTS = old


def test_prem_candle_only_from_own_day_file_and_aligned_minute():
    with tempfile.TemporaryDirectory() as tmp:
        old = fd.PREM_ROOTS
        fd.PREM_ROOTS = (tmp,)
        try:
            day0 = T0 + 19 * 86_400_000
            # файл суток 20.09 содержит «чужую» свечу (open 19.09 23:59) и невыровненную по минуте (open :30 с) — оригинал их не находит (`o in d` для своих суток и ровной минуты)
            write_prem_day(tmp, "AAAUSDT", "2026-09-20", [day0 - 60_000, day0 + 30_000, day0 + 60_000], [9.0, 8.0, 7.0])
            o, c, _ = fd.load_prem("AAAUSDT", ["2026-09-20"])
            assert o.tolist() == [day0 + 60_000] and c.tolist() == [7.0]
        finally:
            fd.PREM_ROOTS = old


# ---- C ---------------------------------------------------------------------------------------------------------------------------------------

def lv(born, end, segs, fb):
    return (born, end, np.array([m for m, _ in segs], dtype=np.int64), np.array([p for _, p in segs], dtype=np.int64), fb)


def to_orig(levels):
    return {"AAAUSDT": [(b, e, m.tolist(), p.tolist()) for b, e, m, p, _ in levels]}


def test_conf_vec_hand_computed():
    p = 1_000_000
    levels = [
        lv(10, 100, [(10, 900_000), (50, 1_003_000)], 1),       # на m = 60 версия 1 003 000 (diff 3000 = 0,3 %) → только τ = 0,4 %
        lv(20, 200, [(20, 1_001_000)], 2),                      # diff 1000 = 0,1 % ровно на границе τ = 0,1 % → conf01 = 1 (≤)
        lv(61, 200, [(61, 1_000_000)], 3),                      # рождён после m = 60 → не живой
        lv(10, 60, [(10, 1_000_000)], 4),                       # end = 60 = m → мёртв (m < end не выполнено)
        lv(10, 200, [(61, 1_000_000)], 5),                      # первая версия цены позже m → цены на m нет
    ]
    got = fd.conf_vec(levels, np.array([60], dtype=np.int64), np.array([p], dtype=np.int64), None, None)
    assert got[:, 0].tolist() == [1.0, 1.0, 1.0]
    only_a = fd.conf_vec(levels[:1], np.array([60], dtype=np.int64), np.array([p], dtype=np.int64), None, None)
    assert only_a[:, 0].tolist() == [0.0, 0.0, 1.0]
    none = fd.conf_vec(levels[2:], np.array([60], dtype=np.int64), np.array([p], dtype=np.int64), None, None)
    assert none[:, 0].tolist() == [0.0, 0.0, 0.0]


def test_conf_vec_equals_scalar_original_random():
    rng = np.random.default_rng(5)
    levels = []
    for k in range(40):
        born = int(rng.integers(0, 3000))
        end = int(born + rng.integers(1, 2000)) if rng.random() < 0.8 else 2 ** 32 - 1
        nseg = int(rng.integers(1, 4))
        mins = np.sort(born + rng.integers(0, 800, size=nseg))
        mins[0] = born
        levels.append(lv(born, end, list(zip(mins.tolist(), rng.integers(900_000, 1_100_000, size=nseg).tolist())), k))
    m = rng.integers(0, 4000, size=600).astype(np.int64)
    p = rng.integers(950_000, 1_050_000, size=600).astype(np.int64)
    got = fd.conf_vec(levels, m, p, None, None)
    orig = to_orig(levels)
    for i in range(600):
        want = ft.confluence(orig, "AAAUSDT", int(m[i]), int(p[i]))
        assert got[:, i].tolist() == want, (i, got[:, i].tolist(), want)
    assert 0 < got.sum() < got.size


def test_conf_vec_excludes_own_240_level_only_when_flagged():
    p = 1_000_000
    levels = [lv(10, 200, [(10, 1_000_500)], 7),                # тот же кластер пивотов (first_bar = 7), диффер 0,05 %: свой уровень строки
              lv(10, 200, [(10, 1_000_000)], 8),                # другой first_bar, но точно та же цена на m: тоже «то же значение»
              lv(10, 200, [(10, 1_003_000)], 9)]                # чужой уровень, 0,3 %
    m = np.array([60, 60], dtype=np.int64)
    pp = np.array([p, p], dtype=np.int64)
    fb = np.array([7, 7], dtype=np.int64)
    own = np.array([False, True])
    got = fd.conf_vec(levels, m, pp, fb, own)
    assert got[:, 0].tolist() == [1.0, 1.0, 1.0]                # без исключения — слит (TK-666 дословно)
    assert got[:, 1].tolist() == [0.0, 0.0, 1.0]                # с исключением своего (first_bar 7) и точно равного по цене (first_bar 8): остаётся только чужой 0,3 % → τ = 0,4 %


def test_conf_garbage_after_m_changes_nothing():
    rng = np.random.default_rng(8)
    levels = [lv(10, 500, [(10, 1_000_500), (300, 1_020_000)], 1), lv(400, 900, [(400, 1_000_000)], 2), lv(20, 100, [(20, 1_000_100)], 3)]
    m = np.array([250], dtype=np.int64)
    p = np.array([1_000_000], dtype=np.int64)
    clean = fd.conf_vec(levels, m, p, None, None)
    dirty = [lv(10, 777, [(10, 1_000_500), (300, int(rng.integers(1, 10 ** 12)))], 1), lv(400, 901, [(400, int(rng.integers(1, 10 ** 12)))], 2), levels[2]]
    assert np.array_equal(clean, fd.conf_vec(dirty, m, p, None, None))      # версии позже m, рождённый позже m и продлённый `end` — без влияния


# ---- compute -----------------------------------------------------------------------------------------------------------------------------------

def write_fund_month(root, sym, month, rows):
    os.makedirs(os.path.join(root, sym), exist_ok=True)
    with zipfile.ZipFile(os.path.join(root, sym, "%s-fundingRate-%s.zip" % (sym, month)), "w") as z:
        z.writestr("%s-fundingRate-%s.csv" % (sym, month), "calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join("%d,%s,%.8f" % r for r in rows) + "\n")


def write_levels(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("sym,level,born,end,first_bar,segs,members\n")
        for i, (sym, born, end, fb, segs) in enumerate(rows):
            f.write("%s,%d,%d,%d,%d,%s,%s\n" % (sym, i, born, end, fb, "|".join("%d:%d" % s for s in segs), "%d:%d" % (fb, segs[0][1])))


def mk(sigs_min, sym="AAAUSDT", **extra):
    n = len(sigs_min)
    sig_ms = np.array([int(m * 60_000 + 1500) for m in sigs_min], dtype=np.int64)
    d = dict(row_id=np.arange(7, 7 + n, dtype=np.int64), sym=[sym] * n, sig_ms=sig_ms, abs_ms=sig_ms + T0, level_px_e8=[1_000_000] * n, natr_e8=[1_000] * n, side="long", win="wc",
             first_bar=[1] * n, mask=[1 << 3] * n)
    d.update(extra)
    return pd.DataFrame(d)


def test_compute_end_to_end_with_missing_data_and_own_exclusion():
    with tempfile.TemporaryDirectory() as tmp:
        saved = (ft.FUND_OLD, ft.FUND_NEW, ft.PREM_OLD, ft.PREM_NEW, ft.ROOT, fd.FUND_ROOTS, fd.PREM_ROOTS)
        fo, fn, po, pn = (os.path.join(tmp, x) for x in ("fo", "fn", "po", "pn"))
        ft.FUND_OLD, ft.FUND_NEW, ft.PREM_OLD, ft.PREM_NEW, ft.ROOT = fo, fn, po, pn, os.path.join(tmp, "r")
        fd.FUND_ROOTS, fd.PREM_ROOTS = (fo, fn), (po, pn)
        try:
            day = T0 + 19 * 86_400_000                                                           # 20.09 00:00 UTC
            write_fund_month(fo, "AAAUSDT", "2026-09", [(T0 + 19 * 86_400_000 - 5 * 3_600_000, 8, 0.0008), (day + 3 * 3_600_000, 8, 0.0016)])       # 19.09 19:00 и 20.09 03:00
            write_prem_day(po, "AAAUSDT", "2026-09-20", day + 60_000 * np.arange(1440), 0.001 * np.arange(1440))
            m_row = 19 * 1440 + 4 * 60 + 10                                                      # 20.09 04:10
            # уровни 240 (минуты от T0): A — тот же кластер, что у строки (first_bar 1), цена в пределах 0,1 %; B — чужой в пределах 0,4 %; C — мёртв
            write_levels(ft.levels_path("t240", "wc", "L"), [("AAAUSDT", 100, 10 ** 6, 1, [(100, 1_000_500)]), ("AAAUSDT", 100, 10 ** 6, 2, [(100, 1_003_500)]),
                                                              ("AAAUSDT", 100, m_row, 3, [(100, 1_000_000)])])
            rows = mk([m_row, m_row + 1, 19 * 1440 + 4 * 60 + 10], mask=[1 << 3, 1 << 7, 1 << 7])                 # строка 0: источник p60n1 (бит 3), строки 1–2: есть источник p240n2 (бит 7)
            rows.loc[2, "sym"] = "ZZZUSDT"                                                                              # монета без файлов (фандинг, премия), без уровней
            st_ = {}
            out = fd.compute(rows, "wc", "long", procs=1, stats=st_)
            assert list(out.index) == [7, 8, 9] and list(out.columns) == fd.COLS and (out.dtypes == np.float64).all()
            # F: последняя выплата 20.09 03:00 (интервал 8 ч, ставка 0,0016 → к 8 ч 0,0016), возраст на 04:10:01,5 = 1 ч 10 мин 1,5 с
            assert out["f8"].iloc[0] == 0.0016 and abs(out["f_age_h"].iloc[0] - (1 + 10 / 60 + 1.5 / 3600)) < 1e-12
            # P: свеча open 04:09 (закрыта 04:10), close = 0,001 · (4·60 + 9); возраст = 1,5 с
            assert abs(out["prem"].iloc[0] - 0.001 * (4 * 60 + 9)) < 1e-12 and abs(out["prem_age_s"].iloc[0] - 1.5) < 1e-9
            # R / lvl_px: p = 1_000_000 (7 цифр) → шаг 10^5; округление к 1 000 000, dist 0
            assert out["rho"].iloc[0] == 0.0 and out["mant"].iloc[0] == 1.0 and out["lvl_px"].iloc[0] == 1_000_000
            # C: строка 0 (нет источника 240) — A (diff 500 → τ 0,1 %: 500 ≤ 1000 ✓.) и B (3500 ≤ 4000: τ 0,4 %) считаются; C мёртв (end = m)
            assert out[["conf01", "conf02", "conf04"]].iloc[0].tolist() == [1.0, 1.0, 1.0]
            # строка 1 (источник p240n2): A — свой (first_bar 1) исключён; остаётся B на 0,35 % → только τ 0,4 %
            assert out[["conf01", "conf02", "conf04"]].iloc[1].tolist() == [0.0, 0.0, 1.0]
            # строка 2: монета без файлов — F, P: NaN; уровней у монеты нет → conf = 0 (не NaN: дамп окна есть)
            assert np.isnan(out["f8"].iloc[2]) and np.isnan(out["prem"].iloc[2]) and out[["conf01", "conf02", "conf04"]].iloc[2].tolist() == [0.0, 0.0, 0.0]
            assert st_["need_counts"]["prem_coin_days"] >= 1 and st_["levels_file_exists"]
            # нет дампа уровней окна и стороны → conf NaN
            os.remove(ft.levels_path("t240", "wc", "L"))
            o2 = fd.compute(rows, "wc", "long", procs=1)
            assert o2[["conf01", "conf02", "conf04"]].isna().all().all() and o2["rho"].notna().all()
        finally:
            ft.FUND_OLD, ft.FUND_NEW, ft.PREM_OLD, ft.PREM_NEW, ft.ROOT, fd.FUND_ROOTS, fd.PREM_ROOTS = saved


def test_compute_no_exclusion_without_mask_columns():
    with tempfile.TemporaryDirectory() as tmp:
        saved = (ft.ROOT, fd.FUND_ROOTS, fd.PREM_ROOTS)
        ft.ROOT, fd.FUND_ROOTS, fd.PREM_ROOTS = os.path.join(tmp, "r"), (tmp,), (tmp,)
        try:
            write_levels(ft.levels_path("t240", "wc", "L"), [("AAAUSDT", 0, 10 ** 6, 1, [(0, 1_000_000)])])
            rows = mk([5000]).drop(columns=["mask", "first_bar"])
            out = fd.compute(rows, "wc", "long")
            assert out[["conf01", "conf02", "conf04"]].iloc[0].tolist() == [1.0, 1.0, 1.0]
        finally:
            ft.ROOT, fd.FUND_ROOTS, fd.PREM_ROOTS = saved


def test_guard_forward_window_and_arg_checks():
    rows = mk([60 * 24 * 74])                                      # 01.09 + 74 сут = 14.11
    try:
        fd.compute(rows, "wc", "long")
    except SystemExit:
        pass
    else:
        raise AssertionError("охрана 14.11 не сработала")
    rows = mk([100])
    for bad in (rows.assign(win="wa"), rows.assign(side="short"), pd.concat([rows, rows])):
        try:
            fd.compute(bad, "wc", "long")
        except ValueError:
            continue
        raise AssertionError("ошибка аргументов не поймана")


if __name__ == "__main__":
    fails = 0
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for k, v in tests:
        try:
            v()
            print("ok  ", k)
        except BaseException:
            fails += 1
            print("FAIL", k)
            traceback.print_exc()
    print("%d тестов, провалов %d" % (len(tests), fails))
    sys.exit(1 if fails else 0)
