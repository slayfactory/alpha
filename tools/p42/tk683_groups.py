#!/usr/bin/env python3
"""TK-683: сколько проходов сигналов в партии делят одно состояние уровня.

    tk683_groups.py <каталог партий или файл *.variants.txt> ...

Проход = уникальный набор ключей сигнала (`SigCfg`); группа = проходы, различающиеся только условием выдачи
кандидата (`natr_min`, `t1h`, `t24`, `e`, `track`, `nf`, `ev`, `ea`, `el`, `eh`, `fh`, `fk`, `fm`, `fb`, `color`, `ap`):
состояние уровня в `tick::candidates_fg` у них одно. «Исходов» — уникальные наборы ключей без портфеля и фильтра
(`c`, `m`, `mm`, `w`, `ww`, `p`, `reentry`, `busy`, `pr`, `pw`, `fv`, `fa`, `fo`): исход сделки по сигналу у вариантов с равным набором один. Сравнение значений — по строке ключа (оценка сверху числа групп).
"""
import sys
from pathlib import Path

SIG = {"natr_p", "natr_min", "t1h", "t24", "tf", "k", "d", "n_bounce", "tau", "l_days", "pk", "lx", "lw", "track", "e",
       "color", "f", "r", "ban", "ap", "pb", "fh", "fk", "fm", "fb", "fg", "nf", "ev", "ea", "el", "eh"}
EMIT = {"natr_min", "t1h", "t24", "e", "track", "nf", "ev", "ea", "el", "eh", "fh", "fk", "fm", "fb", "color", "ap"}
PORT = {"c", "m", "mm", "w", "ww", "p", "reentry", "busy", "pr", "pw", "fv", "fa", "fo"}


def count(path):
    passes, groups, sims, n = set(), set(), set(), 0
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        n += 1
        kv = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        passes.add(tuple(sorted((k, v) for k, v in kv.items() if k in SIG)))
        groups.add(tuple(sorted((k, v) for k, v in kv.items() if k in SIG - EMIT)))
        sims.add(tuple(sorted((k, v) for k, v in kv.items() if k not in PORT)))
    return n, len(passes), len(groups), len(sims)


def main():
    files = []
    for a in sys.argv[1:]:
        p = Path(a)
        files += [p] if p.is_file() else sorted(p.rglob("*.variants.txt"))
    tot = [0, 0, 0, 0]
    print(f"{'файл':<60} {'вар.':>6} {'проходов':>9} {'групп':>6} {'исходов':>8}")
    for f in files:
        r = count(f)
        tot = [x + y for x, y in zip(tot, r)]
        print(f"{str(f)[-60:]:<60} {r[0]:>6} {r[1]:>9} {r[2]:>6} {r[3]:>8}")
    if tot[2]:
        print(f"{'итого ' + str(len(files)) + ' вызовов':<60} {tot[0]:>6} {tot[1]:>9} {tot[2]:>6} {tot[3]:>8}  проходов на группу {tot[1] / tot[2]:.2f}")


if __name__ == "__main__":
    main()
