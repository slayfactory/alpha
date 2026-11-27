#!/usr/bin/env python3
"""TK-726, П-42 этап A, шаг 2 (PS-1055 + PS-1060 пп. 4, 12, 17): разложение результата по видам выхода. Только numpy / scipy; движок не запускается.

  python3 tk726_exits.py natr-check --dump <sigsx.csv> --cache <каталог cache дерева> [--n 100]   # ДО чтения результатов: NATR 30 на закрытой минуте перед сигналом против `natr_e8` дампа движка
  python3 tk726_exits.py run [--report exits-report.txt] [--no-exc]                                  # таблицы 1–6, тесты (i)–(iii), правило «хвост / ядро» → таблица `tk726_exits` в БД

Решают 9 членов B (якорь s4 = BS-l7-tf60 — один из них; ключ без `l_days` / `tf`); S1 / S2 / L401 — описательно (их ход на SEL завышен отбором: «найдено на этих данных»); N1 (14–24.11) и январь — описательно, из выводов сняты (1г, TK-726).
10 периодов решения = февраль … октябрь + 01–13.11 (по суткам входа). Зерно 20261127 (PS-1053 п. 11).

Трактовки, записанные ДО чтения результатов шага 2 (`chron.py add --kind amend`, L-номер в отчёте):
  • P&L сделки — gross − fees (как в шаге 1); месячный «$ на сделку» — Σ $ периода / число ВСЕХ сделок периода; «вклад stop» — Σ $ сделок `stop` / число всех сделок периода; «без stop» = полный − вклад stop (аддитивно).
  • Доля хвоста = cov(вклад stop, полный) / var(полный) по 10 периодам (ddof = 1); условие 2: σ(без stop) ≤ ½ σ(полный). «Хвост» = доля ≥ 0,5 и условие 2.
  • (i) внутри месяца: статистика — среднее по периодам отношения var / mean суточного числа стопов (сутки без сделок входят нулями); нуль — 10 000 перестановок метки `stop` между сделками внутри периода; p — односторонний (пачки = больше). Между месяцами — χ² однородности доли стопов (df = 9). p исхода (i) = меньшее из двух p после Холма по 2 тестам.
  • Исход по клетке: «хвост» и p(i) ≥ 0,05 → «хвост без режима» (шаг 7, шага 9 нет); «хвост» и p(i) < 0,05 → «хвост с пачками» (шаг 9 включается); иначе «ядро» (Г2, шаг 5). Исход шага — «хвост» только если ВСЕ 9 клеток B — «хвост» (одно семейство; протокол: «ядро — иначе»); иначе «ядро», с раскладкой по клеткам.
  • Терцили V суток: сутки ранжируются по среднему V сделок суток (V = NATR 30 на закрытой минуте перед сигналом); (i) повторяется внутри терциля среди суток со сделками (описательно).
  • Интервал доли хвоста — бутстреп суток блоками внутри периодов (блок ⌈n^{1/3}⌉ суток от 286 и 21; блок не длиннее периода), 20 000 повторов.
  • Ходы против / в пользу позиции — по минутам от `entry_min` до `exit_min` включительно (кэш `.b42` дерева окна), в единицах V; свеча входа / выхода целиком — завышение на долю минуты (оговорка отчёта).
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import math
import os
import sqlite3
import sys
from collections import OrderedDict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk726_seam as sm  # noqa: E402

SEED = 20261127
NBOOT = 20000
NPERM = 10000
SCALE = 100_000_000
D = datetime.date.fromisoformat
B_NAMES = ("BS-l14-tf240", "BS-l14-tf30", "BS-l14-tf60", "BS-l3-tf240", "BS-l3-tf30", "BS-l3-tf60", "BS-l7-tf240", "BS-l7-tf30", "BS-l7-tf60")
LINES = ("S1", "S2", "L401")
KINDS = ("stop", "be", "trail", "forced", "tx", "rt", "nt", "tu", "takes")
TAIL_USD = -10.0
# периоды (имя, первые, последние сутки); решают 10 после «Jan», кроме «N1»
PERIODS = (("Jan", "2026-01-01", "2026-01-31"), ("Feb", "2026-02-01", "2026-02-28"), ("Mar", "2026-03-01", "2026-03-31"), ("Apr", "2026-04-01", "2026-04-30"),
           ("May", "2026-05-01", "2026-05-31"), ("Jun", "2026-06-01", "2026-06-30"), ("Jul", "2026-07-01", "2026-07-31"), ("Aug", "2026-08-01", "2026-08-31"),
           ("Sep", "2026-09-01", "2026-09-30"), ("Oct", "2026-10-01", "2026-10-31"), ("Nov1-13", "2026-11-01", "2026-11-13"), ("N1", "2026-11-14", "2026-11-24"))
DEC = tuple(p[0] for p in PERIODS[1:11])
# корни деревьев окон (кэш минуток `.b42`): как в tk700_win / tk726_seam
TREES = {"m01": "/data/p42-tk695/jan", "m02": "/data/p42-tk695/feb", "m03": "/data/p42-tk695/mar", "m04": "/data/p42-tk695/apr", "m05": "/data/p42-tk695/may", "m06": "/data/p42-tk686/wj",
         "wa": "/data/p42-tk621/wa", "wb": "/data/p42-tk621/wb", "wc": "/data/p42-tk621/wc", "n1": "/data/p42-tk700/n1"}
LONG_CELL = 146454                                   # длинная клетка m01…m06 в `trades/tk718c-h1-B/` (описательно, без правила)
LONG_ROOT = "/data/p42-tk718/trades/tk718c-h1-B"


def pday(iso):
    return sm.day_of(iso)


def period_index(eday):
    """Номер периода (в PERIODS) по суткам входа; −1 — вне периодов."""
    out = np.full(len(eday), -1, np.int64)
    for i, (_, lo, hi) in enumerate(PERIODS):
        out[(eday >= pday(lo)) & (eday <= pday(hi))] = i
    return out


# ───────────────────────────── NATR (как в движке) ─────────────────────────────

def natr_at(o, h, l, c, ts, p=30):
    """NATR 1м/p в e8 в минутах `ts` — как `sigfeat_lib.natr_series` / `natr_series` движка (целые Python для деления): TR от закрытия предыдущей минуты с данными, среднее по окну p минут, k·2 ≥ p; −1 — нет значения."""
    o, h, l, c = (np.asarray(x, np.int64) for x in (o, h, l, c))
    n = len(o)
    valid = o != 0
    vi = np.nonzero(valid)[0]
    tr = np.zeros(n, np.int64)
    tr[vi] = (h - l)[vi]
    if len(vi) > 1:
        pc = c[vi[:-1]]
        hv, lv = h[vi[1:]], l[vi[1:]]
        tr[vi[1:]] = np.maximum(np.maximum(hv - lv, np.abs(hv - pc)), np.abs(lv - pc))
    ptr = np.concatenate([[0], np.cumsum(tr)])
    pcnt = np.concatenate([[0], np.cumsum(valid.astype(np.int64))])
    out = []
    for t in np.asarray(ts, np.int64):
        t = int(t)
        if t < p - 1 or t >= n or o[t] == 0:
            out.append(-1)
            continue
        k = int(pcnt[t + 1] - pcnt[t + 1 - p])
        if k * 2 < p:
            out.append(-1)
            continue
        s = int(ptr[t + 1] - ptr[t + 1 - p])
        out.append(min((s * SCALE) // (k * int(c[t])), 2 ** 31 - 1))
    return np.array(out, np.int64)


def load_b42(path):
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] != b"B42\x01":
        raise ValueError("%s: не .b42" % path)
    n = int.from_bytes(head[4:8], "little")
    a = np.fromfile(path, dtype="<i8", offset=8, count=5 * n).reshape(5, n)
    return a[0], a[1], a[2], a[3]


def cmd_natr_check(a):
    rows = []
    with open(a.dump, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            rows.append((r[0], int(r[2]), int(r[8])))
    rng = np.random.default_rng(SEED)
    pick = rng.choice(len(rows), size=min(a.n, len(rows)), replace=False)
    ok = tot = miss = 0
    cache = {}
    bad = []
    for i in sorted(pick):
        sym, sig_ms, nat = rows[i]
        fn = os.path.join(a.cache, sym + ".b42")
        if not os.path.exists(fn):
            miss += 1
            continue
        if sym not in cache:
            cache[sym] = load_b42(fn)
        o, h, l, c = cache[sym]
        t = sig_ms // 60_000
        v = int(natr_at(o, h, l, c, [t - 1])[0])
        tot += 1
        if v == nat:
            ok += 1
        elif len(bad) < 5:
            bad.append((sym, sig_ms, nat, v))
    print("NATR-check: %d из %d моментов равны natr_e8 дампа (нет файла: %d); расхождения: %s" % (ok, tot, miss, bad))
    return 0 if ok == tot and tot > 0 else 1


# ───────────────────────────── сборка сделок ─────────────────────────────

def assemble(cid, long_side=False):
    """Все сделки клетки по окнам (в границах зачётных суток окна); массивы + `win`, `pidx`."""
    parts = []
    for w, (lo, hi, root) in sm.WINDOWS.items():
        if long_side:
            if w not in ("m01", "m02", "m03", "m04", "m05", "m06"):
                continue
            root = LONG_ROOT
        t0 = json.load(open(os.path.join(root, "t0.json"), encoding="utf-8"))[w]
        fn = os.path.join(root, w, "%d-trades.csv" % cid)
        if not os.path.exists(fn):
            continue
        a = sm.read_trades(fn, t0)
        m = sm.in_days(a, sm.day_of(lo), sm.day_of(hi))
        a = sm.take(a, m)
        with open(fn, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        keep = np.nonzero(m)[0]
        a["sig_min"] = np.array([int(rows[i]["sig_min"]) for i in keep], np.int64)
        a["entry_min"] = np.array([int(rows[i]["entry_min"]) for i in keep], np.int64)
        a["exit_min"] = np.array([int(rows[i]["exit_min"]) for i in keep], np.int64)
        a["win"] = np.array([w] * len(keep), dtype=object)
        parts.append(a)
    out = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    out["pidx"] = period_index(out["eday"])
    return out


def add_excursions(cells):
    """V, ход против (mae) и в пользу (mfe) позиции в единицах V по минутам дерева окна. cells: {имя: сделки}. Шорт: против = вверх."""
    stats = {"no_file": 0, "no_v": 0, "done": 0}
    for a in cells.values():
        n = len(a["pnl"])
        a["v"], a["mae"], a["mfe"] = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    for w, root in TREES.items():
        group = {}
        for name, a in cells.items():
            for i in np.nonzero(a["win"] == w)[0]:
                group.setdefault(a["sym"][i], []).append((name, i))
        for sym, items in group.items():
            fn = os.path.join(root, "cache", sym + ".b42")
            if not os.path.exists(fn):
                stats["no_file"] += len(items)
                continue
            o, h, l, c = load_b42(fn)
            for name, i in items:
                a = cells[name]
                v = int(natr_at(o, h, l, c, [int(a["sig_min"][i]) - 1])[0])
                if v <= 0:
                    stats["no_v"] += 1
                    continue
                em, xm = int(a["entry_min"][i]), int(a["exit_min"][i])
                sel = o[em:xm + 1] != 0
                if not sel.any():
                    continue
                px = int(a["entry_px_e8"][i])
                hv, lv = h[em:xm + 1][sel], l[em:xm + 1][sel]
                vf = v / SCALE
                a["v"][i] = vf
                a["mae"][i] = (int(hv.max()) - px) / px / vf
                a["mfe"][i] = (px - int(lv.min())) / px / vf
                stats["done"] += 1
    return stats


# ───────────────────────────── таблицы ─────────────────────────────

def fmt(x, d=2):
    return "—" if x is None or x != x else "%.*f" % (d, x)


def exit_rows(a):
    """По периоду × виду выхода: число, доля, Σ $, среднее $, среднее время в сделке (мин)."""
    rows = []
    for pi, (pname, _, _) in enumerate(PERIODS):
        m = a["pidx"] == pi
        n = int(m.sum())
        for k in sorted(set(a["kind"][m].tolist())):
            mk = m & (a["kind"] == k)
            nk = int(mk.sum())
            tmin = float(((a["exit_ms"][mk] - a["entry_ms"][mk]) / 60000.0).mean())
            rows.append((pname, k, nk, nk / n if n else float("nan"), float(a["pnl"][mk].sum()), float(a["pnl"][mk].mean()), tmin))
    return rows


def period_metrics(a, pi):
    """Метрики периода: пп. 2–5 протокола."""
    m = a["pidx"] == pi
    p = a["pnl"][m]
    n = len(p)
    if n == 0:
        return {}
    kind = a["kind"][m]
    pos, neg = p[p > 0], p[p < 0]
    srt = np.sort(p)
    k1 = int(math.ceil(0.01 * n))
    stop = kind == "stop"
    gross = a["gross"][m]
    return dict(n=n, sum=float(p.sum()), mean=float(p.mean()), n_tail=int((p <= TAIL_USD).sum()), sum_tail=float(p[p <= TAIL_USD].sum()),
                sum_nostop=float(p[~stop].sum()), sum_no_worst1=float(srt[k1:].sum()), avg_plus=float(pos.mean()) if len(pos) else float("nan"),
                avg_minus=float(neg.mean()) if len(neg) else float("nan"), share_pos=float((p > 0).mean()),
                rr=float(pos.mean() / abs(neg.mean())) if len(pos) and len(neg) else float("nan"), loss_stop=float(p[stop].mean()) if stop.any() else float("nan"),
                fee=float(a["fees"][m].mean()), fee_share=float(a["fees"][m].mean() / gross.mean()) if gross.mean() > 0 else float("nan"))


def tail_stats(S, F, N):
    """S, F, N — по периодам: Σ $ сделок stop, Σ $ всех, число сделок. Доля хвоста и условие 2 (PS-1060 п. 4, 17)."""
    S, F, N = (np.asarray(x, np.float64) for x in (S, F, N))
    s, f = S / N, F / N
    var_f = float(f.var(ddof=1))
    share = float(np.cov(s, f, ddof=1)[0, 1] / var_f) if var_f > 0 else float("nan")
    sd_f, sd_ns = float(f.std(ddof=1)), float((f - s).std(ddof=1))
    return dict(share=share, sd_full=sd_f, sd_nostop=sd_ns, cond2=bool(sd_ns <= 0.5 * sd_f), tail=bool(share >= 0.5 and sd_ns <= 0.5 * sd_f))


def day_arrays_by_period(a, pidxs):
    """По периодам: суточные Σ $ stop, Σ $ всех, число сделок (сутки периода целиком, включая пустые)."""
    out = []
    for pi in pidxs:
        _, lo, hi = PERIODS[pi]
        lo, hi = pday(lo), pday(hi)
        m = (a["pidx"] == pi)
        d = (a["eday"][m] - lo).astype(np.int64)
        nd = hi - lo + 1
        st = a["kind"][m] == "stop"
        out.append((np.bincount(d, weights=a["pnl"][m] * st, minlength=nd), np.bincount(d, weights=a["pnl"][m], minlength=nd), np.bincount(d, minlength=nd).astype(np.float64)))
    return out


def tail_boot(days, block, nboot, rng):
    """Бутстреп суток блоками внутри периодов: интервал доли хвоста и доля повторов с условием 2."""
    S = np.zeros((nboot, len(days)))
    F = np.zeros_like(S)
    N = np.zeros_like(S)
    for j, (ds, df, dn) in enumerate(days):
        m = len(ds)
        L = max(1, min(block, m))
        nb = math.ceil(m / L)
        st = rng.integers(0, m, size=(nboot, nb))
        idx = ((st[:, :, None] + np.arange(L)[None, None, :]) % m).reshape(nboot, nb * L)[:, :m]
        S[:, j], F[:, j], N[:, j] = ds[idx].sum(axis=1), df[idx].sum(axis=1), dn[idx].sum(axis=1)
    N = np.maximum(N, 1)
    s, f = S / N, F / N
    sc, fc = s - s.mean(axis=1, keepdims=True), f - f.mean(axis=1, keepdims=True)
    var_f = (fc ** 2).sum(axis=1) / (len(days) - 1)
    cov = (sc * fc).sum(axis=1) / (len(days) - 1)
    share = np.where(var_f > 0, cov / np.where(var_f > 0, var_f, 1), np.nan)
    sd_f = np.sqrt(var_f)
    sd_ns = np.sqrt((((f - s) - (f - s).mean(axis=1, keepdims=True)) ** 2).sum(axis=1) / (len(days) - 1))
    ok = ~np.isnan(share)
    lo, hi = np.percentile(share[ok], [2.5, 97.5]) if ok.any() else (float("nan"), float("nan"))
    return float(lo), float(hi), float((sd_ns[ok] <= 0.5 * sd_f[ok]).mean()) if ok.any() else float("nan")


def dispersion(counts):
    m = counts.mean()
    return float(counts.var(ddof=1) / m) if m > 0 and len(counts) > 1 else float("nan")


def stop_perm_test(per, nperm, rng):
    """per: [(индекс суток каждой сделки (0…nd−1), метка stop (bool), nd)] по периодам. T = среднее по периодам var/mean суточного числа стопов.
    Нуль — перестановки метки внутри периода. p — односторонний (пачки: T больше). Возвращает (T, p, средняя T нуля)."""
    obs, nul = [], []
    for di, st, nd in per:
        cnt = np.bincount(di, weights=st.astype(np.float64), minlength=nd)
        obs.append(dispersion(cnt))
        lab = np.broadcast_to(st.astype(np.float64), (nperm, len(st)))
        sh = rng.permuted(lab, axis=1)
        onehot = np.zeros((len(st), nd))
        onehot[np.arange(len(st)), di] = 1.0
        cn = sh @ onehot
        mean = cn.mean(axis=1)
        nul.append(np.where(mean > 0, cn.var(axis=1, ddof=1) / np.where(mean > 0, mean, 1), np.nan))
    T = float(np.nanmean(obs))
    Tn = np.nanmean(np.vstack(nul), axis=0)
    p = float((1 + (Tn >= T - 1e-12).sum()) / (nperm + 1))
    return T, p, float(np.nanmean(Tn))


def chi2_homog(stops, totals):
    """χ² однородности доли стопов по периодам (2 × k); df = k − 1."""
    from scipy.stats import chi2
    st, tot = np.asarray(stops, np.float64), np.asarray(totals, np.float64)
    p0 = st.sum() / tot.sum()
    e1, e0 = tot * p0, tot * (1 - p0)
    x2 = float((((st - e1) ** 2) / e1).sum() + (((tot - st) - e0) ** 2 / e0).sum())
    return x2, float(chi2.sf(x2, len(tot) - 1)), len(tot) - 1


def psr_stats(x):
    """Шарп суточный / годовой, γ₃, γ₄ (полный эксцесс), SE и 95 % интервал, PSR(SR > 0) — формула шага 2 (iii)."""
    from scipy.stats import norm
    x = np.asarray(x, np.float64)
    n = len(x)
    sd = x.std(ddof=1)
    sr = float(x.mean() / sd) if sd > 0 else float("nan")
    z = (x - x.mean()) / sd
    g3, g4 = float((z ** 3).mean()), float((z ** 4).mean())
    se = math.sqrt(max((1 - g3 * sr + (g4 - 1) / 4 * sr ** 2) / (n - 1), 0.0))
    return dict(sr_d=sr, sr_y=sr * math.sqrt(365), g3=g3, g4=g4, se=se, lo=sr - 1.96 * se, hi=sr + 1.96 * se, psr=float(norm.cdf(sr / se)) if se > 0 else float("nan"), n=n)


def terciles_by_day(a, pidxs):
    """Номер терциля (0–2) для каждых суток со сделками: ранг среднего V сделок суток. Возвращает dict[eday] → терциль."""
    m = np.isin(a["pidx"], pidxs) & ~np.isnan(a["v"])
    days = a["eday"][m]
    v = a["v"][m]
    ud = np.unique(days)
    mv = np.array([v[days == d].mean() for d in ud])
    cut = np.quantile(mv, [1 / 3, 2 / 3])
    return {int(d): int(np.searchsorted(cut, x, side="right")) for d, x in zip(ud, mv)}


def per_for_test(a, pidxs, day_filter=None):
    per = []
    for pi in pidxs:
        _, lo, hi = PERIODS[pi]
        lo, hi = pday(lo), pday(hi)
        m = a["pidx"] == pi
        d = a["eday"][m]
        st = a["kind"][m] == "stop"
        if day_filter is None:
            per.append(((d - lo).astype(np.int64), st, hi - lo + 1))
        else:
            keep = np.array([int(x) in day_filter for x in d], dtype=bool)
            days = sorted(day_filter & set(range(lo, hi + 1)))
            if not days or not keep.any():
                continue
            mp = {x: i for i, x in enumerate(days)}
            per.append((np.array([mp[int(x)] for x in d[keep]], np.int64), st[keep], len(days)))
    return per


# ───────────────────────────── прогон ─────────────────────────────

def cmd_run(a):
    out = sqlite3.connect(a.out, timeout=120)
    out.executescript("CREATE TABLE IF NOT EXISTS tk726_exits(item TEXT, cell TEXT, period TEXT, metric TEXT, value REAL, text TEXT);")
    out.execute("DELETE FROM tk726_exits")
    cid_of = {name: cid for cid, name in out.execute("SELECT cid, name FROM tk726_cell")}
    rows = []

    def put(item, cell, period, metric, value=None, text=""):
        rows.append((item, cell, period, metric, None if value is None else float(value), text))

    lines = []
    say = lines.append
    names = list(LINES) + list(B_NAMES)
    cells = OrderedDict((n, assemble(cid_of[n])) for n in names)
    long_cell = None
    try:
        long_cell = assemble(LONG_CELL, long_side=True)
    except Exception as e:  # noqa: BLE001 — длинная клетка описательно; нет файлов — пишем в отчёт
        say("лонг %d: не прочитан (%s)" % (LONG_CELL, e))
    if not a.no_exc:
        allc = OrderedDict(cells)
        if long_cell is not None:
            allc["long-%d" % LONG_CELL] = long_cell
        st = add_excursions(allc)
        say("ходы по минутам: %s" % st)
    dec_idx = [i for i, p in enumerate(PERIODS) if p[0] in DEC]
    rng = np.random.default_rng(SEED)
    verdicts = {}
    lo_d, hi_d = pday("2026-02-01"), pday("2026-11-13")
    block7 = max(1, math.ceil((hi_d - lo_d + 1) ** (1 / 3)))
    for name, ar in list(cells.items()) + ([("long-%d" % LONG_CELL, long_cell)] if long_cell is not None else []):
        is_b = name in B_NAMES
        role = "решает" if is_b else "описательно"
        say("=" * 20)
        say("клетка %s (%s); сделок %d" % (name, role, len(ar["pnl"])))
        say("таблица 1: период | вид выхода | сделок | доля | Σ $ | среднее $ | время в сделке, мин")
        for r in exit_rows(ar):
            say("  %s | %s | %d | %s | %s | %s | %s" % (r[0], r[1], r[2], fmt(r[3], 3), fmt(r[4]), fmt(r[5]), fmt(r[6], 0)))
            put("t1", name, r[0], "n_" + r[1], r[2]); put("t1", name, r[0], "sum_" + r[1], r[4]); put("t1", name, r[0], "mean_" + r[1], r[5])
        say("таблица 2–5: период | сделок | Σ $ | ≤−10 $: число / Σ | Σ без stop | Σ без худшего 1 % | ср. плюс | ср. минус | доля + | R:R | убыток на stop | издержки / сд. (доля края)")
        for pi, (pname, _, _) in enumerate(PERIODS):
            mt = period_metrics(ar, pi)
            if not mt:
                continue
            say("  %s | %d | %s | %d / %s | %s | %s | %s | %s | %s | %s | %s | %s (%s)" % (pname, mt["n"], fmt(mt["sum"]), mt["n_tail"], fmt(mt["sum_tail"]), fmt(mt["sum_nostop"]), fmt(mt["sum_no_worst1"]),
                  fmt(mt["avg_plus"]), fmt(mt["avg_minus"]), fmt(mt["share_pos"], 3), fmt(mt["rr"]), fmt(mt["loss_stop"]), fmt(mt["fee"], 3), fmt(mt["fee_share"], 3)))
            for k, v in mt.items():
                put("t25", name, pname, k, v)
        # ряд суток 01.02–13.11 (10 периодов + промежутки нет): Шарп, просадка, перехай, PSR
        p_all, n_all = sm.day_arrays(ar, lo_d, hi_d)
        import tk692_walk as W
        ss = W.series_stats(p_all, n_all)
        ps = psr_stats(p_all)
        say("ряд 01.02–13.11 (%d суток): Σ %s $, сделок %d, %s $/сд.; Шарп %s; просадка %s $ (%s %% маржи); перехай %d / %s / %d" % (
            ss["days"], fmt(ss["sum"]), ss["n"], fmt(ss["per_trade"], 3), fmt(ss["sharpe"]), fmt(ss["dd"]), fmt(ss["ddp"], 1), ss["gap_longest"], fmt(ss["gap_mean"], 1), ss["gap_tail"]))
        say("(iii) PSR: SR сут. %s, годовой %s, γ₃ %s, γ₄ %s, SE %s, интервал сут. [%s; %s], PSR(SR>0) %s" % (fmt(ps["sr_d"], 4), fmt(ps["sr_y"]), fmt(ps["g3"]), fmt(ps["g4"]), fmt(ps["se"], 4), fmt(ps["lo"], 4), fmt(ps["hi"], 4), fmt(ps["psr"], 4)))
        for k, v in ss.items():
            put("series", name, "Feb–Nov13", k, v)
        for k, v in ps.items():
            put("psr", name, "Feb–Nov13", k, v)
        # хвост: доля, условие 2, интервалы
        N = [int((ar["pidx"] == pi).sum()) for pi in dec_idx]
        S = [float(ar["pnl"][(ar["pidx"] == pi) & (ar["kind"] == "stop")].sum()) for pi in dec_idx]
        F = [float(ar["pnl"][ar["pidx"] == pi].sum()) for pi in dec_idx]
        ts = tail_stats(S, F, N)
        days = day_arrays_by_period(ar, dec_idx)
        b1 = tail_boot(days, block7, a.nboot, rng)
        b2 = tail_boot(days, 21, a.nboot, rng)
        say("(ii) доля хвоста %s (интервал блок %d [%s; %s], блок 21 [%s; %s]); σ без stop %s, σ полный %s → условие 2 %s (доля повторов с условием 2: %s / %s); хвост = %s" % (
            fmt(ts["share"], 3), block7, fmt(b1[0], 3), fmt(b1[1], 3), fmt(b2[0], 3), fmt(b2[1], 3), fmt(ts["sd_nostop"], 3), fmt(ts["sd_full"], 3),
            "да" if ts["cond2"] else "нет", fmt(b1[2], 2), fmt(b2[2], 2), "ДА" if ts["tail"] else "нет"))
        for k, v in dict(share=ts["share"], sd_full=ts["sd_full"], sd_nostop=ts["sd_nostop"], cond2=float(ts["cond2"]), tail=float(ts["tail"]), lo7=b1[0], hi7=b1[1], lo21=b2[0], hi21=b2[1], c2_7=b1[2], c2_21=b2[2]).items():
            put("tail", name, "Feb–Nov13", k, v)
        # (i): стопы случайны или пачками
        per = per_for_test(ar, dec_idx)
        T, p_in, Tnull = stop_perm_test(per, a.nperm, rng)
        stops_per = [int(((ar["pidx"] == pi) & (ar["kind"] == "stop")).sum()) for pi in dec_idx]
        x2, p_btw, df = chi2_homog(stops_per, N)
        adj = _holm([p_in, p_btw])
        p_i = min(adj)
        say("(i) в месяце: T %s против нуля %s, p %s; между месяцами: χ² %s (df %d), p %s; после Холма %s / %s; p(i) = %s" % (fmt(T, 3), fmt(Tnull, 3), fmt(p_in, 4), fmt(x2, 1), df, fmt(p_btw, 4), fmt(adj[0], 4), fmt(adj[1], 4), fmt(p_i, 4)))
        for k, v in dict(T=T, T_null=Tnull, p_in=p_in, chi2=x2, p_btw=p_btw, adj_in=adj[0], adj_btw=adj[1], p_i=p_i).items():
            put("stoptest", name, "Feb–Nov13", k, v)
        if not a.no_exc and not np.isnan(ar["v"]).all():
            tc = terciles_by_day(ar, dec_idx)
            tres = []
            for q in (0, 1, 2):
                dset = {d for d, t in tc.items() if t == q}
                perq = per_for_test(ar, dec_idx, dset)
                Tq, pq, Tn = stop_perm_test(perq, a.nperm, rng)
                tres.append("терциль V %d: T %s против %s, p %s (суток %d)" % (q + 1, fmt(Tq, 3), fmt(Tn, 3), fmt(pq, 4), len(dset)))
                put("stoptest_v", name, "tercile%d" % (q + 1), "p", pq); put("stoptest_v", name, "tercile%d" % (q + 1), "T", Tq)
            say("(i) внутри третей V (описательно): " + "; ".join(tres))
            # ход против / в пользу позиции
            m = np.isin(ar["pidx"], dec_idx) & ~np.isnan(ar["mae"])
            win, stp = m & (ar["pnl"] > 0), m & (ar["kind"] == "stop")
            q = lambda x: [float(np.quantile(x, z)) for z in (0.75, 0.9, 0.95, 0.99)] + [float(x.max())] if len(x) else [float("nan")] * 5   # noqa: E731
            say("ход против позиции, в V: все (n=%d) среднее %s, q75/q90/q95/q99/макс %s; выигрышные (n=%d) среднее %s, q75/q90/q95/q99/макс %s; stop (n=%d) среднее %s" % (
                m.sum(), fmt(ar["mae"][m].mean()), "/".join(fmt(x) for x in q(ar["mae"][m])), win.sum(), fmt(ar["mae"][win].mean()),
                "/".join(fmt(x) for x in q(ar["mae"][win])), stp.sum(), fmt(ar["mae"][stp].mean())))
            say("доля выигрышных с ходом против ≥ x·V: " + "; ".join("x=%s %s" % (x, fmt(float((ar["mae"][win] >= x).mean()), 3)) for x in (0.5, 1, 1.5, 2, 3, 5)) + "; ход в пользу у stop, в V: среднее %s" % fmt(ar["mfe"][stp].mean()))
            for k, v in dict(n=m.sum(), mae_mean=ar["mae"][m].mean(), mae_win_mean=ar["mae"][win].mean(), mae_stop_mean=ar["mae"][stp].mean(), mfe_stop_mean=ar["mfe"][stp].mean()).items():
                put("exc", name, "Feb–Nov13", k, v)
            for j, x in enumerate(q(ar["mae"][m])):
                put("exc", name, "Feb–Nov13", "mae_q_" + ("75", "90", "95", "99", "max")[j], x)
            for j, x in enumerate(q(ar["mae"][win])):
                put("exc", name, "Feb–Nov13", "mae_win_q_" + ("75", "90", "95", "99", "max")[j], x)
        if is_b:
            if ts["tail"]:
                res = "хвост без режима (шаг 7, шага 9 нет)" if p_i >= 0.05 else "хвост с пачками (шаг 9 включается)"
            else:
                res = "ядро (Г2, шаг 5)"
            verdicts[name] = (ts["tail"], res)
            put("verdict", name, "Feb–Nov13", "tail", float(ts["tail"]), res)
            say("ИСХОД клетки %s: %s" % (name, res))
        say("")
    n_tail = sum(1 for v in verdicts.values() if v[0])
    overall = "хвост" if n_tail == len(B_NAMES) else "ядро"
    say("ИТОГ шага 2 (решают 9 членов B): «хвост» у %d из %d; исход — %s" % (n_tail, len(B_NAMES), overall))
    put("verdict", "B9", "Feb–Nov13", "tail_cells", n_tail, overall)
    out.executemany("INSERT INTO tk726_exits VALUES(?,?,?,?,?,?)", rows)
    out.commit()
    text = "\n".join(lines)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(lines[-1])


def _holm(p):
    """Холм: `lib.holm` (PS-1053 п. 2); без `lib` (нет axes / selection_bias рядом) — та же формула."""
    try:
        import lib
        return lib.holm(list(p))
    except ImportError:
        order = sorted(range(len(p)), key=lambda i: p[i])
        adj, run = [0.0] * len(p), 0.0
        for rank, i in enumerate(order):
            run = max(run, min(1.0, (len(p) - rank) * p[i]))
            adj[i] = run
        return adj


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    n = sp.add_parser("natr-check")
    n.add_argument("--dump", required=True)
    n.add_argument("--cache", required=True)
    n.add_argument("--n", type=int, default=100)
    n.set_defaults(fn=cmd_natr_check)
    r = sp.add_parser("run")
    r.add_argument("--out", default=sm.OUT_DB)
    r.add_argument("--report", default="")
    r.add_argument("--no-exc", action="store_true")
    r.add_argument("--nboot", type=int, default=NBOOT)
    r.add_argument("--nperm", type=int, default=NPERM)
    r.set_defaults(fn=cmd_run)
    a = ap.parse_args(argv)
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
