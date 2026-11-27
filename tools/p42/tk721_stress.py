#!/usr/bin/env python3
"""TK-721 (PS-1043 + поправка PS-1044): стресс ненастроенной базы B шорта — комиссии ×2 (S1), проскальзывание +1 / +2 бп (S3), месяцы (S4), половины монет (S5), фандинг (S6).
Задержка не стрессуется (В-168, PS-1044 п. 1). Движок — только `slip` 2 / 3 (`tk721_run.sh`); S1 / S4 / S5 / S6 — на готовых сделках B (В-213).

  python3 tk721_stress.py gate            # G1 (ряд из сделок = tk718_ser B), G2 (g0 = база на n1), G3 (БД движка), G4 (фандинг post hoc против движка) — печатает PASS / FAIL
  python3 tk721_stress.py run [--report <md>]   # ворота → ряды → бутстрепы → БД /data/p42-tk721/tk721.sqlite (tk721_row / tk721_month / tk721_day) → отчёт

Ряд B_d = (Σ P&L копий членов с входом в сутки d) / 9, простой P&L (gross − fees [− доп. издержки]), ставка 500 $ на копию, маржа 250 $. Сутки — от 01.01.2026 по дате входа UTC. Окна: Feb–Nov = сутки 31…327, SEL = 181…316, N1 = 317…327."""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import math
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk720_fwd as F  # noqa: E402  (underwater, sharpe, block_idx — без правки шорт-форварда)

ROOT = "/data/p42-tk721"
DB = ROOT + "/tk721.sqlite"
MASS = ROOT + "/mass721.sqlite"
TR718, TR719, TR721 = "/data/p42-tk718/trades", "/data/p42-tk719/trades", ROOT + "/trades"
C539 = "/data/p42-tk621/universe/symbols-crypto.txt"
MEMBERS = tuple(range(7180015, 7180024))
G0, S2, S3 = 7210000, 7210100, 7210200
D0 = datetime.date(2026, 1, 1)
D0_MS = int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
DAY_MS = 86_400_000
MONTH_START = (0, 31, 59, 90, 120, 151, 181)                      # янв. … июнь (как tk692_walk.MONTH_START)
MONTHS_H1 = ("m01", "m02", "m03", "m04", "m05", "m06")
D_LO, D_END = 31, 328                                             # Feb–Nov: сутки 31…327
SEL_LO, SEL_HI = 181, 317                                         # SEL: сутки 181…316
ND = D_END - D_LO
SEL = (SEL_LO - D_LO, SEL_HI - D_LO)
FEBNOV = (0, ND)
CREDIT = {                                                         # группа → {окно: (сутки с, по включительно)}; январь (m01) не считается (PS-1043 §1)
    "h1": {m: (MONTH_START[i], MONTH_START[i + 1] - 1) for i, m in enumerate(MONTHS_H1) if m != "m01"},
    "sel": {"wa": (181, 236), "wb": (237, 274), "wc": (275, 316)},
    "n1": {"n1": (317, 327)},
}
BASE_RUNS = {"h1": (TR718, "tk718c-h1-A"), "sel": (TR719, "tk719s-sel-A"), "n1": (TR719, "tk719n-n1-A")}
VAR_RUNS = {"h1": (TR721, "tk721s-h1-A"), "sel": (TR721, "tk721s-sel-A"), "n1": (TR721, "tk721s-n1-A")}
G0_RUN = (TR721, "tk721g-n1-A")
SEED, NBOOT, BLOCK = 721, 10000, 21
MARGIN, STAKE = 250.0, 500.0
NMEMB = 9
MIN_COPIES_MONTH = 30
M_HOLM, ALPHA = 6, 0.05
FUND_STAGE_DAY = 243                                               # 01.09: с этих суток в `funding.csv` деревьев есть ставки (колонка движка)
EPS = F.EPS
TABLES = """
CREATE TABLE IF NOT EXISTS tk721_row(test TEXT, series TEXT, win TEXT, stat TEXT, value REAL, txt TEXT, PRIMARY KEY(test, series, win, stat));
CREATE TABLE IF NOT EXISTS tk721_month(series TEXT, month TEXT, days INTEGER, sum REAL, copies INTEGER, per_copy REAL, sharpe REAL, dd REAL, ddp REAL, few INTEGER, lomo REAL, PRIMARY KEY(series, month));
CREATE TABLE IF NOT EXISTS tk721_day(series TEXT, day INTEGER, pnl REAL, n INTEGER, PRIMARY KEY(series, day));
"""


