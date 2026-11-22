#!/usr/bin/env python3
"""П-42 §13.74 (+ поправка §13.74.1), TK-676: стоп от волатильности (NATR / реализованная, окна 5–60 мин), блок окон W, этап 2 — на h775, результаты в БД летописи.
  python3 tk676_run.py gates              — G-bin, G-base (6 строк баз байт в байт), md5 бинаря → равные (res/eqbins674.json)
  python3 tk676_run.py plan               — клетки этапа 1 и блока W, поиск в БД: «есть / считаем»
  python3 tk676_run.py stage1 --entry L-N — разбор строк движком, счёт отсутствующих клеток этапа 1 и блока W, метрики → БД
  python3 tk676_run.py stage2 --entry L-N — родители по правилу п. 6, клетки сочетаний, счёт, метрики → БД
  python3 tk676_run.py report             — охраны, победитель, опора F, вердикт по правилу п. 7 → res/report676.json, out/report676.md
  python3 tk676_run.py all --entry L-N    — всё подряд одним пакетом
Механика партий, метрики и БД — `tk674_run` без изменений (каталог задачи — /data/p42-tk676). Доллары — ставка 500 $; Sharpe — годовой по суточному ряду; без бп, без медиан."""
from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.environ.setdefault("TK674_ROOT", "/data/p42-tk676")
import tk674_run as m  # noqa: E402
import tk674_axes as ax  # noqa: E402

t = m.t
BIN = ROOT + "/bin/alpha-tk676"
m.BIN = BIN
SIDES = tuple(os.environ.get("TK676_SIDES", "L,S").split(","))
SIDENAME = m.SIDENAME
say = t.say
STAKE, MARGIN = 500.0, 250.0

# среднее V на сигналах базы, % цены (desc676.md, среднее L и S): источник vs → (имя, вид, окно, V̄)
SRC = {1: ("N5", "N", 5, 0.71), 2: ("N15", "N", 15, 0.64), 3: ("N30", "N", 30, 0.59), 4: ("N60", "N", 60, 0.50),
       5: ("R5", "R", 5, 1.06), 6: ("R15", "R", 15, 1.72), 7: ("R30", "R", 30, 2.32), 8: ("R60", "R", 60, 2.90)}
TS = {3: (1.5, 2, 3), 4: (1.5, 2, 3, 4)}            # потолок s → T
REGIME = [(1, 4), (1, 3), (5, 8), (5, 7)]            # пары (короткое окно, длинное окно)
SIG_EXTRA = ("fh", "fk", "r")                        # ключи SigCfg блока W сверх `ax.SIG_KEYS`
# блок W: ось → (новые значения, значения только из БД)
W_AXES = {"natr_exit_p": ([15, 60], []), "tt": ([15, 30], [0, 5, 10, 20, 40, 60]), "tu": ([5, 15, 30], [60, 120, 240]), "fh": ([0.25, 0.5, 1, 2], []),
          "fk": ([15, 30, 60], []), "r": ([5, 30, 60], []), "mm": ([5, 15, 30], []), "ww": ([5, 15, 30], []),
          "l_days": ([], [3, 5, 10, 14]), "tf": ([], [30, 240]), "s": ([], [1, 1.5, 2, 3, 4])}
# этап 2: ось → все значения (значение базы стороны = родитель)
S2_AXES = {"ls": [0, 0.5, 1], "bm": [0, 1, 2, 3], "rr": [1.5, 2, 3], "rf": [0, 0.1, 0.2, 0.3], "rs": [0, 2, 5, 20], "tt": [5, 10, 15, 20, 30, 60], "tu": [5, 15, 30, 60]}


def sig2(x):
    return float("%.2g" % x)


def base_val(side, key):
    d = ax.parse_line(ax.BASE_LINE(side))
    return float(d[key]) if key in d else float(ax.DEFAULTS[key])


def sigkey(patch):
    return tuple(sorted((k, ax.num(patch[k])) for k in ax.SIG_KEYS + SIG_EXTRA if k in patch))


def mk(side, patch, **info):
    ck = ax.canon(ax.BASE_LINE(side) + (" " + ax.patch_str(patch) if patch else ""))
    return dict(ckey=ck, cid=ax.cell_id(patch, []), patch=patch, filt=[], sig=sigkey(patch), **info)


