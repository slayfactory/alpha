#!/usr/bin/env python3
"""TK-718 (PS-1037 + PS-1038): T1 (база B, B^σ, C1), T2 (поверхность соседей, C2), C3(a), T5 (концентрация) по рядам `tk718_ser` — numpy, без движка.

  python3 tk718_tests.py run    [--db /data/p42-tk718/tk718.sqlite]    # T1 / T2 / C3a / T5 по всем сторонам → таблицы `tk718_row`, `tk718_run`
  python3 tk718_tests.py verdict [--db …]                               # правило C1 ∧ C2 ∧ C3 по каждой линии стороны (C3b берётся из строк T3 `tk718_pool.py`) → `tk718_row` test=V

Окна (сутки от 01.01.2026): Jan 0–30, A 31–119, M 120–180, FJ 31–180, SEL 181–316, N1 317–327. Шарп, просадка, перехай — `tk692_walk.series_stats` (как `tk684_metrics`). Зерно 718, бутстреп блоками ⌈n^{1/3}⌉ (интервалы Σ), для ρ_w — блок 3 недели.
Строка результата — `tk718_row(test, side, series, win, stat, value, txt)`: одна запись на число (В-179: результаты в БД, не в md)."""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, "/data/p42-tk692/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import tk692_walk as W  # noqa: E402

SEED = 718
NBOOT = 10000
ROOT = "/data/p42-tk718"
DB = ROOT + "/tk718.sqlite"
TK692 = "/data/p42-tk692/tk692.sqlite"
REPLAY = "/data/p42-tk692/replay.sqlite"
TRADES = ROOT + "/trades"
DAY_MS = 86_400_000
D0_MS = int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
ND = 328                                                          # сутки 0…327 (шорт); лонг — 0…316, N1 пуст
WINS = {"Jan": (0, 31), "A": (31, 120), "M": (120, 181), "FJ": (31, 181), "SEL": (181, 317), "N1": (317, 328)}
MONTH_NAMES = ("m01", "m02", "m03", "m04", "m05", "m06")
MSTART = W.MONTH_START
FJ_WINS = MONTH_NAMES[1:]
SEL_WINS = ("wa", "wb", "wc")
SIDES = ("short", "long")
LINES = {"short": ("S1", "S2"), "long": ("L1", "L2")}
REFS = {"short": ("L401",), "long": ()}                           # справочно (в правило не входят); L384 — из пула, см. `ref_series`
LETTER = {"short": "S", "long": "L"}
WEEK0, NWEEKS, WBLOCK = 32, 21, 3                                 # недели Пн–Вс: сутки 32 + 7k, k = 0…20; блок бутстрепа 3 недели
LAMBDA, JAN_DAYS, C_LO, C_HI = 0.97, 30, 0.25, 4.0                # B^σ
F_M_MIN, FJ_PLATEAU, SEL_PLATEAU, PEAK, HOLD = 0.5, 2 / 3, 0.5, 1 / 3, 0.5
TOP_DAYS, TOP_TRADES = (1, 3, 5, 10), (1, 5, 10, 20)

TABLES = """
CREATE TABLE IF NOT EXISTS tk718_row(test TEXT, side TEXT, series TEXT, win TEXT, stat TEXT, value REAL, txt TEXT, PRIMARY KEY(test, side, series, win, stat));
CREATE TABLE IF NOT EXISTS tk718_run(stage TEXT PRIMARY KEY, params TEXT, code TEXT, created TEXT);
"""


# ───────────────────────────── хранилище рядов ─────────────────────────────

