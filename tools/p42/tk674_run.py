#!/usr/bin/env python3
"""П-42 §13.73 (поправка 22.11, TK-674): карта всех совместных пар осей линии на h775, результаты — в БД летописи (метрики с каноническим ключом клетки).
  python3 tk674_run.py probe     — замер стоимости на окне wa новым бинарём: база (G-base wa), 72 клетки выхода, 26 проходов сигналов (без P&L)
  python3 tk674_run.py gates     — G-bin, G-base: 6 строк баз (L, S × wa / wb / wc) новым бинарём = готовым базам h775 байт в байт
  python3 tk674_run.py counts    — пороги признаков (абсолютные), столбцы `--ftab` составных фильтров, колонки на сделках базы — без P&L
  python3 tk674_run.py plan      — клетки всех пар, канонические ключи, поиск в БД летописи: «есть / считаем» по сеткам, оценка часов → res/plan674-<сторона>.json
  python3 tk674_run.py validate  — сухой разбор строк клеток движком (одна монета, без P&L): недопустимые сочетания ключей → res/invalid674-<сторона>.json
  python3 tk674_run.py cells     — движок: только клетки «считаем» (окна wa / wb / wc, `P42_SIDES` — одна сторона), юниты `tk674-*`
  python3 tk674_run.py tables --entry L-N — метрики клеток (В-169) → БД летописи (строки `metric` с каноническим ключом); охраны G7, G-off, G-key, G-tape, c-проверка Lmax
  python3 tk674_run.py map       — карта пар из БД: охраны (а)–(е), победитель пары, плато, таблица top 15 по стороне → res/map674-<сторона>.json
Механика склейки, статистика — `tk643_run` (§13.43) без изменений. Доллары — ставка 500 $; Sharpe — годовой по суточному ряду Σ $ за 136 суток; без бп, без медиан."""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk643_run as t  # noqa: E402
import tk674_axes as ax  # noqa: E402

ROOT = os.environ.get("TK674_ROOT", "/data/p42-tk674")
t.ROOT = ROOT                                   # run_variants / q / cell_dir пишут в каталог этой задачи
BIN = os.environ.get("TK674_BIN", ROOT + "/bin/alpha-tk674")   # бинарь HEAD ветки (md5 a26ce03f…); клетки оси split — бинарь TK-677 (TK674_BIN, G-base — TK674_GBASE)
GBASE = os.environ.get("TK674_GBASE", ROOT + "/res/gbase674.json")
SIDES = ax.SIDES
ENGNAME = {"L": "long", "S": "short"}
CMP_FILES = ("trades.csv", "daily.csv", "coinday.csv", "entryrej.csv")
say = t.say
LEVEL = int(os.environ.get("TK674_LEVEL", "2"))  # уровень карты: 2 — пары; 3, 4 — тройки, четвёрки (клетки уровня — `tk674_level.py select`); 1 — единички TK-680 (`tk680_singles.py select`)
AUTHOR = os.environ.get("TK674_AUTHOR", "TK-674")
FSUF = str(LEVEL) if LEVEL != 2 else ""          # таблица составных фильтров уровня (ftab674<FSUF>)
SUF = ax.SUF2 + FSUF         # файлы плана / партий / карты при базе стопа ≤ 4 % (поправка §13.73.2) и уровня — рядом с прежними


def _bin():
    import tk604_run as r4
    r4.BIN = BIN                                # r5.tick берёт r4.BIN при вызове
    sha = r4.sha256(BIN)
    json.dump(dict(bin=BIN, sha256=sha), open(t.q("res", "bin674.json"), "w", encoding="utf-8"), indent=1)
    return sha


def invalid_path(side):
    return os.path.join(ROOT, "res", "invalid674%s-%s.json" % (SUF, side))


def load_invalid(side):
    """{cid: причина} клеток, которые движок отвергает при разборе строки (стадия `validate`); пусто, пока стадия не запускалась."""
    f = invalid_path(side)
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else {}


def build_cells(side, thr=None):
    """{канонический ключ: клетка} по всем парам: клетка = dict(ckey, cid, patch, filt, sig (ключ прохода), pairs (где встречается));
    thr — {признак: {метка: порог}} для фильтровых клеток (None — фильтровые клетки пропускаются). Недопустимые позиции — в bad."""
    grids, skipped = ax.pair_grids(side)
    cells, bad = {}, {}
    inv = load_invalid(side)
    for (a, b), g in grids.items():
        for pos, (patch, filt, ok, why) in g["cells"].items():
            if filt and thr is None:
                continue
            if not ok:
                bad[(a, b, pos)] = why
                continue
            if ax.cell_id(patch, filt) in inv:
                bad[(a, b, pos)] = "движок: " + inv[ax.cell_id(patch, filt)]
                continue
            ft = [(thr[n][lab][0], thr[n][lab][1], thr[n][lab][2]) for n, lab in filt] if filt else []
            ck = ax.canon(ax.BASE_LINE(side) + (" " + ax.patch_str(patch) if patch else ""), ft)
            c = cells.setdefault(ck, dict(ckey=ck, cid=ax.cell_id(patch, filt), patch=patch, filt=filt, sig=ax.sigkey(patch), pairs=[]))
            c["pairs"].append((a, b, pos))
    return cells, grids, skipped, bad


def timed(label, fn):
    t0 = time.time()
    fn()
    dt = time.time() - t0
    say("probe %-28s %7.1f с" % (label, dt))
    return dt


def cmd_probe(a):
    st, r5, _, _ = t._srv()
    sha = _bin()
    say("G-bin: %s sha256 %s" % (BIN, sha))
    u = r5.Uni("h775")
    w = st.TRADE_WINDOWS[0]
    res = {}

    one = bool(os.environ.get("TK674_ONESIDE"))                  # P42_SIDES (TK-671): считать только одну сторону

    def run(label, side, variants):
        label += "-1s" if one else ""
        out = os.path.join(ROOT, "probe", "%s-%s" % (label, w.name))
        os.makedirs(out, exist_ok=True)
        env = {"P42_SIDES": ENGNAME[side]} if one else {}
        res[label] = dict(variants=len(variants), sec=timed(label, lambda: r5.tick(u, w, variants, out, env, t.q("log", "probe-%s.log" % label))))
    cells, _, _, _ = build_cells("S")
    exit_only = [c for c in cells.values() if not c["sig"] and not c["filt"]][:72]
    sig_cells = {}
    for c in cells.values():
        if c["sig"] and not c["filt"] and c["sig"] not in sig_cells and len(c["sig"]) == 1:
            sig_cells[c["sig"]] = c
    sig26 = list(sig_cells.values())[:26]
    for side in SIDES:
        run("base%s" % side, side, [("base", t.variant_line(r5, u, side, {}))])
    run("exit72", "S", [(c["cid"], t.variant_line(r5, u, "S", c["patch"])) for c in exit_only])
    run("sig26", "S", [(c["cid"], t.variant_line(r5, u, "S", c["patch"])) for c in sig26])
    mix = []
    for c in sig26:
        for extra in ({"s": 3}, {"rf": 0.1}, {"tt": 10}):
            p = dict(c["patch"], **extra)
            mix.append(("%s+%s" % (c["cid"], list(extra)[0]), t.variant_line(r5, u, "S", p)))
    run("sig26x3", "S", mix)
    json.dump(res, open(t.q("res", "probe674%s.json" % ("-1s" if one else "")), "w", encoding="utf-8"), indent=1)
    off = g_base_window(st, w.name, "-1s" if one else "")
    say("G-base %s: %s" % (w.name, "ok" if not off else "ОШИБКА " + ", ".join(off)))


