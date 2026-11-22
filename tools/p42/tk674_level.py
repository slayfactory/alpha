#!/usr/bin/env python3
"""П-42 §13.73.1 (TK-674): уровни n = 3, 4 карты осей — клетки комбинаций из n осей, счёт «есть / считаем» по БД летописи. Без движка и без P&L.
  python3 tk674_level.py count <n> [--rule <имя>]   — комбинации осей, разные клетки (канонические ключи), есть в БД / считаем, проходы сигналов, оценка часов
Комбинация осей допустима, если все пары осей совместимы (`tk674_axes.compat`); клетка — произведение значений осей с теми же охранами `cell_assign`
(t_low < t_high, min-лот ≥ 5 $, lx ≠ 0 только при tf = 60, пул × признак, стоп ≤ 5 %). Канонический ключ — `tk674_axes.canon` (составной фильтр — «F=…» по смыслу)."""
from __future__ import annotations

import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_axes as ax  # noqa: E402

ROOT = os.environ.get("TK674_ROOT", "/data/p42-tk674")
SIDENAME = {"L": "long", "S": "short"}
UNI, WIN = os.environ.get("TK674_UNI", "h775"), "01.07–13.11"


def axis_combos(side, n):
    """Все комбинации из n осей стороны со всеми совместимыми парами."""
    axs = ax.axes(side)
    out = []
    for c in itertools.combinations(axs, n):
        if all(ax.compat(side, a, b)[0] for a, b in itertools.combinations(c, 2)):
            out.append(tuple(a["name"] for a in c))
    return out


def level_vals(side, a, split=False):
    """Значения оси в сетках уровней: при базе стопа ≤ 4 % стоп выше базы стороны не считается (охрану (д) не проходит по построению, §13.73.3)."""
    if ax.REBASE and a["name"] == "s" and not split:
        return [v for v in a["vals"] if v <= ax.BASE_STOP[side]]
    return a["vals"]


def combo_cells(side, names, thr, cells=None, only_all_moved=False):
    """Клетки комбинации осей → {ckey: клетка}; thr — {ось: {метка: (признак, знак, порог)}} из res/thr674.json.
    only_all_moved — только позиции, где каждая ось сдвинута с базы (остальные позиции — клетки комбинаций меньшего уровня)."""
    amap = ax.axis_map(side)
    cells = {} if cells is None else cells
    base_line = ax.BASE_LINE(side)
    vals = [level_vals(side, amap[n], "split" in names) for n in names]
    for vs in itertools.product(*vals):
        if only_all_moved and any(v == amap[n]["base"] for n, v in zip(names, vs)):
            continue
        patch, filt, ok, why = ax.cell_assign(side, dict(zip(names, vs)))
        if not ok:
            continue
        ft = [(thr[n][lab][0], thr[n][lab][1], thr[n][lab][2]) for n, lab in filt] if filt else []
        ck = ax.canon(base_line + (" " + ax.patch_str(patch) if patch else ""), ft)
        c = cells.get(ck)
        if c is None:
            c = cells[ck] = dict(ckey=ck, cid=ax.cell_id(patch, filt), patch=patch, filt=filt, sig=ax.sigkey(patch), combos=[])
        c["combos"].append(names)
    return cells


def level_cells(side, n, thr, combos=None, only_all_moved=False):
    cells = {}
    for names in (combos if combos is not None else axis_combos(side, n)):
        combo_cells(side, names, thr, cells, only_all_moved)
    return cells


def load_thr(side):
    raw = json.load(open(os.path.join(ROOT, "res", "thr674.json"), encoding="utf-8"))["thr"][side]
    return {n: {lab: tuple(v) for lab, v in d.items()} for n, d in raw.items()}


