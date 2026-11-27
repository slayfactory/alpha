#!/usr/bin/env python3
"""TK-722: проверки замороженной базы B шорта по критику плана (`docs/research/reviews/p42-plan-critic-2026-11-27.md`, шаги 0, 1, 3а, 4).

  python3 tk722_checks.py prep  --out <json> [--kill-out <json>]   # srv: шаг 4 (концентрация, знак членов на FJ, PSR / DSR, MinTRL), e-процесс «работает», таблица остановки до n = 365
  python3 tk722_checks.py venue --out <json> --bybit-dir <каталог>  # srv: шаг 1 — монеты без Bybit, цены B против публичных сделок Bybit ±1 с (N1 + форвард)
  python3 tk722_checks.py beta  --out <json>                        # srv: шаг 3а — регрессия суточного P&L B на равновзвешенный рынок c539
  python3 tk722_checks.py null  --out <json> --win SEL|FebNov       # srv: шаг 3б (PS-1049) — B свечами против нуля со сдвигом минуты входа, R = 100

Ряд B — как TK-720 / TK-721: среднее 9 членов, простой P&L (gross − fees), 500 $ на сделку члена, сутки по дате входа от 01.01.2026. Сделки — те же файлы, что TK-721 (`tk721_stress.BASE_RUNS`)
+ форвард TK-720 (`/data/p42-tk720/fwd/<rid>/out/k<cid>/short-trades.csv`, T0 25.10). Окна: FJ — сутки 31…180, SEL — 181…316, Feb–Nov — 31…327 (как TK-721), форвард — с 328.
Без движка; медиан и t-статистик по сделкам нет."""
from __future__ import annotations

import argparse
import csv
import datetime
import gzip
import json
import math
import os
import sys
from statistics import NormalDist

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk720_fwd as F  # noqa: E402
import tk721_stress as S  # noqa: E402

FWD_RID = "fw-2026-11-26"
FWD_DIR = "/data/p42-tk720/fwd/%s/out" % FWD_RID
FWD_T0 = 1792886400000                                              # T0 окна n1 / n2 (25.10.2026)
KLINE_ROOTS = ("/data/p42-tk621/m01", "/data/p42-tk621/m02", "/data/p42-tk621/m03", "/data/p42-tk621/m04", "/data/p42-tk621/m05", "/data/p42-tk621/m06",
               "/data/p42-tk621/wa", "/data/p42-tk621/wb", "/data/p42-tk621/wc", "/data/p42-tk700/n1/tree", "/data/p42-tk720/fwd/%s/tree" % FWD_RID)
FJ = (31, 181)
SEL = (181, 317)
FEBNOV = (31, 328)
KILL_N365 = 365
SEED722 = 722
N_TRIALS = (4, 8)
E_ACCEPT = 20.0
ND = 330
ND_ = NormalDist()


# ───────────────────────────── чистые функции (тесты — test_tk722.py) ─────────────────────────────

def moments(x):
    """Суточные: среднее, σ (ddof 1), SR (без годового множителя), асимметрия γ3, эксцесс γ4 (не избыточный)."""
    x = np.asarray(x, float)
    m, sd = float(x.mean()), float(x.std(ddof=1))
    z = (x - m) / x.std(ddof=0)
    return dict(n=len(x), mean=m, sd=sd, sr=m / sd, g3=float((z ** 3).mean()), g4=float((z ** 4).mean()))


def psr(mo, sr_star=0.0):
    """Probabilistic Sharpe Ratio (Bailey, López de Prado 2012), суточный SR."""
    v = 1.0 - mo["g3"] * mo["sr"] + (mo["g4"] - 1.0) / 4.0 * mo["sr"] ** 2
    return ND_.cdf((mo["sr"] - sr_star) * math.sqrt(mo["n"] - 1) / math.sqrt(v))


def sr0_expected_max(mo, n_trials):
    """Ожидаемый максимум суточного SR шума из N испытаний (DSR, Bailey–López de Prado 2014): дисперсия SR — оценочная (1 − γ3 SR + (γ4 − 1)/4 SR²) / (T − 1) (модель: испытания независимы, истинный SR 0)."""
    g = 0.5772156649
    v = (1.0 - mo["g3"] * mo["sr"] + (mo["g4"] - 1.0) / 4.0 * mo["sr"] ** 2) / (mo["n"] - 1)
    return math.sqrt(v) * ((1 - g) * ND_.inv_cdf(1 - 1.0 / n_trials) + g * ND_.inv_cdf(1 - 1.0 / (n_trials * math.e)))


def min_trl(mo, sr_star=0.0, conf=0.95):
    """Minimum Track Record Length, сутки: 1 + (1 − γ3 SR + (γ4 − 1)/4 SR²) (z / (SR − SR*))²."""
    z = ND_.inv_cdf(conf)
    v = 1.0 - mo["g3"] * mo["sr"] + (mo["g4"] - 1.0) / 4.0 * mo["sr"] ** 2
    return 1.0 + v * (z / (mo["sr"] - sr_star)) ** 2


def log_e(x, mu1, sd):
    """ln e-процесса (отношение правдоподобий нормальных, H1 среднее mu1 против H0 среднее 0, σ = sd) по префиксам ряда x."""
    x = np.asarray(x, float)
    return np.cumsum(mu1 * x / sd ** 2 - mu1 ** 2 / (2 * sd ** 2), axis=-1)


def extend_paths(n_src, n_total, length, boot, seed, seed_ext):
    """Индексы путей бутстрепа: первые n_src суток — ровно пути `tk720_fwd.kill_tables` (`block_idx(n_src)`, зерно [seed, 1]); дальше — новые блоки той же длины (зерно [seed, seed_ext])."""
    a = F.block_idx(n_src, length, boot, np.random.default_rng([seed, 1]))
    b = _boot_long(n_src, n_total - n_src, np.random.default_rng([seed, seed_ext]), boot, length) if n_total > n_src else np.zeros((boot, 0), int)
    return np.concatenate([a, b], axis=1)


