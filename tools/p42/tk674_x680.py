#!/usr/bin/env python3
"""П-42 §13.73.7 (PS-960, TK-674 × TK-680): режим `TK674_X680=1` — пары «активная ось единичек × ось карты TK-674» и «× другая активная ось единичек». Без движка и без P&L.
Читает карты единичек `<корень>/res/map674b1-{L,S}.json` (корни — TK674_X680_ROOTS, по умолчанию наборы основной / история пробоев / сетки шире / контекст:
/data/p42-tk680, -tk680h, -tk680g, -tk680c; нет каталога или карты — корень пропущен с заметкой) и пороги `thr680*.json` рядом (sha256 сверяется с картой).
Берутся только активные оси (`active`), прирост Шарпа (`gain`) — победителя оси. Ось ключа — значения-метки и заплатка на метку (составная ось несёт несколько ключей),
ось столбца — фильтр класса «filter» со знаком и абсолютными порогами. Одна ось, активная в двух наборах (p, fh, fk в основном и «сетки шире»), — одна, значения объединены, прирост — больший.
Не новые оси: ключ уже ось карты TK-674 (t24, mk, e, e2, natr_min, t1h) — ось карты расширяется победителем набора «сетки шире», если он вне её значений (одно значение), иначе пропуск с причиной;
столбец и знак уже ось карты TK-674. Значение ew = 0 не берётся (вход по минутным барам — упрощение, В-172).
Запреты пар (§13.73.7): столбец × ось уровня, столбец × e, столбец × ключи сигнала TK-680, составные оси с общим ключом, xb × e2 / mk / ek / split, tpp × tj, ось формы стопа × другая такая,
плюс дубли ключа (tpp × t_low / t_high, trp × rr, sg × sn) и два столбца с одним признаком и знаком. Порядок пар — по приросту Шарпа новой оси (у пары двух новых — больший), затем по имени;
пары берутся целиком, пока новых клеток (нет в БД летописи) ≤ потолка на сторону (первая не поместившаяся пара — стоп, как отбор уровней `tk674_level.py select`);
Шарп новых клеток при отборе не читается — только «есть ключ / нет». Переменные: TK674_X680_ROOTS — корни единичек; TK674_X680_NOSHA=1 — принять единственный файл порогов корня,
если его sha256 не равен записанному в карте (по умолчанию — отказ: пороги заморожены); TK674_X680_REPLAN=1 (в `tk674_run.py plan`) — отобрать пары заново."""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_axes as ax  # noqa: E402
import tk680_axes as a80  # noqa: E402

ROOTS_DEFAULT = "/data/p42-tk680,/data/p42-tk680h,/data/p42-tk680g,/data/p42-tk680c"
THR_FILES = {"thr680.json": "", "thr680h.json": "hist", "thr680g.json": "grid", "thr680c.json": "ctx"}     # файл порогов → набор
TAG = {"": "", "hist": "@h", "grid": "@g", "ctx": "@c"}                                                    # метка набора в имени оси столбца
SET_RU = {"": "основной", "hist": "история пробоев", "grid": "сетки шире", "ctx": "контекст"}
SHAPE = ("ls", "sl", "sn", "nx", "sg", "cw")                                    # оси формы стопа (§13.73.7 п. 2)
XB_BAN = ("e2", "mk", "ek", "split")
COL_BAN_KEYS = ("e", "natr_p", "tau", "pk", "pb", "fh", "fk", "fm", "fb", "fg", "f", "r", "ban", "track", "color", "ap")    # ключи, меняющие кандидатов / момент сигнала
DUP_KEYS = (("tpp", "t_low"), ("tpp", "t_high"), ("trp", "rr"), ("sg", "sn"))   # составная ось сама задаёт ключ другой оси
DROP_VALUES = {"ew": ("0",)}                                                    # не значения-кандидаты (координатор 22.11)
DROP_WHY = {"ew": "ew = 0 — вход по минутным барам: упрощение, не кандидат (В-172)"}

