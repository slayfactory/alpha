#!/usr/bin/env python3
"""TK-736 (П-42 шаг 7, §13.80, PS-1080…1083 v3): ворота и анализ на srv. Движок не запускает (`tk736_run.sh`); ввод-вывод — здесь, формулы — `tk736_lib.py`.

  python3 tk736_an.py gsrc            # G-src: ключи B9, наличие файлов, строки / уникальные сделки, повторы, стопы, якорь s4 = 465 / 17 → res/gsrc.json
  python3 tk736_an.py gv              # G-V: V_i (numpy RV60) против vd_pm/1000/k по незажатым строкам vstop.csv (k = 1); P&L не читается
  python3 tk736_an.py kest            # k_FJ, k_SEL, k_pool (q₀,₉₅ κ выигрышных; шорт — уникальные сделки B9), таблица q → заморозка

Порядок ворот — §13.80.3; `kest` отказывает без `res/gsrc.json` и `res/gv.json` (ворота пройдены).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk726_seam as sm  # noqa: E402
import tk727_lib as t7  # noqa: E402
import tk736_lib as L  # noqa: E402

ROOT = os.environ.get("TK736_ROOT", "/data/p42-tk736")
RES = ROOT + "/res"
TRADES = ROOT + "/trades"
TK726 = "/data/p42-tk726/tk726.sqlite"
FREEZE = ROOT + "/tk736-frozen.json"
B_NAMES = ("BS-l14-tf240", "BS-l14-tf30", "BS-l14-tf60", "BS-l3-tf240", "BS-l3-tf30", "BS-l3-tf60", "BS-l7-tf240", "BS-l7-tf30", "BS-l7-tf60")
FJ, SEL = ("m02", "m03", "m04", "m05", "m06"), ("wa", "wb", "wc")
TREES = {"m01": "/data/p42-tk695/jan", "m02": "/data/p42-tk695/feb", "m03": "/data/p42-tk695/mar", "m04": "/data/p42-tk695/apr", "m05": "/data/p42-tk695/may", "m06": "/data/p42-tk686/wj",
         "wa": "/data/p42-tk621/wa", "wb": "/data/p42-tk621/wb", "wc": "/data/p42-tk621/wc", "n1": "/data/p42-tk700/n1"}
STAGE_NO = {"v0long": 1, "gv": 2, "ctl": 3, "kinf": 4, "v2fj": 5, "v2sel": 6, "v2pool": 7}


def run_dir(stage, win):
    """Каталог прогона этапа для окна: run-id `tk736-<этап>-{h1,sel,n1}`."""
    part = "h1" if win.startswith("m") else "n1" if win == "n1" else "sel"
    return "%s/tk736-%s-%s" % (TRADES, stage, part)


def b9_cids():
    import sqlite3
    c = sqlite3.connect("file:%s?mode=ro" % TK726, uri=True)
    d = {n: cid for cid, n in c.execute("SELECT cid, name FROM tk726_cell")}
    return [(n, d[n]) for n in B_NAMES]


def read_cell(path, t0_ms, win):
    """Сделки одного файла в зачётных сутках окна: `sm.read_trades` + level, sig_min, entry_min, exit_min (колонки файла)."""
    a = sm.read_trades(path, t0_ms)
    lo, hi = sm.WINDOWS[win][0], sm.WINDOWS[win][1]
    m = sm.in_days(a, sm.day_of(lo), sm.day_of(hi))
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    keep = np.nonzero(m)[0]
    a = sm.take(a, m)
    a["level"] = np.array([int(rows[i]["level"]) for i in keep], np.int64)
    for k in ("sig_min", "entry_min", "exit_min"):
        a[k] = np.array([int(rows[i][k]) for i in keep], np.int64)
    a["win"] = np.array([win] * len(keep), dtype=object)
    return a


def disk_v0(cid, win):
    """V0 шорта B9 с диска (TK-718 / TK-726)."""
    root = sm.WINDOWS[win][2]
    t0 = json.load(open(os.path.join(root, "t0.json"), encoding="utf-8"))[win]
    p = os.path.join(root, win, "%d-trades.csv" % cid)
    return read_cell(p, t0, win) if os.path.exists(p) else None


def run_cell(stage, cid, win):
    """Сделки клетки этапа (прогон этого шага)."""
    d = run_dir(stage, win)
    p = os.path.join(d, win, "%d-trades.csv" % cid)
    if not os.path.exists(p):
        return None
    t0 = json.load(open(os.path.join(d, "t0.json"), encoding="utf-8"))[win]
    return read_cell(p, t0, win)


def concat(parts):
    parts = [p for p in parts if p is not None and len(p["pnl"])]
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]} if parts else None


_candle_cache = {}


def candles(win, sym):
    """(o, h, l, c) целые e8 по минутам окна; None — нет файла."""
    key = (win, sym)
    if key not in _candle_cache:
        fn = os.path.join(TREES[win], "cache", sym + ".b42")
        if not os.path.exists(fn):
            _candle_cache[key] = None
        else:
            with open(fn, "rb") as f:
                n = int.from_bytes(f.read(8)[4:8], "little")
            a = np.fromfile(fn, dtype="<i8", offset=8, count=5 * n).reshape(5, n)
            o, h, l, c = a[0], a[1], a[2], a[3]
            S = dict(o=o.astype(np.float64), h=h.astype(np.float64), l=l.astype(np.float64), c=c.astype(np.float64), pres=o != 0)
            _candle_cache[key] = (o, h, l, c, t7.rv_sqrt_sum(S, 60))
    return _candle_cache[key]


def v_and_adverse(a, side):
    """По сделкам: V_i (RV60 на закрытой минуте sig_min − 1, −1 — нет) и a_i (ход против позиции, доля цены). Возвращает (V, a, число пустых ходов, нет файла)."""
    n = len(a["pnl"])
    V, A = np.full(n, -1.0), np.zeros(n)
    empty = nofile = 0
    for i in range(n):
        cd = candles(a["win"][i], a["sym"][i])
        if cd is None:
            nofile += 1
            continue
        o, h, l, c, rv = cd
        t = int(a["sig_min"][i]) - 1
        if 0 <= t < len(rv) and np.isfinite(rv[t]) and rv[t] > 0:
            V[i] = rv[t]
        em, xm = int(a["entry_min"][i]), int(a["exit_min"][i])
        A[i] = L.adverse_move(side, h, l, o, em, xm, a["kind"][i] == "stop", int(a["entry_px_e8"][i]))
        empty += (xm == em) and a["kind"][i] != "stop"
    return V, A, empty, nofile


def jwrite(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)


def short_trades(wins):
    """Шорт B9: {имя клетки: сделки по окнам} (V0 с диска)."""
    return {name: concat([disk_v0(cid, w) for w in wins]) for name, cid in b9_cids()}


# ───────────────────────────── ворота ─────────────────────────────

def cmd_gsrc(a):
    wins = FJ + SEL + ("m01", "n1")
    rep = dict(cells=[], note="G-src §13.80.3")
    miss = 0
    uni_all = {"FJ": [], "SEL": []}
    for name, cid in b9_cids():
        row = dict(name=name, cid=cid, files={})
        for w in wins:
            row["files"][w] = disk_v0(cid, w) is not None
            miss += not row["files"][w]
        rep["cells"].append(row)
    rep["missing_files"] = miss
    # строки и уникальные сделки по половинам; стопы
    for half, ws in (("FJ", FJ), ("SEL", SEL)):
        cells = short_trades(ws)
        allp = concat(list(cells.values()))
        uniq = L.dedup_first(allp["sym"], allp["sig_ms"], allp["entry_ms"], allp["exit_ms"], allp["pnl"])
        rep[half] = dict(rows=int(len(allp["pnl"])), unique=int(uniq.sum()), repeat_share=float(1 - uniq.mean()),
                         stops_rows=int((allp["kind"] == "stop").sum()), stops_unique=int(((allp["kind"] == "stop") & uniq).sum()),
                         wins_unique=int(((allp["pnl"] > 0) & uniq).sum()))
        uni_all[half] = uniq
    anchor = disk_v0(dict(b9_cids())["BS-l7-tf60"], "m02")
    rep["anchor_cid"] = dict(b9_cids())["BS-l7-tf60"]
    an = short_trades(FJ + SEL)["BS-l7-tf60"]
    rep["anchor_trades_10_periods"] = int(len(an["pnl"]))
    rep["anchor_stops_10_periods"] = int((an["kind"] == "stop").sum())
    rep["anchor_ok_step2"] = bool(rep["anchor_trades_10_periods"] == 465 and rep["anchor_stops_10_periods"] == 17)
    jwrite(RES + "/gsrc.json", rep)
    print(json.dumps({k: v for k, v in rep.items() if k != "cells"}, ensure_ascii=False))
    return 0 if miss == 0 and anchor is not None and rep["anchor_ok_step2"] else 1


def cmd_gv(a):
    """G-V: V_i (numpy) против vd_pm / 1000 / k по незажатым строкам (vm < d_pm < s_pm); k = 1; без чтения P&L (берутся только sym, level, sig_ms из vstop.csv и sig_min из trades для минуты V)."""
    bad = tot = clamped = nov = 0
    per = {}
    for side, s_pm in (("short", 4000), ("long", 3000)):
        n_all = n_unc = n_bad = n_nov = n_cl = 0
        ids = [cid for _, cid in b9_cids()] if side == "short" else [None]
        for j, _ in enumerate(ids):
            cid = 7360000 + 1000 * STAGE_NO["gv"] + (1 + j if side == "short" else 10)
            for w in FJ + SEL + ("m01", "n1"):
                d = run_dir("gv", w)
                pv = os.path.join(d, w, "%d-vstop.csv" % cid)
                if not os.path.exists(pv):
                    continue
                t0 = json.load(open(os.path.join(d, "t0.json"), encoding="utf-8"))[w]
                with open(pv, encoding="utf-8", newline="") as f:
                    vs = list(csv.DictReader(f))
                with open(os.path.join(d, w, "%d-trades.csv" % cid), encoding="utf-8", newline="") as f:
                    tr = list(csv.DictReader(f))
                assert len(vs) == len(tr), (cid, w, len(vs), len(tr))
                for v, t in zip(vs, tr):
                    assert v["sym"] == t["sym"] and int(v["sig_ms"]) == int(t["sig_ms"]), (cid, w)
                    n_all += 1
                    vd = int(v["vd_pm"])
                    cd = candles(w, v["sym"])
                    if vd < 0:
                        n_nov += 1
                        continue
                    if not (500 < vd < s_pm):
                        n_cl += 1
                        continue
                    n_unc += 1
                    ti = int(t["sig_min"]) - 1
                    Vi = cd[4][ti] if cd is not None and 0 <= ti < len(cd[4]) else float("nan")
                    if not np.isfinite(Vi) or abs(Vi * 100.0 * 1000 / 1 - vd) > 0.5 + 1e-9:     # vd_pm — тысячные процента: V·100·1000; допуск ±0,0005 п.п. = ±0,5 пм
                        n_bad += 1
        per[side] = dict(rows=n_all, unclamped=n_unc, mismatch=n_bad, clamped=n_cl, no_v=n_nov)
    ok = all(v["mismatch"] == 0 and v["unclamped"] > 0 for v in per.values())
    jwrite(RES + "/gv.json", dict(per=per, ok=ok))
    print(json.dumps(per, ensure_ascii=False), "G-V", "ПРОЙДЕН" if ok else "ПРОВАЛЕН")
    return 0 if ok else 1


# ───────────────────────────── оценка k ─────────────────────────────

def kappa_for(side, wins):
    """κ_i = a_i / V_i выигрышных (r > 0, V ≥ 0) сделок: шорт — уникальные сделки B9 (диск), лонг — якорь s3 (прогон v0long). Возвращает (κ, число выигрышных всего, V = −1, нет файла, пустых ходов)."""
    if side == "short":
        allp = concat(list(short_trades(wins).values()))
        keep = L.dedup_first(allp["sym"], allp["sig_ms"], allp["entry_ms"], allp["exit_ms"], allp["pnl"])
        allp = sm.take(allp, keep)
    else:
        allp = concat([run_cell("v0long", 7361001, w) for w in wins])
    V, A, empty, nofile = v_and_adverse(allp, side)
    win = allp["pnl"] > 0
    ok = win & (V > 0)
    return A[ok] / V[ok], int(win.sum()), int((win & (V < 0)).sum()), nofile, empty


def cmd_kest(a):
    for g in ("gsrc", "gv"):
        if not os.path.exists("%s/%s.json" % (RES, g)):
            raise SystemExit("нет res/%s.json: ворота не пройдены (порядок §13.80.3)" % g)
    out, tab = {}, {}
    for side in ("short", "long"):
        kj, nj, v1j, nfj, ej = kappa_for(side, FJ)
        ks, ns, v1s, nfs, es = kappa_for(side, SEL)
        kp = np.concatenate([kj, ks])
        out[side] = dict(k_FJ=L.estimate_k(kj), k_SEL=L.estimate_k(ks), k_pool=L.estimate_k(kp), n_FJ=len(kj), n_SEL=len(ks), wins_FJ=nj, wins_SEL=ns, noV_FJ=v1j, noV_SEL=v1s, nofile=nfj + nfs, empty=ej + es)
        tab[side] = dict(FJ=L.q_table(kj), SEL=L.q_table(ks))
    out["q_table"] = {s: {h: {str(q): v for q, v in d.items()} for h, d in x.items()} for s, x in tab.items()}
    body = json.dumps(out, ensure_ascii=False, sort_keys=True)
    out["sha256_est"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    jwrite(FREEZE, out)
    print(json.dumps({s: out[s] for s in ("short", "long")}, ensure_ascii=False), out["sha256_est"][:16])
    return 0


# ───────────────────────────── G-pow и суд ─────────────────────────────

HALF = {"FJ": (FJ, 31, 180), "SEL": (SEL, 181, 316)}                       # окна и сутки (от 01.01.2026) половины
S_CAP = L.S_CAP


def period_labels(lo, hi):
    """Метка периода (0…4) по суткам половины: месяцы PERIODS из `tk726_exits` (FJ: фев…июн; SEL: июл…окт, 1–13.11)."""
    import tk726_exits as ex
    days = np.arange(lo, hi + 1)
    lab = np.full(len(days), -1)
    ids = [i for i, (nm, a_, b_) in enumerate(ex.PERIODS) if sm.day_of(a_) >= lo and sm.day_of(b_) <= hi and nm not in ("Jan", "N1")]
    for j, i in enumerate(ids):
        lab[(days >= sm.day_of(ex.PERIODS[i][1])) & (days <= sm.day_of(ex.PERIODS[i][2]))] = j
    assert (lab >= 0).all() and len(ids) == 5, (lo, hi, ids)
    return lab, len(ids)


def day_series(a, lo, hi):
    nd = hi - lo + 1
    d = (a["eday"] - lo).astype(np.int64)
    return np.bincount(d, weights=a["pnl"], minlength=nd)[:nd], np.bincount(d, minlength=nd)[:nd].astype(np.float64)


def cells_v0(side, half):
    """[сделки клетки на половине] — V0: шорт B9 с диска (9 клеток), лонг — прогон v0long (1 клетка)."""
    wins = HALF[half][0]
    if side == "short":
        return [concat([disk_v0(cid, w) for w in wins]) for _, cid in b9_cids()]
    return [concat([run_cell("v0long", 7361001, w) for w in wins])]


def cells_v2(side, half, stage):
    wins = HALF[half][0]
    base = 7360000 + 1000 * STAGE_NO[stage]
    if side == "short":
        return [concat([run_cell(stage, base + 1 + j, w) for w in wins]) for j in range(9)]
    return [concat([run_cell(stage, base + 10, w) for w in wins])]


def eq_series(cells, half):
    """Равновесный суточный ряд: среднее суточного P&L и числа сделок по клеткам."""
    _, lo, hi = HALF[half]
    ps, ns = zip(*[day_series(c, lo, hi) for c in cells])
    return np.mean(ps, axis=0), np.mean(ns, axis=0)


def cmd_gpow(a):
    fr = json.load(open(FREEZE, encoding="utf-8"))
    out = {}
    for side in ("short", "long"):
        out[side] = {}
        for dirn, (ek, half) in {"A": ("k_FJ", "SEL"), "B": ("k_SEL", "FJ")}.items():
            k = fr[side][ek]
            if k is None:
                out[side][dirn] = dict(note="k не определён (O4)")
                continue
            _, lo, hi = HALF[half]
            cells = cells_v0(side, half)
            d_list, g_list, s0_list, n0_list = [], [], [], []
            for c in cells:
                V, A, _, _ = v_and_adverse(c, side)
                r2, cut = L.replay(c["pnl"], c["fees"], A, V, k, S_CAP[side])
                d_list.append(day_series(dict(eday=c["eday"], pnl=r2 - c["pnl"]), lo, hi)[0])
                g_list.append(L.gmax(c["pnl"], c["fees"]))
                s0_list.append(float(c["pnl"].sum()))
                n0_list.append(day_series(c, lo, hi)[1])
            delta = np.mean(d_list, axis=0)
            sigma0, gmax = float(np.mean(s0_list)), float(np.mean(g_list))
            mde = L.mde_c1(delta, sigma0)
            pnl0, n0 = eq_series(cells, half)
            lab, nper = period_labels(lo, hi)
            c2 = L.c2_p(pnl0 + delta, n0, pnl0, n0, lab, nper)
            stops = [(int((c["kind"] == "stop").sum()), float(c["pnl"][c["kind"] == "stop"].sum())) for c in cells]
            out[side][dirn] = dict(k=k, half=half, sigma0=sigma0, s5=L.s5(delta), mde_c1=mde, gmax=gmax, mde_c2=c2["se"], o6=bool(mde > gmax),
                                   stops_mean_n=float(np.mean([s[0] for s in stops])), stops_mean_sum=float(np.mean([s[1] for s in stops])))
        out[side]["O6"] = any(v.get("o6") for v in out[side].values() if isinstance(v, dict))
    jwrite(RES + "/gpow.json", out)
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_judge(a):
    fr = json.load(open(FREEZE, encoding="utf-8"))
    gp = json.load(open(RES + "/gpow.json", encoding="utf-8"))
    res, ps = {}, {}
    for side in ("short", "long"):
        if gp[side]["O6"]:
            res[side] = dict(outcome="O6")
            continue
        dirs = {}
        for dirn, (stage, half) in {"A": ("v2fj", "SEL"), "B": ("v2sel", "FJ")}.items():
            _, lo, hi = HALF[half]
            c0, c2 = cells_v0(side, half), cells_v2(side, half, stage)
            p0, n0 = eq_series(c0, half)
            p2, n2 = eq_series(c2, half)
            lab, nper = period_labels(lo, hi)
            r1 = L.c1(p2 - p0, float(np.mean([c["pnl"].sum() for c in c0])))
            r2 = L.c2_p(p2, n2, p0, n0, lab, nper)
            key0, key2 = [], []
            r0a, r2a, k0a, k2a, dl = [], [], [], [], []
            for j, (x0, x2) in enumerate(zip(c0, c2)):
                key0 += [(j, s, m, lv) for s, m, lv in zip(x0["sym"], x0["sig_ms"], x0["level"])]
                key2 += [(j, s, m, lv) for s, m, lv in zip(x2["sym"], x2["sig_ms"], x2["level"])]
                r0a.append(x0["pnl"]); r2a.append(x2["pnl"]); k0a.append(x0["kind"]); k2a.append(x2["kind"])
            # d < s по строкам V2′: vstop.csv (vd_pm < s_pm; −1 → нет)
            s_pm = 4000 if side == "short" else 3000
            base = 7360000 + 1000 * STAGE_NO[stage]
            for j in range(len(c2)):
                for w in HALF[half][0]:
                    pv = os.path.join(run_dir(stage, w), w, "%d-vstop.csv" % (base + (1 + j if side == "short" else 10)))
                    with open(pv, encoding="utf-8", newline="") as f:
                        dl += [0 <= int(r["vd_pm"]) < s_pm for r in csv.DictReader(f)]
            rc3 = L.c3(np.concatenate(r0a), np.concatenate(r2a), key0, key2, np.concatenate(k2a), dl, np.concatenate(k0a))
            dirs[dirn] = dict(c1=r1["verdict"], c1_lo={str(k): v for k, v in r1["lo"].items()}, c1_thr=r1["thr"], delta=r1["delta"], c2p=r2["p"], sigma2=r2["sigma2"], sigma0=r2["sigma0"], c3=rc3)
        res[side] = dirs
        ps[side] = max(dirs["A"]["c2p"], dirs["B"]["c2p"])
    sides = [s for s in ("short", "long") if s in ps]
    adj = dict(zip(sides, L.holm([ps[s] for s in sides]))) if sides else {}
    for s in sides:
        k_fj, k_sel = fr[s]["k_FJ"], fr[s]["k_SEL"]
        c3ok = all(res[s][d]["c3"]["ok"] for d in "AB")
        res[s]["outcome"] = L.outcome(k_fj, k_sel, dict(c1=res[s]["A"]["c1"], c2p=res[s]["A"]["c2p"]), dict(c1=res[s]["B"]["c1"], c2p=res[s]["B"]["c2p"]), adj[s], c3ok)
        res[s]["p_holm"] = adj[s]
        res[s]["k_ratio"] = k_fj / k_sel
    jwrite(RES + "/judge.json", res)
    print(json.dumps({s: res[s].get("outcome") for s in res}, ensure_ascii=False))
    return 0


def cmd_gadv(a):
    """G-adv: тот же код хода против позиции при V = NATR30 (свеча выхода входит, как в шаге 2) воспроизводит числа якоря s4 из отчёта шага 2 в пределах округления; затем RV60 (правило §13.80.1)."""
    import tk726_exits as ex
    an = short_trades(FJ + SEL)["BS-l7-tf60"]
    n = len(an["pnl"])
    mae_n, mae_r = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(n):
        w, sym = an["win"][i], an["sym"][i]
        cd = candles(w, sym)
        o, h, l, c, rv = cd
        t_ = int(an["sig_min"][i]) - 1
        v_n = int(ex.natr_at(o, h, l, c, [t_])[0])
        em, xm = int(an["entry_min"][i]), int(an["exit_min"][i])
        adv = L.adverse_move("short", h, l, o, em, xm, True, int(an["entry_px_e8"][i]))          # свеча выхода входит (шаг 2)
        if v_n > 0:
            mae_n[i] = adv / (v_n / 1e8)
        if np.isfinite(rv[t_]) and rv[t_] > 0:
            mae_r[i] = adv / rv[t_]
    win = an["pnl"] > 0
    qs = (0.75, 0.9, 0.95, 0.99)
    ref = dict(n=465, n_win=184, q=[2.01, 3.87, 5.77, 8.53], max=9.67, mean_win=1.82, mean_all=2.05, frac={0.5: 0.870, 1: 0.647, 1.5: 0.418, 2: 0.255, 3: 0.136, 5: 0.065})
    m = ~np.isnan(mae_n)
    mw = m & win
    got = dict(n=int(m.sum()), n_win=int(mw.sum()), q=[round(float(np.quantile(mae_n[mw], q)), 2) for q in qs], max=round(float(mae_n[mw].max()), 2), mean_win=round(float(mae_n[mw].mean()), 2),
               mean_all=round(float(mae_n[m].mean()), 2), frac={x: round(float((mae_n[mw] >= x).mean()), 3) for x in ref["frac"]})
    ok = got["n"] == ref["n"] and got["n_win"] == ref["n_win"] and all(abs(x - y) <= 0.0101 for x, y in zip(got["q"], ref["q"])) and abs(got["max"] - ref["max"]) <= 0.0101         and abs(got["mean_win"] - ref["mean_win"]) <= 0.0101 and abs(got["mean_all"] - ref["mean_all"]) <= 0.0101 and all(abs(got["frac"][x] - ref["frac"][x]) <= 0.0011 for x in ref["frac"])
    mr = ~np.isnan(mae_r) & win
    rv = dict(n_win=int(mr.sum()), q=[round(float(np.quantile(mae_r[mr], q)), 2) for q in qs + (0.5,)])
    jwrite(RES + "/gadv.json", dict(natr30=got, ref=ref, ok=bool(ok), rv60_winners=rv))
    print(json.dumps(dict(natr30=got, ref={k: v for k, v in ref.items() if k != "frac"}, ok=bool(ok), rv60_winners=rv), ensure_ascii=False), "G-adv", "ПРОЙДЕН" if ok else "ПРОВАЛЕН")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n, f in (("gsrc", cmd_gsrc), ("gv", cmd_gv), ("kest", cmd_kest), ("gpow", cmd_gpow), ("judge", cmd_judge), ("gadv", cmd_gadv)):
        sp.add_parser(n).set_defaults(fn=f)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