def kill_tables_ext(sel, n_max=KILL_N365):
    idx = extend_paths(len(sel), n_max, F.BLOCK, F.NBOOT, F.SEED, SEED722)
    paths = np.asarray(sel, float)[idx]
    dd, gap = F.running_dd_gap(paths)
    out = {}
    for q in (95, 99):
        dq, gq = np.percentile(dd, q, axis=0), np.percentile(gap, q, axis=0)
        fire = (dd > dq[None, :] + F.EPS) | (gap > gq[None, :] + F.EPS)
        out["q%d" % q] = dict(dd=[round(float(x), 6) for x in dq], gap=[float(x) for x in gq], false_stop_60=float(fire[:, :60].any(axis=1).mean()), false_stop_365=float(fire.any(axis=1).mean()))
    cs = np.cumsum(paths, axis=1)
    out["cone"] = {k: [round(float(x), 6) for x in np.percentile(cs, v, axis=0)] for k, v in (("q05", 5), ("q50", 50), ("q95", 95))}
    return out


def drop_top(x, k):
    """Σ ряда без k лучших значений."""
    x = np.sort(np.asarray(x, float))
    return float(x[:len(x) - k].sum()) if k else float(x.sum())


def adverse_bp(side, p_b, p_ref):
    """Вредное отклонение ноги шорта, бп: вход (продажа) — (P_B − P_ref) / P_B; выход (покупка) — (P_ref − P_B) / P_B; > 0 — на Bybit хуже."""
    s = 1.0 if side == "entry" else -1.0
    return s * (p_b - p_ref) / p_b * 1e4


# ───────────────────────────── данные ─────────────────────────────

def all_trades():
    """Копии 9 членов Feb–Nov (как TK-721) + форвард 25–26.11 (сутки 328, 329): cid, sym, ent, ext (мс эпохи), day, gross, fees, takes, px (цена входа)."""
    base = S.load_all(S.MEMBERS, S.BASE_RUNS)
    parts = []
    for grp, (root, run) in S.BASE_RUNS.items():
        t0 = json.load(open(os.path.join(root, run, "t0.json"), encoding="utf-8"))
        for win, (lo, hi) in S.CREDIT[grp].items():
            for cid in S.MEMBERS:
                parts.append(read_full(os.path.join(root, run, win, "%d-trades.csv" % cid), t0[win], cid, lo, hi))
    for cid in S.MEMBERS:
        parts.append(read_full(os.path.join(FWD_DIR, "k%d" % cid, "short-trades.csv"), FWD_T0, cid, 328, 10 ** 6))
    t = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    nb = len(base["ent"])
    assert np.array_equal(t["ent"][:nb], base["ent"]) and np.allclose(t["gross"][:nb], base["gross"]), "сделки ≠ TK-721"
    return t


def read_full(path, t0, cid, lo, hi):
    with open(path, encoding="utf-8", newline="") as f:
        rd = list(csv.DictReader(f))
    ent = np.array([int(r["entry_ms"]) for r in rd], np.int64) + t0
    day = (ent - S.D0_MS) // S.DAY_MS
    keep = (day >= lo) & (day <= hi)
    col = lambda k, tp: np.array([tp(r[k]) for r in rd], dtype=object if tp is str else tp)[keep]  # noqa: E731
    return dict(cid=np.full(int(keep.sum()), cid, np.int64), sym=col("sym", str), ent=ent[keep], ext=(np.array([int(r["exit_ms"]) for r in rd], np.int64) + t0)[keep], day=day[keep],
                gross=col("gross", float), fees=col("fees", float), takes=col("takes", int), px=col("entry_px_e8", float) / 1e8)


def b_daily(t, nd=ND):
    v = t["gross"] - t["fees"]
    return np.bincount(t["day"], weights=v, minlength=nd)[:nd] / S.NMEMB, np.bincount(t["day"], minlength=nd)[:nd]


def wstat(p, n, w):
    return S.stats(p[w[0]:w[1]], n[w[0]:w[1]])


# ───────────────────────────── prep (шаги 0 и 4) ─────────────────────────────