def stage1_cells(side):
    """{ckey: клетка} этапа 1: fam — семья (ось T внутри), hz — ключ соседей по окну того же вида."""
    out = {}

    def add(block, s, T, vs, patch, extra, vz=0, vr=0):
        p = dict(s=s, **patch)
        c = mk(side, p, stage=1, block=block, T=T, s=s, vs=vs, fam=(block, vs, vz, vr, extra, s),
               hz=None if vz else (block, SRC[vs][1], extra, s, T), hw=SRC[vs][2])
        out.setdefault(c["ckey"], c)
    for s, tl in TS.items():
        for T in tl:
            for vs, (_, _, _, vbar) in SRC.items():
                add("A1", s, T, vs, dict(vf=1, vs=vs, va=sig2(T / vbar)), 0.5)
                for vm in (1, 1.5):
                    if vm < T:
                        add("A2", s, T, vs, dict(vf=1, vs=vs, va=sig2(T / vbar), vm=vm), vm)
                for b in (0.5, 1, 1.5):
                    if T - b >= 0.5 - 1e-9:
                        add("A3", s, T, vs, dict(vf=2, vs=vs, va=sig2((T - b) / vbar), vb=b), b)
                for p in (0.5, 0.75):
                    add("A4", s, T, vs, dict(vf=3, vs=vs, va=sig2(T / vbar ** p), vp=p), p)
                for c_ in (0.5, 1, 2):
                    add("A5", s, T, vs, dict(vf=4, vs=vs, va=sig2(T / np.log(1 + 1 / c_)), v0=round(c_ * vbar, 3)), c_)
            for a_, b_ in REGIME:
                for vs, vz, rho in ((a_, b_, 0.5), (a_, b_, 1.5), (b_, a_, 1.5)):
                    vb_ = SRC[vs][3] ** (1 - rho) * SRC[vz][3] ** rho
                    add("A6", s, T, vs, dict(vf=1, vs=vs, va=sig2(T / vb_), vz=vz, vr=rho), rho, vz=vz, vr=rho)
    return out


def w_cells(side):
    """{ckey: клетка} блока W; db_only — значение берётся только из БД (не считается)."""
    out = {}
    for key, (new, old) in W_AXES.items():
        b = base_val(side, key)
        for v, db_only in [(x, False) for x in new] + [(x, True) for x in old]:
            patch = {} if float(v) == b else {key: v}
            c = mk(side, patch, stage="W", axis=key, val=v, db_only=db_only)
            out.setdefault(c["ckey"], c)
    return out


def store_rows(store, side, ckeys):
    rows = store.cells_lookup(SIDENAME[side], m.UNI, m.WIN, list(ckeys), bins=m.eq_bins())
    for r in rows.values():
        if r["sharpe"] is None:
            r["sharpe"] = float("nan")
    return rows


def base_row(store, side):
    ck = ax.canon(ax.BASE_LINE(side))
    r = store_rows(store, side, [ck]).get(ck)
    if r is None:
        raise SystemExit("нет базы стороны %s в БД" % side)
    return r


def fixed_rows(store, side):
    ks = {s: ax.canon(ax.BASE_LINE(side) + " s=%s" % ax.num(s)) for s in (1, 1.5, 2, 3, 4)}
    rows = store_rows(store, side, ks.values())
    return {s: rows[k] for s, k in ks.items() if k in rows}


# ---- гейты --------------------------------------------------------------------------------------------------------------------------
def cmd_gates(a):
    m.cmd_gates(a)
    eq = json.load(open("/data/p42-tk674/res/eqbins674.json", encoding="utf-8"))
    md = m.bin_md5(BIN)
    eq = [md] + [x for x in eq if x != md]
    json.dump(eq, open(t.q("res", "eqbins674.json"), "w", encoding="utf-8"))
    say("равные бинари: %s" % ", ".join(x[:8] for x in eq))


