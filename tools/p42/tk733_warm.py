#!/usr/bin/env python3
"""TK-733, П-42 шаг 6а: край окна — код или рынок. Движок боевого бинаря TK-706 на клетках S1 / S2 / L401 дважды: как в шаге 1 (сделки лежат на диске, TK-726 / TK-718) и со сдвигом старта T0
(прогрев: на 7 суток раньше, сделки до окна отбрасываются; «холодный» вариант — на 6 суток позже, зачёт = первые сигнальные сутки, как у wa). Сделки первых 2 суток окна сравниваются
байт в байт по (sym, sig_ms, entry_ms, exit_ms, kind, gross, fees, takes, entry_px_e8) в абсолютном времени (мс от T0 + t0 прогона); `level`, `*_min`, `entry_day` — индексы от T0, не сравниваются.

  python3 tk733_warm.py prep <окно> <warm|cold|ctrl>     # дерево-ссылки на файлы существующих деревьев + `breakout convert` (P42_T0 = сдвинутый T0)
  python3 tk733_warm.py run  <окно> <warm|cold|ctrl>     # движок на 3 клетках, сделки → /data/p42-tk733/trades/<окно>-<режим>/
  python3 tk733_warm.py cmp  <окно> <warm|cold|ctrl>     # сравнение с шагом 1 → таблицы tk733_cmp, tk733_diff в tk733.sqlite
  ctrl — тот же T0, что в шаге 1 (контроль цепочки: дерево-ссылки → кэш → движок → разбор должны дать сделки шага 1 побайтно).

Только srv, один движок за раз. Данные — `/data/p42-tk733/`."""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.environ.get("TK733_ROOT", "/data/p42-tk733")
DB = ROOT + "/tk733.sqlite"
ENGINE = "/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45"
ENGINE_MD5 = "06367f45fad0d7c5c1031d82dfa03615"
CONVERT_BIN = "/data/p42-tk616/alpha-mk.396989e9"
MASS726 = "/data/p42-tk726/mass726.sqlite"
CELLSET = "tk726s"
CELLS = {7180005: "S1", 7180003: "S2", 758805: "L401"}
C539 = "/data/p42-tk621/universe/symbols-crypto.txt"
TICK_STORE = ["--tick-store", "/data/p42-tk599/tick-store", "--tick-store-cap-gb", "3", "--tick-store-min-free-gb", "5"]
T726 = "/data/p42-tk726/trades"
T718 = "/data/p42-tk718/trades/tk718c-h1-A"
D = datetime.date.fromisoformat
DAY_MS = 86_400_000
EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
SHIFT_WARM, SHIFT_COLD = -7, 6
HORIZON = 21                                   # дерево — до lo + 21 суток (сравниваются входы первых 14 суток окна)

# окно → T0 шага 1, первые зачётные сутки, число суток дерева «как в шаге 1», где лежат сделки шага 1, список контрактов, источники файлов (первый найденный)
WIN = {
    "wa": dict(t0="2026-06-17", lo="2026-07-01", trades=T726 + "/tk726-sel-A", syms=C539,
               src=["/data/p42-tk621/wa", "/data/p42-tk686/wj", "/data/p42-tk621/wa20"]),
    "n1": dict(t0="2026-10-25", lo="2026-11-14", trades=T726 + "/tk726-n1-A", syms="/data/p42-tk700/universe/n1.txt",
               src=["/data/p42-tk700/n1/tree", "/data/p42-tk621/wc", "/data/p42-tk726/twA/tree"]),
    "m01": dict(t0="2025-12-12", lo="2026-01-01", trades=T718, syms="/data/p42-tk700/universe/m01.txt", src=["/data/p42-tk695/jan/tree"]),
    "m02": dict(t0="2026-01-12", lo="2026-02-01", trades=T718, syms="/data/p42-tk700/universe/m02.txt", src=["/data/p42-tk695/feb/tree", "/data/p42-tk695/jan/tree"]),
    "m03": dict(t0="2026-02-09", lo="2026-03-01", trades=T718, syms="/data/p42-tk700/universe/m03.txt", src=["/data/p42-tk695/mar/tree", "/data/p42-tk695/feb/tree"]),
}


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_symbols(path):
    with open(path, encoding="utf-8") as f:
        t = f.read().strip()
    return json.loads(t) if t.startswith("[") else t.replace(",", " ").split()


