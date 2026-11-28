#!/usr/bin/env python3
"""П-42 §13.73 (поправка 22.11, TK-674): оси линии, все совместные пары, клетки и канонический ключ клетки (ключ строки в БД летописи).
Без движка и без P&L. Клетка = база стороны + заплатка ключей движка (+ составной фильтр по таблице `--ftab`).
  канонический ключ = все ключи строки (база + u.extra + заплатка, позднее значение побеждает) без значений по умолчанию движка, отсортированные,
  плюс «F=…» — смысл фильтра (признак, знак, абсолютный порог), а не имя столбца таблицы; rf=0 → rs не значим, tt=0 → tb не значим.
Режим TK674_X680=1 (поправка §13.73.7, PS-960): к осям карты добавляются активные оси единичек TK-680 (`tk674_x680.py`), пары — только новая × любая; файлы с суффиксом «x»."""
from __future__ import annotations

import itertools
import os
import re

SIDES = tuple(os.environ.get("TK674_SIDES", "LS"))    # одна сторона на процесс — TK674_SIDES=L | S (стадия cells уровней идёт двумя процессами)
FV_PASS = 0.5

# значения по умолчанию движка (`Params::base()`, src/breakout/params.rs) — ключ с таким значением в канонический ключ не входит
DEFAULTS = {"tf": 60, "k": 2, "d": 0.5, "n_bounce": 2, "tau": 0.2, "l_days": 7, "pk": 0, "lx": 0, "lw": 0, "natr_p": 30, "natr_min": 0.4, "t1h": 0.5,
            "t24": 0, "track": 2, "e": 0.3, "color": 1, "f": 0.1, "r": 15, "ban": 1, "ap": 0, "pb": 0, "fh": 0, "fk": 120, "fm": 0, "fb": 0.5, "fg": 0,
            "natr_exit_p": 30, "n_tp": 8, "t_low": 1, "t_high": 5, "q": 50, "be": 1, "a": 2, "rr": 2, "s": 5, "slip": 1, "best": 0, "lat": 0, "latx": 0,
            "tpg": 1, "tpk": 0, "ns": 0, "ew": 0, "ex": 0, "ls": 0, "tt": 0, "tb": 0, "rf": 0, "rs": 0, "bm": 0, "e2": 0, "sn": 0, "sg": 0, "sb": 0,
            "sr": 0, "sd": 0, "tj": 0, "tl": 0, "nx": 0, "sl": 0, "cw": 0, "cl": 0, "ch": 0, "cm": 0, "tu": 0, "tpp": 0, "trp": 0, "c": 5, "m": 3,
            "mm": 60, "s_glob": 4, "w": 10, "ww": 60, "p": 3, "reentry": 0, "busy": 1, "mk": 0, "ek": 0,
            "w2": 50, "e2t": 0, "s2": 0,               # разделённый вход TK-677 (бинарь 93cb51a9): доля части B, триггер B за N тиков до уровня, общая цена стопа
            "nf": 0, "ev": 0, "ea": 0, "el": 0.05, "eh": 1, "sm": 0,     # TK-681 (бинарь 4e1fb286): источник фильтра живости, зона входа от волатильности (источник, коэффициент, зажим), мин. расстояние стопа от уровня
            "vf": 0, "vs": 0, "va": 0, "vb": 0, "vp": 0, "v0": 0, "vm": 0.5, "vz": 0, "vr": 0,
            "e1t": 0,                                   # TK-688 (единый бинарь): вся ставка одной лимиткой за N тиков до уровня (В-176, В-178); 0 — выключен
            "tn": 0, "tno": 0,                          # TK-697 № 9 (ключ выхода): доля позиции, %, у первого тейка «следующий уровень − отступ»; tno — отступ, NATR (значим при tn > 0); 0 — выключен
            "lm": 0, "lz": 0, "pp": 0, "li": 0, "fw": 0,         # TK-747 (В-194..196): уровень без усреднения, закол двух пивотов, свежесть по владельцу (ожидание после постановки, мин; разрыв старых уровней, ч); 0 — выключены
            "lk": 0, "ld": 0, "lsl": 0}                 # TK-697 № 1 (источник уровня lx = 4, наклонка): точек линии, давность старшей точки в сутках, предел наклона NATR60 в час (0 — без предела); значимы только при lx = 4
