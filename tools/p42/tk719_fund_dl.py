#!/usr/bin/env python3
"""TK-719 (PS-1041 п. 2): докачка ставок фандинга июль–ноябрь 2026 в раскладку fund-all.

  python3 tk719_fund_dl.py get   [--root /data/p42-tk719/fund-dl] [--workers 6]   # c539: месячные zip 2026-07…2026-10 (data.binance.vision) + REST с 01.11 в <root>/rest/<монета>.csv
  python3 tk719_fund_dl.py check [--root …] [--n 25]                              # REST против архива на октябре: ≥ n монет, ставки и времена равны (иначе код 1)

Только crypto c539 (`/data/p42-tk621/universe/symbols-crypto.txt`). Файл REST: `calc_time,funding_interval_hours,last_funding_rate` (как строки архива), повтор запуска дописывает целиком заново.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

C539 = "/data/p42-tk621/universe/symbols-crypto.txt"
BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
REST = "https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&startTime=%d&limit=1000"      # монета — через quote (китайские тикеры)
MONTHS = ("2026-07", "2026-08", "2026-09", "2026-10")
NOV1_MS = 1793491200000                                           # 2026-11-01 00:00 UTC
OCT1_MS = 1790812800000                                           # 2026-10-01 00:00 UTC


def get(url, tries=4):
    for k in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1.5 * (k + 1))
        except Exception:                                          # noqa: BLE001
            time.sleep(1.5 * (k + 1))
    return None


def symbols():
    return [s for s in open(C539).read().strip().split(",") if s]


def rest_rows(sym, start_ms):
    out, cur = [], start_ms
    while True:
        raw = get(REST % (urllib.parse.quote(sym), cur))
        if raw is None:
            return None
        rows = json.loads(raw)
        out += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
        if len(rows) < 1000:
            return out
        cur = int(rows[-1]["fundingTime"]) + 1


def job(arg):
    root, sym = arg
    d = os.path.join(root, sym)
    os.makedirs(d, exist_ok=True)
    got = 0
    for m in MONTHS:
        p = os.path.join(d, "%s-fundingRate-%s.zip" % (sym, m))
        if os.path.exists(p) and os.path.getsize(p) > 0:
            got += 1
            continue
        q = urllib.parse.quote(sym)
        raw = get("%s/%s/%s-fundingRate-%s.zip" % (BASE, q, q, m))
        if raw:
            open(p, "wb").write(raw)
            got += 1
    rows = rest_rows(sym, NOV1_MS)
    if rows is not None:
        os.makedirs(os.path.join(root, "rest"), exist_ok=True)
        with open(os.path.join(root, "rest", sym + ".csv"), "w", encoding="utf-8", newline="") as f:
            f.write("calc_time,funding_interval_hours,last_funding_rate\n")
            for t, r in rows:
                f.write("%d,8,%s\n" % (t, repr(r)))
    return sym, got, None if rows is None else len(rows)


def cmd_get(a):
    syms = a.only.split(",") if a.only else symbols()
    ok = bad = 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for sym, got, nr in ex.map(job, [(a.root, s) for s in syms]):
            if got == len(MONTHS) and nr is not None:
                ok += 1
            else:
                bad += 1
                print("неполно %s: месяцев %d / %d, REST %s" % (sym, got, len(MONTHS), nr))
    print("монет %d: полных %d, неполных %d (новые листинги — нормально)" % (len(syms), ok, bad))
    return 0


def read_zip(p):
    out = {}
    z = zipfile.ZipFile(p)
    for nm in z.namelist():
        for line in io.TextIOWrapper(z.open(nm), encoding="utf-8").read().splitlines()[1:]:
            q = line.split(",")
            if len(q) >= 3 and q[0].isdigit():
                out[int(q[0])] = float(q[2])
    return out


def cmd_check(a):
    syms = [s for s in symbols() if os.path.exists(os.path.join(a.root, s, "%s-fundingRate-2026-10.zip" % s))][: a.n]
    tot = bad = 0
    for s in syms:
        arc = read_zip(os.path.join(a.root, s, "%s-fundingRate-2026-10.zip" % s))
        rows = rest_rows(s, OCT1_MS)
        if rows is None:
            print("REST недоступен для %s" % s)
            return 1
        rest = {t: r for t, r in rows if t < NOV1_MS}
        for t, r in arc.items():
            tot += 1
            if t not in rest or abs(rest[t] - r) > 1e-12:
                bad += 1
        bad += len(set(rest) - set(arc))
    print("проверка REST против архива (октябрь): монет %d, ставок %d, расхождений %d" % (len(syms), tot, bad))
    return 0 if len(syms) >= a.n and bad == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("get")
    s.add_argument("--root", default="/data/p42-tk719/fund-dl")
    s.add_argument("--workers", type=int, default=6)
    s.add_argument("--only", default="")                               # список монет через запятую (повторная докачка)
    s = sp.add_parser("check")
    s.add_argument("--root", default="/data/p42-tk719/fund-dl")
    s.add_argument("--n", type=int, default=25)
    a = ap.parse_args(argv)
    return {"get": cmd_get, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
