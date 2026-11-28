"""TK-730 §6.2 (признаки режима: IC) и §6.3 (корзина): расчёт по готовым суточным рядам ботов и признакам по неделям. Толкования — летопись L-489."""
from __future__ import annotations

import datetime
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_lib as L  # noqa: E402
import tk730_an as A  # noqa: E402
import tk730_lib as W  # noqa: E402

NB = (7, 21)
KS = (20, 50)
DIRS = (("H1", "H2"), ("H2", "H1"))  # (обучение, проверка)
TRIALS_62 = 16
TRIALS_63 = 32
MIN_MONTH_DAYS = 14


def load_feat(out: str, syms: list[str]) -> np.ndarray:
    """feat/<SYM>.npz → (7, ncoin, NW): chop, vr, hl, cover, turn, beta, trend."""
    f = np.stack([np.load(os.path.join(out, "feat", s + ".npz"))["f"] for s in syms])  # (ncoin, NW, 7)
    return np.transpose(f, (2, 0, 1)).copy()


def prev_week_pnl(pnl_d: np.ndarray) -> np.ndarray:
    """P&L бота за 7 суток до начала недели k (признак 1), (ncoin, NW)."""
    out = np.zeros((pnl_d.shape[0], W.NW))
    for k in range(W.NW):
        ws = W.week_start_day(k)
        out[:, k] = pnl_d[:, ws - 7:ws].sum(axis=1)
    return out


def feature_stack(pnl_d: np.ndarray, F: np.ndarray, U: np.ndarray) -> np.ndarray:
    """(8, ncoin, NW) в порядке W.FEATS; вне универсума U — NaN."""
    fs = np.concatenate([prev_week_pnl(pnl_d)[None], F], axis=0).astype(float)
    return np.where(U[None], fs, np.nan)


def universe(F: np.ndarray) -> np.ndarray:
    """Монета-неделя в универсуме: пилообразность конечна (≥ 7 суток истории, ≥ ½ минут окна)."""
    return np.isfinite(F[0])


def sign_of(x: float) -> int:
    return -1 if x < 0 else 1


# ───────────────────────────── 6.2 ─────────────────────────────

def stage62_core(feats: dict, pw: dict, tw: dict) -> dict:
    """feats[bot] — (8, ncoin, NW). → по боту и признаку: IC по неделям, среднее, интервал, p, Холм, половины, знак повторился, «проходит»."""
    res = {}
    for bot, fs in feats.items():
        rows = []
        for i, name in enumerate(W.FEATS):
            ic = W.ic_weekly(fs[i], pw[bot], tw[bot])
            full = W.boot_week(ic)
            h1 = W.boot_week(ic[W.H1W])
            h2 = W.boot_week(ic[W.H2W])
            rows.append(dict(feat=name, ic=ic, full=full, h1=h1, h2=h2))
        adj = W.holm([r["full"]["p"] for r in rows])
        for r, a in zip(rows, adj):
            r["p_holm"] = a
            r["sign_repeat"] = bool(np.isfinite(r["h1"]["mean"]) and np.isfinite(r["h2"]["mean"]) and np.sign(r["h1"]["mean"]) == np.sign(r["h2"]["mean"]) != 0)
            r["pass"] = bool(a < 0.05 and r["sign_repeat"])
        res[bot] = rows
    return res


# ───────────────────────────── 6.3 ─────────────────────────────

def days_of(weeks: list[int]) -> np.ndarray:
    """Сутки отчёта недель по порядку."""
    return np.concatenate([np.arange(W.week_start_day(k), W.week_start_day(k) + 7) for k in weeks])


def basket_run(pnl_d: np.ndarray, fe: np.ndarray, U: np.ndarray, sign: int, K: int, weeks: list[int]) -> dict:
    """Корзина по неделям: суточный Σ P&L её монет, P&L монет в корзине (по монетам), состав по неделям, бенчмарк K × среднее по универсуму недели (суточный)."""
    daily, bench = [], []
    coin_tot = np.zeros(pnl_d.shape[0])
    members = {}
    for k in weeks:
        ws = W.week_start_day(k)
        idx = W.pick_basket(fe, k, sign, K, U)
        members[k] = idx
        blk = pnl_d[:, ws:ws + 7]
        daily.append(blk[idx].sum(axis=0) if len(idx) else np.zeros(7))
        coin_tot[idx] += blk[idx].sum(axis=1)
        uk = np.flatnonzero(U[:, k])
        bench.append(K * blk[uk].mean(axis=0) if len(uk) else np.zeros(7))
    return dict(daily=np.concatenate(daily), bench=np.concatenate(bench), coin_tot=coin_tot, members=members, days=days_of(weeks))


