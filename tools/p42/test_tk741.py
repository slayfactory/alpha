"""TK-741 (В-192): признаки сетапа и k-means (tk741_cluster.py) — признаки не зависят от минуток ≥ t (только прошлое), k-means детерминирован, шкала названий кластеров."""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk741_cluster as C  # noqa: E402

N = 3000
T = 2500


def make_bars(seed):
    """Синтетические минутки окна: (o, h, l, c, qv, c_ffill, pres) как `bars()`; с пропусками минут."""
    rng = np.random.default_rng(seed)
    c = 1e8 * np.exp(np.cumsum(rng.normal(0, 0.001, N)))
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0005, N)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0005, N)))
    qv = rng.uniform(1e9, 5e9, N)
    pres = rng.random(N) > 0.05
    pres[:1500] = True
    o, h, l, c, qv = (np.where(pres, x, 0.0) for x in (o, h, l, c, qv))
    cn = np.where(pres, c, np.nan)
    idx = np.where(pres, np.arange(N), 0)
    np.maximum.accumulate(idx, out=idx)
    cf = np.where(np.cumsum(pres) > 0, cn[idx], np.nan)
    return (o, h, l, c, qv, cf, pres)


def corrupt_from(b, t):
    """Те же минутки, но с минуты t и позже — другие числа (проверка, что признаки их не читают)."""
    o, h, l, c, qv, cf, pres = (x.copy() for x in b)
    rng = np.random.default_rng(99)
    for a in (o, h, l, c, qv, cf):
        a[t:] = rng.uniform(1e5, 1e12, N - t)
    pres[t:] = rng.random(N - t) > 0.5
    return (o, h, l, c, qv, cf, pres)


def test_features_use_only_minutes_before_t():
    b, btc = make_bars(1), make_bars(2)
    lvl = float(b[3][T - 30]) / 1e8 * 0.999
    f0 = C.features(b, btc, T, lvl, first_bar=T // 60 - 5)
    f1 = C.features(corrupt_from(b, T), corrupt_from(btc, T), T, lvl, first_bar=T // 60 - 5)
    assert set(f0) == set(C.FEATS)
    for k in C.FEATS:
        assert f0[k] == f1[k] or (np.isnan(f0[k]) and np.isnan(f1[k])), k


def test_features_change_when_past_changes():
    b, btc = make_bars(1), make_bars(2)
    lvl = float(b[3][T - 30]) / 1e8
    f0 = C.features(b, btc, T, lvl, first_bar=T // 60 - 5)
    o, h, l, c, qv, cf, pres = (x.copy() for x in b)
    cf[T - 1] *= 1.05
    c[T - 1] *= 1.05
    f1 = C.features((o, h, l, c, qv, cf, pres), btc, T, lvl, first_bar=T // 60 - 5)
    assert abs(f1["r15"] - f0["r15"]) > 3.0 and abs(f1["r60"] - f0["r60"]) > 3.0       # последняя минута до сигнала — в признаках


def test_features_by_hand():
    cf = np.full(N, 100.0 * 1e8)
    cf[T - 1] = 101.0 * 1e8
    pres = np.ones(N, bool)
    z = np.zeros(N)
    b = (cf.copy(), cf.copy(), cf.copy(), cf.copy(), np.full(N, 1e9), cf, pres)
    f = C.features(b, b, T, 100.0, first_bar=T // 60 - 2)
    assert abs(f["r15"] - 1.0) < 1e-9 and abs(f["r60"] - 1.0) < 1e-9 and abs(f["r240"] - 1.0) < 1e-9
    assert abs(f["rv60"] - np.log(1.01) * 100.0) < 1e-9
    assert abs(f["near"] - 59 / 60) < 1e-12 and f["touch"] == 1 and f["stab"] == 0       # одна минута из 60 (закрытие 101) вне полосы ±0,3 %; цена на уровне — одно касание
    assert abs(f["age"] - ((T - (T // 60 - 2) * 60) / 60.0)) < 1e-12 and abs(f["turn"] - 1.0) < 1e-12
    assert f["hi24"] == 0.0 and abs(f["lo24"] - 1.0 / 101.0 * 100.0) < 1e-9 and abs(f["btc60"] - 1.0) < 1e-9
    assert z.sum() == 0


def test_kmeans_deterministic_and_silhouette():
    rng = np.random.default_rng(5)
    X = np.vstack([rng.normal(m, 0.3, (60, 4)) for m in (0.0, 3.0, -3.0)])
    i1, c1, l1 = C.kmeans(X, 3, C.SEED, n_init=5)
    i2, c2, l2 = C.kmeans(X, 3, C.SEED, n_init=5)
    assert i1 == i2 and np.array_equal(c1, c2) and np.array_equal(l1, l2)
    assert sorted(np.bincount(l1).tolist()) == [60, 60, 60]
    assert C.silhouette(X, l1) > 0.8
    assert C.silhouette(X, l1) > C.silhouette(X, rng.integers(0, 3, len(X)))


def test_transform_keeps_shape_and_logs():
    F = np.abs(np.random.default_rng(3).normal(1, 0.5, (20, len(C.FEATS)))) + 0.1
    X = C.transform(F)
    assert X.shape == F.shape
    j = C.FEATS.index("turn")
    assert np.allclose(X[:, j], np.log(F[:, j]))
    j = C.FEATS.index("r60")
    assert np.array_equal(X[:, j], F[:, j])
