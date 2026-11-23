#!/usr/bin/env python3
"""TK-700: докачка данных отложенной выборки (январь–июнь 2026) с data.binance.vision по списку задач — metrics, premiumIndexKlines 1m, fundingRate, bookDepth. Только имена и размеры файлов, содержимое не открывается.

  python3 tk700_dl.py plan  --kind fund [--wins m01,…]                     # fundingRate: все (контракт, месяц) вселенной окон → задачи (месяцы T0 … hi)
  python3 tk700_dl.py plan  --kind metrics|premium|bookdepth --rows-glob '<prod/lvl-*-*/rows.parquet>' [--back 2 --fwd 1] --out tasks-<вид>.txt
                                                                          # по строкам базы: (контракт, сутки сигнала − back … + fwd); раньше T0 и позже hi окна — отброшены
  python3 tk700_dl.py get   --tasks tasks-<вид>.txt [--root /data/p42-tk700/dl] [--workers 6] [--min-free-gb 60]
Раскладка как у TK-600 (читатели tk671_fund / tk671_feat / tk671_bd берут корни переменными / --zip): <root>/metrics-all/<SYM>/<SYM>-metrics-<день>.zip, <root>/premium-all/<SYM>/<SYM>-1m-<день>.zip,
<root>/fund-all/<SYM>/<SYM>-fundingRate-<месяц>.zip, <root>/book/zip/<SYM>/<SYM>-bookDepth-<день>.zip (+ `.sha256` по `.CHECKSUM` биржи). Охват — <root>/coverage-<вид>.csv (kind,sym,day,status,bytes); сторож места — как bookdepth_dl.py (код 3, ничего не удаляет).
Файл с сутками вне окон (позже 30.06.2026) задачей не бывает."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import glob
import hashlib
import os
import shutil
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk700_win as w  # noqa: E402

BASE = "https://data.binance.vision/data/futures/um"
LAST_OK = dt.date(2026, 6, 30)
KINDS = {  # kind → (путь на бирже, подкаталог корня, имя файла)
    "metrics": ("daily/metrics/{s}/{s}-metrics-{d}.zip", "metrics-all", "{s}-metrics-{d}.zip"),
    "premium": ("daily/premiumIndexKlines/{s}/1m/{s}-1m-{d}.zip", "premium-all", "{s}-1m-{d}.zip"),
    "fund": ("monthly/fundingRate/{s}/{s}-fundingRate-{d}.zip", "fund-all", "{s}-fundingRate-{d}.zip"),
    "bookdepth": ("daily/bookDepth/{s}/{s}-bookDepth-{d}.zip", "book/zip", "{s}-bookDepth-{d}.zip"),
}


def plan_fund(wins):
    out = set()
    for n in wins:
        s = w.spec(n)
        t0, hi = dt.date.fromisoformat(s["t0"]), dt.date.fromisoformat(s["hi"])
        months = sorted({(t0 + dt.timedelta(days=k)).strftime("%Y-%m") for k in range((hi - t0).days + 1)})
        for sym in w.universe(n):
            for m in months:
                out.add(("fund", sym, m))
    return sorted(out)


def plan_rows(kind, rows_glob, back, fwd):
    import pandas as pd
    out = set()
    for p in sorted(glob.glob(rows_glob)):
        name = os.path.basename(os.path.dirname(p)).split("-")[1]            # lvl-<окно>-<side>
        s = w.spec(name)
        t0, hi = dt.date.fromisoformat(s["t0"]), dt.date.fromisoformat(s["hi"])
        r = pd.read_parquet(p, columns=["sym", "abs_ms"])
        r = r.assign(day=pd.to_datetime(r["abs_ms"], unit="ms", utc=True).dt.date)
        for sym, day in r[["sym", "day"]].drop_duplicates().itertuples(index=False):
            for k in range(-back, fwd + 1):
                d = day + dt.timedelta(days=k)
                if t0 <= d <= min(hi, LAST_OK):
                    out.add((kind, sym, d.isoformat()))
    return sorted(out)


def free_gb(path):
    os.makedirs(path, exist_ok=True)
    return shutil.disk_usage(path).free / 1e9


def fetch(root, kind, sym, day, retries=3):
    ep, sub, nm = KINDS[kind]
    path = ep.format(s=sym, d=day)
    dst = os.path.join(root, sub, sym, nm.format(s=sym, d=day))
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return "ok", os.path.getsize(dst)
    url = "%s/%s" % (BASE, urllib.parse.quote(path))
    err = "err"
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = r.read()
            want = None
            try:
                with urllib.request.urlopen(url + ".CHECKSUM", timeout=60) as r:
                    want = r.read().decode().split()[0]
            except Exception:
                pass
            if want and hashlib.sha256(data).hexdigest() != want:
                err = "sha"
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dst)
            if want:
                with open(dst + ".sha256", "w") as f:
                    f.write("%s  %s\n" % (want, os.path.basename(dst)))
            return "ok", len(data)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "404", 0
            err = "err:%d" % e.code
        except Exception:
            err = "err"
    return err, 0


def cmd_get(a):
    tasks = [tuple(line.split()) for line in open(a.tasks, encoding="utf-8") if line.strip()]
    kinds = sorted({t[0] for t in tasks})
    stop = threading.Event()
    lock = threading.Lock()
    res = {}
    cov = open(os.path.join(a.root, "coverage-%s.csv" % "+".join(kinds)), "a", encoding="utf-8", newline="\n")

    def one(t):
        if stop.is_set():
            return
        kind, sym, day = t
        if free_gb(a.root) < a.min_free_gb:
            stop.set()
            return
        st, n = fetch(a.root, kind, sym, day)
        with lock:
            cov.write("%s,%s,%s,%s,%d\n" % (kind, sym, day, st, n))
            res[st] = res.get(st, 0) + 1

    with cf.ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, tasks))
    cov.close()
    print("задач %d, итог %s%s" % (len(tasks), dict(sorted(res.items())), "; СТОП: свободно < %g ГБ" % a.min_free_gb if stop.is_set() else ""))
    return 3 if stop.is_set() else 0


def cmd_plan(a):
    if a.kind == "fund":
        t = plan_fund(a.wins.split(",") if a.wins else w.NAMES)
    else:
        t = plan_rows(a.kind, a.rows_glob, a.back, a.fwd)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        for x in t:
            f.write(" ".join(x) + "\n")
    print("задач %d (%s) → %s" % (len(t), a.kind, a.out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("plan")
    p.add_argument("--kind", required=True, choices=sorted(KINDS))
    p.add_argument("--wins", default="")
    p.add_argument("--rows-glob", default="")
    p.add_argument("--back", type=int, default=2)
    p.add_argument("--fwd", type=int, default=1)
    p.add_argument("--out", required=True)
    g = sp.add_parser("get")
    g.add_argument("--tasks", required=True)
    g.add_argument("--root", default="/data/p42-tk700/dl")
    g.add_argument("--workers", type=int, default=6)
    g.add_argument("--min-free-gb", type=float, default=60.0)
    a = ap.parse_args(argv)
    return cmd_plan(a) if a.cmd == "plan" else cmd_get(a)


if __name__ == "__main__":
    sys.exit(main())
