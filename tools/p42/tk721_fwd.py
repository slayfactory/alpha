#!/usr/bin/env python3
"""TK-721 (PS-1043 §7 + PS-1044 п. 6; В-185 «по вчера»): форвард ненастроенной базы B лонга — 9 членов TK-718 (`BL-l{3,7,14}-tf{30,60,240}`, cid 7180006…7180014), без размера. Шорт-форвард TK-720 не меняется; общие функции — из `tk720_fwd.py`.

  python3 tk721_fwd.py freeze --out docs/research/freeze/tk721-long-frozen.json   # srv: члены, движок, окно, ожидание SEL + окна A / M / FJ (L-456), критерий остановки из бутстрепа SEL (блок 21, 10 000, зерно 719)
  python3 tk721_fwd.py gate-l1                                                    # srv: сделки лонга wa / wb / wc (`tk721l-sel-A`, tk700_mass) → суточные ряды членов = `tk718_ser` SEL лонга (|Δ| ≤ 1e-9)
  python3 tk721_fwd.py gate-l2 [--wins wa,wb,wc] [--freeze …]                     # srv: тот же вызов движка, что у раннера (`breakout tick`, P42_SIDES=long), на деревьях окон SEL → сделки равны `tk721l-sel-A` (tk700_mass; PS-1045: вместо n1 — сборки n1-long нет)
  python3 tk721_fwd.py run --through D --freeze-sha256 <hex> [--dl … --rest …]    # srv: дерево T0 25.10 … D → convert → движок лонга (+ шорт для ворот L3) → БД `tk721.sqlite` (`tk721l_*`) → отчёт
  python3 tk721_fwd.py report --rid <id>                                          # печать отчёта по сохранённому чтению (без движка)

Ряд B_d = среднее по 9 членам P&L суток входа = Σ (gross − fees) / 9 (простой P&L, ставка 500 $ на сделку члена; фандинг не входит), сутки — от 01.01.2026 (25.11 = сутки 328). Окно n2 = окно n1 (T0 25.10), продлённое до D: сутки 317…327 — до заморозки
(у лонга в старом движке N1 нет, ряд пишется справочно, не форвард), форвард — с суток 328. Критерий остановки — только по ряду с 25.11 (PS-1043 §7): остановка, если при n сутках просадка $ строго больше DD_q[n] или перехай строго больше GAP_q[n]
(таблицы по n = 1…60 — в файле заморозки; q = 95, при доле ложных остановок на путях SEL > 10 % — 99). Выводы до 20 суток не делаются."""
from __future__ import annotations

import argparse
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
import tk720_fwd as F  # noqa: E402  (underwater, sharpe, kill_tables, forward_table, build_tree, check_tree, check_tape, tkey … — без правки шорт-форварда)

ROOT = "/data/p42-tk721"
DB = ROOT + "/tk721.sqlite"
TRADES = ROOT + "/trades"                                           # `--keep-trades` tk700_mass: tk721l-sel-A/{wa,wb,wc}, tk721l-n1-A/n1; файлы `<cid>-trades.csv`
DB718 = F.DB718
MASS718 = F.MASS718
LCIDS = tuple(range(7180006, 7180015))                              # cid лонг-членов в `tk718core`
L0 = 7210300                                                        # cid тех же клеток в наборе `tk721l` (mass721.sqlite): L0 + k, k — по возрастанию LCIDS
MCIDS = tuple(L0 + k for k in range(len(LCIDS)))
FREEZE_VERSION = "tk721-long-frozen-1"
FREEZE_DEFAULT = os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk721-long-frozen.json")
WINDOWS = {"A": (31, 120), "M": (120, 181), "FJ": (31, 181), "SEL": (181, 317)}      # как `tk719_size.WINS`
L456 = {"A": dict(sum=-15.8, sharpe=-0.41, dd=80.6), "M": dict(sum=48.8, sharpe=1.64, dd=45.8), "FJ": dict(sum=33.1, sharpe=0.48, dd=80.6), "SEL": dict(sum=203.1, sharpe=4.24, dd=53.3)}   # L-456 / PS-1044 п. 6 (округлено)
TOL_GATE = 1e-9
TABLES = """
CREATE TABLE IF NOT EXISTS tk721l_run(rid TEXT PRIMARY KEY, through TEXT, freeze_sha TEXT, code_sha TEXT, engine_md5 TEXT, started TEXT, finished TEXT, rc INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS tk721l_day(rid TEXT, day INTEGER, date TEXT, n INTEGER, pnl REAL, forced INTEGER, PRIMARY KEY(rid, day));
CREATE TABLE IF NOT EXISTS tk721l_member_day(rid TEXT, cid INTEGER, day INTEGER, n INTEGER, pnl REAL, PRIMARY KEY(rid, cid, day));
CREATE TABLE IF NOT EXISTS tk721l_trade(rid TEXT, cid INTEGER, sym TEXT, entry_ms INTEGER, exit_ms INTEGER, gross REAL, fees REAL, funding REAL, kind TEXT, day INTEGER, PRIMARY KEY(rid, cid, sym, entry_ms));
"""


