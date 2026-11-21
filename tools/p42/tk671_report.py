#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), реплика портфеля + таблицы + шум + вердикт по правилам PS-948 (`prereg-13.71.md` §6–§9). Параметры и пороги протокола не меняются.

  python3 tk671_report.py --data-dir /data/p42-tk671/data --fit-dir /data/p42-tk671/fit --out <каталог> [--threads 2] [--nboot 20000] [--nnull 2000]

Что считается (сторона ∈ {long, short}, θ ∈ {0; 0,3; 0,6} $):
  1. Выбор строк с `s_hat ≥ θ` (только строки с меткой: `y_has`), реплика занятости `tk671_pf` (BASE_CFG, `reentry=0`: один уровень — одна сделка; ключ уровня = `meta_level_px_e8`, порядок `(sig_ms, dist, sym, level)`),
     старт 01.08 00:00 UTC с ПУСТЫМ состоянием (строки с `meta_abs_ms` ≥ 01.08; позиции, открытые раньше, не учитываются ни в модели, ни в базах). Сутки = UTC-сутки ЗАКРЫТИЯ сделки, ряд на все 105 суток (01.08–13.11), нули в дни без сделок.
     Базы — той же репликой на сделках двойников (`gate-pf/<окно>/out/{L_ind,S_ind,D_ind}`; строки = сделки двойника с сигналом в зачётном диапазоне окна и ≥ 01.08; порядок ничьих — как в файле, как в воротах G-pf):
     L_base ← L_ind, L_n24 ← L_ind при n24 ≥ 220000 (join по `(win, sym, sig_ms)` с строками данных), S_base ← S_ind, D_S ← D_ind. Движок — справка (перекрытие ключей и ΔΣ).
  2. Таблицы (Σ $, $/сделку, n, годовой Шарп по суткам, R:R, убыток стопа, просадка, перехай, доля суток со сделками) по месяцам и за авг–ноя; R:R печатается первым.
  3. Вывод: парная суточная Δ = модель − база; блочный бутстреп по суткам (блок 5, 20 000, seed 671) суммы Δ; p = доля бутстреп-сумм ≤ 0; Холм по 6 основным ячейкам (сторона × θ); MDE (80 % мощности) = (z_{1−α_шаг} + z_{0,80})·SD_boot(ΔΣ);
     ΔΣ без 2 лучших суток и без 2 лучших монет (положительные суточные / по-монетные Δ; соглашение tk668_post.line_tests). L_n24 и D_S — справочные ячейки того же теста, вне Холма (самый строгий шаг 0,05/6).
  4. Нулевая модель (2 000 случайных наборов той же численности по месяцам, та же реплика, Σ и процентиль модели) и плацебо (`s_plc`, окт и ноя).
  5. Счётчик «CatBoost/f12: 28 + 6 = 34» и DSR из `selection_bias.py` при n = 34.
  6. Вердикт по ячейке — механически по §9 PS-948. Выход: `tables.json` (все числа) и `report.md` (по-русски, только $, без бп и без медиан сделок).

Реплика быстрая (numba, nogil; без numba тот же код на чистом Python — медленно, но тот же результат); равенство с `tk671_pf.replay` проверяется тестами."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import sys
import time
from statistics import NormalDist

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_stats as st  # noqa: E402
import tk663_shape as sh  # noqa: E402
import tk671_pf as pf  # noqa: E402

try:
    from numba import njit
except ImportError:                                   # ПК владельца: тесты малы, без numba
    def njit(*a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda f: f


def _utc(y, m, d):
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp() * 1000)


DAY_MS = 86_400_000
MIN_MS = 60_000
START_MS = _utc(2026, 8, 1)
END_MS = _utc(2026, 11, 14)                           # первая миллисекунда 14.11 — вне периода
NDAYS = (END_MS - START_MS) // DAY_MS                 # 105
MONTH_NAMES = ("авг", "сен", "окт", "01–13.11")
MONTH_LEN = (31, 30, 31, 13)
MONTH_OF_DAY = np.repeat(np.arange(4), MONTH_LEN)
MONTH_START_MS = (_utc(2026, 8, 1), _utc(2026, 9, 1), _utc(2026, 10, 1), _utc(2026, 11, 1))
MARGIN = 250.0                                        # $ маржи: 500 $ при плече 2
THETAS = (0.0, 0.3, 0.6)
SIDES = ("long", "short")
SIDE_RU = {"long": "лонг", "short": "шорт"}
N24_MIN = 220000.0
NBOOT, BLOCK, SEED, NNULL = 20_000, 5, 671, 2_000
ALPHA, POWER = 0.05, 0.80
PRIOR_TRIALS, N_CELLS = 28, 6
TRIALS = PRIOR_TRIALS + N_CELLS                       # 34
MIN_TRADES_MONTH = 30
MONTHS_NEED_POS, MONTHS_NEED_N = 3, 3
V_ADD = "Добавляет к базе (найдено на этих данных; кандидат только вперёд)"
V_NO = "Не добавляет"
V_UNCLEAR = "не ясно"
# база → (имя двойника, сторона, шаблон файла сделок движка)
BASES = {
    "L_base": ("L_ind", "long", "/data/p42-tk642/cells/{win}/L_base/long-trades.csv"),
    "L_n24": ("L_ind", "long", "/data/p42-tk638/ladd-h775/{win}-L/N24_q60/long-trades.csv"),
    "S_base": ("S_ind", "short", "/data/p42-tk642/cells/{win}/S_base/short-trades.csv"),
    "D_S": ("D_ind", "short", "/data/p42-tk668/e4/cells/{win}/S_lx1/short-trades.csv"),
}
PRIMARY_BASE = {"long": "L_base", "short": "S_base"}
REF_BASE = {"long": "L_n24", "short": "D_S"}
DATA_COLS = ["win", "row_id", "sym", "meta_abs_ms", "meta_sig_ms", "meta_level_px_e8", "meta_dist_e8", "y_has", "y_net", "y_entry_ms", "y_exit_ms", "y_stop", "n24"]
PRED_COLS = ["win", "row_id", "month", "s_hat", "p_stop", "s_plc"]


# ───────────────────────────── реплика (numba) ─────────────────────────────