R_COLKEY = "столбец × ключ сигнала TK-680 (natr_p, tau, pk, pb, fh, fk, fm, fb, fg, f, r, ban, track, color, ap): sig_ms таблицы признаков не совпадут"
R_SAMECOL = "два столбца с одним признаком и знаком"
R_COMPOUND = "составные оси с общим ключом-родителем"
R_XB = "xb несовместим с e2 / mk / ek / split (params.rs)"
R_TPP = "tpp несовместим с tj (params.rs)"
R_DUP = "составная ось сама задаёт ключ другой оси (tpp → t_low / t_high, trp → rr, sg → sn)"
R_SHAPE = "ось формы стопа × другая ось формы стопа"

_CACHE = {}
_KEYS = set()                      # имена новых осей ключей (соседи плато — внутри своей формы)


def reset():
    _CACHE.clear()
    _KEYS.clear()


def roots():
    return [r.strip() for r in os.environ.get("TK674_X680_ROOTS", ROOTS_DEFAULT).split(",") if r.strip()]


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _find_thr(root, sha):
    """(имя файла, набор) порогов корня: тот, чей sha256 равен `thr_sha256` карты; без sha в карте — единственный файл."""
    found = [(f, s) for f, s in THR_FILES.items() if os.path.exists(os.path.join(root, "res", f))]
    if not found:
        raise SystemExit("X680: в %s/res нет файла порогов thr680*.json" % root)
    ok = [(f, s) for f, s in found if sha and _sha(os.path.join(root, "res", f)) == sha]
    if len(ok) == 1:
        return ok[0]
    if len(found) == 1 and (not sha or os.environ.get("TK674_X680_NOSHA") == "1"):
        return found[0]
    raise SystemExit("X680: %s — sha256 порогов не совпадает с картой (карта %s, файлы %s); пороги заморожены до счёта (TK674_X680_NOSHA=1 — принять единственный файл)" % (
        root, (sha or "—")[:12], ", ".join(f for f, _ in found)))


def _numeric(lab):
    try:
        float(lab)
        return True
    except ValueError:
        return False


def _merge_labels(lists):
    """Объединение меток значений: числовые — по возрастанию значения, прочие (составные) — порядок первого появления."""
    seen = []
    for lst in lists:
        for x in lst:
            if x not in seen:
                seen.append(x)
    return sorted(seen, key=float) if all(_numeric(x) for x in seen) else seen


def _winner_label(a):
    w = a.get("winner")
    if not w:
        return None
    return next((c["label"] for c in a["cells"] if c.get("ckey") == w["ckey"] and c["label"] != a["base"]), None)


def _key_entry(side, a, sset, eng):
    """Запись оси ключа из карты единичек: метки значений (без отвергнутых движком и без DROP_VALUES), заплатка на метку, база, прирост."""
    name = a["axis"]
    if name not in eng:
        raise SystemExit("X680: ось %s (набор «%s») есть в карте, но нет в tk680_axes — код и карта разошлись" % (name, SET_RU[sset]))
    pm, base = dict(eng[name][0]), eng[name][1]
    if a["base"] != base:
        raise SystemExit("X680: ось %s: база карты %s ≠ база tk680_axes %s" % (name, a["base"], base))
    labels, dropped = [], []
    for c in a["cells"]:
        lab = c["label"]
        if lab not in pm:
            raise SystemExit("X680: ось %s: метка %s карты не найдена в tk680_axes" % (name, lab))
        if lab in DROP_VALUES.get(name, ()):
            dropped.append((name, "значение %s: %s" % (lab, DROP_WHY[name])))
            continue
        if c.get("invalid") and lab != base:
            dropped.append((name, "значение %s: движок отверг (%s)" % (lab, c["invalid"])))
            continue
        labels.append(lab)
    wl = _winner_label(a)
    if wl is not None and wl in DROP_VALUES.get(name, ()):
        dropped.append((name, "победитель оси в карте единичек — значение %s, вне набора кандидатов; ось сохранена по указанию координатора" % wl))
    return dict(kind="key", name=name, set=sset, labels=labels, patches={lab: pm[lab] for lab in labels}, base=base, gain=a["gain"], winner=wl, dropped=dropped)


