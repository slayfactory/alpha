"""TK-680 §13.73.18 п. 6(в) (PS-979): проверка базы единого бинаря (TK674_BIN) — клетка базы стороны в окнах wa / wb / wc целиком против якоря L-334.

Каждый файл каталога клетки якоря (сделки, сводка / мета, суточные ряды…) сравнивается байт в байт; лишний файл в новом результате допустим только `lots.csv`.
Совпало — md5 бинаря дописывается в res/eqbins674.json корня («равен на клетках без ключей TK-681 (gate683 + проверка базы c539)»); нет — стоп, решение координатора.

  srv: TK674_UNI=c539 TK674_ROOT=<корень> TK674_BIN=<единый бинарь> TK674_GBASE=<gbase674.json> TK674_REBASE=1 TK674_LEVEL=1 python3 tk680_basecheck.py
"""
from __future__ import annotations

import filecmp
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_run as run  # noqa: E402

EXTRA_OK = ("lots.csv",)                        # лишним может быть только он (в имени — с приставкой стороны)


def main():
    t, ax = run.t, run.ax
    st, r5, _, _ = t._srv()
    run._bin()
    run.engine_opts(r5)
    u = r5.Uni(run.UNI)
    store = run.chron_store(ro=True)
    bad, rows = [], []
    for side in run.SIDES:
        ck = ax.base_ckey(side)
        found = store.cells_lookup(run.SIDENAME[side], run.UNI, run.WIN, [ck], bins=run.eq_bins())
        if ck not in found:
            raise SystemExit("якорь %s не найден в БД под %s (eqbins %s)" % (side, run.UNI, ",".join(x[:8] for x in run.eq_bins())))
        dirs = found[ck]["extra"]["dirs"]
        patch = dict(ax.cell_assign(side, {})[0])
        line = t.variant_line(r5, u, side, patch)
        for w in st.TRADE_WINDOWS:
            old = dirs[w.name]
            cid = os.path.basename(old.rstrip("/"))
            out = os.path.join(run.ROOT, "basecheck", "%s-%s" % (w.name, side))
            os.makedirs(out, exist_ok=True)
            r5.tick(u, w, [(cid, line)], out, run.one_side_env(side), t.q("log", "basecheck-%s-%s.log" % (w.name, side)))
            new = os.path.join(out, cid)
            fo, fn = set(os.listdir(old)), set(os.listdir(new))
            diff = sorted(f for f in fo if f not in fn or not filecmp.cmp(os.path.join(old, f), os.path.join(new, f), shallow=False))
            extra = sorted(f for f in fn - fo if not f.endswith(EXTRA_OK))
            rows.append(dict(side=side, win=w.name, files=len(fo), diff=diff, extra=extra))
            run.say("basecheck %s-%s: файлов %d, %s" % (w.name, side, len(fo), "байт в байт" if not diff and not extra else "РАЗЛИЧИЯ %s, лишние %s" % (diff, extra)))
            bad += diff + extra
    json.dump(dict(bin_md5=run.bin_md5(), rows=rows, ok=not bad), open(os.path.join(run.ROOT, "res", "basecheck680.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if bad:
        raise SystemExit("basecheck: расхождения с якорями L-334 — набор не считается, решение координатора")
    eq = run.eq_bins()
    if run.bin_md5() not in eq:
        json.dump(eq + [run.bin_md5()], open(os.path.join(run.ROOT, "res", "eqbins674.json"), "w", encoding="utf-8"))
    run.say("basecheck готово: eqbins %s" % ", ".join(x[:8] for x in run.eq_bins()))


if __name__ == "__main__":
    main()