class Store:
    """Ряды клеток из `tk718_ser` (H1 сутки 0–180, SEL 181–316, N1 317–327) → массивы (ND,) с NaN там, где данных нет; клетка / группа без полного `have` — «нет данных»."""

    def __init__(self, path):
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        self.plan = {}
        self.by_name = {}
        for side, ckey, cid, role, name, line_of in con.execute("SELECT side, ckey, cid, role, name, line_of FROM tk718_plan"):
            self.plan[side, ckey] = dict(cid=cid, role=role, name=name, line_of=line_of)
            self.by_name[side, name] = ckey
        self.raw = {}
        for side, ckey, grp, have, src, pnl, n in con.execute("SELECT side, ckey, grp, have, src, pnl, n FROM tk718_ser"):
            self.raw[side, ckey, grp] = (set(json.loads(have)), src, np.frombuffer(pnl, np.float64), np.frombuffer(n, np.int32))
        con.close()

    def ckey(self, side, name):
        return self.by_name.get((side, name))

    def series(self, side, name):
        """(pnl[ND], n[ND]) или None, если ни одной группы нет."""
        ck = self.by_name.get((side, name))
        return None if ck is None else self.series_ck(side, ck)

    def series_ck(self, side, ckey):
        role = self.plan[side, ckey]["role"]
        pnl, n = np.full(ND, np.nan), np.zeros(ND, np.int64)
        got = False
        for grp, (d0, d1), need in (("H1", (0, 181), set(MONTH_NAMES) if role in ("line", "member") else set(FJ_WINS)), ("SEL", (181, 317), set(SEL_WINS)), ("N1", (317, 328), {"n1"})):
            r = self.raw.get((side, ckey, grp))
            if r is None or not need <= r[0]:
                continue
            lo = d0 if role in ("line", "member") or grp != "H1" else 31
            pnl[lo:d1], n[lo:d1] = r[2][lo - d0:], r[3][lo - d0:]
            got = True
        return (pnl, n) if got else None

    def members(self, side):
        return sorted(nm for (s, nm), ck in self.by_name.items() if s == side and self.plan[s, ck]["role"] == "member")

    def neighbours(self, line):
        side = LINES_SIDE[line]
        return [(nm, ck) for (s, nm), ck in sorted(self.by_name.items()) if s == side and self.plan[s, ck]["role"] == "nb" and self.plan[s, ck]["line_of"] == line]


LINES_SIDE = {"S1": "short", "S2": "short", "L1": "long", "L2": "long", "L401": "short"}


# ───────────────────────────── метрики ─────────────────────────────

def win_ok(x, win):
    lo, hi = WINS[win]
    return len(x) >= hi and not np.isnan(x[lo:hi]).any()


def wstats(pnl, n, win):
    """Метрики окна (`series_stats`) или None, если в окне есть пропуски."""
    if pnl is None or not win_ok(pnl, win):
        return None
    lo, hi = WINS[win]
    return W.series_stats(pnl[lo:hi], n[lo:hi])


def wsum(x, win):
    lo, hi = WINS[win]
    return float(np.sum(x[lo:hi])) if win_ok(x, win) else float("nan")


def wsharpe(x, win):
    lo, hi = WINS[win]
    return W.sharpe_of(x[lo:hi]) if win_ok(x, win) else float("nan")


STAT_KEYS = ("days", "n", "sum", "per_trade", "sharpe", "dd", "ddp", "gap_longest", "gap_mean", "gap_tail")


def add_stats(rows, test, side, series, win, st, extra=None):
    for k in STAT_KEYS:
        rows.append((test, side, series, win, k, float(st[k]), None))
    for k, v in (extra or {}).items():
        rows.append((test, side, series, win, k, float(v), None))


def block_idx(n, L, B, rng):
    """Индексы бутстрепа скользящими блоками длины L (обрезка до n)."""
    nb = int(math.ceil(n / L))
    starts = rng.integers(0, max(1, n - L + 1), (B, nb))
    return (starts[:, :, None] + np.arange(L)[None, None, :]).reshape(B, nb * L)[:, :n]


def boot_ci(x, rng, B=NBOOT):
    """95 % интервал Σ суточного ряда: блоки ⌈n^{1/3}⌉."""
    s = W.boot_sums(x, B, rng)
    return float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))


def pearson(a, b):
    a, b = a - a.mean(), b - b.mean()
    da, db = math.sqrt(float((a * a).sum())), math.sqrt(float((b * b).sum()))
    return float((a * b).sum() / (da * db)) if da > 0 and db > 0 else float("nan")


def weekly(x):
    return x[WEEK0:WEEK0 + 7 * NWEEKS].reshape(NWEEKS, 7).sum(axis=1)


def rho_w(b, f, rng, B=NBOOT):
    """ρ_w = Пирсон недельных Σ (21 неделя Пн–Вс), бутстреп скользящими блоками 3 недели: (ρ, 2,5-й, 97,5-й процентиль)."""
    xb, xf = weekly(b), weekly(f)
    rho = pearson(xb, xf)
    idx = block_idx(NWEEKS, WBLOCK, B, rng)
    a, c = xb[idx], xf[idx]
    a, c = a - a.mean(axis=1, keepdims=True), c - c.mean(axis=1, keepdims=True)
    den = np.sqrt((a * a).sum(axis=1) * (c * c).sum(axis=1))
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(den > 0, (a * c).sum(axis=1) / den, np.nan)
    r = r[~np.isnan(r)]
    return rho, float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