@njit(nogil=True)
def _replay_kernel(ms, sym, lid, exit_ms, stop, nsym, nlev, p, cool, m_stops, mm, s_glob, w, ww, reentry, busy_on):
    """Те же правила, что `tk671_pf.replay`; входы УЖЕ в порядке движка. `lid` — номер уровня (одна монета + ключ)."""
    n = len(ms)
    acc = np.zeros(n, dtype=np.bool_)
    busy = np.zeros(nsym, dtype=np.bool_)
    cool_until = np.zeros(nsym, dtype=np.int64)
    cpause = np.zeros(nsym, dtype=np.int64)
    streak = np.zeros(nsym, dtype=np.int64)
    consumed = np.zeros(nlev, dtype=np.bool_)
    oex = np.zeros(p + 2, dtype=np.int64)
    oidx = np.zeros(p + 2, dtype=np.int64)
    nopen = 0
    gst = np.zeros(max(s_glob, 1) + 2, dtype=np.int64)
    ngst = 0
    gpause = 0
    for i in range(n):
        t = ms[i]
        while nopen > 0 and oex[0] <= t:
            ex = oex[0]
            j = oidx[0]
            for q in range(nopen - 1):
                oex[q] = oex[q + 1]
                oidx[q] = oidx[q + 1]
            nopen -= 1
            s = sym[j]
            busy[s] = False
            cool_until[s] = ex + cool
            if stop[j]:
                if reentry == 1:
                    consumed[lid[j]] = True
                streak[s] += 1
                if streak[s] >= m_stops:
                    cpause[s] = ex + mm
                    streak[s] = 0
                gst[ngst] = ex
                ngst += 1
                while ngst > 0 and ex >= gst[0] + w:
                    for q in range(ngst - 1):
                        gst[q] = gst[q + 1]
                    ngst -= 1
                if ngst >= s_glob:
                    if ww > 0:
                        gpause = ex + ww
                    ngst = 0
            else:
                streak[s] = 0
        s = sym[i]
        if consumed[lid[i]]:
            continue
        if busy_on and busy[s]:
            continue
        if t < cool_until[s] or t < cpause[s]:
            continue
        if t < gpause:
            continue
        if nopen >= p:
            continue
        if reentry == 0:
            consumed[lid[i]] = True
        busy[s] = True
        ex = exit_ms[i]
        pos = 0
        while pos < nopen and oex[pos] <= ex:
            pos += 1
        for q in range(nopen, pos, -1):
            oex[q] = oex[q - 1]
            oidx[q] = oidx[q - 1]
        oex[pos] = ex
        oidx[pos] = i
        nopen += 1
        acc[i] = True
    return acc


def _cfg_tuple(cfg=None):
    c = dict(pf.BASE_CFG)
    c.update(cfg or {})
    mm = MIN_MS
    return (int(c["p"]), int(c["cool_min"]) * mm, int(c["m_stops"]), int(c["mm_min"]) * mm, int(c["s_glob"]), int(c["w_min"]) * mm, int(c["ww_min"]) * mm, int(c["reentry"]), bool(c["busy"]))


def level_ids(sym, key):
    """Номер уровня = номер пары (монета, ключ) в порядке сортировки; возвращает (ids, число уровней)."""
    sym, key = np.asarray(sym), np.asarray(key)
    n = len(sym)
    if n == 0:
        return np.zeros(0, dtype=np.int64), 0
    o = np.lexsort((key, sym))
    s, k = sym[o], key[o]
    new = np.ones(n, dtype=bool)
    new[1:] = (s[1:] != s[:-1]) | (k[1:] != k[:-1])
    ids = np.empty(n, dtype=np.int64)
    ids[o] = np.cumsum(new) - 1
    return ids, int(new.sum())


def replay_fast(ms, dist, sym, key, exit_ms, stop, cfg=None):
    """Замена `tk671_pf.replay` (тот же результат, маска в порядке входа); для проверки равенства — тесты."""
    ms = np.asarray(ms)
    o = pf.order(ms, dist, sym, key)
    lid, nlev = level_ids(sym, key)
    sym64 = np.asarray(sym, dtype=np.int64)
    a = _replay_kernel(np.ascontiguousarray(ms[o], dtype=np.int64), np.ascontiguousarray(sym64[o]), np.ascontiguousarray(lid[o]),
                       np.ascontiguousarray(np.asarray(exit_ms)[o], dtype=np.int64), np.ascontiguousarray(np.asarray(stop, dtype=bool)[o]),
                       int(sym64.max()) + 1 if len(ms) else 1, nlev, *_cfg_tuple(cfg))
    acc = np.zeros(len(ms), dtype=bool)
    acc[o] = a
    return acc


class Cand:
    """Кандидаты (строки с меткой или сделки двойника) в порядке движка; `x` — дополнительные массивы в том же порядке."""

    def __init__(self, ms, dist, sym, key, exit_ms, stop, net, extra=None):
        ms = np.asarray(ms, dtype=np.int64)
        o = pf.order(ms, np.asarray(dist), np.asarray(sym), np.asarray(key))
        self.ms = ms[o]
        self.sym = np.asarray(sym, dtype=np.int64)[o]
        self.key = np.asarray(key)[o]
        self.exit = np.asarray(exit_ms, dtype=np.int64)[o]
        self.stop = np.asarray(stop, dtype=bool)[o]
        self.net = np.asarray(net, dtype=float)[o]
        self.x = {k: np.asarray(v)[o] for k, v in (extra or {}).items()}
        self.lid, self.nlev = level_ids(self.sym, self.key)
        self.nsym = int(self.sym.max()) + 1 if len(self.sym) else 1
        self.day = np.floor_divide(self.exit - START_MS, DAY_MS)
        self.inrange = (self.day >= 0) & (self.day < NDAYS)
        self.net_in = np.where(self.inrange, self.net, 0.0)
        self.month = month_of_ms(self.ms)

    def __len__(self):
        return len(self.ms)

    def replay(self, pos, cfg=None):
        """Реплика над подмножеством позиций `pos` (возрастающие индексы); возвращает булев массив длины len(pos)."""
        return _replay_kernel(self.ms[pos], self.sym[pos], self.lid[pos], self.exit[pos], self.stop[pos], self.nsym, self.nlev, *_cfg_tuple(cfg))


def month_of_ms(ms):
    """Индекс месяца сравнения по UTC-миллисекундам: 0 авг … 3 ноя(1–13); −1 до 01.08; 4 с 14.11."""
    return np.searchsorted(np.asarray(MONTH_START_MS + (END_MS,)), np.asarray(ms), side="right") - 1


# ───────────────────────────── метрики ─────────────────────────────

def _nan():
    return float("nan")


def gaps_of(u):
    """Закрытые зазоры между соседними новыми максимумами (первый — от старта) по результату `tk663_shape.uw`."""
    cum, peak = u["cum"], u["peak"]
    prev = np.concatenate([[0.0], peak[:-1]])
    hi = np.nonzero(cum > prev + sh.EPS)[0]
    return np.diff(np.concatenate([[-1], hi]).astype(int))


def metrics(net, stop, daily, ntr):
    """Метрики среза: `net`/`stop` — сделки среза (закрытие внутри периода), `daily`/`ntr` — суточные Σ и число сделок среза."""
    net, stop, daily = np.asarray(net, float), np.asarray(stop, bool), np.asarray(daily, float)
    n = len(net)
    win, loss = net[net > 0], net[net < 0]
    mw = float(win.mean()) if len(win) else _nan()
    ml = float(loss.mean()) if len(loss) else _nan()
    sl = net[stop]
    u = sh.uw(daily)
    g = gaps_of(u)
    sm = float(sl.mean()) if len(sl) else _nan()
    sx = float(sl.min()) if len(sl) else _nan()
    return dict(n=n, sum=float(net.sum()), mean=float(net.sum()) / n if n else _nan(), n_win=int(len(win)), n_loss=int(len(loss)), mean_win=mw, mean_loss=ml,
                rr=mw / abs(ml) if len(win) and len(loss) else _nan(),
                stop_n=int(len(sl)), stop_mean=sm, stop_max=sx, stop_mean_pct=sm / MARGIN * 100 if len(sl) else _nan(), stop_max_pct=sx / MARGIN * 100 if len(sl) else _nan(),
                sharpe=sh.sharpe(daily), maxdd=float(u["maxdd"]), maxdd_pct=float(u["maxdd"]) / MARGIN * 100,
                days=int(len(daily)), days_with_trades=int((np.asarray(ntr) > 0).sum()), share_days_trades=float((np.asarray(ntr) > 0).mean()) if len(daily) else _nan(),
                highs=int(u["highs"]), gap_longest=int(u["gap_longest"]), gap_mean=float(u["gap_mean"]), gap_p90=float(np.percentile(g, 90)) if len(g) else _nan(), tail=int(u["tail"]),
                under_share=float(u["under_share"]), under_longest=int(u["under_longest"]), episodes=int(u["episodes"]))


