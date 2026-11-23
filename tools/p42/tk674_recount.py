#!/usr/bin/env python3
"""П-42 TK-674 (PS-994): сравнение строк БД двух меток окна по списку cid — контроли пересчёта клеток TK-690.
  srv: python3 tk674_recount.py mkplan <план сейчас> <план до PS-986> <cid.json> <выход>   — план шага 3 с добавленными клетками cid (из плана до PS-986) на их места в сетках пар
  srv: TK674_UNI=c539 TK674_ROOT=<корень с res/plan674bsx-S.json> python3 tk674_recount.py cmp <метка A> <метка B> <cid.json> [--stop]
Метка — `WIN` строк БД (`main` — основная `01.07–13.11`, прежние клетки; остальные — как заданы в `TK674_WIN` пересчёта). Печатает: сколько клеток есть в обеих метках, сколько совпало точно (сделки, $, Шарп), у остальных — дельты;
`--stop` — код 1, если есть расхождение или нет строки (К1 PS-994: точное совпадение)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def cmp_rows(store, uni, side_name, plan, win_a, win_b, cids):
    """Строки двух меток по клеткам `cids` плана: (число клеток, точно равных, [расхождения], [нет в A], [нет в B])."""
    ck = {c["cid"]: k for k, c in plan["cells"].items()}
    keys = [ck[c] for c in cids if c in ck]
    a = store.cells_lookup(side_name, uni, win_a, keys)
    b = store.cells_lookup(side_name, uni, win_b, keys)
    same, diff = 0, []
    for k in keys:
        if k in a and k in b:
            ra, rb = a[k], b[k]
            if (ra["trades"], ra["usd"], ra["sharpe"]) == (rb["trades"], rb["usd"], rb["sharpe"]):
                same += 1
            else:
                diff.append(dict(ckey=k, trades=(ra["trades"], rb["trades"]), usd=(ra["usd"], rb["usd"]), sharpe=(ra["sharpe"], rb["sharpe"])))
    return len(keys), same, diff, [k for k in keys if k not in a], [k for k in keys if k not in b]


def mkplan(cur, old, cids):
    """Текущий план + клетки `cids` из плана до PS-986 (клетка и её позиции в сетках пар; позиция в текущем плане должна быть пустой)."""
    keep = set(cids)
    add = {k: c for k, c in old["cells"].items() if c["cid"] in keep}
    out = dict(cur, cells=dict(cur["cells"], **add))
    out["grids"] = {g: dict(v, m=[list(r) for r in v["m"]]) for g, v in cur["grids"].items()}
    for g, v in old["grids"].items():
        if v["vals"] != out["grids"][g]["vals"]:
            raise SystemExit("сетка %s: значения осей различаются" % g)
        for i, r in enumerate(v["m"]):
            for j, k in enumerate(r):
                if k in add:
                    if out["grids"][g]["m"][i][j] is not None:
                        raise SystemExit("сетка %s [%d][%d]: место занято (%s)" % (g, i, j, out["grids"][g]["m"][i][j]))
                    out["grids"][g]["m"][i][j] = k
    out["n_cells"] = len(out["cells"])
    return out, len(add)


def main():
    a = sys.argv[1:]
    if len(a) == 5 and a[0] == "mkplan":
        out, n = mkplan(json.load(open(a[1], encoding="utf-8")), json.load(open(a[2], encoding="utf-8")), json.load(open(a[3], encoding="utf-8")))
        json.dump(out, open(a[4], "w", encoding="utf-8"), ensure_ascii=False)
        print("план: клеток %d (добавлено %d) → %s" % (len(out["cells"]), n, a[4]))
        return
    import tk674_run as run
    if len(a) < 4 or a[0] != "cmp":
        raise SystemExit(__doc__)
    plan = json.load(open(os.path.join(run.ROOT, "res", "plan674bsx-S.json"), encoding="utf-8"))
    cids = json.load(open(a[3], encoding="utf-8"))
    wa, wb = (run.WIN if x == "main" else x for x in a[1:3])
    n, same, diff, no_a, no_b = cmp_rows(run.chron_store(ro=True), run.UNI, run.SIDENAME["S"], plan, wa, wb, cids)
    print("клеток %d; точно равны %d; расходятся %d; нет в «%s» %d; нет в «%s» %d" % (n, same, len(diff), a[1], len(no_a), a[2], len(no_b)))
    for d in diff[:40]:
        print("  %s: сд. %s→%s, $ %+.2f→%+.2f (Δ %+.2f), Шарп %.3f→%.3f (Δ %+.3f)" % (d["ckey"][-60:], d["trades"][0], d["trades"][1], d["usd"][0], d["usd"][1], d["usd"][1] - d["usd"][0],
                                                                                     d["sharpe"][0], d["sharpe"][1], d["sharpe"][1] - d["sharpe"][0]))
    if "--stop" in a and (diff or no_a or no_b):
        sys.exit(1)


if __name__ == "__main__":
    main()