def _col_entry(side, a, sset, thr):
    """Запись оси столбца: метки, абсолютные пороги из thr680*.json (сверка с порогом клетки карты), знак."""
    col, d = a["axis"].rsplit(".", 1)
    if col not in thr[side]:
        raise SystemExit("X680: ось %s: нет столбца %s в файле порогов набора «%s»" % (a["axis"], col, SET_RU[sset]))
    op = {"le": "<=", "ge": ">="}[d]
    tm = {lab: float(x) for lab, x in thr[side][col][d]}
    labels, th, dropped = [], {}, []
    for c in a["cells"]:
        lab = c["label"]
        if lab == a["base"]:
            labels.append(lab)
            continue
        if lab not in tm:
            raise SystemExit("X680: ось %s: метка %s без порога в thr680*.json" % (a["axis"], lab))
        if "thr" in c and float(c["thr"]) != tm[lab]:
            raise SystemExit("X680: ось %s, метка %s: порог карты %s ≠ порог файла %s" % (a["axis"], lab, c["thr"], tm[lab]))
        if c.get("invalid"):
            dropped.append((a["axis"] + TAG[sset], "значение %s: движок отверг (%s)" % (lab, c["invalid"])))
            continue
        labels.append(lab)
        th[lab] = (col, op, tm[lab])
    return dict(kind="col", name=a["axis"] + TAG[sset], set=sset, labels=labels, thr=th, base=a["base"], gain=a["gain"], col=col, op=op,
                src=a80.COL_MAP.get(col, col), dropped=dropped)


def _check_keys(side, axis, map_cells):
    """Сверка: клетка «ось, значение» при остальном базе — тот же канонический ключ, что у клетки карты единичек (общий ключевой простор БД); иначе пары не найдут единичек."""
    bad = []
    for c in map_cells:
        if c.get("invalid") or c["label"] not in axis["vals"] or not c.get("ckey"):
            continue
        patch, filt, ok, why = ax.cell_assign(side, {axis["name"]: c["label"]})
        thr = {axis["name"]: axis["thr"]} if axis.get("thr") else {}
        ck = ax.cell_ckey(side, patch, filt, thr)
        if ck != c["ckey"]:
            bad.append((c["label"], ck, c["ckey"]))
    if bad:
        raise SystemExit("X680: ось %s: ключ клетки не совпал с картой единичек (%d из %d), первая: значение %s — здесь «%s», в карте «%s»" % (
            axis["name"], len(bad), len(map_cells), bad[0][0], bad[0][1], bad[0][2]))