def line_series(day, net, stop):
    """Суточный ряд на все 105 суток и метрики (всего + по месяцам) по сделкам с сутками закрытия `day` (0…104)."""
    day, net, stop = np.asarray(day, dtype=np.int64), np.asarray(net, float), np.asarray(stop, bool)
    daily = np.bincount(day, weights=net, minlength=NDAYS)
    ntr = np.bincount(day, minlength=NDAYS)
    out = {"total": metrics(net, stop, daily, ntr), "months": []}
    md = MONTH_OF_DAY[day]
    for m in range(4):
        dm, tm = MONTH_OF_DAY == m, md == m
        out["months"].append(metrics(net[tm], stop[tm], daily[dm], ntr[dm]))
    return out, daily


# ───────────────────────────── вывод (бутстреп, Холм, MDE, вердикт) ─────────────────────────────

def boot_index(nboot=NBOOT, block=BLOCK, seed=SEED, n=NDAYS):
    """Скользящие блоки суток (круговые), одни и те же для всех ячеек (парность)."""
    return st.block_idx(n, np.random.default_rng(seed), nboot, block)


def holm(ps, alpha=ALPHA):
    """Нисходящий Холм: ранг k (с 1) по возрастанию p (ничьи — по порядку), шаг alpha/(m−k+1), проходит, если p ≤ шага и все ранги ниже прошли."""
    m = len(ps)
    out = [None] * m
    ok = True
    for k, i in enumerate(sorted(range(m), key=lambda j: (ps[j], j)), start=1):
        step = alpha / (m - k + 1)
        ok = ok and bool(ps[i] <= step)
        out[i] = dict(rank=k, step=step, passed=ok)
    return out


def mde(sd_boot, alpha_step, power=POWER):
    nd = NormalDist()
    return (nd.inv_cdf(1 - alpha_step) + nd.inv_cdf(power)) * sd_boot


def two_best(vals):
    """Сумма двух наибольших положительных значений (удаляемых «лучших»): соглашение tk668_post.line_tests."""
    top = np.sort(np.asarray(vals, float))[::-1][:2]
    return float(top[top > 0].sum())


def delta_stats(daily_m, daily_b, coin_m, coin_b, idx):
    """Парная Δ = модель − база по суткам: ΔΣ, бутстреп-p, SD, по месяцам, без 2 лучших суток / монет."""
    d = np.asarray(daily_m, float) - np.asarray(daily_b, float)
    dsum = float(d.sum())
    bs = d[idx].sum(axis=1)
    dc = np.asarray(coin_m, float) - np.asarray(coin_b, float)
    return dict(dsum=dsum, p=float((bs <= 0).mean()), sd_boot=float(bs.std(ddof=1)), boot_mean=float(bs.mean()),
                month_dsum=[float(d[MONTH_OF_DAY == k].sum()) for k in range(4)],
                dsum_no2days=dsum - two_best(d), dsum_no2coins=float(dc.sum()) - two_best(dc),
                best_days_removed=two_best(d), best_coins_removed=two_best(dc))


def verdict(holm_pass, sum_m, sum_b, sh_m, sh_b, month_dsum, month_n, dsum, no2days, no2coins):
    """§9 PS-948, механически. «Добавляет» — все условия; «Не добавляет» — точечная ΔΣ ≤ 0; иначе «не ясно»."""
    conds = dict(holm=bool(holm_pass), sum_ge=bool(sum_m >= sum_b), sharpe_ge=bool(sh_m >= sh_b),
                 months_pos=bool(sum(1 for x in month_dsum if x > 0) >= MONTHS_NEED_POS), months_n=bool(sum(1 for x in month_n if x >= MIN_TRADES_MONTH) >= MONTHS_NEED_N),
                 no2days=bool(no2days > 0), no2coins=bool(no2coins > 0))
    if all(conds.values()):
        return V_ADD, conds
    if dsum <= 0:
        return V_NO, conds
    return V_UNCLEAR, conds