# ───────────────────────────── чистые функции (тесты — test_tk721.py) ─────────────────────────────

def window_stats(p, n, W):
    """Σ, Шарп, просадка, перехай ряда B по окнам W = {имя: (c, по)} (сутки от 01.01.2026); NaN в окне — ошибка."""
    import tk692_walk as TW
    out = {}
    for name, (lo, hi) in W.items():
        x = np.asarray(p[lo:hi], float)
        assert np.isfinite(x).all(), "ряд B неполон в окне %s" % name
        s = TW.series_stats(x, np.asarray(n[lo:hi]))
        out[name] = dict(days=int(s["days"]), trades=int(s["n"]), sum=float(s["sum"]), per_day=float(x.mean()), sharpe=float(s["sharpe"]), dd=float(s["dd"]), ddp=float(s["ddp"]),
                         gap_longest=int(s["gap_longest"]), gap_mean=float(s["gap_mean"]), gap_tail=int(s["gap_tail"]))
    return out


def daily_member(day, val, lo, hi):
    """(pnl[hi − lo], n[hi − lo]) по суткам входа lo…hi − 1 для сделок одного члена (day — сутки от 01.01.2026, val = gross − fees)."""
    day = np.asarray(day, np.int64)
    keep = (day >= lo) & (day < hi)
    return np.bincount(day[keep] - lo, weights=np.asarray(val, float)[keep], minlength=hi - lo)[:hi - lo], np.bincount(day[keep] - lo, minlength=hi - lo)[:hi - lo]


def compare_by_k(new, ref):
    """Сделки двух прогонов, члены сопоставлены по номеру k: new, ref — списки из 9 списков dict (CSV движка). (равны ли, только в новом, только в старом)."""
    only_new, only_ref = [], []
    for k, (a, b) in enumerate(zip(new, ref)):
        sa, sb = {F.tkey(t) for t in a}, {F.tkey(t) for t in b}
        only_new += [(k,) + x for x in sorted(sa - sb)]
        only_ref += [(k,) + x for x in sorted(sb - sa)]
    return (not only_new and not only_ref), only_new, only_ref


def gate_series(mine, ref, mine_n, ref_n, tol=TOL_GATE):
    """Ворота L1 для одного члена: max|Δ P&L| ≤ tol и числа сделок по суткам равны."""
    d = float(np.nanmax(np.abs(np.asarray(mine, float) - np.asarray(ref, float)))) if len(mine) else 0.0
    dn = int(np.abs(np.asarray(mine_n) - np.asarray(ref_n)).max()) if len(mine_n) else 0
    return d <= tol and dn == 0, d, dn


def load_freeze(path, want_sha=None):
    sha = F.sha256_file(path)
    if want_sha and sha != want_sha:
        raise SystemExit("СТОП: sha256 файла заморозки %s ≠ %s" % (sha, want_sha))
    fz = json.load(open(path, encoding="utf-8"))
    if fz.get("version") != FREEZE_VERSION:
        raise SystemExit("версия заморозки %r" % fz.get("version"))
    return fz, sha


