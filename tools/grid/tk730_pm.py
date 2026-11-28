"""TK-730 §7 (П-TK729 PS-1067 / поправки PS-1068, PS-1069): управление позицией на монету — слои PM0 (контроль), PM1 (равный риск), PM4(L) (дневной лимит убытка монеты).

Монеты независимы, у каждой своя ставка 500 $ (§7.2): общего счёта и портфельных правил нет. Слои считаются в ядре одной монеты (`tk729_lib.grid_run`): PM1 — множитель размера ордеров по минутам (`szm`, в момент входа лота),
PM4 — параметр `limit` (дневной убыток монеты ≥ L → всё закрыто по рынку, новых уровней до конца суток UTC нет). Бот — MRf (MR(1,5; 120), центр заморожен, §4), как в §6.3.

    python tk730_pm.py prep --out /data/tk730        # множители PM1 по монетам и неделям (корзина §6.3 и все монеты) из признаков и корзин
    python tk730_pm.py run  --out /data/tk730 --procs 4
    python tk730_pm.py an   --out /data/tk730
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import multiprocessing as mp
import os
import sqlite3
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_cfg as C  # noqa: E402
import tk729_lib as L  # noqa: E402
import tk730_an as A  # noqa: E402
import tk730_lib as W  # noqa: E402

CAP = 2.0  # потолок размера: 2 × 500 $
LIMITS = (25.0, 50.0, 100.0)  # дневной лимит убытка монеты, $ на ставку 500 $
LAYERS = ("PM0", "PM1b", "PM1a", "PM4_25", "PM4_50", "PM4_100")  # PM1b — σ-опора по корзине §6.3, PM1a — по всем монетам (контроль)
NB = (7, 21)
TRIALS_7 = 16  # 4 слоя (PM1 + 3 лимита) × (2 корзины + 2 половины контроля)
TRIALS_ALL = 71 + TRIALS_7


def pm1_mult(sig, ref: float, cap: float = CAP) -> np.ndarray:
    """PM1: множитель размера монеты = σ_ref / σ_eq монеты (равный σ_eq × объём), потолок cap; σ неизвестна или опора неизвестна → 1."""
    sig = np.asarray(sig, float)
    if not np.isfinite(ref) or ref <= 0:
        return np.ones_like(sig)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = ref / sig
    return np.where(np.isfinite(m) & (sig > 0), np.minimum(m, cap), 1.0)


def weekly_mult(sig_w: np.ndarray, sel: np.ndarray, cap: float = CAP) -> np.ndarray:
    """(ncoin, NW) множителей: опора недели k — медиана σ_eq монет множества sel[:, k] (корзина или универсум) за прошлые 7 суток."""
    out = np.ones(sig_w.shape)
    for k in range(sig_w.shape[1]):
        s = sig_w[sel[:, k], k]
        s = s[np.isfinite(s)]
        ref = float(np.median(s)) if len(s) else float("nan")
        out[:, k] = pm1_mult(sig_w[:, k], ref, cap)
    return out


def expand_weekly(mult_c: np.ndarray, n: int) -> np.ndarray:
    """Недельные множители одной монеты → по минутам сетки G0 (длина n); вне недель отчёта — 1."""
    out = np.ones(n)
    for k in range(len(mult_c)):
        a = W.week_start_min(k)
        out[a:a + 7 * 1440] = mult_c[k]
    return out


def mrf_variant() -> dict:
    return [v for v in C.ablation_variants(1.5, 120) if v["kind"] == "ABf"][0]


def layer_variants(mb: np.ndarray, ma: np.ndarray, n: int) -> list[dict]:
    """6 вариантов монеты: PM0 (как в TK-729), PM1b, PM1a, PM4(25 / 50 / 100); mb, ma — недельные множители этой монеты."""
    base = mrf_variant()
    out = [dict(base, name="PM0")]
    out.append(dict(base, name="PM1b", szm=expand_weekly(mb, n)))
    out.append(dict(base, name="PM1a", szm=expand_weekly(ma, n)))
    for lim in LIMITS:
        out.append(dict(base, name="PM4_%d" % lim, P=dict(base["P"], limit=lim)))
    return out


# ───────────────────────────── prep / run ─────────────────────────────

def baskets_sel(out: str, syms: list[str]) -> np.ndarray:
    """(ncoin, NW): монета в проверочной корзине §6.3 недели k (недели H2 — корзина H1 → H2, недели H1 — корзина H2 → H1)."""
    con = sqlite3.connect(os.path.join(out, "tk730.sqlite"))
    idx = {s: i for i, s in enumerate(syms)}
    sel = np.zeros((len(syms), W.NW), bool)
    for d, k, sym in con.execute("select dir, wk, sym from t730_63members"):
        sel[idx[sym], k] = True
    con.close()
    return sel


def stage_prep(out: str):
    syms, _ = A.load_daily(out)
    import tk730_basket as B

    F = B.load_feat(out, syms)
    U = B.universe(F)
    sig = F[3] * W.STEP  # σ_eq (ln-цена), медиана за 7 суток до недели
    sel = baskets_sel(out, syms)
    mb = weekly_mult(sig, sel & U)
    ma = weekly_mult(sig, U)
    np.savez(os.path.join(out, "pm_in.npz"), mb=mb, ma=ma, sel=sel, sig=sig)
    print("корзина: монет-недель %d, множитель корзины: медиана %.3f, p10 %.3f, p90 %.3f, на потолке %.1f %%" % (
        sel.sum(), np.median(mb[sel]), np.percentile(mb[sel], 10), np.percentile(mb[sel], 90), 100.0 * (mb[sel] >= CAP).mean()))


def process(args):
    sym, i, out = args
    dst = os.path.join(out, "res", "pm", sym + ".npz")
    if os.path.exists(dst):
        return sym, 0.0
    t0 = time.time()
    z = np.load(os.path.join(out, "pm_in.npz"))
    S = L.load_coin(sym)
    F = C.coin_features(S, C.btc_block_cached())
    vs = layer_variants(z["mb"][i], z["ma"][i], len(S["c"]))
    res = np.stack([C.run_variant(S, F, v) for v in vs])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp.npz"
    np.savez(tmp, runs=np.array([v["name"] for v in vs]), out=res)
    os.replace(tmp, dst)
    return sym, time.time() - t0


def stage_run(out: str, procs: int):
    syms = L.read_syms()
    t0 = time.time()
    done = 0
    with mp.Pool(procs) as pool:
        for sym, dt in pool.imap_unordered(process, [(s, i, out) for i, s in enumerate(syms)]):
            done += 1
            with open(os.path.join(out, "progress-pm.txt"), "w") as fh:
                fh.write("%d/%d %.0f%% %s %.1fs elapsed %.0fs\n" % (done, len(syms), 100.0 * done / len(syms), sym, dt, time.time() - t0))
    print("готово", done, "монет за", round(time.time() - t0), "с")


# ───────────────────────────── анализ ─────────────────────────────

def load_pm(out: str):
    """→ pnl (слои → (ncoin, 328)), act (слои → (ncoin, 328)), nlim (слои → (ncoin, 328) срабатываний лимита, строка 9)."""
    import glob

    fs = sorted(glob.glob(os.path.join(out, "res", "pm", "*.npz")))
    zs = [np.load(f) for f in fs]
    names = [str(x) for x in zs[0]["runs"]]
    pnl, act, nlim = {}, {}, {}
    for j, nm in enumerate(names):
        pnl[nm] = np.stack([z["out"][j, L.R_PNL] for z in zs])
        act[nm] = np.stack([z["out"][j, L.R_ACT] for z in zs])
        nlim[nm] = np.stack([z["out"][j, L.R_NREG] for z in zs])
    return [os.path.basename(f)[:-4] for f in fs], pnl, act, nlim


def metrics_pm(daily: np.ndarray, margin: float) -> dict:
    """В-169 + хвост: Σ, Шарп, просадка $ и % маржи, перехай (макс / ср / p90), худшие сутки, Σ 5 худших суток, R:R (средний плюс / средний минус), доля суток в минусе."""
    dd = L.drawdown(daily)
    pos, neg = daily[daily > 0], daily[daily < 0]
    s = np.sort(daily)
    return dict(usd=float(daily.sum()), sharpe=L.sharpe_ann(daily), dd=dd["dd"], dd_pct=100.0 * dd["dd"] / margin if margin > 0 else float("nan"), margin=float(margin),
                hw_max=dd["hw_max"], hw_mean=dd["hw_mean"], hw_p90=dd["hw_p90"], worst=float(s[0]), worst5=float(s[:5].sum()),
                rr=float(pos.mean() / -neg.mean()) if len(pos) and len(neg) else float("nan"), loss_share=float((daily < 0).mean()), days=int(len(daily)))


def paired_layer(layer_d: np.ndarray, base_d: np.ndarray) -> dict:
    d = layer_d - base_d
    return {b: L.stat_block(d, b, L.NBOOT, L.SEED) for b in NB}


def layer_useful(m_l: dict, m_0: dict, pr: dict) -> dict:
    """«Слой полезен» на (корзина, половина): (А) просадка (% маржи) ниже PM0 и нижняя граница Σ-разницы в сутки ≥ −10 % |среднесуточного Σ PM0| в обоих блоках; или (Б) Шарп выше PM0 и нижняя граница разницы > 0 в обоих блоках."""
    thr = -0.10 * abs(m_0["usd"] / m_0["days"])
    a = bool(m_l["dd_pct"] < m_0["dd_pct"] and all(pr[b]["lo"] >= thr for b in NB))
    b = bool(m_l["sharpe"] > m_0["sharpe"] and all(pr[b_]["lo"] > 0 for b_ in NB))
    return dict(a=a, b=b, useful=bool(a or b), thr=float(thr))


def A_days(tw: list[int]) -> np.ndarray:
    return np.concatenate([np.arange(W.week_start_day(k), W.week_start_day(k) + 7) for k in tw])


def mult_by_day(mult: np.ndarray, days: np.ndarray) -> np.ndarray:
    """(ncoin, len(days)): недельный множитель по суткам отчёта."""
    wk = {W.week_start_day(k) + j: k for k in range(W.NW) for j in range(7)}
    return np.stack([mult[:, wk[int(d)]] for d in days], axis=1)


def set_daily(pnl_l: np.ndarray, tw: list[int], mem: dict | None) -> np.ndarray:
    """Суточный Σ множества: корзина (mem[k] — индексы монет недели k) или все монеты; по неделям tw подряд."""
    days = A_days(tw)
    if mem is None:
        return pnl_l[:, days].sum(axis=0)
    out = np.zeros(len(days))
    for wi, k in enumerate(tw):
        idx = np.asarray(mem[k], int)
        out[wi * 7:wi * 7 + 7] = pnl_l[idx][:, days[wi * 7:wi * 7 + 7]].sum(axis=0)
    return out


def set_margin(tw: list[int], mem: dict | None, act_l: np.ndarray, mult: np.ndarray | None) -> float:
    """Маржа множества, $: корзина — 250 $ × Σ множителей размера монет корзины (без PM1 — 250 × K); все монеты — 250 $ × (монет в работе в сутки, с множителем), среднее по суткам."""
    if mem is not None:
        tot = 0.0
        for k in tw:
            idx = np.asarray(mem[k], int)
            tot += 250.0 * (len(idx) if mult is None else float(mult[idx, k].sum()))
        return tot / len(tw)
    days = A_days(tw)
    a = act_l[:, days]
    if mult is not None:
        a = a * mult_by_day(mult, days)
    return 250.0 * float(a.sum(axis=0).mean())


def month_sums_all(daily: np.ndarray, days: np.ndarray) -> dict:
    mon = {}
    for x, d in zip(daily, days):
        mm = (datetime.date(2026, 1, 1) + datetime.timedelta(days=int(d))).strftime("%Y-%m")
        mon[mm] = mon.get(mm, 0.0) + float(x)
    return mon


def eval_set(name: str, tw: list[int], mem: dict | None, pnl: dict, act: dict, nlim: dict, mult: dict) -> dict:
    """Слои множества (корзина §6.3 на половине tw или все монеты MRf на половине): метрики, сравнение с PM0, «слой полезен»."""
    layers = ("PM0", "PM1b", "PM4_25", "PM4_50", "PM4_100") if mem is not None else ("PM0", "PM1a", "PM4_25", "PM4_50", "PM4_100")
    days = A_days(tw)
    info = {}
    for lay in layers:
        daily = set_daily(pnl[lay], tw, mem)
        marg = set_margin(tw, mem, act[lay], mult.get(lay))
        m = metrics_pm(daily, marg)
        x = dict(m=m, daily=daily, months=month_sums_all(daily, days))
        if lay.startswith("PM4"):
            if mem is None:
                x["nlim"] = int(nlim[lay][:, days].sum())
            else:
                x["nlim"] = int(sum(nlim[lay][np.asarray(mem[k], int)][:, A_days([k])].sum() for k in tw))
        else:
            x["nlim"] = 0
        info[lay] = x
    for lay in layers[1:]:
        info[lay]["pr"] = paired_layer(info[lay]["daily"], info["PM0"]["daily"])
        info[lay]["useful"] = layer_useful(info[lay]["m"], info["PM0"]["m"], info[lay]["pr"])
    return info


def stage_an(out: str):
    syms, D = A.load_daily(out)
    psyms, pnl, act, nlim = load_pm(out)
    assert psyms == syms, "наборы монет не совпадают"
    z = np.load(os.path.join(out, "pm_in.npz"))
    mult = {"PM1b": z["mb"], "PM1a": z["ma"]}
    gate = bool(np.array_equal(pnl["PM0"], D["MRf"][0]))  # ворота «байт в байт»: PM0 в новом ядре = готовый ряд MRf TK-729
    print("ворота PM0 == MRf TK-729 (байт в байт):", gate)
    con = sqlite3.connect(os.path.join(out, "tk730.sqlite"))
    members = {}
    for d, k, sym in con.execute("select dir, wk, sym from t730_63members"):
        members.setdefault(d, {}).setdefault(k, []).append(syms.index(sym))
    con.close()
    sets = [("basket H1>H2", W.H2W, members["H1>H2"]), ("basket H2>H1", W.H1W, members["H2>H1"]), ("all MRf H1", W.H1W, None), ("all MRf H2", W.H2W, None)]
    res = {"gate": gate, "sets": {}}
    rows = []
    for name, tw, mem in sets:
        info = eval_set(name, tw, mem, pnl, act, nlim, mult)
        res["sets"][name] = {lay: {k: v for k, v in x.items() if k != "daily"} for lay, x in info.items()}
        for lay, x in info.items():
            m = x["m"]
            pr, u = x.get("pr"), x.get("useful")
            rows.append((name, lay, m["usd"], m["sharpe"], m["dd"], m["dd_pct"], m["margin"], m["hw_max"], m["hw_mean"], m["hw_p90"], m["worst"], m["worst5"], m["rr"], m["loss_share"], x["nlim"],
                         json.dumps(A.jclean(x["months"]), ensure_ascii=False), None if pr is None else pr[7]["mean"], None if pr is None else pr[7]["lo"], None if pr is None else pr[7]["hi"],
                         None if pr is None else pr[21]["lo"], None if pr is None else pr[21]["hi"], None if u is None else int(u["a"]), None if u is None else int(u["b"]), None if u is None else int(u["useful"])))
    A.write_rows(out, "t730_7res", ["set_", "layer", "usd", "sharpe", "dd", "dd_pct", "margin", "hw_max", "hw_mean", "hw_p90", "worst", "worst5", "rr", "loss_share", "n_limit", "months",
                                   "diff7_mean", "diff7_lo", "diff7_hi", "diff21_lo", "diff21_hi", "cond_a", "cond_b", "useful"], rows)
    useful = {}
    for lay in ("PM1b", "PM4_25", "PM4_50", "PM4_100"):
        useful[lay] = bool(all(res["sets"][s][lay]["useful"]["useful"] for s in ("basket H1>H2", "basket H2>H1")))
    res["useful_both"] = useful
    import tk729_an as T

    res["noise_sharpe"] = {str(TRIALS_ALL): [T.noise_bar(len(A_days(W.H2W)), TRIALS_ALL), T.noise_bar(len(A_days(W.H1W)), TRIALS_ALL)]}
    json.dump(A.jclean(res), open(os.path.join(out, "r7.json"), "w"), ensure_ascii=False, indent=1)
    for name, tw, mem in sets:
        print("==", name)
        for lay, x in res["sets"][name].items():
            m, u = x["m"], x.get("useful")
            print("  %-7s Σ %8.0f Sharpe %6.2f DD %7.0f (%.2f%%) worst %7.0f worst5 %7.0f R:R %.2f loss%% %.0f n_lim %d%s" % (
                lay, m["usd"], m["sharpe"], m["dd"], m["dd_pct"], m["worst"], m["worst5"], m["rr"], 100 * m["loss_share"], x["nlim"],
                "" if u is None else "  diff7 [%.2f; %.2f] diff21 lo %.2f useful=%s (A=%s B=%s)" % (x["pr"][7]["lo"], x["pr"][7]["hi"], x["pr"][21]["lo"], u["useful"], u["a"], u["b"])))
    print("полезен в обе стороны на корзине:", useful, "шум Шарпа на %d:" % TRIALS_ALL, res["noise_sharpe"])
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("prep", "run", "an"))
    ap.add_argument("--out", default="/data/tk730")
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    if a.stage == "prep":
        stage_prep(a.out)
    elif a.stage == "run":
        stage_run(a.out, a.procs)
    else:
        stage_an(a.out)


if __name__ == "__main__":
    main()
