#!/usr/bin/env python3
"""TK-727, шаг 3 (§13.78.3, §13.78.7 п. 8, 11): гонка оценщиков волатильности.

  tk727_s3.py check    [--out DIR]   # проверка метода ДО чтения результатов: формула NATR30 против `natr_bp` таблицы подходов на 100 случайных строках (зерно 20261127)
  tk727_s3.py moments  [--out DIR] [--procs 6] [--only SYM,…]   # стадия 1: по монете — оценщики 1–11, входы HAR, размах 20 / 60 минут в моментах (б) и (а)
  tk727_s3.py analyze  [--out DIR]   # стадия 2: HAR, масштаб, QLIKE, суточные разности, бутстреп, Холм, правило решения (читается только после check)

Решения по краям, записанные ДО чтения результатов (`chron.py add --kind amend`, L-…): см. `EDGE_RULES` ниже.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk727_lib as L  # noqa: E402

OUT = "/data/p42-tk727"
SYMS = "/data/p42-tk621/universe/symbols-crypto.txt"
ASM = "/data/p42-tk671/asm"
ASM_FILES = [f"{w}-{s}.parquet" for w in ("wa", "wb", "wc") for s in ("long", "short")]
PER_DAY = 20  # случайных минут на монету в сутки (набор б)
SEL = L.HALVES["SEL"]
SIG_DAYS = (181, 317)  # SEL: 01.07 … 13.11
SCALE_E8 = 10 ** 8

EDGE_RULES = """\
Решения по краям шага 3 (протокол §13.78.3 / §13.78.7 п. 8 их не называет; записаны до чтения результатов):
1. Момент m — начало минуты; оценщики берутся на закрытой минуте t = m − 1 (как `natr[sig_ms // 60000 − 1]` в движке); цена размаха — закрытие t; размах — минуты [m, m + H).
   Для сигналов (а) m = meta_abs_ms // 60000 (минута сигнала целиком входит в окно размаха).
2. Моменты (б): 20 различных минут суток без возвращения, rng = default_rng([20261127, номер монеты в отсортированном c539, сутки]); сутки 01.01–24.11 (328).
3. Минуты нет → оценщик NaN (как у движка); окно размаха требует ≥ ½ H присутствующих минут; шаг цены — НОД цен ×1e8 за предыдущие 7 суток по свечам
   (движок — по сделкам, `tick_at`); размах и прогноз снизу ограничены шагом / ценой (RR и k·F).
4. Общая выборка: момент входит, если все 12 оценщиков и размах определены; число выбывших печатается в отчёте.
5. Масштаб k_i,H(d) = среднее RR/F по моментам (б) суток ≤ d − 2 (растущее окно; все цели реализованы), тот же k для (б) и (а). Сутки 0–29 — разгон: масштаб и первая подгонка HAR,
   в сравнения не входят; HAR на сутках 0–29 внутри выборки.
6. HAR: OLS log RR_H ~ 1 + log RVm60 + log RVm1440 + log RVm10080 + 23 часа суток (UTC) на моментах (б) суток ≤ d0 − 2, пересчёт на начале каждого месяца (сутки 31, 59, …), для
   января — по суткам 0–29; общий для монет; прогноз exp(xβ). Нижняя граница входов — шаг / цена.
7. Решающая статистика — суточное среднее QLIKE по моментам суток (равный вес суток); разность «NATR30 − кандидат»; p двусторонний по нормали от среднего и SE бутстрепа (блок ⌈n^{1/3}⌉ = 6 и 21 сутки);
   «нижняя граница > 0 после Холма» ≡ среднее > 0 и p_Холм < 0,05. Знак при двух блоках разный → «не ясно».
8. Правило 5 %: лучший из кодов 1–8 берётся ОТДЕЛЬНО в каждой половине (наименьшая средняя потеря половины); относительное снижение (L_лучший − L_кандидат)/L_лучший ≥ 5 % в обеих половинах и
   нижняя граница > 0 после Холма по 4 новым оценщикам (в каждой половине, блок 6; блок 21 — проверка знака).
9. Победитель — прошедший нижнюю границу в обеих половинах с наибольшим средним (по половинам) относительным снижением потери к NATR30; если победитель № 9–12, он берётся только по правилу 5 %,
   иначе победителем становится лучший прошедший из кодов 1–8; если таких нет — единица остаётся NATR30.