# ---- разбор строк движком, счёт, метрики -----------------------------------------------------------------------------------------------
def validate(side, cells, tag):
    """{cid: причина} клеток, отвергнутых движком при разборе строки (одна монета, без P&L)."""
    import tk604_run as r4
    st, r5, _, _ = t._srv()
    u = r5.Uni(m.UNI)
    w = st.TRADE_WINDOWS[0]
    coin = open(u.coins_file, encoding="utf-8").read().strip().split(",")[0]
    lines = {c["cid"]: t.variant_line(r5, u, side, c["patch"]) for c in cells}
    inv = {}
    d = u.wdir(w.name)
    out = os.path.join(ROOT, "validate", "%s-%s-%s" % (tag, w.name, side))
    os.makedirs(out, exist_ok=True)
    for _ in range(200):
        vf = out + ".variants.txt"
        with open(vf, "w", encoding="utf-8") as f:
            f.write("".join("%s %s\n" % (k, v) for k, v in lines.items() if k not in inv))
        if len(inv) == len(lines):
            break
        cmd = [r4.BIN, "breakout", "tick", "--cache", d + "/cache", "--trades", d + "/aggtrades", "--variants", vf, "--symbols", coin, "--out", out, "--threads", "2"] + r5.TICK_STORE
        r = subprocess.run(cmd, env=dict(os.environ, P42_T0=w.t0, **m.one_side_env(side)), capture_output=True, text=True)
        if r.returncode == 0:
            break
        msg = r.stderr
        if "Error: " not in msg or "Caused by:" not in msg:
            raise SystemExit("validate %s: сбой не при разборе варианта:\n%s" % (side, msg[-600:]))
        err = msg.split("Error: ", 1)[1]
        first = err.split("\n", 1)[0].split()[0]
        why = err.split("Caused by:", 1)[1].strip().split("\n")[0].strip()
        if first not in lines or first in inv:
            raise SystemExit("validate %s: неопознанный вариант «%s»: %s" % (side, first, msg[-400:]))
        inv[first] = why
    else:
        raise SystemExit("validate %s: > 200 отвергнутых" % side)
    say("validate %s %s: вариантов %d, отвергнуто движком %d%s" % (tag, side, len(lines), len(inv), "".join("\n   %s: %s" % kv for kv in inv.items())))
    return inv


def load_json(name, default):
    f = os.path.join(ROOT, "res", name)
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else default


def run_cells(side, todo):
    st, r5, _, _ = t._srv()
    u = r5.Uni(m.UNI)
    index = load_json("batches676-%s.json" % side, {})
    for kind, cur in m.batches(todo):
        nm = m.batch_name(kind, cur)
        index[nm] = [c["ckey"] for c in cur]
        for w in st.TRADE_WINDOWS:
            out = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side), nm)
            done = os.path.join(ROOT, "done", "%s-%s-%s" % (w.name, side, nm))
            if os.path.exists(done):
                continue
            os.makedirs(out, exist_ok=True)
            r5.tick(u, w, [(c["cid"], t.variant_line(r5, u, side, c["patch"])) for c in cur], out, m.one_side_env(side), t.q("log", "cells-%s-%s-%s.log" % (w.name, side, nm)))
            os.makedirs(os.path.dirname(done), exist_ok=True)
            open(done, "w").write("%d\n" % len(cur))
            say("cells %s-%s %s [%d вариантов, проходов %d] готово" % (w.name, side, nm, len(cur), len({c["sig"] for c in cur})))
    json.dump(index, open(t.q("res", "batches676-%s.json" % side), "w", encoding="utf-8"))
    return {ck: nm for nm, cks in index.items() for ck in cks}