def cmd_prep(a):
    import tk718_tests as T
    t = all_trades()
    p, n = b_daily(t)
    st = T.Store(F.DB718)
    mem = [(nm, st.series("short", nm)) for nm in st.members("short")]
    bp, _ = T.base_series(mem)
    assert np.allclose(bp[31:328], p[31:328], atol=1e-6), "ряд B по сделкам ≠ tk718_ser"
    out = dict(windows={}, members={}, top={}, psr={}, eproc={}, kill={})
    for nm, w in (("FJ", FJ), ("SEL", SEL), ("FebNov", FEBNOV), ("fwd", (328, 330))):
        out["windows"][nm] = wstat(p, n, w)
    # S3b (slip +2 бп) — ряд TK-721
    import sqlite3
    c = sqlite3.connect("file:%s?mode=ro" % S.DB, uri=True)
    s3b = np.zeros(ND)
    for d, v in c.execute("SELECT day, pnl FROM tk721_day WHERE series='S3b'"):
        s3b[d] = v
    base721 = np.zeros(ND)
    for d, v in c.execute("SELECT day, pnl FROM tk721_day WHERE series='base'"):
        base721[d] = v
    assert np.allclose(base721[31:328], p[31:328], atol=1e-6), "ряд B ≠ tk721_day base"
    out["windows"]["FJ_S3b"] = S.stats(s3b[FJ[0]:FJ[1]], n[FJ[0]:FJ[1]])
    out["windows"]["FebNov_S3b"] = S.stats(s3b[FEBNOV[0]:FEBNOV[1]], n[FEBNOV[0]:FEBNOV[1]])
    # члены на FJ
    for nm, (mp, mn) in mem:
        out["members"][nm] = dict(fj_sum=float(mp[FJ[0]:FJ[1]].sum()), fj_n=int(mn[FJ[0]:FJ[1]].sum()), fj_sharpe=F.sharpe(mp[FJ[0]:FJ[1]]), sel_sum=float(mp[SEL[0]:SEL[1]].sum()))
    # концентрация: сутки, недели (Пн–Вс), монеты
    v = (t["gross"] - t["fees"]) / S.NMEMB
    for wn, w in (("FJ", FJ), ("FebNov", FEBNOV)):
        x = p[w[0]:w[1]]
        days = np.arange(w[0], w[1])
        wk = (days + 3) // 7                                            # 05.01.2026 — понедельник (сутки 4): неделя = (сутки + 3) // 7
        wsum = np.bincount(wk - wk.min(), weights=x)
        m = (t["day"] >= w[0]) & (t["day"] < w[1])
        syms, inv = np.unique(t["sym"][m].astype(str), return_inverse=True)
        csum = np.bincount(inv, weights=v[m])
        order = np.argsort(-csum)
        dord = np.argsort(-x)
        out["top"][wn] = dict(total=float(x.sum()), days=len(x), weeks=len(wsum), coins=len(syms),
                              no_top1_day=drop_top(x, 1), no_top2_day=drop_top(x, 2), top_days=[[(S.D0 + datetime.timedelta(days=int(days[i]))).isoformat(), float(x[i])] for i in dord[:2]],
                              sharpe_no_top2_day=F.sharpe(np.delete(x, dord[:2])),
                              no_top1_week=drop_top(wsum, 1), no_top2_week=drop_top(wsum, 2),
                              no_top1_coin=float(csum.sum() - csum[order[0]]), no_top2_coin=float(csum.sum() - csum[order[:2]].sum()), top_coins=[[str(syms[i]), float(csum[i])] for i in order[:3]],
                              coins_pos=int((csum > 0).sum()))
    # PSR / DSR / MinTRL на FJ (и справочно Feb–Nov, FJ S3b)
    for nm, x in (("FJ", p[FJ[0]:FJ[1]]), ("FebNov", p[FEBNOV[0]:FEBNOV[1]]), ("FJ_S3b", s3b[FJ[0]:FJ[1]])):
        mo = moments(x)
        r = dict(mo, sr_ann=mo["sr"] * math.sqrt(365), psr0=psr(mo), mintrl=min_trl(mo))
        for k in N_TRIALS:
            s0 = sr0_expected_max(mo, k)
            r["sr0_N%d" % k] = s0
            r["sr0_ann_N%d" % k] = s0 * math.sqrt(365)
            r["dsr_N%d" % k] = psr(mo, s0)
        out["psr"][nm] = r
    # e-процесс «работает»: H0 среднее 0, H1 среднее FJ, σ FJ; мощность — на путях блочного бутстрепа FJ (21 сут., 10 000, зерно 722)
    rng = np.random.default_rng([SEED722, 2])
    for nm, x in (("FJ", p[FJ[0]:FJ[1]]), ("FJ_S3b", s3b[FJ[0]:FJ[1]])):
        mo = moments(x)
        idx = _boot_long(len(x), KILL_N365, rng)
        h1 = x[idx]
        h0 = (x - x.mean())[idx]
        le1, le0 = log_e(h1, mo["mean"], mo["sd"]), log_e(h0, mo["mean"], mo["sd"])
        th = math.log(E_ACCEPT)
        hit1 = (le1 >= th)
        first = np.where(hit1.any(axis=1), hit1.argmax(axis=1) + 1, 10 ** 6)
        fwd = p[328:330]
        out["eproc"][nm] = dict(mu1=mo["mean"], sd=mo["sd"], drift_model=mo["mean"] ** 2 / (2 * mo["sd"] ** 2), days_model=th / (mo["mean"] ** 2 / (2 * mo["sd"] ** 2)),
                                power_h1={str(k): float((first <= k).mean()) for k in (60, 90, 120, 180, 270, 365)}, false_h0_365=float((le0 >= th).any(axis=1).mean()),
                                e_fwd_2d=float(math.exp(log_e(fwd, mo["mean"], mo["sd"])[-1])))
    # таблица остановки до 365
    sel = p[SEL[0]:SEL[1]]
    fz = json.load(open(a.freeze, encoding="utf-8"))
    kt = kill_tables_ext(sel)
    for q in ("q95", "q99"):
        assert np.allclose(kt[q]["dd"][:60], fz["kill"][q]["dd"], atol=1e-6) and kt[q]["gap"][:60] == fz["kill"][q]["gap"], "первые 60 суток ≠ заморозке (%s)" % q
    assert np.allclose(kt["cone"]["q50"][:60], fz["kill"]["cone"]["q50"], atol=1e-6)
    kill = dict(version="tk722-kill365-1", task="TK-722", freeze_sha256=F.sha256_file(a.freeze), level=99,
                source="те же пути бутстрепа ряда B на SEL, что заморозка TK-720 (блоки 21, 10 000, зерно 719; сутки 1…136 пути — ровно `kill_tables`), продлены новыми блоками той же длины из SEL (зерно [719, 722]) до 365 суток; n = 1…60 — равно заморозке",
                rule="как PS-1042 §4: остановка при n сутках форварда, если просадка $ ряда с 25.11 строго больше DD_99[n] или перехай строго больше GAP_99[n]; n = 1…365",
                q95=kt["q95"], q99=kt["q99"], cone=kt["cone"])
    if a.kill_out:
        with open(a.kill_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(kill, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        out["kill"]["file_sha256"] = F.sha256_file(a.kill_out)
    out["kill"].update({q: dict(false_stop_60=kt[q]["false_stop_60"], false_stop_365=kt[q]["false_stop_365"], dd={str(k): kt[q]["dd"][k - 1] for k in (20, 60, 90, 120, 180, 270, 365)},
                                gap={str(k): kt[q]["gap"][k - 1] for k in (20, 60, 90, 120, 180, 270, 365)}) for q in ("q95", "q99")})
    out["kill"]["cone"] = {k: {str(m): kt["cone"][k][m - 1] for m in (60, 120, 180, 365)} for k in kt["cone"]}
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:20000])
    return 0


