#!/usr/bin/env python3
"""П-42 §13.73 (TK-674): импорт готовых клеток h775 (окна wa / wb / wc, обе стороны) задач TK-638 / 643 / 649 / 650 / 652 / 654 / 655 / 657 / 660 / 668 в БД летописи.
Слова владельца 22.11: «Результаты в бд загоняем то что уже прогоняли не делаем» · «Я про бд летопись». Движок не запускается (кроме `spot`).
  python3 tk674_import.py scan             — инвентарь: клетки на диске, строка движка, бинарь (md5 + доказательство), ключ; метрики строкой `tk674_stats.metrics`;
                                             → res/import674-rows.jsonl, import674-skipped.json, import674-inventory.json (в БД не пишет)
  python3 tk674_import.py put --entry L-N  — строки в БД (`cells_put`, партии ≤ 200); повтор не дублирует; расхождения «тот же ключ + бинарь» — в отчёт
  python3 tk674_import.py spot             — выборочная проверка ≤ 3 клеток новым бинарём на wa (сторона × бинарь старой клетки), байты CSV; → res/eqbins674.json
  python3 tk674_import.py count            — `cells_count` и доля ключей сеток пар (`tk674_run.plan_side`), уже лежащих в БД
Ключ клетки — `tk674_axes.canon(строка, фильтр по смыслу)`; строка движка проверяется по `meta.json` клетки (`params` из `Debug` движка): каждое поле = значению ключа.
Доллары — ставка 500 $; Sharpe — годовой по суточному ряду; без бп, без медиан.
Каталоги клеток чужих задач (TK-643 и др.) читает как есть; клетки c674c на упакованных партиях (`tk674_pack`) — не читает."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_axes as ax  # noqa: E402

ROOT = os.environ.get("TK674_ROOT", "/data/p42-tk674")
UNI, WIN = "h775", "01.07–13.11"
SIDES = ("L", "S")
SIDENAME = {"L": "long", "S": "short"}
WINS = ("wa", "wb", "wc")
FV = "0.5"
BATCH = 200
SRC = "tools/p42/tk674_import.py"


def res(*a):
    p = os.path.join(ROOT, "res", *a)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---- проверка строки движка по `params` из meta.json -----------------------------------------------------------------------------
# ключ движка → (путь поля в `Debug` Params, вид значения); `Params::set` (src/breakout/params.rs): pm = round(f·1000), big = round(f·1e6·1e5), h = round(f·60)
FIELDS = {
    "natr_p": ("sig.natr_p", "r"), "natr_min": ("sig.natr_min_pm", "pm"), "t1h": ("sig.t1h_u", "big"), "t24": ("sig.t24_u", "big"), "natr_exit_p": ("exit.natr_p", "r"),
    "tf": ("sig.lv.tf_min", "r"), "k": ("sig.lv.k", "r"), "d": ("sig.lv.d_pm", "pm"), "n_bounce": ("sig.lv.n", "r"), "tau": ("sig.lv.tau_pm", "pm"),
    "l_days": ("sig.lv.l_days", "r"), "pk": ("sig.lv.pk", "b"), "lx": ("sig.lv.lx", "r"), "lw": ("sig.lv.lw", "r"), "track": ("sig.track_pm", "pm"),
    "e": ("sig.e_pm", "pm"), "color": ("sig.color", "b"), "f": ("sig.f_pm", "pm"), "r": ("sig.r_min", "r"), "ban": ("sig.ban", "b"), "ap": ("sig.ap", "r"),
    "pb": ("sig.pb", "b"), "fh": ("sig.fr_h_min", "h"), "fk": ("sig.fr_k", "r"), "fm": ("sig.fr_share", "r"), "fb": ("sig.fr_b_pm", "pm"), "fg": ("sig.fg_m", "pm"),
    "n_tp": ("exit.n_tp", "r"), "t_low": ("exit.t_low_m", "pm"), "t_high": ("exit.t_high_m", "pm"), "q": ("exit.q_pct", "r"), "be": ("exit.be", "b"),
    "a": ("exit.a_m", "pm"), "rr": ("exit.r_m", "pm"), "s": ("exit.s_pm", "pm"), "slip": ("exit.slip_bps", "r"), "best": ("exit.best_order", "b"),
    "lat": ("exit.lat_ms", "r"), "latx": ("exit.lat_exit_ms", "r"), "tpg": ("exit.tp_gamma_m", "pm"), "tpk": ("exit.tp_kappa_m", "pm"), "ns": ("exit.natr_src", "r"),
    "ew": ("exit.ent_w_ms", "r"), "ex": ("exit.ent_x_bp", "r"), "ls": ("exit.ls_m", "pm"), "tt": ("exit.tx_min", "r"), "tb": ("exit.tx_x_pm", "pm"),
    "rf": ("exit.rt_f_pm", "pm"), "rs": ("exit.rt_s", "r"), "bm": ("exit.be_mode", "r"), "e2": ("exit.e2_pm", "pm"), "sn": ("exit.sn_m", "pm"), "sg": ("exit.sg", "r"),
    "sb": ("exit.sb_m", "pm"), "sr": ("exit.sr_m", "pm"), "sd": ("exit.sd_m", "pm"), "tj": ("exit.tj_m", "pm"), "tl": ("exit.tl", "r"), "nx": ("exit.nx_m", "pm"),
    "sl": ("exit.sl_m", "pm"), "cw": ("exit.cs_w", "r"), "cl": ("exit.cs_lo_m", "pm"), "ch": ("exit.cs_hi_m", "pm"), "cm": ("exit.cs_m_m", "pm"),
    "tu": ("exit.tu_min", "r"), "tpp": ("exit.tpp", "b"), "trp": ("exit.trp", "b"),
    "c": ("port.cool_min", "r"), "m": ("port.m_stops", "r"), "mm": ("port.mm_min", "r"), "s_glob": ("port.s_glob", "r"), "w": ("port.w_min", "r"),
    "ww": ("port.ww_min", "r"), "p": ("port.p", "r"), "reentry": ("port.reentry", "r"), "busy": ("port.busy", "b"),
    "mk": ("mk", "r"), "ek": ("ek", "r"), "xb": ("xb", "pm"),
}
FILT_KEYS = ("fa", "fo", "fv")


def rround(x):
    """Rust `f64::round`: от нуля."""
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def enc(kind, f):
    if kind == "b":
        return f != 0.0
    if kind == "pm":
        return rround(f * 1000.0)
    if kind == "big":
        return rround(f * 1e6 * 1e5)
    if kind == "h":
        return rround(f * 60.0)
    return rround(f)


_RE_FIELD = re.compile(r"(\w+): ")
_RE_OPEN = re.compile(r"(\w+) \{")
_RE_SEP = re.compile(r"\s*,?\s*")
_RE_SCALAR = re.compile(r"[^,}\s]+")


def _val(v):
    if v == "true":
        return True
    if v == "false":
        return False
    if v == "None":
        return None
    if v.startswith("Some("):
        inner = v[5:-1]
        try:
            return ("some", float(inner))
        except ValueError:
            return ("some", inner == "true" if inner in ("true", "false") else inner)
    if v.startswith('"'):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        return float(v)


def flat_params(s):
    """`Params { sig: SigCfg { lv: LevelCfg { tf_min: 60, … }, natr_p: 30, … }, … }` → {"sig.lv.tf_min": 60, …} (поля без значения по умолчанию могут не печататься)."""
    m = re.match(r"\s*\w+ \{", s)
    if not m:
        raise ValueError("params: " + s[:60])
    i, n, stack, out = m.end(), len(s), [], {}
    while i < n:
        i = _RE_SEP.match(s, i).end()
        if i >= n:
            break
        if s[i] == "}":
            if stack:
                stack.pop()
            i += 1
            continue
        m = _RE_FIELD.match(s, i)
        if not m:
            raise ValueError("params: разбор на %r" % s[i:i + 40])
        name, i = m.group(1), m.end()
        m2 = _RE_OPEN.match(s, i)
        if m2:
            stack.append(name)
            i = m2.end()
            continue
        if s[i] == '"':
            j = i + 1
            while s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            raw = s[i:j + 1]
        elif s.startswith("Some(", i):
            raw = s[i:s.index(")", i) + 1]
        else:
            raw = _RE_SCALAR.match(s, i).group(0)
        i += len(raw)
        out[".".join(stack + [name])] = _val(raw)
    return out


def check_params(line, params):
    """Строка движка ↔ `params` клетки: каждое поле ключа строки = значению, остальные поля = значениям по умолчанию (`tk674_axes.DEFAULTS`); [] — совпало.
    Поле, которого `Debug` не печатает (нулевое у старых сборок), считается нулём."""
    obs = flat_params(params)
    kv = ax.parse_line(line)
    bad = []

    def cmp(key, want, where):
        path, kind = FIELDS[key]
        got = obs.get(path)
        if got is None:
            if want not in (0, False):
                bad.append("%s: поля %s нет в params, ожидалось %r (%s)" % (key, path, want, where))
        elif got != want:
            bad.append("%s: %s = %r ≠ %r (%s)" % (key, path, got, want, where))
    for key, v in kv.items():
        if key in FILT_KEYS:
            continue
        if key not in FIELDS:
            bad.append("ключ %s не в таблице полей" % key)
            continue
        cmp(key, enc(FIELDS[key][1], float(v)), "строка")
    for key, d in ax.DEFAULTS.items():
        if key in kv or key not in FIELDS:
            continue
        cmp(key, enc(FIELDS[key][1], float(d)), "по умолчанию")
    fa = kv.get("fa")
    name, hi, v = obs.get("filt.name", ""), obs.get("filt.hi"), obs.get("filt.v")
    if fa is None:
        if name != "" or hi is not None or v is not None:
            bad.append("фильтр: в params есть, в строке нет (%r %r %r)" % (name, hi, v))
    else:
        want_hi = ("some", kv.get("fo") == "hi")
        if name != fa or hi != want_hi or v is None or abs(v[1] - float(kv["fv"])) > 1e-12:
            bad.append("фильтр: params %r %r %r ≠ строка fa=%s fo=%s fv=%s" % (name, hi, v, fa, kv.get("fo"), kv.get("fv")))
    return bad


# ---- ключ клетки по смыслу ----------------------------------------------------------------------------------------------------------
def ckey_of(line, filt):
    """Канонический ключ: ключи движка без значений по умолчанию (+ `lw` только при lx = 2: у lx = 1, 3 источник — сутки, `lw` не действует, levels.rs), фильтр — по смыслу."""
    kv = ax.parse_line(line)
    if kv.get("lx") not in ("2", "2.0") and "lw" in kv:
        line = " ".join(t for t in line.split() if not t.startswith("lw="))
    return ax.canon(line, filt)


def diff_patch(ck, base_ck):
    """Заплатка клетки относительно базы стороны по каноническим ключам (отсутствие ключа = значение по умолчанию)."""
    def kv(s):
        return {t.split("=", 1)[0]: t.split("=", 1)[1] for t in s.split() if "=" in t and not t.startswith("F=")}
    a, b = kv(ck), kv(base_ck)
    out = {}
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k, ax.num(ax.DEFAULTS.get(k, 0))), b.get(k, ax.num(ax.DEFAULTS.get(k, 0)))
        if va != vb:
            out[k] = va
    return out


def cell_label(ck, base_ck):
    p = diff_patch(ck, base_ck)
    parts = ["%s%s" % (k, v) for k, v in p.items()]
    if "F=" in ck:
        parts.append(ck.split("F=", 1)[1])
    return "+".join(parts) or "base"


# ---- бинари и доказательство ---------------------------------------------------------------------------------------------------------
def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def _json(path):
    return json.load(open(path, encoding="utf-8"))


# ключ бинаря → (путь, ожидаемый md5-префикс, где записан sha256 / чем доказано)
BINS = {
    "turbo": dict(path="/data/p42-tk599/bin/alpha-master", md5="a112684a", sha="ee28fd7b85eada7dd30ef96e3a91bba14df0489fb929c7a7e7163c8b72a49b53",
                  how="`r4.BIN` задач (G-bin: sha256 ∈ BIN_SHA_OK = ee28fd7b…); файл не менялся с 18.11 21:48 UTC, клетки старше нет"),
    "mk": dict(path="/data/p42-tk616/alpha-mk.396989e9", md5="396989e9", sha="470046d924392f0eab60ee730c43eb943c17913463857458e93b702127ac7fe0",
               how="`tk652_run.FROZEN_BIN` (G-bin: sha256 = FROZEN_SHA замороженного TK-626)"),
    "tu": dict(path="/data/p42-tk649/bin/alpha-tk649-plain.f7ade5d3", md5="f7ade5d3", sha_file="/data/p42-tk649/res/gbyte49.json", sha_key="bin_sha256",
               how="`tk649_run.BIN` (cells требует bin_sha256 gbyte49.json = sha256 файла)"),
    "ek": dict(path="/data/p42-tk660/bin/alpha-tk660.2626d580", md5="2626d580", sha_file="/data/p42-tk660/res/bin60.json", sha_key="sha256",
               how="`tk660_run.BIN` (res/bin60.json sha256 = sha256 файла)"),
    "lx": dict(path="/data/p42-tk668/bin/alpha-lx.0bf46f43", md5="0bf46f43", sha_file="/data/p42-tk668/res/gbyte-e4.json", sha_key="bin_sha256",
               how="`tk668_e4.BIN` (res/gbyte-e4.json bin_sha256 = sha256 файла)"),
    "new": dict(path="/data/p42-tk674/bin/alpha-tk674.a26ce03f", md5="a26ce03f", sha_file="/data/p42-tk674/res/gbase674.json", sha_key="bin_sha256",
                how="`tk674_run.BIN` (res/gbase674.json: G-base 6 строк байт в байт)"),
}


def bin_registry():
    """{ключ: dict(md5 полный, sha256, mtime, ok, why)}; ok = файл есть, md5 начинается с ожидаемого, sha256 = записанному в gate-файле задачи."""
    out = {}
    for k, b in BINS.items():
        d = dict(path=b["path"], how=b["how"], ok=False, why="")
        if not os.path.exists(b["path"]):
            d["why"] = "файла нет"
        else:
            d["md5"], d["sha256"], d["mtime"] = md5_of(b["path"]), sha256_of(b["path"]), os.path.getmtime(b["path"])
            want = b.get("sha") or _json(b["sha_file"]).get(b["sha_key"])
            if not d["md5"].startswith(b["md5"]):
                d["why"] = "md5 %s ≠ %s…" % (d["md5"], b["md5"])
            elif want != d["sha256"]:
                d["why"] = "sha256 %s ≠ записанному %s" % (d["sha256"][:12], str(want)[:12])
            else:
                d["ok"] = True
        out[k] = d
    return out


# ---- перечень клеток ----------------------------------------------------------------------------------------------------------------
def _cellf(path_fn, w, side, cid):
    d = path_fn(w, side, cid)
    return d


def _complete(d, side):
    e = SIDENAME[side]
    return all(os.path.exists(os.path.join(d, f)) for f in ("meta.json", e + "-trades.csv", e + "-daily.csv"))


class Inv:
    """Накопитель: клетки (dict) и пропуски (task, group, side, cid, причина)."""

    def __init__(self):
        self.cells, self.skipped = [], []

    def skip(self, task, group, side, cid, why):
        self.skipped.append(dict(task=task, group=group, side=side, cid=cid, why=why))

    def add(self, task, group, side, cid, lines, filt, path_fn, binkey):
        """lines — строка движка (одна для всех окон) или {окно: строка}; path_fn(w) → каталог клетки окна; filt — [(признак, знак, порог)]."""
        ls = {w: lines for w in WINS} if isinstance(lines, str) else lines
        dirs = {w: path_fn(w) for w in WINS}
        miss = [w for w in WINS if not os.path.isdir(dirs[w]) or not _complete(dirs[w], side)]
        if miss:
            return self.skip(task, group, side, cid, "нет клетки в окне %s" % ",".join(miss))
        self.cells.append(dict(task=task, group=group, side=side, cid=cid, lines=ls, filt=[list(x) for x in filt], dirs=dirs, bin=binkey))

    def listed(self, task, group, side, path_dir_fn, known):
        """Каталоги клеток на диске, которых нет в описании задачи — в пропуски."""
        seen = set()
        for w in WINS:
            d = path_dir_fn(w)
            if os.path.isdir(d):
                seen |= {x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))}
        for cid in sorted(seen - set(known)):
            self.skip(task, group, side, cid, "каталог клетки на диске без описания в коде задачи")


def uni():
    import tk625_run as r5
    return r5.Uni(UNI)


def enum_tk643(inv, u):
    import tk625_run as r5
    import tk643_run as t43
    root = "/data/p42-tk643"
    for side in SIDES:
        cx = _json(root + "/res/cross43-%s.json" % side)["cells"]
        spec = [("base", {})] + [(c["cid"], c["patch"]) for b in t43.block_cells(side).values() for c in b] + [(cid, r["patch"]) for cid, r in cx.items()]
        for cid, patch in spec:
            line = t43.variant_line(r5, u, side, patch)
            inv.add("TK-643", "cells", side, cid, line, [], lambda w, side=side, cid=cid: "%s/cells/%s-%s/%s" % (root, w, side, cid), "turbo")
        inv.listed("TK-643", "cells", side, lambda w, side=side: "%s/cells/%s-%s" % (root, w, side), [c for c, _ in spec])


def enum_tk650(inv, u):
    import tk625_run as r5
    import tk643_run as t43
    import tk650_run as t50
    root = "/data/p42-tk650"
    for side in SIDES:
        spec = [("base", {})] + [(c["cid"], c["patch"]) for c in t50.cells()]
        for cid, patch in spec:
            inv.add("TK-650", "cells", side, cid, t43.variant_line(r5, u, side, patch), [], lambda w, side=side, cid=cid: "%s/cells/%s-%s/%s" % (root, w, side, cid), "turbo")
        inv.listed("TK-650", "cells", side, lambda w, side=side: "%s/cells/%s-%s" % (root, w, side), [c for c, _ in spec])


def enum_tk652(inv, u):
    import tk625_run as r5
    import tk643_run as t43
    import tk652_run as t52
    root = "/data/p42-tk652"
    for side in SIDES:
        spec = [("base", {})] + [(c["cid"], c["patch"]) for c in t52.cells(side)]
        for cid, patch in spec:
            inv.add("TK-652", "cells", side, cid, t43.variant_line(r5, u, side, patch), [], lambda w, side=side, cid=cid: "%s/cells/%s-%s/%s" % (root, w, side, cid), "mk")
        inv.listed("TK-652", "cells", side, lambda w, side=side: "%s/cells/%s-%s" % (root, w, side), [c for c, _ in spec])


def enum_tk655(inv, u):
    import tk625_run as r5
    import tk643_run as t43
    import tk655_run as t55
    root = "/data/p42-tk655"
    for side in SIDES:
        spec = [("base", {})] + [(c["cid"], c["patch"]) for c in t55.cells(side) if c["block"] != "K"]
        for cid, patch in spec:
            inv.add("TK-655", "cells", side, cid, t43.variant_line(r5, u, side, patch), [], lambda w, side=side, cid=cid: "%s/cells/%s-%s/%s" % (root, w, side, cid), "turbo")
        inv.listed("TK-655", "cells", side, lambda w, side=side: "%s/cells/%s-%s" % (root, w, side), [c for c, _ in spec])
        if side == "L":
            for c in t55.cells(side):
                if c["block"] == "K":
                    inv.skip("TK-655", "cells", side, c["cid"], "блок K — повтор сделок по ленте на Python (replay55), не клетка движка")


def enum_tk657(inv, u):
    import tk625_run as r5
    import tk643_run as t43
    import tk657_run as t57
    root = "/data/p42-tk657"
    side = "S"
    spec = [("base", {})] + [(c["cid"], c["patch"]) for c in t57.cells()]
    for cid, patch in spec:
        inv.add("TK-657", "cells", side, cid, t43.variant_line(r5, u, side, patch), [], lambda w, cid=cid: "%s/cells/%s-S/%s" % (root, w, cid), "turbo")
    inv.listed("TK-657", "cells", side, lambda w: "%s/cells/%s-S" % (root, w), [c for c, _ in spec])


def enum_tk660(inv, u):
    import tk625_run as r5
    import tk643_run as t43
    import tk660_run as t60
    root = "/data/p42-tk660"
    for side, spec in (("S", [("base", {})] + [(c["cid"], c["patch"]) for c in t60.cells()]), ("L", [("base", {})])):
        for cid, patch in spec:
            inv.add("TK-660", "cells", side, cid, t43.variant_line(r5, u, side, patch), [], lambda w, side=side, cid=cid: "%s/cells/%s-%s/%s" % (root, w, side, cid), "ek")
        inv.listed("TK-660", "cells", side, lambda w, side=side: "%s/cells/%s-%s" % (root, w, side), [c for c, _ in spec])


def enum_tk649(inv, u):
    import tk625_run as r5
    import tk649_run as t49
    root = "/data/p42-tk649"
    for side in SIDES:
        spec = t49.cells()
        for cid, T in spec:
            inv.add("TK-649", "cells", side, cid, t49.base_line(r5, u, side, T), [], lambda w, side=side, cid=cid: "%s/cells/%s-%s/%s" % (root, w, side, cid), "tu")
        inv.listed("TK-649", "cells", side, lambda w, side=side: "%s/cells/%s-%s" % (root, w, side), [c for c, _ in spec])
        inv.add("TK-649", "gbyte", side, r5.VID[side], t49.base_line(r5, u, side), [], lambda w, side=side: "%s/gbyte/%s/%s" % (root, w, r5.VID[side]), "tu")


def enum_tk638(inv, u):
    import tk638_post as p8
    import tk638_run as t8
    root = "/data/p42-tk638"
    thr = _json(t8.thr_path())
    for side in SIDES:
        pool = p8.pool_variants(u, side)
        for cid, line in pool:
            inv.add("TK-638", "pool", side, cid, line, [], lambda w, side=side, cid=cid: "%s/pool-h775/%s-%s/%s" % (root, w, side, cid), "turbo")
        inv.listed("TK-638", "pool", side, lambda w, side=side: "%s/pool-h775/%s-%s" % (root, w, side), [c for c, _ in pool])
        lad = {c["cid"]: c for c in t8.ladder_cells(thr[side])}
        lv = p8.ladder_variants(u, side)
        for cid, line in lv:
            c = lad[cid]
            inv.add("TK-638", "ladd", side, cid, line, [(c["col"], ">=", c["N"])], lambda w, side=side, cid=cid: "%s/ladd-h775/%s-%s/%s" % (root, w, side, cid), "turbo")
        inv.listed("TK-638", "ladd", side, lambda w, side=side: "%s/ladd-h775/%s-%s" % (root, w, side), [c for c, _ in lv])


def tk654_filt(c):
    """Смысл фильтра клетки TK-654 (оставляет кандидата, если признак проходит; «определён и не проходит» — исключён, пусто проходит)."""
    k = c["kind"]
    if k == "lad":
        return [(c["col"], ">=" if c["dir"] == "ge" else "<=", c["X"])]
    if k == "bk":
        return [("bk%d" % c["K"], ">=", c["tau"])]
    return {"CLEAN": [("pierce_ep", "==", 0)], "ZONE": [("pierce_ep", ">=", 1)], "NOCONS": [("cons60", "==", 0)], "DENSE": [("zv6h", "==", 0)],
            "SHARP": [("sharp", "==", c.get("X"))]}[c["cid"]]


def enum_tk654(inv, u):
    import tk654_post as p54
    import tk654_run as t4
    root = "/data/p42-tk654"
    for side in SIDES:
        defs = {c["cid"]: c for c in _json(t4.cells_path(side))["cells"]}
        v = p54.variants(side)
        for cid, line in v:
            if cid == p54.CTRL:
                inv.skip("TK-654", "cells54", side, cid, "контроль CTRL (все строки 1,0) = база через путь фильтра; не клетка")
                continue
            inv.add("TK-654", "cells54", side, cid, line, tk654_filt(defs[cid]), lambda w, side=side, cid=cid: "%s/cells54/%s-%s/%s" % (root, w, side, cid), "turbo")
        inv.listed("TK-654", "cells54", side, lambda w, side=side: "%s/cells54/%s-%s" % (root, w, side), [c for c, _ in v])
        for cid, c in defs.items():
            for a in c.get("also", []):
                inv.skip("TK-654", "cells54", side, a, "дубль клетки %s по множеству исключённых строк: движком не считалась (результат тот же, но не посчитан)" % cid)


def tk668_filt(sp, thr):
    """Смысл фильтра клетки TK-668 (`cl.excl_mask` — исключённые строки; фильтр оставляет остальные, пусто проходит)."""
    x = thr[sp["col"]]["%.2f" % sp["q"]] if sp["fam"] == "EP" else sp["x"]
    op = sp["op"]
    if op == "lt":
        return [(sp["col"], ">=", x)]
    if op == "gt":
        return [(sp["col"], "<=", x)]
    if op == "abs":
        return [("abs_" + sp["col"], "<", x)]
    if op == "neg":
        return [(sp["col"], ">", -x)]
    return [(sp["col"], "<", x)]


def enum_tk668(inv, u):
    import tk668_cells as cl
    import tk668_post as p68
    root = "/data/p42-tk668"
    specs = {sp["cid"]: sp for sp in cl.cell_specs()}
    n24 = _json("/data/p42-tk638/res/thr38.json")
    for side in SIDES:
        thr = _json(root + "/res/counts68-%s.json" % side)["thr"]
        for group in ("a", "b", "ref"):
            dd = lambda w, side=side, group=group: "%s/cells-%s/%s-%s" % (root, group, w, side)
            on_disk = set()
            for w in WINS:
                if os.path.isdir(dd(w)):
                    on_disk |= {x for x in os.listdir(dd(w)) if os.path.isdir(os.path.join(dd(w), x))}
            for cid in sorted(on_disk):
                if cid == "ALL1":
                    inv.skip("TK-668", "cells-" + group, side, cid, "контроль ALL1 (все строки 1,0) = база через путь фильтра; не клетка")
                    continue
                if group == "ref":
                    nn = n24[side]["N24"]["q"]["0.60"]
                    base_f = [("n24", ">=", nn)]
                    if cid == "REF":
                        filt = base_f
                    elif cid.startswith("N24_") and cid[4:] in specs:
                        filt = base_f + tk668_filt(specs[cid[4:]], thr)
                    else:
                        inv.skip("TK-668", "cells-ref", side, cid, "нет описания клетки")
                        continue
                elif cid in specs:
                    filt = tk668_filt(specs[cid], thr)
                else:
                    inv.skip("TK-668", "cells-" + group, side, cid, "нет описания клетки")
                    continue
                line = p68.variants(u, side, [cid])[0][1]
                inv.add("TK-668", "cells-" + group, side, cid, line, filt, lambda w, side=side, group=group, cid=cid: "%s/cells-%s/%s-%s/%s" % (root, group, w, side, cid), "turbo")
    # E4: источники уровней `lx`; `lw` — день первого понедельника окна, действует только при lx = 2
    import tk668_e4 as e4
    import tk604_stats as st
    for side in SIDES:
        for lx in (1, 2, 3):
            cid = e4.vid(side, lx)
            if lx == 2:
                inv.skip("TK-668", "e4", side, cid, "lx = 2 (недели): `lw` задан окном (wa 5 …), один ключ клетки на три окна не выражается; план TK-674 строку без `lw` не даёт")
                continue
            lines = {w.name: e4.e4_line(u, side, lx, w) for w in st.TRADE_WINDOWS}
            inv.add("TK-668", "e4", side, cid, lines, [], lambda w, cid=cid: "%s/e4/cells/%s/%s" % (root, w, cid), "lx")
        inv.add("TK-668", "e4-gbyte", side, e4.VID[side], e4.t49.base_line(e4.r5, u, side), [], lambda w, side=side: "%s/e4/gbyte/%s/%s" % (root, w, e4.VID[side]), "lx")


def enum_tk674(inv, u):
    import tk625_run as r5
    import tk643_run as t43
    for side in SIDES:
        inv.add("TK-674", "gbase", side, "base", t43.variant_line(r5, u, side, {}), [], lambda w, side=side: "%s/gbase/%s-%s/base" % (ROOT, w, side), "new")


def enumerate_all():
    u = uni()
    inv = Inv()
    for fn in (enum_tk638, enum_tk643, enum_tk649, enum_tk650, enum_tk652, enum_tk654, enum_tk655, enum_tk657, enum_tk660, enum_tk668, enum_tk674):
        n0 = len(inv.cells)
        fn(inv, u)
        say("%s: клеток %d" % (fn.__name__[5:], len(inv.cells) - n0))
    return inv


# ---- строка метрик клетки ----------------------------------------------------------------------------------------------------------
_W = {}


def _winit():
    import tk604_stats as st
    import tk625_run as r5
    import tk638_post as p8
    _W["st"], _W["r5"], _W["p8"] = st, r5, p8
    _W["u"] = r5.Uni(UNI)


def row_job(c):
    """Клетка → строка БД или причина пропуска: G7 по окнам, P&L сделок = val ряда, строка ↔ params, G-off, G-tape; метрики — `tk674_stats.metrics`."""
    import numpy as np
    import tk674_stats as ms
    st, p8, u = _W["st"], _W["p8"], _W["u"]
    side = c["side"]
    ident = dict(task=c["task"], group=c["group"], side=side, cid=c["cid"])
    try:
        bad = []
        for w in WINS:
            meta = _json(os.path.join(c["dirs"][w], "meta.json"))
            bad += ["%s %s" % (w, x) for x in check_params(c["lines"][w], meta["params"])]
            # семейство сборки: `Debug` turbo (alpha-master) не печатает `tl` (TK-603 добавил позже), у остальных — печатает
            if ("exit.tl" in flat_params(meta["params"])) == (c["bin"] == "turbo"):
                bad.append("%s params %s поле tl: семейство сборки не turbo" % (w, "содержит" if c["bin"] == "turbo" else "не содержит"))
        if bad:
            return dict(skip="строка ↔ params: " + "; ".join(bad[:3]), **ident)
        dirs = {w.name: (c["dirs"][w.name], side) for w in st.TRADE_WINDOWS}
        tables, daily = p8.read_tables(dirs)
        for w in st.TRADE_WINDOWS:
            tb, dl = tables[w.name], daily[w.name]
            tot_d, tot_n = float(sum(dl.values())), float(tb.net.sum())
            if abs(tot_d - tot_n) > 1e-4 + 2e-6 * len(tb.net):
                return dict(skip="G7 %s: Σ daily %+.6f ≠ Σ net %+.6f" % (w.name, tot_d, tot_n), **ident)
        ser = st.splice(tables, u.coins(), daily=None)
        kinds, pnl = p8.kinds_pnl(tables)
        if len(pnl) != len(ser.val) or not np.allclose(pnl, ser.val, atol=1e-6):
            return dict(skip="P&L сделок ≠ val ряда", **ident)
        tmax = 0
        for w in st.TRADE_WINDOWS:
            tb = tables[w.name]
            if len(tb.exit_ms):
                tmax = max(tmax, int(tb.exit_ms.max()) + w.t0_ms)
        line = c["lines"]["wa"]
        kv = ax.parse_line(line)
        m = ms.metrics(ser, kinds, pnl, s_pct=float(kv.get("s", 5)))
        errs = []
        if tmax >= st.LAST_MS:
            errs.append("G-tape: выход позже 13.11")
        kn = m["kinds"]
        for key, kind in (("rf", "rt"), ("tt", "tx"), ("tu", "tu")):
            if not float(kv.get(key, 0)) > 0 and kn.get(kind, 0):
                errs.append("G-off: %s = 0, но выходов %s %d" % (key, kind, kn[kind]))
        if errs:
            return dict(skip="; ".join(errs), **ident)
        ck = ckey_of(line, [tuple(x) for x in c["filt"]])
        sig = hashlib.md5((",".join("%s:%d" % k for k in zip(ser.sym_name, [int(x) for x in ser.key_ms])) + "|" + ",".join("%.5f" % x for x in pnl)).encode()).hexdigest()
        m.update(line=line, filt_sem=c["filt"], dirs=c["dirs"], trades_sig=sig, tmax_ms=tmax, v=1, stake=500,
                 imp=dict(task=c["task"], group=c["group"], cid=c["cid"], bin_key=c["bin"]))
        return dict(ok=True, ckey=ck, m=m, **ident)
    except Exception as e:  # noqa: BLE001
        return dict(skip="ошибка чтения: %s: %s" % (type(e).__name__, str(e)[:200]), **ident)


def _clean(x):
    """JSON без NaN / inf (в БД — null)."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def cmd_scan(a):
    inv = enumerate_all()
    regs = bin_registry()
    for k, d in regs.items():
        say("бинарь %-5s %s md5 %s %s" % (k, "ok " if d["ok"] else "НЕТ", d.get("md5", "-"), d["why"]))
    cells = inv.cells
    skipped = list(inv.skipped)
    keep = []
    for c in cells:
        r = regs[c["bin"]]
        if not r["ok"]:
            skipped.append(dict(task=c["task"], group=c["group"], side=c["side"], cid=c["cid"], why="бинарь %s не установлен: %s" % (c["bin"], r["why"])))
            continue
        old = [w for w in WINS if os.path.getmtime(os.path.join(c["dirs"][w], "meta.json")) < r["mtime"]]
        if old:
            skipped.append(dict(task=c["task"], group=c["group"], side=c["side"], cid=c["cid"], why="клетка (%s) старше файла бинаря %s: счёт мог идти другой сборкой" % (",".join(old), c["bin"])))
            continue
        keep.append(c)
    say("клеток на диске %d, к чтению %d, пропущено до чтения %d" % (len(cells), len(keep), len(skipped)))
    rows, n = [], 0
    with mp.Pool(3, initializer=_winit) as pool:
        for r in pool.imap_unordered(row_job, keep, chunksize=8):
            n += 1
            if n % 200 == 0:
                say("прочитано %d / %d" % (n, len(keep)))
            if "skip" in r:
                skipped.append(dict(task=r["task"], group=r["group"], side=r["side"], cid=r["cid"], why=r["skip"]))
            else:
                rows.append(r)
    # ключ базы стороны — охрана ключей: база стороны из этого импорта == ключ плана
    base_ck = {s: ax.canon(ax.BASE_LINE(s)) for s in SIDES}
    out = []
    for r in sorted(rows, key=lambda r: (r["side"], r["task"], r["group"], r["cid"])):
        m = r["m"]
        label = cell_label(r["ckey"], base_ck[r["side"]])
        m["cid"], m["patch"] = label, diff_patch(r["ckey"], base_ck[r["side"]])
        ptr = "srv:" + re.sub(r"/wa(-[LS])?/", lambda mm: "/w{a,b,c}%s/" % (mm.group(1) or ""), m["dirs"]["wa"])
        out.append(dict(task=r["task"], group=r["group"], cid=r["cid"], bin_key=m["imp"]["bin_key"], row=dict(
            side=SIDENAME[r["side"]], uni=UNI, win=WIN, ckey=r["ckey"], bin=regs[m["imp"]["bin_key"]]["md5"], cell=label, trades=m["n"], usd=_clean(m["usd"]),
            mean_usd=_clean(m["mean_usd"]), sharpe=_clean(m["sharpe"]), extra=_clean(m), src=SRC, src_ptr=ptr)))
    with open(res("import674-rows.jsonl"), "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    json.dump(skipped, open(res("import674-skipped.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    inv_out = dict(bins={k: {x: v for x, v in d.items() if x != "mtime"} for k, d in regs.items()}, on_disk=len(cells), read=len(keep), rows=len(out), skipped=len(skipped))
    json.dump(inv_out, open(res("import674-inventory.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    report(out, skipped, regs)
    # база: ключ из импорта = ключ плана (G-key)
    seen_base = {(o["row"]["side"], o["row"]["ckey"]) for o in out if o["row"]["cell"] == "base"}
    for s in SIDES:
        say("G-key база %s: %s" % (s, "ok" if (SIDENAME[s], base_ck[s]) in seen_base else "ОШИБКА — базы стороны с ключом плана нет среди строк"))


def report(out, skipped, regs):
    from collections import Counter
    c = Counter((o["task"], o["row"]["side"], o["bin_key"]) for o in out)
    say("строк к записи %d:" % len(out))
    for (t, s, b), n in sorted(c.items()):
        say("   %-7s %-5s %-5s %d" % (t, s, b, n))
    sk = Counter((s["task"], s["why"].split(":")[0][:60]) for s in skipped)
    say("пропущено %d:" % len(skipped))
    for (t, w), n in sorted(sk.items()):
        say("   %-7s %-60s %d" % (t, w, n))


def load_rows():
    return [json.loads(ln) for ln in open(res("import674-rows.jsonl"), encoding="utf-8")]


def dedupe(rows):
    """Группы (сторона, ключ, бинарь): одна строка в БД; расхождение сделок / Σ $ внутри группы и между бинарями одного ключа — в отчёт (пара задач)."""
    grp, x = {}, {}
    for o in rows:
        r = o["row"]
        grp.setdefault((r["side"], r["ckey"], r["bin"]), []).append(o)
        x.setdefault((r["side"], r["ckey"]), []).append(o)
    same_bin = []
    for k, g in grp.items():
        if len(g) > 1:
            a = g[0]
            for b in g[1:]:
                same_bin.append(dict(side=k[0], ckey=k[1], bin=k[2][:8], a="%s/%s" % (a["task"], a["cid"]), b="%s/%s" % (b["task"], b["cid"]),
                                     trades=(a["row"]["trades"], b["row"]["trades"]), usd=(a["row"]["usd"], b["row"]["usd"]),
                                     sig_equal=a["row"]["extra"]["trades_sig"] == b["row"]["extra"]["trades_sig"]))
    cross = []
    for k, g in x.items():
        bins = {}
        for o in g:
            bins.setdefault(o["row"]["bin"], o)
        if len(bins) > 1:
            first = list(bins.values())
            for b in first[1:]:
                cross.append(dict(side=k[0], ckey=k[1], a="%s/%s@%s" % (first[0]["task"], first[0]["cid"], first[0]["row"]["bin"][:8]),
                                  b="%s/%s@%s" % (b["task"], b["cid"], b["row"]["bin"][:8]), sig_equal=first[0]["row"]["extra"]["trades_sig"] == b["row"]["extra"]["trades_sig"]))
    return [g[0] for g in grp.values()], same_bin, cross


def chron_store(ro=False):
    sys.path.insert(0, "/data/chron")
    import chron
    return chron.Store(chron.connect(chron.SRV_DB, ro=ro))


def cmd_put(a):
    rows = load_rows()
    uniq, same_bin, cross = dedupe(rows)
    bad = [d for d in same_bin if not (d["sig_equal"] and d["trades"][0] == d["trades"][1])]
    say("строк %d, разных (сторона, ключ, бинарь) %d; повторов внутри бинаря %d (расхождений %d); пар одного ключа между бинарями %d (сделки не равны: %d)" % (
        len(rows), len(uniq), len(same_bin), len(bad), len(cross), sum(1 for d in cross if not d["sig_equal"])))
    json.dump(dict(same_bin=same_bin, cross=cross), open(res("import674-dups.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for d in bad[:20]:
        say("РАСХОЖДЕНИЕ", d)
    if bad:
        raise SystemExit("расхождения внутри одного бинаря — запись остановлена")
    store = chron_store()
    new = same = diff = 0
    batch = []
    for o in uniq:
        batch.append(o["row"])
        if len(batch) >= BATCH:
            r = store.cells_put(a.entry, batch, author="TK-674")
            new, same, diff = new + r["new"], same + r["same"], diff + r["diff"]
            batch = []
    if batch:
        r = store.cells_put(a.entry, batch, author="TK-674")
        new, same, diff = new + r["new"], same + r["same"], diff + r["diff"]
    say("cells_put: новых %d, повторов %d, расхождений %d" % (new, same, diff))


# ---- выборочная проверка новым бинарём ----------------------------------------------------------------------------------------------
def pick_spot(rows):
    """3 клетки: (1) TK-643 шорт, самая «толстая» заплатка выхода (старый бинарь turbo); (2) TK-654 лонг, фильтр TPM60 ≤ q60 (turbo, путь `--ftab`); (3) TK-652 лонг, трейл `tl` (alpha-mk)."""
    def find(task, side, cid):
        for o in rows:
            if o["task"] == task and o["row"]["side"] == SIDENAME[side] and o["cid"] == cid:
                return o
        return None
    s643 = [o for o in rows if o["task"] == "TK-643" and o["row"]["side"] == "short" and o["cid"] != "base" and o["row"]["trades"] > 0]
    a = max(s643, key=lambda o: (len(o["row"]["extra"]["patch"]), o["row"]["trades"])) if s643 else None
    b = find("TK-654", "L", "TPM60_le_q60") or find("TK-654", "L", "CN60_le_q60")
    c = find("TK-652", "L", "tl2_rr2") or find("TK-652", "L", "tl1_rr2")
    return [x for x in (a, b, c) if x]


def cmd_spot(a):
    import tk604_run as r4
    import tk625_run as r5
    import tk604_stats as st
    rows = load_rows()
    regs = bin_registry()
    if not regs["new"]["ok"]:
        raise SystemExit("новый бинарь не установлен: " + regs["new"]["why"])
    r4.BIN = BINS["new"]["path"]
    u = r5.Uni(UNI)
    w = st.TRADE_WINDOWS[0]
    out_all, proven = [], {}
    for i, o in enumerate(pick_spot(rows), 1):
        side = "L" if o["row"]["side"] == "long" else "S"
        eng = SIDENAME[side]
        line = o["row"]["extra"]["line"]
        old_dir = o["row"]["extra"]["dirs"][w.name]
        outd = os.path.join(ROOT, "probe", "spot%d-%s" % (i, w.name))
        os.makedirs(outd, exist_ok=True)
        ftab = None
        if "fa=" in line:
            ftab = {"TK-654": "/data/p42-tk654/ftab54r/%s-%s.csv" % (w.name, side)}.get(o["task"])
        cid = o["cid"]
        t0 = time.time()
        r5.tick(u, w, [(cid, line)], outd, {"P42_SIDES": eng}, os.path.join(ROOT, "log", "spot%d-%s.log" % (i, w.name)), ftab=ftab)
        files, same = {}, True
        for f in ("trades.csv", "daily.csv", "coinday.csv", "entryrej.csv"):
            p1, p2 = os.path.join(outd, cid, "%s-%s" % (eng, f)), os.path.join(old_dir, "%s-%s" % (eng, f))
            eq = os.path.exists(p1) and os.path.exists(p2) and open(p1, "rb").read() == open(p2, "rb").read()
            files[f] = eq
            same &= eq
        n_new = sum(1 for _ in open(os.path.join(outd, cid, "%s-trades.csv" % eng), "rb")) - 1 if os.path.exists(os.path.join(outd, cid, "%s-trades.csv" % eng)) else None
        rec = dict(n=i, task=o["task"], side=o["row"]["side"], cid=cid, old_bin=o["bin_key"], old_md5=o["row"]["bin"], line=line, ftab=ftab, window=w.name, files=files,
                   equal=same, trades_new=n_new, trades_old=o["row"]["trades"], sec=round(time.time() - t0, 1))
        out_all.append(rec)
        say("spot%d %s %s %s [%s]: %s (сделок окна wa новых %s)" % (i, o["task"], o["row"]["side"], cid, o["bin_key"], "байт в байт" if same else "РАЗЛИЧАЕТСЯ " + str(files), n_new))
        proven.setdefault(o["row"]["bin"], []).append(same)
    eq = [regs["new"]["md5"]] + [b for b, v in proven.items() if v and all(v)]
    json.dump(out_all, open(res("spot674.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(eq, open(res("eqbins674.json"), "w", encoding="utf-8"))
    say("eqbins674: %s; непроверенные бинари: %s" % (eq, sorted(set(regs[k]["md5"] for k in regs if regs[k]["ok"]) - set(eq))))


# ---- счёт ----------------------------------------------------------------------------------------------------------------------------
def cmd_count(a):
    import tk674_run as run
    store = chron_store(ro=True)
    cnt = store.cells_count()
    say("cells_count (сторона, вселенная, окно, бинарь → клеток):")
    for r in cnt:
        say("   %-5s %s %s %s %d" % (r["side"], r["uni"], r["win"], r["bin"][:8], r["n"]))
    eq = run.eq_bins()
    say("eqbins для поиска: %s" % [b[:8] for b in eq])
    thr_ok = os.path.exists(run.thr_path())
    for s in SIDES:
        cells, grids, skipped, bad, found = run.plan_side(s, store)
        cls = {}
        for k, c in cells.items():
            n = "фильтр" if c["filt"] else ("сигналы" if c["sig"] else "выход")
            cls.setdefault(n, [0, 0])[0] += 1
            cls[n][1] += k in found
        say("== %s: пар %d (пропущено %d), клеток плана %s %d, найдено %d (%.1f %%)%s" % (
            s, len(grids), len(skipped), "без фильтровых (res/thr674.json нет)" if not thr_ok else "со всеми", len(cells), len(found), 100.0 * len(found) / max(1, len(cells)),
            "" if thr_ok else "; клетки с фильтром не считались"))
        say("   ключей импорта (сторона %s, бинари eqbins) в плане: %d из %d" % (s, len(found), len({o["row"]["ckey"] for o in load_rows() if o["row"]["side"] == SIDENAME[s] and o["row"]["bin"] in eq})))
        for n, (t_, f_) in sorted(cls.items()):
            say("   класс %-8s клеток %d, найдено %d" % (n, t_, f_))


COMMANDS = {"scan": cmd_scan, "put": cmd_put, "spot": cmd_spot, "count": cmd_count}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--entry", help="запись летописи L-N (стадия put)")
    a = ap.parse_args()
    if a.cmd == "put" and not a.entry:
        raise SystemExit("--entry L-N")
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
