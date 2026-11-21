"""Тесты §13.71 (TK-671), отчёт `tk671_report.py`: реплика (numba) = `tk671_pf.replay`, Холм, блочный бутстреп, старт 01.08 с пустым состоянием, сутки закрытия, Шарп / просадка / перехай, вердикты, θ монотонно,
нулевая модель, плацебо, соединение n24, DSR, сквозной прогон на синтетике (≈ 30 тыс. строк, 2 стороны, известное преимущество). Реальных данных нет, метки не читаются.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk671_report.py` (нужны numpy, pandas, pyarrow, numba; на srv `/data/tk592/venv/bin/python`) — выполняет все test_* и печатает итог."""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk604_stats as st  # noqa: E402
import tk671_pf as pf  # noqa: E402
import tk671_report as rp  # noqa: E402

MIN = 60_000
THREADS = int(os.environ.get("TK671_TEST_THREADS", "1"))      # srv занят: по умолчанию один поток; 2 — только вручную


def ms(y, m, d, hh=0, mi=0, ss=0, frac=0):
    return int(dt.datetime(y, m, d, hh, mi, ss, tzinfo=dt.timezone.utc).timestamp() * 1000) + frac


# ───────────────────────────── синтетика ─────────────────────────────

COINS = ["C%02dUSDT" % i for i in range(60)]
WIN = {w.name: w for w in st.TRADE_WINDOWS}
ZACHET = {w.name: (ms(w.lod.year, w.lod.month, w.lod.day), ms(w.hid.year, w.hid.month, w.hid.day) + rp.DAY_MS) for w in st.TRADE_WINDOWS}
JUNK = {"wa": (ms(2026, 6, 20), ms(2026, 6, 30)), "wb": (ms(2026, 8, 5), ms(2026, 8, 20)), "wc": (ms(2026, 9, 5), ms(2026, 9, 30))}   # сигналы двойника вне зачётного диапазона окна


def win_of(ab):
    return np.where(ab < ZACHET["wb"][0], "wa", np.where(ab < ZACHET["wc"][0], "wb", "wc"))


def gen_side(rng, n, edge, side_off):
    lo, hi = ms(2026, 7, 10), rp.END_MS
    ab = np.unique(rng.integers(lo, hi, size=int(n * 1.05)))
    ab = np.sort(rng.permutation(ab)[:n])
    win = win_of(ab)
    t0 = np.array([WIN[w].t0_ms for w in win], dtype=np.int64)
    sym_i = rng.integers(0, len(COINS), n)
    lvl_j = rng.integers(0, 6, n)
    f1 = rng.normal(size=n)
    mu = edge * f1
    raw = mu + rng.normal(0, 2.5, n)
    stop = raw < -4.0
    net = np.where(stop, -4.0 - 0.3 * rng.random(n), raw)
    has = rng.random(n) < 0.85
    hold = rng.integers(5, 400, n).astype(np.int64) * MIN
    base_n24 = np.where(np.arange(len(COINS)) % 2 == 0, 3.0e5, 1.0e5)[sym_i]
    df = pd.DataFrame(dict(win=win, row_id=np.arange(n, dtype=np.int64) + side_off, sym=np.array(COINS)[sym_i], meta_abs_ms=ab, meta_sig_ms=ab - t0,
                           meta_level_px_e8=((1 + sym_i) * 100_000_000 * (1 + 0.05 * lvl_j)).astype(np.int64), meta_dist_e8=rng.integers(1, 50_000, n).astype(np.int64),
                           meta_src=rng.integers(0, 12, n).astype(np.int8), y_has=has, y_net=np.where(has, net, np.nan), y_entry_ms=np.where(has, ab + 4, -1),
                           y_exit_ms=np.where(has, ab + hold, -1), y_stop=np.where(has, stop.astype(float), np.nan), n24=base_n24 * (0.9 + 0.2 * rng.random(n)), f1=f1.astype(np.float32)))
    df["_lvl"] = lvl_j
    df["_mu"] = mu
    return df


