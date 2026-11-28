"""TK-730 §6.4 (PS-1070): карта денег грида (описательно) — P&L ботов MRf и C0 по половинам в разрезах: час UTC и день недели открытия лота, трети оборота / σ_eq / «шаг цены ÷ шаг сетки»,
возраст листинга, окно фондинга, монеты с Σ > 0 в обеих половинах против перестановки.

Лот = один круг (вход + выход) или принудительное закрытие; P&L лота — чистый (минус обе комиссии), по журналу ядра (`tk729_lib.run_coin(..., ledger=N)`); половина и slы — по минуте входа.
Половины — §2 (21.01–30.06 / 01.07–24.11, сутки отчёта [20; 181) и [181; 328)).

    python tk730_map.py run --out /data/tk730 --procs 3
    python tk730_map.py an  --out /data/tk730
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_cfg as C  # noqa: E402
import tk729_lib as L  # noqa: E402
import tk730_an as A  # noqa: E402
import tk730_lib as W  # noqa: E402

LEDGER_CAP = 400_000
NPERM = 10_000
BOTS = ("C0", "MRf")
HALF_NAMES = ("H1", "H2")
FUND_MIN = (0, 480, 960)  # выплаты 00:00, 08:00, 16:00 UTC
FUND_HW = 30  # ±30 минут
AGE_BINS = ("до кэша", "<90", "90–365", ">365")
WD = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


# ───────────────────────────── slы лотов (чистые функции) ─────────────────────────────

def half_of(t_open: np.ndarray) -> np.ndarray:
    """0 — H1 (сутки отчёта [20; 181)), 1 — H2 ([181; 328)), −1 — вне счёта; по минуте входа (сетка G0)."""
    d = np.asarray(t_open, np.int64) // 1440 - L.DAY0
    return np.where((d >= L.BURN) & (d < 181), 0, np.where((d >= 181) & (d < L.NOUT), 1, -1))


def hour_of(t_open: np.ndarray) -> np.ndarray:
    return (np.asarray(t_open, np.int64) % 1440) // 60


def weekday_of(t_open: np.ndarray) -> np.ndarray:
    """День недели UTC (0 — пн): сутки 0 сетки G0 = 12.12.2025 (пт)."""
    return (4 + np.asarray(t_open, np.int64) // 1440) % 7


def in_funding(t_open: np.ndarray, hw: int = FUND_HW) -> np.ndarray:
    """Вход в пределах ±hw минут от выплаты фондинга 00:00 / 08:00 / 16:00 UTC (восьмичасовой график; у части монет интервал короче — не учтено)."""
    m = np.asarray(t_open, np.int64) % 1440
    ok = np.zeros(len(m), bool)
    for p in FUND_MIN:
        d = np.abs(m - p)
        d = np.minimum(d, 1440 - d)
        ok |= d <= hw
    return ok


def age_bin(t_open: np.ndarray, first_min: int) -> np.ndarray:
    """0 «до кэша» (первая свеча в первые сутки кэша — возраст неизвестен), 1 «<90», 2 «90–365», 3 «>365» суток от первой свечи в кэше до минуты входа."""
    t = np.asarray(t_open, np.int64)
    if first_min < 1440:
        return np.zeros(len(t), int)
    age = (t - first_min) / 1440.0
    return np.where(age < 90, 1, np.where(age < 365, 2, 3))


def terciles(x: np.ndarray, ok: np.ndarray | None = None) -> np.ndarray:
    """Номер трети (0 низ, 1 середина, 2 верх) по квантилям 1/3, 2/3 среди монет с конечным x и ok; остальным −1."""
    x = np.asarray(x, float)
    m = np.isfinite(x) if ok is None else (np.isfinite(x) & ok)
    out = np.full(len(x), -1)
    if m.sum() < 3:
        return out
    lo, hi = np.quantile(x[m], [1 / 3, 2 / 3])
    out[m & (x <= lo)] = 0
    out[m & (x > lo) & (x <= hi)] = 1
    out[m & (x > hi)] = 2
    return out


def perm_both_positive(a: np.ndarray, b: np.ndarray, nperm: int = NPERM, seed: int = L.SEED) -> dict:
    """Монеты с Σ > 0 в обеих половинах среди торговавших в обеих (a, b ≠ 0 — по монетам); нулевое — перестановка b между монетами, nperm раз. p — доля перестановок с числом ≥ наблюдённого."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = (a != 0) & (b != 0)
    aa, bb = a[m], b[m]
    n = len(aa)
    obs = int(((aa > 0) & (bb > 0)).sum())
    rng = np.random.default_rng(seed)
    pa = aa > 0
    pb = bb > 0
    cnt = np.empty(nperm, int)
    for i in range(nperm):
        cnt[i] = int((pa & pb[rng.permutation(n)]).sum())
    exp = float(pa.sum() * pb.sum() / n) if n else float("nan")
    return dict(n=n, obs=obs, expected_indep=exp, null_mean=float(cnt.mean()), null_lo=float(np.percentile(cnt, 2.5)), null_hi=float(np.percentile(cnt, 97.5)),
                p=float((np.sum(cnt >= obs) + 1) / (nperm + 1)))