def g_base_window(st, wname, suf=""):
    """Строки базы пакета (probe) = готовым базам h775 байт в байт: файлы CSV стороны."""
    r5 = t._srv()[1]
    u = r5.Uni("h775")
    bad = []
    for side in SIDES:
        mine = os.path.join(ROOT, "probe", "base%s%s-%s" % (side, suf, wname), "base")
        ref = u.base(wname, side)
        for f in CMP_FILES:
            p1, p2 = os.path.join(mine, "%s-%s" % (ENGNAME[side], f)), os.path.join(ref, "%s-%s" % (ENGNAME[side], f))
            if not (os.path.exists(p1) and os.path.exists(p2) and filecmp.cmp(p1, p2, shallow=False)):
                bad.append("%s-%s/%s" % (wname, side, f))
    return bad


def cmd_gates(a):
    st, r5, _, _ = t._srv()
    sha = _bin()
    say("G-bin: %s sha256 %s" % (BIN, sha))
    u = r5.Uni("h775")
    bad = []
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            out = os.path.join(ROOT, "gbase", "%s-%s" % (w.name, side))
            if not os.path.exists(out + "/DONE"):
                os.makedirs(out, exist_ok=True)
                r5.tick(u, w, [("base", t.variant_line(r5, u, side, {}))], out, {"P42_SIDES": ENGNAME[side]}, t.q("log", "gbase-%s-%s.log" % (w.name, side)))
                open(out + "/DONE", "w").close()
            mine, ref = os.path.join(out, "base"), u.base(w.name, side)
            for f in CMP_FILES:
                p1, p2 = os.path.join(mine, "%s-%s" % (ENGNAME[side], f)), os.path.join(ref, "%s-%s" % (ENGNAME[side], f))
                if not (os.path.exists(p1) and os.path.exists(p2) and filecmp.cmp(p1, p2, shallow=False)):
                    bad.append("%s-%s/%s" % (w.name, side, f))
    json.dump(dict(bin_sha256=sha, bad=bad), open(t.q("res", "gbase674.json"), "w", encoding="utf-8"), indent=1)
    say("G-base: %s" % ("ok — 6 строк баз (L, S × 3 окна), 4 CSV стороны байт в байт" if not bad else "ОШИБКА " + ", ".join(bad)))
    if bad:
        sys.exit(1)


# ---- константы этапа 1 ------------------------------------------------------------------------------------------------------------
UNI, WIN = os.environ.get("TK674_UNI", "h775"), "01.07–13.11"      # TK674_UNI=c539 — вселенная без TradFi (§13.73.15), клетки в БД под своим именем вселенной
SIDENAME = {"L": "long", "S": "short"}
VMAX, SIGMAX = 400, 26                          # вариантов и разных проходов сигналов на вызов движка (память: 26 проходов ≈ 30 ГБ при двух сторонах)
MIN_N, MIN_MONTH = 150, (15, 15, 15, 15, 8)     # охрана (а) §13.73
FEAT_COLS = ("tpm60", "cons60", "zv6h", "mv5", "n24", "m24")


def bin_md5(path=BIN):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def eq_bins():
    """md5 бинарей, чьи клетки засчитываются как клетки этого бинаря (res/eqbins674.json после G-base и выборочной проверки); по умолчанию — только новый."""
    f = os.path.join(ROOT, "res", "eqbins674.json")
    return list(json.load(open(f, encoding="utf-8"))) if os.path.exists(f) else [bin_md5()]


def chron_store(ro=False):
    sys.path.insert(0, "/data/chron")
    import chron
    return chron.Store(chron.connect(chron.SRV_DB, ro=ro))


def one_side_env(side):
    return {"P42_SIDES": SIDENAME[side]}


# ---- признаки и пороги (без P&L) --------------------------------------------------------------------------------------------------
def _pass(feat, x, tab, op=None):
    """Клетка оставляет кандидата: признак ≤ порога (≥ для n24), «определён и не проходит» — исключён, пусто проходит (как tk654 `_bad`).
    Единички TK-680 (LEVEL = 1): любой столбец таблицы и оба знака — по знаку смысла фильтра (`op`), правило то же."""
    with np.errstate(invalid="ignore"):
        if LEVEL == 1:
            v = np.where(tab["m24"].astype(bool), np.nan, tab["n24"]) if feat == "n24" else tab[feat]
            if op == "<=":
                return ~(np.isfinite(v) & ~(v <= x))
            if op == ">=":
                return ~(np.isfinite(v) & ~(v >= x))
            raise SystemExit("знак %s у признака %s" % (op, feat))
        if feat in ("tpm60", "cons60", "zv6h"):
            v = tab[feat]
            return ~(np.isfinite(v) & ~(v <= x))
        if feat == "n24":
            return ~(~tab["m24"].astype(bool) & (tab["n24"] < x))
        if feat == "sharp":
            cn, mv = tab["cons60"], tab["mv5"]
            ok = np.isfinite(cn) & np.isfinite(mv)
            return ~(ok & ~((cn == 0) & (mv >= x)))
    raise SystemExit("признак %s" % feat)


def ftab_cols(cells):
    """{смысл фильтра (канонический «F=…»): имя столбца fNNN} — детерминированно по отсортированным смыслам."""
    sems = sorted({c["ckey"].split("F=", 1)[1] for c in cells.values() if c["filt"]})
    return {s_: "f%03d" % i for i, s_ in enumerate(sems)}


def parse_sem(sem):
    out = []
    for tok in sem.split("&"):
        for op in ("<=", ">=", "=="):
            if op in tok:
                f_, x = tok.split(op)
                out.append((f_, op, float(x)))
                break
    return out


def thr_path():
    return os.path.join(ROOT, "res", "thr674.json")


def load_thr(side):
    """{ось: {метка: (признак, знак, порог)}} стороны после `counts`."""
    raw = json.load(open(thr_path(), encoding="utf-8"))["thr"][side]
    return {n: {lab: tuple(v) for lab, v in d.items()} for n, d in raw.items()}


def feat_tabs(side, st, u, eps):
    """Таблицы признаков кандидатов стороны по окнам (TK-654 + n24 / m24 из TK-638), с охраной G-key."""
    import tk604_feat as tf
    import tk638_run as t8
    import tk654_run as r54
    hist = tf.episode_history(eps, tf.SIDE_NAME[side])
    tabs = {w.name: r54.load_window(w, side, hist) for w in st.TRADE_WINDOWS}
    for w in st.TRADE_WINDOWS:
        t38 = t8.load_window(u, w, side)
        if list(t38["sym"]) != list(tabs[w.name]["sym"]) or not np.array_equal(t38["ms"], tabs[w.name]["ms"]):
            raise SystemExit("G-key %s %s: строки таблиц TK-638 и TK-654 различаются" % (side, w.name))
        tabs[w.name]["n24"], tabs[w.name]["m24"] = t38["n24"], t38["m24"]
    if any(tabs[w]["gkey_mv5_bad"] for w in tabs):
        raise SystemExit("G-key %s: mv5 эпизодов ≠ таблице TK-634" % side)
    if LEVEL == 1:                                  # единички TK-680: столбцы таблицы TK-671 к тем же строкам кандидатов
        import tk680_singles as s80
        s80.add_columns(side, st, tabs)
    return tabs


def write_ftab(side, st, tabs, cells):
    """Таблица составных фильтров `--ftab` клеток (столбец на смысл фильтра) по окнам → ftab674<FSUF>/<окно>-<сторона>.csv; возвращает {смысл: столбец}."""
    cols = ftab_cols(cells)
    sem_list = {sem: parse_sem(sem) for sem in cols}
    for w in st.TRADE_WINDOWS:
        tab = tabs[w.name]
        pm = {sem: np.logical_and.reduce([_pass(f_, x, tab, op_) for f_, op_, x in parts]) for sem, parts in sem_list.items()}
        path = t.q("ftab674" + FSUF, "%s-%s.csv" % (w.name, side))
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("sym,sig_ms," + ",".join(cols[s_] for s_ in sem_list) + "\n")
            for i in range(len(tab["sym"])):
                f.write("%s,%d,%s\n" % (tab["sym"][i], int(tab["ms"][i]), ",".join("1.0" if pm[s_][i] else "0.0" for s_ in sem_list)))
        say("ftab674%s %s-%s: %d строк, столбцов %d" % (FSUF, w.name, side, len(tab["sym"]), len(cols)))
    return cols


