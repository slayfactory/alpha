#!/usr/bin/env python3
"""TK-718 (PS-1037 + поправка PS-1038): клетки T1 / T2, «есть / считаем» (В-213), набор для движка, сбор рядов.

  python3 tk718_cells.py gen                    # клетки T1 (члены базы) и T2 (соседи ±25 %): счёт и печать, без БД
  python3 tk718_cells.py plan                   # поиск каждой клетки × окна в пуле / летописи → `tk718_plan`, наборы `tk718core`, `tk718nbF`, `tk718nbS` в mass718.sqlite
  python3 tk718_cells.py collect                # ряды всех клеток (пул / летопись / движок) → `tk718_ser` в tk718.sqlite
  python3 tk718_cells.py gate --run-id R        # ворота: клетки L1 (146454) и L401 (758805) × m02…m06 против `mass_day` пула, ≤ 1e-7

Окна: FJ = m02…m06 (сутки 31–180), SEL = wa, wb, wc (сутки 181–316; `mass_day` с D0 = 01.07 → + 181), N1 = n1 (317–327, только шорт); ядро (члены T1, S1, S2, L1, L2, L401) считается и за m01 (январь)
с сохранением сделок (`tk700_mass.py run --keep-trades`). Сутки рядов — от 01.01.2026."""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import random
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")
import tk674_axes as ax  # noqa: E402

SEED = 718
ROOT = "/data/p42-tk718"
OUT_DB = ROOT + "/tk718.sqlite"
MASS718 = ROOT + "/mass718.sqlite"
MASS = "/data/p42-tk700/mass/mass.sqlite"
CHRON = "/data/chron/chron.sqlite"
EQBINS = "/data/p42-tk674c/res/eqbins674.json"
POOL = {"short": "/data/p42-tk717/pool-short", "long": "/data/p42-tk717/pool-long"}
REPLAY = "/data/p42-tk692/replay.sqlite"
SEL_WIN = "01.07–13.11"
BASE = {   # `tk604_run.BASE` (п. 4); равенство проверяет тест
    "L": "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20",
    "S": "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05",
}
LETTER = {"short": "S", "long": "L"}
MONTHS = ("m01", "m02", "m03", "m04", "m05", "m06")
FJ_WINS = MONTHS[1:]
SEL_WINS = ("wa", "wb", "wc")
SEL_DAY0, SEL_DAYS = 181, 136
N1_DAY0, N1_DAYS = 317, 11
BIN = {"long": "c400af112bda3396d807b2ba882aea8b", "short": "562f689967dde28ebce44f4a00f1b7af"}
CID_CORE0, CID_NB0 = 7_180_000, 7_181_000

# линии вердикта и ворота: (сторона, канонический ключ клетки, cid в пуле `all1` или None) — `docs/research/freeze/tk674-frozen.json`, PS-1037 / PS-1038 п. 3
LINES = {
    "S1": ("short", "bm=1 e=0.15 e2t=2 ew=5 fh=4 fm=50 lat=4 latx=4 n_bounce=1 natr_p=60 pb=1 pk=1 rf=0.2 rr=1 rs=2 s=4 t24=12 tb=0.1 tt=20", None),
    "S2": ("short", "bm=1 e=0.15 e2t=2 ew=5 fh=4 fm=50 lat=4 latx=4 n_bounce=1 natr_min=0.6 nf=1 pb=1 pk=1 q=100 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20", None),
    "L1": ("long", "ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12 F=big1800<=0.29&cons60<=1&vpin1h>=0.2", 146454),
    "L2": ("long", "busy=0 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12 tpk=3 F=big1800<=0.29&vpin1h>=0.2", 15450),
    "L401": ("short", "bm=1 e=0.15 e2t=2 ew=5 fh=4 fm=50 lat=4 latx=4 n_bounce=1 natr_min=0.6 nf=1 pb=1 pk=1 rf=0.2 rr=1 rs=2 s=4 t24=12 tb=0.1 tt=20", 758805),
}
ANCHOR = {   # якоря L-334 (PS-1007 §5)
    "long": "ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12",
    "short": "bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20",
}
GRID_L_DAYS, GRID_TF = (3, 7, 14), (30, 60, 240)
VERDICT_LINES = {"short": ("S1", "S2"), "long": ("L1", "L2")}
NOAXIS = {"lat", "latx", "pk", "pb", "busy", "nf", "bm", "natr_p"}  # PS-1038 п. 5: задержка, флаги, категории; PS-1039: natr_p — только 15 / 30 / 60 (движок), ±25 % недопустим
INT_KEYS = {"ew", "fh", "fm", "fk", "n_bounce", "natr_p", "rs", "t24", "tt", "e2t", "tf", "l_days", "q", "n_tp", "c", "p", "m", "k"}
NJOINT = 40