# ───────────────────────────── заморозка ─────────────────────────────

def long_members(st):
    """9 лонг-членов (имя, ckey, cid) по возрастанию cid; cid = LCIDS."""
    names = st.members("long")
    assert len(names) == 9, names
    mem = sorted((st.plan["long", st.ckey("long", nm)]["cid"], nm, st.ckey("long", nm)) for nm in names)
    assert tuple(m[0] for m in mem) == LCIDS, [m[0] for m in mem]
    return mem


def cmd_freeze(a):
    import tk718_tests as T
    st = T.Store(DB718)
    mem = long_members(st)
    series = []
    for cid, nm, ck in mem:
        s = st.series_ck("long", ck)
        assert s is not None, nm
        series.append((nm, s))
    p, n = T.base_series(series)
    assert np.isfinite(p[31:317]).all(), "ряд B лонга на 31…316 неполон"
    stats = window_stats(p, n, WINDOWS)
    for w, ref in L456.items():                                     # сверка с L-456 (округление до 0,1 $ / 0,01 Шарпа)
        got = stats[w]
        assert abs(got["sum"] - ref["sum"]) < 0.06 and abs(got["sharpe"] - ref["sharpe"]) < 0.006 and abs(got["dd"] - ref["dd"]) < 0.06, (w, got, ref)
    sel = p[181:317]
    con = sqlite3.connect("file:%s?mode=ro" % MASS718, uri=True)
    members = []
    for cid, nm, ck in mem:
        row = con.execute("SELECT line, bin FROM cell WHERE cellset='tk718core' AND cid=?", (cid,)).fetchone()
        members.append(dict(name=nm, cid=int(cid), ckey=ck, line=row[0], bin_ref=row[1]))
    eng_md5 = hashlib.md5(open(F.ENGINE, "rb").read()).hexdigest()
    assert eng_md5 == F.ENGINE_MD5, eng_md5
    kill = F.kill_tables(sel)
    syms = open(F.UNIVERSE, encoding="utf-8").read().replace(",", " ").split()
    fz = dict(
        version=FREEZE_VERSION,
        task="TK-721",
        what="база B лонга без размера: среднее суточных P&L 9 членов (l_days ∈ {3, 7, 14} × tf ∈ {30, 60, 240}; якорь лонга TK-718 без фильтров, s=3 rs=20), ставка 500 $ на сделку члена, простой P&L (gross − fees), сутки по дате входа",
        members=members,
        engine=dict(path=F.ENGINE, md5=eng_md5, tick_store="/data/p42-tk599/tick-store", side="long", sides_env="P42_SIDES=long", trades_file="long-trades.csv",
                    note="движок TK-706 PGO m3 (подмена бинаря лонга c400af11…, как TK-718; сверка — ворота L1 / L2)"),
        costs=dict(fees="комиссии движка по тарифу владельца (п. 12) — строка движка; в ряд входят gross − fees", delay_ms=4, delay_keys="lat=4 latx=4", stake_usd=500, margin_usd=F.MARGIN, pnl="простой, фандинг не входит"),
        window=dict(name="n2", t0=F.T0, d0="2026-01-01", forward_from="2026-11-25", forward_day0=F.F0, n1_days=[F.N1_LO, F.N1_HI], universe_file=F.UNIVERSE, universe_sha256=F.sha256_file(F.UNIVERSE), universe_n=len(syms),
                    convert_bin=F.CONVERT_BIN, note="окно n1 (T0 25.10), продлённое до D; сутки 317…327 — до заморозки (у лонга в TK-718 N1 нет — ряд справочный), форвард — с 328"),
        expect=dict(sel_days=136, sel_sum=stats["SEL"]["sum"], sel_mean_per_day=stats["SEL"]["per_day"], sel_sharpe=stats["SEL"]["sharpe"], sel_dd=stats["SEL"]["dd"], sel_ddp=stats["SEL"]["ddp"], sel_gap_longest=stats["SEL"]["gap_longest"],
                    sel_gap_mean=stats["SEL"]["gap_mean"], sel_trades=stats["SEL"]["trades"], windows=stats, l456=L456,
                    note="SEL 01.07–13.11 — лучшее окно лонга, найдено на этих данных; на A (Feb–Apr) лонг минусовой (L-456, PS-1044 п. 6); окна A / M / FJ / SEL — из `tk718_ser`"),
        kill=dict(rule="остановка при n сутках форварда (n ≤ 60), если просадка $ ряда с 25.11 строго больше DD_q[n] или перехай (самый долгий зазор между новыми максимумами, сутки) строго больше GAP_q[n]; n > 60 — таблицы нет, нужен новый протокол",
                  source="бутстреп ряда B лонга на SEL (136 суток), блоки 21, 10 000 путей, зерно 719 (как у шорта TK-719 / TK-720), первые 60 суток пути", level_rule="доля путей, где (а) или (б) срабатывает хотя бы при одном n ≤ 60, на q95 > 10 % → q99 (PS-1041 п. 4)",
                  level=kill["level"], q95=kill["q95"], q99=kill["q99"], cone=kill["cone"], min_days_for_conclusion=20),
        metrics=dict(sharpe="среднее / σ суточного ряда (ddof = 1) × √365; при < 10 суток — «мало дней»", dd="макс. просадка суммы с начала форварда ($; % маржи 250 $)", gap="перехай — сутки между новыми максимумами (самый долгий / средний / хвост)"),
    )
    s = json.dumps(fz, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    print("заморозка записана: %s sha256 %s" % (a.out, F.sha256_file(a.out)))
    for w in WINDOWS:
        x = stats[w]
        print("%s: %d сут., Σ %.2f $, Шарп %.2f, просадка %.1f $, перехай %d, сделок %d" % (w, x["days"], x["sum"], x["sharpe"], x["dd"], x["gap_longest"], x["trades"]))
    print("уровень %d (доля ложных остановок q95 %.3f / q99 %.3f)" % (kill["level"], kill["q95"]["false_stop"], kill["q99"]["false_stop"]))
    return 0


# ───────────────────────────── движок ─────────────────────────────

def engine_run(root, out, threads, cap_gb, members, side="long", trades_dir=None, universe=None, t0=None):
    """Движок на 9 членах стороны: `breakout tick` на дереве `root` (cache, tree/aggtrades — или `trades_dir`), вселенная `universe` (по умолчанию n1), T0 `t0` (по умолчанию 25.10), P42_SIDES = сторона; одна партия, rc проверяется."""
    os.makedirs(out, exist_ok=True)
    vf = out + ".variants.txt"
    with open(vf, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join("k%d %s\n" % (m["cid"], m["line"]) for m in members))
    coins = ",".join(open(universe or F.UNIVERSE, encoding="utf-8").read().replace(",", " ").split())
    cmd = [F.ENGINE, "breakout", "tick", "--cache", root + "/cache", "--trades", trades_dir or root + "/tree/aggtrades", "--variants", vf, "--symbols", coins, "--out", out, "--threads", str(threads), "--cap-gb", str(cap_gb)] + F.TICK_STORE
    env = dict(os.environ)
    env.update({"P42_T0": t0 or F.T0, "P42_SIDES": side})
    with open(out + ".log", "w", encoding="utf-8") as lf:
        r = subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise SystemExit("СТОП: движок (%s) rc=%d, журнал %s" % (side, r.returncode, out + ".log"))


def load_members(out, members, side="long"):
    return [F.read_trades(os.path.join(out, "k%d" % m["cid"], "%s-trades.csv" % side)) for m in members]


def cmd_gate_l1(a):
    """L1: суточные ряды 9 членов из сделок `tk721l-sel-A` (wa / wb / wc, зачёт суток — `tk719_size.CREDIT['sel']`) = `tk718_ser` SEL лонга."""
    import tk718_tests as T
    import tk719_size as Z
    st = T.Store(DB718)
    mem = long_members(st)
    c = Z.load_group("sel", root=TRADES, run="tk721l-sel-A", cids=MCIDS)
    lo, hi = WINDOWS["SEL"]
    bad = 0
    for k, (cid, nm, ck) in enumerate(mem):
        pnl, n = st.series_ck("long", ck)
        m = c["cid"] == MCIDS[k]
        mine, cnt = daily_member(c["day"][m], c["val"][m], lo, hi)
        ok, d, dn = gate_series(mine, pnl[lo:hi], cnt, n[lo:hi])
        print("L1 %s (%d → %d): max|Δ P&L| %.2e, max|Δ n| %d, сделок %d / %d → %s" % (nm, cid, MCIDS[k], d, dn, int(cnt.sum()), int(n[lo:hi].sum()), "PASS" if ok else "FAIL"))
        bad += not ok
    print("gate-l1:", "PASS" if not bad else "FAIL (расхождений %d)" % bad)
    return 1 if bad else 0


def t0_date(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).date().isoformat()


def cmd_gate_l2(a):
    """L2 (PS-1045 вместо n1): тот же вызов движка, что у раннера форварда (`engine_run`: `breakout tick`, P42_SIDES=long, строки членов из заморозки), на деревьях окон wa / wb / wc (`/data/p42-tk621/<окно>`, вселенная c539, T0 окна) =
    прогон `tk700_mass` (`tk721l-sel-A`, `--dry`) на тех же 9 членах: сделки равны по (монета, вход, выход, gross, fees)."""
    fz, sha = load_freeze(a.freeze or FREEZE_DEFAULT)
    t0s = json.load(open(os.path.join(TRADES, "tk721l-sel-A", "t0.json"), encoding="utf-8"))
    bad = 0
    for win in a.wins.split(","):
        tree = "/data/p42-tk621/" + win
        out = os.path.join(ROOT, "gate-l2", win, "out")
        shutil.rmtree(out, ignore_errors=True)
        engine_run(tree, out, a.threads, a.cap_gb, fz["members"], trades_dir=tree + "/aggtrades", universe=F.C539, t0=t0_date(t0s[win]))
        new = load_members(out, fz["members"])
        ref = [F.read_trades(os.path.join(TRADES, "tk721l-sel-A", win, "%d-trades.csv" % cid)) for cid in MCIDS]
        ok, on, orf = compare_by_k(new, ref)
        print("L2 %s (T0 %s): сделок %d / %d, равны: %s, только в новом %d, только в старом %d" % (win, t0_date(t0s[win]), sum(len(v) for v in new), sum(len(v) for v in ref), ok, len(on), len(orf)))
        bad += not ok
    print("gate-l2:", "PASS" if not bad else "FAIL (окон с расхождением %d)" % bad)
    return 3 if bad else 0


# ───────────────────────────── прогон «по вчера» ─────────────────────────────

def next_rid(through, conn):
    base, n = "fw-%s" % through, 2
    rid = base
    while conn.execute("SELECT 1 FROM tk721l_run WHERE rid=?", (rid,)).fetchone() or os.path.exists(os.path.join(ROOT, "fwd-long", rid)):
        rid, n = "%s-r%d" % (base, n), n + 1
    return rid


def series_of(trades_by_k, t0_ms, nd):
    cells, ns = [], []
    for k, cid in enumerate(LCIDS):
        p, n = F.trades_daily(trades_by_k[k], t0_ms, nd)
        cells.append((cid, p, n))
        ns.append(n)
    return cells, np.mean([c[1] for c in cells], axis=0), np.sum(ns, axis=0)


def short_gate(new_short, short_members):
    """Ворота L3 (дерево n2 не изменилось): сделки шорта TK-720 до 24.11 = TK-719 `tk719n-n1-A` (кроме закрытых обрезкой конца данных 24.11) — как «ворота n2» шорт-форварда."""
    ref = {cid: F.read_trades(os.path.join(F.N1_TRADES, "n1", "%d-trades.csv" % cid)) for cid in F.CIDS}
    t0_ms = int(datetime.datetime.fromisoformat(F.T0).replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    n1_end_ms = int((datetime.datetime(2026, 11, 24, tzinfo=datetime.timezone.utc) + datetime.timedelta(days=1)).timestamp() * 1000) - t0_ms
    gate = {}
    for cid, tr in zip((m["cid"] for m in short_members), new_short):
        old = {(t["sym"], int(t["entry_ms"])): F.tkey(t) for t in ref[cid]}
        nw = {(t["sym"], int(t["entry_ms"])): F.tkey(t) for t in tr if int(t["entry_ms"]) < n1_end_ms}
        diff = [k for k in set(old) | set(nw) if old.get(k) != nw.get(k)]
        forced_old = [k for k in diff if k in old and int(old[k][2]) >= n1_end_ms - 120_000]
        gate[cid] = (len(old), len(nw), len(diff), len(forced_old))
    return {c: g for c, g in gate.items() if g[2] != g[3]}, gate


def cmd_run(a):
    fz, sha = load_freeze(a.freeze or FREEZE_DEFAULT, a.freeze_sha256)
    fz720, _ = F.load_freeze(os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk720-frozen.json"), None)    # члены шорта — для ворот L3
    through = a.through
    hi = datetime.date.fromisoformat(through)
    if hi >= datetime.datetime.now(datetime.timezone.utc).date():
        raise SystemExit("СТОП: сутки %s ещё не закончились" % through)
    nd = (hi - F.D0).days + 1
    assert nd > F.F0, "в форварде нет суток"
    os.makedirs(ROOT, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=120)
    conn.executescript(TABLES)
    code_sha = F.sha256_file(os.path.abspath(__file__))
    rid = a.rid or next_rid(through, conn)
    root = os.path.join(ROOT, "fwd-long", rid)
    os.makedirs(root)
    started = time.strftime("%FT%TZ", time.gmtime())
    conn.execute("INSERT INTO tk721l_run VALUES(?,?,?,?,?,?,?,?,?)", (rid, through, sha, code_sha, fz["engine"]["md5"], started, None, None, "running"))
    conn.commit()
    t00 = time.time()
    say = lambda *x: print(time.strftime("%FT%TZ", time.gmtime()), rid, *x, flush=True)   # noqa: E731
    d0_ms = int(datetime.datetime(F.D0.year, F.D0.month, F.D0.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    try:
        days, syms, cnt, miss = F.build_tree(root, through, a.dl, a.rest)
        say("дерево: сутки %s…%s (%d), контрактов %d, минуток %d, сделок %d, нет файла %d" % (days[0], days[-1], len(days), len(syms), cnt["klines"], cnt["aggtrades"], len(miss)))
        have = F.check_tree(root, syms, days)
        say("ворота дерева: сутки %s есть у %d из %d контрактов" % (days[-1], have, len(syms)))
        env = dict(os.environ, P42_T0=F.T0)
        r = subprocess.run([F.CONVERT_BIN, "breakout", "convert", "--src", root + "/tree/klines", "--out", root + "/cache", "--threads", "4"], env=env, stdout=open(root + "/convert.log", "w"), stderr=subprocess.STDOUT)
        if r.returncode != 0:
            raise SystemExit("СТОП: convert rc=%d, журнал %s/convert.log" % (r.returncode, root))
        say("convert готов за %.0f с" % (time.time() - t00))
        out, out_s = os.path.join(root, "out"), os.path.join(root, "out-short")
        t1 = time.time()
        engine_run(root, out, a.threads, a.cap_gb, fz["members"], "long")
        say("движок лонга готов за %.0f с" % (time.time() - t1))
        t1 = time.time()
        engine_run(root, out_s, a.threads, a.cap_gb, fz720["members"], "short")
        say("движок шорта (ворота L3) готов за %.0f с" % (time.time() - t1))
        new = load_members(out, fz["members"], "long")
        t0_ms, last_ms, bad = F.check_tape(dict(enumerate(new)), through)
        if bad:
            raise SystemExit("СТОП: G-tape — сделок лонга с выходом за концом данных: %d, например %s" % (len(bad), bad[:3]))
        new_s = load_members(out_s, fz720["members"], "short")
        gate_bad, gate = short_gate(new_s, fz720["members"])
        say("ворота L3 (шорт на дереве n2 до 24.11 против TK-719): %s" % ("PASS" if not gate_bad else "FAIL %s" % gate_bad))
        if gate_bad:
            raise SystemExit("СТОП: ворота L3 — сделки шорта до 24.11 не равны TK-719: %s" % gate_bad)
        cells, pb, nb = series_of(new, t0_ms, nd)
        forced = np.zeros(nd, np.int64)
        for k in range(len(LCIDS)):
            for t in new[k]:
                if int(t["exit_ms"]) + t0_ms >= last_ms - 120_000:
                    forced[(t0_ms - d0_ms) // F.DAY_MS + int(t["entry_ms"]) // F.DAY_MS] += 1
        conn.execute("DELETE FROM tk721l_day WHERE rid=?", (rid,))
        for d in range(F.N1_LO, nd):
            conn.execute("INSERT INTO tk721l_day VALUES(?,?,?,?,?,?)", (rid, d, F.day_date(d), int(nb[d]), float(pb[d]), int(forced[d])))
        for cid, p, n in cells:
            conn.executemany("INSERT INTO tk721l_member_day VALUES(?,?,?,?,?)", [(rid, cid, d, int(n[d]), float(p[d])) for d in range(F.N1_LO, nd)])
        for k, cid in enumerate(LCIDS):
            for t in new[k]:
                d = (t0_ms - d0_ms) // F.DAY_MS + int(t["entry_ms"]) // F.DAY_MS
                if d >= F.N1_LO:
                    conn.execute("INSERT OR REPLACE INTO tk721l_trade VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, cid, t["sym"], int(t["entry_ms"]), int(t["exit_ms"]), float(t["gross"]), float(t["fees"]), float(t["funding"]), t["kind"], d))
        conn.execute("UPDATE tk721l_run SET finished=?, rc=0, note=? WHERE rid=?", (time.strftime("%FT%TZ", time.gmtime()), "L3 PASS; движок лонга и шорта rc 0; G-tape 0", rid))
        conn.commit()
        say("ряды и сделки записаны в %s (rid %s), %.0f с" % (DB, rid, time.time() - t00))
    except BaseException as e:
        conn.execute("UPDATE tk721l_run SET finished=?, rc=?, note=? WHERE rid=?", (time.strftime("%FT%TZ", time.gmtime()), 1, repr(e)[:300], rid))
        conn.commit()
        raise
    return report(conn, rid, fz, sha, out_path=os.path.join(ROOT, "report-long-%s.md" % rid))


# ───────────────────────────── отчёт ─────────────────────────────

def report(conn, rid, fz, sha, out_path=None):
    run = conn.execute("SELECT through, freeze_sha, code_sha, engine_md5, rc, note FROM tk721l_run WHERE rid=?", (rid,)).fetchone()
    days = conn.execute("SELECT day, date, n, pnl, forced FROM tk721l_day WHERE rid=? ORDER BY day", (rid,)).fetchall()
    fwd = [r for r in days if r[0] >= F.F0]
    pre = [r for r in days if r[0] < F.F0]
    per_day = fz["expect"]["sel_mean_per_day"]
    pnl = np.array([r[3] for r in fwd])
    cnt = np.array([r[2] for r in fwd])
    rows = F.forward_table(pnl, cnt, fz["kill"], per_day)
    ex = fz["expect"]
    wl = "; ".join("%s %+.1f $ (Шарп %.2f, просадка %.1f $)" % (w, ex["windows"][w]["sum"], ex["windows"][w]["sharpe"], ex["windows"][w]["dd"]) for w in ("A", "M", "FJ", "SEL"))
    L = ["# TK-721 — форвард базы B лонга, чтение %s (до %s включительно)" % (rid, run[0]), "",
         "Заморозка: sha256 `%s`; код `%s`; движок md5 `%s`; rc %s (%s). Ряд B = среднее 9 членов, 500 $ на сделку члена, простой P&L; сутки входа; ожидание — SEL (01.07–13.11): %.3f $/сут (Σ %.1f $ за %d сут., Шарп %.2f, просадка %.1f $). "
         "SEL — лучшее окно лонга, найдено на этих данных; на A лонг минусовой. Окна «по рядам»: %s." % (run[1], run[2][:12], run[3], run[4], run[5], per_day, ex["sel_sum"], ex["sel_days"], ex["sel_sharpe"], ex["sel_dd"], wl), ""]
    if pre:
        s1 = np.array([r[3] for r in pre])
        L += ["До заморозки (14–24.11, не форвард, справочно): %d сут., %d сделок, Σ %.2f $." % (len(pre), sum(r[2] for r in pre), s1.sum()), ""]
    L += ["| сутки | сделок | P&L сутки, $ | Σ нарастающим, $ | ожид. Σ (SEL), $ | просадка $ (%% маржи) | перехай (макс / средн / хвост) | Шарп | остановка (DD / перехай; q%d) |" % fz["kill"]["level"], "|---|---|---|---|---|---|---|---|---|"]
    for r, d in zip(rows, fwd):
        k = r["kill"]
        ks = "нет таблицы" if k["stop"] is None else ("СТОП" if k["stop"] else "нет") + " (%.1f / %.1f; %d / %.0f)" % (k["dd"], k["dd_q"], k["gap"], k["gap_q"])
        sh = "%.2f" % r["sharpe"] if r["sharpe"] is not None and not math.isnan(r["sharpe"]) else "мало дней"
        gm = "—" if math.isnan(r["gap_mean"]) else "%.1f" % r["gap_mean"]
        L.append("| %s%s | %d | %+.2f | %+.2f | %+.2f | %s | %d / %s / %d | %s | %s |" % (r["date"], " (обрезка конца: %d сд.)" % d[4] if d[4] else "", r["n"], r["pnl"], r["cum"], r["expect"], F.fmt_dd(r["dd"]), r["gap"], gm, r["tail"], sh, ks))
    if rows:
        last = rows[-1]
        n = len(rows)
        cone = fz["kill"]["cone"]
        cone_s = " (конус SEL при n = %d: Σ q05 %.1f / q50 %.1f / q95 %.1f $)" % (n, cone["q05"][n - 1], cone["q50"][n - 1], cone["q95"][n - 1]) if n <= F.KILL_N else ""
        L += ["", "Итого с 25.11: %d сут., %d сделок, Σ %+.2f $ против ожидания %+.2f $%s; просадка %s; перехай %d / %.1f / %d сут.; Шарп %s. Выводы до 20 суток не делаются (суток: %d)." % (
            n, last["trades"], last["cum"], last["expect"], cone_s, F.fmt_dd(last["dd"]), last["gap"], 0.0 if math.isnan(last["gap_mean"]) else last["gap_mean"], last["tail"], "%.2f" % last["sharpe"] if last["sharpe"] is not None else "мало дней", n)]
    txt = "\n".join(L) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
    print(txt)
    return 0


def cmd_report(a):
    fz, sha = load_freeze(a.freeze or FREEZE_DEFAULT)
    conn = sqlite3.connect(DB, timeout=120)
    return report(conn, a.rid, fz, sha, out_path=os.path.join(ROOT, "report-long-%s.md" % a.rid))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("freeze")
    f.add_argument("--out", required=True)
    g1 = sp.add_parser("gate-l1")
    g2 = sp.add_parser("gate-l2")
    r = sp.add_parser("run")
    r.add_argument("--through", required=True)
    r.add_argument("--freeze-sha256", required=True)
    r.add_argument("--dl", default="/data/p42-tk616/fwd/dl")
    r.add_argument("--rest", default="/data/p42-tk616/fwd/rest")
    r.add_argument("--rid", default="")
    p = sp.add_parser("report")
    p.add_argument("--rid", required=True)
    g2.add_argument("--wins", default="wa,wb,wc")
    for x in (g2, r, p):
        x.add_argument("--freeze", default="")
    for x in (g2, r):
        x.add_argument("--threads", type=int, default=7)
        x.add_argument("--cap-gb", type=int, default=12)
    a = ap.parse_args(argv)
    return {"freeze": cmd_freeze, "gate-l1": cmd_gate_l1, "gate-l2": cmd_gate_l2, "run": cmd_run, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