def sigma_scale(b, lam=LAMBDA, jan=JAN_DAYS, lo=C_LO, hi=C_HI):
    """B^σ: c_t = clip(σ_ref / σ̂_{t−1}, lo, hi), σ̂² — EWMA квадрата B с началом σ_ref², σ_ref = σ ряда за сутки 0…jan − 1; c_t = 1 при t < jan. Возвращает (B^σ, c)."""
    b = np.asarray(b, float)
    sref = float(np.std(b[:jan], ddof=1))
    s2 = sref * sref
    c = np.ones(len(b))
    for t in range(len(b)):
        if t >= jan:
            c[t] = hi if s2 <= 0 else min(hi, max(lo, sref / math.sqrt(s2)))
        s2 = lam * s2 + (1.0 - lam) * b[t] * b[t]
    return b * c, c


def topk_share(x, k):
    """Доля Σ положительных элементов, приходящаяся на k наибольших; nan, если положительных нет."""
    x = np.asarray(x, float)
    pos = x[x > 0]
    if len(pos) == 0:
        return float("nan")
    return float(np.sort(pos)[::-1][:k].sum() / pos.sum())


def topk_of_total(x, k):
    """Доля Σ всех элементов (при Σ > 0), приходящаяся на k наибольших; nan при Σ ≤ 0."""
    x = np.asarray(x, float)
    s = float(x.sum())
    return float(np.sort(x)[::-1][:k].sum() / s) if s > 0 else float("nan")


# ───────────────────────────── ряды стороны (T1) ─────────────────────────────

def member_series(S, side):
    """Ряды 9 членов базы; None, если какого-то нет полностью (H1 m01…m06)."""
    out = []
    for nm in S.members(side):
        s = S.series(side, nm)
        if s is None or np.isnan(s[0][:181]).any():
            return None, nm
        out.append((nm, s))
    return (out if len(out) == 9 else None), len(out)


def base_series(mem):
    """B = среднее посуточных P&L 9 членов (ставка 500 $ на сделку члена); пропуск у любого члена — пропуск B; n — сумма сделок членов."""
    p = np.mean([s[0] for _, s in mem], axis=0)
    n = np.sum([s[1] for _, s in mem], axis=0)
    return p, n


def ref_series(side, pool_dir="/data/p42-tk717/pool-%s", tk692=TK692):
    """Справочные ряды стороны вне плана: L384 (шорт, из пула по ключу), строка TK-692 (шаг «месяц», E = 1, сутки 31–180). Возвращает {имя: (pnl[ND], n[ND])}."""
    import pickle
    out = {}
    if side == "short" and os.path.exists(os.path.join(pool_dir % side, "meta.pkl")):
        ckeys, _meta = pickle.load(open(os.path.join(pool_dir % side, "meta.pkl"), "rb"))
        ck = W.BASES["short"][1][1]
        if ck in ckeys:
            i = ckeys.index(ck)
            X, N = np.load(os.path.join(pool_dir % side, "X.npy"), mmap_mode="r"), np.load(os.path.join(pool_dir % side, "N.npy"), mmap_mode="r")
            p, n = np.full(ND, np.nan), np.zeros(ND, np.int64)
            p[:181], n[:181] = X[i], N[i]
            out["L384"] = (p, n)
    if os.path.exists(tk692):
        con = sqlite3.connect("file:%s?mode=ro" % tk692, uri=True)
        rows = con.execute("SELECT day, pnl, n FROM tk692_oos WHERE rid='A-m06' AND side=? AND step='month' AND emb=1 ORDER BY day", (side,)).fetchall()
        con.close()
        if rows:
            p, n = np.full(ND, np.nan), np.zeros(ND, np.int64)
            for d, v, k in rows:
                p[d], n[d] = v, k
            out["TK692"] = (p, n)
    return out