def cmd_ftab(a):
    """Таблица составных фильтров клеток уровня (TK674_LEVEL > 2): пороги — готовые из res/thr674.json, без P&L."""
    import tk654_run as r54
    if LEVEL == 2:
        raise SystemExit("ftab — только для уровней 1, 3, 4 (у пар таблицу пишет counts)")
    st, r5, _, _ = t._srv()
    u = r5.Uni(UNI)
    eps = r54.episodes_h775()
    for side in SIDES:
        cells = plan_side(side)[0]
        cols = write_ftab(side, st, feat_tabs(side, st, u, eps), cells)
        json.dump(cols, open(t.q("res", "ftabcols674%s-%s.json" % (FSUF, side)), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say("== %s: клеток уровня %d, с фильтром %d, составных столбцов %d" % (side, len(cells), sum(1 for c in cells.values() if c["filt"]), len(cols)))
    say("ftab готово")


def cmd_counts(a):
    import tk654_run as r54
    import tk634_run as r34
    st, r5, _, _ = t._srv()
    u = r5.Uni(UNI)
    eps = r54.episodes_h775()
    thr38 = json.load(open("/data/p42-tk638/res/thr38.json", encoding="utf-8"))
    out, report = {}, {}
    for side in SIDES:
        tabs = feat_tabs(side, st, u, eps)

        def qthr(col, p):
            v = np.concatenate([tabs[w.name][col][r54.in_scored_range(w, tabs[w.name]["ms"]) & np.isfinite(tabs[w.name][col])] for w in st.TRADE_WINDOWS])
            return r54.sig2(float(np.quantile(v, p, method="linear")))
        axs = {}
        for name, feat, op, labels in ax.FILTERS[side]:
            d = {}
            for lab in labels:
                if lab == "on":
                    d[lab] = (feat, op, qthr("mv5", 0.5))
                elif lab.startswith("q") and name == "N24":
                    d[lab] = (feat, op, float(thr38[side]["N24"]["q"]["%.2f" % (int(lab[1:]) / 100)]))
                elif lab.startswith("q"):
                    d[lab] = (feat, op, qthr(feat, int(lab[1:]) / 100))
                else:
                    d[lab] = (feat, op, float(lab))
            axs[name] = d
        out[side] = axs
        cells, _, _, _ = build_cells(side, axs)
        cols = write_ftab(side, st, tabs, cells)
        W, S_, R = r34.trade_rows(u, side)
        idx = {w.name: {(x, int(r)): i for i, (x, r) in enumerate(zip(tabs[w.name]["sym"], tabs[w.name]["ms"]))} for w in st.TRADE_WINDOWS}
        tr = {c: np.full(len(W), np.nan) for c in FEAT_COLS}
        for w in st.TRADE_WINDOWS:
            sel = np.nonzero(W == w.name)[0]
            pos = np.array([idx[w.name].get((S_[i], int(R[i])), -1) for i in sel], dtype=np.int64)
            if (pos < 0).any():
                raise SystemExit("%s %s: сделки базы без строки таблицы кандидатов" % (side, w.name))
            for c in FEAT_COLS:
                tr[c][sel] = tabs[w.name][c][pos].astype(float)
        np.savez(t.q("res", "trcols674-%s.npz" % side), W=W.astype(str), S=S_.astype(str), R=R, **tr)
        json.dump(cols, open(t.q("res", "ftabcols674%s-%s.json" % (FSUF, side)), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        report[side] = dict(n_trades=int(len(W)), cols=len(cols), cells_filter=sum(1 for c in cells.values() if c["filt"]))
        say("== %s: сделок базы %d, составных столбцов %d, клеток с фильтром %d" % (side, len(W), len(cols), report[side]["cells_filter"]))
        for name, d in axs.items():
            say("   %-6s %s" % (name, ", ".join("%s → %g" % (lab, v[2]) for lab, v in d.items())))
    json.dump(dict(thr=out, report=report), open(thr_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("counts готово")


# ---- план: клетки, ключи, поиск в БД -----------------------------------------------------------------------------------------------
def level_path(side):
    return os.path.join(ROOT, "res", "level674%s-%s.json" % (SUF, side))


def plan_side(side, store=None):
    if LEVEL != 2:                                  # клетки уровня — из отбора `tk674_level.py select` (правило отсечения — поправка уровня)
        inv = load_invalid(side)
        raw = json.load(open(level_path(side), encoding="utf-8"))["cells"]
        cells = {k: dict(ckey=k, cid=c["cid"], patch=c["patch"], filt=[tuple(x) for x in c["filt"]], sig=tuple(tuple(x) for x in c["sig"]), pairs=[])
                 for k, c in raw.items() if c["cid"] not in inv}
        found = store.cells_lookup(SIDENAME[side], UNI, WIN, list(cells), bins=eq_bins()) if store else {}
        return cells, {}, [], {}, found
    thr = load_thr(side) if os.path.exists(thr_path()) else None
    cells, grids, skipped, bad = build_cells(side, thr)
    found = store.cells_lookup(SIDENAME[side], UNI, WIN, list(cells), bins=eq_bins()) if store else {}
    return cells, grids, skipped, bad, found


def batches(todo):
    """Партии вызовов движка: без / с фильтром (`--ftab`); внутри — по ключу прохода сигналов; ≤ VMAX вариантов и ≤ SIGMAX проходов на вызов."""
    out = []
    for kind, grp in (("p", [c for c in todo if not c["filt"]]), ("f", [c for c in todo if c["filt"]])):
        grp = sorted(grp, key=lambda c: (c["sig"], c["cid"]))
        cur, sg = [], set()
        for c in grp:
            if cur and (len(cur) >= VMAX or (c["sig"] not in sg and len(sg) >= SIGMAX)):
                out.append((kind, cur))
                cur, sg = [], set()
            cur.append(c)
            sg.add(c["sig"])
        if cur:
            out.append((kind, cur))
    return out


def batch_name(kind, cur):
    return kind + hashlib.md5("\n".join(c["ckey"] for c in cur).encode()).hexdigest()[:10]


def cmd_plan(a):
    pf = os.path.join(ROOT, "res", "probe674-1s.json")
    probe = json.load(open(pf, encoding="utf-8")) if os.path.exists(pf) else None
    store = chron_store(ro=True)
    tot_new, tot_h = 0, 0.0
    for side in SIDES:
        cells, grids, skipped, bad, found = plan_side(side, store)
        todo = [c for k, c in cells.items() if k not in found]
        sigs = {c["sig"] for c in todo if c["sig"]}
        cls = {}
        for c in cells.values():
            k = "фильтр" if c["filt"] else ("сигналы" if c["sig"] else "выход")
            cls.setdefault(k, [0, 0])[0] += 1
            cls[k][1] += (c["ckey"] in found)
        bt = batches(todo)
        if probe and "sig26-1s" in probe:
            per_sig = (probe["sig26-1s"]["sec"] - probe["baseS-1s"]["sec"]) / 26
            per_var = max(0.0, (probe["exit72-1s"]["sec"] - probe["baseS-1s"]["sec"]) / 72)
        else:
            per_sig, per_var = 1.8, 0.083
        win_s = 7 * len(bt) + per_sig * len(sigs) + per_var * len(todo)
        tot_h += win_s * 3 / 3600
        say("== %s: осей %d, пар %d (пропущено %d), позиций сеток %d, разных клеток %d, недопустимых позиций %d; в БД есть %d, считаем %d; проходов сигналов %d; вызовов движка %d; оценка счёта ≈ %.1f ч (3 окна)" % (
            side, len(ax.axes(side)), len(grids), len(skipped), sum(len(g["cells"]) for g in grids.values()), len(cells), len(bad), len(found), len(todo), len(sigs), len(bt), win_s * 3 / 3600))
        for k, (n_, f_) in sorted(cls.items()):
            say("   класс %-8s: клеток %d, есть %d, считаем %d" % (k, n_, f_, n_ - f_))
        by_grid = {}
        for k, c in cells.items():
            for (aa, bb, _pos) in c["pairs"]:
                by_grid.setdefault("%s×%s" % (aa, bb), set()).add(k)
        rows = {g: (len(ks), sum(1 for k in ks if k in found)) for g, ks in by_grid.items()}
        res = dict(side=side, n_cells=len(cells), n_found=len(found), n_todo=len(todo), n_sig=len(sigs), est_hours=win_s * 3 / 3600, skipped=skipped,
                   bad=[(x[0], x[1], x[2], w_) for x, w_ in bad.items()], by_grid=rows,
                   cells={k: dict(cid=c["cid"], patch=c["patch"], filt=c["filt"], sig=c["sig"], found=(k in found)) for k, c in cells.items()},
                   grids={"%s|%s" % ab: dict(vals=g["vals"], m=[[None] * len(g["vals"][1]) for _ in g["vals"][0]]) for ab, g in grids.items()})
        for k, c in cells.items():
            for (aa, bb, pos) in c["pairs"]:
                res["grids"]["%s|%s" % (aa, bb)]["m"][pos[0]][pos[1]] = k
        json.dump(res, open(t.q("res", "plan674%s-%s.json" % (SUF, side)), "w", encoding="utf-8"), ensure_ascii=False)
        tot_new += len(todo)
    say("plan готово: считаем %d клеток, ≈ %.1f ч движка (оба направления, 3 окна)" % (tot_new, tot_h))


def cmd_validate(a):
    """Сухой разбор строк всех клеток к счёту движком на одной монете (без P&L): движок отвергает недопустимые сочетания ключей до счёта; отвергнутые — в `res/invalid674-<сторона>.json`."""
    import subprocess
    import tk625_run as r5
    st, r5, _, _ = t._srv()
    _bin()
    u = r5.Uni(UNI)
    w = st.TRADE_WINDOWS[0]
    coin = open(u.coins_file, encoding="utf-8").read().strip().split(",")[0]
    store = chron_store(ro=True)
    import tk604_run as r4
    for side in SIDES:
        cells, _, _, _, found = plan_side(side, store)
        todo = [c for k, c in cells.items() if k not in found]
        cols = json.load(open(os.path.join(ROOT, "res", "ftabcols674%s-%s.json" % (FSUF, side)), encoding="utf-8"))
        lines = {}
        for c in todo:
            line = t.variant_line(r5, u, side, c["patch"])
            if c["filt"]:
                line += " fa=%s fo=lo fv=%g" % (cols[c["ckey"].split("F=", 1)[1]], ax.FV_PASS)
            lines[c["cid"]] = line
        inv = {}
        d = u.wdir(w.name)
        out = os.path.join(ROOT, "validate", "%s-%s" % (w.name, side))
        os.makedirs(out, exist_ok=True)
        for it in range(400):
            vf = out + ".variants.txt"
            with open(vf, "w", encoding="utf-8") as f:
                f.write("".join("%s %s\n" % (k, v) for k, v in lines.items() if k not in inv))
            cmd = [r4.BIN, "breakout", "tick", "--cache", d + "/cache", "--trades", d + "/aggtrades", "--variants", vf, "--symbols", coin, "--out", out, "--threads", "2",
                   "--ftab", t.q("ftab674" + FSUF, "%s-%s.csv" % (w.name, side))] + r5.TICK_STORE
            env = dict(os.environ, P42_T0=w.t0, **one_side_env(side))
            r = subprocess.run(cmd, env=env, capture_output=True, text=True)
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
            raise SystemExit("validate %s: > 400 отвергнутых" % side)
        json.dump(inv, open(invalid_path(side), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say("validate %s: вариантов %d, отвергнуто движком %d%s" % (side, len(lines), len(inv), "".join("\n   %s: %s" % (k, v) for k, v in list(inv.items())[:10])))
    say("validate готово")


def cmd_cells(a):
    st, r5, _, _ = t._srv()
    sha = _bin()
    say("G-bin: %s sha256 %s" % (BIN, sha))
    g = json.load(open(GBASE, encoding="utf-8"))
    if g["bad"] or g["bin_sha256"] != sha:
        raise SystemExit("G-base не пройден или бинарь подменён")
    u = r5.Uni(UNI)
    store = chron_store(ro=True)
    for side in SIDES:
        cells, grids, skipped, bad, found = plan_side(side, store)
        todo = [c for k, c in cells.items() if k not in found]
        bl = batches(todo)
        index = {}
        cols = json.load(open(os.path.join(ROOT, "res", "ftabcols674%s-%s.json" % (FSUF, side)), encoding="utf-8")) if any(k == "f" for k, _ in bl) else {}
        for kind, cur in bl:
            nm = batch_name(kind, cur)
            index[nm] = [c["ckey"] for c in cur]
            for w in st.TRADE_WINDOWS:
                out = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side), nm)
                done = os.path.join(ROOT, "done", "%s-%s-%s" % (w.name, side, nm))
                if os.path.exists(done):
                    continue
                ftab = t.q("ftab674" + FSUF, "%s-%s.csv" % (w.name, side)) if kind == "f" else None
                variants = []
                for c in cur:
                    line = t.variant_line(r5, u, side, c["patch"])
                    if c["filt"]:
                        line += " fa=%s fo=lo fv=%g" % (cols[c["ckey"].split("F=", 1)[1]], ax.FV_PASS)
                    variants.append((c["cid"], line))
                os.makedirs(out, exist_ok=True)
                r5.tick(u, w, variants, out, one_side_env(side), t.q("log", "cells-%s-%s-%s.log" % (w.name, side, nm)), ftab=ftab)
                os.makedirs(os.path.dirname(done), exist_ok=True)
                open(done, "w").write("%d\n" % len(cur))
                say("cells %s-%s %s [%d вариантов, проходов %d] готово" % (w.name, side, nm, len(cur), len({c["sig"] for c in cur})))
        json.dump(index, open(t.q("res", "batches674%s-%s.json" % (SUF, side)), "w", encoding="utf-8"))
    say("cells готово")


# ---- таблицы → БД ------------------------------------------------------------------------------------------------------------------
_W = {}


def _winit():
    import tk604_stats as st
    import tk625_run as r5
    import tk638_post as p8
    _W["st"], _W["r5"], _W["p8"] = st, r5, p8
    _W["u"] = r5.Uni(UNI)


def _row_job(job):
    import tk674_stats as ms
    st, p8, u = _W["st"], _W["p8"], _W["u"]
    side, ckey, cid, bname, line, patch, filt, eff = job
    dirs = {w.name: (os.path.join(ROOT, "cells", "%s-%s" % (w.name, side), bname, cid), side) for w in st.TRADE_WINDOWS}
    tables, daily = p8.read_tables(dirs)
    for w in st.TRADE_WINDOWS:
        tb, dl = tables[w.name], daily[w.name]
        tot_d, tot_n = float(sum(dl.values())), float(tb.net.sum())
        if abs(tot_d - tot_n) > 1e-4 + 2e-6 * len(tb.net):
            return dict(error="G7 %s %s: Σ daily %+.6f ≠ Σ net %+.6f" % (w.name, cid, tot_d, tot_n), ckey=ckey)
    ser = st.splice(tables, u.coins(), daily=None)
    kinds, pnl = p8.kinds_pnl(tables)
    if len(pnl) != len(ser.val) or not np.allclose(pnl, ser.val, atol=1e-6):
        return dict(error="P&L сделок ≠ val ряда %s" % cid, ckey=ckey)
    tmax = 0
    for w in st.TRADE_WINDOWS:
        tb = tables[w.name]
        if len(tb.exit_ms):
            tmax = max(tmax, int(tb.exit_ms.max()) + w.t0_ms)
    m = ms.metrics(ser, kinds, pnl, s_pct=float(patch.get("s", 5)))
    errs = []
    if tmax >= st.LAST_MS:
        errs.append("G-tape: выход позже 13.11")
    kn = m["kinds"]
    if not eff["rf"] and kn.get("rt", 0):
        errs.append("G-off: rf=0, но выходов rt %d" % kn["rt"])
    if not eff["tt"] and kn.get("tx", 0):
        errs.append("G-off: tt=0, но выходов tx %d" % kn["tx"])
    if not eff["tu"] and kn.get("tu", 0):
        errs.append("G-off: tu=0, но выходов tu %d" % kn["tu"])
    if filt:
        import tk604_run as r4
        for w in st.TRADE_WINDOWS:
            meta = json.load(open(os.path.join(dirs[w.name][0], "meta.json"), encoding="utf-8"))[SIDENAME[side]]
            fc = r4.filter_counters(meta)
            if fc is None or fc["no_key"]:
                errs.append("G-key: no_key %s %s" % (w.name, fc))
    sig = hashlib.md5((",".join("%s:%d" % k for k in zip(ser.sym_name, [int(x) for x in ser.key_ms])) + "|" + ",".join("%.5f" % x for x in pnl)).encode()).hexdigest()
    m.update(cid=cid, line=line, patch=patch, filt=[list(x) for x in filt], dirs={w: os.path.join(ROOT, "cells", "%s-%s" % (w, side), bname, cid) for w in ("wa", "wb", "wc")},
             sigkey=[list(x) for x in ax.sigkey(patch)], trades_sig=sig, tmax_ms=tmax, v=1, stake=500)
    return dict(ckey=ckey, m=m, errs=errs)


def eff_flags(side, patch):
    b = {"L": dict(rf=0.2, tt=0, tu=0), "S": dict(rf=0.2, tt=20, tu=0)}[side]
    return dict((k, float(patch.get(k, b[k])) > 0) for k in b)


def _cc_job(job):
    """c-проверка Lmax по клетке: сделки с выходом `stop` и убытком ≥ 90 % от 500 $ × s (полный размер; в файле сделок размера на выходе нет — `fees` вдвое меньше у половинной позиции, вход в две части e/e2).
    Рядом: число stop-выходов с убытком в зоне 60–90 % (разделение двух мод), худший убыток полного стопа, превышение над Lmax(s) = 500 $ × s / 100 + 0,415 $."""
    side, cid, bname, s_pct = job
    st, p8 = _W["st"], _W["p8"]
    dirs = {w.name: (os.path.join(ROOT, "cells", "%s-%s" % (w.name, side), bname, cid), side) for w in st.TRADE_WINDOWS}
    tables, _ = p8.read_tables(dirs)
    kinds, pnl = p8.kinds_pnl(tables)
    kinds, pnl = np.array(kinds), np.asarray(pnl, dtype=float)
    base = 500.0 * s_pct / 100.0
    lm = base + 0.415
    hard = kinds == "stop"
    loss = -pnl[hard]
    full = loss >= 0.9 * base
    mid = (loss >= 0.6 * base) & (loss < 0.9 * base)
    lf = loss[full]
    return dict(cid=cid, s=s_pct, n_stop=int(hard.sum()), n_full=int(full.sum()), n_mid=int(mid.sum()), c_full=float((lf - base).mean()) if full.any() else None,
                c_all=float((loss - base).mean()) if len(loss) else None, worst=float(lf.max()) if full.any() else None, n_above=int((lf > lm + 1e-6).sum()), ex_max=float((lf - lm).max()) if full.any() else None)


def cmd_ccheck(a):
    """c-проверка Lmax на полных стопах (без записи в БД, без метрик P&L); выход: res/ccheck674.json."""
    st, r5, _, _ = t._srv()
    store = chron_store(ro=True)
    out = {}
    for side in SIDES:
        cells, grids, skipped, bad_pos, found = plan_side(side, store)
        index = json.load(open(os.path.join(ROOT, "res", "batches674%s-%s.json" % (SUF, side)), encoding="utf-8"))
        where = {ck: nm for nm, cks in index.items() for ck in cks}
        jobs = [(side, c["cid"], where[k], float(c["patch"].get("s", 5))) for k, c in cells.items() if k in where]
        with mp.Pool(3, initializer=_winit) as pool:
            res = list(pool.imap_unordered(_cc_job, jobs, chunksize=8))
        out[side] = res
        cf = [r["c_full"] for r in res if r["c_full"] is not None and r["n_full"] >= 20]
        ca = [r["c_all"] for r in res if r["c_all"] is not None and r["n_stop"] >= 20]
        wf = [r for r in res if r["worst"] is not None]
        w5 = [r["worst"] for r in wf if r["s"] == 5]
        n_full, n_above, n_mid = sum(r["n_full"] for r in res), sum(r["n_above"] for r in res), sum(r["n_mid"] for r in res)
        say("== %s: клеток %d; с ≥ 20 полными стопами %d: средний c = %.3f $ (мин %.3f, макс %.3f) | все stop-выходы (как в tables): %.3f $ (%d клеток)" % (
            side, len(res), len(cf), float(np.mean(cf)) if cf else float("nan"), min(cf) if cf else float("nan"), max(cf) if cf else float("nan"),
            float(np.mean(ca)) if ca else float("nan"), len(ca)))
        say("   полных стопов %d, из них убыток > Lmax(s) %d (%.1f %%), стоп-выходов в зоне 60–90 %% от полного %d (%.2f %% от stop-выходов); максимум превышения над Lmax(s) %.2f $; худший убыток полного стопа: при s=5 %.2f $ (Lmax(5 %%) = 25.42 $), по всем клеткам %.2f $" % (
            n_full, n_above, 100.0 * n_above / max(n_full, 1), n_mid, 100.0 * n_mid / max(1, sum(r["n_stop"] for r in res)), max([r["ex_max"] for r in wf] or [float("nan")]),
            max(w5 or [float("nan")]), max([r["worst"] for r in wf] or [float("nan")])))
    json.dump(out, open(t.q("res", "ccheck674.json"), "w", encoding="utf-8"))
    say("ccheck готово")


def cmd_tables(a):
    if not a.entry:
        raise SystemExit("--entry L-N")
    st, r5, _, _ = t._srv()
    mdd = _bin()
    g = json.load(open(GBASE, encoding="utf-8"))
    if g["bad"] or g["bin_sha256"] != mdd:
        raise SystemExit("G-base не пройден или бинарь подменён после G-base")
    store = chron_store()
    binm = bin_md5()
    u = r5.Uni(UNI)
    bad, cmean = [], []
    for side in SIDES:
        cells, grids, skipped, bad_pos, found = plan_side(side, store)
        index = json.load(open(os.path.join(ROOT, "res", "batches674%s-%s.json" % (SUF, side)), encoding="utf-8"))
        where = {ck: nm for nm, cks in index.items() for ck in cks}
        todo = [c for k, c in cells.items() if k not in found and k in where]
        say("== %s: в БД есть %d, записываем %d" % (side, len(found), len(todo)))
        jobs = [(side, c["ckey"], c["cid"], where[c["ckey"]], t.variant_line(r5, u, side, c["patch"]), c["patch"], c["filt"], eff_flags(side, c["patch"])) for c in todo]
        n_new = 0
        with mp.Pool(3, initializer=_winit) as pool:
            rows = []
            for res in pool.imap_unordered(_row_job, jobs, chunksize=8):
                if "error" in res:
                    bad.append(res["error"])
                    continue
                m = res["m"]
                bad += ["%s %s: %s" % (side, m["cid"], e) for e in res["errs"]]
                if m["lmax"]["c_stop"] is not None and m["lmax"]["n_stop"] >= 20:
                    cmean.append(m["lmax"]["c_stop"])
                rows.append(dict(side=SIDENAME[side], uni=UNI, win=WIN, ckey=res["ckey"], bin=binm, cell=m["cid"], trades=m["n"], usd=m["usd"], mean_usd=m["mean_usd"], sharpe=m["sharpe"],
                                 extra=m, src="tools/p42/tk674_run.py", src_ptr="srv:%s/cells/*-%s/%s" % (ROOT, side, m["cid"])))
                if len(rows) >= 200:
                    n_new += store.cells_put(a.entry, rows, author=AUTHOR)["new"]
                    rows = []
            if rows:
                n_new += store.cells_put(a.entry, rows, author=AUTHOR)["new"]
        say("   %s: строк записано %d; нарушений охран %d" % (side, n_new, len(bad)))
    if cmean:
        mc = float(np.mean(cmean))
        say("c-проверка Lmax: средний (убыток − 500 $ × s) по стоп-выходам %.3f $ (ожидание %.3f $), клеток %d" % (mc, 0.415, len(cmean)))
        say("   (справка: по уточнению L-291 c-проверка идёт на полных стопах — стадия ccheck; здесь все stop-выходы, включая половинные)")
    if bad:
        for b in bad[:30]:
            say("СТОП: " + b)
        sys.exit(1)
    say("tables готово")


# ---- карта пар из БД ---------------------------------------------------------------------------------------------------------------
def guards(row, base):
    """Охраны (а)–(в), (д) клетки по строке БД и базе стороны; (г) — в карте по соседям сетки пары."""
    ex = row["extra"]
    b = base["extra"]
    k_base = sum(1 for m_ in b["months"] if m_[1] > 0)
    g = {}
    g["a"] = ex["n"] >= MIN_N and all(m_[0] >= mn for m_, mn in zip(ex["months"], MIN_MONTH))
    g["b"] = sum(1 for m_ in ex["months"] if m_[1] > 0) >= min(4, k_base)
    g["c"] = ex["maxdd"] <= 2 * b["maxdd"]
    g["d"] = ex["lmax"]["usd"] <= b["lmax"]["usd"] + 1e-9
    if is_tick_split(row):                                   # §13.73.4: у клеток с частью B «за тик до уровня» охрана (д) — по реальному среднему убытку stop-выхода
        lm = ex["lmax"]                                      # обе части условия: убыток по цене стопа случая «только часть A» и средний реальный убыток stop-выхода ≤ базы
        cap = b["lmax"]["usd"] + 1e-9
        g["d"] = split_losses(ex)["a_only"] <= cap and (lm["c_stop"] is None or lm["c_stop"] + ms_stake() * lm["s"] / 100.0 <= cap)
    return g


def loss_fields(ex):
    """Стоп клетки и убытки: убыток по цене стопа (500 $ × s + издержки; не максимум — пробой стопа выше), худшая сделка, число сделок хуже цены стопа, $ на сделку."""
    lm = ex["lmax"]
    return dict(s=lm["s"], lstop=lm["usd"], worst=-lm["worst"], n_over=lm["n_over"], n_stop=lm["n_stop"], mean_usd=ex["mean_usd"], under_share=ex["kpi"]["under_share"])


def ref5_key(ckey):
    """Ключ той же клетки при стопе 5 % (значение строки базы движка): справочная колонка карты с базой стопа ≤ 4 %."""
    return " ".join(tok for tok in ckey.split(" ") if not tok.startswith("s="))


def rep_cell(c, bsh):
    if c is None:
        return None
    k, r, gg, nbm = c[:4]
    ex = r["extra"]
    return dict(ckey=k, cid=r["cell"], sharpe=r["sharpe"], usd=r["usd"], n=r["trades"], maxdd=ex["maxdd"], maxdd_pct=ex["maxdd_pct"], gap_longest=ex["kpi"]["gap_longest"],
                gap_mean=ex["kpi"]["gap_mean"], p90=ex["kpi"]["p90"], tail=ex["kpi"]["gap_tail"], rr=ex["rr"]["rr"], months=ex["months"], plateau=gg["g"], nb_mean=nbm, gain=r["sharpe"] - bsh,
                tsig=ex.get("trades_sig"), **loss_fields(ex))


def pick(lst):
    if not lst:
        return None
    top = max(c[1]["sharpe"] for c in lst)
    near = [c for c in lst if top - c[1]["sharpe"] <= 0.1]
    near.sort(key=lambda c: (c[1]["extra"]["kpi"]["gap_longest"], c[1]["extra"]["maxdd"]))
    return near[0]


def synergy(rows, m, ia0, jb0, i, j, B=20000, block=5, seed=674):
    """Синергия клетки пары (i, j) относительно базы пары (ia0, jb0): Σ(a,b) − Σ(a,0) − Σ(0,b) + Σ(0,0), $ с интервалом блочного бутстрепа по суткам (блок 5, 20 000, seed 674)
    и прибавка Шарпа над лучшей из двух одноосевых клеток; None, если нужных клеток нет."""
    if ia0 is None or jb0 is None:
        return None
    ks = [m[i][j], m[i][jb0], m[ia0][j], m[ia0][jb0]]
    if any(k is None for k in ks):
        return None
    r = [rows[k] for k in ks]
    d = np.array(r[0]["extra"]["daily"]) - np.array(r[1]["extra"]["daily"]) - np.array(r[2]["extra"]["daily"]) + np.array(r[3]["extra"]["daily"])
    n = len(d)
    nb = -(-n // block)
    rng = np.random.default_rng(seed)
    idx = (rng.integers(0, n, size=(B, nb))[:, :, None] + np.arange(block)) % n
    sums = d[idx.reshape(B, -1)[:, :n]].sum(axis=1)
    single = [x["sharpe"] for x in r[1:3] if x["sharpe"] == x["sharpe"]]
    return dict(usd=float(d.sum()), lo=float(np.quantile(sums, 0.025)), hi=float(np.quantile(sums, 0.975)), p_le0=float((sums <= 0).mean()),
                sharpe_gain=(r[0]["sharpe"] - max(single)) if single else None, a_only=float(r[1]["sharpe"]), b_only=float(r[2]["sharpe"]))


def is_tick_split(row):
    return float((row["extra"].get("patch") or {}).get("e2t", 0)) > 0


def ms_stake():
    return 500.0


def split_losses(ex):
    """Убыток по цене стопа клетки с разделённым входом: «только часть A» (доля 100 − w2) и «A + B» (вся ставка), $ с издержками полного стопа."""
    import tk674_stats as ms
    lm, w2 = ex["lmax"], float((ex.get("patch") or {}).get("w2", 50))
    full = ms_stake() * lm["s"] / 100.0
    return dict(a_only=full * (100.0 - w2) / 100.0 + ms.COST_USD, a_b=full + ms.COST_USD)


def split_report(side, row):
    """Строка отчёта клетки с частью B «за тик до уровня» (§13.73.4): убыток по цене стопа «только A» / «A + B», стопы каждого вида (число, средний $ сделки),
    доля сделок с исполненной частью B — из `<сторона>-lots.csv` клетки по трём окнам (строки в порядке сделок; lot2 = 1 — часть B исполнена)."""
    import csv
    ex = row["extra"]
    out = dict(split_losses(ex), n=0, n_b=0, stop_a=[0, 0.0], stop_ab=[0, 0.0])
    for d in (ex.get("dirs") or {}).values():
        f = os.path.join(d, "%s-lots.csv" % SIDENAME[side])
        if not os.path.exists(f):
            return dict(out, missing=True)
        for r in csv.DictReader(open(f, encoding="utf-8")):
            out["n"] += 1
            b = int(r["lot2"]) == 1
            out["n_b"] += b
            if r["kind1"] == "stop":
                acc = out["stop_ab"] if b else out["stop_a"]
                acc[0] += 1
                acc[1] -= float(r["net1"]) + float(r["net2"])
    for k in ("stop_a", "stop_ab"):
        n_, s_ = out[k]
        out[k] = dict(n=n_, mean=s_ / n_ if n_ else None)
    return out


def add_split_reports(side, reps, rows):
    for x in reps:
        if x and x["ckey"] in rows and is_tick_split(rows[x["ckey"]]):
            x["split"] = split_report(side, rows[x["ckey"]])


def is_ref5(row):
    """Поправка §13.73.2: при базе стопа ≤ 4 % клетка со стопом 5 % — только справочная колонка (не кандидат, не якорь, не сосед плато);
    §13.73.4: кроме клеток с частью B «за тик до уровня» (e2t > 0) — там стоп 5 % в переборе (В-171)."""
    return ax.REBASE and row["extra"]["lmax"]["s"] >= ax.LINE_STOP and not is_tick_split(row)


def base_idx(side, name, vals):
    b = ax.axis_map(side)[name]["base"]
    return vals.index(b) if b in vals else None


def cmd_map(a):
    store = chron_store(ro=True)
    bins = eq_bins()
    for side in SIDES:
        plan = json.load(open(os.path.join(ROOT, "res", "plan674%s-%s.json" % (SUF, side)), encoding="utf-8"))
        rows = store.cells_lookup(SIDENAME[side], UNI, WIN, list(plan["cells"]), bins=bins)
        base_ck = ax.base_ckey(side)
        brow = store.cells_lookup(SIDENAME[side], UNI, WIN, [base_ck], bins=bins).get(base_ck)
        if brow is None:
            raise SystemExit("нет базы стороны %s в БД (ключ %s)" % (side, base_ck))
        for r_ in list(rows.values()) + [brow]:
            if r_["sharpe"] is None:                         # Шарп не определён (нет сделок / нулевой разброс) → NaN: такие клетки не проходят охрану (а)
                r_["sharpe"] = float("nan")
        miss = [k for k in plan["cells"] if k not in rows]
        if miss:
            raise SystemExit("%s: в БД нет %d клеток плана (первая: %s)" % (side, len(miss), plan["cells"][miss[0]]["cid"]))
        bsh = brow["sharpe"]
        gd = {k: guards(r, brow) for k, r in rows.items()}
        ok4 = lambda k: (rows[k]["sharpe"] == rows[k]["sharpe"] and not is_ref5(rows[k]) and all(gd[k][x] for x in "abcd"))
        best1 = {}                                           # лучший охранённый (а)–(д) Шарп клетки с одной сдвинутой осью — планка «пара лучше своих осей» (отсечение троек, §13.73.3)
        for gname, g in plan["grids"].items():
            m = g["m"]
            an, bn = gname.split("|")
            ia0, jb0 = base_idx(side, an, g["vals"][0]), base_idx(side, bn, g["vals"][1])
            for name, line in ((an, [m[i][jb0] for i in range(len(m)) if i != ia0] if jb0 is not None else []),
                               (bn, [m[ia0][j] for j in range(len(m[0])) if j != jb0] if ia0 is not None else [])):
                for k in line:
                    if k is not None and ok4(k):
                        best1[name] = max(best1.get(name, float("-inf")), rows[k]["sharpe"])
        pair_rows = []
        for gname, g in plan["grids"].items():
            m = g["m"]
            ni, nj = len(m), len(m[0])
            an, bn = gname.split("|")
            ia0, jb0 = base_idx(side, an, g["vals"][0]), base_idx(side, bn, g["vals"][1])
            cand = []
            for i in range(ni):
                for j in range(nj):
                    k = m[i][j]
                    if k is None:
                        continue
                    r = rows[k]
                    if r["sharpe"] != r["sharpe"] or is_ref5(r):
                        continue
                    nb = [m[i2][j] for i2 in ax.nb_idx(an, g["vals"][0], i)] + [m[i][j2] for j2 in ax.nb_idx(bn, g["vals"][1], j)]
                    nb = [x for x in nb if x is not None]
                    nb = [x for x in nb if not is_ref5(rows[x])]
                    nbs = [rows[x]["sharpe"] for x in nb if rows[x]["sharpe"] == rows[x]["sharpe"]]
                    nbm = float(np.mean(nbs)) if nbs else None
                    plateau = bool(nbs) and nbm >= 0.8 * r["sharpe"] and nbm >= bsh
                    cand.append((k, r, dict(gd[k], g=plateau), nbm, i, j))
            ok3 = [c for c in cand if c[2]["a"] and c[2]["b"] and c[2]["c"] and c[2]["d"]]
            full = [c for c in ok3 if c[2]["g"]]
            win, both = pick(full), pick([c for c in full if c[4] != ia0 and c[5] != jb0])
            wr, br = rep_cell(win, bsh), rep_cell(both, bsh)
            if wr:
                wr["moved"] = int(win[4] != ia0) + int(win[5] != jb0)
                wr["syn"] = synergy(rows, m, ia0, jb0, win[4], win[5]) if wr["moved"] == 2 else None
            if br:
                br["moved"] = 2
                br["syn"] = synergy(rows, m, ia0, jb0, both[4], both[5])
            for rp in (wr, br):
                if rp and ax.REBASE:
                    rp["ref5_key"] = ref5_key(rp["ckey"])
            if br:
                br["gain_sub"] = br["sharpe"] - max(bsh, best1.get(an, float("-inf")), best1.get(bn, float("-inf")))
            pair_rows.append(dict(pair=gname, cells=len(cand), guarded3=len(ok3), guarded_all=len(full), winner=wr, both_moved=br, best_no_plateau=rep_cell(pick(ok3), bsh)))
        bx = brow["extra"]
        add_split_reports(side, [p[w] for p in pair_rows for w in ("winner", "both_moved")], rows)
        if ax.REBASE:                                        # справочная колонка: та же клетка при s = 5 % (из БД, прежняя карта)
            reps = [p[w] for p in pair_rows for w in ("winner", "both_moved") if p[w]]
            r5rows = store.cells_lookup(SIDENAME[side], UNI, WIN, sorted({x["ref5_key"] for x in reps}), bins=bins)
            for x in reps:
                q5 = r5rows.get(x["ref5_key"])
                x["ref5"] = dict(sharpe=q5["sharpe"], usd=q5["usd"], n=q5["trades"], maxdd=q5["extra"]["maxdd"], **loss_fields(q5["extra"])) if q5 else None
        # счётчик испытаний (§13.73.1 разд. 3): все клетки стороны в БД (карта + прежние), шум σ·√(2 ln m) — верхняя оценка для независимых клеток
        m_side = sum(r["n"] for r in store.cells_count() if r["side"] == SIDENAME[side] and r["uni"] == UNI and r["win"] == WIN)
        shs = np.array([r["sharpe"] for r in rows.values() if r["sharpe"] == r["sharpe"]])
        sig_sh = float(shs.std()) if len(shs) > 1 else float("nan")
        noise = sig_sh * float(np.sqrt(2 * np.log(max(m_side, 2))))
        # G-dup: клетки с побайтно одинаковыми сделками (trades_sig) — разные заплатки, один результат
        by_sig = {}
        for k, r in rows.items():
            by_sig.setdefault(r["extra"].get("trades_sig"), []).append(k)
        dup = [v for k_, v in by_sig.items() if k_ and len(v) > 1]
        out = dict(side=side, rebase=ax.REBASE, base=dict(ckey=base_ck, sharpe=bsh, usd=brow["usd"], n=brow["trades"], maxdd=bx["maxdd"], maxdd_pct=bx["maxdd_pct"], kpi=bx["kpi"], rr=bx["rr"]["rr"], **loss_fields(bx)), pairs=pair_rows, best1=best1, n_cells=len(rows),
                   m=m_side, sharpe_sd=sig_sh, noise_max=noise, g_dup=dict(groups=len(dup), cells=sum(len(v) for v in dup), examples=dup[:5]))
        json.dump(out, open(t.q("res", "map674%s-%s.json" % (SUF, side)), "w", encoding="utf-8"), ensure_ascii=False)
        top = sorted([p for p in pair_rows if p["winner"]], key=lambda p: -p["winner"]["gain"])[:15]
        say("== %s: база Шарп %.2f, Σ %+.0f $, просадка %.0f $ (%.0f %%), перехай %d сут.; пар %d, с победителем %d" % (
            side, bsh, brow["usd"], bx["maxdd"], bx["maxdd_pct"], bx["kpi"]["gap_longest"], len(pair_rows), sum(1 for p in pair_rows if p["winner"])))
        say("   m (все клетки стороны в БД) %d, σ Шарпа клеток %.2f, ожидаемый максимум шума ≈ %.2f (верхняя оценка: клетки зависимы); G-dup: %d групп / %d клеток с одинаковыми сделками" % (
            m_side, sig_sh, noise, len(dup), sum(len(v) for v in dup)))
        def line(p, w):
            sy = w.get("syn")
            return "   %-18s %-34s Шарп %5.2f (%+.2f)  Σ %+6.0f $  просадка %4.0f $ (%2.0f %%)  перехай %3d  сделок %4d  плато %s  осей сдвинуто %d  синергия %s" % (
                p["pair"].replace("|", "×"), w["cid"][:34], w["sharpe"], w["gain"], w["usd"], w["maxdd"], w["maxdd_pct"], w["gap_longest"], w["n"], "да" if w["plateau"] else "нет", w["moved"],
                ("%+.0f $ [%+.0f; %+.0f]" % (sy["usd"], sy["lo"], sy["hi"])) if sy else "—")
        say("   -- А. победитель пары (как в предрегистрации), top 15 по приросту Шарпа")
        for p in top:
            say(line(p, p["winner"]))
        topb = sorted([p for p in pair_rows if p["both_moved"]], key=lambda p: -p["both_moved"]["gain"])[:15]
        say("   -- Б. лучшая охранённая клетка пары с ОБЕИМИ осями сдвинутыми (справка, те же клетки), top 15 по приросту Шарпа")
        for p in topb:
            say(line(p, p["both_moved"]))
    say("map готово")



def cmd_spot(a):
    """Выборочная проверка нового бинаря (TK674_BIN) на ≤ 3 готовых клетках карты (окно wa, лонг): выход / сигналы / фильтр — файлы сделок байт в байт
    с клетками прежнего бинаря; при успехе md5 нового бинаря дописывается в res/eqbins674.json (его клетки и прежние засчитываются вместе)."""
    st, r5, _, _ = t._srv()
    _bin()
    u = r5.Uni(UNI)
    w, side = st.TRADE_WINDOWS[0], "L"
    index = json.load(open(os.path.join(ROOT, "res", "batches674b-%s.json" % side), encoding="utf-8"))
    cells = plan_side(side)[0]
    picks = {}
    for nm, cks in index.items():
        for ck in cks:
            c = cells.get(ck)
            if c is None:
                continue
            kind = "фильтр" if c["filt"] else ("сигналы" if [k for k, _ in c["sig"] if k != "e"] else "выход")
            picks.setdefault(kind, (nm, c))
    cols = json.load(open(os.path.join(ROOT, "res", "ftabcols674-%s.json" % side), encoding="utf-8"))
    bad = []
    for kind, (nm, c) in sorted(picks.items()):
        line = t.variant_line(r5, u, side, c["patch"])
        if c["filt"]:
            line += " fa=%s fo=lo fv=%g" % (cols[c["ckey"].split("F=", 1)[1]], ax.FV_PASS)
        out = os.path.join(ROOT, "spot677", kind)
        os.makedirs(out, exist_ok=True)
        r5.tick(u, w, [(c["cid"], line)], out, one_side_env(side), t.q("log", "spot677-%s.log" % kind), ftab=t.q("ftab674", "%s-%s.csv" % (w.name, side)) if c["filt"] else None)
        old = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side), nm, c["cid"])
        diff = [f for f in CMP_FILES if not filecmp.cmp(os.path.join(old, "%s-%s" % (SIDENAME[side], f)), os.path.join(out, c["cid"], "%s-%s" % (SIDENAME[side], f)), shallow=False)]
        say("spot %s %s: %s" % (kind, c["cid"], "байт в байт" if not diff else "РАЗЛИЧИЯ " + ", ".join(diff)))
        bad += diff
    if bad or len(picks) < 3:
        raise SystemExit("spot: не пройдено (классов %d)" % len(picks))
    eq = eq_bins()
    if bin_md5() not in eq:
        json.dump(eq + [bin_md5()], open(os.path.join(ROOT, "res", "eqbins674.json"), "w", encoding="utf-8"))
    say("spot готово: eqbins %s" % ", ".join(x[:8] for x in eq_bins()))


def cmd_stopaxis(a):
    """Ось стопа s при остальном у строки базы движка — таблица md из БД (без счёта): R:R и убытки первыми."""
    store = chron_store(ro=True)
    bins = eq_bins()
    c = lambda x, nd=2: ("%.*f" % (nd, x)).replace(".", ",")
    out = ["| сторона | стоп, % | R:R | убыток по цене стопа $ (% маржи) | худшая сделка $ (% маржи) | сделок хуже цены стопа | stop-выходов | Σ $ | $ на сделку | сделок | Шарп | просадка $ (% маржи) | перехай, сут.: самый долгий / средний / хвост |",
           "|" + "---|" * 13]
    for side in SIDES:
        ks = {v: ax.canon(ax.BASE_LINE(side) + (" s=%s" % v if v != ax.LINE_STOP else "")) for v in (1, 1.5, 2, 3, 4, 5)}
        rows = store.cells_lookup(SIDENAME[side], UNI, WIN, list(ks.values()), bins=bins)
        for v, k in ks.items():
            r = rows[k]
            e = r["extra"]
            f = loss_fields(e)
            mark = " (база В-171)" if v == ax.BASE_STOP[side] else (" (справка)" if v == ax.LINE_STOP else "")
            out.append("| %s | %s%s | %s | %s (%s %%) | %s (%s %%) | %d | %d | %+.0f | %s | %d | %s | %.0f (%.0f %%) | %d / %s / %d |" % (
                side, ("%g" % v).replace(".", ","), mark, c(e["rr"]["rr"]), c(f["lstop"]), c(f["lstop"] / 2.5, 1), c(f["worst"]), c(f["worst"] / 2.5, 1), f["n_over"], e["kinds"].get("stop", 0),
                r["usd"], c(f["mean_usd"]), r["trades"], c(r["sharpe"]), e["maxdd"], e["maxdd_pct"], e["kpi"]["gap_longest"], c(e["kpi"]["gap_mean"], 1), e["kpi"]["gap_tail"]))
    open(t.q("res", "stop-axis-table.md"), "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
    say("stopaxis готово: res/stop-axis-table.md")


COMMANDS = {"stopaxis": cmd_stopaxis, "spot": cmd_spot, "ftab": cmd_ftab, "probe": cmd_probe, "gates": cmd_gates, "counts": cmd_counts, "plan": cmd_plan, "validate": cmd_validate, "cells": cmd_cells, "tables": cmd_tables, "ccheck": cmd_ccheck, "map": cmd_map}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--entry", help="запись летописи L-N для строк клеток (стадия tables)")
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
