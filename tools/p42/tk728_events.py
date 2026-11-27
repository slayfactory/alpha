#!/usr/bin/env python3
"""П-42 этап A, шаг 4г (TK-728, §13.78.4 + PS-1060 п. 14): есть ли у монеты память исходов пробоев. srv.

События — закрытые пересечения уровня по монете и стороне из `tk625_events.py` (`/data/p42-tk625/ev-h775/<окно>.csv`): независимые эпизоды (`indep = 1`), `ok_ev = 1`, есть `cross_ms`,
монеты c539, сигнал в зачётных сутках окна wa / wb / wc (01.07–13.11). «Успех» — `r10 = 'o'` (пробой на 1 NATR раньше возврата к уровню; метка 1 NATR — PS-1060 п. 14), прочее (`f`, `s`) — «нет».
Момент исхода — `t10` (успех / возврат — время сделки; `s` — пересечение + 60 мин), абсолютное время = T0 окна + значение.
Предыдущее событие — последнее более раннее событие той же монеты и той же стороны, исход которого известен к сигналу текущего (`t10_пред ≤ sig_тек`) и которое лежит в том же слое.
Слой — «монета × календарный месяц сигнала» (UTC), обе стороны вместе. Статистика — объединённое OR Мантеля–Хензеля (предыдущий исход × текущий) по слоям, интервал — формула Робинса–Бреслоу–Гринленда,
рядом p по 10 000 перестановкам исходов внутри слоя (одно зерно этапа); отдельно — сверхдисперсия долей успеха между монетами (бета-биномиал, метод моментов) по сторонам и вместе.
Правило (до счёта): нижняя граница OR > 1 → память есть, семейство заменяется долей с усадкой; иначе — убирается целиком.
  python3 tk728_events.py --out /data/p42-tk728/res/events.json [--db /data/p42-tk728/res728.sqlite] [--perm 10000]"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk728_lib as L  # noqa: E402
import tk728_db as D  # noqa: E402

EV_DIR = "/data/p42-tk625/ev-h775"
CRYPTO = "/data/p42-tk621/universe/symbols-crypto.txt"
DAY_MS = 86_400_000
# окна: (T0, первые и последние зачётные сутки) — `tk604_stats.WA / WB / WC`
WINS = {"wa": ("2026-06-17", "2026-07-01", "2026-08-25"), "wb": ("2026-07-25", "2026-08-26", "2026-10-02"), "wc": ("2026-09-01", "2026-10-03", "2026-11-13")}
RUN = "A4"


def ms_of(d: str) -> int:
    return int(pd.Timestamp(d, tz="UTC").timestamp() * 1000)


def load_events(coins):
    """События SEL: DataFrame sym, side, sig (абс. мс), tout (абс. мс), succ (0/1)."""
    parts = []
    for w, (t0, lo, hi) in WINS.items():
        ev = pd.read_csv(os.path.join(EV_DIR, w + ".csv"), usecols=["side", "sym", "level", "sig_ms", "indep", "ok_ev", "cross_ms", "r10", "t10"])
        base = ms_of(t0)
        ev = ev[(ev.indep == 1) & (ev.ok_ev == 1) & ev.cross_ms.notna() & ev.sym.isin(coins)].copy()
        ev["sig"] = ev["sig_ms"].astype(np.int64) + base
        ev = ev[(ev["sig"] >= ms_of(lo)) & (ev["sig"] < ms_of(hi) + DAY_MS)]
        if ev["r10"].isna().any() or ev["t10"].isna().any():
            raise SystemExit("G-data %s: у пересечения нет исхода r10 / t10" % w)
        if not set(ev["r10"].unique()) <= {"o", "f", "s"}:
            raise SystemExit("G-data %s: r10 вне {o, f, s}: %s" % (w, sorted(set(ev["r10"].unique()))))
        ev["tout"] = ev["t10"].astype(np.int64) + base
        ev["succ"] = (ev["r10"] == "o").astype(np.int8)
        ev["win"] = w
        parts.append(ev[["win", "sym", "side", "level", "sig", "tout", "succ"]])
    return pd.concat(parts, ignore_index=True)


def build_pairs(ev: pd.DataFrame):
    """Сортировка по слою, предыдущее событие. Возвращает (ev_sorted, prev_idx, cur_idx): индексы — позиции в отсортированной таблице; пары — только внутри слоя."""
    ev = ev.copy()
    ev["month"] = pd.to_datetime(ev["sig"], unit="ms", utc=True).dt.strftime("%Y-%m")
    ev["stratum"] = ev.groupby(["sym", "month"]).ngroup()
    ev = ev.sort_values(["stratum", "side", "sig", "level"], kind="stable").reset_index(drop=True)
    prev = np.full(len(ev), -1, dtype=np.int64)
    for (_, _, _), idx in ev.groupby(["sym", "side", "month"], sort=False).indices.items():
        idx = np.asarray(idx)
        sig, tout = ev["sig"].to_numpy()[idx], ev["tout"].to_numpy()[idx]
        for k in range(1, len(idx)):
            ok = np.nonzero(tout[:k] <= sig[k])[0]
            if len(ok):
                prev[idx[k]] = idx[ok[-1]]
    cur = np.nonzero(prev >= 0)[0]
    return ev, prev[cur], cur


def check_pairs(ev, prev_idx, cur_idx, k=400):
    """Проверка метода до чтения результата (G-pairs): для k случайных событий предыдущее событие, найденное прямым перебором по ключу (сигнал, уровень), равно найденному `build_pairs`;
    предыдущее известно к сигналу (`tout ≤ sig`) и лежит в том же слое."""
    prev = np.full(len(ev), -1, dtype=np.int64)
    prev[cur_idx] = prev_idx
    rng = np.random.default_rng(L.SEED)
    sym, side, mon = (ev[c].to_numpy() for c in ("sym", "side", "month"))
    sig, tout, lvl = (ev[c].to_numpy().astype(np.int64) for c in ("sig", "tout", "level"))
    for i in rng.choice(len(ev), size=min(k, len(ev)), replace=False):
        grp = (sym == sym[i]) & (side == side[i]) & (mon == mon[i])
        earlier = grp & ((sig < sig[i]) | ((sig == sig[i]) & (lvl < lvl[i]))) & (tout <= sig[i])
        cand = np.nonzero(earlier)[0]
        best = -1
        if len(cand):
            order = np.lexsort((lvl[cand], sig[cand]))
            best = int(cand[order[-1]])
        if best != prev[i] and not (best >= 0 and prev[i] >= 0 and sig[best] == sig[prev[i]] and lvl[best] == lvl[prev[i]]):
            raise SystemExit("G-pairs: событие %d: перебор %d, build_pairs %d" % (i, best, prev[i]))
    if (tout[prev_idx] > sig[cur_idx]).any() or (ev["stratum"].to_numpy()[prev_idx] != ev["stratum"].to_numpy()[cur_idx]).any():
        raise SystemExit("G-pairs: предыдущее событие неизвестно к сигналу или из другого слоя")
    print("G-pairs ok: %d событий сверено перебором" % min(k, len(ev)), flush=True)


def summarize(ev, prev_idx, cur_idx, nperm, seed, with_perm=True):
    lab = ev["succ"].to_numpy().astype(bool)
    strat = ev["stratum"].to_numpy()
    strat_pair = strat[cur_idx]
    a, b, c, d = L.mh_cells(lab[prev_idx], lab[cur_idx], strat_pair, int(strat.max()) + 1)
    orr, lo, hi, se = L.mh_or_ci(a, b, c, d)
    res = dict(events=int(len(ev)), pairs=int(len(cur_idx)), strata=int((a + b + c + d > 0).sum()), success_share=float(lab.mean()),
               cells=dict(a=int(a.sum()), b=int(b.sum()), c=int(c.sum()), d=int(d.sum())), or_mh=orr, or_lo=lo, or_hi=hi, se_ln_or=se)
    pc = lab[prev_idx]
    res["p_succ_after_succ"] = float(lab[cur_idx][pc].mean()) if pc.any() else float("nan")
    res["p_succ_after_fail"] = float(lab[cur_idx][~pc].mean()) if (~pc).any() else float("nan")
    if with_perm:
        p1, p2, out = L.mh_perm_p(lab, prev_idx, cur_idx, strat_pair, strat, int(strat.max()) + 1, orr, nperm, seed)
        fin = out[np.isfinite(out)]
        res.update(perm_n=nperm, perm_p_one=p1, perm_p_two=p2, perm_or_mean=float(fin.mean()), perm_or_q025=float(np.percentile(fin, 2.5)), perm_or_q975=float(np.percentile(fin, 97.5)))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--perm", type=int, default=L.N_PERM)
    a = ap.parse_args()
    t0 = time.time()
    coins = set(x for x in open(CRYPTO, encoding="utf-8").read().replace("\n", ",").split(",") if x)
    ev = load_events(coins)
    print("событий %d (%.0f с)" % (len(ev), time.time() - t0), flush=True)
    evs, prev_idx, cur_idx = build_pairs(ev)
    check_pairs(evs, prev_idx, cur_idx)                 # проверка метода — до первого чтения результата
    main_res = summarize(evs, prev_idx, cur_idx, a.perm, L.SEED)
    print("основной тест: OR %.4f [%.4f; %.4f], p %.4f (%.0f с)" % (main_res["or_mh"], main_res["or_lo"], main_res["or_hi"], main_res["perm_p_one"], time.time() - t0), flush=True)
    by_side = {}
    for side in ("long", "short"):
        e2b, p2, c2 = build_pairs(evs[evs.side == side].drop(columns=["stratum", "month"]).reset_index(drop=True))
        by_side[side] = summarize(e2b, p2, c2, 0, L.SEED, with_perm=False)
    by_month = {}
    for m, sub in evs.groupby("month"):
        if len(sub) < 1000:
            continue
        e2 = sub.drop(columns=["stratum", "month"]).reset_index(drop=True)
        e2b, p2, c2 = build_pairs(e2)
        by_month[m] = summarize(e2b, p2, c2, 0, L.SEED, with_perm=False)
    od = {}
    for scope, sub in (("все", evs), ("long", evs[evs.side == "long"]), ("short", evs[evs.side == "short"])):
        g = sub.groupby("sym")["succ"].agg(["sum", "count"])
        od[scope] = L.overdispersion(g["sum"].to_numpy(), g["count"].to_numpy())
    rule = bool(main_res["or_lo"] > 1.0)
    out = dict(main=main_res, by_side=by_side, by_month=by_month, overdispersion=od, rule_memory=rule, n_events_by_win=ev.groupby("win").size().to_dict(),
               success_by_win=ev.groupby("win")["succ"].mean().to_dict(), seed=L.SEED, seconds=round(time.time() - t0))
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.db:
        db = D.connect(a.db)
        D.new_part(db, RUN, ("t728_mem",), "TK-728 шаг 4г: память исходов", "слои монета × месяц, 10 000 перестановок, зерно %d" % L.SEED)
        rows = [("main", "or_mh", main_res["or_mh"], main_res["or_lo"], main_res["or_hi"], main_res["pairs"], json.dumps(main_res, ensure_ascii=False))]
        rows += [("main", "perm_p_one", main_res["perm_p_one"], None, None, a.perm, None), ("main", "perm_p_two", main_res["perm_p_two"], None, None, a.perm, None)]
        rows += [("side:" + k, "or_mh", v["or_mh"], v["or_lo"], v["or_hi"], v["pairs"], json.dumps(v, ensure_ascii=False)) for k, v in by_side.items()]
        rows += [("month:" + k, "or_mh", v["or_mh"], v["or_lo"], v["or_hi"], v["pairs"], json.dumps(v, ensure_ascii=False)) for k, v in by_month.items()]
        rows += [("overdispersion:" + k, "ratio", v["ratio"], None, None, v["n_coins"], json.dumps(v, ensure_ascii=False)) for k, v in od.items()]
        D.put(db, "t728_mem", RUN, rows)
        D.text(db, RUN, "mem_rule", dict(rule_memory=rule, or_lo=main_res["or_lo"]))
        db.commit()
    print(json.dumps(dict(rule_memory=rule, **{k: main_res[k] for k in ("events", "pairs", "strata", "or_mh", "or_lo", "or_hi", "perm_p_one")}), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
