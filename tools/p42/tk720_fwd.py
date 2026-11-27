#!/usr/bin/env python3
"""TK-720 (В-185 «по вчера»; слова владельца 25.11: «ну по вчера скачай допрогони»): форвард ненастроенной базы B шорта — 9 членов TK-718 (`BS-l{3,7,14}-tf{30,60,240}`, cid 7180015…7180023), без размера (TK-719, L-460).

  python3 tk720_fwd.py freeze   --out docs/research/freeze/tk720-frozen.json     # srv: определение B, движок, окно, ожидание и критерий остановки из бутстрепа SEL (блок 21, 10 000, зерно 719); пишет файл и его sha256
  python3 tk720_fwd.py gate-n1                                                    # srv: те же 9 клеток тем же раннером на готовом дереве n1 (14–24.11) — сделки равны сделкам TK-719 (`tk719n-n1-A`)
  python3 tk720_fwd.py run --through D --freeze-sha256 <hex> [--dl … --rest …]    # srv: дерево T0 25.10 … D → convert → движок → БД `tk720.sqlite` → отчёт; ряд сравнивается с N1 и с заморозкой
  python3 tk720_fwd.py report --rid <id>                                          # печать отчёта по сохранённому чтению (без движка)

Один ряд: B_d = среднее по 9 членам P&L суток входа = Σ (gross − fees) / 9 (простой P&L, ставка 500 $ на сделку члена; фандинг не входит). Сутки — от 01.01.2026 (25.11 = сутки 328). Окно `n2` = окно n1 (T0 25.10), продлённое до D:
сутки 317…327 повторяют N1 (ворота `gate-n2`), форвард — с суток 328. Критерий остановки — только по ряду с 25.11 (PS-1040 §8 в части (б), PS-1041 п. 4): остановка, если при n сутках просадка $ строго больше DD_q[n] или перехай строго больше GAP_q[n]
(таблицы по n = 1…60 — в файле заморозки; q = 95, при доле ложных остановок на путях SEL > 10 % — 99). Выводы до 20 суток не делаются."""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ROOT = "/data/p42-tk720"
DB = ROOT + "/tk720.sqlite"
DB718 = "/data/p42-tk718/tk718.sqlite"
MASS718 = "/data/p42-tk718/mass718.sqlite"
UNIVERSE = "/data/p42-tk700/universe/n1.txt"                       # c539 ∩ список TK-710 на 24.11 (522 контракта), как у окна n1
C539 = "/data/p42-tk621/universe/symbols-crypto.txt"
ENGINE = "/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45"
ENGINE_MD5 = "06367f45fad0d7c5c1031d82dfa03615"
CONVERT_BIN = "/data/p42-tk616/alpha-mk.396989e9"                  # тот же бинарь, что делал кэш деревьев TK-710 / n1
TICK_STORE = ["--tick-store", "/data/p42-tk599/tick-store", "--tick-store-cap-gb", "3", "--tick-store-min-free-gb", "5"]
N1_TREE = "/data/p42-tk700/n1"                                      # готовое дерево n1 (T0 25.10, до 24.11): cache, tree/{klines,aggtrades}
N1_TRADES = "/data/p42-tk719/trades/tk719n-n1-A"                    # сделки движка на 9 членах (TK-719), окно n1
T0 = "2026-10-25"
D0 = datetime.date(2026, 1, 1)
F0 = 328                                                            # 25.11.2026 — первые сутки форварда
N1_LO, N1_HI = 317, 327                                             # 14.11 … 24.11
CIDS = tuple(range(7180015, 7180024))
MARGIN = 250.0
EXPECT_PER_DAY_KEY = "sel_mean_per_day"
SEED, NBOOT, BLOCK, KILL_N, MIN_DAYS = 719, 10000, 21, 60, 10
EPS = 1e-9
FREEZE_VERSION = "tk720-frozen-1"
DAY_MS = 86_400_000
TABLES = """
CREATE TABLE IF NOT EXISTS tk720_run(rid TEXT PRIMARY KEY, through TEXT, freeze_sha TEXT, code_sha TEXT, engine_md5 TEXT, started TEXT, finished TEXT, rc INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS tk720_day(rid TEXT, day INTEGER, date TEXT, n INTEGER, pnl REAL, forced INTEGER, PRIMARY KEY(rid, day));
CREATE TABLE IF NOT EXISTS tk720_member_day(rid TEXT, cid INTEGER, day INTEGER, n INTEGER, pnl REAL, PRIMARY KEY(rid, cid, day));
CREATE TABLE IF NOT EXISTS tk720_trade(rid TEXT, cid INTEGER, sym TEXT, entry_ms INTEGER, exit_ms INTEGER, gross REAL, fees REAL, funding REAL, kind TEXT, day INTEGER, PRIMARY KEY(rid, cid, sym, entry_ms));
CREATE TABLE IF NOT EXISTS tk720_row(rid TEXT, stat TEXT, value REAL, txt TEXT, PRIMARY KEY(rid, stat));
"""