def _load(side):
    old_axes = {a["name"]: a for a in ax._old_axes(side)}
    old_filters = {(a["key"], a["op"]) for a in old_axes.values() if a["cls"] == "filter"}
    notes, dropped, entries, col_entries, found_roots = [], [], {}, {}, []
    for ri, root in enumerate(roots()):
        mp = os.path.join(root, "res", "map674b1-%s.json" % side)
        if not os.path.isdir(root) or not os.path.exists(mp):
            notes.append("корень %s пропущен: %s" % (root, "нет каталога" if not os.path.isdir(root) else "нет %s" % mp))
            continue
        m = json.load(open(mp, encoding="utf-8"))
        tf, sset = _find_thr(root, m.get("thr_sha256"))
        thr = json.load(open(os.path.join(root, "res", tf), encoding="utf-8"))
        eng = {n: (v, b) for n, v, b in a80.engine_axes(side, sset)}
        found_roots.append((root, sset, sum(1 for a in m["axes"] if a.get("active"))))
        for mi, a in enumerate(m["axes"]):
            if not a.get("active"):
                continue
            if a["kind"] == "key":
                e = _key_entry(side, a, sset, eng)
                e.update(order=(ri, mi), map_cells=a["cells"])
                dropped += e.pop("dropped")
                entries.setdefault(e["name"], []).append(e)
            else:
                e = _col_entry(side, a, sset, thr)
                e.update(order=(ri, mi), map_cells=a["cells"])
                dropped += e.pop("dropped")
                if (e["col"], e["op"]) in old_filters:
                    dropped.append((e["name"], "столбец и знак уже ось карты TK-674 (%s %s)" % (e["col"], e["op"])))
                    continue
                col_entries[e["name"]] = e
    new_axes, ext = [], {}
    for name, es in entries.items():
        gain = max(e["gain"] for e in es)
        sets = [e["set"] for e in es]
        pm, base = {}, es[0]["base"]
        for e in es:
            if e["base"] != base:
                raise SystemExit("X680: ось %s: база %s в наборе «%s» ≠ %s" % (name, e["base"], SET_RU[e["set"]], base))
            for lab, p in e["patches"].items():
                if lab in pm and pm[lab] != p:
                    raise SystemExit("X680: ось %s, значение %s: заплатки наборов расходятся (%s ≠ %s)" % (name, lab, pm[lab], p))
                pm[lab] = p
        labels = _merge_labels([e["labels"] for e in es])
        if len(es) > 1:
            notes.append("ось %s активна в наборах %s: одна ось, значения объединены (%s), прирост Шарпа — больший (%+.2f)" % (
                name, " и ".join("«%s»" % SET_RU[s] for s in sets), ", ".join(labels), gain))
        order = min(e["order"] for e in es)
        if name in old_axes:                                    # ключ уже ось карты TK-674: не новая ось
            oa = old_axes[name]
            wl = [e["winner"] for e in es if e["winner"] is not None]
            if oa["cls"] == "filter" or name == "split":
                dropped.append((name, "ключ уже ось карты TK-674 (составная / столбец) — расширять нечем"))
                continue
            have = {ax.num(v) for v in oa["vals"]}
            add = sorted({ax.num(w) for w in wl if _numeric(w)} - have, key=float)
            if not add:
                dropped.append((name, "ключ уже ось карты TK-674; победитель набора (%s) — значение оси карты, расширять нечем (пары оси с новыми осями идут и так)" % (
                    ", ".join(wl) or "—")))
                continue
            vals = sorted(list(oa["vals"]) + [float(x) for x in add], key=float)
            ext[name] = dict(oa, vals=vals, x680=True, kind="ext", gain=gain, set=sets, ext=add, order=order)
            notes.append("ось карты %s расширена значением %s (победитель набора «%s», активна, вне прежних значений)" % (name, ", ".join(add), SET_RU[sets[-1]]))
            continue
        keys = sorted({k for p in pm.values() for k in p})
        new_axes.append(dict(name=name, cls="x680key", key=name, vals=labels, base=base, patches={lab: pm[lab] for lab in labels}, keys=tuple(keys), x680=True,
                             kind="key", gain=gain, set=sets, order=order, compound=len(keys) > 1))
    for e in col_entries.values():
        new_axes.append(dict(name=e["name"], cls="filter", key=e["col"], op=e["op"], vals=e["labels"], base=e["base"], thr=e["thr"], x680=True, kind="col",
                             gain=e["gain"], set=[e["set"]], order=e["order"], src=e["src"]))
    new_axes.sort(key=lambda a: (a["order"], a["name"]))
    for a in new_axes:
        a.pop("order")
    for a in ext.values():
        a.pop("order")
    checks = [(name, e["map_cells"]) for name, es in entries.items() if name in {a["name"] for a in new_axes} for e in es]
    checks += [(name, e["map_cells"]) for name, e in col_entries.items()]
    _KEYS.update(a["name"] for a in new_axes if a["kind"] == "key")
    for n in notes:
        print("X680 %s: %s" % (side, n), flush=True)
    return dict(new=new_axes, ext=ext, dropped=dropped, notes=notes, roots=found_roots, checks=checks)


def load(side):
    key = (side, tuple(roots()), os.environ.get("TK674_X680_NOSHA", ""))
    if key not in _CACHE:
        if not ax.REBASE:
            raise SystemExit("TK674_X680=1 требует TK674_REBASE=1")
        L = _load(side)
        _CACHE[key] = L                                  # сверка ключей идёт через `ax.axes` → `load`: сначала в кэш
        try:
            names = {a["name"]: a for a in L["new"]}
            for name, cells in L["checks"]:
                if name in names:
                    _check_keys(side, names[name], cells)
        except SystemExit:
            del _CACHE[key]
            raise
    return _CACHE[key]


def new_axes(side):
    return list(load(side)["new"])


def merge_axes(side, old):
    """Оси карты: прежние (расширенные — значением победителя набора «сетки шире») + новые активные оси единичек."""
    L = load(side)
    return [L["ext"].get(a["name"], a) for a in old] + list(L["new"])


