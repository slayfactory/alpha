#!/usr/bin/env python3
"""TK-674 шаг 0: какие параметры сильнее всего двигают линию — по ГОТОВЫМ клеткам (движок не запускался, В-213).
Читает res/cells*.json TK-643/649/650/652/655/657/660 (параметры движка), группирует клетки по оси (набор ключей patch, отличающихся от базы
стороны) и печатает по оси: лучшую клетку по Шарпу, Δ Σ $ / Δ Шарпа / Δ просадки / Δ перехая против базы, z = dsum/se_sum, долю клеток оси
с Δ Σ > 0. «Лучшая клетка оси» — максимум по клеткам на тех же данных (найдено на этих данных).
Запуск на srv: python3 tk674_effects.py <каталог выхода>   (читает /data/p42-tk6*/res)"""
import json
import os
import sys

SRC = [("TK-643", "/data/p42-tk643/res/cells43-%s.json"), ("TK-649", "/data/p42-tk649/res/cells49-%s.json"), ("TK-650", "/data/p42-tk650/res/cells50-%s.json"),
       ("TK-652", "/data/p42-tk652/res/cells52-%s.json"), ("TK-655", "/data/p42-tk655/res/cells55-%s.json"), ("TK-657", "/data/p42-tk657/res/cells57-%s.json"),
       ("TK-660", "/data/p42-tk660/res/cells60-%s.json")]
BASEV = {"L": dict(s=5, ls=0, tt=0, tb=0, rf=0.2, rs=20, e=0.3, e2=0, bm=0, rr=2, tj=0, t_high=5, q=50, natr_min=0.4, t1h=0.5),
         "S": dict(s=5, ls=0, tt=20, tb=0.1, rf=0.2, rs=2, e=0.15, e2=0.05, bm=1, rr=2, tj=0, t_high=5, q=50, natr_min=0.4, t1h=0.5)}
MIN_N = 70


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return x


def axis_of(side, c):
    p = c.get("patch")
    if not isinstance(p, dict):
        return None, {}
    ch = {k: num(v) for k, v in p.items() if k not in BASEV[side] or num(v) != BASEV[side][k]}
    return "+".join(sorted(ch)) or "(=база)", ch


def load(side):
    rows = []
    for tk, pat in SRC:
        f = pat % side
        if not os.path.exists(f):
            continue
        d = json.load(open(f, encoding="utf-8"))
        b = d["base"]
        for cid, c in d["cells"].items():
            ax, ch = axis_of(side, c)
            if ax is None:
                ax = "tu" if tk == "TK-649" else cid
            if c["n"] < MIN_N:
                continue
            dl = c.get("delta") or {}
            rows.append(dict(tk=tk, cid=cid, ax=ax, ch=ch, n=c["n"], dn=c["n"] - b["n"], sum=c["sum"], dsum=c["sum"] - b["sum"], sh=c["sharpe"], dsh=c["sharpe"] - b["sharpe"],
                             dd=c["maxdd"], ddd=c["maxdd"] - b["maxdd"], lg=c["kpi"]["longest"], dlg=c["kpi"]["longest"] - b["kpi"]["longest"],
                             z=(dl.get("dsum") / dl["se_sum"]) if dl.get("se_sum") else None, p=dl.get("p"), kinds=c.get("kinds_n"),
                             base=dict(n=b["n"], sum=b["sum"], sh=b["sharpe"], dd=b["maxdd"], lg=b["kpi"]["longest"])))
    return rows


def main(out):
    res = {}
    for side in "LS":
        rows = load(side)
        axes = {}
        for r in rows:
            axes.setdefault(r["ax"], []).append(r)
        tab = []
        for ax, rs in axes.items():
            best = max(rs, key=lambda r: r["sh"])
            tab.append(dict(ax=ax, k=len(rs), pos=sum(1 for r in rs if r["dsum"] > 0), best=best, worst_dsh=min(r["dsh"] for r in rs)))
        tab.sort(key=lambda t: -t["best"]["dsh"])
        res[side] = dict(base=rows[0]["base"] if rows else None, n_cells=len(rows), axes=tab)
    json.dump(res, open(os.path.join(out, "effects.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    for side in "LS":
        r = res[side]
        b = r["base"]
        print("== %s: cells n>=%d: %d; base n=%d Σ=%+.0f Sh=%.2f DD=%.0f longest=%d" % (side, MIN_N, r["n_cells"], b["n"], b["sum"], b["sh"], b["dd"], b["lg"]))
        print("axis | k | pos | best cell | Δn | ΔΣ | ΔSh | ΔDD | Δlong | z")
        for t in r["axes"][:25]:
            x = t["best"]
            print("%s | %d | %d | %s/%s | %+d | %+.0f | %+.2f | %+.0f | %+d | %s" % (t["ax"], t["k"], t["pos"], x["tk"], x["cid"], x["dn"], x["dsum"], x["dsh"], x["ddd"], x["dlg"], "%.1f" % x["z"] if x["z"] is not None else "-"))


def filters(out):
    """Признаки-фильтры TK-654 (44 клетки на сторону, исключение сделок по порогу признака) и пул TK-638 (t24 / t1h): лучшая клетка семейства против базы."""
    res = {}
    for side in "LS":
        d = json.load(open("/data/p42-tk654/res/cells54-%s.json" % side, encoding="utf-8"))
        b = d["base"]
        fam = {}
        for cid, c in d["cells"].items():
            if c["n"] < MIN_N:
                continue
            dl = c.get("delta") or {}
            fam.setdefault(cid.split("_")[0], []).append(dict(cid=cid, n=c["n"], dn=c["n"] - b["n"], sum=c["sum"], dsum=c["sum"] - b["sum"], sh=c["sharpe"], dsh=c["sharpe"] - b["sharpe"],
                                                               dd=c["maxdd"], ddd=c["maxdd"] - b["maxdd"], lg=c["kpi"]["longest"], dlg=c["kpi"]["longest"] - b["kpi"]["longest"],
                                                               z=(dl.get("dsum") / dl["se_sum"]) if dl.get("se_sum") else None, in_base=c.get("in_base_share")))
        rows = []
        for f, rs in fam.items():
            x = max(rs, key=lambda r: r["sh"])
            rows.append(dict(fam=f, k=len(rs), pos=sum(1 for r in rs if r["dsum"] > 0), best=x))
        rows.sort(key=lambda t: -t["best"]["dsh"])
        res[side] = dict(base=dict(n=b["n"], sum=b["sum"], sh=b["sharpe"], dd=b["maxdd"], lg=b["kpi"]["longest"]), fams=rows)
        print("== filters TK-654 %s: base n=%d Σ=%+.0f Sh=%.2f DD=%.0f longest=%d" % (side, b["n"], b["sum"], b["sharpe"], b["maxdd"], b["kpi"]["longest"]))
        for t in rows:
            x = t["best"]
            print("%s | k=%d pos=%d | %s | n=%d (%+d) | ΔΣ %+.0f | ΔSh %+.2f | ΔDD %+.0f | Δlong %+d | z %s" % (t["fam"], t["k"], t["pos"], x["cid"], x["n"], x["dn"], x["dsum"], x["dsh"], x["ddd"], x["dlg"], "%.1f" % x["z"] if x["z"] is not None else "-"))
    json.dump(res, open(os.path.join(out, "filters654.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    os.makedirs(sys.argv[1], exist_ok=True)
    main(sys.argv[1])
    filters(sys.argv[1])
