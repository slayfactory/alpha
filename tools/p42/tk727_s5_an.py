"""TK-727, шаг 5: анализ по §13.78.5 / §13.78.7 п. 1–3, 10, 13, 16 и решениям по краям L-477 (проверка V — `tk727_s5.py check` — пройдена до чтения).

Выход: `<out>/s5_result.json` и таблицы `s5_*` в `<out>/tk727.sqlite`. Испытаний по прибыли: 6 признаков × 2 исхода (Холм 6 на исход, повтор на второй половине обязателен).
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time

import numpy as np

import tk727_lib as L
import tk727_s5 as S5
import tk727_s5lib as R

HALVES = {"FJ": (31, 181), "SEL": (181, 317)}                       # [от, до) суток от 01.01.2026
O2_HALVES = {"wa+wb": (S5.day_of("2026-07-01"), S5.day_of("2026-10-03")), "wc": (S5.day_of("2026-10-03"), 317)}
FC_TARGETS = {"FJ": ("jul", "aug", "sep", "oct", "nov1"), "SEL": ("feb", "mar", "apr", "may", "jun")}   # обучение → периоды прогноза (январь, N1 — описательно)
DESC_PERIODS = ("jan", "n1")
FNAMES = ("F1", "F2", "F3", "F4", "F5", "F6")
FKEY = {"F1": "V", "F2": "F2", "F3": "F3", "F4": "F4", "F5": "F5", "F6": "F6"}
MARGIN = 250.0
YEAR_DAYS = 365


# ───────────────────────────── данные ─────────────────────────────

def dedupe_family(tr: dict, cids=S5.FAM):
    """Индексы строк семейства; точные дубликаты (монета, сигнал, вход, выход, результат) — один раз. → (индексы, число сырых, число дубликатов)."""
    idx = np.where(np.isin(tr["cid"], cids))[0]
    seen, keep = set(), []
    for i in idx:
        k = (str(tr["sym"][i]), int(tr["sig_ms"][i]), int(tr["entry_ms"][i]), int(tr["exit_ms"][i]), round(float(tr["pnl"][i]), 6))
        if k in seen:
            continue
        seen.add(k)
        keep.append(i)
    return np.array(keep, np.int64), int(len(idx)), int(len(idx) - len(keep))


def group_data(tr: dict, name: str):
    if name == "FAM":
        idx, n_raw, n_dup = dedupe_family(tr)
    elif name == "s4":
        idx = np.where(tr["cid"] == 7180023)[0]
        n_raw, n_dup = len(idx), 0
    else:
        idx = np.where(tr["cid"] == S5.LINES[name])[0]
        n_raw, n_dup = len(idx), 0
    keys = ("sym", "eday", "pnl", "V", "F2", "turn24", "F3", "F4", "F5", "F6", "price")
    d = {k: tr[k][idx] for k in keys}
    d["n_raw"], d["n_dup"] = n_raw, n_dup
    return d


def ok_mask(d: dict, lo: int, hi: int) -> np.ndarray:
    return (d["eday"] >= lo) & (d["eday"] < hi) & np.isfinite(d["V"]) & (d["V"] > 0) & np.isfinite(d["F2"]) & np.isfinite(d["turn24"])


def prep(d: dict, lo: int, hi: int) -> dict:
    """Матрицы модели для суток [lo, hi): X = [V %, средняя треть оборота, верхняя треть оборота, F2], эффекты суток и монеты."""
    sel = (d["eday"] >= lo) & (d["eday"] < hi)
    ok = ok_mask(d, lo, hi)
    day = (d["eday"][ok] - lo).astype(np.int64)
    th = R.thirds_in_day(d["turn24"][ok], day)
    X = np.column_stack([d["V"][ok] * 100.0, th == 1, th == 2, d["F2"][ok]]).astype(np.float64)
    coin = np.unique(d["sym"][ok], return_inverse=True)[1].astype(np.int64) if ok.any() else np.zeros(0, np.int64)
    return dict(X=X, y=d["pnl"][ok].astype(np.float64), day=day, coin=coin, nd=hi - lo, nc=int(coin.max() + 1) if len(coin) else 0, n=int(ok.sum()), n_sel=int(sel.sum()),
                n_days=int(len(np.unique(day))), th=th, V=d["V"][ok] * 100.0)


def day_stats(d: dict, lo: int, hi: int) -> dict:
    """Σ $, сделок, $ на сделку, Шарп (годовой из суточного), просадка и перехай суточного ряда (дни без сделок = 0)."""
    sel = (d["eday"] >= lo) & (d["eday"] < hi)
    nd = hi - lo
    pnl = np.bincount((d["eday"][sel] - lo).astype(np.int64), weights=d["pnl"][sel], minlength=nd)
    cnt = np.bincount((d["eday"][sel] - lo).astype(np.int64), minlength=nd)
    cum = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    prev = np.concatenate([[0.0], peak[:-1]])
    hi_i = np.nonzero(cum > prev + 1e-9)[0]
    pts = np.concatenate([[-1], hi_i]).astype(int)
    gaps = np.diff(pts)
    tail = nd - 1 - int(pts[-1])
    sd = float(np.std(pnl, ddof=1)) if nd > 1 else 0.0
    n = int(cnt.sum())
    return dict(days=nd, n=n, sum=float(pnl.sum()), per_trade=float(pnl.sum() / n) if n else float("nan"), sharpe=float(pnl.mean() / sd * math.sqrt(YEAR_DAYS)) if sd > 0 else float("nan"),
                dd=float((peak - cum).max()), ddp=float((peak - cum).max() / MARGIN * 100.0), gap_longest=int(max(list(gaps) + [tail])), gap_mean=float(gaps.mean()) if len(gaps) else float("nan"), gap_tail=tail)


# ───────────────────────────── регрессия ─────────────────────────────

def run_reg(arg):
    """Одна регрессия с бутстрепом: kind all|win|loss, модель fe (сутки + монета) | day (только сутки), блок. → точка, коэффициенты, интервал."""
    tag, P, kind, model, block, nboot = arg
    X, y, day, coin = P["X"], P["y"], P["day"], P["coin"]
    if kind == "win":
        m = y > 0
        y_use = y
    elif kind == "loss":
        m = y <= 0
        y_use = -y
    else:
        m = np.ones(len(y), bool)
        y_use = y
    X, y_use, day, coin = X[m], y_use[m], day[m], coin[m]
    if model == "day":
        coin, nc = np.zeros(len(coin), np.int64), 1
    else:
        nc = P["nc"]
    if len(y_use) < 30 or len(np.unique(day)) < 10:
        return tag, dict(kind=kind, model=model, block=block, n_rows=int(len(y_use)), point=float("nan"), skipped=True)
    beta = R.fe_coefs(X, y_use[:, None], day, P["nd"], coin, nc)[:, 0]
    boot = R.boot_fe(X, y_use[:, None], day, coin, P["nd"], nc, block, nboot, L.SEED, 0)[:, 0]
    iv = R.interval(boot, float(beta[0]))
    iv["n_boot"] = iv.pop("n")
    return tag, dict(kind=kind, model=model, block=block, n_rows=int(len(y_use)), coefs=[float(x) for x in beta], mean_y=float(y_use.mean()), **iv)


def covers0(iv: dict) -> bool:
    return bool(iv["lo"] <= 0.0 <= iv["hi"])


# ───────────────────────────── прогноз периодов ─────────────────────────────

def forecast_rows(d: dict, alpha: float, train: str, targets, desc) -> list:
    """Для периода p: предсказанное отклонение α·(V̄_p − V̄_обуч), факт ȳ_p − ȳ_обуч; знак верен, если знаки совпали (ноль — не верен); порог V* = V̄_обуч − ȳ_обуч / α и доля сделок ниже него."""
    lo, hi = HALVES[train]
    m = ok_mask(d, lo, hi)
    v_tr, y_tr = float(d["V"][m].mean() * 100.0), float(d["pnl"][m].mean())
    vstar = v_tr - y_tr / alpha if alpha > 0 else float("nan")
    rows = []
    for p in list(targets) + list(desc):
        a, b = L.PERIODS[p]
        mp = ok_mask(d, a, b)
        if mp.sum() == 0:
            rows.append(dict(period=p, n=0, role="прогноз" if p in targets else "описательно"))
            continue
        vp, yp = float(d["V"][mp].mean() * 100.0), float(d["pnl"][mp].mean())
        pred, act = alpha * (vp - v_tr), yp - y_tr
        rows.append(dict(period=p, n=int(mp.sum()), V_mean=vp, y_mean=yp, pred=pred, actual=act, correct=bool(pred != 0 and act != 0 and (pred > 0) == (act > 0)), role="прогноз" if p in targets else "описательно",
                         vstar=vstar, share_below_vstar=float((d["V"][mp] * 100.0 < vstar).mean()) if np.isfinite(vstar) else float("nan")))
    return rows


# ───────────────────────────── правило Г2 ─────────────────────────────

def decide_g2(res: dict) -> dict:
    """Правило решения Г2 (§13.78.5 + §13.78.7 п. 3): res[half] = {alpha6, alpha21, win, loss}; res['fc'][train] = число верных / число периодов."""
    out = {"a": {}, "b": {}, "c": {}}
    for h in ("FJ", "SEL"):
        r = res[h]
        out["a"][h] = bool(r["alpha6"]["lo"] > 0 and r["alpha21"]["lo"] > 0)
        out["b"][h] = bool(covers0(r["loss"]) or r["loss"]["point"] < r["win"]["point"] / 3.0)
    for tr in ("FJ", "SEL"):
        f = res["fc"][tr]
        out["c"][tr] = bool(f["correct"] == f["total"])
    a_all = all(out["a"].values())
    b_all = all(out["b"].values())
    c_all = all(out["c"].values())
    refuted = bool(covers0(res["FJ"]["alpha6"]) or covers0(res["SEL"]["alpha6"]) or not any(out["b"].values()))
    out["outcome"] = "подтверждена" if (a_all and b_all and c_all) else ("опровергнута" if refuted else "между")
    return out


# ───────────────────────────── признаки (трети внутри суток) ─────────────────────────────

def feat_task(arg):
    """Один признак на одной половине одного исхода: разность «верхняя − нижняя треть» среднего исхода (F3 — разброс по 6 корзинам, p — перестановка)."""
    tag, fname, x, y, day, nd, block, nboot = arg
    ok = np.isfinite(x)
    x, y, day = x[ok], y[ok], day[ok]
    if len(y) < 30:
        return tag, fname, dict(skipped=True, n=int(len(y)))
    if fname == "F3":
        b = x.astype(np.int64)
        means = [float(y[b == k].mean()) if (b == k).any() else float("nan") for k in range(6)]
        pp = R.bin_perm_p(y, b, day, 6, nboot, L.SEED)
        return tag, fname, dict(n=int(len(y)), bin_means=means, spread=pp["obs"], p=pp["p"], nperm=pp["nperm"])
    th = R.thirds_in_day(x, day)
    r = R.top_bottom(y, th, day, nd, block, nboot, L.SEED)
    r["n"] = int(len(y))
    return tag, fname, r


def holm_pass(first: dict, second: dict) -> dict:
    """Решение по признакам одного исхода: Холм по 6 на первой половине; «прошёл» — Холм-p < 0,05 и тот же знак на второй половине."""
    ps = [first[f].get("p", 1.0) if not first[f].get("skipped") else 1.0 for f in FNAMES]
    adj = L.holm(ps)
    out = {}
    for f, pa in zip(FNAMES, adj):
        a, b = first[f], second[f]
        if a.get("skipped") or b.get("skipped"):
            out[f] = dict(holm_p=pa, passed=False, same_sign=False, note="нет данных")
            continue
        if f == "F3":
            ma, mb = np.array(a["bin_means"], float), np.array(b["bin_means"], float)
            ok = np.isfinite(ma) & np.isfinite(mb)
            same = bool(ok.sum() >= 3 and np.corrcoef(ma[ok], mb[ok])[0, 1] > 0)
            diff_a = a["spread"]
        else:
            same = bool(np.sign(a["point"]) == np.sign(b["point"]) and a["point"] != 0)
            diff_a = a["point"]
        out[f] = dict(holm_p=float(pa), raw_p=float(a["p"]), same_sign=same, passed=bool(pa < 0.05 and same), first=float(diff_a))
    return out


# ───────────────────────────── запись ─────────────────────────────

SCHEMA = """CREATE TABLE IF NOT EXISTS s5_rows (item TEXT, grp TEXT, half TEXT, key TEXT, val REAL, lo REAL, hi REAL, p REAL, n INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS s5_decision (item TEXT, outcome TEXT, detail TEXT);"""


def analyze(out: str, procs: int = 6) -> dict:
    import multiprocessing as mp

    t00 = time.time()
    tr = dict(np.load(os.path.join(out, "s5", "trades.npz"), allow_pickle=False))
    ep = dict(np.load(os.path.join(out, "s5", "ep.npz"), allow_pickle=False))
    groups = ("FAM", "s4", "S1", "S2", "L401")
    D = {g: group_data(tr, g) for g in groups}
    P = {(g, h): prep(D[g], *HALVES[h]) for g in groups for h in HALVES}
    blk = {h: L.block_len(HALVES[h][1] - HALVES[h][0]) for h in HALVES}
    nb = L.NBOOT
    counts = {f"{g}|{h}": dict(n_raw=D[g]["n_raw"], n_dup=D[g]["n_dup"], n_half=P[g, h]["n_sel"], n_model=P[g, h]["n"], n_days=P[g, h]["n_days"], n_coins=P[g, h]["nc"]) for g in groups for h in HALVES}
    # регрессии
    tasks = []
    for h in HALVES:
        for g in groups:
            tasks.append(((g, h, "alpha6"), (None, P[g, h], "all", "fe", blk[h], nb)))
            tasks.append(((g, h, "win"), (None, P[g, h], "win", "fe", blk[h], nb)))
            tasks.append(((g, h, "loss"), (None, P[g, h], "loss", "fe", blk[h], nb)))
        tasks.append((("FAM", h, "alpha21"), (None, P["FAM", h], "all", "fe", 21, nb)))
        tasks.append((("FAM", h, "win21"), (None, P["FAM", h], "win", "fe", 21, nb)))
        tasks.append((("FAM", h, "loss21"), (None, P["FAM", h], "loss", "fe", 21, nb)))
        tasks.append((("FAM", h, "dayonly"), (None, P["FAM", h], "all", "day", blk[h], nb)))
    args = [(k, a[1], a[2], a[3], a[4], a[5]) for k, a in tasks]
    keys = [k for k, _ in tasks]
    reg = {}
    with mp.Pool(procs) as pool:
        res_list = pool.map(run_reg, args, chunksize=1)
    for k, (_, r) in zip(keys, res_list):
        reg[k] = r
    print(f"регрессии готовы за {time.time() - t00:.0f} с", flush=True)
    # прогноз периодов (семейство; α из модели «сутки + монета» обучающей половины)
    fc = {}
    for h in HALVES:
        a_pt = reg[("FAM", h, "alpha6")]["point"]
        fc[h] = forecast_rows(D["FAM"], a_pt, h, FC_TARGETS[h], DESC_PERIODS)
    fc_ct = {h: dict(correct=int(sum(r.get("correct", False) for r in fc[h] if r.get("role") == "прогноз")), total=int(sum(1 for r in fc[h] if r.get("role") == "прогноз"))) for h in fc}
    # Г2
    g2in = {h: dict(alpha6=reg[("FAM", h, "alpha6")], alpha21=reg[("FAM", h, "alpha21")], win=reg[("FAM", h, "win")], loss=reg[("FAM", h, "loss")]) for h in HALVES}
    g2in["fc"] = fc_ct
    g2 = decide_g2(g2in)
    # признаки: О1 (сделки семейства, FJ → SEL), О2 (подходы SEL, wa+wb → wc)
    ftasks = []
    for h in HALVES:
        lo, hi = HALVES[h]
        d = D["FAM"]
        sel = (d["eday"] >= lo) & (d["eday"] < hi)
        day = (d["eday"][sel] - lo).astype(np.int64)
        for f in FNAMES:
            ftasks.append((("O1", h), f, d[FKEY[f]][sel].astype(np.float64), d["pnl"][sel].astype(np.float64), day, hi - lo, L.block_len(hi - lo), nb))
    for h, (lo, hi) in O2_HALVES.items():
        sel = (ep["eday"] >= lo) & (ep["eday"] < hi)
        day = (ep["eday"][sel] - lo).astype(np.int64)
        for f in FNAMES:
            ftasks.append((("O2", h), f, ep[FKEY[f]][sel].astype(np.float64), ep["y_n"][sel].astype(np.float64), day, hi - lo, L.block_len(hi - lo), nb))
    with mp.Pool(procs) as pool:
        fres = pool.map(feat_task, ftasks, chunksize=1)
    feat = {}
    for tag, f, r in fres:
        feat.setdefault(tag, {})[f] = r
    fdec = {"O1": holm_pass(feat[("O1", "FJ")], feat[("O1", "SEL")]), "O2": holm_pass(feat[("O2", "wa+wb")], feat[("O2", "wc")])}
    passed = []
    for f in FNAMES:
        p1, p2 = fdec["O1"][f], fdec["O2"][f]
        if p1["passed"] or p2["passed"]:
            ps = [x["holm_p"] for x in (p1, p2) if x["passed"]]
            passed.append(dict(feature=f, o1=p1["passed"], o2=p2["passed"], both=bool(p1["passed"] and p2["passed"]), min_holm_p=float(min(ps))))
    passed.sort(key=lambda r: r["min_holm_p"])
    admit = [r["feature"] for r in passed[:3]]
    # контроль оборота: F2 «верхняя − нижняя треть» внутри каждой трети оборота (описательно)
    ctrl = {}
    for h in HALVES:
        pm = P["FAM", h]
        lo, hi = HALVES[h]
        d = D["FAM"]
        m = ok_mask(d, lo, hi)
        day, th = pm["day"], pm["th"]
        f2, y = d["F2"][m], d["pnl"][m]
        for k in range(3):
            s = th == k
            r = R.top_bottom(y[s], R.thirds_in_day(f2[s], day[s]), day[s], hi - lo, blk[h], nb, L.SEED)
            ctrl[f"{h}|turn{k}"] = r
    # метрики рядов (В-169)
    stats = {f"{g}|{h}": day_stats(D[g], *HALVES[h]) for g in groups for h in HALVES}
    result = dict(counts=counts, reg={f"{k[0]}|{k[1]}|{k[2]}": v for k, v in reg.items()}, forecast=fc, forecast_count=fc_ct, g2=g2,
                  features={f"{k[0]}|{k[1]}": v for k, v in feat.items()}, feature_decision=fdec, admitted=passed, step8=admit, turnover_control=ctrl, day_stats=stats,
                  marks=["единица V выбрана на этих данных (12 кандидатов шага 3, не по прибыли)", "SEL семейства B и S1/S2/L401: состав c539 найден на этих данных (L-334)",
                         "связь F1 с y_n частично по построению (y_n в единицах V)", "F5 у сделок О1 — цена уровня = цена входа (приближение, L-477)"], seconds=time.time() - t00)
    json.dump(result, open(os.path.join(out, "s5_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    write_db(out, result)
    print(f"готово за {time.time() - t00:.0f} с; Г2: {g2['outcome']}; в шаг 8: {admit}", flush=True)
    return result


def write_db(out: str, r: dict):
    db = sqlite3.connect(os.path.join(out, "tk727.sqlite"))
    db.executescript(SCHEMA)
    db.execute("DELETE FROM s5_rows")
    db.execute("DELETE FROM s5_decision")
    rows = []
    for k, v in r["reg"].items():
        g, h, kind = k.split("|")
        rows.append(("alpha", g, h, kind, v.get("point"), v.get("lo"), v.get("hi"), v.get("p"), v.get("n_rows"), f"{v.get('model')} блок {v.get('block')}"))
    for h, lst in r["forecast"].items():
        for x in lst:
            rows.append(("forecast", "FAM", h, x["period"], x.get("pred"), x.get("actual"), x.get("share_below_vstar"), None, x.get("n"), f"верно={x.get('correct')} V̄={x.get('V_mean')} ȳ={x.get('y_mean')} V*={x.get('vstar')} {x.get('role')}"))
    for k, v in r["features"].items():
        o, h = k.split("|")
        for f, x in v.items():
            if x.get("skipped"):
                continue
            rows.append(("feat", o, h, f, x.get("point", x.get("spread")), x.get("lo"), x.get("hi"), x.get("p"), x.get("n"), "разброс по корзинам" if f == "F3" else "верх − низ"))
    for k, v in r["turnover_control"].items():
        h, t = k.split("|")
        rows.append(("ctrl_F2", "FAM", h, t, v.get("point"), v.get("lo"), v.get("hi"), v.get("p"), v.get("n_top", 0) + v.get("n_bottom", 0), "F2 верх − низ внутри трети оборота"))
    for k, v in r["day_stats"].items():
        g, h = k.split("|")
        for kk, vv in v.items():
            rows.append(("stats", g, h, kk, vv, None, None, None, None, None))
    db.executemany("INSERT INTO s5_rows VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    db.execute("INSERT INTO s5_decision VALUES(?,?,?)", ("G2", r["g2"]["outcome"], json.dumps(r["g2"], ensure_ascii=False)))
    db.execute("INSERT INTO s5_decision VALUES(?,?,?)", ("step8", ",".join(r["step8"]), json.dumps(r["admitted"], ensure_ascii=False)))
    db.commit()
    db.close()
