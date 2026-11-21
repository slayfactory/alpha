#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): ворота семейства `bd` (`tk671_bd.py`) на реальных строках, srv. Только чтение.

  python3 tk671_gate_bd.py turn      — G-turn: `turn1h` из `.b42` = столбец `turn1h` таблиц f12 TK-634 (wb / wc × L / S), побитно
  python3 tk671_gate_bd.py eq48      — G-eq-1: `compute` на ключах таблиц TK-648 = записанные выходы `/data/p42-tk648/feat/*.csv` (NaN-узор, значения)
  python3 tk671_gate_bd.py eq --n 2000 — G-eq-2: `compute` против ОРИГИНАЛА `tk648_feat.one_sym` на ≥ 1000 реальных строках в ω (дампы `b0p0` wb / wc, обе стороны)
  python3 tk671_gate_bd.py causal --n 500 — G-causal: значения книги и минутки кэша с ts > σ − 30 с / минут ≥ t заменены мусором → выходы те же
  python3 tk671_gate_bd.py bench --coins 71 --procs 3 — время (мс/строка, секунд на монето-сутки) на плотных реальных сигналах wc
Результат — `/data/p42-tk671/fam/bd/gate-<имя>.json`."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bookdepth_feat as bf  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk648_feat as f48  # noqa: E402
import tk671_bd as bd  # noqa: E402

OUT = "/data/p42-tk671/fam/bd"
B0 = "/data/p42-tk625/h775/eng/%s/b0p0/%s-sigsx.csv"
WIN = {x.name: x for x in st.TRADE_WINDOWS}
SIDEN = {"L": "long", "S": "short"}


EQ_COLS = [c for c in bd.COLS if c != "bd_mode"]          # G-eq против TK-648 — без bd_mode: решение координатора 21.11 (по снимкам строго до σ), оригинал считал по метке суток

def cache_original_days():
    """Оригинальный `bf.load_day` без изменений, но с памятью (монето-сутки читаются один раз): иначе эталон по строкам в одиночку слишком долог."""
    import functools
    bf.load_day = functools.lru_cache(maxsize=None)(bf.load_day)


def rows_from_keys(win, side, sym, sig_ms, row0=0):
    w = WIN[win]
    sig_ms = np.asarray(sig_ms, dtype=np.int64)
    return pd.DataFrame(dict(row_id=np.arange(row0, row0 + len(sym), dtype=np.int64), sym=list(sym), sig_ms=sig_ms, abs_ms=sig_ms + w.t0_ms, side=side, win=win))


def read_keys(path):
    with open(path, encoding="utf-8", newline="") as f:
        r = list(csv.DictReader(f))
    return [x["sym"] for x in r], np.array([int(x["sig_ms"]) for x in r], dtype=np.int64), r