def _boot_long(n_src, n_total, rng, boot=F.NBOOT, length=F.BLOCK):
    """Пути длины n_total из скользящих блоков `length` ряда длины n_src (как `block_idx`, но путь длиннее ряда)."""
    nb = int(math.ceil(n_total / length))
    starts = rng.integers(0, n_src - length + 1, (boot, nb))
    return (starts[:, :, None] + np.arange(length)[None, None, :]).reshape(boot, nb * length)[:, :n_total]


# ───────────────────────────── venue (шаг 1) ─────────────────────────────

BYBIT_API = "https://api.bybit.com"
BYBIT_PUB = "https://public.bybit.com/trading"
BINANCE_TICKER = "https://fapi.binance.com/fapi/v1/ticker/price"


def get_json(url):
    import urllib.request
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "alpha-tk722"}), timeout=60))


def bybit_instruments():
    """Линейные USDT-перпетуалы Bybit: торгуемые и закрытые (status Closed, deliveryTime — делистинг)."""
    out = []
    for st in ("", "&status=Closed"):
        cur = ""
        while True:
            r = get_json("%s/v5/market/instruments-info?category=linear&limit=1000%s%s" % (BYBIT_API, st, "&cursor=" + cur if cur else ""))["result"]
            out += r["list"]
            cur = r.get("nextPageCursor") or ""
            if not cur:
                break
    seen, res = set(), []
    for x in out:
        if x.get("contractType") == "LinearPerpetual" and x.get("quoteCoin") == "USDT" and (x["symbol"], x["launchTime"]) not in seen:
            seen.add((x["symbol"], x["launchTime"]))
            res.append(x)
    return res


def core(name):
    """Базовый актив без множителя: 1000BONK → BONK, SHIB1000 → SHIB, 1MBABYDOGE → BABYDOGE, 1000000MOG → MOG."""
    import re
    n = re.sub(r"^(1M|\d{3,})", "", name)
    return re.sub(r"\d{3,}$", "", n) or name


def pow10_scale(ratio, tol=0.02):
    """Отношение цен Bybit / Binance → степень 10 (в пределах tol) или None."""
    if not (ratio > 0):
        return None
    k = round(math.log10(ratio))
    return 10.0 ** k if abs(ratio / 10.0 ** k - 1.0) <= tol else None


def map_bybit(bn_syms, inst, bn_px, bb_px):
    """Binance символ → (Bybit символ, масштаб цены Bybit / Binance, launchTime мс, deliveryTime мс или 0, способ) или None. Тот же символ (любой статус; при нескольких — торгуемый) — масштаб 1;
    иначе торгуемый контракт с тем же базовым активом без множителя или с именем-продолжением (PUMP → PUMPFUN), масштаб — по отношению текущих цен (степень 10 в пределах 2 %)."""
    by_sym = {}
    for x in inst:
        if x["symbol"] not in by_sym or x["status"] == "Trading":
            by_sym[x["symbol"]] = x
    trading = [x for x in inst if x["status"] == "Trading"]
    out = {}
    for s in bn_syms:
        if s in by_sym:
            x = by_sym[s]
            sc = 1.0
            if x["status"] == "Trading" and s in bn_px and s in bb_px:                # PS-1050: тот же символ — тоже по цене (ONUSDT — другой контракт)
                sc = pow10_scale(bb_px[s] / bn_px[s], tol=0.05)
            out[s] = (s, sc, int(x["launchTime"]), int(x.get("deliveryTime") or 0) if x["status"] != "Trading" else 0, "symbol") if sc is not None else None
            continue
        base = core(s[:-4] if s.endswith("USDT") else s)
        got = None
        for x in sorted(trading, key=lambda y: (core(y["baseCoin"]) != base, y["symbol"])):
            cb = core(x["baseCoin"])
            if not (cb == base or (len(base) >= 3 and (cb.startswith(base) or base.startswith(cb)) and len(cb) >= 3)):
                continue
            sc = pow10_scale(bb_px[x["symbol"]] / bn_px[s]) if s in bn_px and x["symbol"] in bb_px else None
            if sc is not None:
                got = (x["symbol"], sc, int(x["launchTime"]), 0, "core+price")
                break
        out[s] = got
    return out


def bybit_day(sym, date, cache):
    """Сделки Bybit суток UTC: (ts мс, цена, размер, сторона +1 Buy / −1 Sell, rpi), по времени; None, если файла нет."""
    import urllib.error
    import urllib.request
    os.makedirs(os.path.join(cache, sym), exist_ok=True)
    path = os.path.join(cache, sym, "%s%s.csv.gz" % (sym, date))
    if not os.path.exists(path):
        try:
            data = urllib.request.urlopen(urllib.request.Request("%s/%s/%s%s.csv.gz" % (BYBIT_PUB, sym, sym, date), headers={"User-Agent": "alpha-tk722"}), timeout=120).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    ts, px, sz, sd, rpi = [], [], [], [], []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        hdr = next(rd)
        i_ts, i_sd, i_sz, i_px = hdr.index("timestamp"), hdr.index("side"), hdr.index("size"), hdr.index("price")
        i_rpi = hdr.index("RPI") if "RPI" in hdr else None
        for r in rd:
            ts.append(float(r[i_ts]) * 1000.0)
            px.append(float(r[i_px]))
            sz.append(float(r[i_sz]))
            sd.append(1 if r[i_sd] == "Buy" else -1)
            rpi.append(int(r[i_rpi]) if i_rpi is not None and r[i_rpi] else 0)
    o = np.argsort(np.asarray(ts), kind="stable")
    return tuple(np.asarray(v)[o] for v in (ts, px, sz, sd, rpi))


