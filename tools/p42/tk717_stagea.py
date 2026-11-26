#!/usr/bin/env python3
"""TK-717 стадия A (протокол P-42 § TK-717, критик `docs/research/reviews/regime-window-critic-2026-11-26.md`): «окно от точки смены режима» — без движка, по посуточным P&L `mass_day` TK-700 (пул A `all1`, сутки 0–180 = 01.01–30.06.2026, только чтение).

  pack    --db mass.sqlite --side short --runs h1-jan-A-short,… --out /data/p42-tk717/pool-short       # один раз на сторону: X / N / P в .npy
  t1      --pool /data/p42-tk717/pool-short --side short --out /data/p42-tk717/tk717.sqlite             # устойчивость рангов клеток по лагам 1–8 недель
  t2      --pool … --side short --out … --tk692 /data/p42-tk692/tk692.sqlite --extra /data/p42-tk692/replay.sqlite   # скользящие окна L против накопленного
  t3      --pool … --side short --out … --extra …                                                      # фильтр по кривой капитала замороженных линий (справочно)
  verdict --out …                                                                                       # Holm по 8 испытаниям T2 и правило (а)+(б) критика
  report  --out …                                                                                       # таблицы для findings

Правило отбора, охрана, шаг «неделя», E = 1 — как в PS-1007 §3 (`tk692_walk.py`); метрики — `tk692_walk.series_stats` (Шарп: среднее ÷ σ (ddof = 1) × √365 по всем суткам, просадка / перехай — `underwater`)."""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import pickle
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, "/data/p42-tk692/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import tk692_walk as W  # noqa: E402

SEED = 717
NBOOT = 10000                                                       # бутстреп блоками ⌈n^{1/3}⌉
NPERM = 2000                                                        # перестановки недель блоками по 1 неделе (T1)
WEEK0 = 4                                                           # понедельник 05.01.2026 — сутки 4 (D0 = 01.01.2026, четверг)
NWEEK = 25                                                          # недели k = 0…24 (понедельник сутки 4 + 7k; последний — 22.06)
WLEN = 6                                                            # окно недели Пн–Сб; воскресенье — отступ E = 1 до следующей границы
HALF_CUT = 120                                                      # 01.05.2026: H1 = февраль–апрель (сутки 31–119), H2 = май–июнь (120–180)
FEB1 = 31
T1_FIRST = min(k for k in range(NWEEK) if WEEK0 + 7 * k >= FEB1)       # первая неделя-цель T1: понедельник 02.02 (сутки 32), k = 4; январские цели — разгон (поправка PS-1035)
HALVES = ("H1", "H2", "all")
LAGS = tuple(range(1, 9))
LS = (7, 14, 30, 60)                                                # длины скользящего окна T2, сут.
NS = (7, 14, 30)                                                    # длины окна фильтра капитала T3, сут.
RULE_L = (7, 14, 30)                                                # L ≤ 30 — те, что могут пройти правило (б)
SIDES = ("long", "short")
EMB = 1
HOLM_K = tuple(range(1, 9))                                         # α_j = 0,05 / k, k = 1…8 (8 испытаний T2)

TABLES = """
CREATE TABLE IF NOT EXISTS tk717_run(rid TEXT, side TEXT, stage TEXT, params TEXT, code TEXT, created TEXT, PRIMARY KEY(rid, side, stage));
CREATE TABLE IF NOT EXISTS tk717_t1_m(rid TEXT, side TEXT, src INTEGER, tgt INTEGER, rho REAL, n_univ INTEGER, PRIMARY KEY(rid, side, src, tgt));
CREATE TABLE IF NOT EXISTS tk717_t1(rid TEXT, side TEXT, lag INTEGER, half TEXT, n_pairs INTEGER, rho REAL, lo REAL, hi REAL, null_mean REAL, null_lo REAL, null_hi REAL, p_perm REAL, PRIMARY KEY(rid, side, lag, half));
CREATE TABLE IF NOT EXISTS tk717_t2_pick(rid TEXT, side TEXT, series TEXT, b INTEGER, e INTEGER, fullwin INTEGER, cid INTEGER, w INTEGER, score REAL, sum_w REAL, n_w INTEGER, m INTEGER, PRIMARY KEY(rid, side, series, b));
CREATE TABLE IF NOT EXISTS tk717_t2_sum(rid TEXT, side TEXT, L INTEGER, half TEXT, series TEXT, days INTEGER, n INTEGER, sum REAL, per_trade REAL, sharpe REAL, dd REAL, ddp REAL,
    gap_longest INTEGER, gap_mean REAL, gap_tail INTEGER, top_day_share REAL, extra TEXT, PRIMARY KEY(rid, side, L, half, series));
CREATE TABLE IF NOT EXISTS tk717_t2_trial(rid TEXT, side TEXT, L INTEGER, p_h1 REAL, p_h2 REAL, p_trial REAL, d_h1 REAL, d_h2 REAL, lb TEXT, lo_s_h1 REAL, lo_s_h2 REAL, lo_d_h1 REAL, lo_d_h2 REAL, main_slide INTEGER, main_expand INTEGER, extra TEXT, PRIMARY KEY(rid, side, L));
CREATE TABLE IF NOT EXISTS tk717_t2_holm(rid TEXT, side TEXT, L INTEGER, p_trial REAL, rank INTEGER, alpha_j REAL, holm INTEGER, lb_h1 REAL, lb_h2 REAL, pass_b INTEGER, PRIMARY KEY(rid, side, L));
CREATE TABLE IF NOT EXISTS tk717_t3(rid TEXT, side TEXT, line TEXT, nwin INTEGER, half TEXT, series TEXT, days INTEGER, traded_days INTEGER, n INTEGER, sum REAL, per_trade REAL, sharpe REAL, dd REAL, ddp REAL,
    gap_longest INTEGER, gap_mean REAL, gap_tail INTEGER, extra TEXT, PRIMARY KEY(rid, side, line, nwin, half, series));
CREATE TABLE IF NOT EXISTS tk717_verdict(rid TEXT, side TEXT, part TEXT, ok INTEGER, note TEXT, PRIMARY KEY(rid, side, part));
"""