"""


def read_syms():
    return sorted(open(SYMS).read().strip().split(","))


# ───────────────────────────── проверка метода ─────────────────────────────

def exact_natr_e8(S_int, t, p=30):
    """Целочисленный NATR (×1e8) на минуте t как `natr_of`: prevc — закрытие предыдущей присутствующей минуты (в окне и до него)."""
    o, h, l, c, pres = S_int
    if not pres[t]:
        return -1
    j0 = t - p + 1
    # закрытие присутствующей минуты перед окном
    prev = 0
    for j in range(j0 - 1, -1, -1):
        if pres[j]:
            prev = int(c[j])
            break
    s = 0
    k = 0
    for j in range(j0, t + 1):
        if not pres[j]:
            continue
        tr = int(h[j]) - int(l[j])
        if prev != 0:
            tr = max(tr, abs(int(h[j]) - prev), abs(int(l[j]) - prev))
        prev = int(c[j])
        s += tr
        k += 1
    if k * 2 < p:
        return -1
    return (s * SCALE_E8) // (k * int(c[t]))


def load_table_rows():
    import pyarrow.parquet as pq

    keep = set(read_syms())
    rows = []
    for f in ASM_FILES:
        t = pq.read_table(os.path.join(ASM, f), columns=["meta_sym", "meta_abs_ms", "meta_win", "natr_bp"]).to_pydict()
        for s, ms, w, nb in zip(t["meta_sym"], t["meta_abs_ms"], t["meta_win"], t["natr_bp"]):
            if s in keep:
                rows.append((s, int(ms), w, float(nb), f))
    return rows


def cmd_check(a):
    os.makedirs(a.out, exist_ok=True)
    rows = load_table_rows()
    rng = np.random.default_rng(L.SEED)
    # выборка среди строк SEL с конечным natr_bp
    cand = [r for r in rows if np.isfinite(r[3]) and SIG_DAYS[0] <= (r[1] - L.G0_MS) // L.DAY_MS - L.DAY0 < SIG_DAYS[1]]
    sel = rng.choice(len(cand), 100, replace=False)
    res = []
    n_ok_prev = n_ok_cur = 0
    maxrel = 0.0
    for i in sel:
        sym, ms, win, nb, f = cand[i]
        mi = (ms - L.G0_MS) // L.MIN_MS
        d0 = max(0, mi // 1440 - 1)
        d1 = min(L.NDAYS, mi // 1440 + 2)
        # минутки из каталога окна строки (приоритет), затем остальные
        wins = (win,) + tuple(w for w in L.WINS if w != win)
        n = (d1 - d0) * 1440
        o, h, l, c = (np.zeros(n, np.int64) for _ in range(4))
        pres = np.zeros(n, bool)
        for di in range(d0, d1):
            p = L.csv_path(sym, di, wins=wins)
            if p is None:
                continue
            arr = np.loadtxt(p, delimiter=",", skiprows=1, usecols=(0, 1, 2, 3, 4), ndmin=2)
            k = ((arr[:, 0] - L.G0_MS) // L.MIN_MS).astype(np.int64) - d0 * 1440
            okk = (k >= 0) & (k < n)
            k = k[okk]
            o[k], h[k], l[k], c[k] = (np.rint(arr[okk, j] * 1e8).astype(np.int64) for j in (1, 2, 3, 4))
            pres[k] = True
        t = mi - d0 * 1440 - 1  # закрытая минута перед сигналом
        # окно 30 минут берётся целиком из загруженных суток; prev до окна — по предыдущим суткам (загружены)
        e8 = exact_natr_e8((o, h, l, c, pres), t, 30)
        e8_cur = exact_natr_e8((o, h, l, c, pres), t + 1, 30)
        bp = np.float32(e8 / 1e4) if e8 >= 0 else np.float32("nan")
        bpc = np.float32(e8_cur / 1e4) if e8_cur >= 0 else np.float32("nan")
        tab = np.float32(nb)
        ok = bool(bp == tab)
        okc = bool(bpc == tab)
        n_ok_prev += ok
        n_ok_cur += okc
        rel = abs(float(bp) - nb) / nb if nb else 0.0
        maxrel = max(maxrel, rel) if np.isfinite(rel) else maxrel
        res.append(dict(sym=sym, abs_ms=ms, win=win, table_bp=nb, py_bp=float(bp), py_bp_cur=float(bpc), match_prev=ok, match_cur=okc))
    passed = n_ok_prev == 100
    out = dict(check="NATR30 python vs natr_bp таблицы подходов (SEL, c539), 100 случайных строк, зерно 20261127", n=100, match_t_minus_1=n_ok_prev, match_t=n_ok_cur,
               max_rel_diff=maxrel, passed=passed, rows=res, edge_rules=EDGE_RULES)
    json.dump(out, open(os.path.join(a.out, "method_check.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"check: совпало по t−1 {n_ok_prev}/100 (по t {n_ok_cur}/100), max |отн.| {maxrel:.3g}, PASS={passed}")
    for r in res:
        if not r["match_prev"]:
            print("  расхождение:", r)
    sys.exit(0 if passed else 3)


# ───────────────────────────── стадия 1 ─────────────────────────────

EST1 = ["natr5", "natr15", "natr30", "natr60", "rv5", "rv15", "rv30", "rv60", "ewtr", "gk60", "bv60", "rvm60", "rvm1440", "rvm10080"]


def build_moments(S, E, tickd, m, si, tag):
    t = m - 1
    day = L.day_of_minute(m)
    out = {"m": m.astype(np.int32), "day": day.astype(np.int16), "hour": ((m % 1440) // 60).astype(np.int8), "set": np.full(len(m), tag, np.int8), "sym": np.full(len(m), si, np.int16)}
    for k in EST1:
        out[k] = E[k][t]
    out["natr30_prev"] = np.where(t >= 30, E["natr30"][np.maximum(t - 30, 0)], np.nan)
    price = S["c"][t]
    tk = tickd[np.minimum(m // 1440, L.NDAYS - 1)].astype(np.float64) / 1e8
    with np.errstate(invalid="ignore", divide="ignore"):
        out["tickf"] = np.where(tk > 0, tk / price, np.nan)
    out["rr60"] = L.future_range(S, m, 60)
    out["rr20"] = L.future_range(S, m, 20)
    cs = np.concatenate([[0.0], np.cumsum(S["qv"])])
    out["turn24"] = np.where(m >= 1440, cs[m] - cs[np.maximum(m - 1440, 0)], np.nan)
    return out


def coin_task(args):
    si, sym, sig_m, outdir = args
    S = L.load_coin(sym)
    E = L.all_estimators(S)
    tickd = L.tick_by_day(S)
    ms = []
    for d in range(L.DAY0, L.NDAYS):
        rng = np.random.default_rng([L.SEED, si, d])
        ms.append(d * 1440 + np.sort(rng.choice(1440, PER_DAY, replace=False)))
    mb = np.concatenate(ms).astype(np.int64)
    parts = [build_moments(S, E, tickd, mb, si, 0)]
    if len(sig_m):
        sm = np.unique(np.asarray(sig_m, np.int64))
        parts.append(build_moments(S, E, tickd, sm, si, 1))
    res = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    np.savez_compressed(os.path.join(outdir, sym + ".npz"), **res)
    return sym, len(mb), int(len(res["m"]) - len(mb))


def load_signal_minutes(syms):
    """Сигналы SEL таблицы подходов (c539): минута m = meta_abs_ms // 60000 в сетке G0; по монете — уникальные минуты."""
    import pyarrow.parquet as pq

    keep = set(syms)
    by = {s: [] for s in syms}
    n_rows = 0
    for f in ASM_FILES:
        t = pq.read_table(os.path.join(ASM, f), columns=["meta_sym", "meta_abs_ms"]).to_pydict()
        for s, ms in zip(t["meta_sym"], t["meta_abs_ms"]):
            if s not in keep:
                continue
            d = (int(ms) - L.G0_MS) // L.DAY_MS - L.DAY0
            if SIG_DAYS[0] <= d < SIG_DAYS[1]:
                by[s].append((int(ms) - L.G0_MS) // L.MIN_MS)
                n_rows += 1
    return by, n_rows


def cmd_moments(a):
    chk = os.path.join(a.out, "method_check.json")
    if not (os.path.exists(chk) and json.load(open(chk, encoding="utf-8")).get("passed")):
        sys.exit("moments: нет успешного method_check.json — сперва `check` (§13.78.3: проверка метода до чтения)")
    syms = read_syms()
    outdir = os.path.join(a.out, "s3", "coin")
    os.makedirs(outdir, exist_ok=True)
    by, n_rows = load_signal_minutes(syms)
    print(f"сигналов SEL c539 (строк таблицы, 6 файлов): {n_rows}; монет {len(syms)}", flush=True)
    only = set(a.only.split(",")) if a.only else None
    tasks = [(i, s, by[s], outdir) for i, s in enumerate(syms) if (only is None or s in only) and not (a.skip_done and os.path.exists(os.path.join(outdir, s + ".npz")))]
    t0 = time.time()
    import multiprocessing as mp

    with mp.Pool(a.procs) as pool:
        for k, (sym, nb, na) in enumerate(pool.imap_unordered(coin_task, tasks, chunksize=1)):
            if k % 25 == 0:
                print(f"[{k + 1}/{len(tasks)}] {sym} (б) {nb} (а) {na}  {time.time() - t0:.0f} с", flush=True)
    print(f"готово: {len(tasks)} монет за {time.time() - t0:.0f} с", flush=True)


# ───────────────────────────── стадия 2 ─────────────────────────────

def cmd_analyze(a):
    chk = os.path.join(a.out, "method_check.json")
    if not (os.path.exists(chk) and json.load(open(chk, encoding="utf-8")).get("passed")):
        sys.exit("analyze: нет успешного method_check.json")
    from tk727_s3_an import analyze  # отдельный модуль, чтобы стадия 1 не тянула лишнее

    analyze(a.out, EDGE_RULES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "moments", "analyze"])
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--only", default="")
    ap.add_argument("--skip-done", action="store_true")
    a = ap.parse_args()
    {"check": cmd_check, "moments": cmd_moments, "analyze": cmd_analyze}[a.cmd](a)


if __name__ == "__main__":
    main()