def thresholds(side):
    """{ось столбца: {метка: (признак, знак, порог)}} новых осей — к порогам оси карты (`thr674.json`) для канонического ключа клетки с фильтром."""
    return {a["name"]: a["thr"] for a in load(side)["new"] if a["kind"] == "col"}


def column_specs(side):
    """Параметры `tk680_singles.add_columns` для столбцов активных осей: [(имя в таблице, источник, знаки)], расчёт cosig15, файл ok10_d3 / d5 — как у счёта единичек этих наборов."""
    cols = {}
    for a in load(side)["new"]:
        if a["kind"] == "col":
            cols[a["key"]] = a["src"]
    cl = [(c, src, ("le", "ge")) for c, src in sorted(cols.items())]
    return dict(cl=cl, ctx="cosig15" in cols, hist=bool({"ok10_d3", "ok10_d5"} & set(cols)))


def nb_idx(name, vals, i):
    """Соседи значения i новой оси ключа: на шаг по порядку, только внутри своей формы (префикс метки без числа, как `_prefix` карты единичек); None — не наша ось."""
    if name not in _KEYS:
        return None
    pre = lambda lab: re.sub(r"[-\d.]+$", "", lab)
    return [j for j in (i - 1, i + 1) if 0 <= j < len(vals) and pre(vals[j]) == pre(vals[i])]


# ---- запреты совместимости ---------------------------------------------------------------------------------------------------------
def keys_of(a):
    """Ключи движка, которые ось задаёт (хотя бы в одном значении)."""
    if a.get("keys") is not None:
        return tuple(a["keys"])
    if a["cls"] == "filter":
        return ()
    if a["name"] == "split":
        return tuple(ax.SPLIT_KEYS)
    return (a["key"],)


def _colsrc(a):
    return repr(a["src"] if a.get("src") is not None else a["key"]), a["op"]


def compat(side, a, b):
    """(False, причина) для запрещённой пары с новой осью; None — дальше прежние правила `tk674_axes.compat`."""
    names = {a["name"], b["name"]}
    if "xb" in names and (names - {"xb"}) & set(XB_BAN):
        return False, R_XB
    if names == {"tpp", "tj"}:
        return False, R_TPP
    if any(names == set(p) for p in DUP_KEYS):
        return False, R_DUP
    if a["name"] in SHAPE and b["name"] in SHAPE:
        return False, R_SHAPE
    fa, fb = a["cls"] == "filter", b["cls"] == "filter"
    if fa != fb:
        o = b if fa else a
        if o.get("x680") and set(keys_of(o)) & set(COL_BAN_KEYS):
            return False, R_COLKEY
    elif fa and fb and _colsrc(a) == _colsrc(b):
        return False, R_SAMECOL
    ka, kb = keys_of(a), keys_of(b)
    if len(ka) > 1 and len(kb) > 1 and set(ka) & set(kb):
        return False, R_COMPOUND
    return None


# ---- допустимость клетки ----------------------------------------------------------------------------------------------------------
_LINE = {}


def _line_value(side, k):
    if side not in _LINE:
        _LINE[side] = ax.parse_line(ax.BASE_LINE(side))
    if k in _LINE[side]:
        return float(_LINE[side][k])
    return float(ax.DEFAULTS[k]) if k in ax.DEFAULTS else None


def _demand(side, a, v):
    """{ключ: значение}, которое ось требует в клетке (включая значения базы стороны у ключей составной оси)."""
    if a.get("patches") is not None:
        p = a["patches"][v]
        return {k: (float(p[k]) if k in p else _line_value(side, k)) for k in a["keys"]}
    if a["cls"] == "filter" or a["name"] == "s":
        return {}
    if a["name"] == "split":
        return {k: float(ax.SPLIT_VALS[v][k]) for k in ax.SPLIT_KEYS}
    return {a["key"]: float(v)}