def month_sums(daily: np.ndarray, days: np.ndarray) -> dict:
    """Σ по календарным месяцам (месяцы с ≥ 14 сутками в ряду)."""
    mon = {}
    for x, d in zip(daily, days):
        dt = datetime.date(2026, 1, 1) + datetime.timedelta(days=int(d))
        m = dt.strftime("%Y-%m")
        s, c = mon.get(m, (0.0, 0))
        mon[m] = (s + float(x), c + 1)
    return {m: s for m, (s, c) in mon.items() if c >= MIN_MONTH_DAYS}


def metrics_b169(daily: np.ndarray, K: int) -> dict:
    """В-169: Σ, Шарп, просадка $ и % маржи 250 $ × K, перехай (макс / ср / p90)."""
    dd = L.drawdown(daily)
    margin = 250.0 * K
    return dict(usd=float(daily.sum()), sharpe=L.sharpe_ann(daily), dd=dd["dd"], dd_pct=100.0 * dd["dd"] / margin, margin=margin,
                hw_max=dd["hw_max"], hw_mean=dd["hw_mean"], hw_p90=dd["hw_p90"])


def check_basket(run: dict, K: int) -> dict:
    """Четыре условия §6.3 на проверочной половине (нижние границы — блоки 7 и 21)."""
    d = run["daily"]
    boot = {b: L.stat_block(d, b, L.NBOOT, L.SEED) for b in NB}
    pd = d - run["bench"]
    pair = {b: L.stat_block(pd, b, L.NBOOT, L.SEED) for b in NB}
    mon = month_sums(d, run["days"])
    mplus = sum(v > 0 for v in mon.values())
    ct = np.sort(run["coin_tot"])[::-1]
    ex3 = float(d.sum() - ct[:3].sum())
    c1 = all(boot[b]["lo"] > 0 for b in NB)
    c2 = len(mon) > 0 and mplus * 3 >= 2 * len(mon)
    c3 = ex3 > 0
    c4 = all(pair[b]["lo"] > 0 for b in NB)
    return dict(boot=boot, pair=pair, months=mon, months_plus=int(mplus), ex3=ex3, best3=float(ct[:3].sum()), c1=bool(c1), c2=bool(c2), c3=bool(c3), c4=bool(c4),
                ok=bool(c1 and c2 and c3 and c4), usd_bench=float(run["bench"].sum()), metrics=metrics_b169(d, K), bench_metrics=metrics_b169(run["bench"], K))


def verdict_words(chk_dirs: list[dict]) -> str:
    """«работает» — все условия в обе стороны; «не ясно» — блоки 7 и 21 дают разные знаки нижней границы Σ (в какой-то стороне) или направления противоположны по знаку Σ; иначе «нет»."""
    if all(c["ok"] for c in chk_dirs):
        return "работает"
    mixed = any((c["boot"][7]["lo"] > 0) != (c["boot"][21]["lo"] > 0) for c in chk_dirs)
    signs = {np.sign(c["metrics"]["usd"]) for c in chk_dirs}
    if mixed or len(signs) > 1:
        return "не ясно"
    return "нет"