def t1_side(S, side, rows, refs=None):
    """Строки T1 стороны: профили B, B^σ, якоря, линий, справочных; ρ_w, Σ_A(F − B), Σ_M(F − B); значения правила C1 по B и по каждой линии. Возвращает словарь рядов (для T5)."""
    mem, info = member_series(S, side)
    ser = {}
    if mem is None:
        rows.append(("T1", side, "B", "FJ", "nodata", float("nan"), "члены: %s" % (info,)))
        return ser
    b, bn = base_series(mem)
    t_end = int(np.argmax(np.isnan(b))) if np.isnan(b).any() else len(b)           # B^σ — по непрерывному ряду от суток 0 (дальше пропуск окна — ряд обрывается)
    bs = np.full(len(b), np.nan)
    bs[:t_end], c = sigma_scale(b[:t_end])
    ser["B"], ser["Bsig"] = (b, bn), (bs, bn)
    for nm, s in mem:
        if nm.endswith("-l7-tf60"):
            ser["anchor"] = s
    for ln in LINES[side] + REFS[side]:
        s = S.series(side, ln)
        if s is not None:
            ser[ln] = s
    for nm, s in (refs or {}).items():
        ser[nm] = s
    for gi, (name, (p, n)) in enumerate(ser.items()):
        for win in WINS:
            st = wstats(p, n, win)
            if st is None:
                continue
            extra = {}
            if win == "FJ":
                sa, sm, sfj = wsum(p, "A"), wsum(p, "M"), wsum(p, "FJ")
                extra["sum_A"], extra["sum_M"] = sa, sm
                extra["sharpe_A"], extra["sharpe_M"] = wsharpe(p, "A"), wsharpe(p, "M")
                extra["f_M"] = sm / sfj if sfj > 0 else float("nan")
            if win in ("A", "M", "FJ"):
                lo, hi = WINS[win]
                ci = boot_ci(p[lo:hi], np.random.default_rng([SEED, 1, SIDES.index(side), gi, list(WINS).index(win)]))
                extra["sum_ci_lo"], extra["sum_ci_hi"] = ci
            if name in ("B", "Bsig"):                                # у ансамбля сделок на сутки — сумма сделок 9 членов; $ на сделку члена = 9 · Σ_B / n
                extra["per_trade"] = 9.0 * st["sum"] / st["n"] if st["n"] else float("nan")
            add_stats(rows, "T1", side, name, win, st, extra)
    rows.append(("T1", side, "Bsig", "ALL", "c_min", float(c[JAN_DAYS:].min()), None))
    rows.append(("T1", side, "Bsig", "ALL", "c_max", float(c[JAN_DAYS:].max()), None))
    rows.append(("T1", side, "Bsig", "ALL", "c_mean", float(c[JAN_DAYS:].mean()), None))
    for li, ln in enumerate(LINES[side] + REFS[side] + ("anchor", "Bsig")):
        if ln not in ser:
            continue
        f = ser[ln][0]
        if not win_ok(f, "FJ"):
            continue
        rho = rho_w(b, f, np.random.default_rng([SEED, 2, SIDES.index(side), li]))
        for k, v in zip(("rho_w", "rho_lo", "rho_hi"), rho):
            rows.append(("T1", side, ln, "FJ", k, v, None))
        d = f - b
        for win in ("A", "M", "FJ"):
            lo, hi = WINS[win]
            ci = boot_ci(d[lo:hi], np.random.default_rng([SEED, 3, SIDES.index(side), li, list(WINS).index(win)]))
            rows += [("T1", side, ln, win, "sum_F-B", wsum(d, win), None), ("T1", side, ln, win, "sum_F-B_ci_lo", ci[0], None), ("T1", side, ln, win, "sum_F-B_ci_hi", ci[1], None)]
    return ser


def c1_eval(rows, side):
    """C1 (на B): (i) Σ_M(B) > 0 и f_M ≥ 0,5; (ii) для линии нижняя граница ρ_w > 0; (iii) Σ_A(F − B) > 0. Пишет строки test=C1."""
    get = {(r[2], r[3], r[4]): r[5] for r in rows if r[0] == "T1" and r[1] == side}
    sm, fm = get.get(("B", "FJ", "sum_M"), float("nan")), get.get(("B", "FJ", "f_M"), float("nan"))
    ok_i = None if sm != sm else bool(sm > 0 and fm == fm and fm >= F_M_MIN)         # f_M не определена (Σ_FJ ≤ 0) — условие не выполнено
    rows.append(("C1", side, "B", "FJ", "i", float("nan") if ok_i is None else float(ok_i), "Σ_M(B) = %.2f, f_M = %.3f (nan: Σ_FJ ≤ 0)" % (sm, fm)))
    out = {"i": ok_i}
    for ln in LINES[side]:
        lo, da = get.get((ln, "FJ", "rho_lo"), float("nan")), get.get((ln, "A", "sum_F-B"), float("nan"))
        ok_ii = None if lo != lo else bool(lo > 0)
        ok_iii = None if da != da else bool(da > 0)
        rows.append(("C1", side, ln, "FJ", "ii", float("nan") if ok_ii is None else float(ok_ii), "нижняя граница ρ_w = %.3f" % lo))
        rows.append(("C1", side, ln, "FJ", "iii", float("nan") if ok_iii is None else float(ok_iii), "Σ_A(F − B) = %.2f, Σ_M(F − B) = %.2f" % (da, get.get((ln, "M", "sum_F-B"), float("nan")))))
        out[ln] = (ok_ii, ok_iii)
    return out