def cmd_count(n):
    import tk674_run as run
    store = run.chron_store(ro=True)
    bins = run.eq_bins()
    for side in ax.SIDES:
        thr = load_thr(side)
        combos = axis_combos(side, n)
        cells = level_cells(side, n, thr, combos)
        found = store.cells_lookup(SIDENAME[side], UNI, WIN, list(cells), bins=bins)
        todo = [c for k, c in cells.items() if k not in found]
        cls = {}
        for k, c in cells.items():
            kind = "фильтр" if c["filt"] else ("сигналы" if c["sig"] else "выход")
            row = cls.setdefault(kind, [0, 0])
            row[0] += 1
            row[1] += k in found
        sigs = {c["sig"] for c in todo if c["sig"]}
        print("== %s n=%d: комбинаций осей %d, разных клеток %d, в БД есть %d, считаем %d; проходов сигналов %d; %s" % (
            side, n, len(combos), len(cells), len(found), len(todo), len(sigs),
            ", ".join("%s %d/%d" % (k, v[0] - v[1], v[0]) for k, v in sorted(cls.items()))))


SUB_MIN = 2                                     # комбинация уровня n берётся, если активны ≥ SUB_MIN её подкомбинаций уровня n − 1


def map_path(n, side):
    return os.path.join(ROOT, "res", "map674%s%s-%s.json" % (ax.SUF2, n if n > 2 else "", side))


def active_subs(n, side):
    """{frozenset(оси): прирост} активных комбинаций уровня n − 1 из его карты: лучшая охранённая клетка с плато со всеми сдвинутыми осями
    лучше планки нижних уровней (gain_sub > 0) и Σ не ниже Σ базы стороны."""
    pm = json.load(open(map_path(n - 1, side), encoding="utf-8"))
    busd = pm["base"]["usd"]
    act = {}
    for p in pm["pairs"]:
        bm = p["both_moved"]
        if bm and bm.get("gain_sub") is not None and bm["gain_sub"] > 0 and bm["usd"] >= busd:
            act[frozenset(p["pair"].split("|"))] = bm["gain_sub"]
    return act, len(pm["pairs"])


def cmd_select(n, cap):
    """Отбор комбинаций уровня n по правилу поправки (до счёта, по карте уровня n − 1): комбинация берётся, если активны ≥ SUB_MIN её подкомбинаций;
    порядок — по сумме приростов активных подкомбинаций (убывание, затем имя); набираются целиком, пока новых клеток стороны ≤ cap. Без P&L новых клеток."""
    import tk674_run as run
    store = run.chron_store(ro=True)
    bins = run.eq_bins()
    for side in ax.SIDES:
        thr = load_thr(side)
        act, n_sub = active_subs(n, side)
        cand = []
        for names in axis_combos(side, n):
            g = [act[frozenset(c)] for c in itertools.combinations(names, n - 1) if frozenset(c) in act]
            if len(g) >= SUB_MIN:
                cand.append((-sum(g), names, len(g)))
        cand.sort()
        cells, taken, n_new, n_have, stop_at = {}, [], 0, 0, None
        for neg, names, na in cand:
            cc = combo_cells(side, names, thr, {})
            fresh = {k: c for k, c in cc.items() if k not in cells}
            found = store.cells_lookup(SIDENAME[side], UNI, WIN, list(fresh), bins=bins)
            new = [k for k in fresh if k not in found]
            if n_new + len(new) > cap:
                stop_at = names
                break
            cells.update(fresh)
            n_new += len(new)
            n_have += len(fresh) - len(new)
            taken.append(dict(names=list(names), score=-neg, n_act=na, cells=len(cc), new=len(new)))
        sigs = {c["sig"] for c in cells.values() if c["sig"]}
        out = dict(side=side, n=n, cap=cap, sub_min=SUB_MIN, active_subs=len(act), subs=n_sub, eligible=len(cand), taken=taken, left_out=len(cand) - len(taken),
                   n_cells=len(cells), n_have=n_have, n_new=n_new,
                   cells={k: dict(cid=c["cid"], patch=c["patch"], filt=c["filt"], sig=c["sig"]) for k, c in cells.items()})
        json.dump(out, open(os.path.join(ROOT, "res", "level674%s%d-%s.json" % (ax.SUF2, n, side)), "w", encoding="utf-8"), ensure_ascii=False)
        print("== %s n=%d: активных комбинаций уровня %d: %d из %d; комбинаций уровня %d по правилу %d, взято %d, за потолком %d%s; клеток %d, в БД есть %d, считаем %d (потолок %d); проходов сигналов ≤ %d; фильтровых клеток %d" % (
            side, n, n - 1, len(act), n_sub, n, len(cand), len(taken), len(cand) - len(taken), (" (первая: %s)" % "×".join(stop_at)) if stop_at else "",
            len(cells), n_have, n_new, cap, len(sigs), sum(1 for c in cells.values() if c["filt"])))