# ───────────────────────────── ключи, строки движка ─────────────────────────────

def split_ckey(ckey):
    """«k=v … F=a<=x&b>=y» → ({ключ: строка значения}, [(признак, знак, порог float)])."""
    line, _, sem = ckey.partition(" F=") if " F=" in ckey else (ckey, "", "")
    if ckey.startswith("F="):
        line, sem = "", ckey[2:]
    filt = []
    for tok in sem.split("&") if sem else []:
        for op in ("<=", ">=", "=="):
            if op in tok:
                f, x = tok.split(op)
                filt.append((f, op, float(x)))
                break
        else:
            raise ValueError("фильтр %r" % tok)
    return ax.parse_line(line), filt


def to_line(side, keys):
    """Полная строка варианта движка: база стороны (`BASE` + t24 = 12) + явные значения по умолчанию там, где ключа нет, а в базе он не по умолчанию + ключи клетки (позднее значение побеждает)."""
    base = BASE[LETTER[side]] + " t24=12"
    out = [base]
    for k, v in ax.parse_line(base).items():
        if k not in keys and k in ax.DEFAULTS and ax.num(v) != ax.num(ax.DEFAULTS[k]):
            out.append("%s=%s" % (k, ax.num(ax.DEFAULTS[k])))
    out += ["%s=%s" % (k, keys[k]) for k in keys]
    return " ".join(out)


def ckey_of(side, keys, filt):
    return ax.canon(to_line(side, keys), filt)


def valid(side, keys):
    """Допустимость ключей клетки (до движка): t_low < t_high, ступень тейка ≥ 5 $, q ≤ 100, положительные s / e / доли. Остальное отвергает движок (`validate`)."""
    f = lambda k, d: float(keys.get(k, d))   # noqa: E731
    if f("t_low", 1) >= f("t_high", 5):
        return False
    if f("q", 50) > 100 or f("s", 5) <= 0 or f("e", 0.3) <= 0 or f("rf", 0) < 0:
        return False
    patch = {k: keys[k] for k in ("n_tp", "q", "tpk", "w2") if k in keys}
    return ax.min_lot_usd(patch) >= ax.MIN_LOT_USD


# ───────────────────────────── T1: члены базы ─────────────────────────────

def t1_members(side):
    """[(имя, ckey)] — 9 членов (l_days × tf) якоря стороны."""
    keys0, _ = split_ckey(ANCHOR[side])
    out = []
    for ld in GRID_L_DAYS:
        for tf in GRID_TF:
            k = dict(keys0)
            k["l_days"], k["tf"] = str(ld), str(tf)
            out.append(("B%s-l%d-tf%d" % (LETTER[side], ld, tf), ckey_of(side, k, [])))
    return out


# ───────────────────────────── T2: соседи ±25 % ─────────────────────────────

def shift(v, sign, is_int):
    """Значение × 0,75 / × 1,25; целые — до ближайшего (0,5 вверх), равно исходному — ±1; ≤ 0 (целые < 1) → None."""
    v = float(v)
    x = v * (0.75 if sign < 0 else 1.25)
    if is_int:
        y = int(math.floor(x + 0.5))
        if y == int(round(v)):
            y += 1 if sign > 0 else -1
        return y if y >= 1 else None
    y = float("%.6g" % x)
    return y if y > 0 else None