# ───────────────────────────── T2: поверхность ─────────────────────────────

def axis_of(label):
    """«a:kt24+1» → ('kt24', +1); не «a:» — (None, None)."""
    if not label.startswith("a:"):
        return None, None
    body = label[2:]
    return body[:-2], int(body[-2:])


def nb_holds(line_p, line_n, nb_p, nb_n, win):
    """Состояние соседа в окне: ('nodata' | 'twin' | 'held' | 'broke', Шарп соседа)."""
    if not win_ok(nb_p, win) or not win_ok(line_p, win):
        return "nodata", float("nan")
    lo, hi = WINS[win]
    if np.allclose(nb_p[lo:hi], line_p[lo:hi], atol=1e-9, rtol=0) and np.array_equal(nb_n[lo:hi], line_n[lo:hi]):
        return "twin", float("nan")
    sh_l, sh_n = W.sharpe_of(line_p[lo:hi]), W.sharpe_of(nb_p[lo:hi])
    return ("held" if (sh_l > 0 and sh_n >= HOLD * sh_l) else "broke"), sh_n


def t2_line(S, ln, rows, side):
    """T2 линии: для окон FJ и SEL — состояние каждого соседа, доли (а) / (б) / углов / общая, оси; правило C2. Возвращает {win: share}."""
    lp = S.series(side, ln)
    out = {}
    nbs = S.neighbours(ln)
    for win in ("FJ", "SEL"):
        if lp is None or not win_ok(lp[0], win):
            rows.append(("T2", side, ln, win, "nodata", float("nan"), "ряд линии в окне пуст"))
            out[win] = None
            continue
        sh_line = wsharpe(lp[0], win)
        cnt = {"a": [0, 0], "b": [0, 0], "c": [0, 0]}
        tw = nd = 0
        axes = {}
        for nm, ck in nbs:
            s = S.series_ck(side, ck)
            state, sh = ("nodata", float("nan")) if s is None else nb_holds(lp[0], lp[1], s[0], s[1], win)
            lab = nm.split("/", 1)[1]
            rows.append(("T2nb", side, nm, win, "state", {"held": 1.0, "broke": 0.0}.get(state, float("nan")), state))
            if s is not None and state != "nodata":
                st = wstats(s[0], s[1], win)
                add_stats(rows, "T2nb", side, nm, win, st)
            if state == "nodata":
                nd += 1
                continue
            if state == "twin":
                tw += 1
                continue
            kind = lab[0]
            cnt[kind][0] += state == "held"
            cnt[kind][1] += 1
            ax_, sg = axis_of(lab)
            if ax_:
                axes.setdefault(ax_, [0, 0])
                axes[ax_][0] += state == "held"
                axes[ax_][1] += 1
        held = sum(v[0] for v in cnt.values())
        den = sum(v[1] for v in cnt.values())
        share = held / den if den and sh_line > 0 else 0.0
        for k, v in cnt.items():
            rows.append(("T2", side, ln, win, "held_" + k, float(v[0]), None))
            rows.append(("T2", side, ln, win, "den_" + k, float(v[1]), None))
            rows.append(("T2", side, ln, win, "share_" + k, v[0] / v[1] if v[1] else float("nan"), None))
        rows += [("T2", side, ln, win, "held", float(held), None), ("T2", side, ln, win, "den", float(den), None), ("T2", side, ln, win, "twins", float(tw), None),
                 ("T2", side, ln, win, "nodata_nb", float(nd), None), ("T2", side, ln, win, "sharpe_line", sh_line, None), ("T2", side, ln, win, "share", share, "доля соседей, держащих (Шарп ≥ 0,5 × линии; Шарп линии > 0)")]
        for ax_, (h, d) in sorted(axes.items()):
            rows.append(("T2ax", side, ln, win, ax_, h / d if d else float("nan"), "%d из %d (±25 %%)" % (h, d)))
        out[win] = share
    fj, sel = out.get("FJ"), out.get("SEL")
    if fj is None or sel is None:
        rows.append(("C2", side, ln, "ALL", "c2", float("nan"), "нет данных: FJ=%s SEL=%s" % (fj, sel)))
        return out
    c2 = bool(fj >= FJ_PLATEAU and sel >= SEL_PLATEAU)
    kind = "plateau" if c2 else ("peak" if fj <= PEAK else "intermediate")
    rows.append(("C2", side, ln, "ALL", "c2", float(c2), "FJ %.3f (≥ 2/3), SEL %.3f (≥ 1/2) → %s" % (fj, sel, kind)))
    rows.append(("C2", side, ln, "ALL", "peak", float(fj <= PEAK), None))
    return out