def row_job(job):
    """Строка метрик клетки (`tk674_run._row_job`) + статистика стопа: расстояние стопа сделок из `<сторона>-vstop.csv`, убыток stop-выходов,
    контроль c по поправке §13.73.1 (только stop-выходы полного размера: убыток ≥ 90 % от 500 $ × d / 100)."""
    res = m._row_job(job)
    if "error" in res:
        return res
    side, ckey, cid, bname, line, patch, filt, eff = job
    st, p8 = m._W["st"], m._W["p8"]
    dirs = {w.name: (os.path.join(ROOT, "cells", "%s-%s" % (w.name, side), bname, cid), side) for w in st.TRADE_WINDOWS}
    tables, _ = p8.read_tables(dirs)
    kinds, pnl = p8.kinds_pnl(tables)
    kinds, pnl = np.array(kinds), np.asarray(pnl, dtype=float)
    s_pct = float(patch.get("s", 5))
    d_pct = np.full(len(pnl), s_pct)
    vs = dict(on=False)
    if float(patch.get("vf", 0)) > 0:
        V = []
        for w in st.TRADE_WINDOWS:
            tb = tables[w.name]
            with open(os.path.join(dirs[w.name][0], "%s-vstop.csv" % SIDENAME[side]), encoding="utf-8") as f:
                vd = np.array([int(r["vd_pm"]) for r in csv.DictReader(f)], dtype=np.int64)
            if len(vd) != len(tb.entry_ms):
                return dict(error="G-vstop %s %s: строк vstop %d ≠ сделок %d" % (w.name, cid, len(vd), len(tb.entry_ms)), ckey=ckey)
            dd = st.abs_day(w, tb.entry_ms)
            V.append(vd[(dd >= (w.lod - st.D0).days) & (dd <= (w.hid - st.D0).days)])
        vd = np.concatenate(V) if V else np.zeros(0, dtype=np.int64)
        if len(vd) != len(pnl):
            return dict(error="G-vstop %s: сделок vstop %d ≠ %d" % (cid, len(vd), len(pnl)), ckey=ckey)
        ok = vd >= 0
        vm_pm, s_pm = int(round(float(patch.get("vm", 0.5)) * 1000)), int(round(s_pct * 1000))
        if ok.any() and (vd[ok].max() > s_pm or vd[ok].min() < min(vm_pm, s_pm)):
            res["errs"].append("G-vstop: расстояние вне [vm, s]")
        d_pct = np.where(ok, vd / 1000.0, s_pct)
        vs = dict(on=True, d_mean=float(d_pct.mean()) if len(d_pct) else None, d_min=float(d_pct.min()) if len(d_pct) else None, d_max=float(d_pct.max()) if len(d_pct) else None,
                  floor=float((vd[ok] == vm_pm).mean()) if ok.any() else 0.0, cap=float((vd[ok] == s_pm).mean()) if ok.any() else 0.0, none=float((~ok).mean()) if len(ok) else 0.0)
    hard = kinds == "stop"
    loss = -pnl[hard]
    base = STAKE * d_pct[hard] / 100.0
    full = loss >= 0.9 * base
    vs.update(n_stop=int(hard.sum()), stop_mean_loss=float(loss.mean()) if hard.any() else None, stop_worst=float(loss.max()) if hard.any() else None,
              n_full=int(full.sum()), c_full=float((loss[full] - base[full]).mean()) if full.any() else None)
    res["m"]["vstop"] = vs
    return res


def tables(side, cells, where, entry):
    st, r5, _, _ = t._srv()
    u = r5.Uni(m.UNI)
    binm = m.bin_md5(BIN)
    jobs = [(side, c["ckey"], c["cid"], where[c["ckey"]], t.variant_line(r5, u, side, c["patch"]), c["patch"], c["filt"], m.eff_flags(side, c["patch"])) for c in cells]
    bad, n_new, rows = [], 0, []
    with mp.Pool(3, initializer=m._winit) as pool:
        for res in pool.imap_unordered(row_job, jobs, chunksize=8):
            if "error" in res:
                bad.append(res["error"])
                continue
            mm = res["m"]
            bad += ["%s %s: %s" % (side, mm["cid"], e) for e in res["errs"]]
            rows.append(dict(side=SIDENAME[side], uni=m.UNI, win=m.WIN, ckey=res["ckey"], bin=binm, cell=mm["cid"], trades=mm["n"], usd=mm["usd"], mean_usd=mm["mean_usd"], sharpe=mm["sharpe"],
                             extra=mm, src="tools/p42/tk676_run.py", src_ptr="srv:%s/cells/*-%s/%s" % (ROOT, side, mm["cid"])))
    if bad:
        for b in bad[:30]:
            say("СТОП: " + b)
        sys.exit(1)
    store = m.chron_store()
    for i in range(0, len(rows), 200):
        n_new += store.cells_put(entry, rows[i:i + 200], author="TK-676")["new"]
    say("   %s: строк записано %d" % (side, n_new))


def count_stage(side, cells, tag, entry):
    """Поиск в БД → разбор → счёт отсутствующих → метрики в БД. Возвращает {cid: причина} отвергнутых."""
    store = m.chron_store(ro=True)
    found = store_rows(store, side, cells)
    todo = [c for k, c in cells.items() if k not in found and not c.get("db_only")]
    miss_db = [c["cid"] for k, c in cells.items() if k not in found and c.get("db_only")]
    say("== %s %s: клеток %d, в БД есть %d, считаем %d%s" % (tag, side, len(cells), len(found), len(todo), ("; нет в БД (только справка, не считаются): " + ", ".join(miss_db)) if miss_db else ""))
    if not todo:
        return {}
    inv = validate(side, todo, tag)
    todo = [c for c in todo if c["cid"] not in inv]
    where = run_cells(side, todo)
    tables(side, todo, where, entry)
    return inv