def vwap(day, t_ms, lo_ms, hi_ms, side=0, no_rpi=False):
    """VWAP сделок Bybit в [t + lo, t + hi] мс (side ≠ 0 — только эта сторона тейкера; no_rpi — без RPI) и число сделок."""
    ts, px, sz, sd, rpi = day
    a, b = np.searchsorted(ts, t_ms + lo_ms, "left"), np.searchsorted(ts, t_ms + hi_ms, "right")
    m = np.ones(b - a, bool)
    if side:
        m &= sd[a:b] == side
    if no_rpi:
        m &= rpi[a:b] == 0
    if not m.any():
        return float("nan"), 0
    w = sz[a:b][m]
    return float((px[a:b][m] * w).sum() / w.sum()), int(m.sum())


def wstats_bp(x, w):
    """Среднее, p10 / p90 (в весе копий) и доля > 0 по конечным значениям."""
    x, w = np.asarray(x, float), np.asarray(w, float)
    ok = np.isfinite(x)
    x, w = x[ok], w[ok]
    if len(x) == 0:
        return dict(n=0)
    o = np.argsort(x)
    cw = np.cumsum(w[o]) / w.sum()
    return dict(n=int(len(x)), copies=float(w.sum()), mean=float((x * w).sum() / w.sum()), p90=float(x[o][min(len(x) - 1, np.searchsorted(cw, 0.90))]),
                p10=float(x[o][min(len(x) - 1, np.searchsorted(cw, 0.10))]), share_pos=float(w[x > 0].sum() / w.sum()))


