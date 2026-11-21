#!/usr/bin/env python3
"""П-42 §13.73 (поправка 22.11, TK-674): оси линии, все совместные пары, клетки и канонический ключ клетки (ключ строки в БД летописи).
Без движка и без P&L. Клетка = база стороны + заплатка ключей движка (+ составной фильтр по таблице `--ftab`).
  канонический ключ = все ключи строки (база + u.extra + заплатка, позднее значение побеждает) без значений по умолчанию движка, отсортированные,
  плюс «F=…» — смысл фильтра (признак, знак, абсолютный порог), а не имя столбца таблицы; rf=0 → rs не значим, tt=0 → tb не значим."""
from __future__ import annotations

import itertools
import re

SIDES = ("L", "S")
FV_PASS = 0.5

# значения по умолчанию движка (`Params::base()`, src/breakout/params.rs) — ключ с таким значением в канонический ключ не входит
DEFAULTS = {"tf": 60, "k": 2, "d": 0.5, "n_bounce": 2, "tau": 0.2, "l_days": 7, "pk": 0, "lx": 0, "lw": 0, "natr_p": 30, "natr_min": 0.4, "t1h": 0.5,
            "t24": 0, "track": 2, "e": 0.3, "color": 1, "f": 0.1, "r": 15, "ban": 1, "ap": 0, "pb": 0, "fh": 0, "fk": 120, "fm": 0, "fb": 0.5, "fg": 0,
            "natr_exit_p": 30, "n_tp": 8, "t_low": 1, "t_high": 5, "q": 50, "be": 1, "a": 2, "rr": 2, "s": 5, "slip": 1, "best": 0, "lat": 0, "latx": 0,
            "tpg": 1, "tpk": 0, "ns": 0, "ew": 0, "ex": 0, "ls": 0, "tt": 0, "tb": 0, "rf": 0, "rs": 0, "bm": 0, "e2": 0, "sn": 0, "sg": 0, "sb": 0,
            "sr": 0, "sd": 0, "tj": 0, "tl": 0, "nx": 0, "sl": 0, "cw": 0, "cl": 0, "ch": 0, "cm": 0, "tu": 0, "tpp": 0, "trp": 0, "c": 5, "m": 3,
            "mm": 60, "s_glob": 4, "w": 10, "ww": 60, "p": 3, "reentry": 0, "busy": 1, "mk": 0, "ek": 0}
FILT_KEYS = ("fa", "fo", "fv")
# ключи, меняющие проход сигналов (SigCfg); остальные — только выход / портфель, клетка идёт на готовом проходе
SIG_KEYS = ("k", "tf", "n_bounce", "d", "l_days", "lx", "natr_min", "t1h", "t24", "e")
LEVEL_KEYS = ("k", "tf", "n_bounce", "d", "l_days", "lx")
POOL_KEYS = ("natr_min", "t1h", "t24")
NOTIONAL_U, MIN_LOT_USD = 500_000_000, 5.0     # `trade.rs` NOTIONAL_U; минимальная стоимость ордера Bybit (§13.50 п. 7 (з))


def BASE_LINE(side):
    """Строка базы стороны для h775: `tk604_run.BASE` + `u.extra` (` t24=12`)."""
    import tk604_run as r4
    return r4.BASE[side] + " t24=12"


def num(x):
    return "%g" % float(x)


def parse_line(line):
    d = {}
    for t in line.split():
        if "=" in t:
            k, v = t.split("=", 1)
            d[k] = v
    return d


def canon(line, filt=None):
    """Канонический ключ клетки: строка ключей движка (`k=v …`) → отсортированный набор без значений по умолчанию; filt — [(признак, знак, порог)] или []."""
    d = parse_line(line)
    for k in FILT_KEYS:
        d.pop(k, None)
    out = {}
    for k, v in d.items():
        try:
            nv = num(v)
        except ValueError:
            nv = v
        if k in DEFAULTS and nv == num(DEFAULTS[k]):
            continue
        out[k] = nv
    if "rf" not in out:
        out.pop("rs", None)
    if "tt" not in out:
        out.pop("tb", None)
    s = " ".join("%s=%s" % (k, out[k]) for k in sorted(out))
    if filt:
        s += (" " if s else "") + "F=" + "&".join(sorted("%s%s%s" % (f, o, num(x)) for f, o, x in filt))
    return s


