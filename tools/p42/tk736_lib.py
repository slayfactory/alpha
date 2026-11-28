"""TK-736 (П-42 шаг 7, §13.80, PS-1080…1083 v3, прописано L-538 / L-539): чистые функции — ход против позиции, k, реплей, G-pow, бутстрепы, правило C1 / C2 / C3 / исходы.
Только numpy; данные и ввод-вывод — в `tk736_an.py`. Зерно 20261127, 20 000 повторов, блоки ⌈n^{1/3}⌉ и 21 (C2 — только блок ⌈n^{1/3}⌉).
"""
from __future__ import annotations

import math

import numpy as np

SEED = 20261127
NBOOT = 20000
STAKE = 500.0            # $ на сделку (В-169)
VM = 0.005               # нижний зажим стопа от V (`vm` по умолчанию, 0,5 %)
SLIP = 0.0001            # проскальзывание 0,01 % на ногу
S_CAP = {"short": 0.04, "long": 0.03}
Q = 0.95
MIN_WIN = 20             # меньше выигрышных с V ≥ 0 → «k не определён» (O4)
C1_FRAC = 0.10
RATIO = (2.0 / 3.0, 1.5)


# ───────────────────────────── сделки ─────────────────────────────

def dedup_first(sym, sig_ms, entry_ms, exit_ms, pnl):
    """Точные дубликаты между клетками (монета, сигнал, вход, выход, P&L до микродоллара; L-477, TK-727) — маска первых вхождений."""
    seen, m = set(), np.zeros(len(sym), bool)
    keys = zip(np.asarray(sym).tolist(), np.asarray(sig_ms).tolist(), np.asarray(entry_ms).tolist(), np.asarray(exit_ms).tolist(), np.round(np.asarray(pnl) * 1e6).astype(np.int64).tolist())
    for i, k in enumerate(keys):
        if k not in seen:
            seen.add(k)
            m[i] = True
    return m


def adverse_move(side, h, l, o, entry_min, exit_min, is_stop, entry_px):
    """Наибольшее отклонение против позиции от цены входа, доля цены: шорт — наибольший high, лонг — наименьший low; минуты `entry_min … exit_min − 1`, у `kind = stop` — включая `exit_min`;
    только присутствующие минуты (o ≠ 0); пустой набор → 0.0 (число таких сделок печатает вызывающий). h, l, o — целые ряды свечей окна (e8)."""
    hi = int(exit_min) + 1 if is_stop else int(exit_min)               # свеча выхода — только у `stop` (буква протокола; exit == entry у не-стоп → пусто → 0.0)
    em = int(entry_min)
    sel = np.asarray(o[em:hi]) != 0
    if not sel.any():
        return 0.0
    px = int(entry_px)
    if side == "short":
        return (int(np.asarray(h[em:hi])[sel].max()) - px) / px
    return (px - int(np.asarray(l[em:hi])[sel].min())) / px


def estimate_k(kappa_win):
    """k̂ = q₀,₉₅ κ выигрышных сделок (линейная интерполяция), округлено до 0,01; < 20 → None (O4)."""
    x = np.asarray(kappa_win, np.float64)
    x = x[np.isfinite(x)]
    if len(x) < MIN_WIN:
        return None
    return round(float(np.quantile(x, Q)), 2)


def q_table(kappa_win, qs=(0.80, 0.85, 0.90, 0.95)):
    x = np.asarray(kappa_win, np.float64)
    x = x[np.isfinite(x)]
    return {q: round(float(np.quantile(x, q)), 2) for q in qs} if len(x) else {}


def stop_distance(V, k, s, vm=VM):
    """d_i(k): V = −1 (нет значения) → жёсткий `s`; иначе clamp(k·V, vm, s)."""
    V = np.asarray(V, np.float64)
    return np.where(V < 0, s, np.clip(k * V, vm, s))


def replay(r, fees, a, V, k, s, vm=VM):
    """Модель реплея (§13.80.1): срез по первому касанию, если d < s и a ≥ d; r(k) = −Ставка·(d + 0,0001) − fees; остальные сделки как были. Модель, не результат."""
    r, fees, a = (np.asarray(x, np.float64) for x in (r, fees, a))
    d = stop_distance(V, k, s, vm)
    cut = (d < s) & (a >= d)
    return np.where(cut, -STAKE * (d + SLIP) - fees, r), cut


def gmax(r, fees, vm=VM):
    """G_max: Σ по проигравшим (r < 0) max(0, |r| − R), R = Ставка·(vm + 0,0001) + fees — остаточный убыток стопа на нижнем зажиме (правка a критика)."""
    r, fees = np.asarray(r, np.float64), np.asarray(fees, np.float64)
    R = STAKE * (vm + SLIP) + fees
    m = r < 0
    return float(np.maximum(0.0, -r[m] - R[m]).sum())


# ───────────────────────────── бутстрепы ─────────────────────────────