def select_candidates(feats: dict, pd: dict, ic62: dict, U: np.ndarray, train: str, test: str) -> dict:
    """32 кандидата (бот × признак × K) на обучающей половине: знак — среднее IC обучения, Σ обучения; затем лучший по Σ и его проверка; Σ проверки всех — описательно."""
    wtr, wte = (W.H1W if train == "H1" else W.H2W), (W.H1W if test == "H1" else W.H2W)
    rows = []
    for bot in feats:
        for i, name in enumerate(W.FEATS):
            ic = ic62[bot][i]["ic"]
            m = float(np.nanmean(ic[wtr]))
            sg = sign_of(m)
            for K in KS:
                tr = basket_run(pd[bot], feats[bot][i], U, sg, K, wtr)
                te = basket_run(pd[bot], feats[bot][i], U, sg, K, wte)
                rows.append(dict(bot=bot, feat=name, fi=i, sign=sg, K=K, ic_train=m, usd_train=float(tr["daily"].sum()), usd_test=float(te["daily"].sum())))
    best = max(rows, key=lambda r: (r["usd_train"], -rows.index(r)))
    te = basket_run(pd[best["bot"]], feats[best["bot"]][best["fi"]], U, best["sign"], best["K"], wte)
    tr = basket_run(pd[best["bot"]], feats[best["bot"]][best["fi"]], U, best["sign"], best["K"], wtr)
    chk = check_basket(te, best["K"])
    return dict(train=train, test=test, rows=rows, best=best, check=chk, run_test=te, run_train=tr, metrics_train=metrics_b169(tr["daily"], best["K"]))


def stage63_core(feats: dict, pd: dict, ic62: dict, U: np.ndarray) -> dict:
    dirs = [select_candidates(feats, pd, ic62, U, a, b) for a, b in DIRS]
    verdict = verdict_words([d["check"] for d in dirs])
    return dict(dirs=dirs, verdict=verdict)


def noise_bar_sharpe(n_days: int, trials: int) -> float:
    import tk729_an as T

    return T.noise_bar(n_days, trials)


# ───────────────────────────── стадии CLI ─────────────────────────────

def _prep(out: str):
    syms, D = A.load_daily(out)
    F = load_feat(out, syms)
    U = universe(F)
    pw, tw, pd = {}, {}, {}
    feats = {}
    for bot in A.BOT_NAMES:
        pnl_d, nop_d = D[bot]
        pw[bot], tw[bot] = A.bot_weekly(pnl_d, nop_d)
        pd[bot] = pnl_d
        feats[bot] = feature_stack(pnl_d, F, U)
    return syms, F, U, pw, tw, pd, feats


def stage_s62(out: str):
    syms, F, U, pw, tw, pd, feats = _prep(out)
    res = stage62_core(feats, pw, tw)
    rows, rrows = [], []
    for bot, lst in res.items():
        for r in lst:
            rows.append((bot, r["feat"], r["full"]["mean"], r["full"]["lo"], r["full"]["hi"], r["full"]["p"], r["p_holm"], r["h1"]["mean"], r["h1"]["lo"], r["h1"]["hi"],
                         r["h2"]["mean"], r["h2"]["lo"], r["h2"]["hi"], int(r["sign_repeat"]), int(r["pass"]), r["full"]["n"]))
            for k, v in enumerate(r["ic"]):
                rrows.append((bot, r["feat"], k, W.week_date(k), float(v)))
    A.write_rows(out, "t730_62", ["bot", "feat", "ic", "lo", "hi", "p", "p_holm", "ic_h1", "lo_h1", "hi_h1", "ic_h2", "lo_h2", "hi_h2", "sign_repeat", "passes", "n_weeks"], rows)
    A.write_rows(out, "t730_62w", ["bot", "feat", "k", "week", "ic"], rrows)
    A.write_rows(out, "t730_universe", ["k", "week", "coins_u", "coins_traded_c0", "coins_traded_mrf"],
                 [(k, W.week_date(k), int(U[:, k].sum()), int(tw["C0"][:, k].sum()), int(tw["MRf"][:, k].sum())) for k in range(W.NW)])
    json.dump(A.jclean({b: [{k: v for k, v in r.items() if k != "ic"} for r in lst] for b, lst in res.items()}), open(os.path.join(out, "r62.json"), "w"), ensure_ascii=False, indent=1)
    for bot, lst in res.items():
        for r in lst:
            print(bot, "%-9s IC %+.3f [%+.3f; %+.3f] p_holm %.3f  H1 %+.3f H2 %+.3f %s" % (r["feat"], r["full"]["mean"], r["full"]["lo"], r["full"]["hi"], r["p_holm"], r["h1"]["mean"], r["h2"]["mean"], "PASS" if r["pass"] else ""))
    return res