# ───────────────────────────── T5: концентрация ─────────────────────────────

def read_trades_csv(path):
    """CSV сделок движка (`sym, sig_ms, entry_ms, exit_ms, gross, fees, funding, net, kind`) → dict массивов numpy (без pandas)."""
    import csv
    with open(path, encoding="utf-8", newline="") as f:
        rd = list(csv.DictReader(f))
    return dict(sym=np.array([r["sym"] for r in rd], dtype=object), sig_ms=np.array([int(r["sig_ms"]) for r in rd], np.int64), entry_ms=np.array([int(r["entry_ms"]) for r in rd], np.int64),
                exit_ms=np.array([int(r["exit_ms"]) for r in rd], np.int64), val=np.array([float(r["gross"]) - float(r["fees"]) for r in rd], float))


def cell_trades(cid, root=None):
    """Сделки клетки по месяцам m01…m06 из `--keep-trades`: dict массивов (sym, sig_ms, entry_ms, exit_ms [мс эпохи], day, val = gross − fees); сутки — по дате входа, как `mass_day`. Нет файлов — None."""
    parts = []
    root = TRADES if root is None else root
    if not os.path.isdir(root):
        return None
    for run in sorted(os.listdir(root)):
        if not run.startswith("tk718c-h1"):
            continue
        t0 = json.load(open(os.path.join(root, run, "t0.json"), encoding="utf-8"))
        for win, t0_ms in t0.items():
            p = os.path.join(root, run, win, "%d-trades.csv" % cid)
            if not os.path.exists(p) or win not in MONTH_NAMES:
                continue
            t = read_trades_csv(p)
            mi = MONTH_NAMES.index(win)
            day = (t0_ms + t["entry_ms"] - D0_MS) // DAY_MS
            keep = (day >= MSTART[mi]) & (day < MSTART[mi + 1])
            part = {k: v[keep] for k, v in t.items()}
            part["day"] = day[keep]
            for c in ("sig_ms", "entry_ms", "exit_ms"):
                part[c] = part[c] + t0_ms
            parts.append(part)
    if not parts:
        return None
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def trades_in(tr, d0, d1):
    m = (tr["day"] >= d0) & (tr["day"] < d1)
    return {k: v[m] for k, v in tr.items()}


def check_trades(tr, pnl, n):
    """Сделки против ряда клетки: Σ по суткам и число сделок равны → (максимум |разницы Σ|, число суток с другим n)."""
    s = np.bincount(tr["day"], weights=tr["val"], minlength=181)[:181]
    k = np.bincount(tr["day"], minlength=181)[:181]
    return float(np.max(np.abs(s - pnl[:181]))), int((k != n[:181]).sum())


def trade_concentration(tr_val, sym, rows, test, side, name, win="FJ"):
    """Топ-k сделок от Σ (всех) и от Σ положительных, доля максимальной монеты."""
    v = np.asarray(tr_val, float)
    for k in TOP_TRADES:
        rows.append((test, side, name, win, "top%d_of_sum" % k, topk_of_total(v, k), None))
        rows.append((test, side, name, win, "top%d_of_pos" % k, topk_share(v, k), None))
    tot = float(v.sum())
    coins = {}
    for s_, x in zip(sym, v):
        coins[s_] = coins.get(s_, 0.0) + float(x)
    if coins:
        top = max(coins, key=lambda c: coins[c])
        rows.append((test, side, name, win, "max_coin_share", coins[top] / tot if tot > 0 else float("nan"), "%s Σ %.2f из %.2f%s" % (top, coins[top], tot, " — > 50 %" if tot > 0 and coins[top] / tot > 0.5 else "")))
        rows.append((test, side, name, win, "sum_without_top_coin", tot - coins[top], top))
    rows.append((test, side, name, win, "n_trades", float(len(v)), None))
    rows.append((test, side, name, win, "sum", tot, None))


