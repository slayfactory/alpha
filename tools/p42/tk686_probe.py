#!/usr/bin/env python3
"""TK-686: проверка докачки истории 01.01–30.06.2026 для П-42 — только листинг S3 data.binance.vision и HEAD; ничего не качает.
  python3 tk686_probe.py list   <out_dir> [--d0 2025-12-01] [--d1 2026-11-13] [--threads 6]   # out/files.tsv, out/dirs.txt, out/exchange.json
  python3 tk686_probe.py head   <out_dir> [--days 15]                                         # out/head.tsv: HEAD (статус, длина) на 15-е число каждого месяца янв–июн
  python3 tk686_probe.py plan   <out_dir> --d0 D --d1 D --score0 D --plan <dir> [--tree /data/p42-tk621/wa]   # план докачки окна: missing.tsv для tk621_dl.py
  python3 tk686_probe.py report <out_dir> --base <symbols-all.txt> --tk621-files <files.tsv> --store <tick-store> [--tk621-universe <contracts.csv>]
Листинг — как `tk621_list.py` (тот же S3-эндпоинт, `start-after`, ≤ 1000 ключей за страницу). Виды: aggTrades, klines 1m, premiumIndexKlines 1m,
metrics, bookDepth (суточные), fundingRate (месячные), месячные aggTrades / klines 1m (запасной источник). Вселенная H1 — по правилу TK-621:
USDT-перпетуалы с опубликованными aggTrades хотя бы в одни сутки окна."""
import argparse
import csv
import datetime as D
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
CDN = "https://data.binance.vision"
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
BASE = "data/futures/um/"
# вид -> (daily|monthly, каталог, префикс имени)
KINDS = {
    "aggTrades": ("daily", "aggTrades/{s}/", "{s}-aggTrades-"),
    "klines1m": ("daily", "klines/{s}/1m/", "{s}-1m-"),
    "premium1m": ("daily", "premiumIndexKlines/{s}/1m/", "{s}-1m-"),
    "metrics": ("daily", "metrics/{s}/", "{s}-metrics-"),
    "bookDepth": ("daily", "bookDepth/{s}/", "{s}-bookDepth-"),
    "fundingRate": ("monthly", "fundingRate/{s}/", "{s}-fundingRate-"),
    "aggTradesM": ("monthly", "aggTrades/{s}/", "{s}-aggTrades-"),
    "klines1mM": ("monthly", "klines/{s}/1m/", "{s}-1m-"),
}
PER_RE = re.compile(r"-(\d{4}-\d{2}(?:-\d{2})?)\.zip$")


def s3_list(prefix, delimiter=None, start_after=None):
    token = None
    while True:
        q = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if start_after:
            q["start-after"] = start_after
        if delimiter:
            q["delimiter"] = delimiter
        if token:
            q["continuation-token"] = token
        for attempt in range(8):
            try:
                with urllib.request.urlopen(f"{S3}?{urllib.parse.urlencode(q)}", timeout=60) as r:
                    root = ET.fromstring(r.read())
                break
            except OSError:
                if attempt == 7:
                    raise
                time.sleep(2 * (attempt + 1))
        for cp in root.findall("s:CommonPrefixes/s:Prefix", NS):
            yield ("dir", cp.text, 0, "")
        for c in root.findall("s:Contents", NS):
            yield ("file", c.find("s:Key", NS).text, int(c.find("s:Size", NS).text), c.find("s:ETag", NS).text.strip('"'))
        if root.find("s:IsTruncated", NS).text == "true":
            token = root.find("s:NextContinuationToken", NS).text
        else:
            break