def slice_table(led: np.ndarray, key: np.ndarray, nkeys: int, half: np.ndarray) -> np.ndarray:
    """(2 половины, nkeys, 3): Σ P&L лотов, число лотов, число лотов в плюсе — по ключу slа (−1 — пропуск) и половине."""
    out = np.zeros((2, nkeys, 3))
    for h in (0, 1):
        for k in range(nkeys):
            m = (half == h) & (key == k)
            if m.any():
                p = led[m, 2]
                out[h, k] = (p.sum(), m.sum(), (p > 0).sum())
    return out


# ───────────────────────────── прогон ─────────────────────────────

def process(args):
    sym, out = args
    dst = os.path.join(out, "res", "map", sym + ".npz")
    if os.path.exists(dst):
        return sym, 0.0
    t0 = time.time()
    S = L.load_coin(sym)
    F = C.coin_features(S, C.btc_block_cached())
    first = int(np.argmax(S["pres"])) if S["pres"].any() else -1
    res = {}
    for bot in BOTS:
        v = C.base_variants()[0] if bot == "C0" else [x for x in C.ablation_variants(1.5, 120) if x["kind"] == "ABf"][0]
        cen, allow, gfr = C.build_components(F, v)
        o, led, total = L.run_coin(S, F, L.make_params(**v["P"]), cen=cen, allow=allow, gfr=gfr, ledger=LEDGER_CAP)
        assert total <= LEDGER_CAP, (sym, bot, total)
        res[bot + "_out"] = o
        res[bot + "_led"] = led.astype(np.float64)
    tk = F["tick"][L.DAY0:]
    med_c = float(np.nanmedian(S["c"][S["pres"]])) if S["pres"].any() else float("nan")
    tick_rel = float(np.nanmedian(tk[tk > 0]) / med_c) if (tk > 0).any() and med_c > 0 else float("nan")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp.npz"
    np.savez_compressed(tmp, first=first, tick_rel=tick_rel, med_close=med_c, **res)
    os.replace(tmp, dst)
    return sym, time.time() - t0


def stage_run(out: str, procs: int):
    syms = L.read_syms()
    t0 = time.time()
    done = 0
    with mp.Pool(procs) as pool:
        for sym, dt in pool.imap_unordered(process, [(s, out) for s in syms]):
            done += 1
            with open(os.path.join(out, "progress-map.txt"), "w") as fh:
                fh.write("%d/%d %.0f%% %s %.1fs elapsed %.0fs\n" % (done, len(syms), 100.0 * done / len(syms), sym, dt, time.time() - t0))
    print("готово", done, "монет за", round(time.time() - t0), "с")


# ───────────────────────────── анализ ─────────────────────────────

def load_map(out: str, syms: list[str]):
    ledgers = {b: [] for b in BOTS}
    outs = {b: [] for b in BOTS}
    first, tick_rel = [], []
    for s in syms:
        z = np.load(os.path.join(out, "res", "map", s + ".npz"))
        for b in BOTS:
            ledgers[b].append(z[b + "_led"])
            outs[b].append(z[b + "_out"])
        first.append(int(z["first"]))
        tick_rel.append(float(z["tick_rel"]))
    return ledgers, {b: np.stack(outs[b]) for b in BOTS}, np.array(first), np.array(tick_rel)


def coin_half_sum(pnl_d: np.ndarray) -> np.ndarray:
    """(ncoin, 2): Σ P&L монеты по суткам половин (§2)."""
    return np.stack([pnl_d[:, L.BURN:181].sum(axis=1), pnl_d[:, 181:L.NOUT].sum(axis=1)], axis=1)