def axes_of(keys, filt):
    """Оси клетки: числовые ключи (кроме NOAXIS) и пороги фильтра. Элемент — ('k', ключ) или ('f', индекс порога)."""
    out = [("k", k) for k in sorted(keys) if k not in NOAXIS]
    out += [("f", i) for i in range(len(filt))]
    return out


def apply_shifts(side, keys, filt, shifts):
    """shifts {ось: ±1} → (ключи, фильтр) | None, если сдвиг какой-то оси невозможен или клетка недопустима."""
    k2, f2 = dict(keys), list(filt)
    for (kind, ref), sg in shifts.items():
        if kind == "k":
            nv = shift(keys[ref], sg, ref in INT_KEYS)
            if nv is None:
                return None
            k2[ref] = ax.num(nv)
        else:
            f, op, thr = filt[ref]
            nv = shift(thr, sg, False)
            if nv is None:
                return None
            f2[ref] = (f, op, nv)
    if not valid(side, k2):
        return None
    return k2, f2


def neighbours(name, side, ckey):
    """Соседи линии: (а) по одной оси (±), (б) NJOINT совместных со случайным знаком (зерно 718, отбор с отказом: недопустимые не считаются) и 2 угла. Возвращает (список (метка, ckey), статистика)."""
    keys, filt = split_ckey(ckey)
    axs = axes_of(keys, filt)
    seen = {ckey}
    out, st = [], dict(axes=len(axs), single_drop=0, joint_draws=0, corner_drop=0)
    for a in axs:
        for sg in (-1, 1):
            r = apply_shifts(side, keys, filt, {a: sg})
            if r is None:
                st["single_drop"] += 1
                continue
            ck = ckey_of(side, *r)
            if ck not in seen:
                seen.add(ck)
                out.append(("a:%s%s%+d" % (a[0], a[1], sg), ck))
    rng = random.Random("%d-%s" % (SEED, name))
    n = 0
    while n < NJOINT and st["joint_draws"] < 20000:
        st["joint_draws"] += 1
        sh = {a: rng.choice((-1, 1)) for a in axs}
        r = apply_shifts(side, keys, filt, sh)
        if r is None:
            continue
        ck = ckey_of(side, *r)
        if ck in seen:
            continue
        seen.add(ck)
        out.append(("b:%d" % n, ck))
        n += 1
    for sg in (-1, 1):
        r = apply_shifts(side, keys, filt, {a: sg for a in axs})
        if r is None:
            st["corner_drop"] += 1
            continue
        ck = ckey_of(side, *r)
        if ck not in seen:
            seen.add(ck)
            out.append(("c:%+d" % sg, ck))
    st["joint"] = n
    return out, st


def build_cells():
    """Все клетки протокола: {(side, ckey): dict(role, name, line_of)}. Роли: line (S1 / S2 / L1 / L2 / L401), member (T1), nb (сосед линии)."""
    cells = {}
    for name, (side, ckey, pcid) in LINES.items():
        keys, filt = split_ckey(ckey)
        assert ckey_of(side, keys, filt) == ckey, (name, ckey_of(side, keys, filt))
        cells[(side, ckey)] = dict(role="line", name=name, line_of=name, pool_cid=pcid)
    for side in ("short", "long"):
        for nm, ck in t1_members(side):
            cells.setdefault((side, ck), dict(role="member", name=nm, line_of="", pool_cid=None))
    stats = {}
    for name, (side, ckey, _p) in LINES.items():
        if name == "L401":
            continue                                                  # ворота / справочно: соседи не строятся
        nb, st = neighbours(name, side, ckey)
        stats[name] = dict(st, n=len(nb))
        for lab, ck in nb:
            cells.setdefault((side, ck), dict(role="nb", name="%s/%s" % (name, lab), line_of=name, pool_cid=None))
    return cells, stats


# ───────────────────────────── поиск готового ─────────────────────────────