def block_len(n):
    return int(math.ceil(round(n ** (1.0 / 3.0), 12)))


def _idx(n, block, nboot, seed):
    rng = np.random.default_rng(seed)
    nb = math.ceil(n / block)
    st = rng.integers(0, n, size=(nboot, nb))
    return ((st[:, :, None] + np.arange(block)[None, None, :]) % n).reshape(nboot, nb * block)[:, :n]


def boot_sums(x, block, nboot=NBOOT, seed=SEED):
    """Суммы повторов кругового блочного бутстрепа суточного ряда x (целые сутки)."""
    x = np.asarray(x, np.float64)
    return x[_idx(len(x), block, nboot, seed)].sum(axis=1)


def c1(delta, sigma0, nboot=NBOOT, seed=SEED):
    """C1: нижняя граница Δ (5-й перцентиль бутстрепа Σ дневных разностей, односторонняя 95 %) ≥ −0,10·|Σ₀(H)| при блоках ⌈n^{1/3}⌉ и 21. Разные знаки → «unclear»."""
    delta = np.asarray(delta, np.float64)
    thr = -C1_FRAC * abs(sigma0)
    out, oks = {}, []
    for b in (block_len(len(delta)), 21):
        lo = float(np.percentile(boot_sums(delta, b, nboot, seed), 5))
        out[b] = lo
        oks.append(lo >= thr)
    verdict = "pass" if all(oks) else "fail" if not any(oks) else "unclear"
    return dict(verdict=verdict, lo=out, thr=thr, delta=float(delta.sum()))


def s5(delta, nboot=NBOOT, seed=SEED):
    """G-pow: s₅ = Δ̄ − q₅(Δ*) — наибольший из блоков ⌈n^{1/3}⌉ и 21; печатается только s₅ (не Δ̄)."""
    delta = np.asarray(delta, np.float64)
    tot = float(delta.sum())
    return max(tot - float(np.percentile(boot_sums(delta, b, nboot, seed), 5)) for b in (block_len(len(delta)), 21))


def mde_c1(delta, sigma0, nboot=NBOOT, seed=SEED):
    return s5(delta, nboot, seed) - C1_FRAC * abs(sigma0)


def month_sigma(pnl, n, lab, nper):
    """σ (ddof = 1) месячного $ на сделку: Σ P&L периода / Σ числа сделок периода по меткам суток lab (0…nper−1)."""
    sp = np.bincount(lab, weights=pnl, minlength=nper)
    sn = np.bincount(lab, weights=n, minlength=nper)
    return float(np.std(np.where(sn > 0, sp / np.where(sn > 0, sn, 1), 0.0), ddof=1))


def c2_p(pnl2, n2, pnl0, n0, lab, nper, nboot=NBOOT, seed=SEED):
    """C2: p = доля повторов с σ(V2′) − σ(V0) ≥ 0; блок ⌈n^{1/3}⌉ суток по всей половине, метка периода едет с сутками, одни и те же сутки для обоих рядов."""
    lab = np.asarray(lab, np.int64)
    nd = len(lab)
    L = np.eye(nper)[lab]
    idx = _idx(nd, block_len(nd), nboot, seed)

    def sig(p, n):
        sp = (np.asarray(p, np.float64)[:, None] * L)[idx].sum(axis=1)
        sn = (np.asarray(n, np.float64)[:, None] * L)[idx].sum(axis=1)
        return np.std(np.where(sn > 0, sp / np.where(sn > 0, sn, 1), 0.0), axis=1, ddof=1)
    diff = sig(pnl2, n2) - sig(pnl0, n0)
    obs = month_sigma(pnl2, n2, lab, nper) - month_sigma(pnl0, n0, lab, nper)
    return dict(p=float((diff >= 0).mean()), obs_diff=float(obs), se=float(diff.std(ddof=1)), sigma2=month_sigma(pnl2, n2, lab, nper), sigma0=month_sigma(pnl0, n0, lab, nper))


def holm(p):
    order = sorted(range(len(p)), key=lambda i: p[i])
    adj, run = [0.0] * len(p), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(p) - rank) * p[i]))
        adj[i] = run
    return adj


# ───────────────────────────── C3, исходы ─────────────────────────────