FILT_KEYS = ("fa", "fo", "fv")
# ключи, меняющие проход сигналов (SigCfg); остальные — только выход / портфель, клетка идёт на готовом проходе
SIG_KEYS = ("k", "tf", "n_bounce", "d", "l_days", "lx", "lk", "ld", "lsl", "natr_min", "t1h", "t24", "e",
            "tau", "pk", "natr_p", "track", "color", "f", "r", "ban", "ap", "pb", "fh", "fk", "fm", "fb", "fg",
            "nf", "ev", "ea", "el", "eh", "lm", "lz", "pp", "li", "fw")      # TK-747: lm / lz / pp / fw — уровни и допуск кандидата (SigCfg); TK-680: остальные поля SigCfg (оси единичек §13.73.7); nf / ev / ea / el / eh — TK-681 (§13.73.14); sm — выход, на готовом проходе
LEVEL_KEYS = ("k", "tf", "n_bounce", "d", "l_days", "lx", "lk", "ld", "lsl", "lm", "lz", "pp", "li")
POOL_KEYS = ("natr_min", "t1h", "t24")
# поправка §13.73.2 (В-171): база стопа ≤ 4 % — лонг 3 %, шорт 4 % (якоря фиксированного стопа §13.74, L-293); s = 5 % — справочная колонка.
# TK674_REBASE=1 включает новую базу; без переменной — прежняя карта (s = 5 %, L-292) воспроизводится как была.
REBASE = os.environ.get("TK674_REBASE") == "1"
BASE_STOP = {"L": 3, "S": 4}
# поправка §13.73.7 (PS-960, TK-674 × TK-680): TK674_X680=1 добавляет активные оси единичек TK-680 (`tk674_x680.py`) и строит только пары, где есть новая ось;
# база — стоп ≤ 4 % (как у единичек), файлы плана / карты — с суффиксом «bx» (`tk674_run.FSUF`), прежние файлы не затрагиваются
X680 = os.environ.get("TK674_X680") == "1"
if X680 and not REBASE:
    raise SystemExit("TK674_X680=1 требует TK674_REBASE=1: единички TK-680 считались на базе стопа Л 3 % / Ш 4 %")
LINE_STOP = 5                                  # стоп в строке базы движка
# поправка §13.73.4 (В-171, TK-677): составная ось `split` — вход двумя частями; TK674_SPLIT=1 добавляет ось (файлы с суффиксом «s»)
SPLIT = os.environ.get("TK674_SPLIT") == "1"
SUF2 = ("b" if REBASE else "") + ("s" if SPLIT else "")
SPLIT_KEYS = ("e2", "e2t", "w2", "s2")
SPLIT_LINE = {"L": dict(e2=0, e2t=0, w2=50, s2=0), "S": dict(e2=0.05, e2t=0, w2=50, s2=0)}      # значения ключей в строке базы стороны
SPLIT_VALS = {                                  # метка → полный набор ключей; порядок — порядок соседей плато
    "off": dict(e2=0, e2t=0, w2=50, s2=0),
    "e2w25": dict(e2=0.05, e2t=0, w2=25, s2=0), "e2w50": dict(e2=0.05, e2t=0, w2=50, s2=0), "e2w75": dict(e2=0.05, e2t=0, w2=75, s2=0),
    "t1w25": dict(e2=0, e2t=1, w2=25, s2=0), "t1w50": dict(e2=0, e2t=1, w2=50, s2=0), "t1w75": dict(e2=0, e2t=1, w2=75, s2=0),
    "t2w50": dict(e2=0, e2t=2, w2=50, s2=0), "t3w50": dict(e2=0, e2t=3, w2=50, s2=0),
    "t1w50s2": dict(e2=0, e2t=1, w2=50, s2=1),
}
SPLIT_BASE = {"L": "off", "S": "e2w50"}
SPLIT_NB = {                                    # соседи плато по оси split (§13.73.4): шаг w2 внутри семейства, шаг e2t при w2 = 50, s2 у t1w50, off ↔ середины семейств
    "off": ("e2w50", "t1w50"), "e2w25": ("e2w50",), "e2w50": ("e2w25", "e2w75", "off"), "e2w75": ("e2w50",),
    "t1w25": ("t1w50",), "t1w50": ("t1w25", "t1w75", "t2w50", "t1w50s2", "off"), "t1w75": ("t1w50",),
    "t2w50": ("t1w50", "t3w50"), "t3w50": ("t2w50",), "t1w50s2": ("t1w50",),
}