def t0_of(win, mode):
    base = D(WIN[win]["t0"])
    sh = {"warm": SHIFT_WARM, "cold": SHIFT_COLD, "ctrl": 0}[mode]
    return base + datetime.timedelta(days=sh)


def end_of(win, mode):
    lo = D(WIN[win]["lo"])
    orig_end = D(WIN[win]["t0"]) + datetime.timedelta(days=(30 if win == "n1" else 47))
    return min(lo + datetime.timedelta(days=HORIZON), orig_end, t0_of(win, mode) + datetime.timedelta(days=79))


def tree_dir(win, mode):
    return "%s/trees/%s-%s" % (ROOT, win, mode)


def first_existing(paths):
    for p in paths:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return None


def cmd_prep(win, mode):
    t0, end = t0_of(win, mode), end_of(win, mode)
    tdir = tree_dir(win, mode)
    if os.path.exists(tdir + "/klines"):
        raise SystemExit("дерево %s уже есть" % tdir)
    syms = read_symbols(WIN[win]["syms"])
    src = WIN[win]["src"]
    days = [(t0 + datetime.timedelta(i)).isoformat() for i in range((end - t0).days + 1)]
    cnt, miss = {"klines": 0, "aggtrades": 0, "funding": 0}, []
    for s in syms:
        os.makedirs("%s/klines/%s" % (tdir, s), exist_ok=True)
        os.makedirs("%s/aggtrades/%s" % (tdir, s), exist_ok=True)
        for d in days:
            p = first_existing(["%s/klines/%s/%s.csv" % (r, s, d) for r in src])
            if p:
                os.symlink(os.path.realpath(p), "%s/klines/%s/%s.csv" % (tdir, s, d))
                cnt["klines"] += 1
            else:
                miss.append(("klines", s, d))
            p = first_existing(["%s/aggtrades/%s/%s-aggTrades-%s.zip" % (r, s, s, d) for r in src])
            if p:
                os.symlink(os.path.realpath(p), "%s/aggtrades/%s/%s-aggTrades-%s.zip" % (tdir, s, s, d))
                cnt["aggtrades"] += 1
            else:
                miss.append(("aggtrades", s, d))
        f = first_existing(["%s/klines/%s/funding.csv" % (r, s) for r in src])
        if f:
            os.symlink(os.path.realpath(f), "%s/klines/%s/funding.csv" % (tdir, s))
            cnt["funding"] += 1
    with open(tdir + "/nofile.tsv", "w", encoding="utf-8", newline="\n") as f:
        f.writelines("\t".join(r) + "\n" for r in miss)
    # дыры в сутках, которые есть и в шаге 1 (с T0 шага 1): не должно быть лишних
    t0o = D(WIN[win]["t0"])
    pre = [r for r in miss if D(r[2]) < t0o]
    post = [r for r in miss if D(r[2]) >= t0o]
    print("%s-%s: T0 %s, до %s (%d суток), монет %d, ссылок: %s; нет файла: до T0 шага 1 — %d, после — %d" % (win, mode, t0, end, len(days), len(syms), cnt, len(pre), len(post)), flush=True)
    env = dict(os.environ, P42_T0=t0.isoformat())
    cache = tdir + "/cache"
    with open(tdir + "/convert.log", "w", encoding="utf-8") as lf:
        subprocess.run([CONVERT_BIN, "breakout", "convert", "--src", tdir + "/klines", "--out", cache, "--threads", "4"], env=env, stdout=lf, stderr=subprocess.STDOUT, check=True)
    print("convert: %s; кэш %d файлов" % (open(tdir + "/convert.log", encoding="utf-8").read().strip().splitlines()[-1][:160], len(os.listdir(cache))), flush=True)
    return 0


