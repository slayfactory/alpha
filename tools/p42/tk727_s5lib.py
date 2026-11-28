"""TK-727, шаг 5 (§13.78.5, §13.78.7 п. 1–3, 10, 13): регрессия с фиксированными эффектами «сутки + монета», бутстреп сутками целиком, трети внутри суток, перестановки корзин."""
from __future__ import annotations

import math

import numpy as np

import tk727_lib as L


def twoway_residuals(M: np.ndarray, g1: np.ndarray, n1: int, g2: np.ndarray | None, n2: int, iters: int = 40, tol: float = 1e-10) -> np.ndarray:
    """Остатки столбцов M после вычитания средних по группам g1 (сутки) и g2 (монета) попеременно до сходимости; g2 = None — только g1."""
    M = np.array(M, dtype=np.float64, copy=True)
    groups = [(g1, n1)] + ([(g2, n2)] if g2 is not None else [])
    cnts = []
    for g, ng in groups:
        c = np.bincount(g, minlength=ng).astype(np.float64)
        c[c == 0] = 1.0
        cnts.append(c)
    scale = max(float(np.abs(M).max()), 1.0)
    for _ in range(iters if g2 is not None else 1):
        prev = M.copy() if g2 is not None else None
        for (g, ng), c in zip(groups, cnts):
            for j in range(M.shape[1]):
                M[:, j] -= (np.bincount(g, weights=M[:, j], minlength=ng) / c)[g]
        if prev is not None and float(np.abs(M - prev).max()) < tol * scale:
            break
    return M


def fe_coefs(X: np.ndarray, Y: np.ndarray, g1, n1, g2, n2) -> np.ndarray:
    """Коэффициенты OLS Y ~ X после вычитания фиксированных эффектов (Фриш–Во): матрица k × m. Вырожденная матрица — псевдообратная."""
    k = X.shape[1]
    R = twoway_residuals(np.column_stack([X, Y]), g1, n1, g2, n2)
    Xr, Yr = R[:, :k], R[:, k:]
    XtX = Xr.T @ Xr
    try:
        return np.linalg.solve(XtX, Xr.T @ Yr)
    except np.linalg.LinAlgError:
        return np.linalg.pinv(XtX) @ (Xr.T @ Yr)