def utc_date(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date().isoformat()


def cmd_venue(a):
    t = all_trades()
    v = (t["gross"] - t["fees"]) / S.NMEMB
    sym = t["sym"].astype(str)
    syms = sorted(set(sym))
    inst = bybit_instruments()
    bn_px = {x["symbol"]: float(x["price"]) for x in get_json(BINANCE_TICKER)}
    bb_px = {x["symbol"]: float(x["lastPrice"]) for x in get_json(BYBIT_API + "/v5/market/tickers?category=linear")["result"]["list"] if x.get("lastPrice")}
    mp = map_bybit(syms, inst, bn_px, bb_px)
    out = dict(bybit_instruments=len(inst), bybit_trading=sum(1 for x in inst if x["status"] == "Trading"), coins=len(syms),
               mapped={k: x for k, x in mp.items() if x and x[4] != "symbol"}, unmapped=sorted(k for k, x in mp.items() if not x),
               closed_used={k: x for k, x in mp.items() if x and x[3]})
    has = availability(mp, sym, t["ent"])
    # ноги копий N1 + форвард на монетах с Bybit: вход (цена движка и площадка с площадкой), выход (площадка с площадкой)
    m = (t["day"] >= 317) & (t["day"] < 330) & has
    legs = []
    for i in np.nonzero(m)[0]:
        bsym, sc = mp[sym[i]][0], mp[sym[i]][1]
        legs.append(("entry", sym[i], bsym, sc, int(t["ent"][i]), float(t["px"][i])))
        legs.append(("exit", sym[i], bsym, sc, int(t["ext"][i]), float("nan")))
    need_bb, need_bn = {}, {}
    for lg in legs:
        for tt in (lg[4] - 5000, lg[4] + 5000):
            need_bb[(lg[2], utc_date(tt))] = None
            need_bn[(lg[1], utc_date(tt))] = None
    for k in sorted(need_bb):
        need_bb[k] = bybit_day(k[0], k[1], a.bybit_dir)
    for k in sorted(need_bn):
        need_bn[k] = binance_day(k[0], k[1])
    out["files"] = dict(bybit=len(need_bb), bybit_missing=sorted("%s %s" % k for k, x in need_bb.items() if x is None), binance=len(need_bn), binance_missing=sorted("%s %s" % k for k, x in need_bn.items() if x is None))

    def joined(store, key_sym, tms):
        ds = [store[k] for k in sorted({(key_sym, utc_date(tms - 5000)), (key_sym, utc_date(tms + 5000))}) if store[k] is not None]
        return None if not ds else (ds[0] if len(ds) == 1 else tuple(np.concatenate([d[c] for d in ds]) for c in range(5)))

    rows = []
    for kind, s, bsym, sc, tms, pe in legs:
        bb, bn = joined(need_bb, bsym, tms), joined(need_bn, s, tms)
        side = -1 if kind == "entry" else 1
        nan2 = (float("nan"), 0)
        bb1, nbb = vwap(bb, tms, -1000, 1000) if bb is not None else nan2
        bbs, _ = vwap(bb, tms, 0, 1000, side=side) if bb is not None else nan2
        bbr, _ = vwap(bb, tms, -1000, 1000, no_rpi=True) if bb is not None else nan2
        bn1, nbn = vwap(bn, tms, -1000, 1000) if bn is not None else nan2
        bns, _ = vwap(bn, tms, 0, 1000, side=side) if bn is not None else nan2
        bnf = first_after(bn, tms) if bn is not None else float("nan")
        rows.append((kind, s, tms, pe, bn1, bns, bnf, bb1 / sc, bbs / sc, bbr / sc, nbb, nbn))
    bad = sorted({r[1] for r in rows if np.isfinite(r[4]) and np.isfinite(r[7]) and abs(math.log(r[7] / r[4])) > 0.2})   # PS-1050: другой контракт
    out["bad_pairs"] = {c: mp[c][0] for c in bad}
    for c in bad:
        mp[c] = None
    rows = [r for r in rows if r[1] not in bad]
    has = availability(mp, sym, t["ent"])
    late = np.array([bool(mp[s]) and mp[s][2] > e for s, e in zip(sym, t["ent"])])
    gone = np.array([bool(mp[s]) and bool(mp[s][3]) and e >= mp[s][3] for s, e in zip(sym, t["ent"])])
    out["share"] = {}
    for wn, (lo, hi) in (("FebNov_fwd", (31, 330)), ("FJ", FJ), ("SEL", SEL), ("N1_fwd", (317, 330))):
        m = (t["day"] >= lo) & (t["day"] < hi)
        tot = float(v[m].sum())
        out["share"][wn] = dict(sum=tot, sum_no_bybit=float(v[m & ~has].sum()), share_sum=float(v[m & ~has].sum() / tot) if tot else float("nan"),
                                copies=int(m.sum()), copies_no_bybit=int((m & ~has).sum()), share_copies=float((m & ~has).sum() / m.sum()) if m.any() else float("nan"),
                                copies_before_listing=int((m & late).sum()), copies_after_delisting=int((m & gone).sum()), coins_no_bybit=len(set(sym[m & ~has])), coins=len(set(sym[m])),
                                sum_bybit_only=float(v[m & has].sum()), sharpe_bybit_only=F.sharpe(np.bincount(t["day"][m & has] - lo, weights=v[m & has], minlength=hi - lo)))
    out["no_bybit_top"] = sorted([[c, float(v[(sym == c)].sum())] for c in set(sym[~has])], key=lambda z: -z[1])[:10]
    kinds = np.array([r[0] for r in rows])
    coin = np.array([r[1] for r in rows])
    ent = kinds == "entry"
    A = lambda pb, pr: np.array([adverse_bp(r[0], r[pb], r[pr]) for r in rows])  # noqa: E731
    eng_bb = A(3, 7)            # вход: цена движка против VWAP Bybit ±1 с
    bn_bb = A(4, 7)             # площадка с площадкой: VWAP Binance ±1 с против VWAP Bybit ±1 с
    side_bb = A(5, 8)           # стороной тейкера [t, t + 1 с] на обеих площадках
    rpi_bb = A(4, 9)
    rule_x = np.where(ent, eng_bb, bn_bb)
    rng = np.random.default_rng([SEED722, 4])
    res = {}
    for nm, x in (("rule_entry_engine_exit_venue", rule_x), ("venue_vwap1s", bn_bb), ("venue_side_0_1s", side_bb), ("venue_vwap1s_no_rpi", rpi_bb), ("entry_engine_vs_bybit", np.where(ent, eng_bb, np.nan))):
        r = dict(all=wstats_bp(x, np.ones(len(x))), entry=wstats_bp(x[ent], np.ones(int(ent.sum()))), exit=wstats_bp(x[~ent], np.ones(int((~ent).sum()))))
        r["all"]["ci95_coin_boot"] = coin_boot_ci(x, coin, rng)
        r["round_trip_per_leg"] = (r["entry"].get("mean", float("nan")) + r["exit"].get("mean", float("nan"))) / 2.0
        res[nm] = r
    # проверка цены движка: вход против первой сделки Binance после t (с проскальзыванием 1 бп)
    chk = np.array([(r[3] - r[6] * (1 - 1e-4)) / r[3] * 1e4 for r in rows if r[0] == "entry" and np.isfinite(r[6])])
    out["engine_px_check_bp"] = dict(n=int(len(chk)), share_within_0_5bp=float((np.abs(chk) <= 0.5).mean()) if len(chk) else float("nan"), mean=float(chk.mean()) if len(chk) else float("nan"))
    out["legs"] = dict(n=len(rows), entry=int(ent.sum()), exit=int((~ent).sum()), no_bybit_trade_1s=int(sum(1 for r in rows if r[10] == 0)), no_binance_trade_1s=int(sum(1 for r in rows if r[11] == 0)), coins=len(set(coin)))
    out["adverse_bp"] = res
    out["by_coin"] = sorted([[c, int((coin == c).sum()), float(np.nanmean(rule_x[coin == c])) if np.isfinite(rule_x[coin == c]).any() else None] for c in set(coin)], key=lambda z: -z[1])
    mean = res["rule_entry_engine_exit_venue"]["all"].get("mean", float("nan"))
    sh = out["share"]["FebNov_fwd"]["share_sum"]
    out["rule"] = dict(mean_bp=mean, share_no_bybit=sh, verdict="B снимается" if (mean >= 5.6 or sh > 0.30) else ("ожидание S3b" if mean >= 2.0 else "без изменений"))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(a.out + ".legs.csv", "w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["kind", "sym", "t_ms", "p_engine", "bn_vwap1s", "bn_side_0_1s", "bn_first_after", "bb_vwap1s", "bb_side_0_1s", "bb_vwap1s_no_rpi", "n_bb_1s", "n_bn_1s"])
        wr.writerows(rows)
    print(json.dumps(dict(rule=out["rule"], legs=out["legs"], share=out["share"], adverse={k: dict(all=x["all"], entry=x["entry"].get("mean"), exit=x["exit"].get("mean"), rt=x["round_trip_per_leg"]) for k, x in res.items()},
                          check=out["engine_px_check_bp"], unmapped=len(out["unmapped"]), mapped=out["mapped"], files=out["files"]), ensure_ascii=False, indent=1))
    return 0


def availability(mp, sym, ent):
    """Копия торгуема на Bybit: пара есть, launchTime ≤ вход < deliveryTime (0 — не закрыт)."""
    return np.array([bool(mp[s]) and mp[s][2] <= e and (not mp[s][3] or e < mp[s][3]) for s, e in zip(sym, ent)])


def binance_day(sym, date, root="/data/p42-tk616/fwd/dl/aggtrades"):
    """aggTrades Binance суток: (ts мс, цена, количество, сторона тейкера +1 / −1, 0) по времени; None, если zip нет."""
    import io
    import zipfile
    path = os.path.join(root, sym, "%s-aggTrades-%s.zip" % (sym, date))
    if not os.path.exists(path):
        return None
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    rd = csv.reader(io.StringIO(raw.decode("utf-8")))
    ts, px, sz, sd = [], [], [], []
    for r in rd:
        if not r or not r[0][:1].isdigit():
            continue
        px.append(float(r[1]))
        sz.append(float(r[2]))
        ts.append(float(r[5]))
        sd.append(-1 if r[6].strip().lower() == "true" else 1)
    o = np.argsort(np.asarray(ts), kind="stable")
    return tuple(np.asarray(x)[o] for x in (ts, px, sz, sd, np.zeros(len(ts), int)))


def first_after(day, t_ms):
    ts, px = day[0], day[1]
    i = np.searchsorted(ts, t_ms, "left")
    return float(px[i]) if i < len(ts) else float("nan")


def coin_boot_ci(x, coin, rng, boot=10000):
    """95 % интервал среднего бутстрепом по монетам (кластеры)."""
    ok = np.isfinite(x)
    x, coin = x[ok], coin[ok]
    cs = sorted(set(coin))
    if not cs:
        return [float("nan"), float("nan")]
    s = np.array([x[coin == c].sum() for c in cs])
    k = np.array([(coin == c).sum() for c in cs])
    idx = rng.integers(0, len(cs), (boot, len(cs)))
    m = s[idx].sum(axis=1) / k[idx].sum(axis=1)
    return [float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))]


