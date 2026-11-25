#!/usr/bin/env python3
"""TK-700, ворота прогрева 20 суток: сигналы окна `wa20` (T0 = 11.06, дамп `tk700_wa20.sh`) против сигналов `wa` (T0 = 17.06, прогрев 14 суток) на общем отрезке — строки с
sig_ms ≥ суток 20 окна wa20 (= суток 14 окна wa, 01.07). Только сигналы, без P&L (июнь отложенной выборки читается один раз после заморозки).

  python3 tk700_warm20.py --new /data/p42-tk700/gate/wa20/dumps [--out /data/p42-tk700/gate/warm20.json]

Эталоны: 12 источников — `/data/p42-tk671/prod/siglv-wa-<side>/dumps/<источник>/<side>-sigsx.csv`; L_ind / S_ind / D_ind — `/data/p42-tk700/gate/g1/wa/dumps/<вариант>/<side>-sigsx.csv`.
Сдвиг T0 = 6 суток: sig_ms + 518 400 000, born / end + 8 640 мин, first_bar и индексы `members` + 8 640 / tf (бары tf минут); номер `level` — порядковый, не сравнивается.
Вселенная — c539 (`symbols-crypto.txt`): эталоны TK-671 считались на 775 контрактах (до В-175), в wa20 — только крипта.
Обязательны (`required`): L_ind / long, S_ind / short, D_ind / short (строки базы) и 12 источников / long; остальное — справочно: в `all` источники обеих сторон идут с ключами лонга
(стороны — одним проходом), эталон шорта считан `sig-short` с ключами шорта, строки не обязаны совпасть. passed = все обязательные совпали (множества целиком, все столбцы после сдвига);
расхождения — до 5 строк на проверку. Печатает только счётчики."""
from __future__ import annotations

import argparse
import json
import os
import sys

T0_SHIFT_MS = 6 * 86400 * 1000
T0_SHIFT_MIN = 6 * 1440
DAY20_MS = 20 * 86400 * 1000
END_MS = 73 * 86400 * 1000                       # wa20: SIGSX 14:73; wa: 14:73 от T0 = 17.06 → общий отрезок кончается на сутках 67 окна wa
SRC12 = ["p%dn%d" % (tf, nb) for tf in (30, 60, 240) for nb in (1, 2, 3)] + ["lxD", "lxW", "lxP"]
IND = ["L_ind", "S_ind", "D_ind"]
REQ_IND = {("L_ind", "long"), ("S_ind", "short"), ("D_ind", "short")}
C539 = "/data/p42-tk621/universe/symbols-crypto.txt"
REF12 = "/data/p42-tk671/prod/siglv-wa-%s/dumps/%s/%s-sigsx.csv"
REFIND = "/data/p42-tk700/gate/g1/wa/dumps/%s/%s-sigsx.csv"


def tf_of(var):
    if var.startswith("p") and "n" in var:
        return int(var[1:var.index("n")])
    return 60


def read_rows(path):
    import csv
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        head = next(r)
        return head, list(r)


def norm(head, row, shift_in, tf):
    """Строка → кортеж без `level`; shift_in=True — строки эталона (wa) приводятся к координатам wa20."""
    d = dict(zip(head, row))
    d.pop("level", None)
    if shift_in:
        d["sig_ms"] = str(int(d["sig_ms"]) + T0_SHIFT_MS)
        for k in ("born", "end"):
            if k in d:
                d[k] = str(int(d[k]) + T0_SHIFT_MIN)
        fb = T0_SHIFT_MIN // tf
        if "first_bar" in d:
            d["first_bar"] = str(int(d["first_bar"]) + fb)
        if "members" in d and d["members"]:
            d["members"] = "|".join("%d:%s" % (int(i) + fb, p) for i, p in (m.split(":") for m in d["members"].split("|")))
    return tuple(sorted(d.items()))


def drop_end(t):
    return tuple(x for x in t if x[0] != "end")


