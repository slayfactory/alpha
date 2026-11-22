#!/usr/bin/env python3
"""П-42 TK-674: готовность стадии `cells` по счётчику на машине (done-метки и журналы движка), без движка и без чтения P&L.
Запуск на srv в среде цепочки (TK674_ROOT, TK674_UNI, TK674_REBASE, TK674_SPLIT* …): `python3 tk674_progress.py [L|S ...]`.
Печатает по стороне и окну: партии сделано / всего, клетки (варианты × окна) сделано / всего; по видам партий (`kind`) — секунд на клетку-окно
(строки «rc=0 N с» и «cells <окно>-<сторона> <партия> … готово» журнала цепочки `log/chain<суффикс>.log`, TK674_CHAIN_LOG); оценку остатка по этим ставкам."""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_run as run  # noqa: E402

RC = re.compile(r"rc=\d+ (\d+) с")
DONE = re.compile(r"cells (w\w)-([LS]) (\S+) \[(\d+) вариантов")


def main(sides):
    st, _, _, _ = run.t._srv()
    store = run.chron_store(ro=True)
    wins = [w.name for w in st.TRADE_WINDOWS]
    rate = {}                                              # kind → [секунд, клеток-окон] по готовым партиям
    secs_by = {}                                           # (окно, сторона, партия) → секунды движка из журнала цепочки
    lg = os.environ.get("TK674_CHAIN_LOG") or glob.glob(os.path.join(run.ROOT, "log", "chain%s.log" % run.SUF.replace("1", "")))[0]
    last = 0
    for line in open(lg, encoding="utf-8", errors="replace"):
        m = RC.search(line)
        if m:
            last = int(m.group(1))
            continue
        m = DONE.search(line)
        if m:
            secs_by[(m.group(1), m.group(2), m.group(3))] = last
    todo_by = {}
    tot_cells = tot_done = 0
    for side in sides:
        cells, _, _, _, found = run.plan_side(side, store)
        bl = run.batches([c for k, c in cells.items() if k not in found])
        for w in wins:
            nb = nd = cw = cd = 0
            for kind0, cur in bl:
                nm = run.batch_name(kind0, cur)
                kind = "%s, %s проходов сигнала" % (kind0, ">= 20" if len({c["sig"] for c in cur}) >= 20 else "< 20")      # вид партии: дорогие — сотни проходов сигнала на партию
                done = os.path.exists(os.path.join(run.ROOT, "done", "%s-%s-%s" % (w, side, nm)))
                nb += 1
                cw += len(cur)
                if done:
                    nd += 1
                    cd += len(cur)
                    secs = secs_by.get((w, side, nm), 0)
                    r = rate.setdefault(kind, [0, 0])
                    r[0] += secs
                    r[1] += len(cur)
                else:
                    todo_by[kind] = todo_by.get(kind, 0) + len(cur)
            print("%s %s: партий %d / %d, клеток %d / %d" % (side, w, nd, nb, cd, cw))
            tot_cells += cw
            tot_done += cd
    eta = 0.0
    for kind, r in sorted(rate.items()):
        s = r[0] / r[1] if r[1] else float("nan")
        left = todo_by.get(kind, 0)
        eta += s * left if s == s else 0
        print("вид %s: %.2f с на клетку-окно (готово %d, осталось %d)" % (kind, s, r[1], left))
    print("всего клеток-окон %d / %d (%.1f %%); остаток по этим ставкам ≈ %.1f ч (без поправки на нагрузку соседей)" % (
        tot_done, tot_cells, 100.0 * tot_done / max(tot_cells, 1), eta / 3600))


if __name__ == "__main__":
    main(sys.argv[1:] or list(run.SIDES))