def gen_pred(rng, data, with_edge):
    d = data[(data.meta_abs_ms >= rp.START_MS) & (data.meta_abs_ms < rp.END_MS)]
    n = len(d)
    s_hat = d["_mu"].to_numpy() + rng.normal(0, 1.0, n) if with_edge else rng.normal(0, 1.5, n)
    plc = rng.normal(0, 1.5, n)
    plc[d["meta_abs_ms"].to_numpy() < ms(2026, 10, 1)] = np.nan
    month = pd.to_datetime(d["meta_abs_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    return pd.DataFrame(dict(win=d["win"].to_numpy(), row_id=d["row_id"].to_numpy(), month=month.to_numpy(), s_hat=s_hat, p_stop=rng.random(n), s_plc=plc))


TWIN_SIDE = {"L_ind": "long", "S_ind": "short", "D_ind": "short"}
TWIN_FRAC = {"L_ind": 0.35, "S_ind": 0.30, "D_ind": 0.25}


def gen_twin(rng, data, tw):
    out = {}
    side = TWIN_SIDE[tw]
    for wn in ("wa", "wb", "wc"):
        t0 = WIN[wn].t0_ms
        d = data[side][(data[side].win == wn) & data[side].y_has]
        d = d[rng.random(len(d)) < TWIN_FRAC[tw]]
        df = pd.DataFrame(dict(sym=d["sym"].to_numpy(), level=d["_lvl"].to_numpy().astype(np.int64), sig_min=d["meta_sig_ms"].to_numpy() // MIN, entry_min=(d["meta_sig_ms"].to_numpy() + 4) // MIN,
                               exit_min=(d["y_exit_ms"].to_numpy() - t0) // MIN, entry_px_e8=d["meta_level_px_e8"].to_numpy(), kind=np.where(d["y_stop"].to_numpy() == 1.0, "stop", "tp"), takes=0, gross=d["y_net"].to_numpy(),
                               fees=0.0, funding=0.0, net=d["y_net"].to_numpy(), entry_day=0, sig_ms=d["meta_sig_ms"].to_numpy(), entry_ms=d["meta_sig_ms"].to_numpy() + 4,
                               exit_ms=(d["y_exit_ms"].to_numpy() - t0)))
        k = 30                                                                                    # мусорные сделки: монета ZZZ, сигнал вне зачётного диапазона окна, большая прибыль — заметны при протечке
        a = np.sort(rng.integers(JUNK[wn][0], JUNK[wn][1], size=k))
        junk = pd.DataFrame(dict(sym="ZZZUSDT", level=999, sig_min=(a - t0) // MIN, entry_min=(a - t0) // MIN, exit_min=(a - t0) // MIN + 60, entry_px_e8=1, kind="tp", takes=0, gross=50.0, fees=0.0, funding=0.0,
                                 net=50.0, entry_day=0, sig_ms=a - t0, entry_ms=a - t0 + 4, exit_ms=a - t0 + 60 * MIN))
        out[wn] = pd.concat([df, junk], ignore_index=True)
    return out


def make_synth(root, seed=7, n=15000, edge=1.0, with_edge=True):
    rng = np.random.default_rng(seed)
    data = {"long": gen_side(rng, n, edge, 0), "short": gen_side(rng, n, edge, 10_000_000)}
    pred = {s: gen_pred(rng, data[s], with_edge) for s in data}
    twin = {tw: gen_twin(rng, data, tw) for tw in TWIN_SIDE}
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    for s in data:
        data[s].drop(columns=["_lvl", "_mu"]).to_parquet(os.path.join(root, "data", "%s.parquet" % s))
        os.makedirs(os.path.join(root, "fit", s), exist_ok=True)
        pred[s].to_parquet(os.path.join(root, "fit", s, "pred.parquet"))
    for tw, wins in twin.items():
        for wn, df in wins.items():
            p = os.path.join(root, "gate", wn, "out", tw)
            os.makedirs(p, exist_ok=True)
            df.to_csv(os.path.join(p, "%s-trades.csv" % TWIN_SIDE[tw]), index=False)
    return dict(data=data, pred=pred, twin=twin)


def expected_base(S, b):
    """Независимая сборка базы: зачётный диапазон окна, сигнал ≥ 01.08, реплика на чистом Python (`tk671_pf.replay`), порядок ничьих — как в файле."""
    tw, side, _ = rp.BASES[b]
    parts = []
    for wn in ("wa", "wb", "wc"):
        df = S["twin"][tw][wn].copy()
        t0 = WIN[wn].t0_ms
        df["win"], df["sig_abs"], df["exit_abs"] = wn, df["sig_ms"] + t0, df["exit_ms"] + t0
        lo, hi = ZACHET[wn]
        parts.append(df[(df.sig_abs >= max(lo, rp.START_MS)) & (df.sig_abs < hi)])
    t = pd.concat(parts, ignore_index=True)
    if b == "L_n24":
        d = S["data"]["long"]
        d = d[d.meta_abs_ms >= rp.START_MS]
        n24 = {(w, s, g): v for w, s, g, v in zip(d.win, d.sym, d.meta_sig_ms, d.n24)}
        keep = [n24.get((w, s, g), -1.0) >= rp.N24_MIN for w, s, g in zip(t.win, t.sym, t.sig_ms)]
        t = t[np.array(keep)].reset_index(drop=True)
    names = sorted(set(t.sym))
    sid = {x: i for i, x in enumerate(names)}
    acc = pf.replay(t.sig_abs.to_numpy(), np.arange(len(t)), t.sym.map(sid).to_numpy(), t.level.to_numpy(), t.exit_abs.to_numpy(), (t.kind == "stop").to_numpy())
    return t[acc]


def in_range_sum(exit_abs, net):
    d = (np.asarray(exit_abs) - rp.START_MS) // rp.DAY_MS
    m = (d >= 0) & (d < rp.NDAYS)
    return float(np.asarray(net)[m].sum()), int(m.sum())


# ───────────────────────────── реплика ─────────────────────────────

def test_replay_hand_cases():
    cfg = None
    # занятость p = 3, пауза монеты 5 мин, один уровень — одна сделка
    t = np.array([0, 2, 12, 16, 17, 18, 26, 27]) * MIN
    ex = np.array([10, 30, 40, 50, 25, 60, 36, 70]) * MIN
    sym = np.array([0, 1, 0, 0, 2, 3, 2, 3])
    key = np.array([1, 1, 2, 2, 1, 1, 1, 1])
    z = np.zeros(8, dtype=bool)
    exp = [True, True, False, True, True, False, False, True]
    for fn in (pf.replay, rp.replay_fast):
        assert list(fn(t, np.arange(8), sym, key, ex, z, cfg)) == exp, fn
    # m = 3 стопов подряд по монете → пауза 60 мин
    t = np.array([0, 10, 20, 30, 90]) * MIN
    ex = t + 2 * MIN
    stp = np.array([1, 1, 1, 1, 0], dtype=bool)
    for fn in (pf.replay, rp.replay_fast):
        assert list(fn(t, np.arange(5), np.zeros(5, int), np.arange(5), ex, stp)) == [True, True, True, False, True]
    # s_glob = 4 стопов за 10 мин по разным монетам → общая пауза 60 мин
    t = np.array([0, 1, 2, 3, 5, 70]) * MIN
    ex = np.array([1, 2, 3, 4, 6, 71]) * MIN
    stp = np.array([1, 1, 1, 1, 0, 0], dtype=bool)
    for fn in (pf.replay, rp.replay_fast):
        assert list(fn(t, np.arange(6), np.arange(6), np.arange(6), ex, stp)) == [True, True, True, True, False, True]


def test_replay_fast_equals_python_random():
    rng = np.random.default_rng(11)
    for trial, cfg in enumerate([None, dict(reentry=1), dict(reentry=2), dict(busy=False), dict(p=1), dict(p=5, s_glob=2, w_min=30), dict(m_stops=2, mm_min=20, cool_min=0), dict(ww_min=0)]):
        n = 4000
        ms_ = np.sort(rng.integers(0, 3 * 86_400_000, n))
        ms_[rng.random(n) < 0.2] = ms_[0] + 1000                                                  # ничьи по времени
        dist = rng.integers(0, 40, n)
        sym = rng.integers(0, 12, n)
        key = rng.integers(0, 6, n)
        ex = ms_ + rng.integers(1, 120, n) * MIN
        stp = rng.random(n) < 0.45
        a = pf.replay(ms_, dist, sym, key, ex, stp, cfg)
        b = rp.replay_fast(ms_, dist, sym, key, ex, stp, cfg)
        assert np.array_equal(a, b), (trial, cfg, int((a != b).sum()))
        assert 0 < a.sum() < n, (trial, int(a.sum()))


# ───────────────────────────── Холм, бутстреп, MDE, «лучшие» ─────────────────────────────

def test_holm_step_down_hand():
    ps = [0.6, 0.001, 0.02, 0.004, 0.013, 0.02]                      # по возрастанию: .001(1) .004(2) .013(3) .02(4) .02(5) .6(6)
    h = rp.holm(ps)
    steps = [0.05 / 6, 0.05 / 5, 0.05 / 4, 0.05 / 3, 0.05 / 2, 0.05]
    order = [1, 3, 4, 2, 5, 0]
    for k, i in enumerate(order):
        assert h[i]["rank"] == k + 1 and abs(h[i]["step"] - steps[k]) < 1e-15, (i, h[i])
    # .001 ≤ .00833 да; .004 ≤ .01 да; .013 ≤ .0125 нет → дальше всё не проходит, даже .02 ≤ .025 (ранг 5)
    assert [h[i]["passed"] for i in order] == [True, True, False, False, False, False]
    assert [x["passed"] for x in h] == st.holm_reject(ps)
    h2 = rp.holm([0.0] * 6)
    assert all(x["passed"] for x in h2) and [x["rank"] for x in h2] == [1, 2, 3, 4, 5, 6]
    h3 = rp.holm([0.5] * 6)
    assert not any(x["passed"] for x in h3)


def test_block_bootstrap_reproducible_and_blocks():
    a = rp.boot_index(500)
    b = rp.boot_index(500)
    assert a.shape == (500, rp.NDAYS) and np.array_equal(a, b)
    assert not np.array_equal(a, rp.boot_index(500, seed=672))
    assert a.min() >= 0 and a.max() < rp.NDAYS
    for blk in range(rp.NDAYS // rp.BLOCK):                                    # внутри блока — подряд идущие сутки (по кругу)
        sl = a[:, blk * rp.BLOCK:(blk + 1) * rp.BLOCK]
        assert ((np.diff(sl, axis=1) % rp.NDAYS) == 1).all()
    assert rp.BLOCK == 5 and rp.NBOOT == 20000 and rp.SEED == 671


def test_bootstrap_p_known_sign():
    idx = rp.boot_index(2000)
    z = np.zeros(rp.NDAYS)

    def p_of(d):
        return rp.delta_stats(d, z, np.array([d.sum()]), np.array([0.0]), idx)

    r = p_of(np.ones(rp.NDAYS))
    assert r["p"] == 0.0 and abs(r["dsum"] - 105) < 1e-9 and r["sd_boot"] == 0.0                # все блоки дают сумму 105
    assert p_of(-np.ones(rp.NDAYS))["p"] == 1.0
    d = np.where(np.arange(rp.NDAYS) % 7 == 0, -1.0, 1.0)                                      # Σ > 0 уверенно
    assert p_of(d)["p"] < 0.01
    assert p_of(-d)["p"] > 0.99
    d0 = np.where(np.arange(rp.NDAYS) % 2 == 0, 1.0, -1.0)
    d0[-1] = 0.0                                                                               # Σ = 0
    assert 0.3 < p_of(d0)["p"] < 0.97, p_of(d0)["p"]


def test_mde_and_two_best():
    assert abs(rp.mde(10.0, 0.05) - (1.6448536 + 0.8416212) * 10) < 1e-3
    assert rp.mde(10.0, 0.05 / 6) > rp.mde(10.0, 0.05 / 2) > rp.mde(10.0, 0.05)
    assert rp.mde(0.0, 0.01) == 0.0
    assert rp.two_best([5, -2, 3, 1, 0]) == 8.0
    assert rp.two_best([4, -1, -1]) == 4.0                                                    # отрицательные не удаляются
    assert rp.two_best([-1, -2]) == 0.0
    # ΔΣ без 2 лучших суток / монет
    dm = np.zeros(rp.NDAYS)
    db = np.zeros(rp.NDAYS)
    dm[[3, 10, 40]] = [5.0, 3.0, 1.0]
    db[[10]] = [-2.0]
    r = rp.delta_stats(dm, db, np.array([4.0, 3.0, 2.0]), np.array([0.0, 0.0, -2.0]), rp.boot_index(100))
    assert abs(r["dsum"] - 11.0) < 1e-12                                                       # 5 + (3 + 2) + 1
    assert abs(r["best_days_removed"] - 10.0) < 1e-12 and abs(r["dsum_no2days"] - 1.0) < 1e-12  # суточные Δ: 5, 5, 1 → удаляются 5 и 5
    assert abs(r["best_coins_removed"] - 8.0) < 1e-12 and abs(r["dsum_no2coins"] - 3.0) < 1e-12  # по монетам Δ: 4, 3, 4 → удаляются 4 и 4
    assert r["month_dsum"] == [10.0, 1.0, 0.0, 0.0]                                            # сутки 3 и 10 — авг, 40 — сен


def test_dsr_call():
    sb = rp.load_sb()
    d = rp.dsr_cell(sb, 2.0)
    se = (365.0 / 105 * (1 + 4.0 / 730.0)) ** 0.5
    bar = sb.expected_max_sharpe(se, 34)
    assert d["trials"] == 34 and abs(d["se"] - se) < 1e-12 and abs(d["noise_bar"] - bar) < 1e-12 and abs(d["deflated"] - (2.0 - bar)) < 1e-12
    assert rp.TRIALS == 34 and rp.PRIOR_TRIALS == 28 and rp.N_CELLS == 6
    assert rp.dsr_cell(sb, 0.0)["deflated"] < 0


# ───────────────────────────── вердикт ─────────────────────────────

def test_verdict_rules():
    ok = dict(holm_pass=True, sum_m=100.0, sum_b=80.0, sh_m=1.5, sh_b=1.2, month_dsum=[5.0, 3.0, -1.0, 2.0], month_n=[40, 35, 31, 10], dsum=20.0, no2days=5.0, no2coins=3.0)
    v, c = rp.verdict(**ok)
    assert v == rp.V_ADD and all(c.values()), (v, c)
    assert rp.V_ADD == "Добавляет к базе (найдено на этих данных; кандидат только вперёд)"
    # каждое условие по отдельности ломает «Добавляет»; ΔΣ > 0 → «не ясно»
    for name, bad in [("holm_pass", False), ("sum_m", 79.0), ("sh_m", 1.1), ("month_dsum", [5.0, -3.0, -1.0, 2.0]), ("month_n", [40, 29, 31, 10]), ("no2days", 0.0), ("no2coins", -1.0)]:
        kw = dict(ok)
        kw[name] = bad
        v, _ = rp.verdict(**kw)
        assert v == rp.V_UNCLEAR, (name, v)
    # точечная ΔΣ ≤ 0 → «Не добавляет»
    for ds in (-5.0, 0.0):
        kw = dict(ok, dsum=ds, no2days=-1.0, sum_m=70.0)
        assert rp.verdict(**kw)[0] == rp.V_NO
    # ровно 3 из 4 месяцев с Δ > 0 и ровно 3 месяца с ≥ 30 сделок — достаточно
    assert rp.verdict(**dict(ok, month_dsum=[1.0, 1.0, 1.0, -9.0], month_n=[30, 30, 30, 0]))[0] == rp.V_ADD
    assert rp.verdict(**dict(ok, month_dsum=[1.0, 1.0, -1.0, -9.0]))[0] == rp.V_UNCLEAR


# ───────────────────────────── метрики ─────────────────────────────

def test_sharpe_maxdd_underwater_hand():
    x = np.array([1, 1, -1, -1, 1, 1, 3, -2, 0, 0], dtype=float)
    ntr = (x != 0).astype(int)
    m = rp.metrics(x[x != 0], np.zeros((x != 0).sum(), bool), x, ntr)
    sharpe = x.mean() / x.std(ddof=1) * np.sqrt(365)
    assert abs(m["sharpe"] - sharpe) < 1e-12 and abs(sharpe - 4.0417) < 1e-3
    assert abs(m["maxdd"] - 2.0) < 1e-12 and abs(m["maxdd_pct"] - 0.8) < 1e-12                  # 2 $ / 250 $
    assert m["highs"] == 3 and m["gap_longest"] == 5 and m["tail"] == 3 and abs(m["under_share"] - 0.6) < 1e-12
    assert abs(m["gap_mean"] - 7.0 / 3) < 1e-12 and abs(m["gap_p90"] - 4.2) < 1e-12              # зазоры 1, 1, 5
    assert abs(m["share_days_trades"] - 0.8) < 1e-12 and m["days"] == 10
    # R:R и стопы
    net = np.array([10.0, -5.0, 5.0, -3.0, -7.0])
    stop = np.array([False, False, False, True, True])
    q = rp.metrics(net, stop, net, np.ones(5, int))
    assert q["n"] == 5 and abs(q["sum"]) < 1e-12 and abs(q["mean"]) < 1e-12
    assert abs(q["mean_win"] - 7.5) < 1e-12 and abs(q["mean_loss"] + 5.0) < 1e-12 and abs(q["rr"] - 1.5) < 1e-12
    assert q["stop_n"] == 2 and abs(q["stop_mean"] + 5.0) < 1e-12 and abs(q["stop_max"] + 7.0) < 1e-12
    assert abs(q["stop_mean_pct"] + 2.0) < 1e-12 and abs(q["stop_max_pct"] + 2.8) < 1e-12
    # ряд из нулей: Шарп 0, нет эпизодов; без сделок — nan
    z = rp.metrics(np.zeros(0), np.zeros(0, bool), np.zeros(10), np.zeros(10, int))
    assert z["sharpe"] == 0.0 and z["n"] == 0 and z["maxdd"] == 0.0 and z["gap_longest"] == 10 and z["episodes"] == 0 and np.isnan(z["rr"]) and np.isnan(z["mean"])


def test_day_of_exit_bucketing_and_months():
    ex = np.array([ms(2026, 8, 1, 23, 59, 59, 999), ms(2026, 8, 2), ms(2026, 11, 13, 23, 59, 59), ms(2026, 11, 14), ms(2026, 7, 31, 23, 59), ms(2026, 8, 31, 23, 59, 59, 999), ms(2026, 9, 1)])
    n = len(ex)
    sig = ex - 10 * MIN
    c = rp.Cand(sig, np.arange(n), np.arange(n), np.arange(n), ex, np.zeros(n, bool), np.arange(1, n + 1, dtype=float))
    assert list(c.day[np.argsort(c.exit)]) == [-1, 0, 1, 30, 31, 104, 105]
    assert list(c.inrange[np.argsort(c.exit)]) == [False, True, True, True, True, True, False]
    assert list(rp.MONTH_OF_DAY[[0, 30, 31, 60, 61, 91, 92, 104]]) == [0, 0, 1, 1, 2, 2, 3, 3] and rp.NDAYS == 105
    pos = np.arange(n)
    tr = rp.accepted_trades(c, pos, np.ones(n, bool))
    assert tr["n_after"] == 2 and len(tr["net"]) == 5                                        # 14.11 00:00 и 31.07 вне периода
    assert abs(tr["sum_after"] - (c.net[np.argsort(c.exit)][0] + c.net[np.argsort(c.exit)][-1])) < 1e-12
    ser, daily = rp.line_series(tr["day"], tr["net"], tr["stop"])
    assert daily.shape == (105,) and abs(daily.sum() - tr["net"].sum()) < 1e-12
    assert sum(m["n"] for m in ser["months"]) == ser["total"]["n"] == 5
    assert [m["n"] for m in ser["months"]] == [3, 1, 0, 1]
    assert np.array_equal(rp.month_of_ms([ms(2026, 7, 31, 23, 59), ms(2026, 8, 1), ms(2026, 9, 1), ms(2026, 10, 1), ms(2026, 11, 1), ms(2026, 11, 13, 23, 59), ms(2026, 11, 14)]), [-1, 0, 1, 2, 3, 3, 4])


def test_replay_start_empty_at_0108():
    sym_id = {"A": 0, "B": 1}
    data = pd.DataFrame(dict(win="wa", row_id=[1, 2, 3, 4], sym=["A", "A", "B", "B"],
                             meta_abs_ms=[ms(2026, 7, 31, 23, 50), ms(2026, 8, 1, 0, 10), ms(2026, 8, 1, 0, 11), ms(2026, 8, 1, 0, 30)], meta_sig_ms=[1, 2, 3, 4],
                             meta_level_px_e8=[100, 200, 300, 400], meta_dist_e8=[1, 1, 1, 1], y_has=[True, True, True, False],
                             y_net=[1.0, 2.0, 3.0, np.nan], y_entry_ms=[0, 0, 0, -1], y_exit_ms=[ms(2026, 8, 1, 0, 30), ms(2026, 8, 1, 1), ms(2026, 8, 1, 1), -1], y_stop=[0.0, 0.0, 1.0, np.nan], n24=[1.0, 1, 1, 1]))
    pred = pd.DataFrame(dict(win="wa", row_id=[1, 2, 3, 4, 9], month="2026-08", s_hat=[1.0, 1.0, 1.0, 1.0, 1.0], p_stop=0.5, s_plc=np.nan))
    c, info = rp.build_model_cand(data, pred, sym_id)
    assert info["pred_rows"] == 5 and info["pred_without_data"] == 1 and info["rows_outside_period"] == 1 and info["unlabeled_rows"] == 1 and info["labeled_rows"] == 2
    assert len(c) == 2 and list(c.x["row"]) == [0, 1]
    acc = c.replay(np.arange(len(c)))
    assert list(acc) == [True, True]                                                         # открытая до 01.08 позиция A не занимает монету: строка A@00:10 принята
    assert list(c.stop) == [False, True] and abs(c.net.sum() - 5.0) < 1e-12
    # контроль: будь строка A@31.07 23:50 в наборе, строка A@00:10 была бы отклонена (монета занята до 00:30)
    c_all = rp.Cand(np.array([ms(2026, 7, 31, 23, 50), ms(2026, 8, 1, 0, 10)]), np.arange(2), np.zeros(2, int), np.array([100, 200]),
                    np.array([ms(2026, 8, 1, 0, 30), ms(2026, 8, 1, 1)]), np.zeros(2, bool), np.ones(2))
    assert list(c_all.replay(np.arange(2))) == [True, False]


def test_theta_monotone_subsets():
    rng = np.random.default_rng(5)
    n = 500
    s = rng.normal(0, 1, n)
    prev = None
    for th in rp.THETAS:
        pos = np.nonzero(s >= th)[0]
        if prev is not None:
            assert np.isin(pos, prev).all() and len(pos) <= len(prev)
        prev = pos
    assert rp.THETAS == (0.0, 0.3, 0.6)


def test_n24_join_counts():
    data = pd.DataFrame(dict(win="wa", sym=["A", "B", "C", "D", "D"], meta_sig_ms=[100, 200, 300, 400, 400], n24=[300000.0, 100000.0, np.nan, 300000.0, 100000.0]))
    twin = pd.DataFrame(dict(win="wa", sym=["A", "B", "C", "D", "E", "A"], sig_rel=[100, 200, 300, 400, 500, 101]))
    ok, j = rp.n24_pass(twin, data)
    assert list(ok) == [True, False, False, False, False, False]
    assert j == dict(twin_trades=6, no_data_row=2, n24_nan=1, ambiguous_rows_treated_as_fail=1, passed=1), j


# ───────────────────────────── нулевая модель, плацебо ─────────────────────────────

def _cand_from_synth(S, side, with_edge_pred=True):
    names = sorted(set(S["data"][side].sym))
    sid = {x: i for i, x in enumerate(names)}
    return rp.build_model_cand(S["data"][side].drop(columns=["_lvl", "_mu"]), S["pred"][side], sid)


def test_null_model_percentile_sanity():
    for with_edge, lo, hi in ((True, 97.0, 100.0), (False, 0.5, 99.5)):
        S = make_synth(tempfile.mkdtemp(), seed=3, n=4000, edge=1.0 if with_edge else 0.0, with_edge=with_edge)
        c, _ = _cand_from_synth(S, "long")
        pos = np.nonzero(c.x["s_hat"] >= 0.0)[0]
        tr = rp.run_line(c, pos)
        counts = [int((c.month[pos] == m).sum()) for m in range(4)]
        assert sum(counts) == len(pos)
        r = rp.null_cell(c, counts, 150, [671, 0, 0], float(tr["net"].sum()))
        assert lo <= r["model_percentile"] <= hi, (with_edge, r)
        assert r["n"] == 150 and r["sd"] > 0 and r["p5"] <= r["mean"] <= r["p95"]
        r2 = rp.null_cell(c, counts, 150, [671, 0, 0], float(tr["net"].sum()))
        assert r == r2                                                                      # воспроизводимо
        # θ-ячейка с полным набором: нулевая модель = все кандидаты → Σ набора = Σ модели (разброса нет)
        full = [int((c.month == m).sum()) for m in range(4)]
        rf = rp.null_cell(c, full, 5, [671, 0, 0], 0.0)
        assert rf["sd"] == 0.0 and abs(rf["mean"] - float(rp.run_line(c, np.arange(len(c)))["net"].sum())) < 1e-9


def test_placebo_hand():
    n = 4
    ms_ = np.array([ms(2026, 10, 5), ms(2026, 10, 6), ms(2026, 11, 5), ms(2026, 11, 6)])
    c = rp.Cand(ms_, np.arange(n), np.arange(n), np.arange(n), ms_ + 30 * MIN, np.zeros(n, bool), np.array([1.0, 2.0, 4.0, 8.0]),
                extra=dict(s_hat=np.array([1.0, 1.0, -1.0, 1.0]), s_plc=np.array([0.5, -1.0, 0.7, 0.9]), row=np.arange(n)))
    r = rp.placebo(c, 0.3)
    p = r["plc"]
    assert p["n_trades"] == 3 and abs(p["sum"] - 13.0) < 1e-12                                # 1 + 4 + 8
    assert [m["selected_rows"] for m in p["months"]] == [1, 2] and [m["rows"] for m in p["months"]] == [2, 2]
    assert abs(p["months"][0]["share_rows"] - 0.5) < 1e-12 and abs(p["months"][1]["share_rows"] - 1.0) < 1e-12
    assert [m["n_trades"] for m in p["months"]] == [1, 2] and abs(p["months"][1]["sum"] - 12.0) < 1e-12
    m = r["model"]
    assert m["n_trades"] == 3 and abs(m["sum"] - 11.0) < 1e-12                                # 1 + 2 + 8


# ───────────────────────────── сквозной прогон ─────────────────────────────

def _run_main(root, S, out, threads, nnull=40, nboot=2000):
    eng = {}
    for b, (tw, side, _) in rp.BASES.items():
        acc = expected_base(S, b)
        if b == "L_n24":
            acc = acc.iloc[1:]                                                                # движок «потерял» одну сделку — расхождение должно быть видно
        for wn in ("wa", "wb", "wc"):
            a = acc[acc.win == wn]
            p = os.path.join(root, "engine", b, wn)
            os.makedirs(p, exist_ok=True)
            a[["sym", "level", "sig_ms", "net"]].to_csv(os.path.join(p, "%s-trades.csv" % side), index=False)
        eng[b] = os.path.join(root, "engine", b, "{win}", "%s-trades.csv" % side)
    argv = ["--data-dir", os.path.join(root, "data"), "--fit-dir", os.path.join(root, "fit"), "--out", out, "--gate-dir", os.path.join(root, "gate"), "--threads", str(threads),
            "--nboot", str(nboot), "--nnull", str(nnull), "--engine-l-base", eng["L_base"], "--engine-l-n24", eng["L_n24"], "--engine-s-base", eng["S_base"], "--engine-d-s", eng["D_S"]]
    assert rp.main(argv) == 0


def test_end_to_end_synthetic():
    root = tempfile.mkdtemp()
    S = make_synth(root, seed=7, n=6000, edge=1.0)
    out = os.path.join(root, "out")
    _run_main(root, S, out, threads=1, nnull=30)
    res = json.load(open(os.path.join(out, "tables.json"), encoding="utf-8"))
    md = open(os.path.join(out, "report.md"), encoding="utf-8").read()
    # состав
    assert sorted(res["lines"]) == sorted(["L_base", "L_n24", "S_base", "D_S"] + ["%s|t%g" % (s, t) for s in ("long", "short") for t in (0, 0.3, 0.6)])
    assert len(res["cells"]) == 12 and len([k for k in res["cells"] if k.endswith("|primary")]) == 6 and len(res["null"]) == 6 and len(res["placebo"]) == 6
    assert all(len(v["daily"]) == 105 for v in res["lines"].values())
    # модель, θ = 0 лонг: независимый счёт на чистом Python по сырым данным
    for side in ("long", "short"):
        d = S["data"][side].merge(S["pred"][side], on=["win", "row_id"])
        d = d[(d.meta_abs_ms >= rp.START_MS) & (d.meta_abs_ms < rp.END_MS) & d.y_has]
        names = sorted(set(S["data"]["long"][S["data"]["long"].meta_abs_ms >= rp.START_MS].sym) | set(S["data"]["short"][S["data"]["short"].meta_abs_ms >= rp.START_MS].sym) | {"ZZZUSDT"})
        sid = {x: i for i, x in enumerate(names)}
        prev_n = None
        for th in rp.THETAS:
            s = d[d.s_hat >= th]
            acc = pf.replay(s.meta_abs_ms.to_numpy(), s.meta_dist_e8.to_numpy(), s.sym.map(sid).to_numpy(), s.meta_level_px_e8.to_numpy(), s.y_exit_ms.to_numpy(), (s.y_stop == 1.0).to_numpy())
            sm, nn = in_range_sum(s.y_exit_ms.to_numpy()[acc], s.y_net.to_numpy()[acc])
            line = res["lines"]["%s|t%g" % (side, th)]
            assert abs(line["total"]["sum"] - sm) < 1e-6 and line["total"]["n"] == nn, (side, th, line["total"]["sum"], sm, line["total"]["n"], nn)
            assert line["selected_rows"] == len(s) and (prev_n is None or line["selected_rows"] <= prev_n)
            prev_n = line["selected_rows"]
            assert abs(sum(line["daily"]) - sm) < 1e-6
    # базы: независимая сборка двойников + справка против «движка»
    for b in rp.BASES:
        e = expected_base(S, b)
        sm, nn = in_range_sum(e.exit_abs.to_numpy(), e.net.to_numpy())
        assert abs(res["lines"][b]["total"]["sum"] - sm) < 1e-6 and res["lines"][b]["total"]["n"] == nn, b
        assert "ZZZUSDT" not in set(e.sym)                                                   # мусорные сделки вне зачётного диапазона окна не попали
        rc = res["refcheck"][b]
        assert rc["replica_trades"] == len(e)
        if b == "L_n24":
            assert rc["only_replica"] == 1 and rc["only_engine"] == 0 and rc["common"] == len(e) - 1 and not (rc["d_sum"] == 0)
        else:
            assert rc["only_replica"] == 0 and rc["only_engine"] == 0 and abs(rc["d_sum"]) < 1e-6 and rc["within_g_pf_tolerance"]
    assert res["n24_join"]["no_data_row"] == 0 and res["n24_join"]["passed"] > 0 and res["n24_join"]["ambiguous_rows_treated_as_fail"] == 0
    # ячейки: Холм, MDE, вердикт — согласованы с функциями
    prim = {k: v for k, v in res["cells"].items() if k.endswith("|primary")}
    ps = [prim["%s|t%g|primary" % (s, t)]["p"] for s in ("long", "short") for t in (0, 0.3, 0.6)]
    h = rp.holm(ps)
    for i, k in enumerate(["%s|t%g|primary" % (s, t) for s in ("long", "short") for t in (0, 0.3, 0.6)]):
        c = prim[k]
        assert c["holm_rank"] == h[i]["rank"] and abs(c["holm_step"] - h[i]["step"]) < 1e-15 and c["holm_pass"] == h[i]["passed"]
        assert abs(c["mde"] - rp.mde(c["sd_boot"], c["holm_step"])) < 1e-9
        assert c["verdict"] in (rp.V_ADD, rp.V_NO, rp.V_UNCLEAR)
        v, _ = rp.verdict(c["holm_pass"], c["sum_model"], c["sum_base"], c["sharpe_model"], c["sharpe_base"], c["month_dsum"], c["model_months_n"], c["dsum"], c["dsum_no2days"], c["dsum_no2coins"])
        assert v == c["verdict"]
        assert abs(c["dsum"] - (c["sum_model"] - c["sum_base"])) < 1e-6 and abs(sum(c["month_dsum"]) - c["dsum"]) < 1e-6
        assert c["dsr"]["trials"] == 34
    assert sorted(v["holm_rank"] for v in prim.values()) == [1, 2, 3, 4, 5, 6]
    for k, c in res["cells"].items():
        if k.endswith("|ref"):
            assert c["holm_rank"] is None and abs(c["holm_step"] - 0.05 / 6) < 1e-15
    # известное преимущество: у лонга и шорта θ = 0 модель превосходит базу своей стороны и случайный набор
    for side in ("long", "short"):
        c = res["cells"]["%s|t0|primary" % side]
        assert c["dsum"] > 0, (side, c["dsum"])
        assert res["null"]["%s|t0" % side]["model_percentile"] > 90.0
    # плацебо: s_plc не несёт информации — доля строк ≥ θ около доли нормали (θ = 0 → ~ 50 %)
    pl = res["placebo"]["long|t0"]["plc"]["months"]
    assert all(0.4 < m["share_rows"] < 0.6 for m in pl)
    # отчёт: R:R первым, §В-169 и правила владельца
    head = md.split("\n")[:8]
    assert head[0].startswith("# TK-671") and "R:R" in head[2] and "R:R" in head[3]
    assert "28 + 6 = 34" in md and "найдено на этих данных" in md
    for bad in ("бп", " bp", "bps", "медиан", "median"):
        assert bad not in md, bad
    for need in ("перехай", "Холм", "MDE", "Нулевая модель", "Плацебо", "Справка"):
        assert need.lower() in md.lower(), need
    assert "NaN" not in open(os.path.join(out, "tables.json"), encoding="utf-8").read()
    shutil.rmtree(root, ignore_errors=True)


def test_end_to_end_deterministic_across_threads():
    root = tempfile.mkdtemp()
    S = make_synth(root, seed=9, n=2500, edge=0.5)
    o1, o2 = os.path.join(root, "o1"), os.path.join(root, "o2")
    _run_main(root, S, o1, threads=1, nnull=15, nboot=500)
    _run_main(root, S, o2, threads=THREADS, nnull=15, nboot=500)             # THREADS=1 — повторяемость; TK671_TEST_THREADS=2 — равенство 1 и 2 потоков
    a = json.load(open(os.path.join(o1, "tables.json"), encoding="utf-8"))
    b = json.load(open(os.path.join(o2, "tables.json"), encoding="utf-8"))
    for x in (a, b):
        x["meta"].pop("elapsed_s")
    assert a == b
    assert open(os.path.join(o1, "report.md"), encoding="utf-8").read() == open(os.path.join(o2, "report.md"), encoding="utf-8").read()
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    fails = 0
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for k, v in tests:
        try:
            v()
            print("ok  ", k)
        except BaseException:
            fails += 1
            print("FAIL", k)
            traceback.print_exc()
    print("%d тестов, провалов %d" % (len(tests), fails))
    sys.exit(1 if fails else 0)
