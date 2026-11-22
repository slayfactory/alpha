"""Тесты tk681_volq.py: ряды NATR и реализованной волатильности на ручных примерах (те же числа, что в тестах движка `volstop_tests.rs` и TK-681 в
`tick_tests.rs`), края окна, дыры, совокупность «минута t − 1», подбор порога по квантилю. Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk681_volq.py`.
"""
import os
import struct
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tk681_volq as vq  # noqa: E402

E8 = 100_000_000


def flat(n, price):
    a = np.full(n, int(round(price * E8)), dtype=np.int64)
    return a.copy(), a.copy(), a.copy(), a.copy()


def put(cols, t, o, h, l, c):
    for arr, x in zip(cols, (o, h, l, c)):
        arr[t] = int(round(x * E8))


def test_rv_one_jump_is_exact_and_leaves_the_window():
    o, h, l, c = flat(3000, 100.0)
    for t in range(2000, 3000):
        put((o, h, l, c), t, 101.0, 101.0, 101.0, 101.0)
    rv5 = vq.rv_series(o, c, 5)
    assert rv5[1999] == 0 and rv5[2000] == 1_000_000 and rv5[2004] == 1_000_000 and rv5[2005] == 0
    rv60 = vq.rv_series(o, c, 60)
    assert rv60[2059] == 1_000_000 and rv60[2060] == 0
    assert rv5[4] == -1 and rv5[5] == 0, "нужна минутка t и p минут до неё"


def test_rv_and_natr_match_engine_hand_values():
    # мир тестов TK-681 движка: +1 % на минуте a и возврат на a + 1 (цена 90)
    o, h, l, c = flat(500, 90.0)
    a = 300
    put((o, h, l, c), a, 90.0, 90.9, 90.0, 90.9)
    put((o, h, l, c), a + 1, 90.9, 90.9, 90.0, 90.0)
    rv5 = vq.rv_series(o, c, 5)
    assert rv5[a + 2] == 1_407_229, rv5[a + 2]
    assert rv5[a + 5] == 990_099, "в окне осталась только минута возврата"
    assert rv5[a + 6] == 0
    natr5 = vq.natr_series(o, h, l, c, 5)
    assert natr5[a + 2] == 400_000, "(0,9 + 0,9) / 5 / 90 = 0,4 %"
    natr30 = vq.natr_series(o, h, l, c, 30)
    assert natr30[a + 2] == 66_666
    assert natr30[28] == -1 and natr30[29] == 0


def test_holes_give_no_value_and_true_range_uses_previous_valid_close():
    o, h, l, c = flat(200, 100.0)
    for t in range(100, 104):
        o[t] = 0  # дыра 4 минуты из 5
    put((o, h, l, c), 104, 102.0, 102.0, 102.0, 102.0)
    natr5 = vq.natr_series(o, h, l, c, 5)
    assert natr5[102] == -1, "минутки нет"
    assert natr5[104] == -1, "в окне 5 минут одна минутка — меньше половины"
    natr15 = vq.natr_series(o, h, l, c, 15)
    # разрыв 100 → 102 входит в истинный диапазон минуты 104 через закрытие последней минутки с данными (99)
    assert natr15[104] == int(2.0 * E8 * E8 // (11 * 102 * E8))
    rv5 = vq.rv_series(o, c, 5)
    assert rv5[104] == -1 and rv5[103] == -1


def test_pool_is_the_closed_minute_before_a_counted_minute():
    n = vq.START_MIN + 200
    o, h, l, c = flat(n, 50.0)
    qv = np.zeros(n, dtype=np.int64)
    t0 = vq.START_MIN + 100
    qv[t0 - 60 : t0] = 10**10  # 100 тыс. $ в минуту: час = 6 млн $
    natr30 = vq.natr_series(o, h, l, c, 30)
    m = vq.pool_mask(o, qv, natr30, 0.5)
    # час [t − 60, t) набирает 0,5 млн $ начиная с t = t0 − 55 и до t = t0 + 55 → отмечены минуты t − 1
    assert m[t0 - 56] and m[t0 + 54] and not m[t0 - 57] and not m[t0 + 55]
    assert not m[: vq.START_MIN - 1].any(), "до счётного окна — ничего"
    m_all = vq.pool_mask(o, qv, natr30, 0.0)
    assert m_all[vq.START_MIN - 1] and not m_all[vq.START_MIN - 2] and not m_all[n - 1]
    # зачётные сутки 15…15 сетки: минуты t суток 15 → отметки t − 1
    n2 = 17 * 1440
    o, h, l, c = flat(n2, 50.0)
    m15 = vq.pool_mask(o, np.zeros(n2, dtype=np.int64), vq.natr_series(o, h, l, c, 30), 0.0, 15, 15)
    assert m15.sum() == 1440 and m15[15 * 1440 - 1] and m15[16 * 1440 - 2] and not m15[16 * 1440 - 1]


def test_quantile_match_returns_the_threshold_for_the_same_series():
    rng = np.random.default_rng(7)
    v = (rng.lognormal(mean=np.log(0.5), sigma=0.6, size=400_000) * 1e6).astype(np.int64)
    hist = np.zeros(vq.NBINS, dtype=np.int64)
    vq.hist_add(hist, v)
    for g in (0.2, 0.4, 0.8, 1.6):
        q = vq.share_below(hist, g)
        assert abs(q - (v < g * 1e6).mean()) < 1e-12
        assert abs(vq.quantile(hist, q) - g) <= 0.0011, (g, vq.quantile(hist, q))
    # ряд вдвое шире даёт вдвое больший порог на том же квантиле
    hist2 = np.zeros(vq.NBINS, dtype=np.int64)
    vq.hist_add(hist2, v * 2)
    q = vq.share_below(hist, 0.4)
    assert abs(vq.quantile(hist2, q) - 0.8) <= 0.0021


def test_run_reads_b42_and_reports_grid():
    n = vq.START_MIN + 3000
    rng = np.random.default_rng(3)
    with tempfile.TemporaryDirectory() as d:
        for name in ("AAA", "BBB"):
            # два режима волатильности по 500 минут: 0,1 % и 0,4 % за минуту
            sigma = np.where((np.arange(n) // 500) % 2 == 0, 0.001, 0.004)
            steps = rng.normal(0, 1, n) * sigma
            cl = (100 * np.exp(np.cumsum(steps)) * E8).astype(np.int64)
            op = np.concatenate(([cl[0]], cl[:-1]))
            hi = np.maximum(op, cl) + 1000
            lo = np.minimum(op, cl) - 1000
            qv = np.full(n, 10**10, dtype=np.int64)
            with open(os.path.join(d, name + ".b42"), "wb") as f:
                f.write(b"B42\x01" + struct.pack("<I", n))
                for arr in (op, hi, lo, cl, qv):
                    f.write(arr.astype("<i8").tobytes())
                f.write(struct.pack("<I", 0))
        res = vq.run([("w", d)], [0.2, 0.3], 0.5)
        assert res["coin_windows"] == 2 and res["minutes"] == 2 * 3000
        g = res["grid"]
        assert 0 < g[0]["share_below"] < g[1]["share_below"] < 1
        # блуждание: RV 30 минут ≈ σ·√30 — выше NATR 30 (≈ 0,8 σ) на том же квантиле
        assert g[0]["match"]["7"] > 0.2 and g[0]["match"]["7"] <= g[1]["match"]["7"]
        assert set(g[0]["match"]) == {"1", "2", "4", "5", "6", "7", "8"}
        assert res["no_value"]["3"] == 0


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print("все тесты прошли: %d" % len(tests))
