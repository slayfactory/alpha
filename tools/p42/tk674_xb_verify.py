#!/usr/bin/env python3
"""TK-674 PS-995 (ответ критика 16:05): файлы суток «обрыва данных биржи» — sha256 с диска против опубликованного Binance `.CHECKSUM`, целость zip, последняя метка времени файла.

  TK674XB_ROOT=/data/p42-tk674c-xb /data/tk592/venv/bin/python tk674_xb_verify.py [--workers 8]   → res/xb-verify.json (+ печать итога)

Строки обрыва — те же, что в `tk674_xb.py controls` (bd = NaN, не дыра 08.10, не 404, файл есть, снимков в [σ − 3600, σ] нет); проверяется каждый файл суток (σ и σ − 1 ч) этих строк. Код выхода ≠ 0, если хоть один файл не совпал / битый."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import glob
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_xb as xb  # noqa: E402

BASE = "https://data.binance.vision/data/futures/um/daily/bookDepth"


def gap_pairs():
    """→ ({окно: строк обрыва}, {(монета, сутки): число строк обрыва}); та же логика, что `cmd_controls` (охрана NaN)."""
    sys.path.insert(0, xb.C671)
    import pandas as pd
    import tk671_bd as bd
    covs = glob.glob(xb.BOOK + "/coverage-*.csv") + [xb.q("res", "xb-coverage.csv")]
    st404 = {k for k, v in xb.coverage_status([c for c in covs if os.path.exists(c)]).items() if v == "404"}
    per_w, pairs = {}, {}
    for w in xb.WINS:
        rws = pd.read_parquet(xb.rows_path(w))
        sec = bd.row_seconds(rws, w)
        fn = pd.read_parquet(xb.q("fam", "bd", "%s-%s.parquet" % (w, xb.SN)))
        nan_rows = fn.isna().any(axis=1).to_numpy()
        day = np.floor(sec / 86400.0).astype(np.int64)
        d2s = np.floor((sec - 3600.0) / 86400.0).astype(np.int64)
        on_hole = np.isin(day, np.array(bd.HOLES_D, dtype=np.int64))
        symv = rws["sym"].astype(str).to_numpy()
        n = 0
        for i in np.flatnonzero(nan_rows & ~on_hole):
            ds = sorted({xb.day_str(day[i]), xb.day_str(d2s[i])})
            if any((symv[i], d) in st404 for d in ds):
                continue
            tm = bd.load_sym_fast(xb.ZIP, symv[i], ds)
            if tm is None or not xb.has_snapshot_in_hour(tm[0], sec[i]):
                n += 1
                for d in ds:
                    pairs[(symv[i], d)] = pairs.get((symv[i], d), 0) + 1
        per_w[w] = n
    return per_w, pairs


def remote_sha(sym, day, tries=3):
    """sha256 из `<файл>.zip.CHECKSUM` Binance («<sha256>  <имя>») → (хеш | None, статус)."""
    name = "%s-bookDepth-%s.zip.CHECKSUM" % (sym, day)
    url = "%s/%s/%s" % (BASE, urllib.parse.quote(sym), urllib.parse.quote(name))
    err = "err"
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read().decode().split()[0].lower(), "ok"
        except urllib.error.HTTPError as e:
            err = "http %d" % e.code
            if e.code == 404:
                break
        except Exception as e:  # сеть, таймаут
            err = "err:%s" % type(e).__name__
    return None, err


def check_one(pair):
    sym, day = pair
    path = os.path.join(xb.ZIP, sym, "%s-bookDepth-%s.zip" % (sym, day))
    out = dict(sym=sym, day=day, path=path, exists=os.path.exists(path))
    if not out["exists"]:
        return out
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    out["sha256_disk"] = h.hexdigest()
    out["sha256_binance"], out["checksum_status"] = remote_sha(sym, day)
    out["match"] = out["sha256_binance"] == out["sha256_disk"]
    with zipfile.ZipFile(path) as z:
        out["testzip"] = z.testzip()                          # None — целый
        name = z.namelist()[0]
        last = ""
        with z.open(name) as fh:
            for line in fh:
                if line[:4].isdigit():
                    last = line[:19].decode()
        out["last_ts"] = last
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    per_w, pairs = gap_pairs()
    print("строк обрыва по окнам: %s; пар (монета, сутки): %d, монет: %d" % (per_w, len(pairs), len({s for s, _ in pairs})), flush=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(check_one, sorted(pairs)))
    bad = [r for r in res if not r["exists"] or not r.get("match") or r.get("testzip") is not None]
    last = {}
    for r in res:
        last.setdefault((r["day"], r.get("last_ts", "?")[11:16]), []).append(r["sym"])
    rep = dict(rows_by_window=per_w, pairs=len(pairs), coins=len({s for s, _ in pairs}), mismatched_or_broken=len(bad),
               last_timestamp_by_day={"%s %s" % k: len(v) for k, v in sorted(last.items())}, files=res)
    json.dump(rep, open(xb.q("res", "xb-verify.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("файлов проверено %d: sha256 совпал с Binance %d, не совпал / нет файла / битый zip %d" % (len(res), sum(1 for r in res if r.get("match") and r.get("testzip") is None), len(bad)), flush=True)
    for k, v in sorted(rep["last_timestamp_by_day"].items()):
        print("  последняя метка %s — файлов %d" % (k, v), flush=True)
    for r in bad[:10]:
        print("  ПРОБЛЕМА:", {k: r.get(k) for k in ("sym", "day", "exists", "match", "checksum_status", "testzip")}, flush=True)
    if bad:
        raise SystemExit(5)


if __name__ == "__main__":
    main()