# ───────────────────────────── чистые функции (тесты — test_tk721.py) ─────────────────────────────

def uw(x):
    """Просадка $ и перехай суточного ряда (как `tk720_fwd.underwater`) + p90 зазора между новыми максимумами (сутки, вместе с хвостом) и доля суток под водой (сумма строго ниже своего максимума)."""
    x = np.asarray(x, float)
    out = dict(F.underwater(x))
    if len(x) == 0:
        return dict(out, gap_p90=float("nan"), under=float("nan"))
    cum = np.cumsum(x)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    prev = np.concatenate([[0.0], peak[:-1]])
    hi = np.nonzero(cum > prev + EPS)[0]
    pts = np.concatenate([[-1], hi]).astype(int)
    gaps = list(np.diff(pts)) + [len(x) - 1 - int(pts[-1])]
    out["gap_p90"] = float(np.percentile(gaps, 90))
    out["under"] = float(np.mean(cum < peak - EPS))
    return out


def stats(p, n):
    """Строка метрик ряда: Σ $, копий, $/копию (500 $) и в весе B, $/сут., Шарп, просадка $ и % маржи, перехай (долгий / средний / хвост / p90), доля суток под водой."""
    p = np.asarray(p, float)
    u = uw(p)
    tot, nt = float(p.sum()), int(np.sum(n))
    return dict(days=len(p), sum=tot, copies=nt, per_copy=NMEMB * tot / nt if nt else float("nan"), per_copy_w=tot / nt if nt else float("nan"), per_day=tot / len(p) if len(p) else float("nan"),
                sharpe=F.sharpe(p), dd=u["maxdd"], ddp=u["maxdd"] / MARGIN * 100.0, gap_longest=u["gap_longest"], gap_mean=u["gap_mean"], gap_tail=u["tail"], gap_p90=u["gap_p90"], under=u["under"])


def holm(ps):
    """Скорректированные p по Холму (шаг вниз), в исходном порядке."""
    m = len(ps)
    order = np.argsort(ps, kind="stable")
    adj, run = [0.0] * m, 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * ps[i]))
        adj[i] = run
    return adj


def binom_p(k, n=10):
    """Точный односторонний p: P(X ≥ k), X ~ Bin(n, ½)."""
    return sum(math.comb(n, j) for j in range(k, n + 1)) / 2 ** n


def boot_idx(n):
    """Скользящие блоки 21 сут., 10 000 путей; генератор от (зерно 721, n): один набор путей для всех рядов одной длины (парные разности)."""
    return F.block_idx(n, BLOCK, NBOOT, np.random.default_rng([SEED, n]))


def boot_sum(x, idx):
    return np.asarray(x, float)[idx].sum(axis=1)


def p_le0(sums):
    """p = (k + 1) / (R + 1), k — пути с Σ ≤ 0."""
    return (int((np.asarray(sums) <= 0).sum()) + 1) / (len(sums) + 1)


def month_bounds():
    """10 календарных месяцев Feb…Nov: (имя, сутки с, сутки по не включая); ноябрь — до суток 328 (24 сут.)."""
    out = []
    for mm in range(2, 12):
        a = (datetime.date(2026, mm, 1) - D0).days
        b = (datetime.date(2026, mm + 1, 1) - D0).days if mm < 11 else D_END
        out.append(("2026-%02d" % mm, a, b))
    return out


def split_coins(coins, seed=SEED):
    """Половины монет: `sorted(coins)`, `default_rng(seed).permutation(n)`, первые ⌊n/2⌋ — половина A. → (A, B) множества."""
    cs = sorted(coins)
    perm = np.random.default_rng(seed).permutation(len(cs))
    h = len(cs) // 2
    return {cs[i] for i in perm[:h]}, {cs[i] for i in perm[h:]}