def same(a, b, rel=1e-9):
    """(равны с допуском, число расхождений, макс. отн. ошибка, узор NaN совпал)."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    nan_ok = bool((np.isnan(a) == np.isnan(b)).all())
    m = ~np.isnan(a) & ~np.isnan(b)
    err = np.zeros(len(a))
    err[m] = np.abs(a[m] - b[m]) / np.maximum(np.abs(b[m]), 1e-300)
    err[m & (a == b)] = 0.0
    return nan_ok and bool((err <= rel).all()), int((err > rel).sum() + (np.isnan(a) != np.isnan(b)).sum()), float(err.max()) if len(err) else 0.0, nan_ok


def save(name, res):
    os.makedirs(OUT, exist_ok=True)
    json.dump(res, open(os.path.join(OUT, "gate-%s.json" % name), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))


def cmd_turn(a):
    res, tot, bad = {}, 0, 0
    for win in ("wb", "wc"):
        for sd in ("L", "S"):
            sym, ms, raw = read_keys("/data/p42-tk634/feat/h775/%s-%s.csv" % (win, sd))
            want = np.array([float(x["turn1h"]) if x["turn1h"] != "" else np.nan for x in raw])
            tmin = ms // 60_000
            got = np.full(len(sym), np.nan)
            by = {}
            for i, s_ in enumerate(sym):
                by.setdefault(s_, []).append(i)
            for s_, ix in by.items():
                p = "/data/p42-tk621/%s/cache/%s.b42" % (win, s_)
                if os.path.exists(p):
                    got[ix] = bd.turn1h_from_qv(bd.read_qv(p), tmin[ix])
            ok, nbad, mx, nan_ok = same(got, want, 0.0)
            res["%s-%s" % (win, sd)] = dict(rows=len(sym), bad=nbad, nan_pattern_ok=nan_ok, nan_in_table=int(np.isnan(want).sum()))
            tot += len(sym)
            bad += nbad
    res.update(total_rows=tot, total_bad=bad, ok=bad == 0)
    save("turn", res)
    return 0 if bad == 0 else 1


def cmd_eq48(a):
    res, tot, bad, defined = {}, 0, 0, 0
    for win in ("wb", "wc"):
        for sd in ("L", "S"):
            sym, ms, _ = read_keys("/data/p42-tk648/feat/%s-%s.csv" % (win, sd))
            orig = pd.read_csv("/data/p42-tk648/feat/%s-%s.csv" % (win, sd), float_precision="round_trip")      # без round_trip pandas читает repr с ошибкой в последних разрядах
            rows = rows_from_keys(win, SIDEN[sd], sym, ms)
            got = bd.compute(rows, win, SIDEN[sd], procs=3)
            r = {}
            for c in EQ_COLS:
                ok, nbad, mx, nan_ok = same(got[c].to_numpy(), orig[c].to_numpy())
                r[c] = dict(bad=nbad, max_rel=mx, nan_ok=nan_ok)
                bad += nbad
            n_def = int(orig["bd_imb1"].notna().sum())
            res["%s-%s" % (win, sd)] = dict(rows=len(sym), defined=n_def, cols=r)
            tot += len(sym)
            defined += n_def
    res.update(total_rows=tot, total_defined=defined, total_bad=bad, ok=bad == 0)
    save("eq48", res)
    return 0 if res["ok"] else 1


def b0_pool(seed, n_per):
    """Случайные реальные строки в ω: `n_per` на (окно, сторона) из дампов `b0p0` (wb / wc)."""
    rng = np.random.default_rng(seed)
    pools = []
    for win in ("wb", "wc"):
        w = WIN[win]
        for sd in ("long", "short"):
            t = pd.read_csv(B0 % (win, sd), usecols=["sym", "sig_ms"])
            s = w.t0_ms / 1000.0 + t["sig_ms"].to_numpy(dtype=np.int64) / 1000.0
            ok, _ = bd.omega_mask(s)
            t = t[ok]
            t = t.iloc[rng.choice(len(t), size=min(n_per, len(t)), replace=False)]
            pools.append((win, sd, t["sym"].tolist(), t["sig_ms"].to_numpy(dtype=np.int64)))
    return pools


def cmd_eq(a):
    cache_original_days()
    pools = b0_pool(671, a.n // 4)
    res, tot, bad = {}, 0, 0
    for win, sd, sym, ms in pools:
        rows = rows_from_keys(win, sd, sym, ms)
        t0 = time.time()
        got = bd.compute(rows, win, sd, procs=3)
        t_new = time.time() - t0
        # оригинал: `turn1h` — те же значения (G-turn доказал их равенство таблице f12)
        tmin = rows["sig_ms"].to_numpy() // 60_000
        turn = np.full(len(rows), np.nan)
        for s_ in set(sym):
            ix = np.flatnonzero(rows["sym"].to_numpy() == s_)
            p = "/data/p42-tk621/%s/cache/%s.b42" % (win, s_)
            if os.path.exists(p):
                turn[ix] = bd.turn1h_from_qv(bd.read_qv(p), tmin[ix])
        s_abs = f48.abs_s(WIN[win], rows["sig_ms"].to_numpy())
        by = {}
        for i, s_ in enumerate(sym):
            by.setdefault(s_, []).append((i, float(s_abs[i]), sd == "long", float(turn[i])))
        t0 = time.time()
        want = {k: np.full(len(rows), np.nan) for k in bd.COLS}     # оригинал, каждая строка — одна (свои сутки)
        union = {k: np.full(len(rows), np.nan) for k in bd.COLS}    # оригинал как в `tk648_feat build`: объединение суток всех строк монеты
        for s_, items in by.items():
            for it in items:
                for rid, f in f48.one_sym((s_, [it])):
                    for k in bd.COLS:
                        want[k][rid] = f[k]
            for rid, f in f48.one_sym((s_, items)):
                for k in bd.COLS:
                    union[k][rid] = f[k]
        t_old = time.time() - t0
        r = {}
        for c in EQ_COLS:
            ok, nbad, mx, nan_ok = same(got[c].to_numpy(), want[c])
            r[c] = dict(bad=nbad, max_rel=mx, nan_ok=nan_ok)
            bad += nbad
        n_def = int(np.isfinite(want["bd_imb1"]).sum())
        bm_a, bm_b = got["bd_mode"].to_numpy(), np.asarray(want["bd_mode"])
        mode_info = dict(rows_both=int((np.isfinite(bm_a) & np.isfinite(bm_b)).sum()), differs=int((np.isfinite(bm_a) & np.isfinite(bm_b) & (bm_a != bm_b)).sum()), nan_pattern_differs=int((np.isnan(bm_a) != np.isnan(bm_b)).sum()))
        diff_rows = np.zeros(len(rows), dtype=bool)
        for c in EQ_COLS:
            a_, b_ = np.asarray(union[c]), np.asarray(want[c])
            diff_rows |= ~((np.isnan(a_) & np.isnan(b_)) | (np.abs(a_ - b_) <= 1e-9 * np.maximum(np.abs(b_), 1e-300)))
        only_thin = all(not (np.isfinite(union[c][diff_rows]) | np.isfinite(want[c][diff_rows])).any() or c in ("bd_thin5", "bd_thin30") for c in EQ_COLS)
        cols_diff = {c: int((~((np.isnan(union[c]) & np.isnan(want[c])) | (np.abs(union[c] - want[c]) <= 1e-9 * np.maximum(np.abs(want[c]), 1e-300)))).sum()) for c in EQ_COLS}
        res["%s-%s" % (win, sd)] = dict(rows=len(rows), defined=n_def, sec_new=round(t_new, 2), sec_orig=round(t_old, 2), cols=r,
                                       union_original_differs_rows=int(diff_rows.sum()), union_original_differs_by_col=cols_diff, bd_mode_vs_day_label=mode_info)
        tot += len(rows)
    res.update(total_rows=tot, total_defined=sum(v["defined"] for v in res.values() if isinstance(v, dict)), total_bad=bad,
               total_union_differs=sum(v["union_original_differs_rows"] for v in res.values() if isinstance(v, dict)))
    res["ok"] = bad == 0 and res["total_defined"] >= 1000
    save("eq", res)
    return 0 if res["ok"] else 1


def cmd_causal(a):
    rng = np.random.default_rng(671)
    pools = b0_pool(672, a.n)                                                   # кандидатов больше, чем нужно: строки без архива / кэша пропускаются; стоп на n + 100 учтённых
    n = bad_f = bad_t = bad_q = 0
    mode_diff = mode_n = 0
    for win, sd, sym, ms in pools:
        w = WIN[win]
        for s_, m_ in zip(sym, ms):
            if n >= a.n + 100:
                break
            sg = float(f48.abs_s(w, np.array([m_]))[0])
            day = {bd.day_str(d) for d in (int(np.floor(sg / 86400.0)), int(np.floor((sg - 3600.0) / 86400.0)))}
            tm = bd.load_sym_fast(bd.ZIP, s_, sorted(day))
            p = "/data/p42-tk621/%s/cache/%s.b42" % (win, s_)
            if tm is None or not os.path.exists(p):
                continue
            t, m = tm
            qv = bd.read_qv(p)
            tmin = np.array([m_ // 60_000], dtype=np.int64)
            turn = bd.turn1h_from_qv(qv, tmin)
            args = (np.array([sg]), np.array([sd == "long"]))
            clean = bd.features_vec(t, m, *args, turn)
            late = t > sg - bf_lag()
            # (a) значения после границы — мусор (разные знаки, нули, NaN, огромные)
            m2 = m.copy()
            g = rng.lognormal(10.0, 5.0, size=(int(late.sum()), m.shape[1])) * rng.choice([-1.0, 1.0, 0.0, np.nan], size=(int(late.sum()), m.shape[1]))
            m2[late] = g
            dirty = bd.features_vec(t, m2, *args, turn)
            # (b) ещё и метки времени после границы — мусор (всё равно позже границы), порядок восстановлен сортировкой
            t3 = t.copy()
            t3[late] = sg - bf_lag() + rng.uniform(0.001, 5000.0, size=int(late.sum()))
            o = np.argsort(t3, kind="stable")
            dirty_t = bd.features_vec(t3[o], m2[o], *args, turn)
            # минутки: qv с минуты t (включительно) — мусор
            q2 = qv.copy()
            q2[int(tmin[0]):] = rng.integers(-10 ** 12, 10 ** 12, size=len(q2) - int(tmin[0]))
            turn_d = bd.turn1h_from_qv(q2, tmin)
            cols = list(bd.COLS)                                                                      # включая bd_mode: по снимкам строго до σ, мусор после границы его не меняет
            n += 1
            bad_f += not all(same(clean[c], dirty[c], 0.0)[0] for c in cols)
            bad_t += not all(same(clean[c], dirty_t[c], 0.0)[0] for c in cols)
            bad_q += not same(turn, turn_d, 0.0)[0]
            # bd_mode: по снимкам суток ТОЛЬКО до границы — совпадает ли с режимом всех снимков суток (информационно)
            ok_ = np.isfinite(clean["bd_mode"][0])
            if ok_:
                mode_n += 1
                tt = t[t <= sg - bf_lag()]
                dd = int(np.floor(tt[-1] / 86400.0))
                m_c = f48.day_mode(tt).get(dd, np.nan)
                mode_diff += not (m_c == clean["bd_mode"][0])
    res = dict(rows=n, bad_values=bad_f, bad_timestamps=bad_t, bad_turn1h=bad_q, bd_mode_rows=mode_n, bd_mode_differs_if_computed_only_before_sigma=mode_diff, seed=671)
    res["ok"] = n >= 500 and bad_f == bad_t == bad_q == 0
    save("causal", res)
    return 0 if res["ok"] else 1


def bf_lag():
    return bd.bf.LAG_S


def cmd_bench(a):
    """Время на плотных реальных сигналах wc (long): монеты как в срезе 10b (каждая 10-я по порядку имён), доля строк ≈ плотность производства (≈ 13 строк на монето-сутки в ω)."""
    w = WIN["wc"]
    t = pd.read_csv(B0 % ("wc", "long"), usecols=["sym", "sig_ms"])
    coins = sorted(t["sym"].unique())[::10][:a.coins]
    t = t[t["sym"].isin(set(coins))]
    s = w.t0_ms / 1000.0 + t["sig_ms"].to_numpy(dtype=np.int64) / 1000.0
    ok, _ = bd.omega_mask(s)
    t = t[ok]
    days = len(set(zip(t["sym"], (s[ok] // 86400).astype(int))))
    rng = np.random.default_rng(5)
    want = 13 * len(coins) * 40                                                  # 13 строк на монето-сутки × ~40 суток ω окна wc
    t = t.iloc[np.sort(rng.choice(len(t), size=min(want, len(t)), replace=False))]
    rows = rows_from_keys("wc", "long", t["sym"].tolist(), t["sig_ms"].to_numpy(dtype=np.int64))
    st_ = {}
    t0 = time.time()
    got = bd.compute(rows, "wc", "long", procs=a.procs, stats=st_)
    sec = time.time() - t0
    cd = len(set(zip(rows["sym"], (rows["sig_ms"].to_numpy() + w.t0_ms) // 86_400_000)))
    res = dict(rows=len(rows), coins=len(coins), coin_days_with_rows=cd, procs=a.procs, seconds=round(sec, 2), ms_per_row=round(1000 * sec / len(rows), 4),
               ms_per_row_cpu=round(1000 * sec * a.procs / len(rows), 4), cpu_s_per_coin_day=round(sec * a.procs / max(cd, 1), 4),
               nonnan={c: round(float(got[c].notna().mean()), 4) for c in bd.COLS}, stats=st_)
    # то же однопроцессно — для разделения «загрузка суток» / «признаки»
    t0 = time.time()
    bd.compute(rows.iloc[: len(rows) // 4], "wc", "long", procs=1)
    res["sec_quarter_1proc"] = round(time.time() - t0, 2)
    save("bench", res)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("turn", "eq48", "eq", "causal", "bench"))
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--coins", type=int, default=71)
    ap.add_argument("--procs", type=int, default=3)
    a = ap.parse_args()
    return {"turn": cmd_turn, "eq48": cmd_eq48, "eq": cmd_eq, "causal": cmd_causal, "bench": cmd_bench}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
