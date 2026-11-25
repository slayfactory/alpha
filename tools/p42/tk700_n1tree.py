#!/usr/bin/env python3
"""TK-700 (приказ координатора 25.11, дельта TK-692): дерево окна n1 (T0 = 25.10, зачёт 14.11–24.11) — ссылки на прежние деревья TK-621 (≤ 13.11) и общий `fwd/dl` TK-710 (14–24.11). Без движка и без P&L.

  python3 tk700_n1tree.py tree [--dl /data/p42-tk616/fwd/dl] [--rest /data/p42-tk616/fwd/rest]   # <root>/tree/{klines,aggtrades}/<контракт>/… + <root>/nofile.tsv; каталог уже есть — отказ
  python3 tk700_n1tree.py check                                                                  # у каждого контракта окна есть каталог; суток после hi и файлов вне вселенной нет

Контракты — `/data/p42-tk700/universe/n1.txt` (c539 ∩ список TK-710 на 24.11, `tk700_win.py universe --apply n1`). Сборка ссылок — `tk626_prep.build_tree` (как у деревьев TK-710 / точки вперёд), `breakout convert` — шаг драйвера `tk700_n1.sh`."""
from __future__ import annotations

import argparse
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk626_prep as pr  # noqa: E402
import tk700_win as w7  # noqa: E402


def geometry():
    s = w7.spec("n1")
    syms = w7.read_symbols(w7.universe_file("n1"))
    days = pr.day_list(s["t0"], datetime.date.fromisoformat(s["hi"]))
    return s, syms, days


def cmd_tree(a):
    s, syms, days = geometry()
    wdir = os.path.join(s["root"], "tree")
    if os.path.exists(wdir):
        raise SystemExit("каталог %s уже есть: дерево не перестраивается поверх" % wdir)
    kl, tr, fund = pr.sources(a.dl, a.rest)
    cnt, miss = pr.build_tree(wdir, syms, days, kl, tr, fund)
    with open(os.path.join(s["root"], "nofile.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.writelines("\t".join(r) + "\n" for r in miss)
    print("n1 T0 %s: сутки %s…%s (%d), контрактов %d, минуток %d, сделок %d, funding %d, нет файла %d" % (s["t0"], days[0], days[-1], len(days), len(syms), cnt["klines"], cnt["aggtrades"], cnt["funding"], len(miss)))
    return 0


def cmd_check(a):
    s, syms, days = geometry()
    wdir = os.path.join(s["root"], "tree")
    for sub in ("aggtrades", "klines"):
        lack = [x for x in syms if not os.path.isdir(os.path.join(wdir, sub, x))]
        if lack:
            raise SystemExit("n1: у %d контрактов нет каталога %s, например: %s" % (len(lack), sub, lack[:3]))
    last, late, foreign = days[-1], 0, 0
    for sub in ("aggtrades", "klines"):
        have = set(os.listdir(os.path.join(wdir, sub)))
        foreign += len(have - set(syms))
        for x in syms:
            for fn in os.listdir(os.path.join(wdir, sub, x)):
                d = fn.split("-aggTrades-")[-1][:10] if sub == "aggtrades" else fn[:10]
                late += d.count("-") == 2 and d > last
    if late or foreign:
        raise SystemExit("n1: файлов позже %s — %d, контрактов вне вселенной — %d" % (last, late, foreign))
    print("n1: ворота дерева — контрактов %d, суток %s…%s, позже %s и чужих контрактов нет" % (len(syms), days[0], last, last))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    t = sp.add_parser("tree")
    t.add_argument("--dl", default="/data/p42-tk616/fwd/dl")
    t.add_argument("--rest", default="/data/p42-tk616/fwd/rest")
    sp.add_parser("check")
    a = ap.parse_args(argv)
    return {"tree": cmd_tree, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
