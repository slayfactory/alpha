#!/usr/bin/env python3
"""П-42, TK-681: ворота нового бинаря (ключи `natr_p=5`, `nf`, `ev/ea/el/eh`) и покрытие таблицы `FAST`. P&L не считается.
  python3 tk681_run.py gates              — G-bin, G-base (6 строк баз байт в байт, `tk674_run.cmd_gates`), md5 бинаря → равные (res/eqbins674.json)
  python3 tk681_run.py cov <каталог fast> — по каждому <окно>-<сторона>: строки `fast.csv` против `eps-key.csv` (sym, sig_ms построчно), доля непустых по столбцам → res/fastcov681.json"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.environ.setdefault("TK674_ROOT", "/data/p42-tk681")
BIN = ROOT + "/bin/alpha-tk681"
EQ_PREV = "/data/p42-tk677/res/eqbins674.json"


def cmd_gates(a):
    import tk674_run as m
    m.BIN = BIN
    m.cmd_gates(a)
    eq = json.load(open(EQ_PREV, encoding="utf-8"))
    md = m.bin_md5(BIN)
    eq = [md] + [x for x in eq if x != md]
    json.dump(eq, open(m.t.q("res", "eqbins674.json"), "w", encoding="utf-8"))
    m.say("равные бинари: %s" % ", ".join(x[:8] for x in eq))


def cov_one(d):
    """Сверка ключей и покрытие одного каталога: (число строк, {столбец: доля непустых}); расхождение ключей — ValueError."""
    with open(os.path.join(d, "eps-key.csv"), encoding="utf-8", newline="") as fk, open(os.path.join(d, "fast.csv"), encoding="utf-8", newline="") as ff:
        rk, rf = csv.reader(fk), csv.reader(ff)
        hk, hf = next(rk), next(rf)
        ks, kt = hk.index("sym"), hk.index("sig_ms")
        if hf[:2] != ["sym", "sig_ms"]:
            raise ValueError("%s: заголовок fast.csv %s" % (d, hf[:2]))
        cols = hf[2:]
        full = [0] * len(cols)
        n = 0
        for n, row in enumerate(rf, 1):
            key = next(rk, None)
            if key is None or key[ks] != row[0] or key[kt] != row[1]:
                raise ValueError("%s: строка %d — ключ %s против %s" % (d, n, key and (key[ks], key[kt]), row[:2]))
            for i, v in enumerate(row[2:]):
                if v != "":
                    full[i] += 1
        if next(rk, None) is not None:
            raise ValueError("%s: в fast.csv строк меньше, чем в eps-key.csv (%d)" % (d, n))
    return n, {c: (full[i] / n if n else 0.0) for i, c in enumerate(cols)}


def cmd_cov(a):
    res = {}
    for name in sorted(os.listdir(a.dir)):
        d = os.path.join(a.dir, name)
        if os.path.exists(os.path.join(d, "fast.csv")):
            n, share = cov_one(d)
            res[name] = dict(rows=n, share={c: round(v, 4) for c, v in share.items()})
            print("%s строк %d, столбцов %d, мин. покрытие %s" % (name, n, len(share), min(share.items(), key=lambda kv: kv[1])))
    out = os.path.join(ROOT, "res", "fastcov681.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("gates")
    c = sp.add_parser("cov")
    c.add_argument("dir")
    a = ap.parse_args()
    {"gates": cmd_gates, "cov": cmd_cov}[a.cmd](a)


if __name__ == "__main__":
    main()