# ───────────────────────────── beta (шаг 3а) ─────────────────────────────

def day_oc(path):
    """(open первой минуты, close последней минуты) файла минуток суток или None."""
    try:
        with open(path, "rb") as f:
            f.readline()
            first = f.readline()
            f.seek(0, 2)
            end = f.tell()
            f.seek(max(0, end - 4096))
            last = f.read().rstrip(b"\n").split(b"\n")[-1]
    except OSError:
        return None
    if not first or not last or last.startswith(b"open_time"):
        return None
    return float(first.split(b",")[1]), float(last.split(b",")[4])


def market_series(syms, lo, hi):
    """Равновзвешенная доходность суток c539 (%): среднее по монетам с минутками суток (close последней / open первой минуты UTC − 1); и число монет."""
    r, k = np.full(ND, np.nan), np.zeros(ND, np.int64)
    for d in range(lo, hi):
        date = (S.D0 + datetime.timedelta(days=d)).isoformat()
        vals = []
        for s in syms:
            for root in KLINE_ROOTS:
                oc = day_oc(os.path.join(root, "klines", s, date + ".csv"))
                if oc is not None:
                    break
            if oc is not None and oc[0] > 0:
                vals.append(oc[1] / oc[0] - 1.0)
        if vals:
            r[d], k[d] = 100.0 * float(np.mean(vals)), len(vals)
    return r, k


def ols(x, y):
    xm, ym = x.mean(), y.mean()
    b = float(((x - xm) * (y - ym)).sum() / ((x - xm) ** 2).sum())
    a = float(ym - b * xm)
    res = y - a - b * x
    return a, b, float(1.0 - (res ** 2).sum() / ((y - ym) ** 2).sum())


def cmd_beta(a):
    t = all_trades()
    p, n = b_daily(t)
    syms = open(S.C539, encoding="utf-8").read().replace(",", " ").split()
    mkt, k = market_series(syms, 31, 330)
    out = dict(coins_per_day=dict(min=int(k[31:330].min()), max=int(k[31:330].max()), mean=float(k[31:330].mean())), win={})
    rng = np.random.default_rng([SEED722, 3])
    for wn, (lo, hi) in (("FebNov", FEBNOV), ("FJ", FJ), ("SEL", SEL)):
        x, y = mkt[lo:hi], p[lo:hi]
        ok = np.isfinite(x)
        x, y = x[ok], y[ok]
        al, be, r2 = ols(x, y)
        idx = F.block_idx(len(x), F.BLOCK, F.NBOOT, rng)
        bs = np.array([ols(x[i], y[i])[:2] for i in idx])
        res = y - al - be * x
        out["win"][wn] = dict(days=int(len(x)), alpha_per_day=al, alpha_sum=al * len(x), beta_usd_per_pct=be, r2=r2, corr=float(np.corrcoef(x, y)[0, 1]),
                              alpha_ci=[float(np.percentile(bs[:, 0], 2.5)), float(np.percentile(bs[:, 0], 97.5))], beta_ci=[float(np.percentile(bs[:, 1], 2.5)), float(np.percentile(bs[:, 1], 97.5))],
                              mkt_mean_pct=float(x.mean()), mkt_sum_pct=float(x.sum()), beta_part_sum=be * float(x.sum()), b_sum=float(y.sum()),
                              resid_sharpe=F.sharpe(res + al), down_days=dict(n=int((x < 0).sum()), b_sum=float(y[x < 0].sum())), up_days=dict(n=int((x >= 0).sum()), b_sum=float(y[x >= 0].sum())))
    rule = out["win"]["FebNov"]["alpha_ci"][0] > 0
    out["rule"] = dict(alpha_lo=out["win"]["FebNov"]["alpha_ci"][0], verdict="край B не сводится к бете рынка" if rule else "не отделимо от беты — решает 3б")
    np.save(a.out + ".mkt.npy", mkt)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


