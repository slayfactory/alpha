"""Тесты чистых функций `tk722_checks.py` (PYTHONUTF8=1 python -m pytest test_tk722.py -q -p no:cacheprovider)."""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk720_fwd as F  # noqa: E402
import tk722_checks as C  # noqa: E402


def test_extend_paths_prefix_equals_freeze_paths():
    a = F.block_idx(136, 21, 50, np.random.default_rng([719, 1]))
    b = C.extend_paths(136, 365, 21, 50, 719, 722)
    assert b.shape == (50, 365)
    assert np.array_equal(b[:, :136], a)
    assert b.min() >= 0 and b.max() < 136


def test_boot_long_blocks_inside_series():
    idx = C._boot_long(30, 100, np.random.default_rng(1), boot=20, length=7)
    assert idx.shape == (20, 100) and idx.max() < 30
    assert (np.diff(idx[:, :7], axis=1) == 1).all()


def test_psr_mintrl_normal_case():
    rng = np.random.default_rng(5)
    x = rng.normal(0.2, 1.0, 400)
    mo = C.moments(x)
    assert abs(mo["g4"] - 3) < 0.6 and abs(mo["g3"]) < 0.4
    assert 0.99 < C.psr(mo) <= 1.0
    z = 1.6448536269514722
    v = 1 - mo["g3"] * mo["sr"] + (mo["g4"] - 1) / 4 * mo["sr"] ** 2
    assert math.isclose(C.min_trl(mo), 1 + v * (z / mo["sr"]) ** 2)
    assert C.sr0_expected_max(mo, 8) > C.sr0_expected_max(mo, 4) > 0


def test_log_e_gaussian_lr():
    x = np.array([1.0, -0.5, 2.0])
    le = C.log_e(x, 0.5, 2.0)
    want = np.cumsum([(0.5 * v - 0.125) / 4.0 for v in x])
    assert np.allclose(le, want)


def test_drop_top_and_adverse():
    assert C.drop_top([5, -1, 3, 2], 1) == 4 and C.drop_top([5, -1, 3, 2], 2) == 1
    assert math.isclose(C.adverse_bp("entry", 100.0, 99.9), 10.0)        # шорт продал бы на Bybit ниже — вредно
    assert math.isclose(C.adverse_bp("exit", 100.0, 100.1), 10.0)        # откуп на Bybit дороже — вредно
    assert C.adverse_bp("entry", 100.0, 100.1) < 0


def test_core_and_scale():
    assert C.core("1000BONK") == "BONK" and C.core("SHIB1000") == "SHIB" and C.core("1MBABYDOGE") == "BABYDOGE" and C.core("1000000MOG") == "MOG"
    assert C.core("0G") == "0G" and C.core("1INCH") == "1INCH"
    assert C.pow10_scale(1000.0 * 1.01) == 1000.0 and C.pow10_scale(0.001 * 0.99) == 0.001 and C.pow10_scale(1.5) is None


def test_vwap_window_and_side():
    ts = np.array([0.0, 500.0, 1000.0, 1500.0, 3000.0])
    day = (ts, np.array([10.0, 11.0, 12.0, 13.0, 20.0]), np.array([1.0, 1.0, 2.0, 1.0, 1.0]), np.array([1, -1, -1, 1, 1]), np.array([0, 0, 1, 0, 0]))
    v, n = C.vwap(day, 1000.0, -1000, 1000)
    assert n == 4 and math.isclose(v, (10 + 11 + 24 + 13) / 5)
    v, n = C.vwap(day, 1000.0, 0, 1000, side=-1)
    assert n == 1 and v == 12.0
    v, n = C.vwap(day, 1000.0, -1000, 1000, no_rpi=True)
    assert n == 3 and math.isclose(v, 34 / 3)
    assert math.isnan(C.vwap(day, 2200.0, -100, 100)[0])


def test_ols():
    x = np.arange(10.0)
    a, b, r2 = C.ols(x, 2.0 + 3.0 * x)
    assert math.isclose(a, 2.0) and math.isclose(b, 3.0) and math.isclose(r2, 1.0)
