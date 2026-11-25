#!/usr/bin/env python3
"""П-42 TK-674: пересчёт клеток TK-680 L-342 (шорт c539, столбцы FAST) через таблицу фильтров шага 3 (`ftab674x`) — проверка `fast_join`: те же клетки на бинаре h должны дать
числа L-342 (бинарь c06f6674): `s4+sac10.le@t_q80` 151 сд. / +167 $ / Шарп 5,32 / просадка 22 $, `s4+accdv1_300.le@t_q80` 118 сд. / +142 $ / 4,79 / 12 $.
Запуск на srv в среде шага 3 (TK674_X680 …): `python3 tk674_fast_cell.py [cid …]`; результат — `cells/<окно>-S/fchk/<cid>` (отдельная партия «fchk», не пересекается с партиями плана), без записи в БД."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_axes as ax  # noqa: E402
import tk674_run as run  # noqa: E402

CIDS = ("s4+sac10.le@t_q80", "s4+accdv1_300.le@t_q80")


def main(cids):
    st, r5, _, _ = run.t._srv()
    run._bin()                                      # бинарь шага (TK674_BIN) — иначе движок по умолчанию (alpha-master)
    u = r5.Uni(run.UNI)
    run._winit()
    plan = json.load(open(os.path.join(run.ROOT, "res", "plan674%s-S.json" % run.SUF), encoding="utf-8"))
    cols = json.load(open(os.path.join(run.ROOT, "res", "ftabcols674%s-S.json" % run.FSUF), encoding="utf-8"))
    byid = {v["cid"]: (k, v) for k, v in plan["cells"].items()}
    for cid in cids:
        ck, v = byid[cid]
        line = run.t.variant_line(r5, u, "S", v["patch"]) + " fa=%s fo=lo fv=%g" % (cols[ck.split("F=", 1)[1]], ax.FV_PASS)
        for w in st.TRADE_WINDOWS:
            out = os.path.join(run.ROOT, "cells", "%s-S" % w.name, "fchk")
            r5.tick(u, w, [(cid, line)], out, run.one_side_env("S"), run.t.q("log", "fchk-%s-S.log" % w.name), ftab=run.t.q("ftab674" + run.FSUF, "%s-S.csv" % w.name))
        res = run._row_job(("S", ck, cid, "fchk", line, v["patch"], [], run.eff_flags("S", v["patch"])))
        m = res.get("m")
        if m is None:
            print(cid, "ОШИБКА", res.get("error"))
            continue
        print("%s: сделок %d, Σ %+.2f $, %+.3f $/сд, Шарп %.2f, просадка %.1f $, перехай %s сут" % (cid, m["n"], m["usd"], m["mean_usd"], m["sharpe"], m["maxdd"], m["gap_longest"] if "gap_longest" in m else m.get("kpi", {}).get("gap_longest")))


if __name__ == "__main__":
    main(sys.argv[1:] or CIDS)