def compare(new_path, ref_path, tf, syms=None, base_keys=None):
    hn, rn = read_rows(new_path)
    hr, rr = read_rows(ref_path)
    if hn != hr:
        return dict(ok=False, note="столбцы различаются: %s | %s" % (hn, hr))
    ia = hn.index("sig_ms")
    ok_sym = (lambda r: r[0] in syms) if syms else (lambda r: True)         # столбец 0 — sym
    new = {norm(hn, r, False, tf) for r in rn if DAY20_MS <= int(r[ia]) < END_MS and ok_sym(r)}
    ref = {norm(hr, r, True, tf) for r in rr if DAY20_MS <= int(r[ia]) + T0_SHIFT_MS < END_MS and ok_sym(r)}
    on, orf = new - ref, ref - new
    ne, re_ = {drop_end(t) for t in new}, {drop_end(t) for t in ref}        # `end` у уровней, доживших до края данных, — не прогрев: счёт без него отдельно
    out = dict(ok=not on and not orf, new_rows=len(new), ref_rows=len(ref), only_new=len(on), only_ref=len(orf),
               only_new_ex_end=len({drop_end(t) for t in on} - re_), only_ref_ex_end=len({drop_end(t) for t in orf} - ne),
               examples_new=[dict(x) for x in sorted(on)[:5]] if on else [], examples_ref=[dict(x) for x in sorted(orf)[:5]] if orf else [])
    if base_keys is not None:                                               # расхождения на ключах (sym, sig_ms) строк базы
        out["diff_on_base"] = sum(1 for t in on | orf if (dict(t)["sym"], dict(t)["sig_ms"]) in base_keys)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", default="/data/p42-tk700/gate/wa20/dumps")
    ap.add_argument("--out", default="/data/p42-tk700/gate/warm20.json")
    a = ap.parse_args(argv)
    syms = set(open(C539, encoding="utf-8").read().replace(chr(10), ",").split(",")) - {""}
    base_keys = set()                                                       # ключи строк базы: L_ind / long, S_ind / short, D_ind / short окна wa20 (сутки 20 …)
    for var, side in sorted(REQ_IND):
        h, rows = read_rows(os.path.join(a.new, var, "%s-sigsx.csv" % side))
        ia = h.index("sig_ms")
        base_keys |= {(r[0], r[ia]) for r in rows if DAY20_MS <= int(r[ia]) < END_MS and r[0] in syms}
    checks = []
    for var in SRC12 + IND:
        for side in ("long", "short"):
            newp = os.path.join(a.new, var, "%s-sigsx.csv" % side)
            refp = (REFIND % (var, side)) if var in IND else (REF12 % (side, var, side))
            if not (os.path.exists(newp) and os.path.exists(refp)):
                checks.append(dict(var=var, side=side, ok=False, required=True, note="нет файла: %s" % (newp if not os.path.exists(newp) else refp)))
                continue
            c = compare(newp, refp, tf_of(var), syms, base_keys)
            c.update(var=var, side=side, required=(var, side) in REQ_IND, levels12=var in SRC12 and side == "long")
            checks.append(c)
            print("%-6s %-5s %s new=%s ref=%s только_new=%s только_ref=%s (без end: %s / %s) на ключах базы=%s" % (
                var, side, ("ok " if c["ok"] else "РАСХ") + (" обяз" if c["required"] else (" ур12" if c["levels12"] else " спр ")), c.get("new_rows"), c.get("ref_rows"),
                c.get("only_new"), c.get("only_ref"), c.get("only_new_ex_end"), c.get("only_ref_ex_end"), c.get("diff_on_base")))
    bad = [c for c in checks if (c["required"] and not c["ok"]) or (c.get("levels12") and c.get("diff_on_base", 1) != 0)]
    res = dict(passed=not bad, n=len(checks), failed=len(bad), base_keys=len(base_keys), shift_days=6, from_day=20,
               rule="обязательные (строки базы) совпали целиком И уровни 12 источников / лонг без расхождений на ключах строк базы", checks=checks)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WARM20", "PASS" if res["passed"] else "РАСХОЖДЕНИЯ", "(%d проверок, провалено %d, ключей базы %d) → %s" % (res["n"], res["failed"], len(base_keys), a.out))
    return 0 if res["passed"] else 3


if __name__ == "__main__":
    sys.exit(main())
