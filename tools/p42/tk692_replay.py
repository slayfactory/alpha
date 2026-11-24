#!/usr/bin/env python3
"""TK-692 (PS-1007 §9): повтор движком выбранных клеток — R:R, убыток по цене стопа, доля утечки.

Движок и сделки — через `tk700_mass.py` (TK-700, замок на run-id повтора); `mass_day` прежних месяцев не меняется.

  /data/tk592/venv/bin/python tk692_replay.py replay --rid A-m01 --side long --runs h1-jan-A [--cellset all1] [--class A]
  /data/tk592/venv/bin/python tk692_replay.py rr     --rid A-m01 --side long --runs h1-jan-A

`replay`: пары (клетка, месяц), нужные выбору `rid` (торговавшие шаги × месяцы 0…месяц последних суток блока + клетки баз ряда), без уже посчитанных, одним запуском
`tk700_mass run` (набор `sel` — копия строк `cell`); ворота — по каждой паре Σ $, число сделок и P&L по суткам равны `mass_day` / `mass_cw` прежнего запуска месяца
(допуск 1e-7 $), иначе пара не записывается и код выхода 1. Сделки — в `replay.sqlite` (`rp_trade`).
`rr`: по сделкам повтора — `tk692_rr` в БД результатов: R:R = средний выигрыш ÷ |средний проигрыш| (по сделкам ряда, P&L = gross − fees, как `mass_day`); убыток по цене
стопа = ставка 500 $ × стоп клетки (ось `s`, %) / 100 + средняя комиссия сделок ряда, в % маржи 250 $; доля утечки = Σ P&L сделок окна отбора, закрытых не раньше границы шага,
÷ Σ окон отбора выбранных клеток (по торговавшим шагам).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
import time

import numpy as np

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("TK674_REBASE", "1")                                  # как run-mass.sh: до импорта tk674_axes
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk692_walk as W  # noqa: E402

TK700 = "/data/p42-tk700/code"
MASS_DB = "/data/p42-tk700/mass/mass.sqlite"
OUT_DB = "/data/p42-tk692/tk692.sqlite"
RP_DB = "/data/p42-tk692/replay.sqlite"
RP_DIR = "/data/p42-tk692/replay"
STAKE = 500.0
DAY_MS = 86_400_000
D0_MS = int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
TOL = 1e-7

RP_SCHEMA = """
CREATE TABLE IF NOT EXISTS rp_done(side TEXT, cellset TEXT, cid INTEGER, month INTEGER, run_id TEXT, PRIMARY KEY(side, cellset, cid, month)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS rp_trade(side TEXT, cellset TEXT, cid INTEGER, month INTEGER, sym TEXT, entry_day INTEGER, entry_ms INTEGER, exit_ms INTEGER, val REAL, fees REAL, net REAL, kind TEXT);
CREATE INDEX IF NOT EXISTS rp_trade_k ON rp_trade(side, cellset, cid, month);
CREATE TABLE IF NOT EXISTS rp_gate(run_id TEXT, side TEXT, cid INTEGER, month INTEGER, n_ref INTEGER, n_rp INTEGER, usd_ref REAL, usd_rp REAL, day_diff REAL, ok INTEGER, why TEXT,
                                   PRIMARY KEY(run_id, cid, month)) WITHOUT ROWID;
"""


# ───────────────────────────── чистые функции ─────────────────────────────

def needed_pairs(picks, base_cids, nm):
    """{(cid, месяц)}: торговавшие выборы (e > b, без пометки пропуска) × месяцы 0…месяц последних суток блока; клетки баз — все загруженные месяцы."""
    need = set()
    for cid, b, e, sk in picks:
        if cid is None or e <= b or sk:
            continue
        for m in range(min(W.month_of(e - 1), nm - 1) + 1):
            need.add((int(cid), m))
    for cid in base_cids:
        for m in range(nm):
            need.add((int(cid), m))
    return need


def stop_pct(ckey, line=""):
    """Стоп клетки, % цены: ось `s` ключа, иначе строки движка; нет — ошибка (значение не выдумывается)."""
    for src in (ckey, line):
        for t in src.split():
            if t.startswith("s="):
                return float(t[2:])
    raise ValueError("у клетки нет оси s: %r" % ckey)


def rr_of(vals):
    win = [v for v in vals if v > 0]
    loss = [v for v in vals if v < 0]
    if not win or not loss:
        return None
    return (sum(win) / len(win)) / abs(sum(loss) / len(loss))


def leak_usd(entry_day, exit_ms, val, w, boundary_ms):
    """Σ P&L сделок окна отбора (вход до суток w), закрытых не раньше границы."""
    m = (entry_day < w) & (exit_ms >= boundary_ms)
    return float(val[m].sum())


def series_stats_rr(tr, stops, cid_days):
    """tr: {cid: dict(day, val, fees)}; stops: {cid: стоп %}; cid_days: {cid: набор суток торговли} → (R:R, убыток по стопу $, n)."""
    vals, fees, stp = [], [], []
    for cid, days in cid_days.items():
        t = tr.get(cid)
        if t is None:
            continue
        m = np.isin(t["day"], np.fromiter(days, np.int64, len(days)))
        vals.append(t["val"][m])
        fees.append(t["fees"][m])
        stp.append(np.full(int(m.sum()), stops[cid]))
    if not vals:
        return None, None, 0
    v, f, s = np.concatenate(vals), np.concatenate(fees), np.concatenate(stp)
    if len(v) == 0:
        return None, None, 0
    return rr_of(list(v)), float(STAKE * s.mean() / 100.0 + f.mean()), int(len(v))


# ───────────────────────────── БД повтора ─────────────────────────────

def ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def rp_connect(path):
    con = sqlite3.connect(path)
    con.executescript(RP_SCHEMA)
    return con


def month_runs(ref, runs):
    out = {}
    for r in runs:
        row = ref.execute("SELECT wins FROM mass_run WHERE run_id=?", (r,)).fetchone()
        if row is None or row[0] not in W.MONTHS:
            raise SystemExit("запуск %s: нет или окно не m01…m06" % r)
        out[W.MONTHS.index(row[0])] = r
    if sorted(out) != list(range(len(out))):
        raise SystemExit("месяцы запусков не идут подряд с m01: %s" % sorted(out))
    return out


def base_cid_set(out, rid, side):
    s = set()
    for (ex,) in out.execute("SELECT extra FROM tk692_sum WHERE rid=? AND side=? AND series<>'select'", (rid, side)):
        c = json.loads(ex or "{}").get("cid")
        if c is not None:
            s.add(int(c))
    return s


def need_for(out, rid, side, nm):
    picks = out.execute("SELECT cid, b, e, skipped FROM tk692_pick WHERE rid=? AND side=?", (rid, side)).fetchall()
    return needed_pairs(picks, base_cid_set(out, rid, side), nm)


# ───────────────────────────── повтор движком ─────────────────────────────

def cmd_replay(a):
    runs = {}
    ref = ro(a.db)
    out = ro(a.out)
    runs = month_runs(ref, a.runs.split(","))
    nm = len(runs)
    need = need_for(out, a.rid, a.side, nm)
    rp = rp_connect(a.rp)
    done = {(c, m) for c, m in rp.execute("SELECT cid, month FROM rp_done WHERE side=? AND cellset=?", (a.side, a.cellset))}
    miss = need - done
    have = set()
    for m, r in runs.items():
        cs = sorted({c for c, mm_ in miss if mm_ == m})
        for i in range(0, len(cs), 500):
            ch = cs[i:i + 500]
            have |= {(c, m) for (c,) in ref.execute("SELECT cid FROM mass_cw WHERE run_id=? AND cid IN (%s)" % ",".join("?" * len(ch)), [r] + ch)}
    lost = sorted(miss - have)
    miss = sorted(miss & have)
    print("пар нужно %d, готово %d, к повтору %d, нет в mass_cw месяца %d %s" % (len(need), len(need & done), len(miss), len(lost), lost[:10]), flush=True)
    if not miss:
        return 0
    cids = sorted({c for c, _ in miss})
    wins = sorted({W.MONTHS[m] for _, m in miss})
    run_id = "tk692-rp-%s-%s-%s" % (a.side, a.rid, time.strftime("%m%d%H%M%S"))
    os.makedirs(RP_DIR, exist_ok=True)
    path = os.path.join(RP_DIR, run_id + ".sqlite")
    sys.path.insert(0, TK700)
    import tk700_mass as mm                                                 # noqa: E402  (до любого импорта tk604_stats: окна H1 — переменной среды)
    db = mm.connect(path)
    rows = []
    for i in range(0, len(cids), 500):
        ch = cids[i:i + 500]
        rows += ref.execute("SELECT cid, side, ckey, line, sig, filt, feats, ref_n, ref_usd, ref_daily, db_id, bin FROM cell WHERE cellset=? AND side=? AND cid IN (%s)" % ",".join("?" * len(ch)),
                            [a.cellset, a.side] + ch).fetchall()
    if len(rows) != len(cids):
        raise SystemExit("клеток в наборе %s: %d из %d" % (a.cellset, len(rows), len(cids)))
    db.execute("INSERT INTO cellset VALUES('sel', ?, ?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(dict(base=a.cellset, rid=a.rid, tk="TK-692"))))
    db.executemany("INSERT INTO cell VALUES('sel',?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    stops = {r[0]: stop_pct(r[2], r[3]) for r in rows}                      # проверка: у каждой клетки есть ось s до запуска движка
    print("повтор %s: клеток %d, окна %s, стопы %s" % (run_id, len(cids), ",".join(wins), sorted(set(stops.values()))), flush=True)

    got = []
    orig = mm.cell_window

    def hook(st, p8, w, side, vdir, filtered, strict_key=False):
        res = orig(st, p8, w, side, vdir, filtered, strict_key)
        eng = p8.ENG[mm.ho.SIDE_LETTER[side]]
        t = st.read_trades_csv(os.path.join(vdir, eng + "-trades.csv"))
        got.append((int(os.path.basename(vdir)[1:]), w.name, int(w.t0_ms), np.asarray(st.abs_day(w, t.entry_ms)), t))
        return res
    mm.cell_window = hook
    rc = mm.main(["run", "--db", path, "--run-id", run_id, "--cellset", "sel", "--wins", ",".join(wins), "--sides", a.side, "--class", a.cls, "--vmax", "800", "--sigmax", "26",
                  "--threads", str(a.threads), "--cap-gb", str(a.cap_gb)])
    if rc:
        raise SystemExit("движок: код %s" % rc)
    ok_all = True
    wanted = set(miss)
    for cid, win, t0_ms, dd, t in got:
        m = W.MONTHS.index(win)
        if (cid, m) not in wanted:
            continue
        r = runs[m]
        cw = db.execute("SELECT n, usd, errs FROM mass_cw WHERE run_id=? AND cid=? AND win=?", (run_id, cid, win)).fetchone()
        rf = ref.execute("SELECT n, usd FROM mass_cw WHERE run_id=? AND cid=? AND win=?", (r, cid, win)).fetchone()
        dr = {d: (p, n) for d, p, n in db.execute("SELECT day, pnl, n FROM mass_day WHERE run_id=? AND cid=?", (run_id, cid))}
        df = {d: (p, n) for d, p, n in ref.execute("SELECT day, pnl, n FROM mass_day WHERE run_id=? AND cid=?", (r, cid))}
        diff = max([abs(dr.get(d, (0.0, 0))[0] - df.get(d, (0.0, 0))[0]) for d in set(dr) | set(df)] or [0.0])
        ndiff = sum(1 for d in set(dr) | set(df) if dr.get(d, (0, 0))[1] != df.get(d, (0, 0))[1])
        ok = bool(cw and rf and not cw[2] and cw[0] == rf[0] and abs(cw[1] - rf[1]) <= TOL * max(1, rf[0]) and diff <= TOL and ndiff == 0)
        why = "" if ok else "cw %s ref %s, макс. разн. суток %.3g, суток с другим n %d" % (cw, rf, diff, ndiff)
        rp.execute("INSERT OR REPLACE INTO rp_gate VALUES(?,?,?,?,?,?,?,?,?,?,?)", (run_id, a.side, cid, m, rf[0] if rf else None, cw[0] if cw else None, rf[1] if rf else None, cw[1] if cw else None, diff, int(ok), why))
        if not ok:
            ok_all = False
            print("ВОРОТА не пройдены: cid %d %s: %s" % (cid, win, why), flush=True)
            continue
        sel = np.nonzero((dd >= W.MONTH_START[m]) & (dd < W.MONTH_START[m + 1]))[0]
        rp.executemany("INSERT INTO rp_trade VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       [(a.side, a.cellset, cid, m, t.sym[i], int(dd[i]), int(t.entry_ms[i]) + t0_ms, int(t.exit_ms[i]) + t0_ms, float(t.gross[i] - t.fees[i]), float(t.fees[i]), float(t.net[i]), t.kind[i]) for i in sel])
        rp.execute("INSERT OR REPLACE INTO rp_done VALUES(?,?,?,?,?)", (a.side, a.cellset, cid, m, run_id))
    rp.commit()
    seen = {(c, W.MONTHS.index(w)) for c, w, *_ in got}
    if wanted - seen:
        ok_all = False
        print("не посчитаны пары: %s" % sorted(wanted - seen)[:10], flush=True)
    print("повтор %s: пар записано %d из %d, ворота %s" % (run_id, len(wanted & seen), len(wanted), "PASS" if ok_all else "FAIL"), flush=True)
    return 0 if ok_all else 1


# ───────────────────────────── R:R, стоп, утечка ─────────────────────────────

def cmd_rr(a):
    ref = ro(a.db)
    out = sqlite3.connect(a.out)
    runs = month_runs(ref, a.runs.split(","))
    nm = len(runs)
    rp = ro(a.rp)
    need = need_for(out, a.rid, a.side, nm)
    done = {(c, m) for c, m in rp.execute("SELECT cid, month FROM rp_done WHERE side=? AND cellset=?", (a.side, a.cellset))}
    absent = sorted(need - done)
    if absent:
        raise SystemExit("повтор не закончен: нет пар (клетка, месяц) %d: %s" % (len(absent), absent[:10]))
    cids = sorted({c for c, _ in need})
    tr, stops = {}, {}
    for cid in cids:
        rows = rp.execute("SELECT entry_day, exit_ms, val, fees FROM rp_trade WHERE side=? AND cellset=? AND cid=? ORDER BY entry_ms", (a.side, a.cellset, cid)).fetchall()
        arr = np.array(rows, float).reshape(-1, 4)
        tr[cid] = dict(day=arr[:, 0].astype(np.int64), exit=arr[:, 1].astype(np.int64), val=arr[:, 2], fees=arr[:, 3])
        ckey, line = ref.execute("SELECT ckey, line FROM cell WHERE cellset=? AND side=? AND cid=?", (a.cellset, a.side, cid)).fetchone()
        stops[cid] = stop_pct(ckey, line)
    nrow = 0
    for step in W.STEPS:
        for emb in W.EMBARGOS:
            oos = out.execute("SELECT day, cid FROM tk692_oos WHERE rid=? AND side=? AND step=? AND emb=?", (a.rid, a.side, step, emb)).fetchall()
            if not oos:
                continue
            days = sorted(d for d, _ in oos)
            cd = {}
            for d, c in oos:
                cd.setdefault(c, set()).add(d)
            rr, sl, n = series_stats_rr(tr, stops, cd)
            leak, win_sum, npk, nskip = 0.0, 0.0, 0, 0
            for cid, b, e, w, sw, sk in out.execute("SELECT cid, b, e, w, sum_w, skipped FROM tk692_pick WHERE rid=? AND side=? AND step=? AND emb=? AND cid IS NOT NULL AND e>b", (a.rid, a.side, step, emb)):
                if sk:
                    nskip += 1
                    continue
                t = tr[cid]
                leak += leak_usd(t["day"], t["exit"], t["val"], w, D0_MS + b * DAY_MS)
                win_sum += sw
                npk += 1
            share = leak / win_sum if win_sum else None
            note = json.dumps(dict(n=n, picks=npk, skipped=nskip, leak_usd=leak, win_sum=win_sum, stops=sorted({stops[c] for c in cd})))
            out.execute("INSERT OR REPLACE INTO tk692_rr VALUES(?,?,?,?,?,?,?,?,?,?)", (a.rid, a.side, step, emb, "select", rr, sl, sl / W.MARGIN * 100 if sl is not None else None, share, note))
            nrow += 1
            for label, ex in out.execute("SELECT series, extra FROM tk692_sum WHERE rid=? AND side=? AND step=? AND emb=? AND series<>'select'", (a.rid, a.side, step, emb)).fetchall():
                c = json.loads(ex or "{}").get("cid")
                if c is None:
                    continue
                rr_b, sl_b, n_b = series_stats_rr(tr, stops, {int(c): set(days)})
                out.execute("INSERT OR REPLACE INTO tk692_rr VALUES(?,?,?,?,?,?,?,?,?,?)", (a.rid, a.side, step, emb, label, rr_b, sl_b, sl_b / W.MARGIN * 100 if sl_b is not None else None, None,
                                                                                       json.dumps(dict(n=n_b, cid=int(c), stop=stops[int(c)]))))
                nrow += 1
    out.commit()
    print("tk692_rr: строк %d (%s %s)" % (nrow, a.rid, a.side))
    for r in out.execute("SELECT step, emb, series, rr, stop_loss, stop_loss_pct, leak_share, note FROM tk692_rr WHERE rid=? AND side=? ORDER BY emb DESC, step, series", (a.rid, a.side)):
        print("| %s | E=%d | %s | R:R %s | стоп %s $ (%s %% маржи) | утечка %s |" % (
            r[0], r[1], r[2], "—" if r[3] is None else "%.2f" % r[3], "—" if r[4] is None else "%.2f" % r[4], "—" if r[5] is None else "%.1f" % r[5], "—" if r[6] is None else "%.3f" % r[6]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("replay", "rr"):
        s = sp.add_parser(n)
        s.add_argument("--rid", required=True)
        s.add_argument("--side", required=True, choices=("long", "short"))
        s.add_argument("--runs", required=True, help="run-id месяцев TK-700 через запятую (как --runs-long / --runs-short)")
        s.add_argument("--cellset", default="all1")
        s.add_argument("--db", default=MASS_DB)
        s.add_argument("--out", default=OUT_DB)
        s.add_argument("--rp", default=RP_DB)
        if n == "replay":
            s.add_argument("--class", dest="cls", default="A")
            s.add_argument("--threads", type=int, default=1)
            s.add_argument("--cap-gb", type=int, default=6)
    a = ap.parse_args(argv)
    return {"replay": cmd_replay, "rr": cmd_rr}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
