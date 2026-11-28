#!/usr/bin/env python3
"""TK-735, §13.79 п. 6, п. 8: счёт шага 9в — этапы. Запускается юнитом на srv (`/data/tk592/venv/bin/python`, PYTHONPATH=/data/p42-tk735/code:/data/p42-tk728/code).

  tk735_run.py prep      # база режимов (результат r, сутки режима, тип, признаки) → res/base-{FJ,SEL}.npz; G-join, G-day; σ̂, σ̂_t, MDE(f) → res/g_prep.json. Правила не читаются.
  tk735_run.py decide    # бутстреп по суткам (блоки ⌈nd^{1/3}⌉ и 21, 20 000): Δ_A, Δ_D, Δ_X, Δ_{D,X}, Δ_Б, M, PBO — пул и по типам → res/decide-{L40,LF}.json (до исходов).
  tk735_run.py search    # поиск на 100 сплитах × тип × настройка (3 запуска), оценка на W_s и C_s → res/search-{режим}-{настройка}.npz (суточные массивы для бутстрепа, правила, PBO-ранги). Читает P&L правил.

Этап `prep` читает P&L только всей базы (σ̂, MDE — разброс базы, не результат правила; §13.79 п. 6). Поиск и оценка правил — следующие этапы (ещё не написаны).
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk727_s5 as S5  # noqa: E402
import tk735_lib as T  # noqa: E402
import tk735_search as SR  # noqa: E402

RES = os.path.join(T.ROOT, "res")
TK726_DB = "/data/p42-tk726/tk726.sqlite"
FREEZE = os.path.join(T.ROOT, "freeze", "tk735-frozen.json")
BLOCKS = lambda nd: (math.ceil(nd ** (1 / 3)), 21)          # блоки бутстрепа: ⌈n^{1/3}⌉ и 21 сутки (§13.79.3)
MDE_FRACS = (0.05, 0.10, 0.20, 0.40)
MDE_SAMPLES = 20
MDE_BOOT = 2000                                              # повторов бутстрепа для MDE (основной бутстреп — 20 000; MDE — ориентир)


def build_base():
    """Уникальные сделки базы + признаки φ (res/trade_feat.npz): склейка по строкам с проверкой (sym, sig_ms, eday) — G-join. → {режим: dict массивов}, отчёт G-join."""
    u = T.unique_trades()
    tf = np.load(os.path.join(RES, "trade_feat.npz"), allow_pickle=False)
    join = dict(n_trades=int(len(u["sym"])), n_feat=int(len(tf["sym"])))
    ok = join["n_trades"] == join["n_feat"]
    if ok:
        ok = bool((u["sym"] == tf["sym"]).all() and (u["sig_ms"] == tf["sig_ms"]).all() and (u["eday"] == tf["eday"]).all() and (u["regime"] == tf["regime"]).all())
    join["passed"] = bool(ok)
    if not ok:
        return None, join
    X = np.column_stack([tf[k] for k in T.PHI])
    fz = json.load(open(FREEZE, encoding="utf-8"))
    base = {}
    for reg in ("FJ", "SEL"):
        sp = np.load(os.path.join(RES, "splits-%s.npz" % reg), allow_pickle=False)
        syms = [str(x) for x in sp["syms"]]
        sym_typ = {syms[i]: int(sp["typ"][i]) for i in range(len(syms))}
        m = u["regime"] == reg
        lo, hi = T.REG[reg]
        sym = u["sym"][m]
        base[reg] = dict(sym=sym, day=(u["eday"][m] - lo).astype(np.int64), r=u["pnl"][m].astype(np.float64), kind=u["kind"][m], in_anchor=u["in_anchor"][m],
                         typ=np.array([sym_typ.get(str(s), -1) for s in sym], np.int64), X=X[m], nd=hi - lo, sig_ms=u["sig_ms"][m],
                         coin=np.array([syms.index(str(s)) if str(s) in sym_typ else -1 for s in sym], np.int64), syms=np.array(syms))
        join[reg] = dict(n=int(m.sum()), n_typed=int((base[reg]["typ"] >= 0).sum()), K=fz["regimes"][reg].get("K"))
    return base, join


def g_day():
    """G-day (§13.79 п. 8): суточные ряды склейки окон = `tk726_day` до микродоллара — по (клетка семейства, сутки): SEL (wa + wb + wc) и N1. Для FJ и января эталонной таблицы нет."""
    tr = T.load_trades_all(T.FAM)
    names = S5.WIN_NAMES
    con = sqlite3.connect("file:%s?mode=ro" % TK726_DB, uri=True)
    out = {}
    for label, wins in (("sel", ("wa", "wb", "wc")), ("n1", ("n1",))):
        wi = np.array([names.index(w) for w in wins])
        m = np.isin(tr["win"], wi)
        mine = {}
        for c, d, p in zip(tr["cid"][m], tr["eday"][m], tr["pnl"][m]):
            k = (int(c), int(d))
            s, n = mine.get(k, (0.0, 0))
            mine[k] = (s + float(p), n + 1)
        ref = {(int(c), int(d)): (float(p), int(n)) for c, d, p, n in con.execute("SELECT cid, day, pnl, n FROM tk726_day WHERE win=? AND cid IN (%s)" % ",".join(str(c) for c in T.FAM), (label,))}
        keys = set(mine) | set(ref)
        maxd = max((abs(mine.get(k, (0.0, 0))[0] - ref.get(k, (0.0, 0))[0]) for k in keys), default=0.0)
        nbad = sum(1 for k in keys if mine.get(k, (0, 0))[1] != ref.get(k, (0, 0))[1])
        miss = [k for k in keys if (k in mine) != (k in ref)]
        out[label] = dict(n_cells_days=len(keys), max_abs_diff_usd=float(maxd), n_count_mismatch=int(nbad), n_missing=len(miss), passed=bool(maxd < 1e-6 and nbad == 0 and not miss))
    out["passed"] = bool(out["sel"]["passed"] and out["n1"]["passed"])
    return out


def block_half_width(S_d, N_d, nd, block, nboot, seed):
    """Полуширина 95 %-интервала среднего r подмножества: блочный (круговой) бутстреп по суткам; средний r = Σ r / Σ n по повтору."""
    W = SR.day_weights(nd, block, nboot, seed)
    num, den = W @ S_d.astype(np.float32), W @ N_d.astype(np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = num / den
    m = m[np.isfinite(m)]
    return float((np.percentile(m, 97.5) - np.percentile(m, 2.5)) / 2.0) if len(m) > 10 else float("nan")


def mde_table(b, reg):
    """MDE(f) по долям f монет базы режима: MDE_SAMPLES случайных подмножеств монет, полуширина по обоим блокам; среднее по выборкам (печать, ворот нет)."""
    nd = b["nd"]
    coins = np.unique(b["coin"][b["coin"] >= 0])
    out = {}
    for f in MDE_FRACS:
        hw = {blk: [] for blk in BLOCKS(nd)}
        ns = []
        for s in range(MDE_SAMPLES):
            rng = np.random.default_rng(T.SEED + 1000 * int(f * 100) + s)
            pick = rng.choice(coins, max(1, int(round(f * len(coins)))), replace=False)
            m = np.isin(b["coin"], pick)
            S_d, N_d = T.day_series(b["day"][m], b["r"][m], nd)
            ns.append(int(m.sum()))
            for blk in hw:
                hw[blk].append(block_half_width(S_d, N_d, nd, blk, MDE_BOOT, T.SEED + s))
        out[str(f)] = dict(n_trades_mean=float(np.mean(ns)), **{"block_%d" % blk: float(np.nanmean(v)) for blk, v in hw.items()})
    return out


# ───────────────────────────── поиск и оценка на сплитах (§13.79.2–§13.79.3) ─────────────────────────────

def day_arrays(M, r, day, nd, idx, rule):
    """Суточные массивы набора сделок `idx` (индексы в базе режима) под правилом: A = Σ r отобранных, N = их число, Bn = Σ n_отб · m̄_все(сутки) (нуль А), C = Σ r всех, E = число всех."""
    idx = np.asarray(idx, np.int64)
    mask = SR.rule_mask(M[:, idx], rule)
    E, C = SR.agg_days(day[idx], r[idx], np.ones(len(idx), bool), nd)
    N, A = SR.agg_days(day[idx], r[idx], mask, nd)
    with np.errstate(invalid="ignore", divide="ignore"):
        mbar = np.where(E > 0, C / E, 0.0)
    return dict(A=A, N=N, Bn=N * mbar, C=C, E=E)


def canon(rule):
    """Правило в порядке пространства процедуры (по номеру признака)."""
    return tuple(sorted(rule, key=lambda ci: (int(T.COND_FEAT[ci]), ci)))


def eval_split(M, r, day, nd, B, W, Cc, sigma_t, setting, rules, rule_pos):
    """Один (тип, сплит, настройка): три запуска поиска на B_s (все / чётные / нечётные сутки), оценка R_s на W_s и случайных монетах C_s, перекрёстный двойник, ранг PBO.
    Чётность — по номеру суток режима (первые = 1): чётные номера — индексы суток 1, 3, … (day % 2 == 1)."""
    even = (day % 2 == 1)
    R = SR.search(M, r, day, B, sigma_t, setting)
    Be, Bo = B[even[B]], B[~even[B]]
    Re = SR.search(M, r, day, Be, sigma_t, setting) if len(Be) else ()
    Ro = SR.search(M, r, day, Bo, sigma_t, setting) if len(Bo) else ()
    out = dict(rule=R, rule_even=Re, rule_odd=Ro, n_B=int(len(B)), n_W=int(len(W)))
    if R:
        out["same"] = day_arrays(M, r, day, nd, W, R)
        out["c"] = day_arrays(M, r, day, nd, Cc, R)                         # нуль Б: те же правило и пороги на случайных монетах
        RM = T.rule_matrix(M[:, W], rules)
        rank, adm = SR.pbo_rank(RM, r[W], day[W], nd, sigma_t, setting, len(W), rule_pos[canon(R)])
        out["pbo_rank"], out["pbo_adm"] = rank, bool(adm)
    cross = {}
    for j, (rule_x, par) in enumerate(((Re, even), (Ro, ~even))):          # правило чётных суток — на нечётных сутках W_s (j = 0) и наоборот (j = 1)
        if rule_x:
            Wx = W[~par[W]]
            cross[j] = day_arrays(M, r, day, nd, Wx, rule_x)
    out["cross"] = cross
    return out


def cmd_search(a):
    """Поиск и оценка: для каждого режима, настройки, типа, 100 сплитов → суточные массивы (по типам и пулом), правила, PBO. Файл на (режим, настройка)."""
    fz = json.load(open(FREEZE, encoding="utf-8"))
    rules = T.rule_space()
    assert len(rules) == T.EXPECT_RULES, len(rules)
    rule_pos = {ru: i for i, ru in enumerate(rules)}
    g = json.load(open(os.path.join(RES, "g_prep.json"), encoding="utf-8"))
    for reg in ("FJ", "SEL"):
        z = np.load(os.path.join(RES, "base-%s.npz" % reg), allow_pickle=False)
        r, day, typ, coin, X, nd = z["r"], z["day"], z["typ"], z["coin"], z["X"], int(z["nd"])
        sp = np.load(os.path.join(RES, "splits-%s.npz" % reg), allow_pickle=False)
        info = fz["regimes"][reg]
        if "stop" in info:
            continue
        for setting in ("L40", "LF"):
            t0 = time.time()
            res = dict(nd=nd, K=info["K"])
            for t in range(info["K"]):
                th = np.array([[np.nan if v is None else v for v in row] for row in info["thresholds"][str(t)]])
                M = T.cond_masks(X, th)
                sig_t = g["sigma"][reg]["type_%d" % t]["sigma_t"]
                keys = [("same", k) for k in ("A", "N", "Bn", "C", "E")] + [("c", k) for k in ("A", "N", "Bn")] + [("x", k) for k in ("A", "N", "Bn", "C", "E")]
                arr = {kk: np.zeros((T.NSPLIT, nd)) for kk in keys}
                arr_x = {k: np.zeros((2 * T.NSPLIT, nd)) for k in ("A", "N", "Bn", "C", "E")}
                rr, nsel, pbo = [], [], []
                for s in range(T.NSPLIT):
                    Bc, Wc, Cc_ = (sp["t%d_%s" % (t, k)][s] for k in ("B", "W", "C"))
                    B, W, Cc = (np.where(np.isin(coin, c))[0] for c in (Bc, Wc, Cc_))
                    o = eval_split(M, r, day, nd, B, W, Cc, sig_t, setting, rules, rule_pos)
                    rr.append([list(o["rule"]), list(o["rule_even"]), list(o["rule_odd"])])
                    if o["rule"]:
                        for k in ("A", "N", "Bn", "C", "E"):
                            arr[("same", k)][s] = o["same"][k]
                        for k in ("A", "N", "Bn"):
                            arr[("c", k)][s] = o["c"][k]
                        pbo.append((s, o["pbo_rank"], o["pbo_adm"]))
                        nsel.append(int(o["same"]["N"].sum()))
                    for j, cx in o["cross"].items():
                        for k in cx:
                            arr_x[k][2 * s + j] = cx[k]
                res["t%d" % t] = dict(rules=rr, pbo=pbo, nsel=nsel, **{"%s_%s" % kk: v for kk, v in arr.items() if kk[0] != "x"}, **{"x_%s" % k: v for k, v in arr_x.items()})
                print(reg, setting, "тип", t, "правил найдено %d / %d, медиана отобранных %s" % (len(pbo), T.NSPLIT, int(np.median(nsel)) if nsel else "-"), flush=True)
            np.save(os.path.join(RES, "search-%s-%s.npy" % (reg, setting)), np.array([res], dtype=object), allow_pickle=True)
            print(reg, setting, "готово за %.0f с" % (time.time() - t0), flush=True)


# ───────────────────────────── оценки с бутстрепом по суткам (§13.79.3) ─────────────────────────────

def pooled(res, types, prefix, keys):
    """Сумма массивов по типам `types` (сплит-тип без правила — нули: не входит ни в числитель, ни в m^{все})."""
    return {k: sum(res["t%d" % t]["%s_%s" % (prefix, k)] for t in types) for k in keys}


def delta_block(res, types, nd, block, nboot=20000, seed=T.SEED):
    """Δ_A, Δ_D (W_s, те же сутки), Δ_X, Δ_{D,X} (перекрёстный двойник) для набора типов: точка, интервал и односторонний p блочного бутстрепа; Δ_Б — точка; M — среднее m_s."""
    W = SR.day_weights(nd, block, nboot, seed)
    one = np.ones((1, nd), np.float32)
    same = pooled(res, types, "same", ("A", "N", "Bn", "C", "E"))
    x = pooled(res, types, "x", ("A", "N", "Bn", "C", "E"))
    out = {}
    for name, d in (("same", same), ("x", x)):
        args = (d["A"], d["N"], d["Bn"], d["C"], d["E"])
        rep_a, rep_d = SR.delta_rep(W, *args)
        pt_a, pt_d = (float(v[0]) for v in SR.delta_rep(one, *args))
        key_a, key_d = ("dA", "dD") if name == "same" else ("dX", "dDX")
        out[key_a] = dict(SR.interval(rep_a, pt_a), p=SR.p_one_sided(rep_a, pt_a), ub_bonf4=float(np.nanpercentile(rep_a, 100 * (1 - 0.05 / 4))), ub_bonf16=float(np.nanpercentile(rep_a, 100 * (1 - 0.05 / 16))))
        out[key_d] = dict(SR.interval(rep_d, pt_d), p=SR.p_one_sided(rep_d, pt_d))
    c = pooled(res, types, "c", ("A", "N", "Bn"))
    nb = c["N"].sum(1)
    ok = nb > 0
    out["dB"] = float(np.mean((c["A"].sum(1)[ok] - c["Bn"].sum(1)[ok]) / nb[ok])) if ok.any() else float("nan")
    nn = same["N"].sum(1)
    okm = nn > 0
    out["M"] = float(np.mean(same["A"].sum(1)[okm] / nn[okm])) if okm.any() else float("nan")
    out["n_splits_rule"] = int(okm.sum())
    return out


def pbo_share(res, types):
    """PBO: доля пар (сплит, тип) с правилом, где ранг R_s в нижней половине (ранг / допустимых > 0,5) или правило недопустимо на W_s (L-525 п. 2)."""
    n = bad = 0
    for t in types:
        for _s, rank, adm in res["t%d" % t]["pbo"]:
            n += 1
            bad += int((not adm) or rank > 0.5)
    return dict(share=bad / n if n else float("nan"), n=n)


def cmd_decide(a):
    """Бутстреп и оценки по (режим, настройка): пул и по типам, оба блока → res/decide-{настройка}.json. Исходы O1–O7 — отдельным этапом (читать после этого)."""
    for setting in ("L40", "LF"):
        rep = {}
        for reg in ("FJ", "SEL"):
            path = os.path.join(RES, "search-%s-%s.npy" % (reg, setting))
            if not os.path.exists(path):
                continue
            res = np.load(path, allow_pickle=True)[0]
            nd, K = int(res["nd"]), int(res["K"])
            rep[reg] = dict(nd=nd, K=K, blocks={})
            for blk in BLOCKS(nd):
                t0 = time.time()
                rep[reg]["blocks"][str(blk)] = dict(pool=delta_block(res, list(range(K)), nd, blk), types={str(t): delta_block(res, [t], nd, blk) for t in range(K)})
                print(reg, setting, "блок", blk, "готово за %.0f с" % (time.time() - t0), flush=True)
            rep[reg]["pbo"] = dict(pool=pbo_share(res, list(range(K))), types={str(t): pbo_share(res, [t]) for t in range(K)})
        json.dump(rep, open(os.path.join(RES, "decide-%s.json" % setting), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("decide", setting, "записано")


def cmd_prep(a):
    t0 = time.time()
    base, join = build_base()
    print("G-join:", json.dumps(join, ensure_ascii=False))
    if base is None:
        sys.exit("G-join не пройден: стоп, запись amend (§13.79 п. 8)")
    gd = g_day()
    print("G-day:", json.dumps(gd, ensure_ascii=False))
    if not gd["passed"]:
        json.dump(dict(join=join, g_day=gd), open(os.path.join(RES, "g_prep.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        sys.exit("G-day не пройден: стоп, запись amend (§13.79 п. 8)")
    rep = dict(join=join, g_day=gd, sigma={}, mde={})
    for reg, b in base.items():
        np.savez_compressed(os.path.join(RES, "base-%s.npz" % reg), **{k: v for k, v in b.items() if k != "nd"}, nd=np.array(b["nd"]))
        sig = dict(all=float(b["r"].std(ddof=1)), n=int(len(b["r"])))
        for t in sorted(set(b["typ"].tolist())):
            if t >= 0:
                mt = b["typ"] == t
                sig["type_%d" % t] = dict(sigma_t=float(b["r"][mt].std(ddof=1)), n=int(mt.sum()))
        rep["sigma"][reg] = sig
        rep["mde"][reg] = mde_table(b, reg)
        print(reg, "σ̂ =", json.dumps(sig, ensure_ascii=False))
        print(reg, "MDE(f) =", json.dumps(rep["mde"][reg], ensure_ascii=False))
    rep["seconds"] = round(time.time() - t0)
    json.dump(rep, open(os.path.join(RES, "g_prep.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("prep готово за %d с" % rep["seconds"])


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("prep", "search", "decide"))
    a = ap.parse_args()
    {"prep": cmd_prep, "search": cmd_search, "decide": cmd_decide}[a.cmd](a)


if __name__ == "__main__":
    main()