# ───────────────────────────── null (шаг 3б, PS-1049) ─────────────────────────────

def day_closes(sym, d):
    """close минуток суток d (1440, NaN — нет бара) из первого корня, где есть файл."""
    date = (S.D0 + datetime.timedelta(days=int(d))).isoformat()
    out = np.full(1440, np.nan)
    d0 = S.D0_MS + int(d) * S.DAY_MS
    for root in KLINE_ROOTS:
        path = os.path.join(root, "klines", sym, date + ".csv")
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            f.readline()
            for ln in f:
                p = ln.split(b",", 5)
                i = (int(p[0]) - d0) // 60000
                if 0 <= i < 1440:
                    out[i] = float(p[4])
        return out
    return out


def null_run(t, lo, hi, r_draws=100, seed=(SEED722, 5)):
    """B свечами и нуль со сдвигом минуты входа (PS-1049): (b_c[ND], null[R, ND], n[ND], пропущено копий)."""
    m = (t["day"] >= lo) & (t["day"] < hi)
    idx = np.nonzero(m)[0]
    sym = t["sym"].astype(str)
    rng = np.random.default_rng(list(seed))
    bc = np.zeros(ND)
    nul = np.zeros((r_draws, ND))
    cnt = np.zeros(ND, np.int64)
    skipped = 0
    groups = {}
    for i in idx:
        groups.setdefault((sym[i], int(t["day"][i])), []).append(i)
    for (s, d), ii in sorted(groups.items()):
        c = np.concatenate([day_closes(s, d), day_closes(s, d + 1)])
        d0 = S.D0_MS + d * S.DAY_MS
        for i in ii:
            e = int((t["ent"][i] - d0) // 60000)
            x = int((t["ext"][i] - d0) // 60000)
            dur = x - e
            if not (0 <= e < 1440 and 0 <= x < 2880) or not (np.isfinite(c[e]) and np.isfinite(c[x])):
                skipped += 1
                continue
            fee = float(t["fees"][i])
            bc[d] += (500.0 * (1.0 - c[x] / c[e]) - fee) / S.NMEMB
            cnt[d] += 1
            st = np.arange(1440)
            ok = st + dur < 2880
            st = st[ok]
            ok = np.isfinite(c[st]) & np.isfinite(c[st + dur])
            st = st[ok]
            if len(st) == 0:
                skipped += 1
                continue
            pick = st[rng.integers(0, len(st), r_draws)]
            nul[:, d] += (500.0 * (1.0 - c[pick + dur] / c[pick]) - fee) / S.NMEMB
    return bc, nul, cnt, skipped


def cmd_null(a):
    import time
    t = all_trades()
    p, n = b_daily(t)
    t0 = time.time()
    lo, hi = (SEL if a.win == "SEL" else FEBNOV)
    bc, nul, cnt, skipped = null_run(t, lo, hi)
    el = time.time() - t0
    out = dict(win=a.win, seconds=el, skipped_copies=skipped, copies=int(cnt[lo:hi].sum()), res={})
    rng = np.random.default_rng([SEED722, 6])
    for wn, (wl, wh) in (("FebNov", FEBNOV), ("FJ", FJ), ("SEL", SEL)):
        if wl < lo or wh > hi:
            continue
        b, bcw, nm = p[wl:wh], bc[wl:wh], nul[:, wl:wh].mean(axis=0)
        dlt = bcw - nm
        idx = F.block_idx(len(dlt), F.BLOCK, F.NBOOT, rng)
        bs = dlt[idx].sum(axis=1)
        out["res"][wn] = dict(days=wh - wl, b_sum=float(b.sum()), bc_sum=float(bcw.sum()), corr_b_bc=float(np.corrcoef(b, bcw)[0, 1]), null_sum=float(nm.sum()),
                              null_draw_sums=dict(mean=float(nul[:, wl:wh].sum(axis=1).mean()), p95=float(np.percentile(nul[:, wl:wh].sum(axis=1), 95)), max=float(nul[:, wl:wh].sum(axis=1).max())),
                              share_null_ge_bc=float((nul[:, wl:wh].sum(axis=1) >= bcw.sum()).mean()), delta_sum=float(dlt.sum()), delta_ci=[float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))],
                              delta_per_day=float(dlt.mean()), bc_stats=S.stats(bcw, cnt[wl:wh]), null_stats=S.stats(nm, cnt[wl:wh]))
    if "FebNov" in out["res"]:
        r = out["res"]["FebNov"]
        fit = r["corr_b_bc"] >= 0.8
        out["rule"] = dict(fit=fit, delta_lo=r["delta_ci"][0], verdict=("уровень несёт край" if r["delta_ci"][0] > 0 else "B = шорт-бета") if fit else "не годен (свечи не воспроизводят B)")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("prep")
    p.add_argument("--out", required=True)
    p.add_argument("--kill-out")
    p.add_argument("--freeze", default=os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk720-frozen.json"))
    p.set_defaults(fn=cmd_prep)
    p = sp.add_parser("venue")
    p.add_argument("--out", required=True)
    p.add_argument("--bybit-dir", required=True)
    p.set_defaults(fn=cmd_venue)
    p = sp.add_parser("beta")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_beta)
    p = sp.add_parser("null")
    p.add_argument("--out", required=True)
    p.add_argument("--win", choices=("SEL", "FebNov"), default="SEL")
    p.set_defaults(fn=cmd_null)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
