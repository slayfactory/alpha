#!/usr/bin/env python3
"""TK-692 (PS-1007, В-182 / В-183): накопленное окно с 01.01.2026, переотбор одной клетки на сторону раз в месяц / неделю / 3 суток, торговля следующего шага.

Читает посуточные P&L клеток `mass_day` / `mass_cw` (TK-700, `mass.sqlite`, только чтение); движок не запускает. Правило отбора, охрана, шаги, базы и вердикт — в PS-1007 (летопись); числа r охраны — L-335.

  python3 tk692_walk.py run --db /data/p42-tk700/mass/mass.sqlite --out /data/p42-tk692/tk692.sqlite --pool A --cellset all1 \\
      --runs-long h1-jan-A --runs-short h1-jan-A-short [--sides long,short] [--extra /data/p42-tk692/replay.sqlite]       # run-id месяцев m01…, по стороне
  python3 tk692_walk.py verdict --out /data/p42-tk692/tk692.sqlite --rid A-m06          # Holm и граница разницы к якорю (после июня)

Сутки `day` — от D0 = 01.01.2026; месяц `m01…m06`; `through` — последний загруженный месяц. Формулы — как в `tk684_metrics` (Шарп: среднее ÷ σ(ddof = 1) × √365 по всем суткам, нули включены)."""
from __future__ import annotations

import argparse
import bisect
import datetime
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys

import numpy as np

D0 = datetime.date(2026, 1, 1)
MONTH_START = (0, 31, 59, 90, 120, 151, 181)                       # янв. … июнь: начало месяца в сутках от 01.01; последний элемент — конец июня
MONTHS = ("m01", "m02", "m03", "m04", "m05", "m06")
TOTAL = 181
YEAR_DAYS = 365
MARGIN = 250.0
EPS = 1e-9
RATE = {"long": 105 / 136, "short": 81 / 136}                      # охрана (а) L-335: сделок в сутки (лонг n ≥ 105, шорт n ≥ 81 на 136 суток)
STEPS = ("month", "week", "day3")
WEEK_FIRST = 11                                                     # понедельник 12.01 — граница после первой полной недели января (05–11.01)
SEED, NRAND, NBOOT = 692, 2000, 4000
EMBARGOS = (1, 0)                                                   # основная строка E = 1, справочная E = 0

# базы PS-1007 §5: (метка, ключ клетки `cell.ckey`, n, Σ $ на 01.07–13.11 — проверка, не сопоставление)
BASES = {
    "long": (
        ("anchor", "ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12", 298, 325.70),
        ("frozen-L369", "c=60 ew=5 fh=4 fm=50 l_days=14 lat=4 latx=4 p=1 pk=1 rf=0.2 rs=20 s=3 t24=12", 327, 375.90),
    ),
    "short": (
        ("anchor", "bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20", 172, 133.93),
        ("frozen-L384", "bm=1 e=0.15 e2t=2 ew=5 fh=4 fm=50 lat=4 latx=4 n_bounce=1 natr_min=0.6 nf=1 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20", 1456, 1177.05),
        ("frozen-L401", "bm=1 e=0.15 e2t=2 ew=5 fh=4 fm=50 lat=4 latx=4 n_bounce=1 natr_min=0.6 nf=1 pb=1 pk=1 rf=0.2 rr=1 rs=2 s=4 t24=12 tb=0.1 tt=20", 1469, 1187.22),
    ),
}


# ───────────────────────────── геометрия ─────────────────────────────

def month_of(day):
    return bisect.bisect_right(MONTH_START, day) - 1


def n_min(side, w):
    """Охрана (а): сделок в окне из w суток не меньше ⌈r · w⌉ (r — L-335)."""
    return int(math.ceil(RATE[side] * w - 1e-9))


def blocks(step, ndays):
    """[(b, e_nom, e)]: граница b (окно отбора — сутки до b), номинальный конец блока и конец с обрезкой по загруженным суткам; b == ndays — отложенный выбор без суток торговли."""
    if step == "month":
        raw = list(zip(MONTH_START[1:6], MONTH_START[2:7]))
    elif step == "week":
        raw = [(b, min(b + 7, TOTAL)) for b in range(WEEK_FIRST, TOTAL, 7)]
    elif step == "day3":
        raw = [(b, min(b + 3, TOTAL)) for b in range(3, TOTAL, 3)]
    else:
        raise ValueError(step)
    return [(b, en, min(en, ndays)) for b, en in raw if b <= ndays]


# ───────────────────────────── метрики ─────────────────────────────

def underwater(x):
    """Просадка и перехай суточного ряда, как `tk663_shape.uw` (самый долгий / средний зазор между новыми максимумами, хвост)."""
    x = np.asarray(x, float)
    n = len(x)
    if n == 0:
        return dict(maxdd=0.0, gap_longest=0, gap_mean=float("nan"), tail=0)
    cum = np.cumsum(x)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    prev = np.concatenate([[0.0], peak[:-1]])
    hi = np.nonzero(cum > prev + EPS)[0]
    pts = np.concatenate([[-1], hi]).astype(int)
    gaps = np.diff(pts)
    tail = n - 1 - int(pts[-1])
    return dict(maxdd=float((peak - cum).max()), gap_longest=int(max(list(gaps) + [tail])),
                gap_mean=float(gaps.mean()) if len(gaps) else float("nan"), tail=tail)


