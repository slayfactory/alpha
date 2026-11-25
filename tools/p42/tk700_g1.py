#!/usr/bin/env python3
"""TK-700, ворота G1: ключи строк базы по дампу `P42_SIGSX_ONLY=1` (варианты L_ind / S_ind / D_ind, `tk700_sig.sh <окно> ind`) = ключи сделок ∪ `entryrej` двойников TK-671
(`/data/p42-tk671/gate-pf/<окно>/out/<вариант>/<side>-{trades,entryrej}.csv`) на wa / wb / wc. На отложенной выборке двойников нет (они считают P&L), поэтому база строится по дампу.

  python3 tk700_g1.py --dumps /data/p42-tk700/gate/g1 [--wins wa,wb,wc] [--out g1.json]
Ключ — (sym, sig_ms); сравниваются множества. Совпали на всех (окно, вариант, сторона), используемых в `tk671_lvl.py` (L_ind/long, S_ind/short, D_ind/short) → ok.
Расходящиеся ключи (до 20 на проверку) попадают в отчёт; печатает только счётчики и ключи, сумм P&L нет."""
from __future__ import annotations

import argparse
import json
import os
import sys

TWIN = "/data/p42-tk671/gate-pf/%s/out"
C539 = "/data/p42-tk621/universe/symbols-crypto.txt"                    # вселенная поиска: деревья wa / wb / wc после В-175 без TradFi, двойники считались ДО (775 контрактов) — сравнение только по c539
USED = (("L_ind", "long"), ("S_ind", "short"), ("D_ind", "short"))      # как в tk671_lvl.main: p60n2 ← L_ind / S_ind, lxD ← D_ind
ALSO = (("L_ind", "short"), ("S_ind", "long"), ("D_ind", "long"))       # справочно: не используются базой


def read_keys(path):
    import pyarrow as pa
    import pyarrow.csv as pacsv
    t = pacsv.read_csv(path, convert_options=pacsv.ConvertOptions(include_columns=["sym", "sig_ms"], column_types={"sym": pa.string()}))
    d = t.to_pydict()
    return set(zip(d["sym"], d["sig_ms"]))


def c539():
    with open(C539, encoding="utf-8") as f:
        return set(f.read().replace(",", " ").split())


def check(dumps, win, variant, side, twin_root=TWIN, universe=None):
    uni = universe if universe is not None else c539()
    dump = os.path.join(dumps, win, "dumps", variant, "%s-sigsx.csv" % side)
    a = {k for k in read_keys(dump) if k[0] in uni}
    tw = os.path.join(twin_root % win, variant)
    b0 = read_keys(os.path.join(tw, "%s-trades.csv" % side))
    b = {k for k in b0 if k[0] in uni}
    n_tr = len(b)
    rj = os.path.join(tw, "%s-entryrej.csv" % side)
    n_rj = 0
    if os.path.exists(rj):
        r = {k for k in read_keys(rj) if k[0] in uni}
        n_rj = len(r)
        b |= r
    only_dump, only_twin = sorted(a - b), sorted(b - a)
    return {"win": win, "variant": variant, "side": side, "dump_keys": len(a), "twin_trades": n_tr, "twin_entryrej": n_rj, "twin_union": len(b),
            "only_dump": len(only_dump), "only_twin": len(only_twin), "only_dump_sample": only_dump[:20], "only_twin_sample": only_twin[:20], "equal": a == b}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dumps", required=True)
    ap.add_argument("--wins", default="wa,wb,wc")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    rows = []
    for w in a.wins.split(","):
        for v, s in USED + ALSO:
            r = check(a.dumps, w, v, s)
            r["used"] = (v, s) in USED
            rows.append(r)
            print("%s %s %s: дамп %d, двойник %d (сделки %d + entryrej %d), только в дампе %d, только у двойника %d → %s%s" % (
                w, v, s, r["dump_keys"], r["twin_union"], r["twin_trades"], r["twin_entryrej"], r["only_dump"], r["only_twin"], "РАВНЫ" if r["equal"] else "РАСХОДЯТСЯ", "" if r["used"] else " (справочно)"))
    ok = all(r["equal"] for r in rows if r["used"])
    out = {"gate": "G1", "ok": ok, "checks": rows}
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    print("G1:", "ПРОЙДЕНЫ" if ok else "НЕ ПРОЙДЕНЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