if os.environ.get("TK674_SPLITW") == "1":       # поправка §13.73.10 (В-173 № 34 «добавляй 80 и 90 процентов»): доля части B 80 и 90 % в обоих семействах
    for fam, kw in (("e2", dict(e2=0.05, e2t=0)), ("t1", dict(e2=0, e2t=1))):
        for w in (80, 90):
            SPLIT_VALS["%sw%d" % (fam, w)] = dict(kw, w2=w, s2=0)
        SPLIT_NB[fam + "w75"] = SPLIT_NB[fam + "w75"] + (fam + "w80",)
        SPLIT_NB[fam + "w80"] = (fam + "w75", fam + "w90")
        SPLIT_NB[fam + "w90"] = (fam + "w80",)


if os.environ.get("TK674_SPLIT67") == "1" or os.environ.get("TK674_SPLIT178") == "1":
    # поправка §13.73.19 (PS-977, В-177): доля части B 67 % (первая заявка — треть объёма) в обоих семействах; поправка §13.73.21 (В-178): доля части B 70 % и 80 % (часть A 30 % / 20 %) при e2t = 1, 2, 3
    # (TK674_SPLIT178=1: t1w70, t2w70, t3w70, t2w80, t3w80; t1w80 — из TK674_SPLITW). Соседи плато: по w2 внутри семейства (e2t) и по e2t при том же w2; остальное — как в §13.73.4.
    if os.environ.get("TK674_SPLIT67") == "1":
        for fam, kw in (("e2", dict(e2=0.05, e2t=0)), ("t1", dict(e2=0, e2t=1))):
            SPLIT_VALS["%sw67" % fam] = dict(kw, w2=67, s2=0)
    if os.environ.get("TK674_SPLIT178") == "1":
        for e_, w_ in ((1, 70), (2, 70), (3, 70), (2, 80), (3, 80)):
            SPLIT_VALS["t%dw%d" % (e_, w_)] = dict(e2=0, e2t=e_, w2=w_, s2=0)
    _extra = {k: SPLIT_NB[k] for k in ("off", "t1w50s2")}
    _e2 = sorted((v for v in SPLIT_VALS if v.startswith("e2w")), key=lambda v: int(v[3:]))
    _t = {}
    for v in SPLIT_VALS:
        if len(v) > 3 and v[0] == "t" and v[1].isdigit() and v[2] == "w" and v[3:].isdigit():
            _t.setdefault(int(v[1]), []).append(int(v[3:]))
    for e_ in _t:
        _t[e_].sort()
    for i, v in enumerate(_e2):
        SPLIT_NB[v] = tuple(_e2[j] for j in (i - 1, i + 1) if 0 <= j < len(_e2)) + (("off",) if v == "e2w50" else ())
    for e_, ws in _t.items():
        for i, w_ in enumerate(ws):
            nb = [(e_, ws[j]) for j in (i - 1, i + 1) if 0 <= j < len(ws)] + [(e2_, w_) for e2_ in (e_ - 1, e_ + 1) if e2_ in _t and w_ in _t[e2_]]
            lab = ["t%dw%d" % x for x in nb]
            if e_ == 1 and w_ == 50:
                lab += ["t1w50s2", "off"]
            SPLIT_NB["t%dw%d" % (e_, w_)] = tuple(lab)
    _ord = ["off"] + _e2 + ["t%dw%d" % (e_, w_) for e_ in sorted(_t) for w_ in _t[e_]] + ["t1w50s2"]
    SPLIT_VALS = {k: SPLIT_VALS[k] for k in _ord}
    SPLIT_NB.update(_extra)
    SPLIT_NB["t1w50s2"] = ("t1w50",)