# ───────────────────────────── чистые функции (тесты — test_tk720.py) ─────────────────────────────

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def underwater(x):
    """Просадка $ и перехай суточного ряда — как `tk692_walk.underwater` / `tk663_shape.uw` (самый долгий / средний зазор между новыми максимумами, хвост)."""
    x = np.asarray(x, float)
    n = len(x)
    if n == 0:
        return dict(maxdd=0.0, gap_longest=0, gap_mean=float("nan"), tail=0)
    cum = np.cumsum(x)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    prev = np.concatenate([[0.0], peak[:-1]])
    hi = np.nonzero(cum > prev + EPS)[0]
    pts = np.concatenate([[-1], hi]).astype(int)
    gaps = np.diff(pts)
    tail = n - 1 - int(pts[-1])
    return dict(maxdd=float((peak - cum).max()), gap_longest=int(max(list(gaps) + [tail])), gap_mean=float(gaps.mean()) if len(gaps) else float("nan"), tail=tail)


def sharpe(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return float("nan")
    sd = float(np.std(x, ddof=1))
    return float(x.mean() / sd * math.sqrt(365)) if sd > 0 else float("nan")


def running_dd_gap(paths):
    """По путям (P, N): макс. просадка $ и самый долгий перехай по первым n сутками, n = 1…N — векторно (те же определения, что `underwater` у префикса пути)."""
    p = np.asarray(paths, float)
    cum = np.cumsum(p, axis=1)
    peak = np.maximum(np.maximum.accumulate(cum, axis=1), 0.0)
    dd = np.maximum.accumulate(peak - cum, axis=1)
    prev = np.concatenate([np.zeros((p.shape[0], 1)), peak[:, :-1]], axis=1)
    hi = cum > prev + EPS
    t = np.arange(p.shape[1])[None, :]
    last = np.maximum.accumulate(np.where(hi, t, -1), axis=1)                  # индекс последнего нового максимума при t включительно
    last_prev = np.concatenate([np.full((p.shape[0], 1), -1), last[:, :-1]], axis=1)
    gap = np.maximum.accumulate(t - last_prev, axis=1)                         # завершённые зазоры и текущий хвост: g_t = t − last[t − 1]
    return dd, gap


def block_idx(n, length, boot, rng):
    """Скользящие блоки длины `length` (обрезка до n) — как `tk718_tests.block_idx`."""
    nb = int(math.ceil(n / length))
    starts = rng.integers(0, max(1, n - length + 1), (boot, nb))
    return (starts[:, :, None] + np.arange(length)[None, None, :]).reshape(boot, nb * length)[:, :n]


def kill_tables(sel, boot=NBOOT, length=BLOCK, seed=SEED, n_max=KILL_N):
    """Таблицы критерия остановки по бутстрепу ряда SEL (путь — `boot` блоков по `length` суток, первые `n_max` суток): DD_q[n], GAP_q[n], q = 95 / 99; доля путей, где (а) или (б) срабатывает хотя бы при одном n; конус Σ(n)."""
    sel = np.asarray(sel, float)
    idx = block_idx(len(sel), length, boot, np.random.default_rng([seed, 1]))     # те же пути, что у TK-719 (`boot_idx(136, 1)`)
    paths = sel[idx[:, :n_max]]
    dd, gap = running_dd_gap(paths)
    out = {}
    for q in (95, 99):
        dq, gq = np.percentile(dd, q, axis=0), np.percentile(gap, q, axis=0)
        fire = ((dd > dq[None, :] + EPS) | (gap > gq[None, :] + EPS)).any(axis=1)    # строго больше: у перехая целые значения
        out["q%d" % q] = dict(dd=[round(float(x), 6) for x in dq], gap=[float(x) for x in gq], false_stop=float(fire.mean()))
    cs = np.cumsum(paths, axis=1)
    out["cone"] = {k: [round(float(x), 6) for x in np.percentile(cs, v, axis=0)] for k, v in (("q05", 5), ("q50", 50), ("q95", 95))}
    out["level"] = 99 if out["q95"]["false_stop"] > 0.10 else 95
    return out


def kill_flags(fwd, kill):
    """Остановка по ряду форварда (суточный P&L B с 25.11): n = len(fwd) ≤ 60; (а) DD > DD_q[n], (б) перехай > GAP_q[n]; q — уровень из заморозки. n > 60 — таблицы нет (None)."""
    n = len(fwd)
    if n == 0 or n > KILL_N:
        return dict(n=n, dd=None, gap=None, dd_q=None, gap_q=None, stop_dd=None, stop_gap=None, stop=None)
    u = underwater(fwd)
    t = kill["q%d" % kill["level"]]
    dq, gq = t["dd"][n - 1], t["gap"][n - 1]
    sd, sg = u["maxdd"] > dq + EPS, u["gap_longest"] > gq + EPS
    return dict(n=n, dd=u["maxdd"], gap=u["gap_longest"], dd_q=dq, gap_q=gq, stop_dd=bool(sd), stop_gap=bool(sg), stop=bool(sd or sg))


def trades_daily(trades, t0_ms, nd):
    """(pnl[nd], n[nd]) по суткам входа от 01.01.2026: P&L сделки = gross − fees; trades — список dict (entry_ms от t0_ms окна)."""
    d0_ms = int(datetime.datetime(D0.year, D0.month, D0.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    pnl, cnt = np.zeros(nd), np.zeros(nd, np.int64)
    for t in trades:
        d = (t0_ms - d0_ms) // DAY_MS + int(t["entry_ms"]) // DAY_MS
        if 0 <= d < nd:
            pnl[d] += float(t["gross"]) - float(t["fees"])
            cnt[d] += 1
    return pnl, cnt


def read_trades(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def day_date(d):
    return (D0 + datetime.timedelta(days=int(d))).isoformat()


def fmt_dd(x):
    return "%.1f $ (%.1f %%)" % (x, x / MARGIN * 100)


def forward_table(pnl_b, n_b, kill, per_day, first_day=F0):
    """Строки таблицы форварда по суткам и нарастающим итогом (пнл B, сделок, ожидание SEL, DD, перехай, Шарп, остановка)."""
    rows = []
    for i in range(len(pnl_b)):
        fw = pnl_b[:i + 1]
        u = underwater(fw)
        kf = kill_flags(fw, kill)
        rows.append(dict(day=first_day + i, date=day_date(first_day + i), n=int(n_b[i]), pnl=float(pnl_b[i]), cum=float(fw.sum()), trades=int(np.sum(n_b[:i + 1])), expect=per_day * (i + 1),
                         dd=u["maxdd"], gap=u["gap_longest"], gap_mean=u["gap_mean"], tail=u["tail"], sharpe=sharpe(fw) if i + 1 >= MIN_DAYS else None, kill=kf))
    return rows


# ───────────────────────────── заморозка ─────────────────────────────

def cmd_freeze(a):
    import tk718_tests as T
    st = T.Store(DB718)
    names = st.members("short")
    assert len(names) == 9, names
    mem = []
    for nm in names:
        s = st.series("short", nm)
        assert s is not None, nm
        mem.append((nm, s))
    p, n = T.base_series(mem)
    assert np.isfinite(p[181:328]).all(), "ряд B на SEL / N1 неполон"
    sel, n1 = p[181:317], p[317:328]
    con = sqlite3.connect("file:%s?mode=ro" % MASS718, uri=True)
    members = []
    for nm in names:
        ck = st.ckey("short", nm)
        cid = st.plan["short", ck]["cid"]
        row = con.execute("SELECT line, bin FROM cell WHERE cellset='tk719b' AND cid=?", (cid,)).fetchone()
        members.append(dict(name=nm, cid=int(cid), ckey=ck, line=row[0], bin_ref=row[1]))
    members.sort(key=lambda m: m["cid"])
    assert tuple(m["cid"] for m in members) == CIDS, [m["cid"] for m in members]
    eng_md5 = hashlib.md5(open(ENGINE, "rb").read()).hexdigest()
    assert eng_md5 == ENGINE_MD5, eng_md5
    import tk692_walk as W
    stx = W.series_stats(sel, n[181:317])
    kill = kill_tables(sel)
    syms = open(UNIVERSE, encoding="utf-8").read().replace(",", " ").split()
    fz = dict(
        version=FREEZE_VERSION,
        task="TK-720",
        what="база B шорта без размера: среднее суточных P&L 9 членов (l_days ∈ {3, 7, 14} × tf ∈ {30, 60, 240}; якорь шорта L-334 без фильтров), ставка 500 $ на сделку члена, простой P&L (gross − fees), сутки по дате входа",
        members=members,
        engine=dict(path=ENGINE, md5=eng_md5, tick_store="/data/p42-tk599/tick-store", note="движок TK-706 PGO m3 (как TK-718 / TK-719; результаты клеток побайтно равны исходному бинарю шорта TK-604)"),
        costs=dict(fees="комиссии движка по тарифу владельца (п. 12: фьючерс мейкер 1,4 / тейкер 3,5 bps, минус 10 % возврата) — строка движка, как у пула; в ряд входят gross − fees", delay_ms=4, delay_keys="lat=4 latx=4", stake_usd=500, margin_usd=MARGIN, pnl="простой (шорт: 1 − c1/c0), фандинг не входит (печатается отдельно)"),
        window=dict(name="n2", t0=T0, d0="2026-01-01", forward_from="2026-11-25", forward_day0=F0, n1_days=[N1_LO, N1_HI], universe_file=UNIVERSE, universe_sha256=sha256_file(UNIVERSE), universe_n=len(syms),
                    convert_bin=CONVERT_BIN, trees_old=["/data/p42-tk621/wc", "/data/p42-tk621/wb", "/data/p42-tk621/wa"], note="окно n1 (T0 25.10), продлённое до D; сутки 317…327 повторяют N1 (ворота), форвард — с 328; PS-1040 §8 'T0 = lo − 20' заменено на T0 n1, чтобы состояние движка и ряд N1 не менялись"),
        expect=dict(sel_days=136, sel_sum=float(sel.sum()), sel_mean_per_day=float(sel.mean()), sel_sharpe=float(stx["sharpe"]), sel_dd=float(stx["dd"]), sel_ddp=float(stx["ddp"]), sel_gap_longest=int(stx["gap_longest"]), sel_gap_mean=float(stx["gap_mean"]),
                    sel_trades=int(stx["n"]), n1_sum=float(n1.sum()), n1_days=11, note="SEL 01.07–13.11 (окно отбора линий; B не настраивалась); N1 14–24.11"),
        kill=dict(rule="остановка при n сутках форварда (n ≤ 60), если просадка $ ряда с 25.11 строго больше DD_q[n] или перехай (самый долгий зазор между новыми максимумами, сутки) строго больше GAP_q[n]; q = level; n > 60 — таблицы нет, нужен новый протокол",
                  source="бутстреп ряда B на SEL (136 суток), блоки 21, 10 000 путей, зерно 719 (те же пути, что TK-719), первые 60 суток пути", level_rule="доля путей, где (а) или (б) срабатывает хотя бы при одном n ≤ 60, на q95 > 10 % → q99 (PS-1041 п. 4)",
                  level=kill["level"], q95=kill["q95"], q99=kill["q99"], cone=kill["cone"], min_days_for_conclusion=20),
        metrics=dict(sharpe="среднее / σ суточного ряда (ddof = 1) × √365; при < 10 суток — «мало дней»", dd="макс. просадка суммы с начала форварда ($; % маржи 250 $)", gap="перехай — сутки между новыми максимумами (самый долгий / средний / хвост)"),
    )
    s = json.dumps(fz, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    print("заморозка записана: %s sha256 %s" % (a.out, sha256_file(a.out)))
    print("SEL: Σ %.2f $ (%.3f $/сут), Шарп %.2f, DD %.1f $, перехай %d; N1 Σ %.2f $; уровень %d (доля ложных остановок q95 %.3f / q99 %.3f)" % (
        sel.sum(), sel.mean(), stx["sharpe"], stx["dd"], stx["gap_longest"], n1.sum(), kill["level"], kill["q95"]["false_stop"], kill["q99"]["false_stop"]))
    return 0


# ───────────────────────────── движок ─────────────────────────────

def engine_run(root, hi, out, threads, cap_gb, members):
    """Движок на 9 членах: `breakout tick` на дереве `root` (cache, tree/aggtrades), T0 25.10, сторона S; один запуск (одна партия), rc проверяется."""
    os.makedirs(out, exist_ok=True)
    vf = out + ".variants.txt"
    with open(vf, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join("k%d %s\n" % (m["cid"], m["line"]) for m in members))
    coins = ",".join(open(UNIVERSE, encoding="utf-8").read().replace(",", " ").split())
    cmd = [ENGINE, "breakout", "tick", "--cache", root + "/cache", "--trades", root + "/tree/aggtrades", "--variants", vf, "--symbols", coins, "--out", out, "--threads", str(threads), "--cap-gb", str(cap_gb)] + TICK_STORE
    env = dict(os.environ)
    env.update({"P42_T0": T0, "P42_SIDES": "short"})
    with open(out + ".log", "w", encoding="utf-8") as lf:
        r = subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise SystemExit("СТОП: движок rc=%d, журнал %s" % (r.returncode, out + ".log"))


def load_members(out, members):
    return {m["cid"]: read_trades(os.path.join(out, "k%d" % m["cid"], "short-trades.csv")) for m in members}


def check_tape(trades_by_cid, hi_date):
    """Охрана конца данных: ни одна сделка не выходит позже конца суток hi (G-tape, как `tk700_mass.cell_window`); сделки с выходом в последнюю минуту данных — `forced` (закрыты обрезкой конца)."""
    t0_ms = int(datetime.datetime.fromisoformat(T0).replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    last_ms = int((datetime.datetime.fromisoformat(hi_date).replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(days=1)).timestamp() * 1000)
    bad = [(c, t["sym"], t["exit_ms"]) for c, tr in trades_by_cid.items() for t in tr if int(t["exit_ms"]) + t0_ms >= last_ms]
    return t0_ms, last_ms, bad


def series_of(trades_by_cid, t0_ms, nd):
    cells, ns = [], []
    for cid in CIDS:
        p, n = trades_daily(trades_by_cid[cid], t0_ms, nd)
        cells.append((cid, p, n))
        ns.append(n)
    pb = np.mean([c[1] for c in cells], axis=0)
    return cells, pb, np.sum(ns, axis=0)


def tkey(t):
    return (t["sym"], int(t["entry_ms"]), int(t["exit_ms"]), round(float(t["gross"]), 6), round(float(t["fees"]), 6))


def compare_trades(new, ref):
    """Сделки двух прогонов одного окна: (равны ли множества по (монета, вход, выход, gross, fees), только в новом, только в старом) по всем cid."""
    only_new, only_ref = [], []
    for cid in CIDS:
        a, b = {tkey(t) for t in new[cid]}, {tkey(t) for t in ref[cid]}
        only_new += [(cid,) + k for k in sorted(a - b)]
        only_ref += [(cid,) + k for k in sorted(b - a)]
    return (not only_new and not only_ref), only_new, only_ref


def load_freeze(path, want_sha=None):
    sha = sha256_file(path)
    if want_sha and sha != want_sha:
        raise SystemExit("СТОП: sha256 файла заморозки %s ≠ %s" % (sha, want_sha))
    fz = json.load(open(path, encoding="utf-8"))
    if fz.get("version") != FREEZE_VERSION:
        raise SystemExit("версия заморозки %r" % fz.get("version"))
    return fz, sha


def freeze_path(a):
    return a.freeze or os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk720-frozen.json")


def cmd_gate_n1(a):
    """Ворота раннера: 9 клеток тем же вызовом движка на готовом дереве n1 — сделки равны сделкам `tk719n-n1-A`."""
    fz, sha = load_freeze(freeze_path(a))
    out = os.path.join(ROOT, "gate-n1", "out")
    shutil.rmtree(out, ignore_errors=True)
    engine_run(N1_TREE, "2026-11-24", out, a.threads, a.cap_gb, fz["members"])
    new = load_members(out, fz["members"])
    ref = {cid: read_trades(os.path.join(N1_TRADES, "n1", "%d-trades.csv" % cid)) for cid in CIDS}
    ok, on, orf = compare_trades(new, ref)
    t0_ms, last_ms, bad = check_tape(new, "2026-11-24")
    print("gate-n1: сделок %d / %d, равны: %s, только в новом %d, только в старом %d, G-tape %d" % (sum(len(v) for v in new.values()), sum(len(v) for v in ref.values()), ok, len(on), len(orf), len(bad)))
    print("gate-n1:", "PASS" if ok and not bad else "FAIL")
    return 0 if ok and not bad else 3


# ───────────────────────────── прогон «по вчера» ─────────────────────────────

def build_tree(root, hi, dl, rest):
    import tk626_prep as pr
    syms = open(UNIVERSE, encoding="utf-8").read().replace(",", " ").split()
    days = pr.day_list(T0, datetime.date.fromisoformat(hi))
    wdir = os.path.join(root, "tree")
    kl, tr, fund = pr.sources(dl, rest)
    cnt, miss = pr.build_tree(wdir, syms, days, kl, tr, fund)
    with open(os.path.join(root, "nofile.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.writelines("\t".join(r) + "\n" for r in miss)
    return days, syms, cnt, miss


def check_tree(root, syms, days):
    """Ворота дерева: у каждого контракта есть каталоги, суток позже последней нет, последние сутки есть у ≥ 99 % контрактов."""
    last = days[-1]
    have = 0
    for s in syms:
        for sub in ("aggtrades", "klines"):
            if not os.path.isdir(os.path.join(root, "tree", sub, s)):
                raise SystemExit("СТОП: нет каталога %s/%s" % (sub, s))
        have += os.path.exists(os.path.join(root, "tree", "klines", s, last + ".csv")) and os.path.exists(os.path.join(root, "tree", "aggtrades", s, "%s-aggTrades-%s.zip" % (s, last)))
    if have * 100 < len(syms) * 99:
        raise SystemExit("СТОП: сутки %s есть у %d из %d контрактов (< 99 %%)" % (last, have, len(syms)))
    return have


def next_rid(through, conn):
    base, n = "fw-%s" % through, 2
    rid = base
    while conn.execute("SELECT 1 FROM tk720_run WHERE rid=?", (rid,)).fetchone() or os.path.exists(os.path.join(ROOT, "fwd", rid)):
        rid, n = "%s-r%d" % (base, n), n + 1
    return rid


def cmd_run(a):
    fz, sha = load_freeze(freeze_path(a), a.freeze_sha256)
    through = a.through
    hi = datetime.date.fromisoformat(through)
    if hi >= datetime.datetime.now(datetime.timezone.utc).date():
        raise SystemExit("СТОП: сутки %s ещё не закончились" % through)
    nd = (hi - D0).days + 1
    assert nd > F0, "в форварде нет суток"
    os.makedirs(ROOT, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=120)
    conn.executescript(TABLES)
    code_sha = sha256_file(os.path.abspath(__file__))
    rid = a.rid or next_rid(through, conn)
    root = os.path.join(ROOT, "fwd", rid)
    os.makedirs(root)
    started = time.strftime("%FT%TZ", time.gmtime())
    conn.execute("INSERT INTO tk720_run VALUES(?,?,?,?,?,?,?,?,?)", (rid, through, sha, code_sha, fz["engine"]["md5"], started, None, None, "running"))
    conn.commit()
    t00 = time.time()
    say = lambda *x: print(time.strftime("%FT%TZ", time.gmtime()), rid, *x, flush=True)   # noqa: E731
    try:
        days, syms, cnt, miss = build_tree(root, through, a.dl, a.rest)
        say("дерево: сутки %s…%s (%d), контрактов %d, минуток %d, сделок %d, нет файла %d" % (days[0], days[-1], len(days), len(syms), cnt["klines"], cnt["aggtrades"], len(miss)))
        have = check_tree(root, syms, days)
        say("ворота дерева: сутки %s есть у %d из %d контрактов" % (days[-1], have, len(syms)))
        env = dict(os.environ, P42_T0=T0)
        r = subprocess.run([CONVERT_BIN, "breakout", "convert", "--src", root + "/tree/klines", "--out", root + "/cache", "--threads", "4"], env=env, stdout=open(root + "/convert.log", "w"), stderr=subprocess.STDOUT)
        if r.returncode != 0:
            raise SystemExit("СТОП: convert rc=%d, журнал %s/convert.log" % (r.returncode, root))
        say("convert готов за %.0f с" % (time.time() - t00))
        out = os.path.join(root, "out")
        t1 = time.time()
        engine_run(root, through, out, a.threads, a.cap_gb, fz["members"])
        say("движок готов за %.0f с" % (time.time() - t1))
        new = load_members(out, fz["members"])
        t0_ms, last_ms, bad = check_tape(new, through)
        if bad:
            raise SystemExit("СТОП: G-tape — сделок с выходом за концом данных: %d, например %s" % (len(bad), bad[:3]))
        # ворота n2: все сделки N1 (≤ 24.11) = сделки TK-719 `tk719n-n1-A`, кроме закрытых обрезкой конца данных 24.11 (у них другой выход) — по множествам (монета, вход)
        ref = {cid: read_trades(os.path.join(N1_TRADES, "n1", "%d-trades.csv" % cid)) for cid in CIDS}
        n1_end_ms = int((datetime.datetime(2026, 11, 24, tzinfo=datetime.timezone.utc) + datetime.timedelta(days=1)).timestamp() * 1000) - t0_ms
        gate = {}
        for cid in CIDS:
            old = {(t["sym"], int(t["entry_ms"])): tkey(t) for t in ref[cid]}
            nw = {(t["sym"], int(t["entry_ms"])): tkey(t) for t in new[cid] if int(t["entry_ms"]) < n1_end_ms}
            # сделка старого прогона, закрытая обрезкой конца 24.11: выход в последние 2 мин данных n1 — допускается другой выход
            diff = [k for k in set(old) | set(nw) if old.get(k) != nw.get(k)]
            forced_old = [k for k in diff if k in old and int(old[k][2]) >= n1_end_ms - 120_000]
            gate[cid] = (len(old), len(nw), len(diff), len(forced_old))
        gate_bad = {c: g for c, g in gate.items() if g[2] != g[3]}
        say("ворота n2 (сделки до 24.11 против TK-719): %s" % ("PASS" if not gate_bad else "FAIL %s" % gate_bad))
        if gate_bad:
            raise SystemExit("СТОП: ворота n2 — сделки до 24.11 не равны TK-719: %s" % gate_bad)
        cells, pb, nb = series_of(new, t0_ms, nd)
        forced = np.zeros(nd, np.int64)
        for cid in CIDS:
            for t in new[cid]:
                if int(t["exit_ms"]) + t0_ms >= last_ms - 120_000:
                    d = (t0_ms - int(datetime.datetime(D0.year, D0.month, D0.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)) // DAY_MS + int(t["entry_ms"]) // DAY_MS
                    forced[d] += 1
        conn.execute("DELETE FROM tk720_day WHERE rid=?", (rid,))
        for d in range(nd):
            if d >= N1_LO:
                conn.execute("INSERT INTO tk720_day VALUES(?,?,?,?,?,?)", (rid, d, day_date(d), int(nb[d]), float(pb[d]), int(forced[d])))
        for cid, p, n in cells:
            conn.executemany("INSERT INTO tk720_member_day VALUES(?,?,?,?,?)", [(rid, cid, d, int(n[d]), float(p[d])) for d in range(N1_LO, nd)])
        for cid in CIDS:
            for t in new[cid]:
                d = (t0_ms - int(datetime.datetime(D0.year, D0.month, D0.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)) // DAY_MS + int(t["entry_ms"]) // DAY_MS
                if d >= N1_LO:
                    conn.execute("INSERT OR REPLACE INTO tk720_trade VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, cid, t["sym"], int(t["entry_ms"]), int(t["exit_ms"]), float(t["gross"]), float(t["fees"]), float(t["funding"]), t["kind"], d))
        conn.execute("UPDATE tk720_run SET finished=?, rc=0, note=? WHERE rid=?", (time.strftime("%FT%TZ", time.gmtime()), "gate n2 PASS; движок rc 0; G-tape 0", rid))
        conn.commit()
        say("ряды и сделки записаны в %s (rid %s), %.0f с" % (DB, rid, time.time() - t00))
    except BaseException as e:
        conn.execute("UPDATE tk720_run SET finished=?, rc=?, note=? WHERE rid=?", (time.strftime("%FT%TZ", time.gmtime()), 1, repr(e)[:300], rid))
        conn.commit()
        raise
    return report(conn, rid, fz, sha, out_path=os.path.join(ROOT, "report-%s.md" % rid))


# ───────────────────────────── отчёт ─────────────────────────────

def report(conn, rid, fz, sha, out_path=None):
    run = conn.execute("SELECT through, freeze_sha, code_sha, engine_md5, rc, note FROM tk720_run WHERE rid=?", (rid,)).fetchone()
    days = conn.execute("SELECT day, date, n, pnl, forced FROM tk720_day WHERE rid=? ORDER BY day", (rid,)).fetchall()
    fwd = [r for r in days if r[0] >= F0]
    n1 = [r for r in days if r[0] < F0]
    per_day = fz["expect"][EXPECT_PER_DAY_KEY]
    pnl = np.array([r[3] for r in fwd])
    cnt = np.array([r[2] for r in fwd])
    rows = forward_table(pnl, cnt, fz["kill"], per_day)
    L = ["# TK-720 — форвард базы B шорта, чтение %s (до %s включительно)" % (rid, run[0]), "",
         "Заморозка: sha256 `%s`; код `%s`; движок md5 `%s`; rc %s (%s). Ряд B = среднее 9 членов, 500 $ на сделку члена, простой P&L; сутки входа; ожидание — SEL (01.07–13.11): %.3f $/сут (Σ %.1f $ за %d сут., Шарп %.2f, просадка %.1f $)." % (
             run[1], run[2][:12], run[3], run[4], run[5], per_day, fz["expect"]["sel_sum"], fz["expect"]["sel_days"], fz["expect"]["sel_sharpe"], fz["expect"]["sel_dd"]), ""]
    if n1:
        s1 = np.array([r[3] for r in n1])
        L += ["До заморозки (N1, 14–24.11, не форвард): %d сут., %d сделок, Σ %.2f $." % (len(n1), sum(r[2] for r in n1), s1.sum()), ""]
    L += ["| сутки | сделок | P&L сутки, $ | Σ нарастающим, $ | ожид. Σ (SEL), $ | просадка $ (% маржи) | перехай (макс / средн / хвост) | Шарп | остановка (DD / перехай; q%d) |" % fz["kill"]["level"], "|---|---|---|---|---|---|---|---|---|"]
    for r, d in zip(rows, fwd):
        k = r["kill"]
        ks = "нет таблицы" if k["stop"] is None else ("СТОП" if k["stop"] else "нет") + " (%.1f / %.1f; %d / %.0f)" % (k["dd"], k["dd_q"], k["gap"], k["gap_q"])
        sh = "%.2f" % r["sharpe"] if r["sharpe"] is not None and not math.isnan(r["sharpe"]) else "мало дней"
        L.append("| %s%s | %d | %+.2f | %+.2f | %+.2f | %s | %d / %.1f / %d | %s | %s |" % (r["date"], " (обрезка конца: %d сд.)" % d[4] if d[4] else "", r["n"], r["pnl"], r["cum"], r["expect"], fmt_dd(r["dd"]), r["gap"], r["gap_mean"], r["tail"], sh, ks))
    if rows:
        last = rows[-1]
        n = len(rows)
        cone = fz["kill"]["cone"]
        cone_s = " (конус SEL при n = %d: Σ q05 %.1f / q50 %.1f / q95 %.1f $)" % (n, cone["q05"][n - 1], cone["q50"][n - 1], cone["q95"][n - 1]) if n <= KILL_N else ""
        L += ["", "Итого с 25.11: %d сут., %d сделок, Σ %+.2f $ против ожидания %+.2f $%s; просадка %s; перехай %d / %.1f / %d сут.; Шарп %s. Выводы до 20 суток не делаются (суток: %d)." % (
            n, last["trades"], last["cum"], last["expect"], cone_s, fmt_dd(last["dd"]), last["gap"], last["gap_mean"], last["tail"], "%.2f" % last["sharpe"] if last["sharpe"] is not None else "мало дней", n)]
    txt = "\n".join(L) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
    print(txt)
    return 0


def cmd_report(a):
    fz, sha = load_freeze(freeze_path(a))
    conn = sqlite3.connect(DB, timeout=120)
    return report(conn, a.rid, fz, sha, out_path=os.path.join(ROOT, "report-%s.md" % a.rid))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("freeze")
    f.add_argument("--out", required=True)
    g = sp.add_parser("gate-n1")
    r = sp.add_parser("run")
    r.add_argument("--through", required=True)
    r.add_argument("--freeze-sha256", required=True)
    r.add_argument("--dl", default="/data/p42-tk616/fwd/dl")
    r.add_argument("--rest", default="/data/p42-tk616/fwd/rest")
    r.add_argument("--rid", default="")
    p = sp.add_parser("report")
    p.add_argument("--rid", required=True)
    for x in (g, r, p):
        x.add_argument("--freeze", default="")
    for x in (g, r):
        x.add_argument("--threads", type=int, default=7)
        x.add_argument("--cap-gb", type=int, default=12)
    a = ap.parse_args(argv)
    return {"freeze": cmd_freeze, "gate-n1": cmd_gate_n1, "run": cmd_run, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