# ---- оси --------------------------------------------------------------------------------------------------------------------------
# axis = dict(name, cls, key, vals, base) ; base = значение базы стороны или None (ключа в базе нет и «база» не значение оси)
# cls: exit | entry | level | pool | filter
def _ax(name, cls, vals, base, key=None):
    return dict(name=name, cls=cls, key=key or name, vals=list(vals), base=base)


def _common(side):
    S = side == "S"
    return [
        _ax("s", "exit", [1, 1.5, 2, 3, 4, 5], 5),                      # стоп, % цены; не выше базовых 5 % (слова владельца 22.11)
        _ax("rf", "exit", [0, 0.1, 0.2, 0.3], 0.2),
        _ax("rs", "exit", [0, 2, 5, 20], 2 if S else 20),
        _ax("tt", "exit", [0, 5, 10, 20, 40, 60], 20 if S else 0),
        _ax("tb", "exit", [0.1, 0.2, 0.3], 0.1 if S else None),
        _ax("bm", "exit", [0, 1, 2, 3], 1 if S else 0),
        _ax("rr", "exit", [1, 1.5, 2, 3], 2),
        _ax("tl", "exit", [0, 1, 2], 0),
        _ax("tj", "exit", [0, 0.25, 0.5], 0),
        _ax("t_high", "exit", [3, 4, 5, 8, 12], 5),
        _ax("t_low", "exit", [0.5, 1, 1.5, 2], 1),
        _ax("q", "exit", [25, 50, 75, 100], 50),
        _ax("n_tp", "exit", [4, 6, 8, 12], 8),
        _ax("tpg", "exit", [0.25, 0.5, 1, 2], 1),
        _ax("tpk", "exit", [-0.75, 0, 3, 10], 0),                        # κ = 30, 100 не исполнимы: наименьшая ступень < 5 $ (§13.50 п. 7 (з))
        _ax("tu", "exit", [0, 60, 120, 240], 0),
        _ax("e", "entry", [0.05, 0.1, 0.15, 0.3, 0.5] if S else [0.1, 0.15, 0.3, 0.5], 0.15 if S else 0.3),
        _ax("e2", "entry", [0, 0.025, 0.05, 0.1] if S else [0, 0.05, 0.1], 0.05 if S else 0),
        _ax("ek", "entry", [0, 3, 5, 8, 15], 0),
        _ax("mk", "entry", [0, 1, 2], 0),
        _ax("k", "level", [1, 2, 3, 4], 2),
        _ax("tf", "level", [30, 60, 240], 60),
        _ax("n_bounce", "level", [1, 2, 3, 4], 2),
        _ax("d", "level", [0.25, 0.5, 1, 2], 0.5),
        _ax("l_days", "level", [3, 5, 7, 10, 14], 7),
        _ax("lx", "level", [0, 1, 3], 0),                                   # lx = 2 (недели) требует `lw` по окнам (wa 5 …): один ключ клетки на три окна не выражается — снято
        _ax("natr_min", "pool", [0.2, 0.4, 0.6, 0.8], 0.4),
        _ax("t1h", "pool", [0.25, 0.5, 1, 2], 0.5),
        _ax("t24", "pool", [6, 12, 25, 50], 12),
    ]


# признаковые оси: значения — метки квантилей (порог — абсолютное число из res/thr674.json) или «off»; SHARP — on / off
FILTERS = {
    "S": [("TPM60", "tpm60", "<=", ("q50", "q60", "q70", "q80")), ("CN60", "cons60", "<=", ("q50", "q60", "q70", "q80")),
          ("ZV6", "zv6h", "<=", ("q10", "q20", "q30", "q40")), ("N24", "n24", ">=", ("q20", "q40", "q60", "q80")), ("SHARP", "sharp", "==", ("on",))],
    "L": [("TPM60", "tpm60", "<=", ("q60", "q70", "q80", "q90")), ("CN60", "cons60", "<=", ("q50", "q60", "q70", "q80")),
          ("ZV6", "zv6h", "<=", ("q10", "q20", "q30", "q40")), ("N24", "n24", ">=", ("85000", "140000", "220000", "450000")), ("SHARP", "sharp", "==", ("on",))],
}


def axes(side):
    out = _common(side)
    for name, feat, op, labels in FILTERS[side]:
        out.append(dict(name=name, cls="filter", key=feat, op=op, vals=list(labels) + ["off"], base="off"))
    return out


def axis_map(side):
    return {a["name"]: a for a in axes(side)}