def sharpe_of(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return float("nan")
    sd = float(np.std(x, ddof=1))
    return float(x.mean() / sd * math.sqrt(YEAR_DAYS)) if sd > 0 else float("nan")


def series_stats(pnl, n):
    pnl = np.asarray(pnl, float)
    tot, nt = float(pnl.sum()), int(np.asarray(n).sum())
    u = underwater(pnl)
    return dict(days=len(pnl), n=nt, sum=tot, per_trade=tot / nt if nt else float("nan"), sharpe=sharpe_of(pnl), dd=u["maxdd"], ddp=u["maxdd"] / MARGIN * 100.0,
                gap_longest=u["gap_longest"], gap_mean=u["gap_mean"], gap_tail=u["tail"], top_day_share=float(pnl.max() / tot) if tot > 0 and len(pnl) else float("nan"))


# ───────────────────────────── набор клеток ─────────────────────────────

class Pool:
    """Клетки стороны: `X[c, d]` — P&L за сутки, `N[c, d]` — сделок, `P[c, m]` — клетка посчитана в месяце (строка `mass_cw`)."""

    def __init__(self, cids, X, N, P, ckeys=None, meta=None):
        self.cids, self.X, self.N, self.P = np.asarray(cids, np.int64), X, N, P
        self.ckeys = ckeys if ckeys is not None else [None] * len(self.cids)
        self.meta = meta or {}                                      # cid → (ckey, ref_n, ref_usd) всех клеток набора `cell` (не только загруженных)
        self.nd = X.shape[1]
        self.pre = None

    def prefix(self):
        if self.pre is None:
            self.pre = (np.cumsum(self.X, axis=1), np.cumsum(self.X * self.X, axis=1), np.cumsum(self.N, axis=1))
        return self.pre

    def index_of_ckey(self, ckey):
        for i, k in enumerate(self.ckeys):
            if k == ckey:
                return i
        return None

    def resolve_base(self, ckey, n0, usd0):
        """Сопоставление базы (PS-1007 §5): (1) равенство ключа; (2) семейство клеток с равным n, |Σ − usd0| ≤ 0,02 $, одинаковой Σ (разброс ≤ 0,001 $) и равными рядами загруженных месяцев; (3) None.
        Возвращает (индекс, способ, cid семейства)."""
        i = self.index_of_ckey(ckey)
        if i is not None:
            return i, "ckey", [int(self.cids[i])]
        fam = sorted(c for c, (_, n, u) in self.meta.items() if n == n0 and abs(u - usd0) <= 0.0201)
        if not fam:
            return None, "absent", []
        spread = max(self.meta[c][2] for c in fam) - min(self.meta[c][2] for c in fam)
        if spread > 0.001:
            return None, "family-sigma-differs(%d, разброс %.4f $)" % (len(fam), spread), fam
        pos = {int(c): i for i, c in enumerate(self.cids)}
        ids = [pos[c] for c in fam if c in pos]
        if not ids:
            return None, "family-not-loaded(%d)" % len(fam), fam
        x0, n0_ = self.X[ids[0]], self.N[ids[0]]
        if not all(np.array_equal(self.X[j], x0) and np.array_equal(self.N[j], n0_) for j in ids[1:]):
            return None, "family-series-differ(%d)" % len(fam), fam
        return ids[0], "family(%d из %d загружено)" % (len(ids), len(fam)), fam

    def present(self, idx, d0, d1):
        """Булева маска `(len(idx), d1 − d0)`: клетка посчитана в месяце каждых суток (иначе P&L суток = 0, шаг помечается)."""
        mi = np.array([month_of(d) for d in range(d0, d1)], int)
        return self.P[np.asarray(idx)][:, mi]

    def step_pnl(self, idx, d0, d1):
        idx = np.asarray(idx)
        pr = self.present(idx, d0, d1)
        return self.X[idx, d0:d1] * pr, self.N[idx, d0:d1] * pr, ~pr.all(axis=1)


def window_scores(pool, side, w):
    """Балл (Шарп окна из w суток), Σ $, сделок и маска охраны для всех клеток; клетки с σ = 0 или без охраны — вне маски."""
    s1, s2, sn = pool.prefix()
    tot, sq, nw = s1[:, w - 1], s2[:, w - 1], sn[:, w - 1]
    if w < 2:
        return np.full(len(tot), -np.inf), tot, nw, np.zeros(len(tot), bool)
    mean = tot / w
    var = np.maximum((sq - w * mean * mean) / (w - 1), 0.0)
    sd = np.sqrt(var)
    ok = (sd > 1e-9) & (nw >= n_min(side, w))
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.where(ok, mean / sd * math.sqrt(YEAR_DAYS), -np.inf)
    return score, tot, nw, ok


def eligible(pool, b):
    """Состав набора на границе b: клетки, посчитанные во всех месяцах, начавшихся до b."""
    return pool.P[:, :month_of(b - 1) + 1].all(axis=1)


def select(pool, side, b, emb):
    """Выбор клетки на границе b по окну [0, b − emb): максимум балла; при равенстве Σ $ окна, затем меньший cid. None — окно короче 2 суток или нет прошедших охрану."""
    w = b - emb
    if w < 2:
        return None
    score, tot, nw, ok = window_scores(pool, side, w)
    elig = eligible(pool, b) & ok
    cand = np.nonzero(elig)[0]
    if len(cand) == 0:
        return None
    mx = float(score[cand].max())
    top = cand[score[cand] >= mx]
    best = top[np.lexsort((pool.cids[top], -tot[top]))[0]]
    dup = int(((score[cand] == score[best]) & (tot[cand] == tot[best]) & (nw[cand] == nw[best])).sum())
    sc = score[cand]
    noise = float(np.std(sc) * math.sqrt(2.0 * math.log(len(cand)))) if len(cand) > 1 else float("nan")
    return dict(idx=int(best), cid=int(pool.cids[best]), w=w, score=float(score[best]), sum_w=float(tot[best]), n_w=int(nw[best]), m=len(cand), noise=noise, n_dup=dup, elig=elig)


def walk(pool, side, step, emb):
    """Отбор по всем границам шага: список выборов и склеенный ряд вне выборки (сутки, P&L, сделок, пометки пропуска)."""
    picks, days, pnl, nn = [], [], [], []
    for b, en, e in blocks(step, pool.nd):
        pk = select(pool, side, b, emb)
        rec = dict(b=b, e_nom=en, e=e, pick=pk, skipped=False)
        if pk is not None and e > b:
            p, n, miss = pool.step_pnl([pk["idx"]], b, e)
            days += list(range(b, e))
            pnl += list(p[0])
            nn += list(n[0])
            rec["skipped"] = bool(miss[0])
        picks.append(rec)
    return picks, np.array(days, int), np.array(pnl, float), np.array(nn, int)


def line_hash(pool, idx):
    return hashlib.md5(pool.X[idx].tobytes() + pool.N[idx].tobytes()).hexdigest()


def line_changes(pool, picks):
    hs = [line_hash(pool, r["pick"]["idx"]) for r in picks if r["pick"] is not None and r["e"] > r["b"]]
    return sum(1 for a, c in zip(hs, hs[1:]) if a != c), len(set(hs))


BASE_CID0 = 9_000_000                                               # cid баз вне набора (повтор движком, `tk692_replay.py base`): 9_000_000 + номер базы в BASES[side]


def extra_base(path, side, label, nd):
    """Ряд базы без клетки в наборе из повтора движком (§5 ступень 3): (P&L по суткам, сделок по суткам, cid) или None, если нет ворот Jul–Nov или повтор не покрыл все загруженные месяцы."""
    if not path or not os.path.exists(path):
        return None
    cid = BASE_CID0 + [lb for lb, *_ in BASES[side]].index(label) + 1
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        ok = con.execute("SELECT ok FROM rp_gate WHERE side=? AND cid=? AND month=-1 ORDER BY run_id DESC", (side, cid)).fetchone()
        got = {m for (m,) in con.execute("SELECT month FROM rp_done WHERE side=? AND cellset=? AND cid=?", (side, "base-" + label, cid))}
        if not ok or not ok[0] or got != set(range(month_of(nd - 1) + 1)):
            return None
        x, n = np.zeros(nd), np.zeros(nd, np.int32)
        for d, p_, k in con.execute("SELECT entry_day, sum(val), count(*) FROM rp_trade WHERE side=? AND cellset=? AND cid=? GROUP BY entry_day", (side, "base-" + label, cid)):
            x[d], n[d] = p_, k
        return x, n, cid
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def base_series(pool, idx, days):
    """Ряд клетки-базы на сутках `days` (с учётом пропуска месяцев); None — клетки нет."""
    if idx is None:
        return None
    p, n, miss = pool.step_pnl([idx], 0, pool.nd)
    return p[0][days], n[0][days], bool(miss[0])


# ───────────────────────────── случайные клетки, бутстреп, Holm ─────────────────────────────

def rand_c1(pool, side, picks_rows, emb, R, rng):
    """(c1) каждый шаг независимо из клеток набора, прошедших охрану на этом шаге; ряды `(R, T)`."""
    cols = []
    for r in picks_rows:
        b, e = r["b"], r["e"]
        if e <= b or r["pick"] is None:
            continue
        pool_idx = np.nonzero(r["pick"]["elig"])[0]
        cells = pool_idx[rng.integers(0, len(pool_idx), R)]
        cols.append(pool.step_pnl(cells, b, e)[0])
    return np.concatenate(cols, axis=1) if cols else np.zeros((R, 0))


def rand_c2(pool, side, picks_rows, days, R, rng):
    """(c2) одна клетка из набора первого шага вне выборки на весь ряд."""
    first = next((r for r in picks_rows if r["pick"] is not None and r["e"] > r["b"]), None)
    if first is None or len(days) == 0:
        return np.zeros((R, 0))
    pool_idx = np.nonzero(first["pick"]["elig"])[0]
    cells = pool_idx[rng.integers(0, len(pool_idx), R)]
    p, _, _ = pool.step_pnl(cells, 0, pool.nd)
    return p[:, days]


def dist_stats(G):
    """Среднее, 10-й / 90-й процентиль Σ $ и Шарпа розыгрышей; медианы нет."""
    if G.shape[1] < 2:
        return {}
    s = G.sum(axis=1)
    sd = G.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sh = np.where(sd > 0, G.mean(axis=1) / sd * math.sqrt(YEAR_DAYS), np.nan)
    v = sh[~np.isnan(sh)]
    nan = float("nan")
    return dict(sum_mean=float(s.mean()), sum_p10=float(np.percentile(s, 10)), sum_p90=float(np.percentile(s, 90)),
                sharpe_mean=float(v.mean()) if len(v) else nan, sharpe_p10=float(np.percentile(v, 10)) if len(v) else nan, sharpe_p90=float(np.percentile(v, 90)) if len(v) else nan), s, sh


def share_ge(sample, value):
    """Поправка (k + 1) / (R + 1): k — розыгрышей не ниже выбранной."""
    sample = np.asarray(sample, float)
    return float(((sample >= value - 1e-9).sum() + 1) / (len(sample) + 1))


def boot_sums(d, B, rng):
    """Бутстреп Σ разницы суточных рядов блоками ⌈n^{1/3}⌉ (скользящие блоки, обрезка до n)."""
    d = np.asarray(d, float)
    n = len(d)
    L = max(1, int(math.ceil(n ** (1.0 / 3.0))))
    nb = int(math.ceil(n / L))
    starts = rng.integers(0, max(1, n - L + 1), (B, nb))
    idx = (starts[:, :, None] + np.arange(L)[None, None, :]).reshape(B, nb * L)[:, :n]
    return d[idx].sum(axis=1)


def holm(pvals, alpha=0.05):
    """Holm: ранги по возрастанию p, строка ранга j из m проходит при p ≤ α / (m − j + 1) и если прошли все прежние. Возвращает (прошла, α_j) в порядке входа."""
    m = len(pvals)
    order = np.argsort(pvals, kind="stable")
    res = [None] * m
    alive = True
    for j, i in enumerate(order, start=1):
        aj = alpha / (m - j + 1)
        alive = alive and pvals[i] <= aj
        res[i] = (bool(alive), aj)
    return res


# ───────────────────────────── загрузка из mass.sqlite ─────────────────────────────

def load_pool(con, runs, cellset, side):
    """Набор клеток стороны по run-id месяцев. Месяц запуска — `mass_run.wins` (один из m01…m06); сутки `day` — от D0 запуска (= 01.01.2026)."""
    info = {}
    for r in runs:
        row = con.execute("SELECT cellset, wins, params FROM mass_run WHERE run_id=?", (r,)).fetchone()
        if row is None:
            raise SystemExit("нет запуска %s" % r)
        wins = [w for w in row[1].split(",") if w]
        if len(wins) != 1 or wins[0] not in MONTHS:
            raise SystemExit("запуск %s: ожидается одно окно m01…m06, а не %r" % (r, row[1]))
        if json.loads(row[2]).get("D0") != D0.isoformat():
            raise SystemExit("запуск %s: D0 не 2026-01-01" % r)
        if row[0] != cellset:
            raise SystemExit("запуск %s: набор %s, ожидался %s" % (r, row[0], cellset))
        info[r] = MONTHS.index(wins[0])
    months = sorted(info.values())
    if months != list(range(len(months))):
        raise SystemExit("месяцы запусков не идут подряд с m01: %s" % months)
    nm = len(months)
    nd = MONTH_START[nm]
    meta = {c: (k, n, u) for c, k, n, u in con.execute("SELECT cid, ckey, ref_n, ref_usd FROM cell WHERE cellset=? AND side=?", (cellset, side))}
    cell = {c: v[0] for c, v in meta.items()}
    per_month = {}
    for r, mi in info.items():
        cs = [c for (c,) in con.execute("SELECT cid FROM mass_cw WHERE run_id=?", (r,)) if c in cell]
        per_month[mi] = (r, set(cs))
    cids = sorted(set().union(*[v[1] for v in per_month.values()]))
    pos = {c: i for i, c in enumerate(cids)}
    X = np.zeros((len(cids), nd))
    N = np.zeros((len(cids), nd), np.int32)
    P = np.zeros((len(cids), nm), bool)
    for mi, (r, cs) in per_month.items():
        if len(set(cs) & set(c for c in cids if P[pos[c], mi])):
            raise SystemExit("клетка в месяце %d встречается дважды" % mi)
        for c in cs:
            P[pos[c], mi] = True
        cur = con.execute("SELECT cid, day, pnl, n FROM mass_day WHERE run_id=?", (r,))
        while True:
            rows = cur.fetchmany(1_000_000)
            if not rows:
                break
            a = np.array([(c, d, p, n) for c, d, p, n in rows if c in cell], float)
            if len(a) == 0:
                continue
            ii = np.fromiter((pos[int(c)] for c in a[:, 0]), np.int64, len(a))
            dd = a[:, 1].astype(np.int64)
            if dd.max() >= nd or dd.min() < MONTH_START[mi] or dd.max() >= MONTH_START[mi + 1]:
                raise SystemExit("запуск %s: сутки вне месяца %d" % (r, mi))
            X[ii, dd] += a[:, 2]
            N[ii, dd] += a[:, 3].astype(np.int32)
    return Pool(cids, X, N, P, [cell[c] for c in cids], meta)


# ───────────────────────────── запись ─────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS tk692_run(rid TEXT PRIMARY KEY, pool TEXT, through TEXT, runs TEXT, params TEXT, code TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS tk692_pick(rid TEXT, side TEXT, step TEXT, emb INTEGER, b INTEGER, e_nom INTEGER, e INTEGER, cid INTEGER, w INTEGER, score REAL, sum_w REAL, n_w INTEGER,
    m INTEGER, noise REAL, n_dup INTEGER, skipped INTEGER, why TEXT, PRIMARY KEY(rid, side, step, emb, b));