def pool_index(side):
    """{ckey: индекс строки} и массив P пула (клетка × месяц)."""
    with open(os.path.join(POOL[side], "meta.pkl"), "rb") as f:
        ckeys, _meta = pickle.load(f)
    return {k: i for i, k in enumerate(ckeys)}, np.load(os.path.join(POOL[side], "P.npy"))


def chron_sel(side, ckeys):
    """{ckey: (id, trades, usd, daily, dayn)} — клетки в летописи на SEL (бинарь реестра равенства, последняя строка по id, без `metric_fix`)."""
    eq = set(json.load(open(EQBINS, encoding="utf-8")))
    con = sqlite3.connect("file:%s?mode=ro" % CHRON, uri=True)
    got = {}
    ck = sorted(ckeys)
    for i in range(0, len(ck), 400):
        part = ck[i:i + 400]
        q = ("SELECT id, ckey, bin, trades, usd, json_extract(extra, '$.daily'), json_extract(extra, '$.dayn') FROM metric WHERE side=? AND uni='c539' AND win=? AND ckey IN (%s)" % ",".join("?" * len(part)))
        for id_, k, b, tr, usd, daily, dayn in con.execute(q, [side, SEL_WIN] + part):
            if b not in eq and b not in BIN.values():
                continue
            if con.execute("SELECT 1 FROM metric_fix WHERE metric=?", (id_,)).fetchone():
                continue
            if daily is None:
                continue
            if k not in got or id_ > got[k][0]:
                got[k] = (id_, int(tr), float(usd), json.loads(daily), json.loads(dayn) if dayn else None)
    con.close()
    return got


def plan_cells(cells):
    """Для каждой клетки: где лежит ряд FJ (пул / движок) и SEL (летопись / движок). Возвращает {ключ: dict(fj=…, sel=…, pool_i=…)} и числа."""
    res = {}
    for side in ("short", "long"):
        mine = [k for k in cells if k[0] == side]
        idx, P = pool_index(side)
        sel = chron_sel(side, [k[1] for k in mine])
        for key in mine:
            c = cells[key]
            i = idx.get(key[1])
            r = dict(pool_i=i, fj="engine", sel="engine", chron_id=None)
            if i is not None and P[i, 1:6].all() and c["role"] != "line" and c["role"] != "member":
                r["fj"] = "pool"
            if key[1] in sel and c["role"] != "line" and c["role"] != "member":
                r["sel"] = "chron"
                r["chron_id"] = sel[key[1]][0]
            if c["role"] in ("line", "member"):                    # ядро: сделки нужны, ряды СДЕЛОК берём только из движка; SEL — из летописи, если есть
                r["fj"] = "engine"
                if key[1] in sel:
                    r["sel"] = "chron"
                    r["chron_id"] = sel[key[1]][0]
            res[key] = r
    return res


# ───────────────────────────── БД ─────────────────────────────

CELL_SCHEMA = """
CREATE TABLE IF NOT EXISTS cellset(name TEXT PRIMARY KEY, created TEXT, params TEXT);
CREATE TABLE IF NOT EXISTS cell(cellset TEXT, cid INTEGER, side TEXT, ckey TEXT, line TEXT, sig TEXT, filt TEXT, feats TEXT, ref_n INTEGER, ref_usd REAL, ref_daily TEXT,
                                db_id INTEGER, bin TEXT, PRIMARY KEY(cellset, cid));
CREATE INDEX IF NOT EXISTS cell_side_sig ON cell(cellset, side, sig, cid);
"""
OUT_SCHEMA = """
CREATE TABLE IF NOT EXISTS tk718_plan(side TEXT, ckey TEXT, cid INTEGER, role TEXT, name TEXT, line_of TEXT, fj TEXT, sel TEXT, n1 TEXT, pool_i INTEGER, chron_id INTEGER, PRIMARY KEY(side, ckey));
CREATE TABLE IF NOT EXISTS tk718_ser(side TEXT, ckey TEXT, grp TEXT, have TEXT, src TEXT, pnl BLOB, n BLOB, PRIMARY KEY(side, ckey, grp));
"""