def compat(side, a, b):
    """(совместимы, причина) для пары осей; пары, в которых часть значений нельзя, отфильтровываются по клеткам (`cell_ok`)."""
    ca, cb = a["cls"], b["cls"]
    if {a["name"], b["name"]} == {"ek", "mk"}:
        return False, "ek несовместим с mk (params.rs)"
    if "filter" in (ca, cb):
        o = b if ca == "filter" else a
        if o["cls"] == "level":
            return False, "таблица признаков построена на кандидатах базового уровня: у новых кандидатов строк нет (§13.73 п. 6.2)"
        if o["name"] == "e":
            return False, "e меняет момент сигнала: ключи таблицы признаков (sig_ms) не совпадут"
    return True, ""


def values(side, ax):
    return ax["vals"]


def min_lot_usd(patch):
    """Наименьшая ступень тейка, $ — копия `tick.rs tp_lots_n` (tk650_run.tp_lots) с n_tp / q клетки."""
    n = int(patch.get("n_tp", 8))
    qp = float(patch.get("q", 50))
    km = round(float(patch.get("tpk", 0)) * 1000)
    total = int(NOTIONAL_U * qp) // 100
    if km == 0:
        lots = [total // n] * n
    else:
        w = [1000 * (n - 1) + km * i for i in range(n)]
        sw = sum(w)
        lots = [total * x // sw for x in w]
    return min(lots) / 1e6


def cell_assign(side, assign):
    """assign {ось: значение} → (patch ключей движка относительно базы, [(признак-ось, метка)], допустима ли клетка, причина)."""
    am = axis_map(side)
    patch, filt = {}, []
    for name, v in assign.items():
        a = am[name]
        if a["cls"] == "filter":
            if v != "off":
                filt.append((name, v))
            continue
        if a["base"] is None or v != a["base"]:
            patch[a["key"]] = v
    if "t_low" in patch or "t_high" in patch:
        if float(patch.get("t_low", 1)) >= float(patch.get("t_high", 5)):
            return patch, filt, False, "t_low ≥ t_high"
    if min_lot_usd(patch) < MIN_LOT_USD:
        return patch, filt, False, "наименьшая ступень тейка < 5 $"
    if filt:
        for k in POOL_KEYS:
            if k in patch and float(patch[k]) < DEFAULTS_BASE(side)[k]:
                return patch, filt, False, "ослабленный пул (%s) не покрыт строками таблицы признаков" % k
    return patch, filt, True, ""


def DEFAULTS_BASE(side):
    return {"natr_min": 0.4, "t1h": 0.5, "t24": 12}


def patch_str(p):
    return " ".join("%s=%s" % (k, num(v)) for k, v in p.items())


def sigkey(patch):
    return tuple(sorted((k, num(patch[k])) for k in SIG_KEYS if k in patch))


def pair_grids(side):
    """{(A, B): {vals, cells: {(i, j): (patch, filt, ok, reason)}}} по всем совместимым парам осей; плюс список пропущенных пар с причиной."""
    ax = axes(side)
    grids, skipped = {}, []
    for a, b in itertools.combinations(ax, 2):
        ok, why = compat(side, a, b)
        if not ok:
            skipped.append((a["name"], b["name"], why))
            continue
        cells = {}
        for (i, va), (j, vb) in itertools.product(enumerate(a["vals"]), enumerate(b["vals"])):
            cells[(i, j)] = cell_assign(side, {a["name"]: va, b["name"]: vb})
        grids[(a["name"], b["name"])] = dict(vals=(list(a["vals"]), list(b["vals"])), cells=cells)
    return grids, skipped


def cell_id(patch, filt):
    parts = ["%s%s" % (k, num(v)) for k, v in sorted(patch.items())] + ["%s_%s" % (n, v) for n, v in sorted(filt)]
    return "+".join(parts) or "base"


if __name__ == "__main__":
    import sys
    for side in SIDES:
        grids, skipped = pair_grids(side)
        keys, bad = {}, 0
        for g in grids.values():
            for patch, filt, ok, why in g["cells"].values():
                if not ok:
                    bad += 1
                    continue
                keys[(tuple(sorted((k, num(v)) for k, v in patch.items())), tuple(sorted(filt)))] = (patch, filt)
        sig = {sigkey(p) for p, f in keys.values()}
        print(side, "осей", len(axes(side)), "пар", len(grids), "пропущено", len(skipped), "позиций сеток", sum(len(g["cells"]) for g in grids.values()),
              "разных клеток (по заплатке)", len(keys), "недопустимых позиций", bad, "проходов сигналов", len(sig), file=sys.stdout)