def stage_an(out: str):
    syms, D = A.load_daily(out)
    ledgers, outs, first, tick_rel = load_map(out, syms)
    gate = {}
    for bot, (ph, nm) in {"C0": ("base", "C0"), "MRf": ("frz", "ABf_MR_c1.5_h120")}.items():
        ok = True
        for i, s in enumerate(syms):
            z = np.load(os.path.join("/data/tk729", "res", ph, s + ".npz"))
            j = [str(x) for x in z["runs"]].index(nm)
            ok &= bool(np.array_equal(outs[bot][i][:9], z["out"][j][:9]))
        gate[bot] = ok
    print("ворота: прогон журнала равен готовому TK-729 (строки 0–8, все монеты):", gate)
    # характеристики монет
    turn = np.array([float(np.load(os.path.join("/data/tk729", "res", "base", s + ".npz"))["turn"]) for s in syms])
    import tk730_basket as B

    Fe = B.load_feat(out, syms)  # (7, ncoin, NW)
    sig_eq = np.nanmean(Fe[3], axis=1) * W.STEP  # σ_eq монеты (ln), среднее по неделям
    res = {"gate": gate, "bots": {}}
    tabs = {}
    for bot in BOTS:
        led = np.concatenate([np.hstack([l, np.full((len(l), 1), i, float)]) for i, l in enumerate(ledgers[bot])]) if sum(len(l) for l in ledgers[bot]) else np.zeros((0, 6))
        half = half_of(led[:, 0])
        keep = half >= 0
        led, half = led[keep], half[keep]
        ci = led[:, 5].astype(int)
        r = dict(lots=int(len(led)), usd_lots=[float(led[half == h, 2].sum()) for h in (0, 1)], usd_days=[float(x) for x in coin_half_sum(D[bot][0]).sum(axis=0)])
        r["hour"] = slice_table(led, hour_of(led[:, 0]).astype(int), 24, half)
        r["hour_cyc"] = slice_table(led[led[:, 4] == 0], hour_of(led[led[:, 4] == 0, 0]).astype(int), 24, half[led[:, 4] == 0])
        r["weekday"] = slice_table(led, weekday_of(led[:, 0]).astype(int), 7, half)
        r["funding"] = slice_table(led, in_funding(led[:, 0]).astype(int), 2, half)  # ключ 1 — окно фондинга, 0 — прочее время
        ab = np.full(len(led), -1)
        for i in range(len(syms)):
            m = ci == i
            if m.any():
                ab[m] = age_bin(led[m, 0], int(first[i]))
        r["age"] = slice_table(led, ab, 4, half)
        # трети по монете
        pnl_h = coin_half_sum(D[bot][0])
        gstep = np.full(len(syms), 0.005) if bot == "C0" else np.maximum(1.5 * sig_eq, 0.0015)
        chars = {"оборот": turn, "σ_eq": sig_eq, "тик ÷ шаг сетки": tick_rel / gstep}
        r["terc"] = {}
        for nm, x in chars.items():
            tc = terciles(x)
            tb = np.zeros((2, 3, 3))
            for h in (0, 1):
                for k in range(3):
                    m = tc == k
                    tb[h, k] = (pnl_h[m, h].sum(), m.sum(), (pnl_h[m, h] > 0).sum())
            r["terc"][nm] = tb
        # монеты в плюсе в обеих половинах
        both = (pnl_h[:, 0] > 0) & (pnl_h[:, 1] > 0)
        r["coins_both"] = dict(list=[(syms[i], float(pnl_h[i, 0]), float(pnl_h[i, 1])) for i in np.flatnonzero(both)], perm=perm_both_positive(pnl_h[:, 0], pnl_h[:, 1]),
                               traded_both=int(((pnl_h[:, 0] != 0) & (pnl_h[:, 1] != 0)).sum()), n=len(syms))
        res["bots"][bot] = r
    # БД
    rows = []
    def add(bot, sl, key, tb, names=None):
        for k in range(tb.shape[1]):
            u1, u2 = tb[0, k, 0], tb[1, k, 0]
            rows.append((bot, sl, names[k] if names else str(k), u1, int(tb[0, k, 1]), u2, int(tb[1, k, 1]), int(u1 > 0 and u2 > 0)))
    for bot, r in res["bots"].items():
        add(bot, "час UTC", "h", r["hour"])
        add(bot, "час UTC (только круги)", "h", r["hour_cyc"])
        add(bot, "день недели", "w", r["weekday"], WD)
        add(bot, "фондинг", "f", r["funding"], ("прочее", "±30 мин"))
        add(bot, "возраст листинга", "a", r["age"], AGE_BINS)
        for nm, tb in r["terc"].items():
            add(bot, "треть: " + nm, "t", tb, ("низ", "середина", "верх"))
    A.write_rows(out, "t730_64", ["bot", "slice", "cell", "usd_h1", "n_h1", "usd_h2", "n_h2", "both_positive"], rows)
    crow = []
    for bot, r in res["bots"].items():
        for sym, a, b in r["coins_both"]["list"]:
            crow.append((bot, sym, a, b))
    A.write_rows(out, "t730_64coins", ["bot", "sym", "usd_h1", "usd_h2"], crow)
    json.dump(A.jclean(res), open(os.path.join(out, "r64.json"), "w"), ensure_ascii=False, indent=1)
    for bot, r in res["bots"].items():
        print("==", bot, "лотов", r["lots"], "Σ журнал", np.round(r["usd_lots"]), "Σ сутки", np.round(r["usd_days"]))
        cb = r["coins_both"]
        print("  монет Σ>0 в обеих половинах:", cb["perm"], "торговали в обеих", cb["traded_both"], "из", cb["n"])
        for nm in ("hour", "weekday", "funding", "age"):
            tb = r[nm]
            pos = [(k, round(tb[0, k, 0]), round(tb[1, k, 0])) for k in range(tb.shape[1]) if tb[0, k, 0] > 0 and tb[1, k, 0] > 0]
            print("  ", nm, "ячейки + в обеих:", pos)
        for nm, tb in r["terc"].items():
            print("  треть", nm, [(round(tb[0, k, 0]), round(tb[1, k, 0])) for k in range(3)])
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("run", "an"))
    ap.add_argument("--out", default="/data/tk730")
    ap.add_argument("--procs", type=int, default=3)
    a = ap.parse_args()
    stage_run(a.out, a.procs) if a.stage == "run" else stage_an(a.out)


if __name__ == "__main__":
    main()