def list_kind(kind, s, d0, d1):
    """[(период, размер, etag)] — файлы вида kind для монеты s; период ISO-день или месяц, d0 ≤ период ≤ d1 (для месяца — по первым 7 символам)."""
    freq, dirp, namep = KINDS[kind]
    pre = BASE + freq + "/" + dirp.format(s=s)
    nm = namep.format(s=s)
    lo = d0 if freq == "daily" else d0[:7]
    hi = d1 if freq == "daily" else d1[:7]
    out = []
    for k, key, size, etag in s3_list(pre, start_after=f"{pre}{nm}{lo}"):
        m = PER_RE.search(key)
        if k == "file" and m and key.endswith(".zip") and key.startswith(pre + nm) and lo <= m.group(1) <= hi:
            out.append((m.group(1), size, etag))
    return out


def cmd_list(a):
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    dirs = sorted(p.rstrip("/").split("/")[-1] for k, p, _, _ in s3_list(BASE + "daily/aggTrades/", "/") if k == "dir")
    syms = [s for s in dirs if s.endswith("USDT") and "_" not in s]
    open(f"{a.out}/dirs.txt", "w", encoding="utf-8").write("\n".join(dirs) + "\n")
    print(f"dirs {len(dirs)}, USDT-перпетуалов {len(syms)}, {time.time() - t0:.0f} с", flush=True)
    with ThreadPoolExecutor(a.threads) as pool:
        agg = dict(zip(syms, pool.map(lambda s: list_kind("aggTrades", s, a.d0, a.d1), syms)))
    # вселенная под остальные виды — монеты с сутками aggTrades в окне 17.12.2025…30.06.2026 (прогрев + H1)
    need = sorted(s for s, v in agg.items() if any("2025-12-17" <= d <= "2026-06-30" for d, _, _ in v))
    print(f"aggTrades: {sum(len(v) for v in agg.values())} файлов у {sum(1 for v in agg.values() if v)} монет; "
          f"нужны остальные виды для {len(need)} монет, {time.time() - t0:.0f} с", flush=True)
    rows = [("aggTrades", s, d, size, etag) for s, v in agg.items() for d, size, etag in v]
    for kind in KINDS:
        if kind == "aggTrades":
            continue
        with ThreadPoolExecutor(a.threads) as pool:
            res = list(pool.map(lambda s: list_kind(kind, s, a.d0, a.d1), need))
        n = 0
        for s, v in zip(need, res):
            rows += [(kind, s, d, size, etag) for d, size, etag in v]
            n += len(v)
        print(f"{kind}: {n} файлов, {time.time() - t0:.0f} с", flush=True)
    with open(f"{a.out}/files.tsv", "w", encoding="utf-8") as f:
        for r in rows:
            f.write("\t".join(map(str, r)) + "\n")
    try:
        info = json.load(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=60))
        ex = {s["symbol"]: {k: s.get(k) for k in ("status", "contractType", "underlyingType", "onboardDate", "deliveryDate")}
              for s in info["symbols"]}
    except OSError as e:
        ex = {"error": str(e)}
    json.dump(ex, open(f"{a.out}/exchange.json", "w"), ensure_ascii=False)
    print(f"готово, строк {len(rows)}, exchangeInfo {len(ex)}, {time.time() - t0:.0f} с", flush=True)


def cmd_head(a):
    rows = {}
    for line in open(f"{a.out}/files.tsv", encoding="utf-8"):
        kind, s, per, size, _ = line.rstrip("\n").split("\t")
        rows[(kind, s, per)] = int(size)
    samples = ["BTCUSDT", "ETHUSDT", "1000PEPEUSDT"]
    out = []
    for kind, (freq, dirp, namep) in KINDS.items():
        for mo in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"):
            per = f"{mo}-{a.days:02d}" if freq == "daily" else mo
            for s in samples:
                url = f"{CDN}/{BASE}{freq}/{dirp.format(s=s)}{namep.format(s=s)}{per}.zip"
                st, ln = None, None
                for attempt in range(3):
                    try:
                        req = urllib.request.Request(urllib.parse.quote(url, safe=":/"), method="HEAD")
                        with urllib.request.urlopen(req, timeout=30) as r:
                            st, ln = r.status, int(r.headers.get("Content-Length", -1))
                        break
                    except urllib.error.HTTPError as e:
                        st, ln = e.code, None
                        break
                    except OSError:
                        time.sleep(1 + attempt)
                lst = rows.get((kind, s, per))
                out.append((kind, s, per, st, ln if ln is not None else "", lst if lst is not None else "",
                            "" if ln is None or lst is None else int(ln == lst)))
                time.sleep(0.2)
    with open(f"{a.out}/head.tsv", "w", encoding="utf-8") as f:
        f.write("kind\tsymbol\tperiod\thttp\tcontent_length\tlisting_size\tsame\n")
        for r in out:
            f.write("\t".join(map(str, r)) + "\n")
    print(f"HEAD: {len(out)} запросов, 200: {sum(1 for r in out if r[3] == 200)}, "
          f"длина = листинг: {sum(1 for r in out if r[6] == 1)} из {sum(1 for r in out if r[6] != '')}")