# ───────────────────────────── геометрия ─────────────────────────────

def week_days(k):
    """Окно недели k: сутки [4 + 7k, 4 + 7k + 6) — понедельник–суббота."""
    d0 = WEEK0 + 7 * k
    return d0, d0 + WLEN


def half_of_week(k):
    return "H1" if WEEK0 + 7 * k < HALF_CUT else "H2"


def half_days(nd, half):
    d = np.arange(nd)
    if half == "H1":
        return (d >= FEB1) & (d < HALF_CUT)
    if half == "H2":
        return d >= HALF_CUT
    return d >= FEB1


# ───────────────────────────── T1: устойчивость рангов ─────────────────────────────

def rank_avg(x):
    """Ранги с усреднением при равенстве."""
    x = np.asarray(x, float)
    n = len(x)
    o = np.argsort(x, kind="stable")
    xs = x[o]
    new = np.concatenate(([True], xs[1:] != xs[:-1]))
    grp = np.cumsum(new) - 1
    start = np.nonzero(new)[0]
    end = np.append(start[1:], n)
    avg = (start + end - 1) / 2.0 + 1.0
    r = np.empty(n)
    r[o] = avg[grp]
    return r


def pearson(a, b):
    a = a - a.mean()
    b = b - b.mean()
    da, db = math.sqrt(float((a * a).sum())), math.sqrt(float((b * b).sum()))
    return float((a * b).sum() / (da * db)) if da > 0 and db > 0 else float("nan")


def week_sums(pool):
    """Σ $ и число сделок каждой клетки за каждую неделю: массивы (NWEEK, C)."""
    S = np.stack([pool.X[:, d0:d1].sum(axis=1) for d0, d1 in (week_days(k) for k in range(NWEEK))])
    Nn = np.stack([pool.N[:, d0:d1].sum(axis=1) for d0, d1 in (week_days(k) for k in range(NWEEK))])
    return S, Nn


def spearman_matrix(pool, side, min_cells=30):
    """M[a, b] — Спирмен между Σ клеток недели a (источник) и недели b (цель) по клеткам, посчитанным во всех месяцах и прошедшим охрану (a) на неделе-источнике: n ≥ ⌈r · 6⌉."""
    S, Nn = week_sums(pool)
    elig = pool.P.all(axis=1)
    M = np.full((NWEEK, NWEEK), np.nan)
    NU = np.zeros(NWEEK, int)
    for a in range(NWEEK):
        U = np.nonzero(elig & (Nn[a] >= W.n_min(side, WLEN)))[0]
        NU[a] = len(U)
        if len(U) < min_cells:
            continue
        ra = rank_avg(S[a, U])
        for b in range(NWEEK):
            if b != a:
                M[a, b] = pearson(ra, rank_avg(S[b, U]))
    return M, NU


def t1_stats(M, seed=SEED, nboot=NBOOT, nperm=NPERM):
    """Строки (лаг, половина): среднее ρ по парам (a, a + лаг) с целью в половине, бутстреп блоками ⌈n^{1/3}⌉ (границы 2,5 / 97,5 %), нуль — перестановка недель (каждая неделя — блок)."""
    rng = np.random.default_rng([seed, 1])
    perms = np.stack([rng.permutation(NWEEK) for _ in range(nperm)])
    rows = []
    for lag in LAGS:
        for half in HALVES:
            tg = np.array([t for t in range(max(lag, T1_FIRST), NWEEK) if half == "all" or half_of_week(t) == half], int)
            v = M[tg - lag, tg]
            ok = ~np.isnan(v)
            tg, v = tg[ok], v[ok]
            if len(v) < 2:
                rows.append((lag, half, int(len(v)), float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), float("nan")))
                continue
            obs = float(v.mean())
            brng = np.random.default_rng([seed, 2, lag, HALVES.index(half)])
            bm = W.boot_sums(v, nboot, brng) / len(v)
            nul = np.nanmean(M[perms[:, tg - lag], perms[:, tg]], axis=1)
            p = float(((nul >= obs - 1e-12).sum() + 1) / (len(nul) + 1))
            rows.append((lag, half, int(len(v)), obs, float(np.percentile(bm, 2.5)), float(np.percentile(bm, 97.5)), float(nul.mean()), float(np.percentile(nul, 2.5)), float(np.percentile(nul, 97.5)), p))
    return rows


