#!/usr/bin/env python3
"""TK-736 (П-42 шаг 7, §13.80.2 «что печатается рядом», §13.80.3): описательные таблицы после суда — метрики V0 / V2′ по направлениям, склейка «вне выборки», пул k_pool (все окна),
N1 и январь («первые 2 суток окна» отдельно), якорь s4, реплей против движка, G-cap, единицы k (RV60 против NATR30). Решений не двигает; только srv.

  python3 tk736_desc.py     # → res/desc.json и res/report.txt
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk726_seam as sm  # noqa: E402
import tk727_s5lib as S5  # noqa: E402
import tk736_an as A  # noqa: E402
import tk736_lib as L  # noqa: E402

M3_BOOT = 1000           # M3 — описательно: 1 000 повторов (суд — 20 000)
SCOPE = {
    "A": dict(stage="v2fj", wins=A.SEL, lo=181, hi=316, kk="k_FJ", name="A: k_FJ → SEL"),
    "B": dict(stage="v2sel", wins=A.FJ, lo=31, hi=180, kk="k_SEL", name="B: k_SEL → FJ"),
    "pool": dict(stage="v2pool", wins=A.FJ + A.SEL, lo=31, hi=316, kk="k_pool", name="пул: k_pool на FJ ∪ SEL"),
    "jan": dict(stage="v2pool", wins=("m01",), lo=0, hi=30, kk="k_pool", name="январь: k_pool"),
    "n1": dict(stage="v2pool", wins=("n1",), lo=317, hi=327, kk="k_pool", name="N1: k_pool"),
}
S_PM = {"short": 4000, "long": 3000}
OUT = []                 # строки текстового отчёта


def say(*a):
    s = " ".join(str(x) for x in a)
    OUT.append(s)
    print(s)


def get_cells(side, var, sc):
    """Сделки по клеткам (9 у шорта, 1 у лонга) на окнах области; None — нет файлов (лонг на n1)."""
    wins, stage = SCOPE[sc]["wins"], SCOPE[sc]["stage"]
    if var == "v0":
        cs = [A.concat([A.disk_v0(cid, w) for w in wins]) for _, cid in A.b9_cids()] if side == "short" else [A.concat([A.run_cell("v0long", 7361001, w) for w in wins])]
    else:
        base = 7360000 + 1000 * A.STAGE_NO[stage]
        cs = [A.concat([A.run_cell(stage, base + 1 + j, w) for w in wins]) for j in range(9)] if side == "short" else [A.concat([A.run_cell(stage, base + 10, w) for w in wins])]
    return None if any(c is None for c in cs) else cs


def series(cells, lo, hi):
    ps, ns = zip(*[A.day_series(c, lo, hi) for c in cells])
    return np.mean(ps, axis=0), np.mean(ns, axis=0)


def labels(lo, hi):
    """Метки периодов суток [lo, hi]: половины — 5 периодов, пул — 10 (фев…окт, 1–13.11)."""
    if (lo, hi) in ((31, 180), (181, 316)):
        return A.period_labels(lo, hi)
    if (lo, hi) == (31, 316):
        import tk726_exits as ex
        days = np.arange(lo, hi + 1)
        lab = np.full(len(days), -1)
        per = [p for p in ex.PERIODS if p[0] not in ("Jan", "N1")]
        for j, (_, a_, b_) in enumerate(per):
            lab[(days >= sm.day_of(a_)) & (days <= sm.day_of(b_))] = j
        assert (lab >= 0).all() and len(per) == 10
        return lab, 10
    return None, None


def metrics(cells, lo, hi, lab=None, nper=None):
    p, n = series(cells, lo, hi)
    u = L.uw_stats(p)
    r = np.concatenate([c["pnl"] for c in cells])
    kd = np.concatenate([c["kind"] for c in cells])
    st, tail = kd == "stop", r <= -10.0
    nc = len(cells)
    m = dict(sum=float(p.sum()), trades=float(n.sum()), per_trade=float(p.sum() / n.sum()) if n.sum() > 0 else None, sharpe=L.sharpe(p), dd=u["dd"], dd_pct=u["dd"] / L.MARGIN * 100.0,
             gap_longest=u["gap_longest"], gap_mean=u["gap_mean"], gap_p90=u["gap_p90"], pos_days=float((p > 0).mean()), rr=L.rr(r), worst=float(r.min()) if len(r) else None,
             stops_n=float(st.sum()) / nc, stops_sum=float(r[st].sum()) / nc, usd_per_stop=float(r[st].mean()) if st.any() else None, tail_n=float(tail.sum()) / nc, tail_sum=float(r[tail].sum()) / nc)
    if lab is not None:
        m["month"] = L.month_stats(p, n, lab, nper)
    return m, p, n


def pair_stats(c0, c2, side):
    """Парные сделки по ключу (клетка, sym, sig_ms, level): только в одном ряду; срезанные рабочим стопом (kind = stop, d < s) и что делал с ними V0."""
    s_pm = S_PM[side]
    npairs = only0 = only2 = 0
    cut = dict(n=0, sum2=0.0, paired=0, sum0=0.0, v0_win=0, v0_stop=0)
    for x0, x2 in zip(c0, c2):
        m0 = {k: i for i, k in enumerate(zip(x0["sym"].tolist(), x0["sig_ms"].tolist(), x0["level"].tolist()))}
        m2 = {k: i for i, k in enumerate(zip(x2["sym"].tolist(), x2["sig_ms"].tolist(), x2["level"].tolist()))}
        npairs += len(set(m0) & set(m2))
        only0 += len(set(m0) - set(m2))
        only2 += len(set(m2) - set(m0))
        vd = x2["vd"]
        for i in np.nonzero((x2["kind"] == "stop") & (vd >= 0) & (vd < s_pm))[0]:
            cut["n"] += 1
            cut["sum2"] += float(x2["pnl"][i])
            k = (x2["sym"][i], int(x2["sig_ms"][i]), int(x2["level"][i]))
            if k in m0:
                i0 = m0[k]
                cut["paired"] += 1
                cut["sum0"] += float(x0["pnl"][i0])
                cut["v0_win"] += bool(x0["pnl"][i0] > 0)
                cut["v0_stop"] += bool(x0["kind"][i0] == "stop")
    nc = len(c0)
    return dict(pairs=npairs, only_v0=only0, only_v2=only2, cut_n=cut["n"], cut_paired=cut["paired"], cut_sum_v2=cut["sum2"] / nc, cut_sum_v0_same=cut["sum0"] / nc, cut_v0_win=cut["v0_win"], cut_v0_stop=cut["v0_stop"], cells=nc)


def vstop_shares(c2, side):
    vd = np.concatenate([x["vd"] for x in c2])
    n = max(len(vd), 1)
    return dict(n=int(len(vd)), d_eq_vm=float((vd == 500).sum()) / n, d_eq_s=float((vd == S_PM[side]).sum()) / n, no_v=float((vd < 0).sum()) / n, d_between=float(((vd > 500) & (vd < S_PM[side])).sum()) / n)


def slope(cells, side, lo, hi, block):
    """M3: наклон α результата сделки ($) на V (п.п. RV60), фиксированные эффекты «сутки + монета» (как §13.78.5); уникальные сделки; интервал — бутстреп суток блоками (M3_BOOT повторов)."""
    allp = A.concat(cells)
    keep = L.dedup_first(allp["sym"], allp["sig_ms"], allp["entry_ms"], allp["exit_ms"], allp["pnl"])
    allp = sm.take(allp, keep)
    V, _, _, _ = A.v_and_adverse(allp, side)
    ok = V > 0
    x, r = V[ok] * 100.0, allp["pnl"][ok]
    day = (allp["eday"][ok] - lo).astype(np.int64)
    syms, coin = np.unique(allp["sym"][ok].astype(str), return_inverse=True)
    nd = hi - lo + 1
    X, Y = x[:, None], r[:, None]
    a = float(S5.fe_coefs(X, Y, day, nd, coin, len(syms))[0, 0])
    bt = S5.boot_fe(X, Y, day, coin, nd, len(syms), block, nboot=M3_BOOT, which=0)[:, 0]
    iv = S5.interval(bt, a)
    return dict(alpha=a, lo=iv["lo"], hi=iv["hi"], n=int(ok.sum()), mean_v_pp=float(x.mean()))


def replay_vs_engine(side, k, c0, c2):
    """Реплей (модель §13.80.1) против движка по тем же сделкам V0 для замороженного k: совпадение срезов и $ на срезанных (описательно, критик п. 10)."""
    s_pm = S_PM[side]
    both = only_rep = only_eng = neither = 0
    sum_eng_b = sum_rep_b = sum_v0_b = 0.0
    sum_v0_or = sum_eng_or = sum_v0_oe = sum_eng_oe = 0.0
    for x0, x2 in zip(c0, c2):
        V, Ar, _, _ = A.v_and_adverse(x0, side)
        rr_, cut = L.replay(x0["pnl"], x0["fees"], Ar, V, k, L.S_CAP[side])
        m2 = {key: i for i, key in enumerate(zip(x2["sym"].tolist(), x2["sig_ms"].tolist(), x2["level"].tolist()))}
        for i, key in enumerate(zip(x0["sym"].tolist(), x0["sig_ms"].tolist(), x0["level"].tolist())):
            j = m2.get(key)
            if j is None:
                continue
            eng = bool(x2["kind"][j] == "stop" and 0 <= x2["vd"][j] < s_pm)
            rep = bool(cut[i])
            if rep and eng:
                both += 1
                sum_eng_b += float(x2["pnl"][j]); sum_rep_b += float(rr_[i]); sum_v0_b += float(x0["pnl"][i])
            elif rep:
                only_rep += 1
                sum_v0_or += float(x0["pnl"][i]); sum_eng_or += float(x2["pnl"][j])
            elif eng:
                only_eng += 1
                sum_v0_oe += float(x0["pnl"][i]); sum_eng_oe += float(x2["pnl"][j])
            else:
                neither += 1
    return dict(k=k, both=both, only_replay=only_rep, only_engine=only_eng, neither=neither, sum_engine_both=sum_eng_b, sum_replay_both=sum_rep_b, sum_v0_both=sum_v0_b,
                only_replay_v0=sum_v0_or, only_replay_engine=sum_eng_or, only_engine_v0=sum_v0_oe, only_engine_engine=sum_eng_oe)


def gcap(side, k, c0):
    """G-cap: распределение d_i(k) по уникальным сделкам V0 окон FJ ∪ SEL (пул): доли d = s, d = vm, d ≥ 0,019 (убыток ≥ 10 $ при 500 $), V = −1."""
    allp = A.concat(c0)
    allp = sm.take(allp, L.dedup_first(allp["sym"], allp["sig_ms"], allp["entry_ms"], allp["exit_ms"], allp["pnl"]))
    V, _, _, _ = A.v_and_adverse(allp, side)
    s = L.S_CAP[side]
    d = L.stop_distance(V, k, s)
    n = len(d)
    return dict(n=int(n), d_eq_s=float((d >= s - 1e-12).mean()), d_eq_vm=float((d <= L.VM + 1e-12).mean()), d_ge_0019=float((d >= 0.019).mean()), no_v=float((V < 0).mean()))


def units():
    """Единицы k (запрос координатора): ход против позиции выигрышных в NATR30 (шаг 2) и в RV60 на тех же сделках, отношение RV60 / NATR30."""
    import tk726_exits as ex
    out = {}
    for label, wins in (("anchor_s4_10", A.FJ + A.SEL), ("B9_unique_FJ_SEL", A.FJ + A.SEL)):
        cells = A.short_trades(wins)
        allp = cells["BS-l7-tf60"] if label.startswith("anchor") else A.concat(list(cells.values()))
        if not label.startswith("anchor"):
            allp = sm.take(allp, L.dedup_first(allp["sym"], allp["sig_ms"], allp["entry_ms"], allp["exit_ms"], allp["pnl"]))
        n = len(allp["pnl"])
        kn, kr, ratio = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
        for i in range(n):
            cd = A.candles(allp["win"][i], allp["sym"][i])
            o, h, l, c, rv = cd
            t = int(allp["sig_min"][i]) - 1
            vn = int(ex.natr_at(o, h, l, c, [t])[0]) / 1e8
            adv = L.adverse_move("short", h, l, o, int(allp["entry_min"][i]), int(allp["exit_min"][i]), allp["kind"][i] == "stop", int(allp["entry_px_e8"][i]))
            v = rv[t] if 0 <= t < len(rv) and np.isfinite(rv[t]) and rv[t] > 0 else np.nan
            if vn > 0 and np.isfinite(v):
                kn[i], kr[i], ratio[i] = adv / vn, adv / v, v / vn
        w = (allp["pnl"] > 0) & np.isfinite(kn)
        qs = (0.75, 0.90, 0.95, 0.99)
        out[label] = dict(n_win=int(w.sum()), q_natr30=[round(float(np.quantile(kn[w], q)), 2) for q in qs], q_rv60=[round(float(np.quantile(kr[w], q)), 2) for q in qs],
                          ratio_rv_natr_mean=float(ratio[w].mean()), ratio_q10_q50_q90=[round(float(np.quantile(ratio[w], q)), 3) for q in (0.1, 0.5, 0.9)],
                          q95_ratio=float(np.quantile(kr[w], 0.95) / np.quantile(kn[w], 0.95)))
    return out


def fmt(x, d=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else ("%.*f" % (d, x))


def line(tag, m):
    return "  %-5s Σ %s $ · сделок %s · %s $/сд · Шарп %s · просадка %s $ (%s %% маржи) · перехай %s / %s / %s сут · R:R %s · стопов %s (%s $, %s $/стоп) · худшая %s $ · плюс-суток %s %%" % (
        tag, fmt(m["sum"]), fmt(m["trades"], 1), fmt(m["per_trade"], 3), fmt(m["sharpe"]), fmt(m["dd"]), fmt(m["dd_pct"], 1), m["gap_longest"], fmt(m["gap_mean"], 1), fmt(m["gap_p90"], 1), fmt(m["rr"]),
        fmt(m["stops_n"], 1), fmt(m["stops_sum"]), fmt(m["usd_per_stop"]), fmt(m["worst"]), fmt(m["pos_days"] * 100, 1))


def month_line(m):
    mo = m["month"]
    return "        месяцы: M1 %d / %d · σ $/сд %s · среднее %s · σ/|среднее| %s · Σ по периодам %s" % (mo["m1"], mo["nper"], fmt(mo["sigma"], 3), fmt(mo["mean"], 3),
                                                                                                  fmt(mo["sigma"] / abs(mo["mean"]) if abs(mo["mean"]) >= 0.02 else float("inf"), 2), [round(x, 1) for x in mo["sum"]])


def run():
    fr = json.load(open(A.FREEZE, encoding="utf-8"))
    res = {}
    say("# TK-736 описательные таблицы (решений не двигают); ряд шорта — равновесный (среднее 9 клеток B9), лонг — якорь s3; $ при ставке 500 $, маржа 250 $")
    stitched = {}
    for side in ("short", "long"):
        res[side] = {}
        for sc in ("A", "B", "pool", "jan", "n1"):
            c0, c2 = get_cells(side, "v0", sc), get_cells(side, "v2", sc)
            if c0 is None or c2 is None:
                say("\n## %s · %s: нет данных (%s)" % (side, SCOPE[sc]["name"], "лонг на n1 не считается: нет таблицы"))
                continue
            lo, hi = SCOPE[sc]["lo"], SCOPE[sc]["hi"]
            lab, nper = labels(lo, hi)
            k = fr[side][SCOPE[sc]["kk"]]
            m0, p0, n0 = metrics(c0, lo, hi, lab, nper)
            m2, p2, n2 = metrics(c2, lo, hi, lab, nper)
            d = p2 - p0
            blocks = (L.block_len(len(d)), 21) if sc in ("A", "B") else (7, 21) if sc == "pool" else (L.block_len(len(d)),)
            ci = L.delta_ci(d, blocks)
            ps, vs = pair_stats(c0, c2, side), vstop_shares(c2, side)
            r = dict(k=k, v0=m0, v2=m2, delta_sum=float(d.sum()), delta_ci={str(b): v for b, v in ci.items()}, pairs=ps, vstop=vs)
            say("\n## %s · %s · k = %s" % (side, SCOPE[sc]["name"], k))
            say(line("V0", m0))
            if "month" in m0:
                say(month_line(m0))
            say(line("V2′", m2))
            if "month" in m2:
                say(month_line(m2))
            say("  Δ Σ (V2′ − V0) = %s $; 95 %% двусторонний, блоки %s: %s" % (fmt(float(d.sum())), "/".join(str(b) for b in blocks), "; ".join("[%s; %s]" % (fmt(v[0]), fmt(v[1])) for v in ci.values())))
            say("  парных %d · только V0 %d · только V2′ %d · срезано рабочим стопом (d<s) %d (парных %d; в V0 из них плюс %d, stop %d; Σ V2′ %s $ против Σ V0 %s $ на тех же сигналах; на клетку) · доли vstop: d=vm %s %%, d=s %s %%, V=−1 %s %%" % (
                ps["pairs"], ps["only_v0"], ps["only_v2"], ps["cut_n"], ps["cut_paired"], ps["cut_v0_win"], ps["cut_v0_stop"], fmt(ps["cut_sum_v2"]), fmt(ps["cut_sum_v0_same"]), fmt(vs["d_eq_vm"] * 100, 1), fmt(vs["d_eq_s"] * 100, 1), fmt(vs["no_v"] * 100, 1)))
            if sc in ("A", "B", "pool"):
                bl = L.block_len(hi - lo + 1)
                r["m3_v0"], r["m3_v2"] = slope(c0, side, lo, hi, bl), slope(c2, side, lo, hi, bl)
                say("  M3 α ($ на п.п. RV60, ФЭ сутки + монета; %d повторов): V0 %s [%s; %s] (n %d) · V2′ %s [%s; %s] (n %d)" % (M3_BOOT, fmt(r["m3_v0"]["alpha"], 3), fmt(r["m3_v0"]["lo"], 3), fmt(r["m3_v0"]["hi"], 3), r["m3_v0"]["n"],
                                                                                                               fmt(r["m3_v2"]["alpha"], 3), fmt(r["m3_v2"]["lo"], 3), fmt(r["m3_v2"]["hi"], 3), r["m3_v2"]["n"]))
            if sc in ("jan", "n1"):
                for tag, skip in (("без первых 2 суток окна", 2),):
                    dd = d[skip:]
                    cs = L.delta_ci(dd, (L.block_len(len(dd)),))
                    r["skip2"] = dict(sum0=float(p0[skip:].sum()), sum2=float(p2[skip:].sum()), delta=float(dd.sum()), ci={str(b): v for b, v in cs.items()})
                    say("  %s: Σ V0 %s $ · Σ V2′ %s $ · Δ %s $ [%s]" % (tag, fmt(float(p0[skip:].sum())), fmt(float(p2[skip:].sum())), fmt(float(dd.sum())), "; ".join("%s; %s" % (fmt(v[0]), fmt(v[1])) for v in cs.values())))
            if sc == "pool":
                r["replay_vs_engine"] = replay_vs_engine(side, k, c0, c2)
                rv = r["replay_vs_engine"]
                say("  реплей против движка (k_pool, те же сигналы): срез в обоих %d · только реплей %d · только движок %d · ни там ни там %d; на срезанных в обоих Σ движка %s $ против реплея %s $ (V0 %s $); только реплей: V0 %s $ / движок %s $; только движок: V0 %s $ / движок %s $" % (
                    rv["both"], rv["only_replay"], rv["only_engine"], rv["neither"], fmt(rv["sum_engine_both"]), fmt(rv["sum_replay_both"]), fmt(rv["sum_v0_both"]), fmt(rv["only_replay_v0"]), fmt(rv["only_replay_engine"]), fmt(rv["only_engine_v0"]), fmt(rv["only_engine_engine"])))
                r["gcap"] = {kk: gcap(side, fr[side][kk], c0) for kk in ("k_FJ", "k_SEL", "k_pool")}
                for kk, g in r["gcap"].items():
                    say("  G-cap %s = %s на уникальных сделках V0 (n %d): d=s %s %%, d=vm %s %%, d≥1,9 %% (убыток ≥ 10 $) %s %%, V=−1 %s %%" % (kk, fr[side][kk], g["n"], fmt(g["d_eq_s"] * 100, 1), fmt(g["d_eq_vm"] * 100, 1), fmt(g["d_ge_0019"] * 100, 1), fmt(g["no_v"] * 100, 1)))
                if side == "short":
                    a4 = A.b9_cids()[-1][0]
                    r["anchor_s4"] = {}
                    m0a, p0a, _ = metrics([c0[8]], lo, hi, lab, nper)
                    m2a, p2a, _ = metrics([c2[8]], lo, hi, lab, nper)
                    r["anchor_s4"] = dict(name=a4, v0=m0a, v2=m2a, delta=float((p2a - p0a).sum()))
                    say("  якорь s4 (%s, клетка 7180023, k_pool): V0 Σ %s $ / %s сд / %s стопов; V2′ Σ %s $ / %s сд / %s стопов (%s $); Δ %s $ · Шарп %s → %s" % (
                        a4, fmt(m0a["sum"]), fmt(m0a["trades"], 0), fmt(m0a["stops_n"], 0), fmt(m2a["sum"]), fmt(m2a["trades"], 0), fmt(m2a["stops_n"], 0), fmt(m2a["stops_sum"]), fmt(float((p2a - p0a).sum())), fmt(m0a["sharpe"]), fmt(m2a["sharpe"])))
            if sc in ("A", "B"):
                stitched[(side, sc)] = (p0, n0, p2, n2)
            res[side][sc] = r
        if (side, "A") in stitched and (side, "B") in stitched:
            pb0, nb0, pb2, nb2 = stitched[(side, "B")]
            pa0, na0, pa2, na2 = stitched[(side, "A")]
            p0, n0, p2, n2 = (np.concatenate([x, y]) for x, y in ((pb0, pa0), (nb0, na0), (pb2, pa2), (nb2, na2)))
            lab, nper = labels(31, 316)
            u0, u2 = L.uw_stats(p0), L.uw_stats(p2)
            mo0, mo2 = L.month_stats(p0, n0, lab, nper), L.month_stats(p2, n2, lab, nper)
            d = p2 - p0
            ci = L.delta_ci(d, (7, 21))
            res[side]["stitched"] = dict(sum0=float(p0.sum()), sum2=float(p2.sum()), sharpe0=L.sharpe(p0), sharpe2=L.sharpe(p2), dd0=u0["dd"], dd2=u2["dd"], gap0=u0["gap_longest"], gap2=u2["gap_longest"], m1_0=mo0["m1"], m1_2=mo2["m1"],
                                         delta=float(d.sum()), ci={str(b): v for b, v in ci.items()}, month0=mo0, month2=mo2)
            say("\n## %s · склейка «вне выборки» (FJ с k_SEL + SEL с k_FJ против V0; 286 суток)" % side)
            say("  V0  Σ %s $ · Шарп %s · просадка %s $ (%s %%) · перехай %d сут · M1 %d / 10 · $/сд по периодам %s" % (fmt(p0.sum()), fmt(L.sharpe(p0)), fmt(u0["dd"]), fmt(u0["dd"] / L.MARGIN * 100, 1), u0["gap_longest"], mo0["m1"], [round(x, 3) for x in mo0["per_trade"]]))
            say("  V2′ Σ %s $ · Шарп %s · просадка %s $ (%s %%) · перехай %d сут · M1 %d / 10 · $/сд по периодам %s" % (fmt(p2.sum()), fmt(L.sharpe(p2)), fmt(u2["dd"]), fmt(u2["dd"] / L.MARGIN * 100, 1), u2["gap_longest"], mo2["m1"], [round(x, 3) for x in mo2["per_trade"]]))
            say("  Δ Σ %s $; 95 %% двусторонний, блоки 7 / 21: %s" % (fmt(float(d.sum())), "; ".join("[%s; %s]" % (fmt(v[0]), fmt(v[1])) for v in ci.values())))
    res["units"] = units()
    say("\n## единицы k: ход против позиции выигрышных, NATR30 (шаг 2) и RV60 на тех же сделках")
    for label, u in res["units"].items():
        say("  %s: выигрышных %d · NATR30 q75/90/95/99 = %s · RV60 = %s · RV60 / NATR30 на сделку: среднее %s, q10 / q50 / q90 = %s · отношение q95 (RV60 / NATR30) = %s" % (
            label, u["n_win"], "/".join(str(x) for x in u["q_natr30"]), "/".join(str(x) for x in u["q_rv60"]), fmt(u["ratio_rv_natr_mean"], 3), "/".join(str(x) for x in u["ratio_q10_q50_q90"]), fmt(u["q95_ratio"], 3)))
    A.jwrite(A.RES + "/desc.json", res)
    with open(A.RES + "/report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(run())