def months(d0, d1):
    a, b = D.date.fromisoformat(d0), D.date.fromisoformat(d1)
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield f"{y}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def cmd_report(a):
    base = set(open(a.base, encoding="utf-8").read().replace("\n", ",").split(",")) - {""}
    ex = json.load(open(f"{a.out}/exchange.json", encoding="utf-8"))
    tradfi_now = {s for s, v in ex.items() if isinstance(v, dict) and v.get("contractType") == "TRADIFI_PERPETUAL"}
    tradfi_base = set()
    if a.tk621_universe:
        for r in csv.DictReader(open(a.tk621_universe, encoding="utf-8")):
            if r["tradfi"] == "1":
                tradfi_base.add(r["symbol"])
    files = defaultdict(dict)  # (kind, sym) -> {период: size}
    for line in open(f"{a.out}/files.tsv", encoding="utf-8"):
        kind, s, per, size, _ = line.rstrip("\n").split("\t")
        files[(kind, s)][per] = int(size)
    syms_agg = sorted({s for (k, s) in files if k == "aggTrades"})
    h1 = [f"2026-{m:02d}" for m in range(1, 7)]
    uni_h1 = sorted(s for s in syms_agg if any(d[:7] in h1 for d in files[("aggTrades", s)]))
    uni_h1_set = set(uni_h1)
    R = {"universe": {}, "month": {}, "gaps": {}, "store": {}}
    first = {s: min(files[("aggTrades", s)]) for s in syms_agg}
    last = {s: max(files[("aggTrades", s)]) for s in syms_agg}
    R["universe"] = {
        "exchangeInfo_now": len([s for s, v in ex.items() if isinstance(v, dict) and v.get("contractType") in ("PERPETUAL", "TRADIFI_PERPETUAL")
                                 and s.endswith("USDT") and v.get("status") == "TRADING"]),
        "tradfi_now_exchangeInfo": len(tradfi_now),
        "base775": len(base), "base_tradfi": len(tradfi_base),
        "U_H1_rule": len(uni_h1), "U_H1_in_base": len(uni_h1_set & base), "U_H1_not_in_base": len(uni_h1_set - base),
        "base_not_in_H1": len(base - uni_h1_set),
        "U_H1_tradfi_in_base": len(uni_h1_set & tradfi_base),
        "U_H1_not_in_base_last_day_before_0701": len([s for s in uni_h1_set - base if last[s] < "2026-07-01"]),
        "U_H1_not_in_base_listing": sorted(uni_h1_set - base)[:80],
    }
    for mo in months("2026-01-01", "2026-11-30"):
        row = {}
        for kind in KINDS:
            n = b = nb = bb = sy = syb = 0
            sset = set()
            for (k, s), v in files.items():
                if k != kind:
                    continue
                for per, size in v.items():
                    if per[:7] == mo:
                        n += 1
                        b += size
                        sset.add(s)
                        if s in base:
                            nb += 1
                            bb += size
            row[kind] = {"files": n, "gb": round(b / 1e9, 3), "syms": len(sset), "files_base": nb, "gb_base": round(bb / 1e9, 3),
                         "syms_base": len(sset & base)}
        R["month"][mo] = row
    # что докачивать: прогрев (с 17.12.2025, 14 суток до 01.01) + H1; у монет базы 17–30.06 уже на диске (TK-621 качал с 17.06)
    def need(kind, only_base):
        n = b = nd = 0
        for (k, s), v in files.items():
            if k != kind or s not in uni_h1_set or (only_base and s not in base):
                continue
            for per, size in v.items():
                if "2025-12-17" <= per <= "2026-06-30" and not (s in base and per >= "2026-06-17"):
                    n += 1
                    b += size
        return {"files": n, "gb": round(b / 1e9, 3)}
    R["need"] = {kind: {"U_H1": need(kind, False), "base_only": need(kind, True)} for kind in ("aggTrades", "klines1m")}
    for kind in ("premium1m", "metrics", "bookDepth", "fundingRate", "aggTradesM", "klines1mM"):
        n = b = 0
        for (k, s), v in files.items():
            if k == kind and s in uni_h1_set:
                for per, size in v.items():
                    if "2026-01" <= per[:7] <= "2026-06":
                        n += 1
                        b += size
        R["need"][kind] = {"U_H1": {"files": n, "gb": round(b / 1e9, 3)}}
    # провалы внутри жизни контракта (первый…последний день листинга окна H1, вид aggTrades и klines1m)
    for kind in ("aggTrades", "klines1m"):
        miss = 0
        exp = 0
        for s in uni_h1:
            ds = [d for d in files.get((kind, s), {}) if d[:7] in h1]
            if not ds:
                continue
            a0, a1 = D.date.fromisoformat(min(ds)), D.date.fromisoformat(max(ds))
            n = (a1 - a0).days + 1
            exp += n
            miss += n - len(ds)
        R["gaps"][kind] = {"expected_days_between_first_last": exp, "missing": miss}
    # склад тиков: размер .tk / размер zip тех же (монета, сутки) по TK-621 files.tsv
    zsz = {}
    for line in open(a.tk621_files, encoding="utf-8"):
        kind, s, per, size, _ = line.rstrip("\n").split("\t")
        if kind == "aggTrades":
            zsz[(s, per)] = int(size)
    by = defaultdict(lambda: [0, 0, 0])
    tot = [0, 0]
    for d in os.scandir(a.store):
        if not d.is_dir():
            continue
        for f in os.scandir(d.path):
            m = re.search(r"-(\d{4}-\d{2}-\d{2})\.tk$", f.name)
            if not m:
                continue
            sz = f.stat().st_size
            tot[0] += 1
            tot[1] += sz
            z = zsz.get((d.name, m.group(1)))
            if z:
                x = by[m.group(1)[:7]]
                x[0] += 1
                x[1] += sz
                x[2] += z
    R["store"] = {"files": tot[0], "bytes": tot[1],
                  "by_month": {mo: {"files": v[0], "tk_bytes": v[1], "zip_bytes": v[2], "ratio_tk_over_zip": round(v[1] / v[2], 4)} for mo, v in sorted(by.items()) if v[2]}}
    json.dump(R, open(f"{a.out}/report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(R["universe"], ensure_ascii=False)[:1500])
    print("gaps", R["gaps"])
    print("store", R["store"])


def cmd_plan(a):
    """План докачки окна: вселенная по правилу TK-621 (aggTrades хотя бы в одни сутки [score0, d1]), файлы [d0, d1], уже лежащее — по дереву --tree."""
    files = defaultdict(dict)
    for line in open(f"{a.out}/files.tsv", encoding="utf-8"):
        kind, s, per, size, etag = line.rstrip("\n").split("\t")
        if kind in ("aggTrades", "klines1m"):
            files[(kind, s)][per] = (int(size), etag)
    skip = set(open(a.exclude, encoding="utf-8").read().split()) if a.exclude else set()
    uni = sorted(s for (k, s), v in files.items() if k == "aggTrades" and s not in skip and any(a.score0 <= d <= a.d1 for d in v))
    os.makedirs(a.plan, exist_ok=True)
    rows, tot = [], defaultdict(lambda: [0, 0, 0, 0])  # kind -> [нужно файлов, байт, лежит файлов, байт лежит]
    for s in uni:
        for kind, nm, tdir, tname in (("aggTrades", "aggTrades", "aggtrades", "{s}-aggTrades-{d}.zip"), ("klines1m", "klines", "klines", "{d}.csv")):
            for d, (size, etag) in sorted(files.get((kind, s), {}).items()):
                if not a.d0 <= d <= a.d1:
                    continue
                t = tot[kind]
                t[0] += 1
                t[1] += size
                if os.path.exists(os.path.join(a.tree, tdir, s, tname.format(s=s, d=d))):
                    t[2] += 1
                    t[3] += size
                else:
                    rows.append((nm, s, d, size, etag))
    with open(f"{a.plan}/missing.tsv", "w", encoding="utf-8") as f:
        for r in rows:
            f.write("\t".join(map(str, r)) + "\n")
    nd = (D.date.fromisoformat(a.d1) - D.date.fromisoformat(a.d0)).days + 1
    new = defaultdict(lambda: [0, 0])
    for r in rows:
        new[r[0]][0] += 1
        new[r[0]][1] += r[3]
    agg_b, kl_b = new["aggTrades"][1], new["klines"][1]
    store_b = agg_b * a.store_ratio
    csv_b = kl_b * a.csv_ratio
    cache_b = 2.6e9 * nd / 74 * len(uni) / 566
    R = {"window": [a.d0, a.d1], "score0": a.score0, "days": nd, "contracts": len(uni),
         "listing": {k: {"files": v[0], "gb": round(v[1] / 1e9, 3)} for k, v in tot.items()},
         "on_disk": {k: {"files": v[2], "gb": round(v[3] / 1e9, 3)} for k, v in tot.items()},
         "to_download": {k: {"files": v[0], "gb": round(v[1] / 1e9, 3)} for k, v in new.items()},
         "model_gb": {"store_new": round(store_b / 1e9, 2), "csv_new": round(csv_b / 1e9, 2), "cache": round(cache_b / 1e9, 2)},
         "growth_gib_zips_only": round((agg_b + kl_b) / 2**30, 1),
         "growth_gib_total": round((agg_b + kl_b + store_b + csv_b + cache_b) / 2**30, 1)}
    json.dump(R, open(f"{a.plan}/plan.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(uni, open(f"{a.plan}/universe.json", "w"))
    print(json.dumps(R, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "head", "report", "plan"):
        q = sp.add_parser(name)
        q.add_argument("out")
        if name == "list":
            q.add_argument("--d0", default="2025-12-01")
            q.add_argument("--d1", default="2026-11-13")
            q.add_argument("--threads", type=int, default=6)
        if name == "head":
            q.add_argument("--days", type=int, default=15)
        if name == "plan":
            q.add_argument("--d0", required=True)
            q.add_argument("--d1", required=True)
            q.add_argument("--score0", required=True)
            q.add_argument("--tree", default="/data/p42-tk621/wa")
            q.add_argument("--plan", required=True)
            q.add_argument("--exclude", help="файл со списком монет (по одной в слове), которых во вселенной нет (TradFi, В-175)")
            q.add_argument("--store-ratio", type=float, default=0.2141)
            q.add_argument("--csv-ratio", type=float, default=3.318)
        if name == "report":
            q.add_argument("--base", required=True)
            q.add_argument("--tk621-files", required=True)
            q.add_argument("--tk621-universe")
            q.add_argument("--store", required=True)
    a = p.parse_args()
    {"list": cmd_list, "head": cmd_head, "report": cmd_report, "plan": cmd_plan}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