# ───────────────────────────── T2: скользящее окно против накопленного ─────────────────────────────

class Pre:
    """Префиксные суммы (ведущий нуль): окно [a, bb) = s[:, bb] − s[:, a]; для a = 0 значения совпадают с `tk692_walk.window_scores` бит в бит."""

    def __init__(self, pool):
        C, nd = pool.X.shape
        self.s1 = np.zeros((C, nd + 1))
        self.s2 = np.zeros((C, nd + 1))
        self.sn = np.zeros((C, nd + 1), np.int64)
        np.cumsum(pool.X, axis=1, out=self.s1[:, 1:])
        np.cumsum(pool.X * pool.X, axis=1, out=self.s2[:, 1:])
        np.cumsum(pool.N, axis=1, out=self.sn[:, 1:])


def scores_ab(pre, side, a, bb):
    w = bb - a
    tot = pre.s1[:, bb] - pre.s1[:, a]
    sq = pre.s2[:, bb] - pre.s2[:, a]
    nw = pre.sn[:, bb] - pre.sn[:, a]
    if w < 2:
        return np.full(len(tot), -np.inf), tot, nw, np.zeros(len(tot), bool)
    mean = tot / w
    var = np.maximum((sq - w * mean * mean) / (w - 1), 0.0)
    sd = np.sqrt(var)
    ok = (sd > 1e-9) & (nw >= W.n_min(side, w))
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.where(ok, mean / sd * math.sqrt(W.YEAR_DAYS), -np.inf)
    return score, tot, nw, ok


def select_ab(pre, pool, side, a, bb, b, excl=None):
    """Выбор PS-1007 §3 по окну [a, bb): максимум балла; при равенстве — большая Σ $ окна, затем меньший cid. `excl` — клетки, исключённые из набора."""
    score, tot, nw, ok = scores_ab(pre, side, a, bb)
    elig = W.eligible(pool, b) & ok
    if excl is not None:
        elig &= ~excl
    cand = np.nonzero(elig)[0]
    if len(cand) == 0:
        return None
    mx = float(score[cand].max())
    top = cand[score[cand] >= mx]
    best = top[np.lexsort((pool.cids[top], -tot[top]))[0]]
    return dict(idx=int(best), cid=int(pool.cids[best]), w=bb - a, score=float(score[best]), sum_w=float(tot[best]), n_w=int(nw[best]), m=len(cand))


def walk_ab(pre, pool, side, L, excl=None, emb=EMB):
    """Недельные границы PS-1007 §4; L = None — накопленное окно [0, b − E), иначе скользящее [b − E − L, b − E); `full` = окно L сут. целиком в загруженных сутках."""
    recs = []
    for b, en, e in W.blocks("week", pool.nd):
        bb = b - emb
        a = 0 if L is None else bb - L
        rec = dict(b=b, e=e, pick=None, full=a >= 0, pnl=None, n=None)
        if a >= 0:
            pk = select_ab(pre, pool, side, a, bb, b, excl)
            rec["pick"] = pk
            if pk is not None and e > b:
                p, n, _ = pool.step_pnl([pk["idx"]], b, e)
                rec["pnl"], rec["n"] = p[0], n[0]
        recs.append(rec)
    return recs


def day_series(recs, nd):
    """Склеенный суточный ряд: P&L, сделки, cid выбора и маска суток блоков с полным окном (P&L = 0, если выбора нет)."""
    pnl, n = np.zeros(nd), np.zeros(nd, np.int64)
    cid = np.full(nd, -1, np.int64)
    inb = np.zeros(nd, bool)
    for r in recs:
        if r["full"] and r["e"] > r["b"]:
            inb[r["b"]:r["e"]] = True
        if r["pick"] is not None and r["pnl"] is not None:
            pnl[r["b"]:r["e"]], n[r["b"]:r["e"]], cid[r["b"]:r["e"]] = r["pnl"], r["n"], r["pick"]["cid"]
    return pnl, n, cid, inb


def main_line_mask(pool, pnl, cid, mask):
    """«Главная линия» — выбор с наибольшей Σ $ на сутках mask; маска всех клеток набора с тем же суточным рядом (равные ряды — одна линия, п. 5 PS-1007)."""
    sel = mask & (cid >= 0)
    if not sel.any():
        return -1, np.zeros(len(pool.cids), bool)
    tot = {}
    for c, p in zip(cid[sel], pnl[sel]):
        tot[int(c)] = tot.get(int(c), 0.0) + float(p)
    best = max(tot, key=lambda c: (tot[c], -c))
    i = int(np.nonzero(pool.cids == best)[0][0])
    return best, (pool.X == pool.X[i]).all(axis=1) & (pool.N == pool.N[i]).all(axis=1)


def stats_row(st):
    return (st["days"], st["n"], st["sum"], st["per_trade"], st["sharpe"], st["dd"], st["ddp"], st["gap_longest"], st["gap_mean"], st["gap_tail"], st["top_day_share"])