def daily(day, val, nmemb=NMEMB):
    """(P&L, копий) по суткам Feb–Nov: Σ val по дате входа / nmemb."""
    d = np.asarray(day) - D_LO
    ok = (d >= 0) & (d < ND)
    return np.bincount(d[ok], weights=np.asarray(val, float)[ok], minlength=ND) / nmemb, np.bincount(d[ok], minlength=ND)


def posthoc_funding(ent, ext, ts, rate, notional=STAKE):
    """Фандинг копии шорта: каждая выплата строго между входом и выходом; шорт получает при ставке > 0; полный номинал, mark/e = 1. ts — отсортированные времена выплат (мс), rate — ставки."""
    ts = np.asarray(ts, np.int64)
    if len(ts) == 0:
        return np.zeros(len(ent))
    cs = np.concatenate([[0.0], np.cumsum(rate)])
    k0 = np.searchsorted(ts, np.asarray(ent), side="right")
    k1 = np.searchsorted(ts, np.asarray(ext), side="left")
    return np.where(k1 > k0, cs[np.maximum(k1, k0)] - cs[k0], 0.0) * notional


# ───────────────────────────── сделки ─────────────────────────────

def read_trades(path):
    with open(path, encoding="utf-8", newline="") as f:
        rd = list(csv.DictReader(f))
    return dict(sym=np.array([r["sym"] for r in rd], dtype=object), ent=np.array([int(r["entry_ms"]) for r in rd], np.int64), ext=np.array([int(r["exit_ms"]) for r in rd], np.int64),
                gross=np.array([float(r["gross"]) for r in rd], float), fees=np.array([float(r["fees"]) for r in rd], float), fund=np.array([float(r["funding"]) for r in rd], float))


def load_group(grp, cids, runs):
    """Копии 9 членов группы: cid, sym, ent, ext [мс эпохи], day, gross, fees, fund (колонка движка); зачёт суток — CREDIT (по дате входа)."""
    root, run = runs[grp]
    t0 = json.load(open(os.path.join(root, run, "t0.json"), encoding="utf-8"))
    parts = []
    for win, (lo, hi) in CREDIT[grp].items():
        for cid in cids:
            t = read_trades(os.path.join(root, run, win, "%d-trades.csv" % cid))
            t["ent"], t["ext"] = t["ent"] + t0[win], t["ext"] + t0[win]
            t["day"] = (t["ent"] - D0_MS) // DAY_MS
            keep = (t["day"] >= lo) & (t["day"] <= hi)
            part = {k: v[keep] for k, v in t.items()}
            part["cid"] = np.full(int(keep.sum()), cid, np.int64)
            parts.append(part)
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def load_all(cids, runs):
    gs = [load_group(g, cids, runs) for g in ("h1", "sel", "n1")]
    return {k: np.concatenate([g[k] for g in gs]) for k in gs[0]}


def raw_set(root, run, win, cid):
    """Сделки CSV окна как множество (монета, вход, выход, gross, fees) — для ворот G2 (все строки, без зачёта суток)."""
    t = read_trades(os.path.join(root, run, win, "%d-trades.csv" % cid))
    return {(s, int(a), int(b), round(float(g), 6), round(float(f), 6)) for s, a, b, g, f in zip(t["sym"], t["ent"], t["ext"], t["gross"], t["fees"])}


# ───────────────────────────── ворота ─────────────────────────────

def gate_g1(base):
    """G1: ряд B из готовых сделок = tk718_ser B по суткам 31…327 (|Δ| ≤ 1e-9), число копий равно."""
    import tk718_tests as T
    st = T.Store("/data/p42-tk718/tk718.sqlite")
    mem = [(nm, st.series("short", nm)) for nm in st.members("short")]
    assert len(mem) == 9
    p, n = T.base_series(mem)
    pv, nv = daily(base["day"], base["gross"] - base["fees"])
    dmax = float(np.max(np.abs(pv - p[D_LO:D_END])))
    ndiff = int((nv != n[D_LO:D_END]).sum())
    ok = dmax <= 1e-9 and ndiff == 0
    print("G1: max|Δ| %.3g $, суток с другим числом копий %d → %s" % (dmax, ndiff, "PASS" if ok else "FAIL"))
    return ok


