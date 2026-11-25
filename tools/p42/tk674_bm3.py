#!/usr/bin/env python3
"""П-42 TK-674 (аудит TK-682, L-312): счётчики стопов у клеток с `bm = 3` по файлам сделок. Без движка, только чтение.
При bm = 3 стоп после 1-го тейка стоит на min(вход, нижняя точка) − k·NATR, а движок помечает такой выход «безубыток» (`be`) —
в счётчики стопов карт он не входил. Здесь выходы `be` этих клеток считаются стопами: число, из них с убытком по сделке, Σ и средний $.
  /opt/venv/bin/python tk674_bm3.py <каталог с map674*-L.json / -S.json> <выход.json> <выход.md>
Клетки — все строки БД летописи (h775, 01.07–13.11) с `bm=3` в ключе; повторы наборов сделок (`trades_sig`) в итогах стороны считаются один раз."""
from __future__ import annotations

import ast
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_run as run  # noqa: E402


def lit(x):
    return ast.literal_eval(x) if isinstance(x, str) else x


def cell(side, row):
    st, p8 = run._W["st"], run._W["p8"]
    ex = row["extra"]
    ex = json.loads(ex) if isinstance(ex, str) else ex
    dirs = {w: (d, side) for w, d in lit(ex["dirs"]).items()}
    tables, _ = p8.read_tables(dirs)
    kinds, pnl = p8.kinds_pnl(tables)
    kinds, pnl = np.array(kinds), np.asarray(pnl, dtype=float)
    if len(pnl) != int(row["trades"]) or abs(float(pnl.sum()) - float(row["usd"])) > 1e-4:
        raise SystemExit("%s %s: файлы сделок ≠ строка БД (n %d / %s, Σ %.4f / %s)" % (side, row["ckey"], len(pnl), row["trades"], pnl.sum(), row["usd"]))
    hard, be = kinds == "stop", kinds == "be"
    both = hard | be

    def grp(m):
        return dict(n=int(m.sum()), usd=float(pnl[m].sum()), mean=float(pnl[m].mean()) if m.any() else None, n_neg=int((pnl[m] < 0).sum()),
                    usd_neg=float(pnl[m][pnl[m] < 0].sum()), worst=float(pnl[m].min()) if m.any() else None)
    return dict(cid=row["cell"], n=int(len(pnl)), usd=float(pnl.sum()), sharpe=float(row["sharpe"]) if row["sharpe"] is not None else None,
                sig=ex.get("trades_sig"), stop=grp(hard), be=grp(be), stop_all=grp(both))


def f2(x):
    return "—" if x is None else ("%+.2f" % x).replace(".", ",").replace("-", "−")


def main(mapdir, out_json, out_md):
    run._winit()
    store = run.chron_store(ro=True)
    bins = run.eq_bins()
    maps = {}
    for f in sorted(glob.glob(os.path.join(mapdir, "map674*-[LS].json"))):
        maps[os.path.basename(f)] = open(f, encoding="utf-8").read()
    res, md = {}, ["# Счётчики стопов у клеток с `bm = 3` (TK-674, аудит TK-682)", "",
                   "Дельта без движка: файлы сделок готовых клеток. При `bm = 3` стоп после 1-го тейка стоит ниже входа, движок помечает такой выход «безубыток»; "
                   "здесь он считается стопом и показан отдельной колонкой: средний $ полных стопов и стопов после 1-го тейка не смешиваются. Ставка 500 $. «с убытком» — сделка целиком в минусе (первый тейк уже взят, поэтому часть таких выходов в плюсе).", ""]
    for side in "LS":
        keys = [r[0] for r in store.c.execute("SELECT DISTINCT ckey FROM metric WHERE side=? AND uni=? AND win=? AND ckey LIKE '%bm=3%'", (run.SIDENAME[side], run.UNI, run.WIN))]
        keys = [k for k in keys if "bm=3" in k.split()]
        rows = store.cells_lookup(run.SIDENAME[side], run.UNI, run.WIN, keys, bins=bins)
        cells = {k: cell(side, r) for k, r in sorted(rows.items())}
        seen, uniq = set(), []
        for k, c in cells.items():
            if c["sig"] not in seen:
                seen.add(c["sig"])
                uniq.append(c)
        tot = {g: dict(n=sum(c[g]["n"] for c in uniq), n_neg=sum(c[g]["n_neg"] for c in uniq), usd=sum(c[g]["usd"] for c in uniq)) for g in ("stop", "be", "stop_all")}
        shown = {k: [m for m, txt in maps.items() if m.endswith("-%s.json" % side) and json.dumps(k, ensure_ascii=False) in txt] for k in cells}
        res[side] = dict(cells=cells, n_cells=len(cells), n_uniq=len(uniq), total=tot, in_maps={k: v for k, v in shown.items() if v})
        md += ["## %s" % {"L": "Лонг", "S": "Шорт"}[side], "",
               "Клеток с `bm = 3` в БД: %d, разных наборов сделок: %d. По разным наборам: выходов `stop` %d (Σ %s $), выходов «безубыток» %d, из них с убытком по сделке %d "
               "(Σ всех %s $, средний %s $); стопов с учётом «безубытка» %d вместо %d." % (
                   len(cells), len(uniq), tot["stop"]["n"], f2(tot["stop"]["usd"]), tot["be"]["n"], tot["be"]["n_neg"], f2(tot["be"]["usd"]),
                   f2(tot["be"]["usd"] / tot["be"]["n"]) if tot["be"]["n"] else "—", tot["stop_all"]["n"], tot["stop"]["n"]), "",
               "Клетки, попавшие в карты (любая строка карты):", "",
               "| клетка | сделок | Σ $ | Шарп | полных стопов (ср. $) | стопов после 1-го тейка (ср. $; с убытком; худший $) | стопов всего | карты |", "|---|---|---|---|---|---|---|---|"]
        for k, ms in sorted(res[side]["in_maps"].items(), key=lambda kv: -(cells[kv[0]]["sharpe"] or -9)):
            c = cells[k]
            md.append("| `%s` | %d | %s | %s | %d (%s) | %d (%s; %d; %s) | %d | %s |" % (
                c["cid"], c["n"], f2(c["usd"]), ("%.2f" % c["sharpe"]).replace(".", ",") if c["sharpe"] is not None else "—", c["stop"]["n"], f2(c["stop"]["mean"]),
                c["be"]["n"], f2(c["be"]["mean"]), c["be"]["n_neg"], f2(c["be"]["worst"]), c["stop_all"]["n"],
                ", ".join(m.replace("map674", "").replace("-%s.json" % side, "") or "пары" for m in ms)))
        md.append("")
    json.dump(res, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(out_md, "w", encoding="utf-8").write("\n".join(md))
    print("bm3: L %d клеток / %d наборов, S %d / %d" % (res["L"]["n_cells"], res["L"]["n_uniq"], res["S"]["n_cells"], res["S"]["n_uniq"]))


if __name__ == "__main__":
    main(*sys.argv[1:4])