def gate_expanding(recs, tk692_path, side, nd):
    """Ворота: накопленное окно этого модуля = ряд вне выборки L-436 (`tk692_oos`, A-m06, неделя, E = 1): те же сутки (11…nd − 1, 170 при 181), cid и P&L по суткам; пустая ссылка или другое число суток — стоп (SystemExit). Возвращает (число суток, расхождений)."""
    con = sqlite3.connect("file:%s?mode=ro" % tk692_path, uri=True)
    try:
        ref = con.execute("SELECT day, cid, pnl FROM tk692_oos WHERE rid='A-m06' AND side=? AND step='week' AND emb=1 ORDER BY day", (side,)).fetchall()
    finally:
        con.close()
    expect = [d for b, en, e in W.blocks("week", nd) for d in range(b, e)]
    if not ref or [d for d, _, _ in ref] != expect:
        raise SystemExit("ворота: в L-436 (tk692_oos, A-m06, %s, неделя, E = 1) %d суток, у накопленного ряда %d (сутки %d…%d) — стоп" % (side, len(ref), len(expect), expect[0], expect[-1]))
    pnl, _, cid, _ = day_series(recs, nd)
    bad = [(d, c, p, int(cid[d]), float(pnl[d])) for d, c, p in ref if c != int(cid[d]) or abs(p - pnl[d]) > 1e-9]
    return len(ref), bad


def t2_side(pool, side, tk692=None, extra=None, nboot=NBOOT):
    """Все испытания T2 одной стороны: L ∈ LS против накопленного, по половинам; утечка «без главной линии»; опорные линии (якорь, замороженные L384 / L401) на тех же сутках."""
    pre = Pre(pool)
    nd = pool.nd
    exp_recs = walk_ab(pre, pool, side, None)
    exp_pnl, exp_n, exp_cid, _ = day_series(exp_recs, nd)
    gate = gate_expanding(exp_recs, tk692, side, nd) if tk692 else None
    refs = {}
    an = W.anchor_series(pool, side, extra or "")
    if an is not None:
        refs["anchor"] = (an[0], an[1], an[2], an[3])
    for label, ck, n0, usd0 in W.BASES[side][1:]:
        idx, how, _ = pool.resolve_base(ck, n0, usd0)
        if idx is not None:
            bs = W.base_series(pool, idx, np.arange(nd))
            refs[label] = (bs[0], bs[1], int(pool.cids[idx]), how)
    out = dict(gate=gate, picks=[], sums=[], trials=[], refs={k: v[2:] for k, v in refs.items()})
    for r in exp_recs:
        pk = r["pick"]
        out["picks"].append(("expand", r["b"], r["e"], int(r["full"]), pk["cid"] if pk else None, pk["w"] if pk else None, pk["score"] if pk else None, pk["sum_w"] if pk else None,
                             pk["n_w"] if pk else None, pk["m"] if pk else None))
    for li, L in enumerate(LS):
        recs = walk_ab(pre, pool, side, L)
        pnl, n, cid, inb = day_series(recs, nd)
        for r in recs:
            pk = r["pick"]
            out["picks"].append(("L%d" % L, r["b"], r["e"], int(r["full"]), pk["cid"] if pk else None, pk["w"] if pk else None, pk["score"] if pk else None, pk["sum_w"] if pk else None,
                                 pk["n_w"] if pk else None, pk["m"] if pk else None))
        d = pnl - exp_pnl
        trial = dict(L=L, half={})
        lb_all = {}
        for hi, half in enumerate(HALVES):
            mk = inb & half_days(nd, half)
            if mk.sum() < 2:
                continue
            for ser, (pp, nn_) in (("slide", (pnl, n)), ("expand", (exp_pnl, exp_n))):
                out["sums"].append((L, half, ser, stats_row(W.series_stats(pp[mk], nn_[mk])), {}))
            for lab, (rp, rn, rc, how) in refs.items():
                out["sums"].append((L, half, lab, stats_row(W.series_stats(rp[mk], rn[mk])), dict(cid=rc, how=how)))
            if half == "all":
                continue
            bs = W.boot_sums(d[mk], nboot, np.random.default_rng([SEED, 3, SIDES.index(side), li, hi]))
            p = float(((bs <= 0).sum() + 1) / (len(bs) + 1))
            lb_all[half] = {str(k): float(np.percentile(bs, 100 * 0.05 / k)) for k in HOLM_K}
            trial["half"][half] = dict(p=p, d=float(d[mk].sum()), days=int(mk.sum()))
        if len(trial["half"]) < 2:
            out["trials"].append(dict(L=L, note="нет суток в обеих половинах", half=trial["half"]))
            continue
        mk_all = inb & half_days(nd, "all")
        ms, mask_s = main_line_mask(pool, pnl, cid, mk_all)
        me, mask_e = main_line_mask(pool, exp_pnl, exp_cid, mk_all)

        def rerun(excl):
            """Σ разницы по половинам: оба ряда заново проходят отбор на наборе без клеток excl."""
            ps2, _, _, _ = day_series(walk_ab(pre, pool, side, L, excl), nd)
            pe2, _, _, _ = day_series(walk_ab(pre, pool, side, None, excl), nd)
            return {h: float(((ps2 - pe2)[inb & half_days(nd, h)]).sum()) for h in ("H1", "H2")}

        excl_both = mask_s | mask_e
        lo_s, lo = rerun(mask_s), rerun(excl_both)                  # обязательный вариант — без главной линии скользящего ряда; второй — без главных линий обоих рядов
        out["trials"].append(dict(L=L, half=trial["half"], lb=lb_all, lo=lo, lo_s=lo_s, main_slide=ms, main_expand=me, excluded_cells=int(excl_both.sum()), excluded_cells_slide=int(mask_s.sum()),
                                  p_trial=max(trial["half"]["H1"]["p"], trial["half"]["H2"]["p"])))
    return out