def c3(r0, r2, key0, key2, kind2, d2_lt_s, kind0):
    """C3 (L-538): (а) на парных сделках (ключ (клетка, sym, sig_ms, level) есть в обоих рядах) max(−r V2′) ≤ max(−r V0); (б) средний убыток сделок V2′ `stop` с d < s не больше среднего убытка `stop` V0.
    r0, r2 — P&L по сделкам; key0, key2 — списки ключей; kind2, kind0 — виды выхода; d2_lt_s — признак d < s по сделкам V2′ (из vstop.csv)."""
    m0 = {k: i for i, k in enumerate(key0)}
    pairs = [(m0[k], j) for j, k in enumerate(key2) if k in m0]
    r0, r2 = np.asarray(r0, np.float64), np.asarray(r2, np.float64)
    if pairs:
        i0, i2 = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
        l0, l2 = float((-r0[i0]).max()), float((-r2[i2]).max())
    else:
        l0 = l2 = float("nan")
    a_ok = bool(pairs) and l2 <= l0 + 1e-9
    s2 = (np.asarray(kind2) == "stop") & np.asarray(d2_lt_s, bool)
    s0 = np.asarray(kind0) == "stop"
    m2_, m0_ = (float(-r2[s2].mean()) if s2.any() else float("nan")), (float(-r0[s0].mean()) if s0.any() else float("nan"))
    b_ok = (not s2.any()) or (s0.any() and m2_ <= m0_ + 1e-9)
    return dict(ok=bool(a_ok and b_ok), a_ok=bool(a_ok), l0=l0, l2=l2, pairs=len(pairs), b_ok=bool(b_ok), loss_stop2=m2_, loss_stop0=m0_)


def outcome(k_fj, k_sel, res_a, res_b, p_side_holm, c3_ok, powered=True, defined=True):
    """Исход стороны (§13.80.2): O6 (не различимо) / O4 (k не определён) / O5 (C3) / O1 / O2 / O3. res_* — dict(c1=verdict, c2p=p) по направлению A (суд на SEL) и B (суд на FJ)."""
    if not powered:
        return "O6"
    if not defined or k_fj is None or k_sel is None:
        return "O4"
    if not c3_ok:
        return "O5"
    ratio = k_fj / k_sel
    ok_ratio = RATIO[0] <= ratio <= RATIO[1]
    c1v = (res_a["c1"], res_b["c1"])
    c2_ok = p_side_holm < 0.05
    if "fail" in c1v or not c2_ok:
        return "O2"
    if "unclear" in c1v or not ok_ratio:
        return "O3"
    return "O1"


# ───────────────────────────── метрики описательных таблиц (В-169) ─────────────────────────────

MARGIN = 250.0           # $ на линию (В-169)


def sharpe(x):
    """Годовой Шарп суточного ряда (среднее / σ, ddof = 1, × √365; нулевые сутки входят); None — σ = 0 или < 2 суток."""
    x = np.asarray(x, np.float64)
    if len(x) < 2:
        return None
    sd = float(x.std(ddof=1))
    return float(x.mean() / sd * math.sqrt(365)) if sd > 0 else None


def uw_stats(x):
    """Просадка и перехай суточного ряда: максимальная просадка $ от пика накопленного ряда (старт 0); перехай — зазор между новыми максимумами, сутки: самый длинный (хвост считается),
    средний, p90 числа суток с последнего максимума. Правила — `tk684_metrics` / `tk663_shape.uw`."""
    x = np.asarray(x, np.float64)
    n = len(x)
    if n == 0:
        return dict(dd=0.0, gap_longest=0, gap_mean=float("nan"), gap_p90=float("nan"), tail=0)
    cum = np.cumsum(x)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    prev = np.concatenate([[0.0], peak[:-1]])
    new = cum > prev + 1e-9
    last, gaps, since = -1, [], np.zeros(n)
    for i in range(n):
        if new[i]:
            gaps.append(i - last)
            last = i
        since[i] = i - last
    tail = n - 1 - last
    return dict(dd=float((peak - cum).max()), gap_longest=int(max(gaps + [tail])), gap_mean=float(np.mean(gaps)) if gaps else float("nan"), gap_p90=float(np.percentile(since, 90)), tail=int(tail))


def rr(v):
    """R:R = средний выигрыш ÷ |средний проигрыш| по сделкам; None — нет тех или других."""
    v = np.asarray(v, np.float64)
    w, l = v[v > 0], v[v < 0]
    return float(w.mean() / abs(l.mean())) if len(w) and len(l) else None


def delta_ci(delta, blocks, nboot=NBOOT, seed=SEED):
    """Двусторонний 95 % интервал Σ суточных разностей: блочный бутстреп, блоки из `blocks`."""
    delta = np.asarray(delta, np.float64)
    out = {}
    for b in blocks:
        s = boot_sums(delta, b, nboot, seed)
        out[int(b)] = (float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5)))
    return out


def month_stats(pnl, n, lab, nper):
    """По периодам: Σ $, сделок, $ на сделку; σ (ddof = 1) и среднее месячного $ на сделку; число периодов с Σ > 0 (M1)."""
    sp = np.bincount(lab, weights=pnl, minlength=nper)
    sn = np.bincount(lab, weights=n, minlength=nper)
    per = np.where(sn > 0, sp / np.where(sn > 0, sn, 1), 0.0)
    sd = float(per.std(ddof=1)) if nper > 1 else float("nan")
    return dict(sum=[float(x) for x in sp], n=[float(x) for x in sn], per_trade=[float(x) for x in per], sigma=sd, mean=float(per.mean()), m1=int((sp > 0).sum()), nper=int(nper))