CREATE TABLE IF NOT EXISTS tk692_oos(rid TEXT, side TEXT, step TEXT, emb INTEGER, day INTEGER, cid INTEGER, pnl REAL, n INTEGER, PRIMARY KEY(rid, side, step, emb, day));
CREATE TABLE IF NOT EXISTS tk692_sum(rid TEXT, side TEXT, step TEXT, emb INTEGER, series TEXT, days INTEGER, n INTEGER, sum REAL, per_trade REAL, sharpe REAL, dd REAL, ddp REAL,
    gap_longest INTEGER, gap_mean REAL, gap_tail INTEGER, top_day_share REAL, extra TEXT, PRIMARY KEY(rid, side, step, emb, series));
CREATE TABLE IF NOT EXISTS tk692_rand(rid TEXT, side TEXT, step TEXT, emb INTEGER, kind TEXT, stat TEXT, value REAL, PRIMARY KEY(rid, side, step, emb, kind, stat));
CREATE TABLE IF NOT EXISTS tk692_rr(rid TEXT, side TEXT, step TEXT, emb INTEGER, series TEXT, rr REAL, stop_loss REAL, stop_loss_pct REAL, leak_share REAL, note TEXT, PRIMARY KEY(rid, side, step, emb, series));
CREATE TABLE IF NOT EXISTS tk692_verdict(rid TEXT, side TEXT, step TEXT, p REAL, alpha_j REAL, holm INTEGER, boot_lo REAL, e0_positive INTEGER, verdict TEXT, note TEXT, PRIMARY KEY(rid, side, step));
CREATE TABLE IF NOT EXISTS tk692_cmp(rid TEXT, side TEXT, win TEXT, series TEXT, step TEXT, cid INTEGER, days INTEGER, n INTEGER, sum REAL, per_trade REAL, sharpe REAL, dd REAL, ddp REAL,
    extra TEXT, PRIMARY KEY(rid, side, win, series, step));