def stage_s63(out: str):
    syms, F, U, pw, tw, pd, feats = _prep(out)
    ic62 = stage62_core(feats, pw, tw)
    res = stage63_core(feats, pd, ic62, U)
    crow, mrow, brow = [], [], []
    for d in res["dirs"]:
        tag = "%s>%s" % (d["train"], d["test"])
        for r in d["rows"]:
            crow.append((tag, r["bot"], r["feat"], r["sign"], r["K"], r["ic_train"], r["usd_train"], r["usd_test"], int(r is d["best"] or (r["bot"], r["feat"], r["K"]) == (d["best"]["bot"], d["best"]["feat"], d["best"]["K"]))))
        b = d["best"]
        wte = W.H1W if d["test"] == "H1" else W.H2W
        for k, idx in d["run_test"]["members"].items():
            for ci in idx:
                mrow.append((tag, b["bot"], b["feat"], b["K"], k, W.week_date(k), syms[ci]))
        c = d["check"]
        brow.append((tag, b["bot"], b["feat"], b["sign"], b["K"], d["metrics_train"]["usd"], c["metrics"]["usd"], c["metrics"]["sharpe"], c["metrics"]["dd"], c["metrics"]["dd_pct"],
                     c["metrics"]["hw_max"], c["metrics"]["hw_mean"], c["metrics"]["hw_p90"], c["boot"][7]["lo"], c["boot"][21]["lo"], c["months_plus"], len(c["months"]), c["ex3"],
                     c["pair"][7]["mean"], c["pair"][7]["lo"], c["pair"][21]["lo"], c["usd_bench"], c["bench_metrics"]["sharpe"], int(c["c1"]), int(c["c2"]), int(c["c3"]), int(c["c4"]), int(c["ok"])))
    A.write_rows(out, "t730_63cand", ["dir", "bot", "feat", "sign", "K", "ic_train", "usd_train", "usd_test", "selected"], crow)
    A.write_rows(out, "t730_63members", ["dir", "bot", "feat", "K", "wk", "week", "sym"], mrow)
    A.write_rows(out, "t730_63res", ["dir", "bot", "feat", "sign", "K", "usd_train", "usd_test", "sharpe", "dd", "dd_pct", "hw_max", "hw_mean", "hw_p90", "lo7", "lo21", "months_plus", "months_n", "ex3",
                                     "pair_mean", "pair_lo7", "pair_lo21", "usd_bench", "sharpe_bench", "c_lb", "c_months", "c_ex3", "c_vs_all", "ok"], brow)
    nd = [len(d["run_test"]["daily"]) for d in res["dirs"]]
    out_j = dict(verdict=res["verdict"], noise_sharpe_48=[noise_bar_sharpe(n, TRIALS_62 + TRIALS_63) for n in nd], noise_sharpe_71=[noise_bar_sharpe(n, 71) for n in nd],
                 dirs=[dict(train=d["train"], test=d["test"], best=d["best"], check={k: v for k, v in d["check"].items()}, metrics_train=d["metrics_train"]) for d in res["dirs"]])
    json.dump(A.jclean(out_j), open(os.path.join(out, "r63.json"), "w"), ensure_ascii=False, indent=1)
    print("verdict:", res["verdict"])
    for d in res["dirs"]:
        b, c = d["best"], d["check"]
        print(d["train"], ">", d["test"], b["bot"], b["feat"], "sign", b["sign"], "K", b["K"], "train %.0f test %.0f sharpe %.2f dd %.0f (%.1f%%) lo7 %.2f lo21 %.2f months %d/%d ex3 %.0f vs_all lo7 %.2f lo21 %.2f ok=%s" % (
            d["metrics_train"]["usd"], c["metrics"]["usd"], c["metrics"]["sharpe"], c["metrics"]["dd"], c["metrics"]["dd_pct"], c["boot"][7]["lo"], c["boot"][21]["lo"], c["months_plus"], len(c["months"]), c["ex3"],
            c["pair"][7]["lo"], c["pair"][21]["lo"], c["ok"]))
    return res