def gate_g2():
    """G2: g0 (строки без изменений, cid 7210000 + k) на n1 даёт сделки, равные `tk719n-n1-A` (множества по (монета, вход, выход, gross, fees))."""
    bad = 0
    n = 0
    for k, cid in enumerate(MEMBERS):
        a = raw_set(TR719, "tk719n-n1-A", "n1", cid)
        b = raw_set(G0_RUN[0], G0_RUN[1], "n1", G0 + k)
        n += len(a)
        bad += len(a ^ b)
    ok = bad == 0 and n > 0
    print("G2: g0 против базы на n1: сделок %d, различий %d → %s" % (n, bad, "PASS" if ok else "FAIL"))
    return ok


def gate_g3():
    """G3: по БД движка — состояние done, партии rc = 0, mass_skip 0, errs пусто (в errs — G-tape)."""
    c = sqlite3.connect("file:%s?mode=ro" % MASS, uri=True)
    ok = True
    for r in ("tk721g-n1-A", "tk721s-n1-A", "tk721s-sel-A", "tk721s-h1-A"):
        st = c.execute("SELECT state FROM mass_run WHERE run_id=?", (r,)).fetchone()
        nb = c.execute("SELECT count(*) FROM mass_batch WHERE run_id=? AND rc<>0", (r,)).fetchone()[0]
        okb = c.execute("SELECT count(*) FROM mass_batch WHERE run_id=? AND rc=0", (r,)).fetchone()[0]
        sk = c.execute("SELECT count(*) FROM mass_skip WHERE run_id=?", (r,)).fetchone()[0]
        er = c.execute("SELECT count(*) FROM mass_cw WHERE run_id=? AND errs<>''", (r,)).fetchone()[0]
        good = bool(st and st[0] == "done" and nb == 0 and okb > 0 and sk == 0 and er == 0)
        ok &= good
        print("G3 %s: state %s, партий rc=0 %d, rc≠0 %d, mass_skip %d, errs %d → %s" % (r, st[0] if st else None, okb, nb, sk, er, "PASS" if good else "FAIL"))
    return ok


# ───────────────────────────── фандинг (S6) ─────────────────────────────

def funding_posthoc(base):
    """Фандинг каждой копии базы (post hoc, архивы ставок `tk719_size.load_funding_coin`); покрыта ли копия: у монеты есть ставка не старше 9 ч на момент входа. → (fund[copies], covered[copies])."""
    import tk719_size as S
    fund = np.zeros(len(base["day"]))
    cov = np.zeros(len(base["day"]), bool)
    for sym in sorted(set(base["sym"].tolist())):
        ix = np.nonzero(base["sym"] == sym)[0]
        ts, rate = S.load_funding_coin(sym)
        if not len(ts):
            continue
        fund[ix] = posthoc_funding(base["ent"][ix], base["ext"][ix], ts, rate)
        k = np.searchsorted(ts, base["ent"][ix], side="right") - 1
        kk = np.maximum(k, 0)
        cov[ix] = (k >= 0) & (base["ent"][ix] - ts[kk] <= S.FUND_AGE_MS)
    return fund, cov


def gate_g4(base, fund):
    """G4: на копиях с суток ≥ 243 (с 01.09: в деревьях есть ставки) сумма post hoc против колонки `funding` движка."""
    m = base["day"] >= FUND_STAGE_DAY
    a, b = float(fund[m].sum()), float(base["fund"][m].sum())
    n_eng = int((base["fund"][m] != 0).sum())
    n_ph = int((fund[m] != 0).sum())
    print("G4: копий с 01.09 %d; с фандингом: post hoc %d, движок %d; Σ post hoc %+.3f $ (на копию 500 $), Σ движка %+.3f $, разность %+.3f $" % (int(m.sum()), n_ph, n_eng, a, b, a - b))
    return dict(n=int(m.sum()), n_ph=n_ph, n_eng=n_eng, ph=a, eng=b)


