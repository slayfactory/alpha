#!/usr/bin/env python3
"""TK-744 (В-195): независимая проверка правила (б) «наторговка» на 532 сделках V0nt — по минутным свечам, без движка. Только srv.

  python3 tk744_rule_check.py      # → res/rule_b.json, res/rule_b.txt

Правило (src/breakout/tick.rs `fresh_ok`, (б)): среди закрытых минут t−K … t−1 (K = 120; минуты без данных не в счёт) закрытий строго выше уровня L и не дальше b = 0,5 % от него — не меньше K·50/100 = 60,
и ни одного закрытия на уровне или за ним (шорт: за уровнем — ниже). t — минута сигнала `sig_min`. Для каждой сделки считается доля, число закрытий за уровнем и допуск; результат сверяется с движком (V1 = 1 сделка из 532).
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk741_cluster as C  # noqa: E402
import tk744_lib as L  # noqa: E402

T = L.T
K, SHARE, B = 120, 50, 0.005


def main():
    a = T.load("nt", T.ALLW, with_sigsx=True)
    fh = T.load("fh0", T.ALLW, with_sigsx=False)
    in_v1 = {(fh["win"][i], fh["sym"][i], int(fh["sig_ms"][i])) for i in range(len(fh["pnl"]))}
    rows = []
    for i in range(len(a["pnl"])):
        w, sym, lv = a["win"][i], a["sym"][i], float(a["lvl"][i]) * 1e8   # свечи в e8
        t = int((int(a["sig_ms"][i]) - int(a["t0"][i])) // 60000)
        b = C.bars(w, sym)
        lo = max(0, t - K)
        cl, pres = b[3][lo:t], b[6][lo:t]
        c = cl[pres]
        n_in = int(((c > lv) & (c - lv <= lv * B)).sum())
        beyond = int((c <= lv).sum())
        rows.append(dict(win=w, sym=sym, t=t, n=int(len(c)), n_in=n_in, beyond=beyond, share=n_in / K * 100.0, ok=bool(beyond == 0 and n_in >= K * SHARE // 100),
                         v1=(w, sym, int(a["sig_ms"][i])) in in_v1))
    ok = [r for r in rows if r["ok"]]
    v1 = [r for r in rows if r["v1"]]
    sh = np.array([r["share"] for r in rows])
    out = ["Правило (б) по свечам: допущено %d из %d сделок V0nt; движок (V1) — %d; совпали по допуску: %d из %d" % (len(ok), len(rows), len(v1), sum(1 for r in rows if r["ok"] == r["v1"]), len(rows)),
           "Доля закрытий в полосе 0,5 %% (из 120 мин): медиана %.1f %%, 90-й процентиль %.1f %%, максимум %.1f %%; сделок с закрытием за уровнем в окне: %d; с долей ≥ 50 %%: %d" % (
               np.median(sh), np.percentile(sh, 90), sh.max(), sum(1 for r in rows if r["beyond"] > 0), int((sh >= 50).sum()))]
    out.append("Сделок V0nt с долей в полосе ≥ 10 / 20 / 30 / 50 %% (описательно): %s" % " / ".join(str(int((sh >= x).sum())) for x in (10, 20, 30, 50)))
    print("\n".join(out))
    os.makedirs(L.RES, exist_ok=True)
    json.dump(rows, open(L.RES + "/rule_b.json", "w"), ensure_ascii=False)
    open(L.RES + "/rule_b.txt", "w", encoding="utf-8").write("\n".join(out) + "\n")
    import sqlite3
    db = sqlite3.connect(L.ROOT + "/tk744.sqlite")
    db.executescript("DROP TABLE IF EXISTS t744_ruleb; CREATE TABLE t744_ruleb(win TEXT, sym TEXT, sig_min INTEGER, n_closes INTEGER, n_in INTEGER, beyond INTEGER, share REAL, ok_rule_b INTEGER, in_v1 INTEGER);")
    db.executemany("INSERT INTO t744_ruleb VALUES(?,?,?,?,?,?,?,?,?)", [(r["win"], r["sym"], r["t"], r["n"], r["n_in"], r["beyond"], r["share"], int(r["ok"]), int(r["v1"])) for r in rows])
    db.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