def need_gates():
    sha = m._bin()
    g = load_json("gbase674.json", None)
    if g is None or g["bad"] or g["bin_sha256"] != sha:
        raise SystemExit("G-base не пройден или бинарь подменён")


def cmd_plan(a):
    store = m.chron_store(ro=True)
    for side in SIDES:
        for tag, cells in (("этап 1", stage1_cells(side)), ("блок W", w_cells(side))):
            found = store_rows(store, side, cells)
            new = [c for k, c in cells.items() if k not in found and not c.get("db_only")]
            say("%s %s: клеток %d, есть %d, считаем %d, проходов сигналов %d" % (tag, side, len(cells), len(found), len(new), len({c["sig"] for c in new})))


def cmd_stage1(a):
    need_gates()
    inv = load_json("invalid676.json", {})
    for side in SIDES:
        inv["1-" + side] = count_stage(side, stage1_cells(side), "s1", a.entry)
        inv["W-" + side] = count_stage(side, w_cells(side), "w", a.entry)
        json.dump(inv, open(t.q("res", "invalid676.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("stage1 готово")


# ---- охраны, родители, этап 2 -----------------------------------------------------------------------------------------------------------
def neighbours1(cells):
    fam, hz = {}, {}
    for k, c in cells.items():
        fam.setdefault(c["fam"], []).append((c["T"], k))
        if c["hz"] is not None:
            hz.setdefault(c["hz"], []).append((c["hw"], k))
    nb = {k: [] for k in cells}
    for grp in list(fam.values()) + list(hz.values()):
        grp.sort()
        for i, (_, k) in enumerate(grp):
            nb[k] += [grp[j][1] for j in (i - 1, i + 1) if 0 <= j < len(grp)]
    return nb


def guard_cells(cells, rows, nb, brow, f_sh):
    """{ckey: (ключ, строка, охраны)} клеток с определённым Шарпом; (г) — относительно опоры F (поправка §13.74.1)."""
    out = {}
    for k in cells:
        r = rows.get(k)
        if r is None or r["sharpe"] != r["sharpe"]:
            continue
        g = m.guards(r, brow)
        nbs = [rows[x]["sharpe"] for x in nb.get(k, []) if x in rows and rows[x]["sharpe"] == rows[x]["sharpe"]]
        nbm = float(np.mean(nbs)) if nbs else None
        g["g"] = bool(nbs) and nbm >= 0.8 * r["sharpe"] and nbm >= f_sh
        g["all"] = all(g[x] for x in "abcdg")
        out[k] = (k, r, g, nbm)
    return out


def anchor(store, side):
    fx = fixed_rows(store, side)
    cand = [(fx[s]["sharpe"], s) for s in (3, 4) if s in fx]
    if not cand:
        raise SystemExit("нет фиксированных стопов 3 / 4 %% стороны %s в БД" % side)
    s = max(cand)[1]
    return s, fx[s], fx


def parents(side, store):
    cells = stage1_cells(side)
    rows = store_rows(store, side, cells)
    brow = base_row(store, side)
    _, frow, _ = anchor(store, side)
    gd = guard_cells(cells, rows, neighbours1(cells), brow, frow["sharpe"])
    best = {}
    for vs in SRC:
        w = m.pick([gd[k] for k in gd if cells[k]["vs"] == vs and gd[k][2]["all"]])
        if w:
            best[vs] = w
    top = sorted(best, key=lambda v: -best[v][1]["sharpe"])[:3]
    return [cells[best[v][0]] for v in top]


def stage2_cells(side, pars):
    out = {}
    for p in pars:
        for key, vals in S2_AXES.items():
            b = base_val(side, key)
            for v in vals:
                if float(v) == b:
                    continue
                c = mk(side, dict(p["patch"], **{key: v}), stage=2, parent=p["ckey"], axis=key, val=v)
                out.setdefault(c["ckey"], c)
    return out


def neighbours2(side, cells):
    grp = {}
    for k, c in cells.items():
        grp.setdefault((c["parent"], c["axis"]), []).append((float(c["val"]), k))
    nb = {k: [] for k in cells}
    for (par, key), g in grp.items():
        g = sorted(g + [(base_val(side, key), par)])
        for i, (_, k) in enumerate(g):
            if k in nb:
                nb[k] += [g[j][1] for j in (i - 1, i + 1) if 0 <= j < len(g)]
    return nb


def cmd_stage2(a):
    need_gates()
    inv = load_json("invalid676.json", {})
    pj = {}
    for side in SIDES:
        pars = parents(side, m.chron_store(ro=True))
        pj[side] = [dict(ckey=p["ckey"], cid=p["cid"], patch=p["patch"]) for p in pars]
        say("родители %s: %s" % (side, ", ".join(p["cid"] for p in pars) or "нет (охранённых клеток этапа 1 нет)"))
        if pars:
            inv["2-" + side] = count_stage(side, stage2_cells(side, pars), "s2", a.entry)
    json.dump(pj, open(t.q("res", "parents676.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(inv, open(t.q("res", "invalid676.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("stage2 готово")


# ---- отчёт -----------------------------------------------------------------------------------------------------------------------------
def boot(dw, df, B=20000, block=5, seed=676):
    """Парная суточная разница (победитель − F): Σ $ с 95 % интервалом и выборочная ошибка разницы годового Шарпа — блочный бутстреп по суткам."""
    dw, df = np.asarray(dw, dtype=float), np.asarray(df, dtype=float)
    n = len(dw)
    nbk = -(-n // block)
    rng = np.random.default_rng(seed)
    idx = ((rng.integers(0, n, size=(B, nbk))[:, :, None] + np.arange(block)) % n).reshape(B, -1)[:, :n]
    sums = (dw - df)[idx].sum(axis=1)

    def sh(x):
        sd = x.std(axis=1, ddof=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(sd > 0, x.mean(axis=1) / sd * np.sqrt(365.0), np.nan)
    dsh = sh(dw[idx]) - sh(df[idx])                            # формула `tk625_stats.sharpe_year`: среднее / σ (ddof = 1) × √365
    return dict(usd=float((dw - df).sum()), lo=float(np.quantile(sums, 0.025)), hi=float(np.quantile(sums, 0.975)), se_sharpe=float(np.nanstd(dsh)))


def line_of(r, label, s_pct=None):
    ex = r["extra"]
    lm, v, k = ex["lmax"], ex.get("vstop") or {}, ex["kpi"]
    sm = v.get("stop_mean_loss")
    if sm is None and lm.get("c_stop") is not None:
        sm = STAKE * lm["s"] / 100.0 + lm["c_stop"]
    return dict(label=label, cid=r["cell"], rr=ex["rr"]["rr"], stop_mean_loss=sm, worst=-lm["worst"], lmax=lm["usd"], lmax_pct=lm["pct_margin"], usd=r["usd"], mean_usd=r["mean_usd"], n=r["trades"],
                sharpe=r["sharpe"], maxdd=ex["maxdd"], maxdd_pct=ex["maxdd_pct"], gap_longest=k["gap_longest"], gap_mean=k["gap_mean"], p90=k["p90"], tail=k["gap_tail"], stops=lm["n_stop"],
                months=ex["months"], folds=ex["folds"], vstop=v)


HDR = "| линия | R:R | убыток по стопу: средний / худшая сделка / максимально возможный, $ (% маржи) | Σ $ | $ на сделку | сделок | Шарп | просадка $ (% маржи) | перехай: длинный / средний / p90 / хвост, сут. | stop-выходов | стоп: средний d % / зажим низ / верх / нет V |\n|---|---|---|---|---|---|---|---|---|---|---|"


def md_row(x):
    v = x["vstop"]
    vtxt = "%.2f / %.0f %% / %.0f %% / %.0f %%" % (v["d_mean"], 100 * v["floor"], 100 * v["cap"], 100 * v["none"]) if v.get("on") else "фикс."
    sm = "%.2f" % x["stop_mean_loss"] if x["stop_mean_loss"] is not None else "–"
    return "| %s `%s` | %.2f | %s / %.2f / %.2f (%.1f %%) | %+.0f | %+.3f | %d | %.2f | %.0f (%.0f %%) | %d / %.1f / %.0f / %d | %d | %s |" % (
        x["label"], x["cid"], x["rr"], sm, x["worst"], x["lmax"], x["lmax_pct"], x["usd"], x["mean_usd"], x["n"], x["sharpe"], x["maxdd"], x["maxdd_pct"],
        x["gap_longest"], x["gap_mean"], x["p90"], x["tail"], x["stops"], vtxt)


def cmd_report(a):
    store = m.chron_store(ro=True)
    pj = load_json("parents676.json", {})
    inv = load_json("invalid676.json", {})
    rep, md = {}, []
    for side in SIDES:
        c1, cw = stage1_cells(side), w_cells(side)
        pars = [c1[p["ckey"]] for p in pj.get(side, [])]
        c2 = stage2_cells(side, pars)
        rows = store_rows(store, side, list(c1) + list(c2) + list(cw))
        brow = base_row(store, side)
        fs, frow, fx = anchor(store, side)
        gd = guard_cells(c1, rows, neighbours1(c1), brow, frow["sharpe"])
        gd.update(guard_cells(c2, rows, neighbours2(side, c2), brow, frow["sharpe"]))
        cells = dict(c1, **c2)
        ok = [gd[k] for k in gd if gd[k][2]["all"]]
        win = m.pick(ok)
        sec = [k for k in list(cells) + [k for k, c in cw.items() if not c.get("db_only")] if k in rows]
        m_sec = len(sec)
        m_all = sum(r["n"] for r in store.cells_count() if r["side"] == SIDENAME[side] and r["uni"] == m.UNI and r["win"] == m.WIN)
        shs = np.array([rows[k]["sharpe"] for k in sec if rows[k]["sharpe"] == rows[k]["sharpe"]])
        by_sig = {}
        for k in sec:
            by_sig.setdefault(rows[k]["extra"].get("trades_sig"), []).append(k)
        dup = [v for v in by_sig.values() if len(v) > 1]
        cc = [rows[k]["extra"]["vstop"]["c_full"] for k in sec if (rows[k]["extra"].get("vstop") or {}).get("n_full", 0) >= 20]
        R = dict(side=side, base=line_of(brow, "база (стоп 5 %, справка)"), F=line_of(frow, "опора F: фикс. стоп %g %%" % fs), fixed={str(s): line_of(r, "фикс. %g %%" % s) for s, r in fx.items()},
                 m_sec=m_sec, m_all=m_all, sharpe_sd_cells=float(shs.std()) if len(shs) > 1 else None, n_guarded=len(ok), n_cells=len([k for k in cells if k in rows]),
                 n_guard={x: sum(1 for k in gd if gd[k][2][x]) for x in "abcdg"}, g_dup=dict(groups=len(dup), cells=sum(len(v) for v in dup)),
                 c_check=dict(cells=len(cc), mean=float(np.mean(cc)) if cc else None, min=min(cc) if cc else None, max=max(cc) if cc else None),
                 parents=[p["cid"] for p in pars], invalid={k: v for k, v in inv.items() if k.endswith("-" + side)})
        verdict = "не работает"
        if win:
            wr = win[1]
            bt = boot(wr["extra"]["daily"], frow["extra"]["daily"])
            folds_pos = sum(1 for (_, a_), (_, b_) in zip(wr["extra"]["folds"], frow["extra"]["folds"]) if a_ - b_ > 0)
            gain = wr["sharpe"] - frow["sharpe"]
            noise = bt["se_sharpe"] * float(np.sqrt(2 * np.log(max(m_sec, 2))))
            better = gain > 0 and wr["usd"] >= frow["usd"]
            if better:
                verdict = "работает" if (bt["lo"] > 0 and folds_pos >= 3 and gain >= noise) else "не ясно"
            R.update(winner=line_of(wr, "победитель"), win_stage=cells[win[0]]["stage"], win_patch=cells[win[0]]["patch"], win_nb_mean=win[3], boot=bt, folds_pos=folds_pos, gain=gain, noise=noise,
                     noise_all=bt["se_sharpe"] * float(np.sqrt(2 * np.log(max(m_all, 2)))), below_base=bool(wr["sharpe"] < brow["sharpe"]), better=bool(better))
        R["verdict"] = verdict
        # лучшие охранённые клетки по блокам и источникам (справка, те же клетки)
        tops = sorted(ok, key=lambda c: -c[1]["sharpe"])[:10]
        R["top"] = [line_of(c[1], "этап %s" % cells[c[0]]["stage"]) for c in tops]
        best_blk = {}
        for k, c in c1.items():
            if k in gd:
                key = (c["block"], "да" if gd[k][2]["all"] else "нет")
                if key not in best_blk or gd[k][1]["sharpe"] > best_blk[key][1]["sharpe"]:
                    best_blk[key] = gd[k]
        R["best_block"] = [dict(block=b, guarded=g, **line_of(v[1], "%s, охрана %s" % (b, g))) for (b, g), v in sorted(best_blk.items())]
        wt = []
        for k, c in cw.items():
            if k in rows:
                g = m.guards(rows[k], brow)
                wt.append(dict(axis=c["axis"], val=c["val"], new=not c.get("db_only"), guards="".join(x for x in "abcd" if not g[x]) or "ок", **line_of(rows[k], "%s=%s" % (c["axis"], ax.num(c["val"])))))
        R["W"] = wt
        rep[side] = R
        # ---- md
        md.append("\n## %s — %s\n" % ("Лонг" if side == "L" else "Шорт", verdict.upper()))
        md.append("Клеток раздела m_р = %d (всего клеток стороны в БД m = %d), охранённых (а)–(д) %d из %d; прошли охрану: (а) %d, (б) %d, (в) %d, (д) %d, (г) %d; G-dup: %d групп / %d клеток; контроль c: %s; родители этапа 2: %s.\n" % (
            m_sec, m_all, len(ok), R["n_cells"], R["n_guard"]["a"], R["n_guard"]["b"], R["n_guard"]["c"], R["n_guard"]["d"], R["n_guard"]["g"], len(dup), R["g_dup"]["cells"],
            ("среднее %.3f $ (мин %.3f, макс %.3f; клеток %d)" % (R["c_check"]["mean"], R["c_check"]["min"], R["c_check"]["max"], len(cc))) if cc else "клеток с ≥ 20 полными стопами нет",
            ", ".join("`%s`" % p for p in R["parents"]) or "нет"))
        md.append(HDR)
        md.append(md_row(R["base"]))
        for s in sorted(fx):
            md.append(md_row(R["fixed"][str(s)]))
        if win:
            md.append(md_row(R["winner"]))
            bt = R["boot"]
            md.append("\nПобедитель против опоры F: Δ Σ %+.0f $ [%+.0f; %+.0f] (95 %%, бутстреп по суткам), складок с Δ > 0: %d из 4, прибавка Шарпа %+.2f при шуме выбора %.2f (m_р) / %.2f (m), Шарп победителя %s Шарпа базы 5 %%. Найдено на этих данных, ждёт проверки вперёд.\n" % (
                bt["usd"], bt["lo"], bt["hi"], R["folds_pos"], R["gain"], R["noise"], R["noise_all"], "ниже" if R["below_base"] else "не ниже"))
            md.append("Месяцы (сделок: Σ $) победитель: %s; F: %s. Складки победитель: %s; F: %s.\n" % tuple(
                ", ".join("%d: %+.0f" % (n_, s_) for n_, s_ in x) for x in (R["winner"]["months"], R["F"]["months"], R["winner"]["folds"], R["F"]["folds"])))
        else:
            md.append("\nОхранённой клетки нет.\n")
        md.append("Лучшие клетки блоков этапа 1 (справка; «охрана да» — все охраны):\n\n" + HDR)
        md += [md_row(x) for x in R["best_block"]]
        md.append("\nДесять лучших охранённых клеток (справка, те же данные):\n\n" + HDR)
        md += [md_row(x) for x in R["top"]]
        md.append("\nБлок W — окна остальных параметров (одна ось от базы; «охраны» — какие из (а)(б)(в)(д) не пройдены):\n\n" + HDR.replace("| линия |", "| параметр = значение (новое / БД) | охраны |", 1).replace("|---|", "|---|---|", 1))
        for x in sorted(wt, key=lambda x: (x["axis"], float(x["val"]))):
            md.append(md_row(x).replace("| %s `%s` |" % (x["label"], x["cid"]), "| %s (%s) | %s |" % (x["label"], "новое" if x["new"] else "БД", x["guards"]), 1))
    json.dump(rep, open(t.q("res", "report676.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(t.q("out", "report676.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    for side in SIDES:
        R = rep[side]
        say("== %s: вердикт «%s»; охранённых %d из %d; победитель %s" % (side, R["verdict"], R["n_guarded"], R["n_cells"], R.get("winner", {}).get("cid")))
    say("report готово")


def cmd_all(a):
    cmd_gates(a)
    cmd_stage1(a)
    cmd_stage2(a)
    cmd_report(a)


COMMANDS = {"gates": cmd_gates, "plan": cmd_plan, "stage1": cmd_stage1, "stage2": cmd_stage2, "report": cmd_report, "all": cmd_all}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--entry", help="запись летописи L-N для строк клеток")
    a = ap.parse_args()
    if a.cmd in ("stage1", "stage2", "all") and not a.entry:
        raise SystemExit("--entry L-N")
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