def day_blocks(nd: int, block: int, nboot: int, seed: int = L.SEED) -> np.ndarray:
    """Номера суток (по кругу) для nboot повторов: блоки подряд идущих суток длины `block`, nd позиций на повтор."""
    rng = np.random.default_rng(seed)
    nb = math.ceil(nd / block)
    starts = rng.integers(0, nd, size=(nboot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % nd
    return idx.reshape(nboot, nb * block)[:, :nd]


def boot_fe(X, Y, day, coin, nd, nc, block, nboot=None, seed=L.SEED, which=0) -> np.ndarray:
    """Бутстреп: пересэмплинг суток блоками целиком (все сделки суток) и пересчёт регрессии на каждом повторе; возвращает nboot × m — коэффициент при X[:, which]
    для каждого исхода. Сделки отсортированы по суткам внутри функции; двойники суток делят метку (результат тот же, что при раздельных метках)."""
    nboot = L.NBOOT if nboot is None else nboot
    order = np.argsort(day, kind="stable")
    X, Y, day, coin = X[order], Y[order], day[order], coin[order]
    cnt = np.bincount(day, minlength=nd)
    start = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    blocks = day_blocks(nd, block, nboot, seed)
    out = np.full((nboot, Y.shape[1]), np.nan)
    for r in range(nboot):
        ds = blocks[r]
        ln = cnt[ds]
        tot = int(ln.sum())
        if tot < 10:
            continue
        csum = np.cumsum(ln)
        idx = np.repeat(start[ds] - (csum - ln), ln) + np.arange(tot)
        out[r] = fe_coefs(X[idx], Y[idx], day[idx], nd, coin[idx], nc)[which]
    return out


def interval(boot: np.ndarray, point: float) -> dict:
    """Точка, 95 % перцентильный интервал и двусторонний p по нормали (SE бутстрепа)."""
    from statistics import NormalDist

    b = boot[np.isfinite(boot)]
    se = float(b.std(ddof=1)) if len(b) > 1 else float("nan")
    lo, hi = (float(x) for x in np.percentile(b, [2.5, 97.5])) if len(b) > 1 else (float("nan"), float("nan"))
    z = point / se if se and se > 0 else 0.0
    p = 2 * (1 - NormalDist().cdf(abs(z))) if se and se > 0 else 1.0
    return dict(point=float(point), se=se, lo=lo, hi=hi, p=p, n=int(len(b)))


def thirds_in_day(x: np.ndarray, day: np.ndarray) -> np.ndarray:
    """Трети значения признака внутри суток: 0 — нижняя, 1 — средняя, 2 — верхняя (ранг среди сделок суток; границы нигде не подбираются). NaN → −1."""
    out = np.full(len(x), -1, np.int8)
    ok = np.isfinite(x)
    idx = np.where(ok)[0]
    if not len(idx):
        return out
    od = np.lexsort((x[idx], day[idx]))
    ii = idx[od]
    dd = day[ii]
    starts = np.r_[0, np.where(np.diff(dd) != 0)[0] + 1]
    ends = np.r_[starts[1:], len(ii)]
    for s0, e0 in zip(starts, ends):
        n = e0 - s0
        r = np.arange(n) / n
        out[ii[s0:e0]] = np.minimum((r * 3).astype(np.int8), 2)
    return out


def top_bottom(y, tercile, day, nd, block, nboot=None, seed=L.SEED) -> dict:
    """Разность «верхняя − нижняя треть» среднего y на сделку; интервал — бутстреп блоками по суткам (суммы по суткам пересчитываются на каждом повторе)."""
    ok = tercile >= 0
    s_t = np.bincount(day[ok & (tercile == 2)], weights=y[ok & (tercile == 2)], minlength=nd)
    n_t = np.bincount(day[ok & (tercile == 2)], minlength=nd).astype(float)
    s_b = np.bincount(day[ok & (tercile == 0)], weights=y[ok & (tercile == 0)], minlength=nd)
    n_b = np.bincount(day[ok & (tercile == 0)], minlength=nd).astype(float)
    with np.errstate(all="ignore"):
        point = s_t.sum() / n_t.sum() - s_b.sum() / n_b.sum()
    nboot = L.NBOOT if nboot is None else nboot
    bl = day_blocks(nd, block, nboot, seed)
    with np.errstate(all="ignore"):
        b = s_t[bl].sum(1) / n_t[bl].sum(1) - s_b[bl].sum(1) / n_b[bl].sum(1)
    res = interval(b, point)
    res.update(mean_top=float(s_t.sum() / n_t.sum()) if n_t.sum() else float("nan"), mean_bottom=float(s_b.sum() / n_b.sum()) if n_b.sum() else float("nan"),
               n_top=int(n_t.sum()), n_bottom=int(n_b.sum()))
    return res


def bin_spread(y, bins, nb=6) -> float:
    """Разброс между корзинами: стандартное отклонение средних y по корзинам (корзины без сделок не считаются)."""
    m = [y[bins == b].mean() for b in range(nb) if (bins == b).any()]
    return float(np.std(m)) if len(m) > 1 else 0.0


def bin_perm_p(y, bins, day, nb=6, nperm=20000, seed=L.SEED) -> dict:
    """Нуль для F3: перестановка корзин между сделками одних суток; p = доля перестановок с разбросом не меньше наблюдаемого (с +1)."""
    obs = bin_spread(y, bins, nb)
    rng = np.random.default_rng(seed)
    order = np.argsort(day, kind="stable")
    y, bins, day = y[order], bins[order], day[order]
    starts = np.r_[0, np.where(np.diff(day) != 0)[0] + 1]
    ends = np.r_[starts[1:], len(day)]
    ge = 0
    for _ in range(nperm):
        pb = bins.copy()
        for s0, e0 in zip(starts, ends):
            if e0 - s0 > 1:
                pb[s0:e0] = bins[s0:e0][rng.permutation(e0 - s0)]
        if bin_spread(y, pb, nb) >= obs:
            ge += 1
    return dict(obs=obs, p=(ge + 1) / (nperm + 1), nperm=nperm)