def combo_grid(side, names, thr):
    """{позиция (i, j, …): канонический ключ | None} комбинации осей и индексы базы по осям (None — у оси нет значения базы)."""
    amap = ax.axis_map(side)
    base_line = ax.BASE_LINE(side)
    vals = [level_vals(side, amap[n], "split" in names) for n in names]
    grid = {}
    for pos in itertools.product(*[range(len(v)) for v in vals]):
        patch, filt, ok, why = ax.cell_assign(side, {n: vals[d][i] for d, (n, i) in enumerate(zip(names, pos))})
        if not ok:
            grid[pos] = None
            continue
        ft = [(thr[n][lab][0], thr[n][lab][1], thr[n][lab][2]) for n, lab in filt] if filt else []
        grid[pos] = ax.canon(base_line + (" " + ax.patch_str(patch) if patch else ""), ft)
    b0 = [vals[d].index(amap[n]["base"]) if amap[n]["base"] in vals[d] else None for d, n in enumerate(names)]
    return grid, b0


def cmd_map(n):
    """Карта уровня n из БД: охраны (а)–(д) относительно базы стороны, плато по соседям на шаг по каждой оси, победитель комбинации и лучшая клетка со всеми
    сдвинутыми осями; прирост над планкой нижних уровней (gain_sub). → res/map674<b><n>-<сторона>.json (тот же вид, что карта пар)."""
    import numpy as np
    import tk674_run as run
    store = run.chron_store(ro=True)
    bins = run.eq_bins()
    for side in ax.SIDES:
        thr = load_thr(side)
        lv = json.load(open(run.level_path(side), encoding="utf-8"))
        inv = run.load_invalid(side)
        rows = store.cells_lookup(SIDENAME[side], UNI, WIN, list(lv["cells"]), bins=bins)
        base_ck = ax.base_ckey(side)
        brow = store.cells_lookup(SIDENAME[side], UNI, WIN, [base_ck], bins=bins)[base_ck]
        for r_ in rows.values():
            if r_["sharpe"] is None:
                r_["sharpe"] = float("nan")
        bsh = brow["sharpe"]
        gd = {k: run.guards(r, brow) for k, r in rows.items()}
        bar = {}                                             # планка нижних уровней: лучшая охранённая (а)–(д) клетка каждой подкомбинации
        for lvl in range(2, n):
            pm = json.load(open(map_path(lvl, side), encoding="utf-8"))
            for p in pm["pairs"]:
                if p["best_no_plateau"]:
                    bar[frozenset(p["pair"].split("|"))] = p["best_no_plateau"]["sharpe"]
            if lvl == 2:
                for a_, v in pm.get("best1", {}).items():
                    bar[frozenset([a_])] = v
        out_rows, missing = [], 0
        for tk in lv["taken"]:
            names = tuple(tk["names"])
            grid, b0 = combo_grid(side, names, thr)
            gvals = [level_vals(side, ax.axis_map(side)[x], "split" in names) for x in names]
            cand = []
            for pos, k in grid.items():
                if k is None or k not in rows:
                    missing += int(k is not None and lv["cells"][k]["cid"] not in inv)
                    continue
                r = rows[k]
                if r["sharpe"] != r["sharpe"] or run.is_ref5(r):
                    continue
                nb = []
                for d in range(len(pos)):
                    for i2 in ax.nb_idx(names[d], gvals[d], pos[d]):
                        x = grid.get(pos[:d] + (i2,) + pos[d + 1:])
                        if x is not None and x in rows and not run.is_ref5(rows[x]) and rows[x]["sharpe"] == rows[x]["sharpe"]:
                            nb.append(rows[x]["sharpe"])
                nbm = float(np.mean(nb)) if nb else None
                plateau = bool(nb) and nbm >= 0.8 * r["sharpe"] and nbm >= bsh
                cand.append((k, r, dict(gd[k], g=plateau), nbm, pos))
            ok3 = [c for c in cand if all(c[2][x] for x in "abcd")]
            full = [c for c in ok3 if c[2]["g"]]
            win = run.pick(full)
            allm = run.pick([c for c in full if all(i != b for i, b in zip(c[4], b0))])
            wr, ar = run.rep_cell(win, bsh), run.rep_cell(allm, bsh)
            sub_bar = max([bsh] + [v for ks, v in bar.items() if ks < frozenset(names)])
            for rp, c in ((wr, win), (ar, allm)):
                if rp:
                    rp["moved"] = sum(1 for i, b in zip(c[4], b0) if i != b)
                    rp["syn"] = None
                    rp["gain_sub"] = rp["sharpe"] - sub_bar
                    if ax.REBASE:
                        rp["ref5_key"] = run.ref5_key(rp["ckey"])
            out_rows.append(dict(pair="|".join(names), score=tk["score"], n_act=tk["n_act"], cells=len(cand), guarded3=len(ok3), guarded_all=len(full), winner=wr, both_moved=ar,
                                 best_no_plateau=run.rep_cell(run.pick(ok3), bsh), sub_bar=sub_bar))
        if missing:
            raise SystemExit("%s: в БД нет %d клеток уровня" % (side, missing))
        bx = brow["extra"]
        run.add_split_reports(side, [p[w] for p in out_rows for w in ("winner", "both_moved")], rows)
        if ax.REBASE:
            reps = [p[w] for p in out_rows for w in ("winner", "both_moved") if p[w]]
            r5rows = store.cells_lookup(SIDENAME[side], UNI, WIN, sorted({x["ref5_key"] for x in reps}), bins=bins)
            for x in reps:
                q5 = r5rows.get(x["ref5_key"])
                x["ref5"] = dict(sharpe=q5["sharpe"], usd=q5["usd"], n=q5["trades"], maxdd=q5["extra"]["maxdd"], **run.loss_fields(q5["extra"])) if q5 else None
        m_side, m_own = run.m_trials(store, SIDENAME[side])
        shs = np.array([r["sharpe"] for r in rows.values() if r["sharpe"] == r["sharpe"]])
        sig_sh = float(shs.std()) if len(shs) > 1 else float("nan")
        by_sig = {}
        for k, r in rows.items():
            by_sig.setdefault(r["extra"].get("trades_sig"), []).append(k)
        dup = [v for k_, v in by_sig.items() if k_ and len(v) > 1]
        out = dict(side=side, level=n, rebase=ax.REBASE, base=dict(ckey=base_ck, sharpe=bsh, usd=brow["usd"], n=brow["trades"], maxdd=bx["maxdd"], maxdd_pct=bx["maxdd_pct"], kpi=bx["kpi"],
                   rr=bx["rr"]["rr"], **run.loss_fields(bx)), pairs=out_rows, n_cells=len(rows), m=m_side, m_own=m_own, sharpe_sd=sig_sh, noise_max=sig_sh * float(np.sqrt(2 * np.log(max(m_side, 2)))),
                   g_dup=dict(groups=len(dup), cells=sum(len(v) for v in dup), examples=dup[:5]),
                   select=dict(cap=lv["cap"], eligible=lv["eligible"], taken=len(lv["taken"]), left_out=lv["left_out"], active_subs=lv["active_subs"], subs=lv["subs"]))
        json.dump(out, open(map_path(n, side), "w", encoding="utf-8"), ensure_ascii=False)
        nw = sum(1 for p in out_rows if p["both_moved"] and p["both_moved"]["gain_sub"] > 0)
        print("== %s n=%d: комбинаций %d, клеток %d, m %d, шум ≈ %.2f; с победителем %d; все оси сдвинуты и лучше планки нижних уровней: %d" % (
            side, n, len(out_rows), len(rows), m_side, out["noise_max"], sum(1 for p in out_rows if p["winner"]), nw))
    print("map уровня %d готово" % n)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "count":
        cmd_count(int(sys.argv[2]))
    elif len(sys.argv) >= 4 and sys.argv[1] == "select":
        cmd_select(int(sys.argv[2]), int(sys.argv[3]))
    elif len(sys.argv) >= 3 and sys.argv[1] == "map":
        cmd_map(int(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