def load_sb(sb_dir=None):
    for d in (sb_dir, HERE, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts")):
        if d and os.path.exists(os.path.join(d, "selection_bias.py")):
            sys.path.insert(0, d)
            import selection_bias
            return selection_bias
    raise FileNotFoundError("selection_bias.py не найден (рядом со скриптом, в .claude/skills/alpha-research/scripts или --sb-dir)")


def dsr_cell(sb, sharpe, trials=TRIALS, t_days=NDAYS):
    """DSR как в TK-654/668: sb.significance(S, se, trials), se = sqrt(365/T·(1 + S²/730)), T = число суток периода сравнения (105)."""
    se = math.sqrt(365.0 / t_days * (1 + sharpe * sharpe / 730.0))
    r = sb.significance(sharpe, se, trials)
    return {k: (bool(v) if isinstance(v, (bool, np.bool_)) else float(v)) for k, v in r.items()}


# ───────────────────────────── загрузка ─────────────────────────────

def read_side(data_dir, side):
    path = os.path.join(data_dir, "%s.parquet" % side)
    have = set(pq.read_schema(path).names)
    cols = [c for c in DATA_COLS if c in have]
    df = pd.read_parquet(path, columns=cols)
    if "n24" not in df.columns:
        df["n24"] = np.nan
    df["sym"] = df["sym"].astype(str)
    return df


def read_pred(fit_dir, side):
    path = os.path.join(fit_dir, side, "pred.parquet")
    have = set(pq.read_schema(path).names)
    return pd.read_parquet(path, columns=[c for c in PRED_COLS if c in have])


def labeled_mask(df):
    """Строка торгуется, если есть метка: y_has, конечный y_net, y_exit_ms ≥ 0."""
    has = df["y_has"].fillna(False).astype(bool).to_numpy()
    ok = has & np.isfinite(df["y_net"].to_numpy(float)) & (df["y_exit_ms"].to_numpy(float) >= 0)
    return ok, int(has.sum() - ok.sum())


def build_model_cand(data, pred, sym_id):
    """Кандидаты модели: строки валидации с меткой и сигналом ≥ 01.08 (пустое состояние на старте); печатаемые счётчики."""
    info = {}
    if "s_plc" not in pred.columns:
        pred = pred.assign(s_plc=np.nan)
    df = pred.merge(data, on=["win", "row_id"], how="inner", validate="one_to_one")
    info["pred_rows"] = int(len(pred))
    info["pred_without_data"] = int(len(pred) - len(df))
    ab = df["meta_abs_ms"].to_numpy(np.int64)
    inr = (ab >= START_MS) & (ab < END_MS)
    info["rows_outside_period"] = int((~inr).sum())
    df = df[inr]
    lab, bad = labeled_mask(df)
    info["labeled_inconsistent"] = bad
    info["rows_in_period"] = int(len(df))
    info["labeled_rows"] = int(lab.sum())
    info["unlabeled_rows"] = int(len(df) - lab.sum())
    d = df[lab]
    c = Cand(d["meta_abs_ms"].to_numpy(np.int64), d["meta_dist_e8"].to_numpy(np.int64), d["sym"].map(sym_id).to_numpy(np.int64), d["meta_level_px_e8"].to_numpy(np.int64),
             d["y_exit_ms"].to_numpy(np.int64), d["y_stop"].to_numpy(float) == 1.0, d["y_net"].to_numpy(float),
             extra=dict(s_hat=d["s_hat"].to_numpy(float), s_plc=d["s_plc"].to_numpy(float), row=np.arange(len(d))))
    return c, info


def load_twin(gate_dir, tw, side):
    """Сделки двойника по трём окнам: сигнал внутри зачётного диапазона окна и ≥ 01.08 (абсолютное UTC-время)."""
    parts = []
    for w in st.TRADE_WINDOWS:
        p = os.path.join(gate_dir, w.name, "out", tw, "%s-trades.csv" % side)
        df = pd.read_csv(p, usecols=["sym", "level", "sig_ms", "exit_ms", "net", "kind"], dtype={"sym": str})
        df = df.rename(columns={"sig_ms": "sig_rel", "exit_ms": "exit_rel"})
        df["sig_abs"] = df["sig_rel"].to_numpy(np.int64) + w.t0_ms
        df["exit_abs"] = df["exit_rel"].to_numpy(np.int64) + w.t0_ms
        lo = max(_utc(w.lod.year, w.lod.month, w.lod.day), START_MS)
        hi = _utc(w.hid.year, w.hid.month, w.hid.day) + DAY_MS
        df = df[(df["sig_abs"] >= lo) & (df["sig_abs"] < hi)].copy()
        df["win"] = w.name
        parts.append(df)
    return pd.concat(parts, ignore_index=True)


def load_engine(tpl, side):
    parts = []
    for w in st.TRADE_WINDOWS:
        df = pd.read_csv(tpl.format(win=w.name), usecols=["sym", "level", "sig_ms", "net"], dtype={"sym": str})
        sig = df["sig_ms"].to_numpy(np.int64) + w.t0_ms
        lo = max(_utc(w.lod.year, w.lod.month, w.lod.day), START_MS)
        hi = _utc(w.hid.year, w.hid.month, w.hid.day) + DAY_MS
        df = df[(sig >= lo) & (sig < hi)].copy()
        df["sig_abs"] = sig[(sig >= lo) & (sig < hi)]
        parts.append(df)
    return pd.concat(parts, ignore_index=True)


def n24_pass(twin, data):
    """Для сделок двойника: n24 ≥ 220000 в строке данных `(win, sym, sig_ms)`. Строки с одним ключом, не согласные по порогу, и сделки без строки считаются и НЕ проходят."""
    g = data[["win", "sym", "meta_sig_ms", "n24"]].copy()
    g["n24"] = g["n24"].astype(float)
    agg = g.groupby(["win", "sym", "meta_sig_ms"])["n24"].agg(["min", "max", "count", "size"]).reset_index()
    agg = agg.rename(columns={"meta_sig_ms": "sig_rel"})
    m = twin[["win", "sym", "sig_rel"]].merge(agg, on=["win", "sym", "sig_rel"], how="left")
    has = m["size"].notna().to_numpy()
    mn, mx, cnt = m["min"].to_numpy(float), m["max"].to_numpy(float), m["count"].to_numpy(float)
    nan24 = has & (cnt == 0)
    amb = has & ~nan24 & (mn < N24_MIN) & (mx >= N24_MIN)
    ok = has & ~nan24 & (mn >= N24_MIN)
    return ok, dict(twin_trades=int(len(twin)), no_data_row=int((~has).sum()), n24_nan=int(nan24.sum()), ambiguous_rows_treated_as_fail=int(amb.sum()), passed=int(ok.sum()))


def base_cand(tw, sym_id):
    """Кандидаты базы из сделок двойника. `dist` — порядок строк файла (как в воротах G-pf)."""
    n = len(tw)
    return Cand(tw["sig_abs"].to_numpy(np.int64), np.arange(n), tw["sym"].map(sym_id).to_numpy(np.int64), tw["level"].to_numpy(),
                tw["exit_abs"].to_numpy(np.int64), (tw["kind"] == "stop").to_numpy(), tw["net"].to_numpy(float),
                extra=dict(sym_name=tw["sym"].to_numpy(), level=tw["level"].to_numpy(), sig_abs=tw["sig_abs"].to_numpy(np.int64)))


# ───────────────────────────── линии, нулевая модель, плацебо ─────────────────────────────

def accepted_trades(c, pos, acc):
    """Принятые реплика сделки (позиции `pos[acc]`), закрытые внутри периода; счёт и Σ вне периода — отдельно."""
    sel = pos[acc]
    inr = c.inrange[sel]
    return dict(pos=sel[inr], day=c.day[sel][inr], net=c.net[sel][inr], stop=c.stop[sel][inr], sym=c.sym[sel][inr], ms=c.ms[sel][inr],
                n_after=int((~inr).sum()), sum_after=float(c.net[sel][~inr].sum()))


def run_line(c, pos, cfg=None):
    acc = c.replay(pos, cfg)
    return accepted_trades(c, pos, acc)


def coin_sums(tr, ncoins):
    return np.bincount(tr["sym"], weights=tr["net"], minlength=ncoins)


def coin_dict(arr, names):
    return {names[i]: float(arr[i]) for i in np.nonzero(arr)[0]}


def null_cell(c, counts, nnull, seed, model_sum):
    """Нулевая модель: случайные наборы кандидатов той же численности по месяцам (месяц сигнала), та же реплика; Σ по сделкам, закрытым внутри периода."""
    bounds = np.searchsorted(c.ms, np.asarray(MONTH_START_MS + (END_MS,)), side="left")
    rng = np.random.default_rng(seed)
    sums = np.empty(nnull)
    for b in range(nnull):
        parts = []
        for m in range(4):
            a, e, k = int(bounds[m]), int(bounds[m + 1]), int(counts[m])
            if k <= 0:
                continue
            parts.append(a + (np.arange(e - a) if k >= e - a else np.sort(rng.choice(e - a, size=k, replace=False))))
        sel = np.concatenate(parts).astype(np.int64) if parts else np.zeros(0, dtype=np.int64)
        acc = c.replay(sel)
        sums[b] = float(c.net_in[sel][acc].sum())
    below = float((sums < model_sum).mean() + 0.5 * (sums == model_sum).mean())
    return dict(n=int(nnull), mean=float(sums.mean()), sd=float(sums.std(ddof=1)) if nnull > 1 else _nan(), p5=float(np.percentile(sums, 5)), p95=float(np.percentile(sums, 95)),
                max=float(sums.max()), model_sum=float(model_sum), model_percentile=below * 100, p_ge=float((1 + (sums >= model_sum).sum()) / (1 + nnull)), counts=[int(x) for x in counts])


def placebo(c, theta):
    """Плацебо: для окт и 01–13.11 выбор по `s_plc ≥ θ` (метки настоящие), реплика со стартом 01.10 с пустым состоянием; рядом — модель `s_hat` на тех же месяцах и тем же протоколом."""
    out = {}
    for name, score in (("plc", c.x["s_plc"]), ("model", c.x["s_hat"])):
        sel = np.nonzero((score >= theta) & (c.month >= 2) & (c.month <= 3))[0]
        tr = run_line(c, sel)
        res = {"n_trades": int(len(tr["net"])), "sum": float(tr["net"].sum()), "months": []}
        for m in (2, 3):
            tm = MONTH_OF_DAY[tr["day"]] == m
            in_m = (c.month == m) & ~np.isnan(score)
            res["months"].append(dict(month=MONTH_NAMES[m], share_rows=float(((score >= theta) & in_m).sum() / in_m.sum()) if in_m.sum() else _nan(), rows=int(in_m.sum()),
                                      selected_rows=int(((score >= theta) & in_m).sum()), n_trades=int(tm.sum()), sum=float(tr["net"][tm].sum())))
        out[name] = res
    return out


# ───────────────────────────── основной счёт ─────────────────────────────

def theta_id(side, th):
    return "%s|t%g" % (side, th)


def compute(a, log=print):
    t_start = time.time()
    sb = load_sb(a.sb_dir)
    res = {"meta": {}, "lines": {}, "cells": {}, "null": {}, "placebo": {}, "refcheck": {}, "counter": {}, "n24_join": {}, "inputs": {}}
    data = {s: read_side(a.data_dir, s) for s in SIDES}
    pred = {s: read_pred(a.fit_dir, s) for s in SIDES}
    twins = {}
    for b, (tw, side, _) in BASES.items():
        twins[b] = load_twin(a.gate_dir, tw, side)
    names = set()
    for s in SIDES:
        names.update(data[s].loc[data[s]["meta_abs_ms"] >= START_MS, "sym"].unique().tolist())
    for t in twins.values():
        names.update(t["sym"].unique().tolist())
    names_sorted = sorted(names)
    sym_id = {n: i for i, n in enumerate(names_sorted)}
    ncoins = len(sym_id)
    coin_arr = {}
    idx = boot_index(a.nboot)
    # warm-up numba до потоков
    _replay_kernel(np.zeros(1, np.int64), np.zeros(1, np.int64), np.zeros(1, np.int64), np.zeros(1, np.int64), np.zeros(1, np.bool_), 1, 1, *_cfg_tuple())

    # ── базы (реплика на двойниках) + справка против движка
    base = {}
    for b, (tw, side, tpl) in BASES.items():
        t = twins[b]
        if b == "L_n24":
            ok, j = n24_pass(t, data["long"][data["long"]["meta_abs_ms"] >= START_MS])
            res["n24_join"] = j
            t = t[ok].reset_index(drop=True)
        c = base_cand(t, sym_id)
        pos = np.arange(len(c))
        acc = c.replay(pos)
        tr = accepted_trades(c, pos, acc)
        base[b] = (c, tr)
        eng = load_engine(a.engine_tpl.get(b, tpl), side)
        rep_keys = set(zip(c.x["sym_name"][pos[acc]].tolist(), c.x["level"][pos[acc]].tolist(), c.x["sig_abs"][pos[acc]].tolist()))
        eng_keys = set(zip(eng["sym"].tolist(), eng["level"].tolist(), eng["sig_abs"].tolist()))
        s_rep, s_eng = float(c.net[pos[acc]].sum()), float(eng["net"].sum())
        common = len(rep_keys & eng_keys)
        res["refcheck"][b] = dict(twin_trades=int(len(t)), replica_trades=int(acc.sum()), engine_trades=int(len(eng)), common=common, only_replica=len(rep_keys - eng_keys), only_engine=len(eng_keys - rep_keys),
                                  overlap_share=common / max(len(eng_keys), 1), sum_replica=s_rep, sum_engine=s_eng, d_sum=s_rep - s_eng,
                                  d_sum_share=(s_rep - s_eng) / s_eng if s_eng else _nan(), within_g_pf_tolerance=bool(common / max(len(eng_keys), 1) >= 0.99 and abs(s_rep - s_eng) <= 0.02 * abs(s_eng)))
        ser, daily = line_series(tr["day"], tr["net"], tr["stop"])
        ser.update(side=side, kind="base", daily=daily.tolist(), n_closed_after=tr["n_after"], sum_closed_after=tr["sum_after"], twin=tw)
        coin_arr[b] = coin_sums(tr, ncoins)
        ser["coin_sums"] = coin_dict(coin_arr[b], names_sorted)
        res["lines"][b] = ser
        log("база %s: сделок %d, Σ %+.2f $, Шарп %.2f (%.0f с)" % (b, ser["total"]["n"], ser["total"]["sum"], ser["total"]["sharpe"], time.time() - t_start))

    # ── модель
    cands, cinfo = {}, {}
    for s in SIDES:
        cands[s], cinfo[s] = build_model_cand(data[s], pred[s], sym_id)
    res["inputs"] = cinfo
    model_lines, counts = {}, {}
    for s in SIDES:
        c = cands[s]
        prev = None
        for th in THETAS:
            pos = np.nonzero(c.x["s_hat"] >= th)[0]
            if prev is not None:
                assert np.isin(pos, prev).all(), "θ не монотонен"
            prev = pos
            tr = run_line(c, pos)
            ser, daily = line_series(tr["day"], tr["net"], tr["stop"])
            mrows = [int((c.month[pos] == m).sum()) for m in range(4)]
            ser.update(side=s, kind="model", theta=th, daily=daily.tolist(), selected_rows=int(len(pos)), selected_rows_month=mrows, n_closed_after=tr["n_after"], sum_closed_after=tr["sum_after"])
            coin_arr[theta_id(s, th)] = coin_sums(tr, ncoins)
            ser["coin_sums"] = coin_dict(coin_arr[theta_id(s, th)], names_sorted)
            res["lines"][theta_id(s, th)] = ser
            model_lines[(s, th)] = (tr, daily, ser)
            counts[(s, th)] = mrows
            log("модель %s θ=%g: строк %d, сделок %d, Σ %+.2f $ (%.0f с)" % (s, th, len(pos), ser["total"]["n"], ser["total"]["sum"], time.time() - t_start))

    # ── вывод по ячейкам
    cells = {}
    for s in SIDES:
        for th in THETAS:
            tr, daily, ser = model_lines[(s, th)]
            for kind, b in (("primary", PRIMARY_BASE[s]), ("ref", REF_BASE[s])):
                bl = res["lines"][b]
                bd = np.asarray(bl["daily"])
                d = delta_stats(daily, bd, coin_arr[theta_id(s, th)], coin_arr[b], idx)
                cells[(s, th, kind)] = dict(side=s, theta=th, kind=kind, base=b, **d)
    prim = [(s, th) for s in SIDES for th in THETAS]
    hl = holm([cells[(s, th, "primary")]["p"] for s, th in prim])
    for (s, th), h in zip(prim, hl):
        cells[(s, th, "primary")].update(holm_rank=h["rank"], holm_step=h["step"], holm_pass=h["passed"], mde=mde(cells[(s, th, "primary")]["sd_boot"], h["step"]))
    strict = ALPHA / N_CELLS
    for s in SIDES:
        for th in THETAS:
            r = cells[(s, th, "ref")]
            r.update(holm_rank=None, holm_step=strict, holm_pass=bool(r["p"] <= strict), mde=mde(r["sd_boot"], strict))
    for (s, th, kind), cl in cells.items():
        ser, bl = model_lines[(s, th)][2], res["lines"][cl["base"]]
        v, conds = verdict(cl["holm_pass"], ser["total"]["sum"], bl["total"]["sum"], ser["total"]["sharpe"], bl["total"]["sharpe"], cl["month_dsum"], [m["n"] for m in ser["months"]],
                           cl["dsum"], cl["dsum_no2days"], cl["dsum_no2coins"])
        cl.update(verdict=v, conditions=conds, model_months_n=[m["n"] for m in ser["months"]], model_share_days=ser["total"]["share_days_trades"], sum_model=ser["total"]["sum"], sum_base=bl["total"]["sum"],
                  sharpe_model=ser["total"]["sharpe"], sharpe_base=bl["total"]["sharpe"], months_dsum_positive=int(sum(1 for x in cl["month_dsum"] if x > 0)),
                  months_n_ok=int(sum(1 for m in ser["months"] if m["n"] >= MIN_TRADES_MONTH)))
        cl["dsr"] = dsr_cell(sb, ser["total"]["sharpe"]) if kind == "primary" else None
    res["cells"] = {"%s|t%g|%s" % k: v for k, v in cells.items()}

    # ── нулевая модель (основные ячейки), плацебо
    def job(si, ti):
        s, th = SIDES[si], THETAS[ti]
        tr, daily, ser = model_lines[(s, th)]
        return (s, th), null_cell(cands[s], counts[(s, th)], a.nnull, [SEED, si, ti], ser["total"]["sum"] + 0.0)
    t0 = time.time()
    keys = [(si, ti) for si in range(2) for ti in range(3)]
    with cf.ThreadPoolExecutor(max_workers=max(1, a.threads)) as ex:
        for (s, th), r in ex.map(lambda k: job(*k), keys):
            res["null"][theta_id(s, th)] = r
    log("нулевая модель: %d ячеек × %d наборов, %.0f с" % (len(keys), a.nnull, time.time() - t0))
    for s in SIDES:
        for th in THETAS:
            res["placebo"][theta_id(s, th)] = placebo(cands[s], th)

    # ── счётчик и DSR
    cl_all = {k: v for k, v in cells.items() if k[2] == "primary"}
    res["counter"] = dict(prior=PRIOR_TRIALS, cells=N_CELLS, trials=TRIALS, text="CatBoost/f12 trials: %d + %d = %d" % (PRIOR_TRIALS, N_CELLS, TRIALS),
                          call="sb.significance(S, se, %d), se = sqrt(365/%d·(1 + S²/730)), S — годовой Шарп модели за авг–ноя" % (TRIALS, NDAYS), source=os.path.abspath(sb.__file__),
                          note="все семейства признаков и сами правила базы найдены на этих данных (01.07–13.11); вывод — только кандидат вперёд")
    res["meta"] = dict(start="2026-08-01", end="2026-11-13", days=int(NDAYS), nboot=a.nboot, block=BLOCK, seed=SEED, nnull=a.nnull, alpha=ALPHA, power=POWER, thetas=list(THETAS), margin=MARGIN, n24_min=N24_MIN,
                       cfg=dict(pf.BASE_CFG), numba=bool(hasattr(_replay_kernel, "py_func")), elapsed_s=round(time.time() - t_start, 1), clarifications=CLARIFICATIONS)
    return res


CLARIFICATIONS = [
    "p = доля бутстреп-сумм Δ ≤ 0 (число ÷ число повторов, без поправки +1).",
    "Справочные ячейки L_n24 и D_S (вне Холма): шаг и MDE по самому строгому шагу Холма 0,05/6; условие «Холм» вердикта — p ≤ 0,05/6; вердикт справочный.",
    "«Строк ≥ 30 минимум в 3 месяцах» — принятые репликой сделки модели по месяцу закрытия (число выбранных строк по месяцу сигнала печатается рядом).",
    "«Без 2 лучших суток / монет»: из ΔΣ вычитаются два наибольших ПОЛОЖИТЕЛЬНЫХ суточных (по-монетных) Δ; ΔΣ по монетам — модель минус база на тех же сутках периода (соглашение tk668_post.line_tests).",
    "Сделки, закрытые после 13.11 23:59:59 UTC, в ряд 105 суток не входят у модели и баз одинаково (число и Σ печатаются).",
    "Месяц сделки — месяц закрытия (как сутки P&L); в нулевой модели численность по месяцу — выбранные строки с меткой по месяцу сигнала.",
    "Плацебо: Окт и 01–13.11 одной репликой со стартом 01.10 с пустым состоянием; модель на тех же месяцах и тем же протоколом — рядом.",
    "Годовой Шарп при нулевой дисперсии суточного ряда = 0 (как tk663_shape.sharpe).",
    "Базы: порядок ничьих `sig_ms` — как в файле двойника (`dist` = номер строки, как в воротах G-pf); ключ уровня базы — `level` двойника; модели — `meta_level_px_e8`.",
]


# ───────────────────────────── отчёт ─────────────────────────────

def _f(x, nd=2, sign=False):
    return sh.num(None if x is None else float(x), nd, sign)


def _th(th):
    return ("%g" % th).replace(".", ",")


def _line_title(k, ser):
    if ser["kind"] == "model":
        return "%s модель θ=%s" % (SIDE_RU[ser["side"]], _th(ser["theta"]))
    return k


def _strip(o):
    """JSON без NaN и numpy-типов."""
    if isinstance(o, dict):
        return {str(k): _strip(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_strip(v) for v in o]
    if isinstance(o, np.ndarray):
        return _strip(o.tolist())
    if isinstance(o, (np.floating, float)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def build_report(res):
    L, P = res["lines"], []
    cells = res["cells"]
    order_lines = lambda side: [theta_id(side, th) for th in THETAS] + [PRIMARY_BASE[side], REF_BASE[side]]

    def t(side, th):
        return L[theta_id(side, th)]["total"]

    # верх: ≤ 5 строк, R:R первым
    P.append("# TK-671 (§13.71): CatBoost на всём наборе, правила — признаки. Реплика портфеля, сравнение с базой, 01.08–13.11 (105 суток), ставка 500 $, плечо 2\n")
    for side in SIDES:
        bits = []
        for k in order_lines(side):
            m = L[k]["total"]
            bits.append("%s %s (стоп %s $ = %s %% маржи 250 $, макс. %s $)" % (_line_title(k, L[k]), _f(m["rr"]), _f(m["stop_mean"], 1, True), _f(m["stop_mean_pct"], 1, True), _f(m["stop_max"], 1, True)))
        P.append("- **R:R %s** (средний выигрыш ÷ |средний убыток|, авг–ноя): %s." % (SIDE_RU[side], "; ".join(bits)))
    groups = {V_ADD: [], V_NO: [], V_UNCLEAR: []}
    for side in SIDES:
        for th in THETAS:
            c = cells["%s|t%g|primary" % (side, th)]
            groups[c["verdict"]].append("%s θ=%s (p %s, шаг Холма %s)" % (SIDE_RU[side], _th(th), _f(c["p"], 4), _f(c["holm_step"], 4)))
    P.append("- **Вердикты (6 основных ячеек, база стороны, Холм m = 6):** " + "; ".join("%s — %s" % (v, ", ".join(x) if x else "нет") for v, x in groups.items()) + ".")
    ss = []
    for side in SIDES:
        b = L[PRIMARY_BASE[side]]["total"]
        ss.append("%s: база %s %s $ (n %d, Шарп %s); модель θ=0 / 0,3 / 0,6 — %s / %s / %s $ (Шарп %s / %s / %s; n %d / %d / %d)" % (
            SIDE_RU[side], PRIMARY_BASE[side], _f(b["sum"], 0, True), b["n"], _f(b["sharpe"]), _f(t(side, 0)["sum"], 0, True), _f(t(side, 0.3)["sum"], 0, True), _f(t(side, 0.6)["sum"], 0, True),
            _f(t(side, 0)["sharpe"]), _f(t(side, 0.3)["sharpe"]), _f(t(side, 0.6)["sharpe"]), t(side, 0)["n"], t(side, 0.3)["n"], t(side, 0.6)["n"]))
    P.append("- **Σ $ за авг–ноя:** " + "; ".join(ss) + ".")
    P.append("- **Счётчик:** %s; всё найдено на этих данных (01.07–13.11), результат — только кандидат вперёд (В-165)." % res["counter"]["text"])
    P.append("")

    # 1. R:R и стопы
    P.append("## 1. R:R и убыток стопа (авг–ноя; 250 $ маржи)\n")
    for side in SIDES:
        rows = []
        for k in order_lines(side):
            m = L[k]["total"]
            rows.append([_line_title(k, L[k]), m["n"], _f(m["mean_win"], 2, True), _f(m["mean_loss"], 2, True), _f(m["rr"]), m["stop_n"], "%s (%s %%)" % (_f(m["stop_mean"], 2, True), _f(m["stop_mean_pct"], 1, True)),
                         "%s (%s %%)" % (_f(m["stop_max"], 2, True), _f(m["stop_max_pct"], 1, True))])
        P.append("**%s**\n" % SIDE_RU[side])
        P.append("\n".join(sh.table(["линия", "сделок", "средний выигрыш, $", "средний убыток, $", "R:R", "стопов", "убыток стопа средний, $ (% маржи)", "убыток стопа макс., $ (% маржи)"], rows)))

    # 2. итоги за период
    P.append("## 2. Итоги за авг–ноя (ряд на все 105 суток; сутки — закрытие сделки, UTC)\n")
    for side in SIDES:
        rows = []
        for k in order_lines(side):
            m = L[k]["total"]
            rows.append([_line_title(k, L[k]), _f(m["sum"], 2, True), _f(m["mean"], 3, True), m["n"], _f(m["sharpe"]), "%s (%s %%)" % (_f(m["maxdd"]), _f(m["maxdd_pct"], 1)), sh.pct(m["share_days_trades"])])
        P.append("**%s**\n" % SIDE_RU[side])
        P.append("\n".join(sh.table(["линия", "Σ, $", "на сделку, $", "сделок", "годовой Шарп", "макс. просадка, $ (% маржи)", "доля суток со сделками"], rows)))

    P.append("## 3. По месяцам (месяц закрытия): Σ $ · $/сделку · n · Шарп\n")
    for side in SIDES:
        rows = []
        for k in order_lines(side):
            row = [_line_title(k, L[k])]
            for mm in L[k]["months"]:
                row.append("%s · %s · %d · %s" % (_f(mm["sum"], 1, True), _f(mm["mean"], 2, True), mm["n"], _f(mm["sharpe"], 1)))
            rows.append(row)
        P.append("**%s**\n" % SIDE_RU[side])
        P.append("\n".join(sh.table(["линия"] + list(MONTH_NAMES), rows)))

    P.append("## 4. Перехай P&L (сутки между новыми максимумами; авг–ноя)\n")
    P.append("Зазор — календарных суток между соседними новыми максимумами конца суток (старт — 0); «самый длинный» включает хвост до конца периода; средний и p90 — по закрытым зазорам; доля под водой — доля суток ниже пика.\n")
    for side in SIDES:
        rows = []
        for k in order_lines(side):
            m = L[k]["total"]
            rows.append([_line_title(k, L[k]), m["highs"], m["gap_longest"], _f(m["gap_mean"], 1), _f(m["gap_p90"], 1), m["tail"], sh.pct(m["under_share"]), m["under_longest"]])
        P.append("**%s**\n" % SIDE_RU[side])
        P.append("\n".join(sh.table(["линия", "новых максимумов", "самый длинный, сут.", "средний, сут.", "p90, сут.", "хвост, сут.", "доля суток под водой", "самый долгий эпизод под водой, сут."], rows)))

    # 5. вывод
    P.append("## 5. Шум и вердикт: парная суточная Δ (модель − база), блочный бутстреп (блок %d, %d, seed %d)\n" % (BLOCK, res["meta"]["nboot"], SEED))
    P.append("Основные ячейки — база стороны, Холм m = 6; ячейки `справка` (L_n24, D_S) — тот же тест вне Холма (самый строгий шаг 0,05/6). MDE — что вообще видно при 80 % мощности. Δ по месяцам — месяц закрытия.\n")
    rows = []
    for key, c in cells.items():
        side, th, kind = key.split("|")[0], c["theta"], c["kind"]
        rows.append(["%s θ=%s vs %s%s" % (SIDE_RU[side], _th(th), c["base"], "" if kind == "primary" else " (справка)"), _f(c["sum_model"], 1, True), _f(c["sum_base"], 1, True), _f(c["dsum"], 1, True), _f(c["p"], 4),
                     ("%d · %s" % (c["holm_rank"], _f(c["holm_step"], 4)) if c["holm_rank"] else "— · %s" % _f(c["holm_step"], 4)) + (" · да" if c["holm_pass"] else " · нет"),
                     _f(c["sd_boot"], 1), _f(c["mde"], 1), " / ".join(_f(x, 1, True) for x in c["month_dsum"]), "%d из 4" % c["months_dsum_positive"],
                     " / ".join(str(x) for x in c["model_months_n"]), _f(c["dsum_no2days"], 1, True), _f(c["dsum_no2coins"], 1, True), sh.pct(c["model_share_days"]), c["verdict"]])
    P.append("\n".join(sh.table(["ячейка", "Σ модели, $", "Σ базы, $", "ΔΣ, $", "p (односторонний)", "ранг · шаг Холма · прошла", "SD бутстрепа ΔΣ, $", "MDE, $", "ΔΣ по месяцам, $", "месяцев Δ>0", "сделок по месяцам", "ΔΣ без 2 лучших суток, $", "ΔΣ без 2 лучших монет, $", "доля суток со сделками", "вердикт"], rows)))

    P.append("### 5.1. Условия вердикта (основные ячейки)\n")
    rows = []
    for key, c in cells.items():
        if c["kind"] != "primary":
            continue
        cd = c["conditions"]
        yn = lambda x: "да" if x else "нет"
        rows.append(["%s θ=%s" % (SIDE_RU[c["side"]], _th(c["theta"])), yn(cd["holm"]), yn(cd["sum_ge"]), yn(cd["sharpe_ge"]), yn(cd["months_pos"]), yn(cd["months_n"]), yn(cd["no2days"]), yn(cd["no2coins"]), c["verdict"]])
    P.append("\n".join(sh.table(["ячейка", "p ≤ шага Холма", "Σ модели ≥ базы", "Шарп модели ≥ базы", "Δ>0 в ≥ 3 из 4 месяцев", "≥ 30 сделок в ≥ 3 месяцах", "ΔΣ>0 без 2 лучших суток", "ΔΣ>0 без 2 лучших монет", "вердикт"], rows)))

    # 6. нулевая модель и плацебо
    P.append("## 6. Нулевая модель: %d случайных наборов той же численности по месяцам, та же реплика\n" % res["meta"]["nnull"])
    rows = []
    for k, r in res["null"].items():
        side, th = k.split("|t")
        rows.append(["%s θ=%s" % (SIDE_RU[side], _th(float(th))), "/".join(str(x) for x in r["counts"]), _f(r["mean"], 1, True), _f(r["sd"], 1), "%s … %s" % (_f(r["p5"], 1, True), _f(r["p95"], 1, True)), _f(r["model_sum"], 1, True),
                     _f(r["model_percentile"], 1) + " %", _f(r["p_ge"], 4)])
    P.append("\n".join(sh.table(["ячейка", "выбрано строк по месяцам", "Σ набора, среднее, $", "SD, $", "5 … 95 %, $", "Σ модели, $", "процентиль модели", "доля наборов с Σ ≥ модели"], rows)))
    P.append("## 7. Плацебо (`s_plc`, окт и 01–13.11, метки настоящие; старт реплики 01.10)\n")
    rows = []
    for k, r in res["placebo"].items():
        side, th = k.split("|t")
        pm, mm = r["plc"], r["model"]
        rows.append(["%s θ=%s" % (SIDE_RU[side], _th(float(th))), " / ".join("%s %s" % (x["month"], sh.pct(x["share_rows"], 1)) for x in pm["months"]), pm["n_trades"], _f(pm["sum"], 1, True),
                     " / ".join("%s %s" % (x["month"], sh.pct(x["share_rows"], 1)) for x in mm["months"]), mm["n_trades"], _f(mm["sum"], 1, True)])
    P.append("\n".join(sh.table(["ячейка", "доля строк ≥ θ (плацебо)", "сделок", "Σ плацебо, $", "доля строк ≥ θ (модель)", "сделок", "Σ модели на тех же месяцах, $"], rows)))

    # 8. счётчик
    P.append("## 8. Счётчик и DSR\n")
    P.append("**%s** (прежний счётчик CatBoost / f12 = %d + эти 6 ячеек); справочные ячейки в счётчик не входят. Все семейства признаков — «найдено на этих данных». DSR: `%s`, источник `%s`.\n" % (
        res["counter"]["text"], PRIOR_TRIALS, res["counter"]["call"], res["counter"]["source"]))
    rows = []
    for key, c in cells.items():
        if c["kind"] != "primary":
            continue
        d = c["dsr"]
        rows.append(["%s θ=%s" % (SIDE_RU[c["side"]], _th(c["theta"])), _f(d["sharpe"]), _f(d["se"], 3), _f(d["noise_bar"]), _f(d["deflated"]), "да" if d["survives"] else "нет"])
    P.append("\n".join(sh.table(["ячейка", "Шарп модели", "SE", "планка шума (n = 34)", "дефлированный Шарп", "значим"], rows)))

    # 9. справка
    P.append("## 9. Справка: реплика на двойниках против движка (01.08–13.11, сделки по ключу `(sym, level, sig_ms)`)\n")
    rows = []
    for b, r in res["refcheck"].items():
        rows.append([b, r["twin_trades"], r["replica_trades"], r["engine_trades"], r["common"], r["only_replica"], r["only_engine"], sh.pct(r["overlap_share"], 2), _f(r["sum_replica"], 2, True), _f(r["sum_engine"], 2, True), _f(r["d_sum"], 2, True),
                     "да" if r["within_g_pf_tolerance"] else "НЕТ"])
    P.append("\n".join(sh.table(["база", "сделок двойника", "реплика", "движок", "общих", "только реплика", "только движок", "перекрытие", "Σ реплики, $", "Σ движка, $", "ΔΣ, $", "в допуске G-pf (≥ 99 % ключей, модуль ΔΣ ≤ 2 %)"], rows)))
    r24 = res["refcheck"]["L_n24"]
    if not r24["within_g_pf_tolerance"]:
        P.append("**L_n24: реплика НЕ совпала с движком** (перекрытие %s, ΔΣ %s $, только реплика %d, только движок %d) — числа L_n24 принимать с оговоркой.\n" % (sh.pct(r24["overlap_share"], 2), _f(r24["d_sum"], 2, True), r24["only_replica"], r24["only_engine"]))
    j = res["n24_join"]
    P.append("L_n24: сделок двойника %d; без строки данных %d; n24 пустой %d; строки одного ключа не согласны по порогу %d (считаются НЕ прошедшими); прошло n24 ≥ %d: %d.\n" % (j["twin_trades"], j["no_data_row"], j["n24_nan"], j["ambiguous_rows_treated_as_fail"], int(N24_MIN), j["passed"]))
    inp = res["inputs"]
    for side in SIDES:
        i = inp[side]
        P.append("Данные %s: строк предсказаний %d, без строки данных %d, вне периода %d, строк с меткой %d, без метки %d (не торгуют), несогласованных меток %d." % (SIDE_RU[side], i["pred_rows"], i["pred_without_data"], i["rows_outside_period"], i["labeled_rows"], i["unlabeled_rows"], i["labeled_inconsistent"]))
    P.append("")
    for k, ser in L.items():
        if ser["n_closed_after"]:
            P.append("Закрыто после 13.11 и не вошло в ряд: %s — %d сделок, Σ %s $." % (_line_title(k, ser), ser["n_closed_after"], _f(ser["sum_closed_after"], 2, True)))
    P.append("\n## 10. Уточнения прочтения протокола\n")
    P.extend("- " + c for c in res["meta"]["clarifications"])
    P.append("")
    return "\n".join(P)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--fit-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gate-dir", default="/data/p42-tk671/gate-pf", help="<gate-dir>/<окно>/out/{L_ind,S_ind,D_ind}/{long,short}-trades.csv")
    ap.add_argument("--sb-dir", default=None, help="каталог с selection_bias.py (по умолчанию — рядом или .claude/skills/alpha-research/scripts)")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--nboot", type=int, default=NBOOT)
    ap.add_argument("--nnull", type=int, default=NNULL)
    for b, (_, _, tpl) in BASES.items():
        ap.add_argument("--engine-%s" % b.lower().replace("_", "-"), dest="engine_" + b, default=tpl, help="файл сделок движка для %s, {win} — окно" % b)
    a = ap.parse_args(argv)
    a.engine_tpl = {b: getattr(a, "engine_" + b) for b in BASES}
    os.makedirs(a.out, exist_ok=True)
    res = compute(a)
    with open(os.path.join(a.out, "tables.json"), "w", encoding="utf-8") as f:
        json.dump(_strip(res), f, ensure_ascii=False, indent=1)
    with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(build_report(res))
    print("готово:", os.path.join(a.out, "report.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