def holm_family(trials):
    """Holm по p_trial (max p половин) 8 испытаний (сторона × L). trials: список (side, L, p_trial) → {(side, L): (rank, alpha_j, holm)}."""
    ps = [t[2] for t in trials]
    hm = W.holm(ps)
    order = np.argsort(ps, kind="stable")
    rank = {int(i): j for j, i in enumerate(order, start=1)}
    return {(t[0], t[1]): (rank[i], hm[i][1], hm[i][0]) for i, t in enumerate(trials)}


# ───────────────────────────── T3: фильтр по кривой капитала ─────────────────────────────

def equity_filter(x, N, start=FEB1, emb=EMB):
    """Сутки t ≥ start торгуются, если Σ P&L линии за сутки [t − E − N, t − E) строго > 0; возвращает маску торговли (по сутками start…nd − 1)."""
    cs = np.concatenate(([0.0], np.cumsum(x)))
    t = np.arange(start, len(x))
    hi, lo = t - emb, t - emb - N
    if (lo < 0).any():
        raise ValueError("окно фильтра выходит за начало ряда")
    return (cs[hi] - cs[lo]) > 0


def t3_side(pool, side, extra=None):
    nd = pool.nd
    lines = {}
    an = W.anchor_series(pool, side, extra or "")
    if an is not None:
        lines["anchor"] = (an[0], an[1], an[2], an[3])
    for label, ck, n0, usd0 in W.BASES[side][1:]:
        idx, how, _ = pool.resolve_base(ck, n0, usd0)
        if idx is not None:
            bs = W.base_series(pool, idx, np.arange(nd))
            lines[label] = (bs[0], bs[1], int(pool.cids[idx]), how)
    rows = []
    for lab, (x, n, cid, how) in lines.items():
        for N in NS:
            on = equity_filter(x, N)
            fp, fn = np.zeros(nd), np.zeros(nd, np.int64)
            fp[FEB1:], fn[FEB1:] = x[FEB1:] * on, n[FEB1:] * on
            traded = np.zeros(nd, bool)
            traded[FEB1:] = on
            for half in HALVES:
                mk = half_days(nd, half)
                for ser, (pp, nn_, tr) in (("filter", (fp, fn, traded)), ("raw", (x, n, np.ones(nd, bool)))):
                    rows.append((lab, N, half, ser, int((tr & mk).sum()), stats_row(W.series_stats(pp[mk], nn_[mk])), dict(cid=cid, how=how)))
    return rows


# ───────────────────────────── вердикт по правилу критика ─────────────────────────────

def rule_a(t1_rows):
    """(а) шорт: на лаге 1 ρ > 0 и нижняя граница бутстрепа > 0 в обеих половинах."""
    r = {h: (rho, lo) for lag, h, n, rho, lo, *_ in t1_rows if lag == 1 and h in ("H1", "H2")}
    ok = all(h in r and r[h][0] == r[h][0] and r[h][0] > 0 and r[h][1] > 0 for h in ("H1", "H2"))
    return ok, r


# ───────────────────────────── ввод-вывод ─────────────────────────────

def save_pool(pool, path):
    os.makedirs(path, exist_ok=True)
    np.save(os.path.join(path, "X.npy"), pool.X)
    np.save(os.path.join(path, "N.npy"), pool.N)
    np.save(os.path.join(path, "P.npy"), pool.P)
    np.save(os.path.join(path, "cids.npy"), pool.cids)
    with open(os.path.join(path, "meta.pkl"), "wb") as f:
        pickle.dump((list(pool.ckeys), pool.meta), f, protocol=4)


def load_packed(path):
    with open(os.path.join(path, "meta.pkl"), "rb") as f:
        ckeys, meta = pickle.load(f)
    return W.Pool(np.load(os.path.join(path, "cids.npy")), np.load(os.path.join(path, "X.npy")), np.load(os.path.join(path, "N.npy")), np.load(os.path.join(path, "P.npy")), ckeys, meta)