def sig_of(line):
    patch = {}
    for t in line.split():
        if "=" in t:
            k, v = t.split("=", 1)
            try:
                patch[k] = float(v)
            except ValueError:
                pass
    return json.dumps([list(x) for x in ax.sigkey(patch)], separators=(",", ":"))


def cell_row(cellset, cid, side, ckey):
    keys, filt = split_ckey(ckey)
    line = to_line(side, keys)
    assert ax.canon(line, filt) == ckey, (ckey, ax.canon(line, filt))
    return (cellset, cid, side, ckey, line, sig_of(line), json.dumps([list(x) for x in filt]), ",".join(sorted({f for f, _, _ in filt})), 0, 0.0, None, 0, BIN[side])


def cmd_gen(a):
    cells, stats = build_cells()
    by = {}
    for (side, _ck), c in cells.items():
        by[side, c["role"]] = by.get((side, c["role"]), 0) + 1
    print("клеток: %s" % dict(sorted(by.items())))
    for n, s in stats.items():
        print("  %-5s осей %2d, отброшено одиночных сдвигов %d, совместных 40 (попыток %d), углов отброшено %d, соседей %d" % (n, s["axes"], s["single_drop"], s["joint_draws"], s["corner_drop"], s["n"]))
    return 0


def cmd_plan(a):
    cells, stats = build_cells()
    res = plan_cells(cells)
    os.makedirs(ROOT, exist_ok=True)
    out = sqlite3.connect(OUT_DB, timeout=120)
    out.executescript(OUT_SCHEMA)
    mass = sqlite3.connect(MASS718, timeout=120)
    mass.execute("PRAGMA journal_mode=WAL")
    mass.executescript(CELL_SCHEMA)
    for nm in ("tk718core", "tk718nbF", "tk718nbS", "tk718gate"):
        mass.execute("DELETE FROM cell WHERE cellset=?", (nm,))
        mass.execute("DELETE FROM cellset WHERE name=?", (nm,))
    core, nbf, nbs = [], [], []
    ncore = nnb = 0
    plan_rows = []
    for key in sorted(cells, key=lambda k: (cells[k]["role"] != "line", cells[k]["role"] != "member", k)):
        side, ckey = key
        c, r = cells[key], res[key]
        if c["role"] in ("line", "member"):
            cid = c["pool_cid"] or CID_CORE0 + len(core) + 1
            core.append(cell_row("tk718core", cid, side, ckey))
            n1 = "engine" if side == "short" else "-"
        else:
            cid = CID_NB0 + nnb
            nnb += 1
            n1 = "-"
            if r["fj"] == "engine":
                nbf.append(cell_row("tk718nbF", cid, side, ckey))
            if r["sel"] == "engine":
                nbs.append(cell_row("tk718nbS", cid, side, ckey))
        plan_rows.append((side, ckey, cid, c["role"], c["name"], c["line_of"], r["fj"], r["sel"], n1, r["pool_i"], r["chron_id"]))
    gate = [r[:0] + ("tk718gate",) + r[1:] for r in core if r[1] in (146454, 758805)]
    for nm, rows in (("tk718core", core), ("tk718nbF", nbf), ("tk718nbS", nbs), ("tk718gate", gate)):
        mass.execute("INSERT INTO cellset VALUES(?,?,?)", (nm, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(dict(seed=SEED, ps="PS-1037/PS-1038"))))
        mass.executemany("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    mass.commit()
    out.execute("DELETE FROM tk718_plan")
    out.executemany("INSERT INTO tk718_plan VALUES(?,?,?,?,?,?,?,?,?,?,?)", plan_rows)
    out.commit()
    print("ВЫПИСКА «есть / считаем» (PS-1038 п. 3):")
    for role in ("line", "member", "nb"):
        for side in ("short", "long"):
            rows = [p for p in plan_rows if p[3] == role and p[0] == side]
            if not rows:
                continue
            print("  %-6s %-5s клеток %4d | FJ: пул %4d, движок %4d | SEL: летопись %4d, движок %4d" % (
                role, side, len(rows), sum(1 for p in rows if p[6] == "pool"), sum(1 for p in rows if p[6] == "engine"), sum(1 for p in rows if p[7] == "chron"), sum(1 for p in rows if p[7] == "engine")))
    print("наборы для движка: tk718core %d, tk718nbF %d (FJ m02…m06), tk718nbS %d (SEL wa, wb, wc)" % (len(core), len(nbf), len(nbs)))
    nsig = len({r[5] for r in nbf}), len({r[5] for r in nbs})
    print("уникальных проходов сигналов: nbF %d, nbS %d" % nsig)
    est = (len(nbf) * 0.059 * 5 + nsig[0] * 4.0 * 5 + len(nbs) * 0.059 * 3 + nsig[1] * 4.0 * 3 + len(core) * 0.059 * 10 + len(core) * 4.0 * 10)
    print("оценка ЦП движка (модель mass_batch): %.0f ЦП-с ≈ %.1f ЦП-ч" % (est, est / 3600))
    return 0


# ───────────────────────────── сбор рядов ─────────────────────────────

def day_arrays(rows, d0, nd):
    pnl, n = np.zeros(nd), np.zeros(nd, np.int32)
    for d, p, k in rows:
        j = d - d0
        if 0 <= j < nd:
            pnl[j] += p
            n[j] += k
    return pnl, n


def put(out, side, ckey, grp, have, src, pnl, n):
    out.execute("INSERT OR REPLACE INTO tk718_ser VALUES(?,?,?,?,?,?,?)", (side, ckey, grp, json.dumps(have), src, np.asarray(pnl, np.float64).tobytes(), np.asarray(n, np.int32).tobytes()))


def cmd_collect(a):
    out = sqlite3.connect(OUT_DB, timeout=120)
    out.executescript(OUT_SCHEMA)
    plan = out.execute("SELECT side, ckey, cid, role, fj, sel, pool_i FROM tk718_plan").fetchall()
    mass = sqlite3.connect("file:%s?mode=ro" % MASS718, uri=True)
    ro = sqlite3.connect("file:%s?mode=ro" % MASS, uri=True)
    # (а) пул, FJ
    for side in ("short", "long"):
        X, N, P = (np.load(os.path.join(POOL[side], f)) for f in ("X.npy", "N.npy", "P.npy"))
        for s_, ckey, cid, role, fj, sel, pi in plan:
            if s_ == side and fj == "pool":
                put(out, side, ckey, "H1", list(FJ_WINS), "pool", X[pi, :181], N[pi, :181])
        del X, N, P
    # (б) летопись, SEL
    for side in ("short", "long"):
        ck = [p[1] for p in plan if p[0] == side and p[5] == "chron"]
        got = chron_sel(side, ck)
        for ckey in ck:
            _id, _tr, _usd, daily, dayn = got[ckey]
            put(out, side, ckey, "SEL", list(SEL_WINS), "chron:%d" % _id, daily, dayn if dayn else np.zeros(len(daily), np.int32))
    # (в) движок: mass_day по запускам
    runs = [r[0] for r in mass.execute("SELECT run_id FROM mass_run WHERE (run_id LIKE 'tk718c-%' OR run_id LIKE 'tk718f-%' OR run_id LIKE 'tk718s-%') AND state='done'")]   # ворота (`tk718g`) — не ряды
    by_cid = {(p[2]): p for p in plan}
    for run in runs:
        wins = mass.execute("SELECT wins FROM mass_run WHERE run_id=?", (run,)).fetchone()[0].split(",")
        dry = mass.execute("SELECT dry FROM mass_run WHERE run_id=?", (run,)).fetchone()[0]
        grp, d0, nd = ("SEL", SEL_DAY0, SEL_DAYS) if dry else (("N1", N1_DAY0, N1_DAYS) if wins == ["n1"] else ("H1", 0, 181))
        cws = {}
        for cid, win, nn, usd, errs in mass.execute("SELECT cid, win, n, usd, errs FROM mass_cw WHERE run_id=?", (run,)):
            if not errs:
                cws.setdefault(cid, set()).add(win)
        days = {}
        for cid, day, pnl, n in mass.execute("SELECT cid, day, pnl, n FROM mass_day WHERE run_id=?", (run,)):
            days.setdefault(cid, []).append(((day + SEL_DAY0) if dry else day, pnl, n))
        for cid, have in cws.items():
            p = by_cid.get(cid)
            if p is None:
                continue
            pnl, n = day_arrays(days.get(cid, []), d0, nd)
            put(out, p[0], p[1], grp, sorted(have), "engine:" + run, pnl, n)
    out.commit()
    for grp in ("H1", "SEL", "N1"):
        print(grp, out.execute("SELECT src like 'pool%' , src like 'chron%', count(*) FROM tk718_ser WHERE grp=? GROUP BY 1,2", (grp,)).fetchall())
    ro.close()
    return 0


def cmd_gate(a):
    """Ворота (PS-1038 п. 4): cid 146454 (класс B) / 758805 (A) × m02…m06 из запусков `--run-id` (через запятую) против `mass_day` пула (run-id `h1-<месяц>-A[-short]`, L1 — `h1-<месяц>-B`): ряд ≤ 1e-7 по каждым суткам, n равно."""
    mass = sqlite3.connect("file:%s?mode=ro" % MASS718, uri=True)
    ro = sqlite3.connect("file:%s?mode=ro" % MASS, uri=True)
    runs = a.run_id.split(",")
    bad = 0
    for cid, side, cls in ((146454, "long", "B"), (758805, "short", "A")):
        for mi, w in enumerate(FJ_WINS, start=1):
            ref = "h1-%s-%s%s" % (("jan", "feb", "mar", "apr", "may", "jun")[mi], cls, "-short" if side == "short" else "")
            q = ",".join("?" * len(runs))
            got = {d: (p, n) for d, p, n in mass.execute("SELECT day, pnl, n FROM mass_day WHERE run_id IN (%s) AND cid=? AND day>=? AND day<?" % q, runs + [cid, ax_month(mi)[0], ax_month(mi)[1]])}
            want = {d: (p, n) for d, p, n in ro.execute("SELECT day, pnl, n FROM mass_day WHERE run_id=? AND cid=?", (ref, cid))}
            cw_g = mass.execute("SELECT n, usd FROM mass_cw WHERE run_id IN (%s) AND cid=? AND win=?" % q, runs + [cid, w]).fetchone()
            cw_w = ro.execute("SELECT n, usd FROM mass_cw WHERE run_id=? AND cid=? AND win=?", (ref, cid, w)).fetchone()
            ok = cw_g is not None and cw_w is not None and cw_g[0] == cw_w[0] and abs(cw_g[1] - cw_w[1]) <= 1e-7
            for d in set(got) | set(want):
                g, wv = got.get(d, (0.0, 0)), want.get(d, (0.0, 0))
                if abs(g[0] - wv[0]) > 1e-7 or g[1] != wv[1]:
                    ok = False
            print("ворота %s cid %d %s: n %s / %s, Σ %s / %s → %s" % (side, cid, w, cw_g and cw_g[0], cw_w and cw_w[0], cw_g and round(cw_g[1], 6), cw_w and round(cw_w[1], 6), "РАВНО" if ok else "РАЗНО"))
            bad += 0 if ok else 1
    print("ВОРОТА: %s" % ("пройдены" if bad == 0 else "ПРОВАЛЕНЫ (%d)" % bad))
    return 0 if bad == 0 else 3


MONTH_START = (0, 31, 59, 90, 120, 151, 181)


def ax_month(mi):
    return MONTH_START[mi], MONTH_START[mi + 1]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("gen", "plan", "collect", "gate"):
        s = sp.add_parser(n)
        if n == "gate":
            s.add_argument("--run-id", required=True)
    a = ap.parse_args(argv)
    return {"gen": cmd_gen, "plan": cmd_plan, "collect": cmd_collect, "gate": cmd_gate}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
