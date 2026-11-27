#!/usr/bin/env python3
"""TK-726, П-42 этап A, шаг 6 (PS-1059 + PS-1060 пп. 9, 10(1), 11): условность карты влияния осей. Только чтение `metric_cur` в БД летописи (клетки c539, SEL 01.07–13.11); движок не запускается.

  python3 tk726_cond.py run [--db /data/chron/chron.sqlite] [--out /data/p42-tk726/tk726.sqlite] [--report cond-report.txt] [--sides short,long] [--nboot 20000]

Трактовки, записанные ДО чтения результатов шага 6 (`chron.py add --kind amend`):
  • Досчёт уровней 3–4 (6.1): не выполняется — прогон 2 `infl_run` (25.11 22:54, уровни 3–4, compute) позже самой новой клетки `metric` (25.11 09:42): новых клеток нет, пересчёт был бы тем же (В-213, только дельта).
  • Клетка уровня k — набор осей, отличных от якоря (`tk689_influence.assign_axes`, записи 334 — якорь); исключённые (`excluded`: стоп 5 %, ew = 0) и повторы ключа не входят.
  • Аддитивная модель: ŷ(S) = A + Σ_a e_a + Σ_{a<b} p_ab, e_a = y({a}) − A, p_ab = y({a,b}) − y({a}) − y({b}) + A; считается по суточным рядам (линейна: коэффициенты A: 1 − k + C(k,2), клетка {a}: 2 − k, пара: 1). Клетка уровня 3–4 предсказуема, только если в БД есть все её одиночные клетки и пары; выборка 20 000 клеток на уровень — случайная (зерно 20261127) из ВСЕХ клеток уровня; R² — на предсказуемых из выборки, доля предсказуемых печатается. Клетки уровней 3–4 в БД — отбор (над лучшими парами / тройками, TK-674), не случайные: R² относится к отобранной совокупности.
  • R² = 1 − Σ(y − ŷ)² / Σ(y − ȳ)²; потолок шума — квадрат корреляции Пирсона между Σ $ клетки на чётных и на нечётных сутках (R² лучшей линейной подгонки со свободным сдвигом; запись L-482: буквальный R² «чётные → нечётные» отрицателен из-за сдвига уровня половин — печатается рядом как `ceiling_lit`), R² модели на тех же половинах — буквальный (без подгонки); отношение = R²_модели(половины) / потолок. Правило: отношение < 0,5 → «условна»; интервалы — циркулярные блоки суток (⌈136^{1/3}⌉ = 6 и 21), 20 000 повторов; точка и граница по обоим блокам по разные стороны 0,5 → «не ясно».
  • ρ_ось: оси с ≥ 4 значениями на фоне; фон = якорь (клетки уровня 1) или одна другая активная ось (клетки уровня 2 с ключом {другая ось = значение}); эффект значения v на фоне B = y(B ∪ {a = v}) − y(B); Спирмен между эффектами двух фонов по общим значениям (≥ 4); ρ_ось = среднее по парам фонов (не более 12 фонов на ось, случайно); средняя ρ — по осям поровну. Нуль — перестановка значений эффекта внутри фона (2 000 повторов); решает: средняя ρ ≥ 0,5 и p нуля < 0,05; иначе «условна».
  • Итог: «карта условна», если отношение R² < 0,5 ИЛИ средняя ρ_ось < 0,5 (или не отличается от нуля); решает шорт (база под перестройку), лонг — описательно.
  • Стоп как фон (описательно): эффект (a = u) на фоне s ∈ {1; 1,5; 2} против фона s = 4 (якорь; s = 5 исключён В-173): Спирмен по парам (a, u) и доля совпадения знака.
  • Оси в шаг 8 (PS-1060 10(1)): полное влияние оси U = Σ_{k=1..4} r_p,k (infl_axis, среднее по сеткам уровня; уровень без оценки — 0); по убыванию U, пока накопленная доля до оси < 80 %, не больше 8.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict

import numpy as np

SEED = 20261127
NBOOT = 20000
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, "/data/p42-tk689/code"):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def fmt(x, d=3):
    return "—" if x is None or x != x else "%.*f" % (d, x)


# ───────────────────────────── чистые функции ─────────────────────────────

def coef_additive(k):
    """Коэффициенты суточного ряда в модели «якорь + одиночные + пары»: (якорь, одиночная клетка, пара) для клетки из k осей."""
    return 1 - k + k * (k - 1) // 2, 2 - k, 1


def r2(y, yh):
    sst = float(((y - y.mean()) ** 2).sum())
    return 1.0 - float(((y - yh) ** 2).sum()) / sst if sst > 0 else float("nan")


def corr2_cols(X, Y):
    """Квадрат корреляции Пирсона по столбцам (повторы): X, Y — n × B. R² лучшей линейной подгонки с свободным сдвигом."""
    xc, yc = X - X.mean(axis=0), Y - Y.mean(axis=0)
    den = (xc ** 2).sum(axis=0) * (yc ** 2).sum(axis=0)
    return np.where(den > 0, (xc * yc).sum(axis=0) ** 2 / np.where(den > 0, den, 1), np.nan)


def circ_idx(T, block, B, rng):
    """Индексы суток B повторов циркулярными блоками длины `block` (до T суток)."""
    L = max(1, min(block, T))
    nb = math.ceil(T / L)
    st = rng.integers(0, T, size=(B, nb))
    return ((st[:, :, None] + np.arange(L)[None, None, :]) % T).reshape(B, nb * L)[:, :T]


def counts(idx, T):
    c = np.zeros((idx.shape[0], T))
    np.add.at(c, (np.arange(idx.shape[0])[:, None], idx), 1.0)
    return c


def r2_cols(Y, Yh):
    """R² по столбцам (повторы): Y, Yh — n × B."""
    sst = ((Y - Y.mean(axis=0)) ** 2).sum(axis=0)
    sse = ((Y - Yh) ** 2).sum(axis=0)
    return np.where(sst > 0, 1.0 - sse / np.where(sst > 0, sst, 1), np.nan)


def ratio_stats(Dm, Pm, block, nboot, rng, chunk=1000):
    """Dm, Pm — n × T (клетка × сутки): факт и модель. Возвращает точку и интервалы R² (полные сутки), R²_модели на половинах, потолок, отношение."""
    n, T = Dm.shape
    odd = np.arange(T) % 2 == 1
    ev = ~odd

    def point():
        y, yh = Dm.sum(1), Pm.sum(1)
        m_o, m_e = r2(Dm[:, odd].sum(1), Pm[:, odd].sum(1)), r2(Dm[:, ev].sum(1), Pm[:, ev].sum(1))
        c1, c2 = r2(Dm[:, ev].sum(1), Dm[:, odd].sum(1)), r2(Dm[:, odd].sum(1), Dm[:, ev].sum(1))
        cc = float(corr2_cols(Dm[:, ev].sum(1)[:, None], Dm[:, odd].sum(1)[:, None])[0])
        return r2(y, yh), (m_o + m_e) / 2, cc, (c1 + c2) / 2

    full, half, ceil_, lit = point()
    out = dict(r2=full, r2_half=half, ceiling=ceil_, ceiling_lit=lit, ratio=half / ceil_ if ceil_ and ceil_ > 0 else float("nan"))
    R, Rh, Cc, Rt, Cl = [], [], [], [], []
    left = nboot
    while left > 0:
        B = min(chunk, left)
        left -= B
        idx = circ_idx(T, block, B, rng)
        C = counts(idx, T)
        Co, Ce = C[:, odd], C[:, ev]
        Y, Yh = Dm @ C.T, Pm @ C.T
        Yo, Ye = Dm[:, odd] @ Co.T, Dm[:, ev] @ Ce.T
        Yho, Yhe = Pm[:, odd] @ Co.T, Pm[:, ev] @ Ce.T
        r_full = r2_cols(Y, Yh)
        m = (r2_cols(Yo, Yho) + r2_cols(Ye, Yhe)) / 2
        c = corr2_cols(Yo, Ye)
        cl = (r2_cols(Yo, Ye) + r2_cols(Ye, Yo)) / 2
        R.append(r_full); Rh.append(m); Cc.append(c); Cl.append(cl); Rt.append(np.where(c > 0, m / np.where(c > 0, c, 1), np.nan))
    for name, arr in (("r2", R), ("r2_half", Rh), ("ceiling", Cc), ("ceiling_lit", Cl), ("ratio", Rt)):
        a = np.concatenate(arr)
        a = a[~np.isnan(a)]
        out[name + "_lo"], out[name + "_hi"] = (float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))) if len(a) else (float("nan"), float("nan"))
    return out


def rank_rows(x):
    """Ранги по строкам (без обработки совпадений: значения эффектов — вещественные)."""
    return np.argsort(np.argsort(x, axis=-1), axis=-1).astype(np.float64)


def spearman_rows(a, b):
    """Спирмен по последней оси; a, b — (…, m)."""
    ra, rb = rank_rows(a), rank_rows(b)
    ra -= ra.mean(axis=-1, keepdims=True)
    rb -= rb.mean(axis=-1, keepdims=True)
    den = np.sqrt((ra ** 2).sum(axis=-1) * (rb ** 2).sum(axis=-1))
    return np.where(den > 0, (ra * rb).sum(axis=-1) / np.where(den > 0, den, 1), np.nan)


def axis_rho(eff, nperm, rng, maxbg=12):
    """eff: {фон: {значение: эффект}} одной оси. Возвращает (ρ_ось, число пар фонов, нуль[nperm])."""
    bgs = sorted(eff, key=str)
    if len(bgs) > maxbg:
        bgs = [bgs[i] for i in sorted(rng.choice(len(bgs), maxbg, replace=False))]
    pairs = []
    for b1, b2 in itertools.combinations(bgs, 2):
        com = sorted(set(eff[b1]) & set(eff[b2]), key=str)
        if len(com) >= 4:
            pairs.append((b1, b2, com))
    if not pairs:
        return float("nan"), 0, None
    rho = np.array([spearman_rows(np.array([eff[b1][v] for v in com]), np.array([eff[b2][v] for v in com])) for b1, b2, com in pairs])
    null = np.zeros(nperm)
    for b1, b2, com in pairs:
        a = np.array([eff[b1][v] for v in com])
        b = np.array([eff[b2][v] for v in com])
        pa = rng.permuted(np.broadcast_to(a, (nperm, len(a))), axis=1)
        pb = rng.permuted(np.broadcast_to(b, (nperm, len(b))), axis=1)
        null += spearman_rows(pa, pb)
    return float(np.nanmean(rho)), len(pairs), null / len(pairs)


# ───────────────────────────── загрузка ─────────────────────────────

def load_side(db, side, T, say):
    """Одна сторона: якорь, клетки уровней 1–2 (все), уровни ≥ 3 — только номера (для выборки). Возвращает dict."""
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=120)
    t0 = time.time()
    rows = con.execute("SELECT id, entry, ckey, usd FROM metric_cur WHERE ckey IS NOT NULL AND uni=? AND win=? AND side=? ORDER BY id", (T.UNI, T.WIN, side)).fetchall()
    anchor = next((r for r in rows if r[1] == T.ANCHOR_ENTRY), None)
    if anchor is None:
        raise RuntimeError("нет якоря (запись %d) для %s" % (T.ANCHOR_ENTRY, side))
    akeys, _ = T.parse_ckey(anchor[2])
    seen = set()
    low, hi_ids, hi_entry, excl = {}, defaultdict(list), defaultdict(Counter), Counter()
    for cid, entry, ck, usd in rows:
        if ck in seen:
            continue
        seen.add(ck)
        keys, filt = T.parse_ckey(ck)
        axes = T.assign_axes(keys, filt, akeys)
        k = len(axes)
        if k and T.excluded(keys):
            excl[T.excluded(keys)] += 1
            continue
        if k <= 2:
            low[frozenset(axes.items())] = low.get(frozenset(axes.items())) or (cid, usd, k, entry)
        else:
            hi_ids[k].append(cid)
            hi_entry[k][entry] += 1
    say("%s: строк %d, якорь id %d, загрузка %.0f с; уровни: %s; исключено %s" % (side, len(rows), anchor[0], time.time() - t0, {k: len(v) for k, v in sorted(hi_ids.items())}, dict(excl)))
    return dict(con=con, anchor=anchor, akeys=akeys, low=low, hi_ids=hi_ids, hi_entry=hi_entry)


def fetch_daily(con, ids, T):
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        for cid, ex in con.execute("SELECT id, extra FROM metric WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk):
            d = (json.loads(ex).get("daily") if ex else None)
            if d:
                out[cid] = np.array(d, dtype=np.float64)
    return out


# ───────────────────────────── прогон ─────────────────────────────

def cmd_run(a):
    import tk689_influence as T
    out = sqlite3.connect(a.out, timeout=120)
    out.executescript("CREATE TABLE IF NOT EXISTS tk726_cond(item TEXT, side TEXT, level TEXT, key TEXT, value REAL, text TEXT);")
    out.execute("DELETE FROM tk726_cond")
    rows = []
    put = lambda item, side, level, key, value=None, text="": rows.append((item, side, str(level), key, None if value is None else float(value), text))   # noqa: E731
    lines = []
    say = lines.append
    rng = np.random.default_rng(SEED)
    verdict = {}
    for side in a.sides.split(","):
        say("=" * 20)
        say("сторона %s" % side)
        S = load_side(a.db, side, T, say)
        con, low = S["con"], S["low"]
        akey = frozenset()
        anchor_id = S["anchor"][0]
        l1 = {s: v for s, v in low.items() if v[2] == 1}
        l2 = {s: v for s, v in low.items() if v[2] == 2}
        need = {anchor_id} | {v[0] for v in l1.values()} | {v[0] for v in l2.values()}
        daily = fetch_daily(con, need, T)
        Tn = len(daily[anchor_id])
        say("суточные ряды: клеток уровней 0–2 с рядом %d из %d, суток %d" % (len(daily), len(need), Tn))
        dA = daily[anchor_id]
        yA = float(dA.sum())
        # --- 6.1: как порождались клетки уровней 3–4
        for k in (3, 4):
            ids = S["hi_ids"].get(k, [])
            ent = S["hi_entry"].get(k, Counter())
            say("уровень %d: клеток %d; записей-источников %d; наибольшие: %s" % (k, len(ids), len(ent), ", ".join("L-entry %s: %d" % (e, n) for e, n in ent.most_common(5))))
            put("gen", side, k, "n_cells", len(ids)); put("gen", side, k, "n_entries", len(ent))
        # --- 2: аддитивность вне выборки
        cmp_all = {}
        for k in (3, 4):
            ids = np.array(S["hi_ids"].get(k, []), dtype=np.int64)
            if not len(ids):
                continue
            samp = np.sort(rng.choice(ids, size=min(a.sample, len(ids)), replace=False))
            dsm = fetch_daily(con, samp.tolist(), T)
            cks = {}
            sl = samp.tolist()
            for i in range(0, len(sl), 400):
                ch = sl[i:i + 400]
                for cid, ck in con.execute("SELECT id, ckey FROM metric WHERE id IN (%s)" % ",".join("?" * len(ch)), ch):
                    cks[cid] = ck
            Ds, Ps, miss, nopair = [], [], 0, 0
            cA, cS, cP = coef_additive(k)
            for cid in sl:
                if cid not in dsm or cid not in cks:
                    miss += 1
                    continue
                keys, filt = T.parse_ckey(cks[cid])
                axes = T.assign_axes(keys, filt, S["akeys"])
                items = list(axes.items())
                singles = [low.get(frozenset([it])) for it in items]
                pairs = [low.get(frozenset(pr)) for pr in itertools.combinations(items, 2)]
                if len(items) != k or any(x is None or x[0] not in daily for x in singles + pairs):
                    nopair += 1
                    continue
                p = cA * dA + cS * sum(daily[x[0]] for x in singles) + cP * sum(daily[x[0]] for x in pairs)
                Ds.append(dsm[cid]); Ps.append(p)
            say("уровень %d: выборка %d из %d; нет ряда / ключа %d; нет одиночной клетки или пары %d; предсказуемых %d (%s)" % (k, len(sl), len(ids), miss, nopair, len(Ds), fmt(len(Ds) / max(1, len(sl)), 3)))
            put("add", side, k, "sample", len(sl)); put("add", side, k, "predictable", len(Ds))
            if len(Ds) < 50:
                say("уровень %d: предсказуемых < 50 — R² не считается" % k)
                continue
            Dm, Pm = np.vstack(Ds), np.vstack(Ps)
            cmp_all[k] = (Dm, Pm)
        pooled = None
        if cmp_all:
            pooled = (np.vstack([v[0] for v in cmp_all.values()]), np.vstack([v[1] for v in cmp_all.values()]))
        res_by = {}
        for name, (Dm, Pm) in list(((("уровень %d" % k), v) for k, v in cmp_all.items())) + ([("уровни 3+4", pooled)] if pooled and len(cmp_all) > 1 else []):
            L7 = max(1, math.ceil(Dm.shape[1] ** (1 / 3)))
            r7 = ratio_stats(Dm, Pm, L7, a.nboot, rng)
            r21 = ratio_stats(Dm, Pm, 21, a.nboot, rng)
            res_by[name] = (r7, r21)
            say("R² вне выборки, %s (n = %d, суток %d): R² %s [%s; %s] (блок 21: [%s; %s]); R² на половинах %s; потолок шума (r² половин) %s [%s; %s], буквальный R² %s; отношение %s [%s; %s] (блок 21: [%s; %s])" % (
                name, Dm.shape[0], Dm.shape[1], fmt(r7["r2"]), fmt(r7["r2_lo"]), fmt(r7["r2_hi"]), fmt(r21["r2_lo"]), fmt(r21["r2_hi"]), fmt(r7["r2_half"]), fmt(r7["ceiling"]),
                fmt(r7["ceiling_lo"]), fmt(r7["ceiling_hi"]), fmt(r7["ceiling_lit"]), fmt(r7["ratio"]), fmt(r7["ratio_lo"]), fmt(r7["ratio_hi"]), fmt(r21["ratio_lo"]), fmt(r21["ratio_hi"])))
            for kk, v in r7.items():
                put("add", side, name, kk + "_b%d" % L7, v)
            for kk, v in r21.items():
                put("add", side, name, kk + "_b21", v)
        # --- 3: ρ_ось
        eff_by_axis = defaultdict(dict)       # ось → {фон → {значение → эффект}}
        for s1, v1 in l1.items():
            (ax, lab), = tuple(s1)
            eff_by_axis[ax].setdefault("anchor", {})[lab] = float(daily[v1[0]].sum() - yA)
        for s2, v2 in l2.items():
            items = tuple(s2)
            for i in (0, 1):
                (ax, lab), other = items[i], items[1 - i]
                ref = l1.get(frozenset([other]))
                if ref is None or ref[0] not in daily:
                    continue
                eff_by_axis[ax].setdefault(other, {})[lab] = float(daily[v2[0]].sum() - daily[ref[0]].sum())
        rhos, npairs, nulls, used = {}, {}, [], []
        for ax, eff in sorted(eff_by_axis.items()):
            eff = {b: {v: e for v, e in d.items()} for b, d in eff.items() if len(d) >= 4}
            vals = set(v for d in eff.values() for v in d)
            if len(eff) < 2 or len(vals) < 4:
                continue
            rho, npr, nul = axis_rho(eff, a.nperm, rng)
            if npr == 0 or rho != rho:
                continue
            rhos[ax], npairs[ax] = rho, npr
            nulls.append(nul)
        if rhos:
            mean_rho = float(np.mean(list(rhos.values())))
            null_mean = np.mean(np.vstack(nulls), axis=0)
            p_null = float((1 + (null_mean >= mean_rho).sum()) / (len(null_mean) + 1))
            say("ρ_ось: осей %d (с ≥ 4 значениями на ≥ 2 фонах), пар фонов %d; средняя ρ %s; нуль (перестановка внутри фона): среднее %s, q95 %s, p %s; ρ осей: мин %s, макс %s, доля ρ ≥ 0,5: %s" % (
                len(rhos), sum(npairs.values()), fmt(mean_rho), fmt(float(null_mean.mean())), fmt(float(np.quantile(null_mean, 0.95))), fmt(p_null, 4), fmt(min(rhos.values())), fmt(max(rhos.values())),
                fmt(float(np.mean([r >= 0.5 for r in rhos.values()])))))
            put("rho", side, "all", "mean_rho", mean_rho); put("rho", side, "all", "p_null", p_null); put("rho", side, "all", "n_axes", len(rhos))
            for ax, r in sorted(rhos.items(), key=lambda t: -t[1]):
                put("rho_axis", side, "all", ax, r)
            say("ρ по осям (по убыванию): " + "; ".join("%s %s" % (ax, fmt(r, 2)) for ax, r in sorted(rhos.items(), key=lambda t: -t[1])[:40]))
        else:
            mean_rho, p_null = float("nan"), float("nan")
            say("ρ_ось: нет осей с ≥ 4 значениями на ≥ 2 фонах")
        # --- 4: стоп как фон (описательно)
        sl_low, sl_hi = [], []
        for s2, v2 in l2.items():
            items = tuple(s2)
            for i in (0, 1):
                if items[i][0] == "s" and items[i][1] in ("1", "1.5", "2"):
                    other = items[1 - i]
                    ref_s = l1.get(frozenset([items[i]]))
                    ref_a = l1.get(frozenset([other]))
                    if ref_s and ref_a and ref_s[0] in daily and ref_a[0] in daily and other[0] != "s":
                        sl_low.append((other, float(daily[v2[0]].sum() - daily[ref_s[0]].sum()), float(daily[ref_a[0]].sum() - yA)))
        if len(sl_low) >= 5:
            agg = defaultdict(list)
            for o, dl, dh in sl_low:
                agg[o].append((dl, dh))
            lo = np.array([np.mean([x[0] for x in v]) for v in agg.values()])
            hi_ = np.array([v[0][1] for v in agg.values()])
            rho_s = float(spearman_rows(lo, hi_))
            same = float(np.mean(np.sign(lo) == np.sign(hi_)))
            say("стоп как фон (описательно): пар (ось = значение) с эффектом и при s ≤ 2 %%, и при s = 4 %%: %d; Спирмен %s; совпадение знака %s" % (len(agg), fmt(rho_s), fmt(same)))
            put("stopbg", side, "all", "rho", rho_s); put("stopbg", side, "all", "same_sign", same); put("stopbg", side, "all", "n", len(agg))
        else:
            say("стоп как фон: пар с эффектом на обоих фонах %d — мало" % len(sl_low))
        # --- итог по стороне
        main = res_by.get("уровни 3+4") or (next(iter(res_by.values())) if res_by else None)
        ratio = main[0]["ratio"] if main else float("nan")
        unclear = bool(main and any((r["ratio_lo"] < 0.5 < r["ratio_hi"]) for r in main)) if main else False
        cond_r2 = ratio < 0.5
        cond_rho = (mean_rho < 0.5) or (p_null >= 0.05) if mean_rho == mean_rho else True
        verdict[side] = dict(ratio=ratio, mean_rho=mean_rho, cond_r2=cond_r2, cond_rho=bool(cond_rho), unclear=unclear)
        say("ИТОГ %s: отношение R² к потолку %s (%s%s), средняя ρ_ось %s (p нуля %s) → %s" % (
            side, fmt(ratio), "< 0,5" if cond_r2 else "≥ 0,5", ", интервал накрывает 0,5" if unclear else "", fmt(mean_rho), fmt(p_null, 4),
            "карта условна" if (cond_r2 or cond_rho) else "карта около якоря аддитивна"))
        put("verdict", side, "all", "conditional", float(cond_r2 or cond_rho))
        put("verdict", side, "all", "ratio", ratio)
    # --- оси в шаг 8 (по infl_axis)
    cc = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True, timeout=120)
    for side in a.sides.split(","):
        per = defaultdict(lambda: defaultdict(list))
        for run, k, grid, ax, rp, n in cc.execute("SELECT run, k, grid, axis, r_p, n FROM infl_axis WHERE side=? AND r_p IS NOT NULL AND k BETWEEN 1 AND 4", (side,)):
            per[ax][k].append((rp, n or 0))
        tot = {}
        for ax, byk in per.items():
            s = 0.0
            for k, lst in byk.items():
                w = sum(n for _, n in lst)
                s += (sum(rp * n for rp, n in lst) / w) if w > 0 else float(np.mean([rp for rp, _ in lst]))
            tot[ax] = s
        order = sorted(tot.items(), key=lambda t: -t[1])
        grand = sum(v for _, v in order)
        pick, acc = [], 0.0
        for ax, v in order:
            if acc / grand >= 0.8 or len(pick) >= 8:
                break
            pick.append((ax, v)); acc += v
        say("оси в шаг 8, %s: осей с оценкой %d; U = Σ r_p по уровням 1–4; отобрано %d (накопленная доля %s): %s" % (side, len(order), len(pick), fmt(acc / grand if grand else float("nan")), "; ".join("%s %s" % (ax, fmt(v, 2)) for ax, v in pick)))
        say("первые 15 по U (%s): %s" % (side, "; ".join("%s %s (%s)" % (ax, fmt(v, 2), fmt(v / grand, 3)) for ax, v in order[:15])))
        for i, (ax, v) in enumerate(order[:40]):
            put("u_axis", side, "all", ax, v, "в шаг 8" if i < len(pick) else "")
    out.executemany("INSERT INTO tk726_cond VALUES(?,?,?,?,?,?)", rows)
    out.commit()
    text = "\n".join(lines)
    print(text[-1500:])
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--db", default="/data/chron/chron.sqlite")
    r.add_argument("--out", default="/data/p42-tk726/tk726.sqlite")
    r.add_argument("--report", default="")
    r.add_argument("--sides", default="short,long")
    r.add_argument("--nboot", type=int, default=NBOOT)
    r.add_argument("--nperm", type=int, default=2000)
    r.add_argument("--sample", type=int, default=20000)
    r.set_defaults(fn=cmd_run)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