def tk692_trades(side, rid="A-m06", tk692=TK692, replay=REPLAY):
    """Сделки строки TK-692 (шаг «месяц», E = 1): для каждого шага выбранной клетки — её сделки `rp_trade` с датой входа в [b, e)."""
    if not (os.path.exists(tk692) and os.path.exists(replay)):
        return None
    a = sqlite3.connect("file:%s?mode=ro" % tk692, uri=True)
    picks = a.execute("SELECT b, e, cid FROM tk692_pick WHERE rid=? AND side=? AND step='month' AND emb=1 AND cid IS NOT NULL AND e>b", (rid, side)).fetchall()
    a.close()
    r = sqlite3.connect("file:%s?mode=ro" % replay, uri=True)
    sym, val = [], []
    for b, e, cid in picks:
        for s_, v in r.execute("SELECT sym, val FROM rp_trade WHERE side=? AND cid=? AND cellset NOT LIKE 'base-%' AND entry_day>=? AND entry_day<?", (side, cid, b, e)):
            sym.append(s_)
            val.append(v)
    r.close()
    return np.array(sym), np.array(val)


def t5_side(S, side, ser, rows):
    """T5: доли топ-k суток (по Σ положительных суток) на FJ у B, B^σ, линий и строки TK-692; топ-k сделок и доля максимальной монеты (B — члены с весом 1/9)."""
    for name, (p, _n) in ser.items():
        if not win_ok(p, "FJ"):
            continue
        lo, hi = WINS["FJ"]
        for k in TOP_DAYS:
            rows.append(("T5d", side, name, "FJ", "top%d_days" % k, topk_share(p[lo:hi], k), None))
    for ln in LINES[side]:
        if "B" in ser and ln in ser and win_ok(ser[ln][0], "FJ"):
            d = topk_share(ser[ln][0][31:181], 5) - topk_share(ser["B"][0][31:181], 5)
            rows.append(("T5d", side, ln, "FJ", "top5_F-B", d, "≥ +0,10 — «ставка на эпизод усилена» (описательно)" if d == d and d >= 0.10 else None))
    mem, _ = member_series(S, side)
    if mem is not None:
        val, sym = [], []
        ok = True
        for nm, _s in mem:
            tr = cell_trades(S.plan[side, S.ckey(side, nm)]["cid"])
            if tr is None:
                ok = False
                break
            chk = check_trades(tr, *_s)
            rows.append(("T5chk", side, nm, "H1", "max_abs_diff", chk[0], "расхождений n: %d" % chk[1]))
            f = trades_in(tr, 31, 181)
            val.append(f["val"] / 9.0)
            sym += list(f["sym"])
        if ok:
            trade_concentration(np.concatenate(val), sym, rows, "T5t", side, "B")
    for ln in LINES[side]:
        tr = cell_trades(S.plan[side, S.ckey(side, ln)]["cid"]) if S.ckey(side, ln) else None
        if tr is None:
            continue
        s = S.series(side, ln)
        if s is not None and not np.isnan(s[0][:181]).any():
            chk = check_trades(tr, *s)
            rows.append(("T5chk", side, ln, "H1", "max_abs_diff", chk[0], "расхождений n: %d" % chk[1]))
        f = trades_in(tr, 31, 181)
        trade_concentration(f["val"], list(f["sym"]), rows, "T5t", side, ln)
    t = tk692_trades(side)
    if t is not None and len(t[1]):
        trade_concentration(t[1], list(t[0]), rows, "T5t", side, "TK692")


# ───────────────────────────── запись / вердикт ─────────────────────────────

def save_rows(db, rows, tests):
    con = sqlite3.connect(db, timeout=120)
    con.executescript(TABLES)
    sides = {r[1] for r in rows}
    for t in tests:
        for s in sides:
            con.execute("DELETE FROM tk718_row WHERE test=? AND side=?", (t, s))
    con.executemany("INSERT OR REPLACE INTO tk718_row VALUES(?,?,?,?,?,?,?)", [r for r in rows if r[0] in tests])
    con.commit()
    con.close()


