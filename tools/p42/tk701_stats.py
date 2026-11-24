#!/usr/bin/env python3
"""TK-701 (П-42, описательно): расхождение сторон «шорт − лонг» по семейству статистик на сделках пары n_bounce1+s4 (лонг 3 270, шорт 2 242; c539, 01.07–13.11; найдено на этих данных, пара выбрана после просмотра P&L TK-698).
Семейство (размер печатается и пишется в БД ДО перестановок и до чтения любой статистики):
  A — среднее признака / исхода по сделкам стороны (исходы после входа: ход r<τ>, MFE / MAE, барьеры, гонки, возвраты, поток после входа, рынок после входа; до входа: скорость пробоя, поток, стакан, рынок, производные);
  B — средний чистый P&L сделки (`val` = gross − fees, движок) в корзине условия: терцили пула обеих сторон для непрерывных (границы без P&L), категории для часа (4 ч) / дня недели / месяца. Исходы после входа условием не бывают (не оси).
Статистика = среднее шорт − среднее лонг. Нуль — обмен меток стороны целыми сутками (200 перестановок, seed 701); z = (D − среднее по перестановкам) / SD; порог — квантиль 95 % max |z| по всему семейству (max-T).
Корзина / столбец входит в семейство, если на каждой стороне ≥ 50 значений (по счёту, без P&L). Просмотр выживших — только после печати размера семейства.
Выжившие (i) проверяются: (ii) знак по месяцам; (iii) без главной монеты и без двух главных суток (тот же знак и |z| ≥ 2); (v) внутри монеты (монеты с обеими сторонами, вес min(nL, nS)): тот же знак и |z_внутри| ≥ 2; (vi) срез по тренду BTC за 7 суток.
Запуск на srv: python3 tk701_stats.py <run_key>   (TK698_DB=/data/p42-tk701/res701.sqlite)"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK698_DB", "/data/p42-tk701/res701.sqlite")
import tk698_db as D  # noqa: E402
import tk698_lib as L  # noqa: E402
import tk701_tape as T  # noqa: E402

ROOT = "/data/p42-tk701"
LINES = {"L": "L_nb1s4", "S": "S_nb1s4"}
NPERM = 200
SEED = 701
MINN = 50
MIN_M = 30
NOTIONAL = 500.0
FEE = T.FEE_ROUND
TAUS, XS, YS = T.TAUS, T.XS, T.YS
HORIZ = (60, 300, 3600)
RACES = ((0.5, 0.5), (1.0, 1.0), (2.0, 1.0))
BD_COLS = ["bd_imb02", "bd_imb1", "bd_imb2", "bd_thin5", "bd_thin30", "bd_depth_turn"]
DERIV_COLS = ["oi_c1", "oi_c4", "oi_c24", "ls_tc", "ls_ts", "ls_a", "dls_tc", "dls_ts", "dls_a", "fund_last", "fund_chg24", "basis"]
FLOW_COLS = ["tfv10", "tfv60", "tfv300", "tfv1800", "tfn10", "tfn60", "tfn300", "tfn1800", "acc_v", "acc_n", "lmsz300", "msz_r"]
MK_BEFORE = ["mk_btc_s5", "mk_btc_s15", "mk_btc_s60", "mk_btc_s240", "mk_btc_s1440", "mk_eth_s5", "mk_eth_s15", "mk_eth_s60", "mk_own_s15", "mk_own_s60", "mk_rel_s60", "mk_rv5_btc_60"]
COV_LIMITED = set(BD_COLS) | set(DERIV_COLS) | {"ls_a_s", "ls_tc_s", "ls_ts_s", "fund_s", "basis_s"}


def cj(o):
    """dict с numpy-значениями → обычный dict для JSON."""
    return json.loads(json.dumps(o, default=lambda v: v.item() if hasattr(v, "item") else str(v)))


def load_master() -> pd.DataFrame:
    parts = []
    for line in (LINES["L"], LINES["S"]):
        tr = pd.read_parquet(os.path.join(T.TR_ROOT, "trades", line + ".parquet"))
        tp = pd.concat([pd.read_parquet(os.path.join(ROOT, "tape", "%s-%s.parquet" % (line, w))) for w in L.WINDOWS], ignore_index=True)
        ax = pd.concat([pd.read_parquet(os.path.join(ROOT, "aux", "%s-%s.parquet" % (line, w))) for w in L.WINDOWS], ignore_index=True)
        pth = pd.concat([pd.read_parquet(os.path.join(T.TR_ROOT, "path", "%s-%s.parquet" % (line, w)))[["sym", "entry_ms", "natr"]] for w in L.WINDOWS], ignore_index=True)
        m = tr.merge(pth, on=["sym", "entry_ms"], how="left", validate="one_to_one").merge(tp, on=["sym", "entry_ms"], how="left", validate="one_to_one").merge(ax, on=["sym", "entry_ms"], how="left", validate="one_to_one")
        assert len(m) == len(tr), line
        m["z"] = 0 if line.startswith("L_") else 1      # 0 = лонг, 1 = шорт
        parts.append(m)
    df = pd.concat(parts, ignore_index=True)
    df["s"] = np.where(df["z"] == 0, 1, -1)
    df["month"] = L.MONTH_OF_DAY[df["day"].to_numpy()]
    df["hour4"] = ((df["sig_ms"] // 3_600_000) % 24 // 4).astype(int)
    df["wday"] = (((df["sig_ms"] // 86_400_000) + 3) % 7).astype(int)
    df["hold_min"] = (df["exit_ms"] - df["entry_ms"]) / 60000.0
    return df


def derive(df: pd.DataFrame) -> dict:
    """Столбцы семейства A (исходы / признаки) и условия B. Возвращает (outcomes: имя → массив, conds: имя → массив)."""
    natr = df["natr"].to_numpy(float)
    nv = natr > 0
    n = len(df)
    nan = np.full(n, np.nan)
    h_ok = df["h_ok"].fillna(False).to_numpy(bool)
    hv = h_ok & nv
    out, cond = {}, {}
    for tau in TAUS:
        out["r%d_usd" % tau] = df["r%d" % tau].to_numpy(float) * NOTIONAL
        out["mfe%d_n" % tau] = np.where(nv, df["mfe%d" % tau].to_numpy(float) / np.where(nv, natr, 1.0), np.nan)
        out["mae%d_n" % tau] = np.where(nv, df["mae%d" % tau].to_numpy(float) / np.where(nv, natr, 1.0), np.nan)
    dn = {y: df["dn%g" % y].to_numpy(float) for y in YS}
    up = {x: df["up%g" % x].to_numpy(float) for x in XS}
    for y in YS:
        for hz in HORIZ:
            out["dn%g_le%d" % (y, hz)] = np.where(hv, (dn[y] <= hz).astype(float), np.nan)
    for x in XS:
        for hz in HORIZ:
            out["up%g_le%d" % (x, hz)] = np.where(hv, (up[x] <= hz).astype(float), np.nan)
    out["stop4_le3600"] = np.where(h_ok, df["stop4"].notna().to_numpy().astype(float), np.nan)
    race_pnl = {}
    for x, y in RACES:
        ux, dy = up[x], dn[y]
        up_first = np.isfinite(ux) & (~np.isfinite(dy) | (ux < dy))
        dn_first = np.isfinite(dy) & (~np.isfinite(ux) | (dy < ux))
        out["race_up_%g_%g" % (x, y)] = np.where(hv, up_first.astype(float), np.nan)
        out["race_dn_%g_%g" % (x, y)] = np.where(hv, dn_first.astype(float), np.nan)
        gross = np.where(up_first, x * natr * NOTIONAL, np.where(dn_first, -y * natr * NOTIONAL, df["r3600"].to_numpy(float) * NOTIONAL))
        race_pnl[(x, y)] = np.where(hv, gross - FEE, np.nan)
        out["race_pnl_%g_%g" % (x, y)] = race_pnl[(x, y)]
    for x in XS:
        ev = df["ret%g" % x].notna().to_numpy()
        out["ret%g_ev" % x] = np.where(hv, ev.astype(float), np.nan)
        for c in ("c900", "c3600"):
            v = df["ret%g_%s" % (x, c)].to_numpy(float)
            out["ret%g_%s_n" % (x, c)] = np.where(nv, v / np.where(nv, natr, 1.0), np.nan)
    out["t_mfeH_s"] = np.where(h_ok, df["t_mfeH"].to_numpy(float), np.nan)
    out["t_maeH_s"] = np.where(h_ok, df["t_maeH"].to_numpy(float), np.nan)
    out["pflow10"], out["pflow60"] = df["pflow10"].to_numpy(float), df["pflow60"].to_numpy(float)
    for nm in ("aft_btc15", "aft_btc60", "aft_eth15", "aft_eth60"):
        out[nm] = df[nm].to_numpy(float)
    # до входа (условия и среднее): знаковый ход в NATR, поток, стакан, рынок, производные
    for k in T.PRE:
        v = df["pre_r%d" % k].to_numpy(float)
        cond["pre_n%d" % k] = np.where(nv, v / np.where(nv, natr, 1.0), np.nan)
    cond["pre_ticks60"] = df["pre_n60"].to_numpy(float)
    for c in FLOW_COLS + BD_COLS + MK_BEFORE + ["btc_trend7"]:
        cond[c] = df[c].to_numpy(float)
    for c in DERIV_COLS:
        cond[c] = df[c].to_numpy(float)
    s = df["s"].to_numpy(float)
    cond["ls_a_s"], cond["ls_tc_s"], cond["ls_ts_s"] = s * df["ls_a"].to_numpy(float), s * df["ls_tc"].to_numpy(float), s * df["ls_ts"].to_numpy(float)
    cond["fund_s"], cond["basis_s"] = s * df["fund_last"].to_numpy(float), s * df["basis"].to_numpy(float)
    cond["natr"] = natr
    cond["turn24"] = df["turn24"].to_numpy(float)
    return out, cond, race_pnl


def build_family(df, out, cond):
    """Список столбцов семейства (имя, семейство, вид, условие, корзина) и матрица значений X (n × m, NaN = нет)."""
    z = df["z"].to_numpy()
    val = df["val"].to_numpy(float)
    cols, X = [], []

    def ok_counts(x):
        v = ~np.isnan(x)
        return int((v & (z == 0)).sum()), int((v & (z == 1)).sum())

    for name, x in out.items():
        a, b = ok_counts(x)
        if a >= MINN and b >= MINN:
            fam = "A_outcome"
            cols.append((name, fam, "A", name, ""))
            X.append(x)
    for name, x in cond.items():
        a, b = ok_counts(x)
        if a >= MINN and b >= MINN:
            cols.append((name, "A_pre", "A", name, ""))
            X.append(x)
    for name, x in cond.items():
        v = x[~np.isnan(x)]
        if len(v) < 3 * MINN or len(np.unique(v)) < 4:
            continue
        q1, q2 = np.quantile(v, [1 / 3, 2 / 3])
        b = np.where(np.isnan(x), -1, np.where(x <= q1, 0, np.where(x <= q2, 1, 2)))
        for k in range(3):
            m = b == k
            if (m & (z == 0)).sum() >= MINN and (m & (z == 1)).sum() >= MINN:
                cols.append(("%s|t%d" % (name, k), "B_cond", "B", name, "t%d [%.4g, %.4g]" % (k, v.min() if k == 0 else (q1 if k == 1 else q2), q1 if k == 0 else (q2 if k == 1 else v.max()))))
                X.append(np.where(m, val, np.nan))
    for name, key, nb in (("hour4", "hour4", 6), ("wday", "wday", 7), ("month", "month", 5)):
        b = df[key].to_numpy()
        for k in range(nb):
            m = b == k
            if (m & (z == 0)).sum() >= MINN and (m & (z == 1)).sum() >= MINN:
                cols.append(("%s|%d" % (name, k), "B_cond", "B", name, str(k)))
                X.append(np.where(m, val, np.nan))
    return cols, np.column_stack(X)


def side_means(X, zz):
    V = ~np.isnan(X)
    X0 = np.where(V, X, 0.0)
    w1 = zz.astype(float)
    w0 = 1.0 - w1
    s1, s0 = w1 @ X0, w0 @ X0
    n1, n0 = w1 @ V.astype(float), w0 @ V.astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        m1, m0 = s1 / n1, s0 / n0
    return m1, m0, n1, n0, s1, s0


def perms(df, X, rng):
    z = df["z"].to_numpy()
    day = df["day"].to_numpy()
    D_obs = np.subtract(*side_means(X, z)[:2])
    Dp = np.empty((NPERM, X.shape[1]))
    swaps = rng.random((NPERM, L.NDAYS)) < 0.5
    for k in range(NPERM):
        zz = z ^ swaps[k][day].astype(z.dtype)
        m1, m0, n1, n0, _, _ = side_means(X, zz)
        d = m1 - m0
        d[(n1 < 20) | (n0 < 20)] = np.nan
        Dp[k] = d
    return D_obs, Dp, swaps


def month_signs(df, x, z, month):
    out = []
    for mo in range(5):
        m = month == mo
        v = ~np.isnan(x)
        a, b = (m & v & (z == 0)).sum(), (m & v & (z == 1)).sum()
        out.append((int(a), int(b), float(np.nanmean(x[m & (z == 1)]) - np.nanmean(x[m & (z == 0)])) if a >= 1 and b >= 1 else float("nan")))
    return out


def within_coin(x, z, coin):
    v = ~np.isnan(x)
    nC = coin.max() + 1
    c1 = np.bincount(coin, weights=(v & (z == 1)).astype(float), minlength=nC)
    c0 = np.bincount(coin, weights=(v & (z == 0)).astype(float), minlength=nC)
    x0 = np.where(v, x, 0.0)
    s1 = np.bincount(coin, weights=x0 * (z == 1), minlength=nC)
    s0 = np.bincount(coin, weights=x0 * (z == 0), minlength=nC)
    ok = (c1 > 0) & (c0 > 0)
    if not ok.any():
        return float("nan"), 0
    w = np.minimum(c1, c0)[ok]
    d = (s1[ok] / c1[ok]) - (s0[ok] / c0[ok])
    return float((w * d).sum() / w.sum()), int(ok.sum())


def main(run_key):
    t0 = time.time()
    df = load_master()
    out, cond, race_pnl = derive(df)
    cols, X = build_family(df, out, cond)
    fam_size = len(cols)
    names = [c[0] for c in cols]
    print("FAMILY SIZE", fam_size, "(A_outcome %d, A_pre %d, B_cond %d)" % tuple(sum(1 for c in cols if c[1] == f) for f in ("A_outcome", "A_pre", "B_cond")), "trades", len(df), flush=True)
    db = D.connect()
    D.new_run(db, run_key, "tk701-stats", "tools/p42/tk701_stats.py", "расхождение сторон шорт − лонг на паре n_bounce1+s4; найдено на этих данных; окно 01.07–13.11")
    D.put(db, "res_text", run_key, [("family_size", json.dumps(dict(total=fam_size, names=names), ensure_ascii=False))], ["key", "value"])
    rng = np.random.default_rng(SEED)
    D_obs, Dp, swaps = perms(df, X, rng)
    mu, sd = np.nanmean(Dp, axis=0), np.nanstd(Dp, axis=0)
    okc = sd > 0
    Zp = (Dp - mu) / np.where(okc, sd, np.nan)
    tmax = np.nanmax(np.abs(Zp[:, okc]), axis=1)
    t95 = float(np.quantile(tmax, 0.95))
    Zo = (D_obs - mu) / np.where(okc, sd, np.nan)
    pval = np.array([((np.abs(Zp[:, j]) >= abs(Zo[j])).sum() + 1) / (NPERM + 1) if okc[j] else np.nan for j in range(fam_size)])
    z = df["z"].to_numpy()
    m1, m0, n1, n0, s1, s0 = side_means(X, z)
    rows = []
    for j, (name, fam, kind, cnd, binlab) in enumerate(cols):
        extra = dict(kind=kind, cond=cnd, bin=binlab, mean_short=float(m1[j]), mean_long=float(m0[j]), sum_short=float(s1[j]), sum_long=float(s0[j]), sd_perm=float(sd[j]), mu_perm=float(mu[j]), p=float(pval[j]), pass_i=bool(okc[j] and abs(Zo[j]) > t95))
        rows.append((name, fam, None, float(D_obs[j]), float(Zo[j]) if okc[j] else None, int(n1[j]), int(n0[j]), cj(extra)))
    D.put(db, "res_div", run_key, rows, ["col", "family", "rank", "div", "noise_hi", "n_short", "n_long", "extra"])
    D.put(db, "res_text", run_key, [("t95_maxT", json.dumps(dict(t95=t95, nperm=NPERM, seed=SEED, trials=fam_size)))], ["key", "value"])
    surv = [j for j in range(fam_size) if okc[j] and abs(Zo[j]) > t95]
    print("t95 maxT %.3f, survivors (i): %d of %d" % (t95, len(surv), fam_size), flush=True)
    # ---- проверки выживших
    month = df["month"].to_numpy()
    coin = pd.factorize(df["sym"])[0]
    day = df["day"].to_numpy()
    trend = cond["btc_trend7"]
    chk = []
    for j in surv:
        x = X[:, j]
        ms = month_signs(df, x, z, month)
        sgn = np.sign(D_obs[j])
        valid_m = [(a, b, d) for a, b, d in ms if a >= MIN_M and b >= MIN_M]
        cov_lim = cols[j][3] in COV_LIMITED
        if cov_lim:
            valid_m = [(a, b, d) for a, b, d in ms if a >= 100 and b >= 100]
            ok_ii = len(valid_m) >= 2 and all(np.sign(d) == sgn for _, _, d in valid_m)
        else:
            ok_ii = len(valid_m) >= 4 and sum(np.sign(d) == sgn for _, _, d in valid_m) >= 4
        # (iii) без главной монеты и без двух главных суток
        v = ~np.isnan(x)
        def dval(mask):
            a, b = mask & v & (z == 0), mask & v & (z == 1)
            return (np.mean(x[b]) - np.mean(x[a])) if a.sum() and b.sum() else np.nan
        infl_c = {c: abs(D_obs[j] - dval(coin != c)) for c in np.unique(coin[v])}
        top_c = max(infl_c, key=lambda c: infl_c[c] if np.isfinite(infl_c[c]) else -1)
        d_c = dval(coin != top_c)
        infl_d = {d: abs(D_obs[j] - dval(day != d)) for d in np.unique(day[v])}
        top_d = sorted(infl_d, key=lambda d: -(infl_d[d] if np.isfinite(infl_d[d]) else -1))[:2]
        d_d = dval(~np.isin(day, top_d))
        zc, zd = (d_c - mu[j]) / sd[j], (d_d - mu[j]) / sd[j]
        ok_iii = bool(np.sign(d_c) == sgn and abs(zc) >= 2 and np.sign(d_d) == sgn and abs(zd) >= 2)
        # (v) внутри монеты
        dw, ncoin = within_coin(x, z, coin)
        dwp = np.array([within_coin(x, z ^ swaps[k][day].astype(z.dtype), coin)[0] for k in range(NPERM)])
        zw = (dw - np.nanmean(dwp)) / np.nanstd(dwp) if np.nanstd(dwp) > 0 else np.nan
        ok_v = bool(np.sign(dw) == sgn and abs(zw) >= 2)
        # (vi) тренд BTC за 7 суток
        up, dnm = trend > 0, trend <= 0
        d_up = dval(up & ~np.isnan(trend)) if (up & v).any() else np.nan
        d_dn = dval(dnm & ~np.isnan(trend)) if (dnm & v).any() else np.nan
        chk.append(dict(col=cols[j][0], family=cols[j][1], D=float(D_obs[j]), z=float(Zo[j]), months=[(a, b, None if np.isnan(d) else float(d)) for a, b, d in ms], ok_ii=bool(ok_ii), top_coin=str(df["sym"].iloc[int(np.flatnonzero(coin == top_c)[0])]), D_drop_coin=float(d_c), z_drop_coin=float(zc), top_days=[int(d) for d in top_d], D_drop_days=float(d_d), z_drop_days=float(zd), ok_iii=ok_iii,
                        D_within=float(dw), z_within=float(zw), coins_both=ncoin, ok_v=ok_v, D_btc_up=None if np.isnan(d_up) else float(d_up), D_btc_dn=None if np.isnan(d_dn) else float(d_dn)))
    D.put(db, "res_split", run_key, [("survivor", c["col"], c["family"], "check", "", 0, 0, c["D"], c["z"], None, None, cj(c)) for c in chk], ["item", "line", "dim", "market", "bin", "n_days", "n", "usd", "per_trade", "per_day", "share", "extra"])
    n_ok = sum(1 for c in chk if c["ok_ii"] and c["ok_iii"] and c["ok_v"])
    print("survivors passing (ii)+(iii)+(v):", n_ok, "of", len(chk), "sec", round(time.time() - t0, 1), flush=True)
    # ---- гонки в $ (равная геометрия на ленте): метрики стороны по суткам
    rr = []
    for (x_, y_), pnl in race_pnl.items():
        for zz, nm in ((0, "L"), (1, "S")):
            m = (z == zz) & ~np.isnan(pnl)
            daily = np.bincount(day[m], weights=pnl[m], minlength=L.NDAYS)
            me = L.metrics(daily)
            rr.append(("race", "%s_%g_%g" % (nm, x_, y_), "pnl", "tape", "", int((daily != 0).sum()), int(m.sum()), float(pnl[m].sum()), float(pnl[m].mean()), None, None, dict(me, nd=int(m.sum()))))
    # ---- дескриптивные таблицы: виды выхода движка, разворот убыточных, после выхода
    for zz, nm in ((0, "L"), (1, "S")):
        d = df[df["z"] == zz]
        daily = L.daily_pnl(d)
        me = L.metrics(daily)
        rr.append(("line", nm, "all", "engine", "", int((daily != 0).sum()), int(len(d)), float(d["val"].sum()), float(d["val"].mean()), None, None, dict(me)))
        for k in sorted(d["kind"].unique()):
            e = d[d["kind"] == k]
            rr.append(("kind", nm, k, "engine", "", 0, int(len(e)), float(e["val"].sum()), float(e["val"].mean()), None, float(len(e) / len(d)), dict(hold_min=float(e["hold_min"].mean()))))
            for c in T.POST_EXIT:
                pv = e["post%d" % c].to_numpy(float) * NOTIONAL
                ns = e["natr"].to_numpy(float)
                rr.append(("post_exit", nm, k, "tape", str(c), 0, int(np.isfinite(pv).sum()), float(np.nansum(pv)), float(np.nanmean(pv)) if np.isfinite(pv).any() else None, None, None,
                           dict(share_beyond_minus1natr=float(np.nanmean((e["post%d" % c].to_numpy(float) / ns) <= -1.0)), share_beyond_plus1natr=float(np.nanmean((e["post%d" % c].to_numpy(float) / ns) >= 1.0)))))
        loss = d[d["val"] < 0]
        tm = loss["g_t_mae"].to_numpy(float)
        rr.append(("loser_turn", nm, "loss", "engine", "", 0, int(len(loss)), float(loss["val"].sum()), float(loss["val"].mean()), None, None,
                   dict(t_mae_min_mean=float(np.nanmean(tm)), hold_min_mean=float(loss["hold_min"].mean()), mae_first_share=float(np.nanmean(~loss["g_green_first"].fillna(False).to_numpy(bool))),
                        t_mae_le1=float(np.nanmean(tm <= 1)), t_mae_le5=float(np.nanmean(tm <= 5)), t_mae_le15=float(np.nanmean(tm <= 15)), t_mae_gt15=float(np.nanmean(tm > 15)))))
    D.put(db, "res_split", run_key, [r[:-1] + (cj(r[-1]),) for r in rr], ["item", "line", "dim", "market", "bin", "n_days", "n", "usd", "per_trade", "per_day", "share", "extra"])
    D.put(db, "res_text", run_key, [("coverage", json.dumps({nm: {c: round(float(np.mean(~np.isnan(x[z == zz]))), 4) for c, x in {**out, **cond}.items()} for zz, nm in ((0, "L"), (1, "S"))}))], ["key", "value"])
    print("готово, сек", round(time.time() - t0, 1), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