def nb_idx(name, vals, i):
    """Индексы соседей значения i оси на шаг (плато (г)): у обычной оси — i ± 1, у составной оси split — по SPLIT_NB."""
    if name == "split":
        return [vals.index(x) for x in SPLIT_NB[vals[i]] if x in vals]
    if X680:
        r = _x6().nb_idx(name, vals, i)          # ось ключа единичек TK-680: соседи — внутри своей формы (как в карте единичек)
        if r is not None:
            return r
    return [j for j in (i - 1, i + 1) if 0 <= j < len(vals)]
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
    if "tn" not in out:
        out.pop("tno", None)
    if float(out.get("lx", 0)) != 4:            # TK-697 № 1: ключи линии значимы только при lx = 4
        for k in ("lk", "ld", "lsl"):
            out.pop(k, None)
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
        _ax("s", "exit", [1, 1.5, 2, 3, 4, 5], BASE_STOP[side] if REBASE else LINE_STOP),                      # стоп, % цены; не выше базовых 5 % (слова владельца 22.11)
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


def _old_axes(side):
    out = _common(side)
    if SPLIT:
        out.append(dict(name="split", cls="entry", key="split", vals=list(SPLIT_VALS), base=SPLIT_BASE[side]))
    for name, feat, op, labels in FILTERS[side]:
        out.append(dict(name=name, cls="filter", key=feat, op=op, vals=list(labels) + ["off"], base="off"))
    return out


def _x6():
    import tk674_x680 as x6          # ленивый импорт: модуль сам импортирует эти оси
    return x6


def axes(side):
    """Оси карты TK-674; при TK674_X680=1 — оси, расширенные победителем набора «сетки шире», и следом активные оси единичек TK-680 (поле `x680`), по одной на имя."""
    out = _old_axes(side)
    if X680:
        out = _x6().merge_axes(side, out)
    return out


def axis_map(side):
    return {a["name"]: a for a in axes(side)}