def cell_lines():
    c = sqlite3.connect("file:%s?mode=ro" % MASS726, uri=True)
    rows = {r[0]: r[1] for r in c.execute("SELECT cid, line FROM cell WHERE cellset=?", (CELLSET,))}
    return {cid: rows[cid] for cid in CELLS}


def cmd_run(win, mode, threads=5, cap_gb=12):
    if hashlib.md5(open(ENGINE, "rb").read()).hexdigest() != ENGINE_MD5:
        raise SystemExit("движок: md5 ≠ %s" % ENGINE_MD5)
    t0 = t0_of(win, mode)
    tdir = tree_dir(win, mode)
    syms = read_symbols(WIN[win]["syms"])
    lines = cell_lines()
    work = "%s/work/%s-%s" % (ROOT, win, mode)
    os.makedirs(work, exist_ok=True)
    vf = work + ".variants.txt"
    with open(vf, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join("k%d %s\n" % (cid, lines[cid]) for cid in CELLS))
    cmd = [ENGINE, "breakout", "tick", "--cache", tdir + "/cache", "--trades", tdir + "/aggtrades", "--variants", vf, "--symbols", ",".join(syms), "--out", work,
           "--threads", str(threads), "--cap-gb", str(cap_gb)] + TICK_STORE
    env = dict(os.environ, P42_T0=t0.isoformat(), P42_SIDES="short")
    ts = time.time()
    with open(work + ".log", "w", encoding="utf-8") as lf:
        subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, check=True)
    keep = "%s/trades/%s-%s" % (ROOT, win, mode)
    os.makedirs(keep, exist_ok=True)
    import shutil
    for cid in CELLS:
        shutil.copyfile("%s/k%d/short-trades.csv" % (work, cid), "%s/%d-trades.csv" % (keep, cid))
    t0_ms = int((datetime.datetime.combine(t0, datetime.time(), tzinfo=datetime.timezone.utc) - EPOCH).total_seconds() * 1000)
    json.dump({"t0": t0.isoformat(), "t0_ms": t0_ms, "engine_md5": ENGINE_MD5, "seconds": time.time() - ts}, open(keep + "/t0.json", "w"))
    print("%s-%s: движок %.0f с, сделки → %s" % (win, mode, time.time() - ts, keep), flush=True)
    return 0


def read_trades(path, t0_ms):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        out.append(dict(sym=r["sym"], sig=int(r["sig_ms"]) + t0_ms, entry=int(r["entry_ms"]) + t0_ms, exit=int(r["exit_ms"]) + t0_ms, kind=r["kind"], gross=r["gross"], fees=r["fees"],
                        takes=r["takes"], px=r["entry_px_e8"], pnl=float(r["gross"]) - float(r["fees"])))
    return out


def orig_trades(win, cid):
    wd = WIN[win]["trades"]
    t0j = json.load(open(wd + "/t0.json", encoding="utf-8"))
    return read_trades("%s/%s/%d-trades.csv" % (wd, win, cid), int(t0j[win]))


SCHEMA = """
CREATE TABLE IF NOT EXISTS tk733_cmp(win TEXT, mode TEXT, cid INTEGER, line_of TEXT, span_days INTEGER, n_orig INTEGER, n_alt INTEGER, n_match INTEGER, only_orig INTEGER, only_alt INTEGER,
  n_fielddiff INTEGER, pnl_orig REAL, pnl_alt REAL, utc TEXT, PRIMARY KEY(win, mode, cid, span_days));
CREATE TABLE IF NOT EXISTS tk733_diff(win TEXT, mode TEXT, cid INTEGER, span_days INTEGER, what TEXT, sym TEXT, sig_ms INTEGER, entry_ms INTEGER, exit_orig INTEGER, exit_alt INTEGER,
  kind_orig TEXT, kind_alt TEXT, pnl_orig REAL, pnl_alt REAL);
"""


