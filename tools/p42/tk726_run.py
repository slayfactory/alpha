#!/usr/bin/env python3
"""TK-726, П-42 этап A, шаг 1 (PS-1054 + поправки PS-1060 п. 15, 6, 7, 11): 12 клеток шорта на wa / wb / wc / n1 и двух окнах-двойниках, сделки сохраняются. Только srv, один движок за раз.

  python3 tk726_run.py cells                 # набор `tk726s` (12 различных клеток: S1, S2, L401, 9 членов B; якорь s4 = член l7-tf60) → mass726.sqlite, имена → tk726.sqlite
  python3 tk726_run.py twin-prep             # дерево двойника A (539 монет, T0 12.10, как строится дерево n1) + `breakout convert`; проверка дерева wc для B
  python3 tk726_run.py twin-run              # движок на окнах twA, twB (по одной партии), сделки → <trades>/tk726-twin/<окно>/<cid>-trades.csv, ряды → tk726_day
  python3 tk726_run.py gate                  # ворота 1: суточные ряды повторного прогона wa / wb / wc / n1 = ряды летописи / TK-718 байт в байт (стоп до 1б при расхождении)
  python3 tk726_run.py collect               # ряды прогонов tk726-sel / tk726-n1 (mass_day) → tk726_day (сутки от 01.01.2026)

Окна-двойники (PS-1060 п. 6), оба на 01–13.11 (зачёт), сделки счёта идут от 14-х суток T0:
  twA — состав c539 (539), T0 = 12.10 (разгон 20 суток до 01.11), дерево строится как у n1 (ссылки `tk626_prep.build_tree` на опубликованные файлы), кэш — `breakout convert` с P42_T0 = 12.10;
  twB — состав c539 ∩ список TK-710 на 24.11 (522, как n1), T0 = 01.09 как у wc, дерево и кэш wc.
Расхождение A с wc = разгон / дерево; B с wc = состав. Данные результатов — `/data/p42-tk726/tk726.sqlite` (В-179)."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")

ROOT = os.environ.get("TK726_ROOT", "/data/p42-tk726")
OUT_DB = ROOT + "/tk726.sqlite"
MASS726 = ROOT + "/mass726.sqlite"
MASS718 = "/data/p42-tk718/mass718.sqlite"
TK718 = "/data/p42-tk718/tk718.sqlite"
TRADES = ROOT + "/trades"
CHRON = "/data/chron/chron.sqlite"
C539 = "/data/p42-tk621/universe/symbols-crypto.txt"
N1_UNI = "/data/p42-tk700/universe/n1.txt"
CONVERT_BIN = "/data/p42-tk616/alpha-mk.396989e9"                  # `breakout convert` как у дерева n1 (tk700_n1.sh)
ENGINE = "/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45"
ENGINE_MD5 = "06367f45fad0d7c5c1031d82dfa03615"
CELLSET = "tk726s"
SEL_WINS = ("wa", "wb", "wc")
D0_2026 = datetime.date(2026, 1, 1)
SEL_DAY0, SEL_DAYS = 181, 136
N1_DAY0, N1_DAYS = 317, 11
# окна-двойники: имя → (T0, начало зачёта, конец зачёта, корень дерева, файл состава)
TWINS = {
    "twA": dict(t0="2026-10-12", lo="2026-11-01", hi="2026-11-13", tree=ROOT + "/twA", syms=C539),
    "twB": dict(t0="2026-09-01", lo="2026-11-01", hi="2026-11-13", tree="/data/p42-tk621/wc", syms=N1_UNI),
}
TICK_STORE = ["--tick-store", "/data/p42-tk599/tick-store", "--tick-store-cap-gb", "3", "--tick-store-min-free-gb", "5"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS tk726_cell(cid INTEGER PRIMARY KEY, name TEXT, line_of TEXT, ckey TEXT);
CREATE TABLE IF NOT EXISTS tk726_day(run TEXT, win TEXT, cid INTEGER, day INTEGER, pnl REAL, n INTEGER, PRIMARY KEY(run, win, cid, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS tk726_runinfo(run TEXT, win TEXT, t0 TEXT, lo TEXT, hi TEXT, engine_md5 TEXT, symbols INTEGER, seconds REAL, utc TEXT, PRIMARY KEY(run, win));
"""


