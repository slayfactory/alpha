#!/usr/bin/env python3
"""TK-742: сверка минуток вкладки «Сделки»: data/trades/<№>.json (целые столбцы, tk741_pagedata.py pack_bars) против кэша минуток окна (.b42), бит в бит. Только srv, только чтение.

  /opt/venv/bin/python tk742_verify_bars.py <каталог страницы /data/pages/ml>

Для каждой сделки: те же минуты [вход − 3 сут … выход + 1 сут] (с обрезкой по окну); цены open/high/low/close — точное равенство целых e8 (после умножения шагов на tick),
минуты без сделок — те же номера; оборот — равенство в центах; время первой минуты == старт окна + номер минуты. Код разбора здесь — независимая запись формата (как в клиенте bars.ts)."""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk741_cluster as C  # noqa: E402
import tk741_lib as T  # noqa: E402
import tk741_pagedata as P  # noqa: E402


def decode(j):
    n, tick, gap = j["n"], j["tick"], set(j["g"])
    o_, h_, l_, c_, q_ = (np.zeros(n, np.int64) for _ in range(5))
    prev = 0
    for k in range(n):
        if k in gap:
            continue
        o = prev + j["o"][k]
        c = o + j["c"][k]
        o_[k], c_[k] = o * tick, c * tick
        h_[k] = (max(o, c) + j["h"][k]) * tick
        l_[k] = (min(o, c) - j["l"][k]) * tick
        q_[k] = j["q"][k]
        prev = c
    return o_, h_, l_, c_, q_, np.array(sorted(gap), np.int64)


def main(page):
    rows = json.load(open(T.RES + "/trades.json", encoding="utf-8"))
    a = T.load("nt", T.ALLW, with_sigsx=True)
    t0 = {w: int(a["t0"][np.nonzero(a["win"] == w)[0][0]]) for w in T.ALLW}
    bad, nbars, nbytes = [], 0, 0
    for rw in rows:
        fn = "%s/data/trades/%d.json" % (page, rw["i"])
        nbytes += os.path.getsize(fn)
        j = json.load(open(fn, encoding="utf-8"))
        b = C.bars(rw["win"], rw["sym"])
        n = len(b[0])
        m0, m1 = max(0, rw["entry_min"] - P.PRE_MIN), min(n - 1, rw["exit_min"] + P.POST_MIN)
        sl = slice(m0, m1 + 1)
        pres = b[6][sl].astype(bool)
        o, h, l, c, q, gap = decode(j)
        ok = j["n"] == m1 - m0 + 1 and j["t0"] == t0[rw["win"]] + m0 * 60000
        ok = ok and np.array_equal(gap, np.nonzero(~pres)[0])
        for got, k in ((o, 0), (h, 1), (l, 2), (c, 3)):
            ok = ok and np.array_equal(got[pres], b[k][sl][pres].astype(np.int64))
        ok = ok and np.array_equal(q[pres], np.rint(b[4][sl][pres] / 1e6).astype(np.int64))
        ok = ok and (rw["entry_min"] - m0) >= 0 and (rw["exit_min"] - m0) < j["n"] and pres[rw["entry_min"] - m0]
        nbars += int(pres.sum())
        if not ok:
            bad.append(rw["i"])
    print("сделок %d, минуток со сделками %d, файлов %.1f МБ; расхождений: %d %s" % (len(rows), nbars, nbytes / 1e6, len(bad), bad[:10]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