def cmd_cmp(win, mode):
    lo_ms = int((datetime.datetime.combine(D(WIN[win]["lo"]), datetime.time(), tzinfo=datetime.timezone.utc) - EPOCH).total_seconds() * 1000)
    end_ms = int((datetime.datetime.combine(end_of(win, mode), datetime.time(), tzinfo=datetime.timezone.utc) - EPOCH).total_seconds() * 1000) + DAY_MS
    keep = "%s/trades/%s-%s" % (ROOT, win, mode)
    t0a = json.load(open(keep + "/t0.json", encoding="utf-8"))["t0_ms"]
    os.makedirs(ROOT, exist_ok=True)
    db = sqlite3.connect(DB, timeout=60)
    db.executescript(SCHEMA)
    db.execute("DELETE FROM tk733_cmp WHERE win=? AND mode=?", (win, mode))
    db.execute("DELETE FROM tk733_diff WHERE win=? AND mode=?", (win, mode))
    for cid, name in CELLS.items():
        o_all, a_all = orig_trades(win, cid), read_trades("%s/%d-trades.csv" % (keep, cid), t0a)
        for span in (1, 2, 3, 7, 14):
            hi_ms = lo_ms + span * DAY_MS
            o = {(t["sym"], t["sig"], t["entry"]): t for t in o_all if lo_ms <= t["entry"] < hi_ms}
            a = {(t["sym"], t["sig"], t["entry"]): t for t in a_all if lo_ms <= t["entry"] < hi_ms}
            both = o.keys() & a.keys()
            fd = [k for k in both if any(o[k][f] != a[k][f] for f in ("exit", "kind", "gross", "fees", "takes", "px"))]
            # сделки, у которых выход позже конца данных дерева (принудительное закрытие по концу данных), в сравнении не участвуют
            db.execute("INSERT OR REPLACE INTO tk733_cmp VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (win, mode, cid, name, span, len(o), len(a), len(both), len(o.keys() - a.keys()), len(a.keys() - o.keys()), len(fd),
                                                                                                 sum(t["pnl"] for t in o.values()), sum(t["pnl"] for t in a.values()), utc()))
            if span in (2, 14):
                rows = []
                for k in sorted(o.keys() - a.keys()):
                    t = o[k]
                    rows.append((win, mode, cid, span, "only_orig", k[0], k[1], k[2], t["exit"], None, t["kind"], None, t["pnl"], None))
                for k in sorted(a.keys() - o.keys()):
                    t = a[k]
                    rows.append((win, mode, cid, span, "only_alt", k[0], k[1], k[2], None, t["exit"], None, t["kind"], None, t["pnl"]))
                for k in sorted(fd):
                    rows.append((win, mode, cid, span, "fielddiff", k[0], k[1], k[2], o[k]["exit"], a[k]["exit"], o[k]["kind"], a[k]["kind"], o[k]["pnl"], a[k]["pnl"]))
                db.executemany("INSERT INTO tk733_diff VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            print("%s-%s %-4s первые %2d сут.: шаг 1 %4d сделок, повтор %4d, совпали %4d, только шаг 1 %3d, только повтор %3d, поля различаются %3d | Σ$ %.4f / %.4f" % (
                win, mode, name, span, len(o), len(a), len(both), len(o.keys() - a.keys()), len(a.keys() - o.keys()), len(fd), sum(t["pnl"] for t in o.values()), sum(t["pnl"] for t in a.values())), flush=True)
    # выходы за конец данных дерева: сколько сделок первых 14 суток закрываются позже end_ms − 1 суток
    db.commit()
    return 0


def main(argv):
    if len(argv) != 4 or argv[1] not in ("prep", "run", "cmp") or argv[2] not in WIN or argv[3] not in ("warm", "cold", "ctrl"):
        print(__doc__)
        return 2
    return {"prep": cmd_prep, "run": cmd_run, "cmp": cmd_cmp}[argv[1]](argv[2], argv[3])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