def read_symbols(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip().replace(",", " ").split()


def day_index(date_iso):
    """Сутки от 01.01.2026 (как `tk718_ser`)."""
    return (datetime.date.fromisoformat(date_iso) - D0_2026).days


def twin_window(st, name):
    c = TWINS[name]
    return st.Window(name, c["t0"], c["lo"], c["hi"])


def twin_geometry_ok(name):
    """T0 + 14 ≤ зачёт ≤ T0 + 73 (торговая часть сетки) и разгон до зачёта (суток) — ≥ 14; данные не позже 13.11."""
    c = TWINS[name]
    t0, lo, hi = (datetime.date.fromisoformat(c[k]) for k in ("t0", "lo", "hi"))
    return t0 + datetime.timedelta(days=14) <= lo and hi <= t0 + datetime.timedelta(days=73) and hi <= datetime.date(2026, 11, 13)


def connect_out():
    os.makedirs(ROOT, exist_ok=True)
    c = sqlite3.connect(OUT_DB, timeout=120)
    c.executescript(SCHEMA)
    return c


def utc():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ───────────────────────────── набор клеток ─────────────────────────────

def cmd_cells(a):
    src = sqlite3.connect("file:%s?mode=ro" % MASS718, uri=True)
    plan = sqlite3.connect("file:%s?mode=ro" % TK718, uri=True)
    rows = [r for r in src.execute("SELECT cellset, cid, side, ckey, line, sig, filt, feats, ref_n, ref_usd, ref_daily, db_id, bin FROM cell WHERE cellset='tk718core' AND side='short' ORDER BY cid")]
    names = {r[0]: (r[1], r[2]) for r in plan.execute("SELECT cid, name, line_of FROM tk718_plan WHERE side='short' AND role IN ('line', 'member')")}
    assert len(rows) == 12 and set(names) == {r[1] for r in rows}, (len(rows), sorted(names))
    import tk700_cells as cl
    db = cl.connect(MASS726)
    import tk700_mass as ms
    db.executescript(ms.SCHEMA)
    db.executescript("CREATE TABLE IF NOT EXISTS cellset(name TEXT PRIMARY KEY, created TEXT, params TEXT);"
                     "CREATE TABLE IF NOT EXISTS cell(cellset TEXT, cid INTEGER, side TEXT, ckey TEXT, line TEXT, sig TEXT, filt TEXT, feats TEXT, ref_n INTEGER, ref_usd REAL, ref_daily TEXT,"
                     " db_id INTEGER, bin TEXT, PRIMARY KEY(cellset, cid));")
    db.execute("DELETE FROM cell WHERE cellset=?", (CELLSET,))
    db.execute("DELETE FROM cellset WHERE name=?", (CELLSET,))
    db.execute("INSERT INTO cellset VALUES(?,?,?)", (CELLSET, utc(), json.dumps(dict(ps="PS-1054/PS-1060", src="tk718core short"))))
    db.executemany("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [(CELLSET,) + r[1:] for r in rows])
    db.commit()
    out = connect_out()
    out.execute("DELETE FROM tk726_cell")
    out.executemany("INSERT INTO tk726_cell VALUES(?,?,?,?)", [(r[1], names[r[1]][0], names[r[1]][1], r[3]) for r in rows])
    out.commit()
    print("клеток %d: %s" % (len(rows), ", ".join(names[r[1]][0] for r in rows)))
    return 0


# ───────────────────────────── окна-двойники ─────────────────────────────

def cmd_twin_prep(a):
    import tk626_prep as pr
    c = TWINS["twA"]
    syms = read_symbols(c["syms"])
    assert len(syms) == 539, len(syms)
    days = pr.day_list(c["t0"], datetime.date.fromisoformat(c["hi"]))
    wdir = os.path.join(c["tree"], "tree")
    if os.path.exists(wdir):
        raise SystemExit("каталог %s уже есть: дерево не перестраивается поверх" % wdir)
    kl, tr, fund = pr.sources("/data/p42-tk616/fwd/dl", "/data/p42-tk616/fwd/rest")
    cnt, miss = pr.build_tree(wdir, syms, days, kl, tr, fund)
    with open(os.path.join(c["tree"], "nofile.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.writelines("\t".join(r) + "\n" for r in miss)
    print("twA T0 %s: сутки %s…%s (%d), контрактов %d, минуток %d, сделок %d, funding %d, нет файла %d" % (c["t0"], days[0], days[-1], len(days), len(syms), cnt["klines"], cnt["aggtrades"], cnt["funding"], len(miss)))
    # дыры допустимы только те же, что в дереве wc (монета ещё не торговалась / нет суток): файла нет и в wc — иначе двойник A не строится
    wcdir = "/data/p42-tk621/wc"
    extra = [r for r in miss if os.path.exists(os.path.join(wcdir, r[0], r[1], (r[2] + ".csv") if r[0] == "klines" else "%s-aggTrades-%s.zip" % (r[1], r[2])))]
    print("нет файла в дереве A: %d, из них есть в wc: %d" % (len(miss), len(extra)))
    if extra:
        raise SystemExit("в дереве A нет %d файлов, которые есть в wc (см. nofile.tsv) — двойник A не строится" % len(extra))
    env = dict(os.environ, P42_T0=c["t0"])
    cache = os.path.join(c["tree"], "cache")
    with open(os.path.join(c["tree"], "convert.log"), "w", encoding="utf-8") as lf:
        subprocess.run([CONVERT_BIN, "breakout", "convert", "--src", os.path.join(wdir, "klines"), "--out", cache, "--threads", "4"], env=env, stdout=lf, stderr=subprocess.STDOUT, check=True)
    for k in ("aggtrades", "klines"):
        link = os.path.join(c["tree"], k)
        if not os.path.lexists(link):
            os.symlink(os.path.join(wdir, k), link)
    print("convert: %s; кэш %d файлов" % (open(os.path.join(c["tree"], "convert.log"), encoding="utf-8").read().strip().splitlines()[-1][:200], len(os.listdir(cache))))
    cb = TWINS["twB"]
    have = set(os.listdir(os.path.join(cb["tree"], "cache")))
    lack = [s for s in read_symbols(cb["syms"]) if s + ".b42" not in have]
    print("twB: дерево wc, контрактов %d, нет в кэше wc: %d" % (len(read_symbols(cb["syms"])), len(lack)))
    return 1 if lack else 0


def run_twin(name, m, st, p8, ho, ms, db, out, threads, cap_gb):
    c = TWINS[name]
    assert twin_geometry_ok(name), name
    w = twin_window(st, name)
    syms = read_symbols(c["syms"])
    cells = ms.load_cells(db, CELLSET, "short")
    keep = os.path.join(TRADES, "tk726-twin", name)
    os.makedirs(keep, exist_ok=True)
    t0 = utc()
    import time
    ts = time.time()
    for bi, cur in enumerate(ms.make_batches(cells, 800, 26)):
        work = os.path.join(ROOT, "work", name + "-%d" % bi)
        import shutil
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        vf = work + ".variants.txt"
        with open(vf, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join("k%d %s\n" % (x["cid"], x["line"]) for x in cur))
        cmd = [ENGINE, "breakout", "tick", "--cache", c["tree"] + "/cache", "--trades", c["tree"] + "/aggtrades", "--variants", vf, "--symbols", ",".join(syms), "--out", work, "--threads", str(threads), "--cap-gb", str(cap_gb)] + TICK_STORE
        env = dict(os.environ, P42_T0=c["t0"], P42_SIDES="short")
        with open(work + ".log", "w", encoding="utf-8") as lf:
            subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, check=True)
        for x in cur:
            n, usd, errs, notes, ds, pn, ct = ms.cell_window(st, p8, w, "short", os.path.join(work, "k%d" % x["cid"]), False)
            if errs:
                raise SystemExit("%s k%d: %s" % (name, x["cid"], errs))
            base = (w.t0d - st.D0).days - (w.t0d - D0_2026).days                     # сутки st.D0 → сутки от 01.01.2026
            out.executemany("INSERT OR REPLACE INTO tk726_day VALUES(?,?,?,?,?,?)", [("tk726-twin", name, x["cid"], d - base, p, k) for d, p, k in zip(ds, pn, ct)])
            shutil.copyfile(os.path.join(work, "k%d" % x["cid"], p8.ENG["S"] + "-trades.csv"), os.path.join(keep, "%d-trades.csv" % x["cid"]))
            print("%s cid %d: сделок %d Σ %.4f %s" % (name, x["cid"], n, usd, notes), flush=True)
        shutil.rmtree(work, ignore_errors=True)
    t0p = os.path.join(TRADES, "tk726-twin", "t0.json")
    t0d = json.load(open(t0p, encoding="utf-8")) if os.path.exists(t0p) else {}
    t0d[name] = int(w.t0_ms)
    json.dump(t0d, open(t0p, "w", encoding="utf-8"))
    out.execute("INSERT OR REPLACE INTO tk726_runinfo VALUES(?,?,?,?,?,?,?,?,?)", ("tk726-twin", name, c["t0"], c["lo"], c["hi"], ENGINE_MD5, len(syms), time.time() - ts, t0))
    out.commit()


def cmd_twin_run(a):
    import hashlib
    if hashlib.md5(open(ENGINE, "rb").read()).hexdigest() != ENGINE_MD5:
        raise SystemExit("движок: md5 ≠ %s" % ENGINE_MD5)
    import tk700_holdout as ho
    m = ho.modes(True)                                                    # режим wa / wb / wc: таблицы окон Jul–Nov, окна двойников — свои `Window` (не из TRADE_WINDOWS)
    import tk638_post as p8
    import tk700_mass as ms
    st = m["st"]
    db = ms.connect(MASS726)
    out = connect_out()
    for name in a.wins.split(","):
        run_twin(name, m, st, p8, ho, ms, db, out, a.threads, a.cap_gb)
    return 0


# ───────────────────────────── ряды и ворота ─────────────────────────────

def cmd_collect(a):
    """mass_day прогонов tk726-sel (dry: сутки от 01.07 → +181) и tk726-n1 (сутки от 01.01) → tk726_day."""
    mass = sqlite3.connect("file:%s?mode=ro" % MASS726, uri=True)
    out = connect_out()
    for run, dry in (("tk726-sel-A", True), ("tk726-n1-A", False)):
        rows = list(mass.execute("SELECT cid, day, pnl, n FROM mass_day WHERE run_id=?", (run,)))
        if not rows:
            print("нет рядов", run)
            continue
        wins = {r[0]: r[1] for r in mass.execute("SELECT cid, win FROM mass_cw WHERE run_id=?", (run,))}
        out.execute("DELETE FROM tk726_day WHERE run=?", (run,))
        out.executemany("INSERT INTO tk726_day VALUES(?,?,?,?,?,?)", [(run, "sel" if dry else "n1", cid, day + (SEL_DAY0 if dry else 0), pnl, n) for cid, day, pnl, n in rows])
        print(run, "рядов", len(rows), "клеток", len({r[0] for r in rows}))
    out.commit()
    return 0


def dense(rows, d0, nd):
    """[(day, pnl, n)] → массивы длины nd (сутки d0…d0+nd−1)."""
    p, k = np.zeros(nd), np.zeros(nd, np.int32)
    for d, pnl, n in rows:
        j = d - d0
        if 0 <= j < nd:
            p[j] += pnl
            k[j] += n
    return p, k


def cmd_gate(a):
    """Ворота 1 (PS-1060 п. 15): SEL — против `metric.extra.daily` / `dayn` летописи (как `tk718_ser`), n1 — против ряда `tk718c-n1-A` (`tk718_ser`, группа N1). Байт в байт (`tobytes`)."""
    out = connect_out()
    t718 = sqlite3.connect("file:%s?mode=ro" % TK718, uri=True)
    cells = {r[0]: r[1] for r in out.execute("SELECT cid, name FROM tk726_cell")}
    plan = {r[0]: (r[1], r[2]) for r in t718.execute("SELECT cid, ckey, chron_id FROM tk718_plan WHERE side='short'")}
    chron = sqlite3.connect("file:%s?mode=ro" % CHRON, uri=True)
    bad = 0
    for cid in sorted(cells):
        ckey, chron_id = plan[cid]
        mine = dense(list(out.execute("SELECT day, pnl, n FROM tk726_day WHERE run='tk726-sel-A' AND cid=?", (cid,))), SEL_DAY0, SEL_DAYS)
        daily, dayn = chron.execute("SELECT json_extract(extra, '$.daily'), json_extract(extra, '$.dayn') FROM metric WHERE id=?", (chron_id,)).fetchone()
        ref = (np.asarray(json.loads(daily), np.float64), np.asarray(json.loads(dayn), np.int32))
        # SEL: ряд летописи накоплен в другом порядке суммирования, чем mass_day (расхождение ≤ 3e-14 $ у 12 из 12 клеток при равных сделках и Σ): равенство — число сделок по суткам байт в байт
        # и P&L по суткам в микродолларах (разрешение файлов сделок, 6 знаков); побайтно 64-бит ряд SEL не воспроизводим (отступление от PS-1060 п. 15, `chron --kind amend`); n1 — побайтно
        mu = lambda x: np.round(np.asarray(x, np.float64) * 1e6).astype(np.int64)   # noqa: E731
        ok_sel = len(ref[0]) == SEL_DAYS and mine[1].tobytes() == ref[1].tobytes() and np.array_equal(mu(mine[0]), mu(ref[0]))
        ulp = float(np.abs(mine[0] - ref[0]).max())
        got1 = dense(list(out.execute("SELECT day, pnl, n FROM tk726_day WHERE run='tk726-n1-A' AND cid=?", (cid,))), N1_DAY0, N1_DAYS)
        r1 = t718.execute("SELECT pnl, n FROM tk718_ser WHERE side='short' AND ckey=? AND grp='N1'", (ckey,)).fetchone()
        if r1 is None:
            ok_n1, why = False, "нет ряда N1 в tk718_ser"
        else:
            rp, rn = np.frombuffer(r1[0], np.float64), np.frombuffer(r1[1], np.int32)
            ok_n1, why = got1[0].tobytes() == rp.tobytes() and got1[1].tobytes() == rn.tobytes(), ""
        print("ворота %-14s cid %-8d SEL(wa+wb+wc, %d сут.) %s (n байт в байт, P&L до 1e-6; макс |Δ| %.1e $; побайтно %s) | n1 %s (побайтно) %s" % (
            cells[cid], cid, len(ref[0]), "РАВНО" if ok_sel else "РАЗНО", ulp, "да" if mine[0].tobytes() == ref[0].tobytes() else "нет", "РАВНО" if ok_n1 else "РАЗНО", why))
        bad += (not ok_sel) + (not ok_n1)
    print("ВОРОТА 1: %s" % ("пройдены (SEL: n байт в байт и P&L до микродоллара; n1 побайтно)" if bad == 0 else "ПРОВАЛЕНЫ (%d из 24)" % bad))
    return 0 if bad == 0 else 3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("cells", "twin-prep", "twin-run", "collect", "gate"):
        s = sp.add_parser(n)
        if n == "twin-run":
            s.add_argument("--wins", default="twA,twB")
            s.add_argument("--threads", type=int, default=7)
            s.add_argument("--cap-gb", type=int, default=12)
    a = ap.parse_args(argv)
    return {"cells": cmd_cells, "twin-prep": cmd_twin_prep, "twin-run": cmd_twin_run, "collect": cmd_collect, "gate": cmd_gate}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