def git_head():
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, capture_output=True, text=True, timeout=10).stdout.strip() or "nogit"
    except Exception:
        return "nogit"


def cmd_run(a):
    S = Store(a.db)
    rows = []
    for side in SIDES:
        refs = ref_series(side)
        ser = t1_side(S, side, rows, refs)
        c1 = c1_eval(rows, side) if "B" in ser else {}
        for ln in LINES[side]:
            t2_line(S, ln, rows, side)
            s = S.series(side, ln)
            sm = wsum(s[0], "M") if s is not None else float("nan")
            rows.append(("C3a", side, ln, "M", "c3a", float("nan") if sm != sm else float(sm > 0), "Σ_M(F) = %.2f $ (вне выборки отбора)" % sm))
        t5_side(S, side, ser, rows)
        print("сторона %s: B %s, рядов T1 %d, строк %d" % (side, "есть" if "B" in ser else "нет", len(ser), len(rows)))
    save_rows(a.db, rows, {"T1", "T2", "T2nb", "T2ax", "C1", "C2", "C3a", "T5d", "T5t", "T5chk"})
    con = sqlite3.connect(a.db)
    con.execute("INSERT OR REPLACE INTO tk718_run VALUES('tests', ?, ?, ?)", (json.dumps(dict(seed=SEED, nboot=NBOOT)), git_head(), datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    con.close()
    return 0


def verdict_rows(rows):
    """Правило PS-1037 §5 по рядам test ∈ {C1, C2, C3a, T3}: сторона «здорово» ⇔ для КАЖДОЙ линии C1 ∧ C2 ∧ C3 (C3 = (a) ∧ (b)); None = нет данных. Возвращает {side: {line: {cond: bool|None}}, 'side': bool|None}."""
    g = {}
    for t, side, ser, win, stat, v, txt in rows:
        g[t, side, ser, win, stat] = v
    out = {}
    for side in SIDES:
        res = {}
        c1i = g.get(("C1", side, "B", "FJ", "i"))
        c3b = g.get(("T3", side, "verdict", "M", "c3b"))
        for ln in LINES[side]:
            def b(v):
                return None if v is None or v != v else bool(v)
            cond = {"C1(i)": b(c1i), "C1(ii)": b(g.get(("C1", side, ln, "FJ", "ii"))), "C1(iii)": b(g.get(("C1", side, ln, "FJ", "iii"))), "C2": b(g.get(("C2", side, ln, "ALL", "c2"))),
                    "C3(a)": b(g.get(("C3a", side, ln, "M", "c3a"))), "C3(b)": b(c3b)}
            res[ln] = cond
        vals = [v for c in res.values() for v in c.values()]
        res["side"] = False if any(v is False for v in vals) else (None if any(v is None for v in vals) else True)
        res["failed"] = sorted({(ln, k) for ln, c in res.items() if isinstance(c, dict) for k, v in c.items() if v is False})
        out[side] = res
    return out


def cmd_verdict(a):
    con = sqlite3.connect(a.db)
    rows = con.execute("SELECT test, side, series, win, stat, value, txt FROM tk718_row WHERE test IN ('C1','C2','C3a','T3')").fetchall()
    vd = verdict_rows(rows)
    out = []
    for side, res in vd.items():
        for ln in LINES[side]:
            for k, v in res[ln].items():
                out.append(("V", side, ln, "ALL", k, float("nan") if v is None else float(v), None))
        out.append(("V", side, "side", "ALL", "healthy", float("nan") if res["side"] is None else float(res["side"]), "провалено: %s" % (res["failed"] or "ничего")))
        print("%s: %s; провалено %s" % (side, {True: "здорово", False: "подгонка под эпизод", None: "нет данных"}[res["side"]], res["failed"]))
    con.execute("DELETE FROM tk718_row WHERE test='V'")
    con.executemany("INSERT OR REPLACE INTO tk718_row VALUES(?,?,?,?,?,?,?)", out)
    con.commit()
    con.close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("run", "verdict"):
        s = sp.add_parser(n)
        s.add_argument("--db", default=DB)
    a = ap.parse_args(argv)
    return {"run": cmd_run, "verdict": cmd_verdict}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