# ───────────────────────────── расчёт ─────────────────────────────

def build_series(base, v2, v3, fund):
    """Копии → ряды (P&L[ND], копий[ND]) по сценариям: base, S1, S3a, S3b, S3e1, S3e2, S6."""
    val = base["gross"] - base["fees"]
    ser = {"base": daily(base["day"], val), "S1": daily(base["day"], base["gross"] - 2 * base["fees"]),
           "S3a": daily(v2["day"], v2["gross"] - v2["fees"]), "S3b": daily(v3["day"], v3["gross"] - v3["fees"]),
           "S3e1": daily(base["day"], val - 1 * STAKE * 1e-4), "S3e2": daily(base["day"], val - 2 * STAKE * 1e-4),
           "S6": daily(base["day"], val + fund)}
    return ser


def win_slice(x, w):
    return np.asarray(x)[w[0]:w[1]]


def run(a):
    os.makedirs(ROOT, exist_ok=True)
    base = load_all(MEMBERS, BASE_RUNS)
    print("база: копий %d (H1 Feb–Jun + SEL + N1)" % len(base["day"]))
    ok = gate_g1(base)
    ok &= gate_g2()
    ok &= gate_g3()
    v2 = load_all([S2 + k for k in range(9)], VAR_RUNS)
    v3 = load_all([S3 + k for k in range(9)], VAR_RUNS)
    fund, cov = funding_posthoc(base)
    g4 = gate_g4(base, fund)
    if not ok:
        raise SystemExit("СТОП: ворота G1–G3 красные, стресс-ряды не читаются")
    ser = build_series(base, v2, v3, fund)
    conn = sqlite3.connect(DB, timeout=120)
    conn.executescript(TABLES)
    conn.execute("DELETE FROM tk721_row"), conn.execute("DELETE FROM tk721_month"), conn.execute("DELETE FROM tk721_day")
    L = []
    P = lambda *x: (L.append(" ".join(str(i) for i in x)), print(*x))   # noqa: E731
    wins = {"SEL": SEL, "Feb–Nov": FEBNOV}
    rows = {}
    for nm, (p, n) in ser.items():
        for d in range(ND):
            conn.execute("INSERT INTO tk721_day VALUES(?,?,?,?)", (nm, D_LO + d, float(p[d]), int(n[d])))
        for wn, w in wins.items():
            s = stats(win_slice(p, w), win_slice(n, w))
            rows[nm, wn] = s
            for k, v in s.items():
                conn.execute("INSERT OR REPLACE INTO tk721_row VALUES(?,?,?,?,?,?)", ("T0", nm, wn, k, float(v), ""))
    # бутстреп: Σ по окну, парные разности против базы
    boot = {}
    for wn, w in wins.items():
        idx = boot_idx(w[1] - w[0])
        base_p = win_slice(ser["base"][0], w)
        for nm, (p, n) in ser.items():
            x = win_slice(p, w)
            sums = boot_sum(x, idx)
            dl = boot_sum(x - base_p, idx)
            boot[nm, wn] = dict(lo=float(np.percentile(sums, 2.5)), hi=float(np.percentile(sums, 97.5)), p=p_le0(sums), dlo=float(np.percentile(dl, 2.5)), dhi=float(np.percentile(dl, 97.5)), d=float(x.sum() - base_p.sum()))
            for k, v in boot[nm, wn].items():
                conn.execute("INSERT OR REPLACE INTO tk721_row VALUES(?,?,?,?,?,?)", ("T0", nm, wn, "boot_" + k, float(v), ""))
    # S4: месяцы
    mon = {}
    for nm in ("base", "S1", "S3a"):
        p, n = ser[nm]
        for name, a0, b0 in month_bounds():
            x, c = p[a0 - D_LO:b0 - D_LO], n[a0 - D_LO:b0 - D_LO]
            s = stats(x, c)
            lomo = float(p.sum() - x.sum())
            mon[nm, name] = dict(s, lomo=lomo, few=int(s["copies"] < MIN_COPIES_MONTH))
            conn.execute("INSERT OR REPLACE INTO tk721_month VALUES(?,?,?,?,?,?,?,?,?,?,?)", (nm, name, len(x), s["sum"], s["copies"], s["per_copy"], s["sharpe"] if not math.isnan(s["sharpe"]) else None, s["dd"], s["ddp"], int(s["copies"] < MIN_COPIES_MONTH), lomo))
    npos = sum(1 for name, _, _ in month_bounds() if mon["base", name]["copies"] > 0 and mon["base", name]["sum"] > 0)
    p_s4 = binom_p(npos, 10)
    # S5: половины монет
    coins = sorted(set(open(C539, encoding="utf-8").read().replace(",", " ").split()) | set(base["sym"].tolist()))
    ha, hb = split_coins(coins)
    val = base["gross"] - base["fees"]
    half = {}
    for hn, hs in (("A", ha), ("B", hb)):
        m = np.array([s in hs for s in base["sym"]])
        p, n = daily(base["day"][m], val[m])
        p1, _ = daily(base["day"][m], (base["gross"] - 2 * base["fees"])[m])
        half[hn] = dict(all=stats(p, n), sel=stats(win_slice(p, SEL), win_slice(n, SEL)), s1=stats(p1, n), p=p_le0(boot_sum(p, boot_idx(ND))), coins=len(hs), p_arr=p)
    psplit = max(half["A"]["p"], half["B"]["p"])
    both_pos = half["A"]["all"]["sum"] > 0 and half["B"]["all"]["sum"] > 0
    per_coin = {}
    for s_, v_ in zip(base["sym"], val):
        per_coin[s_] = per_coin.get(s_, 0.0) + v_ / NMEMB
    pc = np.array([per_coin.get(c, 0.0) for c in coins])
    ok_rand = 0
    for i in range(1000):
        pm = np.random.default_rng(7210 + i).permutation(len(coins))
        h = len(coins) // 2
        ok_rand += int(pc[pm[:h]].sum() > 0 and pc[pm[h:]].sum() > 0)
    # вердикт
    sel = {nm: rows[nm, "SEL"]["sum"] for nm in ser}
    v1, v3 = sel["S1"] > 0, sel["S3a"] > 0
    v4, v5 = npos >= 7, both_pos
    verdict = "устойчива" if (v1 and v3 and v4 and v5) else "хрупкая"
    # Холм m = 6: S1, S3a, S3b (p по Σ_SEL), S4 (биномиальный), S5 (max p по половинам), S6 (p по Σ_SEL)
    ps = [boot["S1", "SEL"]["p"], boot["S3a", "SEL"]["p"], boot["S3b", "SEL"]["p"], p_s4, psplit, boot["S6", "SEL"]["p"]]
    adj = holm(ps)
    names = ["S1", "S3a", "S3b", "S4", "S5", "S6"]
    sig13 = adj[0] < ALPHA and adj[1] < ALPHA
    label = ("устойчива (значимо)" if sig13 else "устойчива (по знаку)") if verdict == "устойчива" else "хрупкая"
    for nm_, p_, a_ in zip(names, ps, adj):
        conn.execute("INSERT OR REPLACE INTO tk721_row VALUES(?,?,?,?,?,?)", ("HOLM", nm_, "SEL", "p", float(p_), ""))
        conn.execute("INSERT OR REPLACE INTO tk721_row VALUES(?,?,?,?,?,?)", ("HOLM", nm_, "SEL", "p_adj", float(a_), ""))
    # точка поломки
    msel = (base["day"] >= SEL_LO) & (base["day"] < SEL_HI)
    fees_sel = float(base["fees"][msel].sum()) / NMEMB
    m_star = 1.0 + sel["base"] / fees_sel if fees_sel > 0 else float("nan")
    pts = [(0, sel["base"]), (1, sel["S3a"]), (2, sel["S3b"])]
    slope = np.polyfit([x for x, _ in pts], [y for _, y in pts], 1)
    slip_star = -slope[1] / slope[0] if slope[0] < 0 else float("inf")
    e_star = sel["base"] / (STAKE * 1e-4 * rows["base", "SEL"]["copies"] / NMEMB) if rows["base", "SEL"]["copies"] else float("nan")
    for k, v in dict(m_star=m_star, slip_star_bp=slip_star, entry_only_star_bp=e_star, fees_sel=fees_sel, npos=npos, p_s4=p_s4, p_split=psplit, ok_rand=ok_rand, v1=v1, v3=v3, v4=v4, v5=v5).items():
        conn.execute("INSERT OR REPLACE INTO tk721_row VALUES(?,?,?,?,?,?)", ("VERDICT", "-", "-", k, float(v), ""))
    conn.execute("INSERT OR REPLACE INTO tk721_row VALUES(?,?,?,?,?,?)", ("VERDICT", "-", "-", "label", 0.0, label))
    conn.commit()
    # отчёт
    f = lambda x, d=1: ("%." + str(d) + "f") % x   # noqa: E731
    sg = lambda x: ("%+.2f" % x)                    # noqa: E731
    P("## Строки (база и стресс), SEL (01.07–13.11, 136 сут.) и Feb–Nov (01.02–24.11, 297 сут.)")
    P("")
    P("| ряд | окно | Σ $ | копий | $/копию (500 $) | $/копию (вес B) | $/сут | Шарп | просадка $ (% маржи) | перехай: долгий / средний / хвост / p90 | под водой | Δ Σ против B [интервал] | Σ [интервал] |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    label_of = {"base": "B (база)", "S1": "S1 комиссии ×2", "S3a": "S3a slip +1 бп", "S3b": "S3b slip +2 бп", "S3e1": "S3e +1 бп только вход (аналит.)", "S3e2": "S3e +2 бп только вход (аналит.)", "S6": "S6 + фандинг (верхняя оценка, в пользу шорта)"}
    for wn in wins:
        for nm in ser:
            s, b = rows[nm, wn], boot[nm, wn]
            P("| %s | %s | %s | %d | %s | %s | %s | %s | %s (%s %%) | %d / %s / %d / %s | %s %% | %s [%s; %s] | [%s; %s] |" % (
                label_of[nm], wn, sg(s["sum"]), s["copies"], f(s["per_copy"], 3), f(s["per_copy_w"], 4), f(s["per_day"], 3), f(s["sharpe"], 2), f(s["dd"]), f(s["ddp"]), s["gap_longest"], f(s["gap_mean"]) if not math.isnan(s["gap_mean"]) else "—", s["gap_tail"], f(s["gap_p90"], 1),
                f(100 * s["under"], 0), sg(b["d"]), sg(b["dlo"]), sg(b["dhi"]), sg(b["lo"]), sg(b["hi"])))
    P("")
    P("## S4 — месяцы (база; Σ без месяца — LOMO; в скобках S1 и S3a)")
    P("")
    P("| месяц | суток | Σ $ | копий | $/копию (500 $) | Шарп | просадка $ (% маржи) | Σ без месяца (LOMO) | S1 Σ | S3a Σ | метка |")
    P("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, a0, b0 in month_bounds():
        s = mon["base", name]
        P("| %s | %d | %s | %d | %s | %s | %s (%s %%) | %s | %s | %s | %s |" % (name, b0 - a0, sg(s["sum"]), s["copies"], f(s["per_copy"], 3), f(s["sharpe"], 2), f(s["dd"]), f(s["ddp"]), sg(s["lomo"]), sg(mon["S1", name]["sum"]), sg(mon["S3a", name]["sum"]), "мало сделок" if s["few"] else ""))
    P("")
    P("Плюсовых месяцев в базе: **%d из 10** (V4: ≥ 7; биномиальный p при ½ = %.3f, справочно — месяцы как независимые)." % (npos, p_s4))
    P("")
    P("## S5 — половины монет (всего монет %d; зерно 721; Feb–Nov, база)" % len(coins))
    P("")
    P("| половина | монет | Σ $ (Feb–Nov) | копий | $/копию (500 $) | Шарп | просадка $ (% маржи) | перехай: долгий / средний / хвост / p90 | под водой | Σ SEL | Σ Feb–Nov под S1 | p (Σ ≤ 0) |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for hn in ("A", "B"):
        h = half[hn]
        s = h["all"]
        P("| %s | %d | %s | %d | %s | %s | %s (%s %%) | %d / %s / %d / %s | %s %% | %s | %s | %.4f |" % (hn, h["coins"], sg(s["sum"]), s["copies"], f(s["per_copy"], 3), f(s["sharpe"], 2), f(s["dd"]), f(s["ddp"]), s["gap_longest"], f(s["gap_mean"]) if not math.isnan(s["gap_mean"]) else "—", s["gap_tail"], f(s["gap_p90"], 1), f(100 * s["under"], 0), sg(h["sel"]["sum"]), sg(h["s1"]["sum"]), h["p"]))
    P("")
    P("Справочно: из 1 000 случайных разбиений монет (зёрна 7210…8209) с плюсом в обеих половинах (Feb–Nov, база) — %d (%.1f %%)." % (ok_rand, ok_rand / 10.0))
    P("")
    P("## S6 — фандинг (post hoc, **верхняя оценка в пользу шорта**: полный номинал 500 $, mark/e = 1; в улучшение B не засчитывается)")
    P("")
    mf = base["day"] >= D_LO
    P("Копий с фандингом: %d из %d (покрыто ставками: %.1f %%); Σ фандинга на копию (500 $): %+.2f $; в весе B: %+.2f $ за Feb–Nov, %+.2f $ за SEL." % (
        int((fund[mf] != 0).sum()), int(mf.sum()), 100.0 * cov[mf].mean(), float(fund[mf].sum()), float(fund[mf].sum()) / NMEMB, rows["S6", "SEL"]["sum"] - rows["base", "SEL"]["sum"]))
    P("Ворота G4 (с 01.09): копий %d; с фандингом post hoc %d / движок %d; Σ post hoc %+.3f $ против движка %+.3f $ (разность %+.3f $)." % (g4["n"], g4["n_ph"], g4["n_eng"], g4["ph"], g4["eng"], g4["ph"] - g4["eng"]))
    P("")
    P("## Холм (m = 6) и вердикт")
    P("")
    P("| испытание | p | p по Холму |")
    P("|---|---|---|")
    for nm_, p_, a_ in zip(names, ps, adj):
        P("| %s | %.4f | %.4f |" % (nm_, p_, a_))
    P("")
    P("V1 Σ_SEL(S1) %s %s; V3 Σ_SEL(S3a) %s %s; V4 плюсовых месяцев %d из 10 (%s); V5 обе половины монет Σ > 0: %s (A %s, B %s)." % (sg(sel["S1"]), "> 0" if v1 else "≤ 0", sg(sel["S3a"]), "> 0" if v3 else "≤ 0", npos, "≥ 7" if v4 else "< 7", v5, sg(half["A"]["all"]["sum"]), sg(half["B"]["all"]["sum"])))
    P("**Вердикт по правилу PS-1043 §3 (с PS-1044): B «%s».**" % label)
    P("")
    P("Точка поломки: безубыточный множитель комиссий m* = %.2f× (точно, линейно; тариф п. 12); проскальзывание (ключ `slip`, обе тейкерские ноги): Σ_SEL при +0 / +1 / +2 бп = %s / %s / %s $, безубыточное +%.1f бп (линейная экстраполяция по трём точкам — измерено только в точках); только вход (аналит.): Σ_SEL(+1) %s, Σ_SEL(+2) %s, безубыточное +%.1f бп." % (
        m_star, sg(sel["base"]), sg(sel["S3a"]), sg(sel["S3b"]), slip_star, sg(sel["S3e1"]), sg(sel["S3e2"]), e_star))
    txt = "\n".join(L) + "\n"
    if a.report:
        with open(a.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(txt)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("gate")
    r = sp.add_parser("run")
    r.add_argument("--report", default=ROOT + "/report-tk721.md")
    a = ap.parse_args(argv)
    if a.cmd == "gate":
        base = load_all(MEMBERS, BASE_RUNS)
        ok = gate_g1(base) & gate_g2() & gate_g3()
        fund, _cov = funding_posthoc(base)
        gate_g4(base, fund)
        return 0 if ok else 3
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
