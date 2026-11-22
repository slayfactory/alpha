#!/usr/bin/env python3
"""TK-686: окно июня 2026 (отложенная выборка, В-170) — дерево окна и проверка полноты. Движок не запускается, P&L нет.
  python3 tk686_june.py tree  --files <files.tsv> --universe <universe.json> --d0 2026-05-12 --d1 2026-06-30 --dl <dl> --old <дерево TK-621 wa> --out <окно>
  python3 tk686_june.py check --files <files.tsv> --universe <universe.json> --d0 … --d1 … --out <окно> --store <склад тиков> [--report <файл>]
`tree`: <окно>/{aggtrades,klines}/<МОНЕТА>/ — ссылки на уже лежащие файлы (как `tk599_prep.sh`: первый найденный из --dl, затем --old), плюс funding.csv из --old.
`check` (один проход): по листингу S3 — каждый файл (монета, сутки) есть в дереве, размер zip = размеру в листинге, минутка CSV не пустая, в складе тиков есть `.tk`
каждых суток aggTrades; содержимое склада не читается и не сверяется."""
import argparse
import datetime as D
import json
import os
import sys
from collections import defaultdict


def days(d0, d1):
    a, b = D.date.fromisoformat(d0), D.date.fromisoformat(d1)
    return [(a + D.timedelta(i)).isoformat() for i in range((b - a).days + 1)]


def load(a):
    files = defaultdict(dict)
    for line in open(a.files, encoding="utf-8"):
        kind, s, per, size, _ = line.rstrip("\n").split("\t")
        if kind in ("aggTrades", "klines1m"):
            files[(kind, s)][per] = int(size)
    return files, json.load(open(a.universe, encoding="utf-8"))


def cand(kind, s, d, a):
    if kind == "aggTrades":
        n = f"{s}-aggTrades-{d}.zip"
        return [f"{a.dl}/aggtrades/{s}/{n}", f"{a.old}/aggtrades/{s}/{n}"]
    return [f"{a.dl}/klines-csv/{s}/{d}.csv", f"{a.old}/klines/{s}/{d}.csv"]


def cmd_tree(a):
    files, uni = load(a)
    nk = nt = 0
    miss = []
    for s in uni:
        os.makedirs(f"{a.out}/klines/{s}", exist_ok=True)
        os.makedirs(f"{a.out}/aggtrades/{s}", exist_ok=True)
        for d in days(a.d0, a.d1):
            for kind in ("aggTrades", "klines1m"):
                if d not in files.get((kind, s), {}):
                    continue
                for p in cand(kind, s, d, a):
                    if os.path.exists(p) and os.path.getsize(p) > 0:
                        tgt = os.path.realpath(p)
                        dst = (f"{a.out}/aggtrades/{s}/{os.path.basename(tgt)}" if kind == "aggTrades" else f"{a.out}/klines/{s}/{d}.csv")
                        if not os.path.lexists(dst):
                            os.symlink(tgt, dst)
                        nt += kind == "aggTrades"
                        nk += kind == "klines1m"
                        break
                else:
                    miss.append((kind, s, d))
        f = f"{a.old}/klines/{s}/funding.csv"
        if os.path.exists(f) and not os.path.lexists(f"{a.out}/klines/{s}/funding.csv"):
            os.symlink(os.path.realpath(f), f"{a.out}/klines/{s}/funding.csv")
    print(f"дерево {a.out}: монет {len(uni)}, суток {len(days(a.d0, a.d1))}, сделок {nt}, минуток {nk}, нет файла {len(miss)}")
    for m in miss[:20]:
        print("НЕТ", *m)
    return 1 if miss else 0


def cmd_check(a):
    files, uni = load(a)
    exp = defaultdict(int)
    bad = defaultdict(list)
    ndays = days(a.d0, a.d1)
    zbytes = 0
    for s in uni:
        for d in ndays:
            for kind in ("aggTrades", "klines1m"):
                size = files.get((kind, s), {}).get(d)
                if size is None:
                    continue
                exp[kind] += 1
                if kind == "aggTrades":
                    p = f"{a.out}/aggtrades/{s}/{s}-aggTrades-{d}.zip"
                    if not os.path.exists(p):
                        bad["нет_zip"].append((s, d))
                        continue
                    sz = os.path.getsize(p)
                    zbytes += sz
                    if sz != size:
                        bad["размер_zip"].append((s, d, sz, size))
                    tk = f"{a.store}/{s}/{s}-{d}.tk"
                    if not os.path.exists(tk):
                        bad["нет_склада"].append((s, d))
                else:
                    p = f"{a.out}/klines/{s}/{d}.csv"
                    if not os.path.exists(p) or os.path.getsize(p) == 0:
                        bad["нет_минутки"].append((s, d))
    R = {"window": [a.d0, a.d1], "contracts": len(uni), "expected": dict(exp), "zip_bytes_gb": round(zbytes / 1e9, 3),
         "problems": {k: len(v) for k, v in bad.items()}, "examples": {k: v[:5] for k, v in bad.items()}}
    print(json.dumps(R, ensure_ascii=False))
    if a.report:
        json.dump(R, open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 1 if bad else 0


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    for name in ("tree", "check"):
        q = sp.add_parser(name)
        q.add_argument("--files", required=True)
        q.add_argument("--universe", required=True)
        q.add_argument("--d0", required=True)
        q.add_argument("--d1", required=True)
        q.add_argument("--out", required=True)
        if name == "tree":
            q.add_argument("--dl", required=True)
            q.add_argument("--old", required=True)
        else:
            q.add_argument("--store", required=True)
            q.add_argument("--report")
    a = p.parse_args()
    return {"tree": cmd_tree, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