def cell_check(side, am, assign):
    """Причина недопустимости клетки с новой осью: две оси требуют разные значения одного ключа (ключ составной оси при базовом значении другой — тоже), иначе пусто."""
    dem = [(n, _demand(side, am[n], v)) for n, v in assign.items()]
    for (n1, d1), (n2, d2) in itertools.combinations(dem, 2):
        for k in d1.keys() & d2.keys():
            if d1[k] is not None and d2[k] is not None and abs(d1[k] - d2[k]) > 1e-12:
                return "ключ %s: ось %s требует %g, ось %s — %g" % (k, n1, d1[k], n2, d2[k])
    return ""


# ---- отбор пар ---------------------------------------------------------------------------------------------------------------------
def pairs_by_rule(side):
    """([{pair, a, b, gain}] по убыванию прироста Шарпа новой оси, затем по имени; [(a, b, причина)] запрещённых) — пары, где есть новая ось."""
    A = ax.axes(side)
    rule, banned = [], []
    for a, b in itertools.combinations(A, 2):
        if not (a.get("x680") or b.get("x680")):
            continue
        ok, why = ax.compat(side, a, b)
        if not ok:
            banned.append((a["name"], b["name"], why))
            continue
        rule.append(dict(pair="%s|%s" % (a["name"], b["name"]), a=a["name"], b=b["name"], gain=max(x["gain"] for x in (a, b) if x.get("x680"))))
    rule.sort(key=lambda p: (-p["gain"], p["pair"]))
    return rule, banned


def select(side, lookup, cap, thr, inv=()):
    """Отбор пар по правилу: пары по порядку целиком, пока новых клеток стороны ≤ cap (первая не поместившаяся — стоп). lookup(ключи) → {ключ: …} найденных в БД (читаются только ключи).
    thr — {ось: {метка: (признак, знак, порог)}} (прежние оси признаков + новые столбцы); inv — cid клеток, отвергнутых движком."""
    rule, banned = pairs_by_rule(side)
    A = {a["name"]: a for a in ax.axes(side)}
    seen, taken, n_new, n_have = {}, [], 0, 0
    for p in rule:
        g = ax.pair_grid(side, A[p["a"]], A[p["b"]])
        keys = {}
        for pos, (patch, filt, ok, why) in g["cells"].items():
            if not ok:
                continue
            if ax.cell_id(patch, filt) in inv:
                continue
            keys[ax.cell_ckey(side, patch, filt, thr)] = 1
        fresh = [k for k in keys if k not in seen]
        found = set(lookup(fresh)) if fresh else set()
        new = [k for k in fresh if k not in found]
        if n_new + len(new) > cap:
            break
        for k in fresh:
            seen[k] = k in found
        n_new += len(new)
        n_have += len(fresh) - len(new)
        taken.append(dict(pair=p["pair"], gain=p["gain"], cells=len(keys), new=len(new)))
    by = {}
    for _, _, why in banned:
        by[why] = by.get(why, 0) + 1
    return dict(cap=cap, n_rule=len(rule), taken=taken, left_out=[dict(pair=p["pair"], gain=p["gain"]) for p in rule[len(taken):]], banned=[list(x) for x in banned],
                banned_by_reason=by, n_cells=len(seen), n_have=n_have, n_new=n_new, axes_sig=fingerprint(side))


def fingerprint(side):
    """Подпись набора новых осей (имена, значения, заплатки / пороги, прирост): замороженный отбор пар не применяется к другим осям."""
    rows = [(a["name"], a["kind"], a["vals"], a["base"], sorted((k, sorted(v.items())) for k, v in (a.get("patches") or {}).items()),
             sorted((k, list(v)) for k, v in (a.get("thr") or {}).items()), round(a["gain"], 9)) for a in ax.axes(side) if a.get("x680")]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:16]


def summary(side):
    """Строки для таблицы «есть / считаем»: активные новые оси по наборам, расширенные оси карты, пропущенные с причиной, заметки."""
    L = load(side)
    by = {}
    for a in L["new"]:
        for s in a["set"]:
            by.setdefault(s, []).append(a["name"])
    return dict(by_set={SET_RU[s]: v for s, v in by.items()}, n_new=len(L["new"]), ext={n: dict(add=a["ext"], gain=a["gain"]) for n, a in L["ext"].items()},
                dropped=L["dropped"], notes=L["notes"], roots=L["roots"])
