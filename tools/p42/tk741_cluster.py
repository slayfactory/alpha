#!/usr/bin/env python3
"""TK-741 (В-192): шаг 2 — кластеры сделок базы V0nt по сетапу (картина ДО сигнальной минуты, только минутки ≤ t−1), описательно. Только srv.

  python3 tk741_cluster.py run       # → res/cluster.json, res/cluster.txt, таблицы t741_trade / t741_cluster / t741_sil в /data/p42-tk741/tk741.sqlite

Признаки (на сделку; t = минута сигнала `sig_min`, бары ≤ t−1, цена уровня L — из дампа сигналов движка, цена закрытия минут — с пропусками закрыта предыдущей):
  r15, r60, r240 — доходность закрытия за 15 / 60 / 240 мин, %; rv60 — RV60 (корень суммы квадратов минутных лог-доходностей за 60 мин), %;
  near — доля из последних 60 минут, где закрытие в полосе подхода ±0,3 % от L; touch — число касаний L за 240 мин (минуты с low ≤ L·1,001, подряд идущие — одно);
  age — возраст уровня, часы (от первого пивота `first_bar`·60 мин); turn — оборот 15 мин ÷ средний оборот 15 мин за 24 ч; stab — число минут за 60 мин с проколом low < L и закрытием ≥ L (каскад);
  hi24 / lo24 — расстояние от максимума / минимума за 24 ч до закрытия t−1, %; btc60 — доходность BTCUSDT за 60 мин, %.
Преобразования: log для rv60, turn, hi24, lo24; log1p для touch, age, stab; затем стандартизация по сделкам FJ ∪ SEL, обрезка ±4. k-means (k-means++, 40 стартов, seed 20261127), k ∈ 3…8 — по силуэту; P&L в выборе не участвует.
Кластеры строятся на сделках FJ ∪ SEL (фев–13.11); январь и N1 относятся к ближайшему центру (описательно). «Найдено на этих данных».
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk741_lib as T  # noqa: E402

A, L = T.A, T.L
DB = T.ROOT + "/tk741.sqlite"
SEED = 20261127
BAND, TOUCH_PCT, STAKE = 0.003, 0.001, 500.0
FEATS = ["r15", "r60", "r240", "rv60", "near", "touch", "age", "turn", "stab", "hi24", "lo24", "btc60"]
LOGF, LOG1P = {"rv60", "turn", "hi24", "lo24"}, {"touch", "age", "stab"}
OUT = []
_cc = {}


def say(*a):
    s = " ".join(str(x) for x in a)
    OUT.append(s)
    print(s)


def bars(win, sym):
    """(o, h, l, c, qv, c_ffill) float по минутам окна; None — нет файла."""
    k = (win, sym)
    if k not in _cc:
        fn = os.path.join(A.TREES[win], "cache", sym + ".b42")
        if not os.path.exists(fn):
            _cc[k] = None
        else:
            with open(fn, "rb") as f:
                n = int.from_bytes(f.read(8)[4:8], "little")
            a = np.fromfile(fn, dtype="<i8", offset=8, count=5 * n).reshape(5, n).astype(np.float64)
            pres = a[0] != 0
            c = np.where(pres, a[3], np.nan)
            idx = np.where(pres, np.arange(n), 0)
            np.maximum.accumulate(idx, out=idx)
            cf = np.where(np.cumsum(pres) > 0, c[idx], np.nan)
            _cc[k] = (a[0], a[1], a[2], a[3], a[4], cf, pres)
    return _cc[k]


def features(b, btc, t, lvl, first_bar):
    o, h, l, c, qv, cf, pres = b
    e8 = 1e8
    lvl8 = lvl * e8
    cl = cf[t - 1]
    f = {}
    for k, m in (("r15", 15), ("r60", 60), ("r240", 240)):
        f[k] = (cl / cf[t - 1 - m] - 1.0) * 100.0
    lr = np.diff(np.log(cf[t - 61:t]))
    f["rv60"] = float(np.sqrt(np.nansum(lr ** 2)) * 100.0)
    c60, p60 = c[t - 60:t], pres[t - 60:t]
    f["near"] = float((np.abs(c60[p60] / lvl8 - 1.0) <= BAND).sum() / 60.0)
    lo240, p240 = l[t - 240:t], pres[t - 240:t]
    hit = p240 & (lo240 <= lvl8 * (1.0 + TOUCH_PCT))
    f["touch"] = int(hit[0]) + int((hit[1:] & ~hit[:-1]).sum())
    f["age"] = max(0.0, (t - first_bar * 60) / 60.0)
    q15, q24 = qv[t - 15:t].sum(), qv[t - 1440:t].sum()
    f["turn"] = float(q15 / (q24 / 96.0)) if q24 > 0 else np.nan
    l60, cc60 = l[t - 60:t], c[t - 60:t]
    f["stab"] = int((p60 & (l60 < lvl8) & (cc60 >= lvl8)).sum())
    hmax = np.max(np.where(pres[t - 1440:t], h[t - 1440:t], -np.inf))
    lmin = np.min(np.where(pres[t - 1440:t], l[t - 1440:t], np.inf))
    f["hi24"] = float((hmax - cl) / cl * 100.0)
    f["lo24"] = float((cl - lmin) / cl * 100.0)
    bc = btc[5]
    f["btc60"] = float((bc[t - 1] / bc[t - 61] - 1.0) * 100.0)
    return f


def transform(F):
    X = F.copy()
    for j, k in enumerate(FEATS):
        if k in LOGF:
            X[:, j] = np.log(np.maximum(X[:, j], 1e-3))
        elif k in LOG1P:
            X[:, j] = np.log1p(np.maximum(X[:, j], 0.0))
    return X


def kmeans(X, k, seed, n_init=40, iters=200):
    best = None
    for s in range(n_init):
        rng = np.random.default_rng([seed, k, s])
        cen = [X[rng.integers(len(X))]]
        for _ in range(1, k):
            d2 = np.min(((X[:, None, :] - np.array(cen)[None]) ** 2).sum(-1), axis=1)
            cen.append(X[rng.choice(len(X), p=d2 / d2.sum())])
        cen = np.array(cen)
        for _ in range(iters):
            lab = ((X[:, None, :] - cen[None]) ** 2).sum(-1).argmin(1)
            new = np.array([X[lab == j].mean(0) if (lab == j).any() else cen[j] for j in range(k)])
            if np.allclose(new, cen):
                break
            cen = new
        lab = ((X[:, None, :] - cen[None]) ** 2).sum(-1).argmin(1)
        inertia = float(((X - cen[lab]) ** 2).sum())
        if best is None or inertia < best[0] - 1e-12:
            best = (inertia, cen, lab)
    return best


def silhouette(X, lab):
    D = np.sqrt(((X[:, None, :] - X[None]) ** 2).sum(-1))
    ks = np.unique(lab)
    s = np.zeros(len(X))
    for i in range(len(X)):
        own = lab == lab[i]
        if own.sum() <= 1:
            continue
        a_ = D[i, own].sum() / (own.sum() - 1)
        b_ = min(D[i, lab == j].mean() for j in ks if j != lab[i])
        s[i] = (b_ - a_) / max(a_, b_)
    return float(s.mean())


# названия кластеров по их центрам (после просмотра центров k = 3; условия — проверка, что центр похож на описание; при другом k или центрах скрипт останавливается)
NAMES = {
    1: ("Тихий подход к молодому уровню", lambda z, m: z["age"] < 0 and z["turn"] < 0 and z["lo24"] > 0),
    2: ("Падение к старому уровню у минимума суток", lambda z, m: z["age"] > 0 and z["turn"] > 0 and z["lo24"] < 0 and z["btc60"] < 0),
    3: ("Резкий обвал, оборот втрое выше обычного", lambda z, m: z["r60"] < -1.0 and z["rv60"] > 1.0 and z["turn"] > 0),
}


def cluster_stats(pnl, kind):
    st = kind == "stop"
    return dict(n=int(len(pnl)), sum=float(pnl.sum()), mean=float(pnl.mean()) if len(pnl) else None, rr=L.rr(pnl), stops=int(st.sum()), loss_per_stop=float(pnl[st].mean()) if st.any() else None)


def cmd_run(_a):
    a = T.load("nt", T.ALLW, with_sigsx=True)
    n = len(a["pnl"])
    F = np.full((n, len(FEATS)), np.nan)
    for i in range(n):
        w, s = a["win"][i], a["sym"][i]
        b, btc = bars(w, s), bars(w, "BTCUSDT")
        t = int(a["sig_min"][i])
        if b is None or btc is None or t < 1441:
            continue
        f = features(b, btc, t, float(a["lvl"][i]), int(a["first_bar"][i]))
        F[i] = [f[k] for k in FEATS]
    ok = np.isfinite(F).all(1)
    say("сделок V0nt: %d, с полным набором признаков: %d" % (n, ok.sum()))
    fit = np.isin(a["win"], list(T.FJ + T.SEL)) & ok
    X = transform(F)
    mu, sd = X[fit].mean(0), X[fit].std(0)
    Z = np.clip((X - mu) / np.where(sd > 0, sd, 1.0), -4.0, 4.0)
    sil = {}
    fits = {}
    for k in range(3, 9):
        inertia, cen, lab = kmeans(Z[fit], k, SEED)
        sil[k] = silhouette(Z[fit], lab)
        fits[k] = (cen, lab)
        say("k = %d: силуэт %.4f, инерция %.1f, размеры %s" % (k, sil[k], inertia, sorted(np.bincount(lab, minlength=k).tolist(), reverse=True)))
    kbest = max(sil, key=lambda k: (round(sil[k], 6), -k))
    cen, lab_fit = fits[kbest]
    order = np.argsort(-np.bincount(lab_fit, minlength=kbest), kind="stable")      # кластер 1 — самый большой
    rank = np.empty(kbest, int)
    rank[order] = np.arange(kbest)
    cen = cen[order]
    lab = np.full(n, -1)
    idx = np.nonzero(ok)[0]
    lab[idx] = ((Z[idx][:, None, :] - cen[None]) ** 2).sum(-1).argmin(1)
    say("выбрано k = %d (силуэт %.4f)" % (kbest, sil[kbest]))
    # центры в исходных единицах: медиана признаков кластера (по сделкам области подгонки) и стандартизованный центр
    clusters = []
    for j in range(kbest):
        m = fit & (lab == j)
        med = {k: float(np.median(F[m, i])) for i, k in enumerate(FEATS)}
        z = {k: float(cen[j, i]) for i, k in enumerate(FEATS)}
        st = cluster_stats(a["pnl"][m], a["kind"][m])
        st["share"] = float(m.sum() / fit.sum())
        if kbest == 3:
            nm, cond = NAMES[j + 1]
            assert cond(z, med), ("центр кластера не похож на название", j + 1, z)
        else:
            nm = "Кластер %d" % (j + 1)
        clusters.append(dict(id=j + 1, name=nm, med=med, z=z, stats=st))
        say("К%d «%s»: n %d (%.1f %%), Σ %.2f $, средн. %.3f $, R:R %s, стопов %d, $/стоп %s" % (j + 1, nm, st["n"], st["share"] * 100, st["sum"], st["mean"], "—" if st["rr"] is None else "%.2f" % st["rr"], st["stops"],
                                                                                          "—" if st["loss_per_stop"] is None else "%.2f" % st["loss_per_stop"]))
        say("    медианы: " + ", ".join("%s %.2f" % (k, v) for k, v in med.items()))
        say("    центр z: " + ", ".join("%s %+.2f" % (k, v) for k, v in z.items()))
    # описательно: январь и N1 по кластерам
    for nm, ws in (("январь", ("m01",)), ("N1", ("n1",))):
        m = np.isin(a["win"], list(ws)) & ok
        say("%s: %s" % (nm, "; ".join("К%d n %d Σ %.2f" % (j + 1, ((lab == j) & m).sum(), a["pnl"][(lab == j) & m].sum()) for j in range(kbest))))
    # запись: сделки с кластерами и признаками — в БД и json
    ex = (a["entry_px_e8"] / 1e8)
    avg_exit = ex * (1.0 - a["gross"] / STAKE)          # шорт: средняя цена выхода по валовому P&L ставки 500 $
    period = np.where(np.isin(a["win"], list(T.FJ)), "FJ", np.where(np.isin(a["win"], list(T.SEL)), "SEL", np.where(a["win"] == "m01", "JAN", "N1")))
    rows = []
    for i in range(n):
        rows.append(dict(i=i, win=str(a["win"][i]), per=str(period[i]), sym=str(a["sym"][i]), level=int(a["level"][i]), sig_ms=int(a["sig_ms"][i]), entry_ms=int(a["entry_ms"][i]), exit_ms=int(a["exit_ms"][i]),
                         sig_min=int(a["sig_min"][i]), entry_min=int(a["entry_min"][i]), exit_min=int(a["exit_min"][i]), entry_px=float(ex[i]), exit_px=float(avg_exit[i]), lvl=float(a["lvl"][i]), kind=str(a["kind"][i]),
                         gross=float(a["gross"][i]), fees=float(a["fees"][i]), pnl=float(a["pnl"][i]), natr=float(a["natr"][i]), cluster=int(lab[i]) + 1 if lab[i] >= 0 else 0,
                         f={k: (None if not np.isfinite(F[i, j]) else float(F[i, j])) for j, k in enumerate(FEATS)}))
    os.makedirs(T.RES, exist_ok=True)
    res = dict(k=kbest, silhouette={str(k): v for k, v in sil.items()}, feats=FEATS, mu=[float(x) for x in mu], sd=[float(x) for x in sd], clusters=clusters, n_fit=int(fit.sum()), n_trades=n, n_ok=int(ok.sum()))
    json.dump(res, open(T.RES + "/cluster.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(rows, open(T.RES + "/trades.json", "w", encoding="utf-8"), ensure_ascii=False)
    open(T.RES + "/cluster.txt", "w", encoding="utf-8").write("\n".join(OUT) + "\n")
    db = sqlite3.connect(DB)
    db.executescript("DROP TABLE IF EXISTS t741_trade; DROP TABLE IF EXISTS t741_cluster; DROP TABLE IF EXISTS t741_sil;"
                     "CREATE TABLE t741_trade(i INTEGER PRIMARY KEY, win TEXT, per TEXT, sym TEXT, level INTEGER, sig_ms INTEGER, entry_ms INTEGER, exit_ms INTEGER, entry_px REAL, exit_px REAL, lvl REAL, kind TEXT,"
                     " gross REAL, fees REAL, pnl REAL, natr REAL, cluster INTEGER, " + ", ".join("%s REAL" % k for k in FEATS) + ");"
                     "CREATE TABLE t741_cluster(id INTEGER PRIMARY KEY, name TEXT, n INTEGER, share REAL, usd REAL, mean REAL, rr REAL, stops INTEGER, loss_per_stop REAL, med TEXT, z TEXT);"
                     "CREATE TABLE t741_sil(k INTEGER PRIMARY KEY, silhouette REAL, chosen INTEGER);")
    db.executemany("INSERT INTO t741_trade VALUES(%s)" % ",".join("?" * (17 + len(FEATS))),
                   [(r["i"], r["win"], r["per"], r["sym"], r["level"], r["sig_ms"], r["entry_ms"], r["exit_ms"], r["entry_px"], r["exit_px"], r["lvl"], r["kind"], r["gross"], r["fees"], r["pnl"], r["natr"], r["cluster"]) + tuple(r["f"][k] for k in FEATS) for r in rows])
    db.executemany("INSERT INTO t741_cluster VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(c["id"], c["name"], c["stats"]["n"], c["stats"]["share"], c["stats"]["sum"], c["stats"]["mean"], c["stats"]["rr"], c["stats"]["stops"], c["stats"]["loss_per_stop"],
                                                                         json.dumps(c["med"]), json.dumps(c["z"])) for c in clusters])
    db.executemany("INSERT INTO t741_sil VALUES(?,?,?)", [(k, v, int(k == kbest)) for k, v in sil.items()])
    db.commit()
    return 0


if __name__ == "__main__":
    sys.exit({"run": cmd_run}[(sys.argv[1:] or ["run"])[0]](None))