"""


def git_head():
    try:
        return subprocess.check_output(["git", "-C", os.path.dirname(os.path.abspath(__file__)), "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def sum_row(rid, side, step, emb, series, st, extra=None):
    return (rid, side, step, emb, series, st["days"], st["n"], st["sum"], st["per_trade"], st["sharpe"], st["dd"], st["ddp"], st["gap_longest"], st["gap_mean"],
            st["gap_tail"], st["top_day_share"], json.dumps(extra or {}, ensure_ascii=False))


def skip_reason(con, runs, cid):
    rows = con.execute("SELECT run_id, win, why FROM mass_skip WHERE cid=?", (cid,)).fetchall()
    return "; ".join("%s %s %s" % r for r in rows)


def check_consistency(out, rid, side, step, emb, picks, nd, pool_name):
    """Выбор шага зависит только от суток до границы и состава набора по месяцам до границы: при добавлении месяца выборы шагов, целиком лежавших в прежних месяцах, не меняются."""
    for (prid,) in out.execute("SELECT DISTINCT rid FROM tk692_pick WHERE side=? AND rid LIKE ? AND rid<>?", (side, pool_name + "-m%", rid)).fetchall():
        pnd = MONTH_START[MONTHS.index(prid.split("-")[-1]) + 1]
        if pnd >= nd:
            continue
        old = {b: c for b, c, en in out.execute("SELECT b, cid, e_nom FROM tk692_pick WHERE rid=? AND side=? AND step=? AND emb=?", (prid, side, step, emb)) if en <= pnd}
        bad = [(r["b"], old[r["b"]], r["pick"]["cid"] if r["pick"] else None) for r in picks if r["b"] in old and (r["pick"]["cid"] if r["pick"] else None) != old[r["b"]]]
        if bad:
            raise SystemExit("выбор изменился против %s (%s %s E=%d): (b, было, стало) %s" % (prid, side, step, emb, bad[:5]))


def window_bounds(nd):
    """[(окно, d0, d1, месяцы)]: каждый загруженный месяц и «cum» (все загруженные сутки)."""
    nm = month_of(nd - 1) + 1
    return [(MONTHS[m], MONTH_START[m], min(MONTH_START[m + 1], nd), [m]) for m in range(nm)] + [("cum", 0, nd, list(range(nm)))]


def anchor_series(pool, side, extra_path):
    """Ряд якоря L-334 стороны (базовая линия без осей фильтра): клетка набора (ключ / семейство) или повтор движком; None — нет."""
    label, ck, n0, usd0 = BASES[side][0]
    idx, how, fam = pool.resolve_base(ck, n0, usd0)
    if idx is not None:
        p, n, miss = pool.step_pnl([idx], 0, pool.nd)
        return p[0], n[0], int(pool.cids[idx]), how + (", пропуск месяца" if miss[0] else "")
    xb = extra_base(extra_path, side, label, pool.nd)
    return (xb[0], xb[1], xb[2], "replay") if xb is not None else None


def cmp_row(rid, side, win, series, step, cid, st, extra):
    return (rid, side, win, series, step, cid, st["days"], st["n"], st["sum"], st["per_trade"], st["sharpe"], st["dd"], st["ddp"], json.dumps(extra, ensure_ascii=False))


def cmp_rows(pool, side, rid, anchor):
    """Якорь (базовая линия) и лучшая клетка набора по каждому окну; лучшая — максимум Σ $ окна среди клеток, посчитанных во всех месяцах окна, с охраной (а) — найдено на тех же данных."""
    rows = []
    for win, d0, d1, ms in window_bounds(pool.nd):
        tot, nn = pool.X[:, d0:d1].sum(axis=1), pool.N[:, d0:d1].sum(axis=1)
        cand = np.nonzero(pool.P[:, ms].all(axis=1) & (nn >= n_min(side, d1 - d0)))[0]
        a_sum, ext_a = None, {}
        if anchor is not None:
            st = series_stats(anchor[0][d0:d1], anchor[1][d0:d1])
            a_sum, ext_a = st["sum"], dict(how=anchor[3])
        if len(cand):
            best = cand[np.lexsort((pool.cids[cand], -tot[cand]))[0]]
            ext_b = dict(m=int(len(cand)), found_on_same_data=True)
            if a_sum is not None:
                above = int((tot[cand] > a_sum).sum())
                ext_b["n_above_anchor"], ext_a["n_above_anchor"], ext_a["m"] = above, above, int(len(cand))
            rows.append(cmp_row(rid, side, win, "best", "", int(pool.cids[best]), series_stats(pool.X[best, d0:d1], pool.N[best, d0:d1]), ext_b))
        if anchor is not None:
            rows.append(cmp_row(rid, side, win, "anchor", "", anchor[2], st, ext_a))
    return rows


def process_side(con, out, rid, pool, side, args):
    ckeys = {label: (ck, n, usd) for label, ck, n, usd in BASES[side]}
    report = []
    out.executemany("INSERT OR REPLACE INTO tk692_cmp VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cmp_rows(pool, side, rid, anchor_series(pool, side, getattr(args, "extra", ""))))
    for step in STEPS:
        for emb in EMBARGOS:
            rng = np.random.default_rng([SEED, 0 if side == "long" else 1, STEPS.index(step), emb])
            picks, days, pnl, nn = walk(pool, side, step, emb)
            check_consistency(out, rid, side, step, emb, picks, pool.nd, args.pool)
            for r in picks:
                pk = r["pick"]
                why = skip_reason(con, None, pk["cid"]) if pk is not None and r["skipped"] else ""
                out.execute("INSERT OR REPLACE INTO tk692_pick VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (rid, side, step, emb, r["b"], r["e_nom"], r["e"], pk["cid"] if pk else None, pk["w"] if pk else None, pk["score"] if pk else None, pk["sum_w"] if pk else None,
                             pk["n_w"] if pk else None, pk["m"] if pk else None, pk["noise"] if pk else None, pk["n_dup"] if pk else None, int(r["skipped"]), why))
            tr = [r for r in picks if r["pick"] is not None and r["e"] > r["b"]]
            if tr:
                k = 0
                rows = []
                for r in tr:
                    for d in range(r["b"], r["e"]):
                        rows.append((rid, side, step, emb, d, r["pick"]["cid"], float(pnl[k]), int(nn[k])))
                        k += 1
                out.executemany("INSERT OR REPLACE INTO tk692_oos VALUES(?,?,?,?,?,?,?,?)", rows)
            if len(days) < 2:
                report.append((side, step, emb, "нет суток вне выборки: выборов %d, последний b=%d" % (sum(1 for r in picks if r["pick"]), picks[-1]["b"] if picks else -1)))
                continue
            st = series_stats(pnl, nn)
            ch, dist = line_changes(pool, picks)
            hi = max((r["pick"]["noise"] for r in tr if r["pick"]["noise"] == r["pick"]["noise"]), default=float("nan"))
            extra = dict(changes=ch, distinct=dist, skipped_steps=sum(r["skipped"] for r in tr), steps=len(tr), win_sharpe_mean=float(np.mean([r["pick"]["score"] for r in tr])), noise_max=hi)
            out.execute("INSERT OR REPLACE INTO tk692_sum VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", sum_row(rid, side, step, emb, "select", st, extra))
            if emb == EMBARGOS[0]:
                for win, d0, d1, _ in window_bounds(pool.nd):
                    mk = (days >= d0) & (days < d1)
                    if mk.any():
                        out.execute("INSERT OR REPLACE INTO tk692_cmp VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cmp_row(rid, side, win, "select", step, None, series_stats(pnl[mk], nn[mk]), dict(emb=emb)))
            g1 = rand_c1(pool, side, picks, emb, args.nrand, rng)
            g2 = rand_c2(pool, side, picks, days, args.nrand, rng)
            for kind, G in (("c1", g1), ("c2", g2)):
                ds = dist_stats(G)
                if not ds:
                    continue
                stats, s, sh = ds
                stats["p_sum"] = share_ge(s, st["sum"])
                stats["p_sharpe"] = share_ge(sh[~np.isnan(sh)], st["sharpe"]) if st["sharpe"] == st["sharpe"] else float("nan")
                for k2, v in stats.items():
                    out.execute("INSERT OR REPLACE INTO tk692_rand VALUES(?,?,?,?,?,?,?)", (rid, side, step, emb, kind, k2, v))
            for label, (ck, n0, usd0) in ckeys.items():
                idx, how, fam = pool.resolve_base(ck, n0, usd0)
                xb = extra_base(getattr(args, "extra", ""), side, label, pool.nd) if idx is None else None
                if idx is None and xb is None:
                    out.execute("INSERT OR REPLACE INTO tk692_sum VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (rid, side, step, emb, label, 0, 0, None, None, None, None, None, None, None, None, None, json.dumps(dict(waiting="повтор движком", how=how, fam=fam))))
                    continue
                if xb is not None:
                    bs, bcid, how, fam = (xb[0][days], xb[1][days], False), xb[2], "replay(%s)" % how, []
                else:
                    bs, bcid = base_series(pool, idx, days), int(pool.cids[idx])
                stb = series_stats(bs[0], bs[1])
                bd = boot_sums(pnl - bs[0], args.nboot, np.random.default_rng([SEED, 7, STEPS.index(step), emb]))
                ext = dict(cid=bcid, how=how, fam=fam, miss=bs[2], diff=float((pnl - bs[0]).sum()), boot_lo={str(k): float(np.percentile(bd, 100 * 0.05 / k)) for k in range(1, 25)})
                out.execute("INSERT OR REPLACE INTO tk692_sum VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", sum_row(rid, side, step, emb, label, stb, ext))
            report.append((side, step, emb, st, extra))
    return report


def cmd_run(a):
    con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    out = sqlite3.connect(a.out)
    out.executescript(SCHEMA)
    for side in [s for s in a.sides.split(",") if s]:
        runs = [r for r in getattr(a, "runs_" + side).split(",") if r]
        if not runs:
            raise SystemExit("--runs-%s пуст" % side)
        pool = load_pool(con, runs, a.cellset, side)
        through = MONTHS[pool.P.shape[1] - 1]
        rid = "%s-%s" % (a.pool, through)
        for t in ("tk692_pick", "tk692_oos", "tk692_sum", "tk692_rand", "tk692_rr", "tk692_verdict", "tk692_cmp"):
            out.execute("DELETE FROM %s WHERE rid=? AND side=?" % t, (rid, side))
        out.execute("INSERT OR REPLACE INTO tk692_run VALUES(?,?,?,?,?,?,?)", (rid + "/" + side, a.pool, through, ",".join(runs), json.dumps(dict(side=side, cellset=a.cellset, nrand=a.nrand, nboot=a.nboot, cells=len(pool.cids))),
                                                                           git_head(), datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
        rep = process_side(con, out, rid, pool, side, a)
        out.commit()
        print_report(rid, side, rep, len(pool.cids))
    con.close()
    out.close()
    return 0


def cmd_verdict(a):
    """Вердикт PS-1007 §8 по строкам основной схемы (E = 1): Holm по p c1, граница разницы к якорю на уровне α_j, Σ $ > 0 при E = 0."""
    out = sqlite3.connect(a.out)
    rows = out.execute("SELECT side, step, sum FROM tk692_sum WHERE rid=? AND series='select' AND emb=1", (a.rid,)).fetchall()
    if not rows:
        raise SystemExit("нет строк для %s" % a.rid)
    keys, ps = [], []
    for side, step, tot in rows:
        r = out.execute("SELECT value FROM tk692_rand WHERE rid=? AND side=? AND step=? AND emb=1 AND kind='c1' AND stat='p_sum'", (a.rid, side, step)).fetchone()
        keys.append((side, step, tot))
        ps.append(r[0] if r else 1.0)
    hm = holm(ps)
    for (side, step, tot), p, (ok, aj) in zip(keys, ps, hm):
        e0 = out.execute("SELECT sum FROM tk692_sum WHERE rid=? AND side=? AND step=? AND emb=0 AND series='select'", (a.rid, side, step)).fetchone()
        an = out.execute("SELECT extra FROM tk692_sum WHERE rid=? AND side=? AND step=? AND emb=1 AND series='anchor'", (a.rid, side, step)).fetchone()
        ext = json.loads(an[0]) if an else {}
        k = int(round(0.05 / aj))
        lo = ext.get("boot_lo", {}).get(str(k))
        e0pos = int(e0 is not None and e0[0] is not None and e0[0] > 0)
        top = out.execute("SELECT top_day_share FROM tk692_sum WHERE rid=? AND side=? AND step=? AND emb=1 AND series='select'", (a.rid, side, step)).fetchone()
        if tot is None or tot <= 0:
            v, note = "не держится", "Σ $ ≤ 0"
        elif lo is None:
            v, note = "ждёт повтора", "ряд якоря L-334 не получен (§5, §9)"
        elif e0pos and ok and lo > 0:
            v, note = "держится", ""
        else:
            v, note = "не отличается от случайного", "E=0>0: %d, Holm: %d, нижняя граница к якорю: %s" % (e0pos, int(ok), lo)
        if top and top[0] is not None and top[0] == top[0] and top[0] > 0.5:
            note += " доля максимальных суток в Σ %.0f %% > 50 %%" % (top[0] * 100)
        out.execute("INSERT OR REPLACE INTO tk692_verdict VALUES(?,?,?,?,?,?,?,?,?,?)", (a.rid, side, step, p, aj, int(ok), lo, e0pos, v, note.strip()))
        print("%-5s %-6s Σ %9.2f $  p %.4f  α_j %.4f  Holm %d  нижняя граница к якорю %s  E=0>0 %d  → %s %s" % (side, step, tot, p, aj, ok, "%.2f" % lo if lo is not None else "—", e0pos, v, note))
    out.commit()
    out.close()
    return 0


def cmd_cmp(a):
    """Таблица «база против отобранных»: по стороне и окну — якорь L-334 (базовая линия без фильтра), выбранная линия по шагам (E = 1), лучшая клетка пула (найдено на тех же данных)."""
    out = sqlite3.connect("file:%s?mode=ro" % a.out, uri=True)
    rids = [r for r in a.rids.split(",") if r]
    for side in [x for x in a.sides.split(",") if x]:
        print("### %s: база против отобранных (ряд — суммарно по окну; ставка 500 $, маржа 250 $)" % side)
        print("| окно | пул | ряд | шаг | cid | суток | сд. | Σ $ | Шарп | просадка $ (% маржи) | примечание |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        wins = [w for (w,) in out.execute("SELECT DISTINCT win FROM tk692_cmp WHERE side=? AND rid IN (%s)" % ",".join("?" * len(rids)), [side] + rids)]
        for win in sorted(wins, key=lambda w: (w == "cum", w)):
            for rid in rids:
                for ser, step, cid, days, n, tot, sh, dd, ddp, ex in out.execute(
                        "SELECT series, step, cid, days, n, sum, sharpe, dd, ddp, extra FROM tk692_cmp WHERE rid=? AND side=? AND win=? ORDER BY series='select', series, step", (rid, side, win)):
                    ex = json.loads(ex)
                    note = []
                    if ser == "anchor":
                        note.append(ex.get("how", ""))
                        if "n_above_anchor" in ex:
                            note.append("клеток выше базы %d из %d" % (ex["n_above_anchor"], ex["m"]))
                    elif ser == "best":
                        note.append("найдено на этих данных, клеток %d" % ex["m"])
                        if "n_above_anchor" in ex:
                            note.append("выше базы %d" % ex["n_above_anchor"])
                    print("| %s | %s | %s | %s | %s | %d | %d | %+.2f | %.2f | %.2f (%.1f %%) | %s |" % (
                        win, rid.split("-")[0], ser, step or "—", cid if cid is not None else "—", days, n, tot, sh, dd, ddp, "; ".join(x for x in note if x)))
        print()
    return 0


def cmd_report(a):
    """Таблица месяца из БД результатов: по стороне и шагу — выбранная линия, базы, случайные (c1 / c2); при `--db` — ключи выбранных клеток последнего шага."""
    out = sqlite3.connect("file:%s?mode=ro" % a.out, uri=True)
    src = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True) if a.db else None
    for side, in out.execute("SELECT DISTINCT side FROM tk692_sum WHERE rid=? ORDER BY side", (a.rid,)).fetchall():
        print("### %s %s (E = %d; ряд вне выборки, ставка 500 $, маржа 250 $)" % (a.rid, side, a.emb))
        print("| шаг | ряд | суток | сд. | Σ $ | $/сд. | Шарп | просадка $ (% маржи) | перехай (долгий / средний / хвост) | примечание |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for step in STEPS:
            rows = out.execute("SELECT series, days, n, sum, per_trade, sharpe, dd, ddp, gap_longest, gap_mean, gap_tail, top_day_share, extra FROM tk692_sum WHERE rid=? AND side=? AND step=? AND emb=? ORDER BY series='select' DESC, series",
                               (a.rid, side, step, a.emb)).fetchall()
            for ser, days, n, tot, pt, sh, dd, ddp, gl, gm, gt, top, extra in rows:
                ex = json.loads(extra or "{}")
                if tot is None:
                    print("| %s | %s | — | — | — | — | — | — | — | ждёт повтора: %s |" % (step, ser, ex.get("how", "")))
                    continue
                note = []
                if ser == "select":
                    note.append("смен %d из %d шагов, разных %d" % (ex.get("changes", 0), ex.get("steps", 0), ex.get("distinct", 0)))
                    if ex.get("skipped_steps"):
                        note.append("шагов с пропуском месяца %d" % ex["skipped_steps"])
                else:
                    note.append("cid %s, %s" % (ex.get("cid"), ex.get("how")))
                if top == top and top is not None and top > 0.5:
                    note.append("макс. сутки %.0f %% Σ" % (top * 100))
                print("| %s | %s | %d | %d | %+.2f | %+.2f | %.2f | %.2f (%.1f %%) | %d / %.1f / %d | %s |" % (step, ser, days, n, tot, pt, sh, dd, ddp, gl, gm, gt, "; ".join(note)))
            for kind in ("c1", "c2"):
                r = {k: v for k, v in out.execute("SELECT stat, value FROM tk692_rand WHERE rid=? AND side=? AND step=? AND emb=? AND kind=?", (a.rid, side, step, a.emb, kind))}
                if r:
                    print("| %s | случайная %s | | | среднее %+.2f [p10 %+.2f; p90 %+.2f] | | среднее %.2f | | | доля не ниже выбранной: Σ %.3f, Шарп %.3f |" % (
                        step, kind, r["sum_mean"], r["sum_p10"], r["sum_p90"], r["sharpe_mean"], r["p_sum"], r["p_sharpe"]))
        lastb = out.execute("SELECT step, b, cid, w, score, sum_w, n_w, m, noise, n_dup FROM tk692_pick WHERE rid=? AND side=? AND emb=? AND e=b AND cid IS NOT NULL ORDER BY step", (a.rid, side, a.emb)).fetchall()
        for step, b, cid, wd, sc, sw, nw, m, noise, dup in lastb:
            ck = src.execute("SELECT ckey FROM cell WHERE cellset=? AND cid=?", ("all1", cid)).fetchone() if src else None
            print("\nСледующий выбор (%s, граница b = %d, окно %d сут.): cid %d, Шарп окна %.2f при шуме %.2f, Σ %.1f $, сделок %d, клеток в наборе %d, дублей %d%s" % (
                step, b, wd, cid, sc, noise, sw, nw, m, dup, ("\n  ключ: " + ck[0]) if ck else ""))
        print()
    return 0


def print_report(rid, side, rep, ncell):
    print("== %s %s: %d клеток" % (rid, side, ncell))
    for item in rep:
        if len(item) == 4:
            print("  %-6s E=%d  %s" % (item[1], item[2], item[3]))
            continue
        side_, step, emb, st, ex = item
        print("  %-6s E=%d  дней %3d  сд. %5d  Σ %9.2f $  %6.2f $/сд.  Шарп %6.2f  DD %7.2f $ (%5.1f %%)  перехай %d/%.1f/%d  смен %d из %d шагов (разных %d)" % (
            step, emb, st["days"], st["n"], st["sum"], st["per_trade"], st["sharpe"], st["dd"], st["ddp"], st["gap_longest"], st["gap_mean"], st["gap_tail"], ex["changes"], ex["steps"], ex["distinct"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--db", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--pool", default="A")
    r.add_argument("--cellset", default="all1")
    r.add_argument("--runs-long", default="")
    r.add_argument("--runs-short", default="")
    r.add_argument("--extra", default="", help="replay.sqlite повтора движком (`tk692_replay.py base`): ряды баз, которых нет в наборе (§5 ступень 3)")
    r.add_argument("--sides", default="long,short")
    r.add_argument("--nrand", type=int, default=NRAND)
    r.add_argument("--nboot", type=int, default=NBOOT)
    r.set_defaults(fn=cmd_run)
    v = sub.add_parser("verdict")
    v.add_argument("--out", required=True)
    v.add_argument("--rid", required=True)
    v.set_defaults(fn=cmd_verdict)
    c = sub.add_parser("cmp")
    c.add_argument("--out", required=True)
    c.add_argument("--rids", required=True, help="rid пулов через запятую, например A-m01,H-m01,B-m01")
    c.add_argument("--sides", default="long,short")
    c.set_defaults(fn=cmd_cmp)
    q = sub.add_parser("report")
    q.add_argument("--out", required=True)
    q.add_argument("--rid", required=True)
    q.add_argument("--emb", type=int, default=1)
    q.add_argument("--db", default="")
    q.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