def compat(side, a, b):
    """(совместимы, причина) для пары осей; пары, в которых часть значений нельзя, отфильтровываются по клеткам (`cell_ok`)."""
    ca, cb = a["cls"], b["cls"]
    if X680 and (a.get("x680") or b.get("x680")):
        r = _x6().compat(side, a, b)             # запреты пар с новой осью (§13.73.7); None — дальше прежние правила
        if r is not None:
            return r
    if {a["name"], b["name"]} == {"ek", "mk"}:
        return False, "ek несовместим с mk (params.rs)"
    if {a["name"], b["name"]} == {"split", "e2"}:
        return False, "ось split сама задаёт e2"
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
    w2 = float(patch.get("w2", 50))
    return min(lots) / 1e6 * (min(w2, 100 - w2) / 100 if w2 != 50 else 1.0)    # меньшая часть входа при w2 ≠ 50 (TK-677); при 50 — как было, проверяет движок (validate)


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
        if name == "s":
            if v != LINE_STOP:
                patch["s"] = v
            continue
        if name == "split":
            for k in SPLIT_KEYS:
                if SPLIT_VALS[v][k] != SPLIT_LINE[side][k]:
                    patch[k] = SPLIT_VALS[v][k]
            continue
        if a.get("patches") is not None:                # ось ключа единичек TK-680 (X680): заплатка метки, у составной оси — несколько ключей
            patch.update(a["patches"][v])
            continue
        if a["base"] is None or v != a["base"]:
            patch[a["key"]] = v
    if REBASE and "s" not in assign:
        patch["s"] = BASE_STOP[side]
    if "t_low" in patch or "t_high" in patch:
        if float(patch.get("t_low", 1)) >= float(patch.get("t_high", 5)):
            return patch, filt, False, "t_low ≥ t_high"
    if float(patch.get("lx", 0)) not in (0, 4) and float(patch.get("tf", 60)) != 60:
        return patch, filt, False, "lx = 1, 2, 3 требует tf = 60 (params.rs validate)"
    if min_lot_usd(patch) < MIN_LOT_USD:
        return patch, filt, False, "наименьшая ступень тейка < 5 $"
    if filt:
        for k in POOL_KEYS:
            if k in patch and float(patch[k]) < DEFAULTS_BASE(side)[k]:
                return patch, filt, False, "ослабленный пул (%s) не покрыт строками таблицы признаков" % k
    if X680 and any(am[n].get("x680") for n in assign):
        why = _x6().cell_check(side, am, assign)         # две оси требуют разные значения одного ключа движка
        if why:
            return patch, filt, False, why
    return patch, filt, True, ""


def base_ckey(side):
    """Канонический ключ базы стороны (при базе стопа ≤ 4 % — строка базы + s)."""
    patch = cell_assign(side, {})[0]
    return canon(BASE_LINE(side) + (" " + patch_str(patch) if patch else ""))


def DEFAULTS_BASE(side):
    return {"natr_min": 0.4, "t1h": 0.5, "t24": 12}


def patch_str(p):
    return " ".join("%s=%s" % (k, num(v)) for k, v in p.items())


def sigkey(patch):
    return tuple(sorted((k, num(patch[k])) for k in SIG_KEYS if k in patch))


def pair_grid(side, a, b):
    """Сетка пары осей: {vals: (значения a, значения b), cells: {(i, j): (patch, filt, ok, reason)}}."""
    cells = {}
    for (i, va), (j, vb) in itertools.product(enumerate(a["vals"]), enumerate(b["vals"])):
        cells[(i, j)] = cell_assign(side, {a["name"]: va, b["name"]: vb})
    return dict(vals=(list(a["vals"]), list(b["vals"])), cells=cells)


def pair_grids(side, only=None):
    """{(A, B): {vals, cells: {(i, j): (patch, filt, ok, reason)}}} по всем совместимым парам осей; плюс список пропущенных пар с причиной.
    TK674_X680=1: только пары, где есть новая ось (старые × старые — карта TK-674, не пересчитываются); only — множество пар (A, B), сетки строятся лишь для них."""
    ax = axes(side)
    grids, skipped = {}, []
    for a, b in itertools.combinations(ax, 2):
        if X680 and not (a.get("x680") or b.get("x680")):
            continue
        ok, why = compat(side, a, b)
        if not ok:
            skipped.append((a["name"], b["name"], why))
            continue
        if only is not None and (a["name"], b["name"]) not in only:
            continue
        grids[(a["name"], b["name"])] = pair_grid(side, a, b)
    return grids, skipped


def cell_ckey(side, patch, filt, thr):
    """Канонический ключ клетки: база стороны + заплатка + смысл составного фильтра (thr — {ось: {метка: (признак, знак, порог)}})."""
    ft = [(thr[n][lab][0], thr[n][lab][1], thr[n][lab][2]) for n, lab in filt] if filt else []
    return canon(BASE_LINE(side) + (" " + patch_str(patch) if patch else ""), ft)


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