def open_out(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    out = sqlite3.connect(path)
    out.executescript(TABLES)
    return out


def run_row(out, rid, side, stage, params):
    out.execute("INSERT OR REPLACE INTO tk717_run VALUES(?,?,?,?,?,?)", (rid, side, stage, json.dumps(params, ensure_ascii=False), W.git_head(),
                                                                       datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))


def cmd_pack(a):
    con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    pool = W.load_pool(con, [r for r in a.runs.split(",") if r], a.cellset, a.side)
    if pool.nd != W.TOTAL:
        raise SystemExit("ожидалось %d суток, загружено %d" % (W.TOTAL, pool.nd))
    save_pool(pool, a.out)
    with open(os.path.join(a.out, "info.json"), "w", encoding="utf-8") as f:
        json.dump(dict(side=a.side, runs=a.runs, cellset=a.cellset, cells=len(pool.cids), nd=pool.nd, created=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")), f, ensure_ascii=False)
    print("pack %s: %d клеток, %d суток → %s" % (a.side, len(pool.cids), pool.nd, a.out))
    return 0


def cmd_t1(a):
    pool = load_packed(a.pool)
    out = open_out(a.out)
    M, NU = spearman_matrix(pool, a.side)
    rows = t1_stats(M, nboot=a.nboot, nperm=a.nperm)
    for t in ("tk717_t1_m", "tk717_t1"):
        out.execute("DELETE FROM %s WHERE rid=? AND side=?" % t, (a.rid, a.side))
    out.executemany("INSERT INTO tk717_t1_m VALUES(?,?,?,?,?,?)", [(a.rid, a.side, s, t, None if math.isnan(M[s, t]) else float(M[s, t]), int(NU[s])) for s in range(NWEEK) for t in range(NWEEK) if s != t])
    out.executemany("INSERT INTO tk717_t1 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [(a.rid, a.side) + r for r in rows])
    run_row(out, a.rid, a.side, "t1", dict(nboot=a.nboot, nperm=a.nperm, cells=len(pool.cids), universe_min=int(NU.min()), universe_max=int(NU.max())))
    out.commit()
    for lag, half, n, rho, lo, hi, nm, nlo, nhi, p in rows:
        print("%-5s лаг %d %-3s пар %2d  ρ %+.3f [%+.3f; %+.3f]  нуль %+.3f [%+.3f; %+.3f]  p_перест %.4f" % (a.side, lag, half, n, rho, lo, hi, nm, nlo, nhi, p))
    return 0


def cmd_t2(a):
    pool = load_packed(a.pool)
    out = open_out(a.out)
    res = t2_side(pool, a.side, a.tk692 or None, a.extra or None, a.nboot)
    if res["gate"] is not None:
        n_ref, bad = res["gate"]
        print("ворота накопленного окна против L-436: суток %d, расхождений %d" % (n_ref, len(bad)))
        if bad:
            raise SystemExit("ворота не пройдены: %s" % bad[:5])
    for t in ("tk717_t2_pick", "tk717_t2_sum", "tk717_t2_trial", "tk717_t2_holm"):
        out.execute("DELETE FROM %s WHERE rid=? AND side=?" % t, (a.rid, a.side))
    out.executemany("INSERT INTO tk717_t2_pick VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [(a.rid, a.side) + p for p in res["picks"]])
    for L, half, ser, st, ex in res["sums"]:
        out.execute("INSERT OR REPLACE INTO tk717_t2_sum VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (a.rid, a.side, L, half, ser) + tuple(st) + (json.dumps(ex, ensure_ascii=False),))
    for t in res["trials"]:
        if "lb" not in t:
            out.execute("INSERT OR REPLACE INTO tk717_t2_trial VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (a.rid, a.side, t["L"], None, None, None, None, None, None, None, None, None, None, None, None, json.dumps(dict(note=t["note"]))))
            continue
        h = t["half"]
        out.execute("INSERT OR REPLACE INTO tk717_t2_trial VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (a.rid, a.side, t["L"], h["H1"]["p"], h["H2"]["p"], t["p_trial"], h["H1"]["d"], h["H2"]["d"], json.dumps(t["lb"]),
                                                                                                t["lo_s"]["H1"], t["lo_s"]["H2"], t["lo"]["H1"], t["lo"]["H2"], t["main_slide"], t["main_expand"], json.dumps(dict(excluded_cells=t["excluded_cells"], excluded_cells_slide=t["excluded_cells_slide"], days=[h["H1"]["days"], h["H2"]["days"]]))))
    run_row(out, a.rid, a.side, "t2", dict(nboot=a.nboot, cells=len(pool.cids), gate=None if res["gate"] is None else dict(days=res["gate"][0], bad=len(res["gate"][1])), refs=res["refs"]))
    out.commit()
    print_t2(out, a.rid, a.side)
    return 0


def print_t2(out, rid, side):
    for L, h1, h2, p, d1, d2, s1, s2, l1, l2, ms, me in out.execute("SELECT L, p_h1, p_h2, p_trial, d_h1, d_h2, lo_s_h1, lo_s_h2, lo_d_h1, lo_d_h2, main_slide, main_expand FROM tk717_t2_trial WHERE rid=? AND side=? ORDER BY L", (rid, side)):
        if p is None:
            print("%-5s L=%d: нет суток в обеих половинах" % (side, L))
            continue
        print("%-5s L=%2d  Σ разницы H1 %+9.2f $ (p %.4f)  H2 %+9.2f $ (p %.4f)  p_исп %.4f  без главной линии скольз. (%s): H1 %+.2f H2 %+.2f; без обеих (+ накопл. %s): H1 %+.2f H2 %+.2f" % (side, L, d1, h1, d2, h2, p, ms, s1, s2, me, l1, l2))


def cmd_t3(a):
    pool = load_packed(a.pool)
    out = open_out(a.out)
    rows = t3_side(pool, a.side, a.extra or None)
    out.execute("DELETE FROM tk717_t3 WHERE rid=? AND side=?", (a.rid, a.side))
    for lab, N, half, ser, tr, st, ex in rows:
        out.execute("INSERT OR REPLACE INTO tk717_t3 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (a.rid, a.side, lab, N, half, ser, st[0], tr) + tuple(st[1:10]) + (json.dumps(ex, ensure_ascii=False),))
    run_row(out, a.rid, a.side, "t3", dict(lines=sorted({r[0] for r in rows}), NS=list(NS)))
    out.commit()
    for lab, N, half, ser, tr, st, ex in rows:
        if half == "all":
            print("%-5s %-12s N=%2d %-6s суток %3d торг. %3d  Σ %+9.2f $  Шарп %5.2f  DD %7.2f $ (%5.1f %%)" % (a.side, lab, N, ser, st[0], tr, st[2], st[4], st[5], st[6]))
    return 0


def cmd_verdict(a):
    """Holm по 8 испытаниям T2 (сторона × L) и правило критика (а) + (б) для шорта; лонг — справочно (правила для него критик не задал)."""
    out = open_out(a.out)
    tr = out.execute("SELECT side, L, p_trial FROM tk717_t2_trial WHERE rid=? AND p_trial IS NOT NULL", (a.rid,)).fetchall()
    if len(tr) != len(SIDES) * len(LS):
        raise SystemExit("нужны все 8 испытаний T2 (обе стороны), есть %d" % len(tr))
    hm = holm_family([(s, L, p) for s, L, p in tr])
    out.execute("DELETE FROM tk717_t2_holm WHERE rid=?", (a.rid,))
    out.execute("DELETE FROM tk717_verdict WHERE rid=?", (a.rid,))
    pass_b = {}
    for side, L, p in tr:
        rank, aj, ok = hm[(side, L)]
        lb, d1, d2, l1, l2, s1, s2 = out.execute("SELECT lb, d_h1, d_h2, lo_d_h1, lo_d_h2, lo_s_h1, lo_s_h2 FROM tk717_t2_trial WHERE rid=? AND side=? AND L=?", (a.rid, side, L)).fetchone()
        lb = json.loads(lb)
        k = str(int(round(0.05 / aj)))
        lb1, lb2 = lb["H1"][k], lb["H2"][k]
        pb = int(ok and d1 > 0 and d2 > 0 and lb1 > 0 and lb2 > 0 and s1 > 0 and s2 > 0 and l1 > 0 and l2 > 0)
        pass_b[(side, L)] = pb
        out.execute("INSERT INTO tk717_t2_holm VALUES(?,?,?,?,?,?,?,?,?,?)", (a.rid, side, L, p, rank, aj, int(ok), lb1, lb2, pb))
        print("%-5s L=%2d  p_исп %.4f  ранг %d  α_j %.4f  Holm %d  нижняя граница разницы (α_j): H1 %+.2f H2 %+.2f  без главной линии скольз.: H1 %+.2f H2 %+.2f  без обеих: H1 %+.2f H2 %+.2f  → (б) %d" % (side, L, p, rank, aj, int(ok), lb1, lb2, s1, s2, l1, l2, pb))
    for side in SIDES:
        t1 = [r for r in out.execute("SELECT lag, half, n_pairs, rho, lo, hi, null_mean, p_perm FROM tk717_t1 WHERE rid=? AND side=?", (a.rid, side))]
        ok_a, ra = rule_a(t1) if t1 else (False, {})
        ok_b = any(pass_b.get((side, L), 0) for L in RULE_L)
        nul = {h: (nm, pp) for lag, h, n, rho, lo, hi, nm, pp in t1 if lag == 1}
        note_a = "; ".join("%s ρ=%+.3f нижняя=%+.3f нуль перестановок=%+.3f p_перест=%.4f" % ((h, ra[h][0], ra[h][1]) + nul[h]) for h in ("H1", "H2") if h in ra) if ra else "T1 не посчитан"
        out.execute("INSERT INTO tk717_verdict VALUES(?,?,?,?,?)", (a.rid, side, "a", int(ok_a), note_a))
        out.execute("INSERT INTO tk717_verdict VALUES(?,?,?,?,?)", (a.rid, side, "b", int(ok_b), ",".join("L%d=%d" % (L, pass_b.get((side, L), 0)) for L in RULE_L)))
        out.execute("INSERT INTO tk717_verdict VALUES(?,?,?,?,?)", (a.rid, side, "stage_b", int(ok_a and ok_b), "по правилу критика (а)+(б); основная строка — шорт" if side == "short" else "справочно: правила для лонга критик не задал"))
        print("%-5s (а) %d [%s]   (б) %d   → этап B %s" % (side, ok_a, note_a, ok_b, "ДА" if ok_a and ok_b else "НЕТ"))
    out.commit()
    return 0


def fmt_st(st):
    days, n, tot, pt, sh, dd, ddp, gl, gm, gt, top = (float("nan") if v is None else v for v in st)       # NaN хранится в SQLite как NULL
    return "%d | %d | %+.2f | %.2f | %.2f (%.1f %%) | %d / %.1f / %d" % (days, n, tot, sh, dd, ddp, gl, gm, gt)


def cmd_report(a):
    """Таблицы T1 / T2 / T3 из БД результатов (ставка 500 $, маржа 250 $; пул отобран на 01.07–13.11 — верхняя граница)."""
    out = sqlite3.connect("file:%s?mode=ro" % a.out, uri=True)
    print("Пул A отобран на июле–ноябре (Σ > 1 $, В-183) — все числа «верхняя граница»; ставка 500 $, маржа 250 $.\n")
    print("### T1: ρ Спирмена Σ клеток недели a → недели a + лаг\n| сторона | лаг | половина | пар | ρ | граница 2,5 / 97,5 % | нуль (перестановка недель) | p_перест |\n|---|---|---|---|---|---|---|---|")
    for side, lag, half, n, *v in out.execute("SELECT side, lag, half, n_pairs, rho, lo, hi, null_mean, null_lo, null_hi, p_perm FROM tk717_t1 WHERE rid=? ORDER BY side DESC, lag, half", (a.rid,)):
        rho, lo, hi, nm, nlo, nhi, p = (float("nan") if x is None else x for x in v)
        print("| %s | %d | %s | %d | %+.3f | %+.3f / %+.3f | %+.3f [%+.3f; %+.3f] | %.4f |" % (side, lag, half, n, rho, lo, hi, nm, nlo, nhi, p))
    print("\n### T2: скользящее окно L против накопленного (шаг неделя, E = 1)\n| сторона | L | половина | ряд | суток | сд. | Σ $ | Шарп | просадка $ (% маржи) | перехай (долгий / средний / хвост) |\n|---|---|---|---|---|---|---|---|---|---|")
    for side, L, half, ser, *st in out.execute("SELECT side, L, half, series, days, n, sum, per_trade, sharpe, dd, ddp, gap_longest, gap_mean, gap_tail, top_day_share FROM tk717_t2_sum WHERE rid=? ORDER BY side DESC, L, half, series='slide' DESC, series", (a.rid,)):
        print("| %s | %d | %s | %s | %s |" % (side, L, half, ser, fmt_st(st)))
    print("\n### T2: Holm по 8 испытаниям и условие «без главной линии»\n| сторона | L | p испытания | ранг | α_j | Holm | нижняя граница H1 / H2 | без главной линии скольз. H1 / H2 | без обеих линий H1 / H2 | (б) |\n|---|---|---|---|---|---|---|---|---|---|")
    for side, L, p, rank, aj, ok, l1, l2, pb, k1, k2, s1, s2 in out.execute("SELECT h.side, h.L, h.p_trial, h.rank, h.alpha_j, h.holm, h.lb_h1, h.lb_h2, h.pass_b, t.lo_d_h1, t.lo_d_h2, t.lo_s_h1, t.lo_s_h2 FROM tk717_t2_holm h JOIN tk717_t2_trial t USING(rid, side, L) WHERE rid=? ORDER BY h.side DESC, h.L", (a.rid,)):
        print("| %s | %d | %.4f | %d | %.4f | %d | %+.2f / %+.2f | %+.2f / %+.2f | %+.2f / %+.2f | %d |" % (side, L, p, rank, aj, ok, l1, l2, s1, s2, k1, k2, pb))
    print("\n### T3 (справочно): линия торгуется, только если её Σ за последние N сут. (до вчерашнего) > 0\n| линия | N | половина | ряд | торг. суток | суток | сд. | Σ $ | Шарп | просадка $ (% маржи) | перехай (долгий / средний / хвост) |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for line, N, half, ser, tr, *st in out.execute("SELECT line, nwin, half, series, traded_days, days, n, sum, per_trade, sharpe, dd, ddp, gap_longest, gap_mean, gap_tail, 0 FROM tk717_t3 WHERE rid=? ORDER BY line, nwin, half, series DESC", (a.rid,)):
        print("| %s | %d | %s | %s | %d | %s |" % (line, N, half, ser, tr, fmt_st(st)))
    print("\n### Вердикт по правилу критика")
    for side, part, ok, note in out.execute("SELECT side, part, ok, note FROM tk717_verdict WHERE rid=? ORDER BY side DESC, part", (a.rid,)):
        print("- %s %s: %d (%s)" % (side, part, ok, note))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--db", required=True)
    p.add_argument("--side", required=True, choices=SIDES)
    p.add_argument("--runs", required=True)
    p.add_argument("--cellset", default="all1")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_pack)
    for name, fn in (("t1", cmd_t1), ("t2", cmd_t2), ("t3", cmd_t3)):
        q = sub.add_parser(name)
        q.add_argument("--pool", required=True)
        q.add_argument("--side", required=True, choices=SIDES)
        q.add_argument("--out", required=True)
        q.add_argument("--rid", default="SA")
        q.add_argument("--nboot", type=int, default=NBOOT)
        if name == "t1":
            q.add_argument("--nperm", type=int, default=NPERM)
        else:
            q.add_argument("--extra", default="", help="replay.sqlite повтора движком (якорь вне набора)")
        if name == "t2":
            q.add_argument("--tk692", required=True, help="tk692.sqlite: ворота накопленного окна против L-436 (обязательны)")
        q.set_defaults(fn=fn)
    for name, fn in (("verdict", cmd_verdict), ("report", cmd_report)):
        q = sub.add_parser(name)
        q.add_argument("--out", required=True)
        q.add_argument("--rid", default="SA")
        q.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
