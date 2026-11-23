#!/usr/bin/env python3
"""TK-689: описательный разбор влияния осей на прибыль и убыток ПО УРОВНЯМ (соло / пары / тройки / четвёрки …) по готовым клеткам
БД летописи (c539, окно 01.07–13.11, ставка 500 $). Движок не запускается. Правило — `kb/task/TK-689.md` (записано до чтения ранжирования).

Ось = семейство ключей движка (RULES) или один ключ или столбец фильтра; клетка = набор активных осей против якоря стороны (L-334);
уровень k = число осей. Эффект оси a на уровне k: Δ = метрика(C) − метрика(C∖a) по клеткам C с осью a (опора C∖a — в БД; k = 1 — якорь).
Уровни не усредняются. Запуск (srv, `systemd-run`, ≤ 2 ядра, MemoryMax 4G, nice):
    python3 tk689_influence.py compute --db /data/chron/chron.sqlite --out DIR [--levels 1,2,3,4] [--code /data/p42-tk674c/code] [--no-trades]
Отчёт и графики — `tk689_report.py`."""
import argparse
import itertools
import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tk674_axes import DEFAULTS  # noqa: E402  значения движка по умолчанию: ключ с таким значением в канонический ключ не входит

UNI, WIN, STAKE = "c539", "01.07–13.11", 500.0
ANCHOR_ENTRY = 334
COMPLETE_ENTRIES = (333, 334, 336, 337)       # полные сетки: наборы единичек TK-680 и карта пар TK-674; остальные записи — отбор по результату
THR_MANY, THR_LITTLE = 0.30, 0.05              # размер r: «много» ≥ 0,30; «мало» ≥ 0,05; иначе «ничего»
SIGN_STABLE, MONTHS_STABLE = 0.75, 4
OVERLAP_EDGE, CORR_EDGE, MIN_AFFECTED = 0.5, 0.7, 10
NULL_B, NULL_SEED, NULL_MIN_SHARE = 1000, 689, 0.5
BOOT_B, BOOT_BLOCK, BOOT_SEED = 20000, 5, 674
METRICS = ("n", "usd", "mean", "sh", "dd", "ddp", "gap", "rr")

# (ось, ведущие ключи, спутники) — порядок важен: e2 сначала забирают e1t / xb (они его обнуляют), остаток e2 — split
RULES = [
    ("e1t", {"e1t"}, {"e2"}), ("xb", {"xb"}, {"e2"}), ("split", {"e2", "e2t", "w2", "s2"}, set()), ("ev", {"ev", "ea", "el", "eh"}, set()),
    ("nf", {"nf"}, {"natr_min"}), ("sn", {"sn", "sg", "sb", "sd", "sr"}, {"sm"}), ("chan", {"ch", "cl", "cm", "cw"}, {"sm"}),
    ("sl", {"sl"}, {"sm"}), ("nx", {"nx"}, {"sm"}), ("rf", {"rf"}, {"rs"}), ("tt", {"tt"}, {"tb"}),
]
DROPPED_COMPANION = ("rf", "tt")               # rs / tb спутник, только если в клетке ключа нет (canon убирает rs без rf, tb без tt)
FILT_RE = re.compile(r"^([^<>=]+)(<=|>=|==|<|>)(.*)$")


# ---- оси и уровни ------------------------------------------------------------------------------------------------------------------
def parse_ckey(ck):
    keys, filt = {}, []
    for t in ck.split():
        if t.startswith("F="):
            filt = [x for x in t[2:].split("&") if x]
        elif "=" in t:
            k, v = t.split("=", 1)
            keys[k] = v
    return keys, filt


def eff(keys, k):
    """Действующее значение ключа: нет в ключе клетки — значение по умолчанию движка (число в виде строки «%g»)."""
    if k in keys:
        return keys[k]
    return "%g" % DEFAULTS[k] if k in DEFAULTS else None


def assign_axes(keys, filt, akeys):
    """{ось: метка значения}: ключи, отличающиеся от якоря, сведены в оси по RULES; столбец фильтра — ось «F:<столбец>»."""
    left = {k for k in set(keys) | set(akeys) if eff(keys, k) != eff(akeys, k)}
    groups = {}
    for name, leads, absorbs in RULES:
        hit = leads & left
        if not hit:
            continue
        claimed = set(hit)
        for k in absorbs & left:
            if name in DROPPED_COMPANION and k in keys:
                continue
            claimed.add(k)
        groups[name] = claimed
        left -= claimed
    for k in sorted(left):
        groups[k] = {k}
    out = {}
    for name, ks in groups.items():
        out[name] = eff(keys, next(iter(ks))) if len(ks) == 1 and name == next(iter(ks)) else ",".join("%s=%s" % (k, eff(keys, k)) for k in sorted(ks))
    for f in filt:
        m = FILT_RE.match(f)
        out["F:" + (m.group(1) if m else f)] = (m.group(2) + m.group(3)) if m else ""
    return out


def excluded(keys):
    """Причина исключения клетки из эффектов (поправка 4 критика): стоп 5 % (В-173), вход минутной свечой ew = 0 (В-172)."""
    if eff(keys, "ew") == "0":
        return "ew0"
    if eff(keys, "s") == "5":
        return "s5_split" if float(eff(keys, "e2t") or 0) > 0 else "s5"
    return None


def label_num(label):
    m = re.search(r"-?\d+(?:\.\d+)?", label or "")
    return float(m.group(0)) if m else float("inf")


def sig_of(axes):
    return frozenset(axes.items())


# ---- загрузка клеток ---------------------------------------------------------------------------------------------------------------
def cell_from_row(r):
    x = json.loads(r["extra"]) if r["extra"] else {}
    keys, filt = parse_ckey(r["ckey"])
    return dict(id=r["id"], entry=r["entry"], side=r["side"], cell=r["cell"], ckey=r["ckey"], bin=(r["bin"] or "")[:8], keys=keys, filt=filt,
                n=r["trades"], usd=r["usd"], mean=r["mean_usd"], sh=r["sharpe"], dd=x.get("maxdd"), ddp=x.get("maxdd_pct"),
                gap=(x.get("kpi") or {}).get("gap_longest"), rr=(x.get("rr") or {}).get("rr"), months=x.get("months"),
                daily=np.array(x["daily"], dtype=np.float64) if x.get("daily") else None, tsig=x.get("trades_sig"), dirs=x.get("dirs"))


def load_rows(db, uni=UNI, win=WIN):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM metric_cur WHERE ckey IS NOT NULL AND uni=? AND win=? ORDER BY id", (uni, win)).fetchall()
    out = [cell_from_row(r) for r in rows]
    m = {}
    for side in ("long", "short"):
        m[side] = c.execute("SELECT count(DISTINCT ckey) FROM metric_cur WHERE side=? AND uni IN ('c539','h775') AND ckey IS NOT NULL", (side,)).fetchone()[0]
    c.close()
    return out, m


def prepare(cells):
    """По сторонам: якорь, клетки уровня k ≥ 1 (без исключённых), индекс по набору осей; счётчики исключений и повторов ключа."""
    sides, stat = {}, dict(excluded=defaultdict(int), dup_keys=0, dup_diff=0)
    for side in ("long", "short"):
        mine = [c for c in cells if c["side"] == side]
        anc = [c for c in mine if c["entry"] == ANCHOR_ENTRY]
        if not anc:
            continue
        a = anc[0]
        seen, cl = {}, []
        for c in mine:
            if c["ckey"] in seen:
                stat["dup_keys"] += 1
                o = seen[c["ckey"]]
                if o["n"] != c["n"] or abs((o["usd"] or 0) - (c["usd"] or 0)) > 1e-6 * max(1.0, abs(o["usd"] or 0)):
                    stat["dup_diff"] += 1
                continue
            seen[c["ckey"]] = c
            c["axes"] = assign_axes(c["keys"], c["filt"], a["keys"])
            c["k"] = len(c["axes"])
            c["sig"] = sig_of(c["axes"])
            c["grid"] = "полная" if c["entry"] in COMPLETE_ENTRIES else "отбор"
            why = excluded(c["keys"]) if c["k"] else None
            c["excl"] = why
            if why:
                stat["excluded"][(side, why)] += 1
            cl.append(c)
        idx = {}
        for c in cl:
            if not c["excl"] and c["sig"] not in idx:
                idx[c["sig"]] = c
        sides[side] = dict(anchor=a, cells=[c for c in cl if not c["excl"]], idx=idx)
    return sides, stat


# ---- эффекты по осям --------------------------------------------------------------------------------------------------------------
def observations(sd, k):
    """[(ось, метка, C, опора)] уровня k; опора C∖a — клетка с тем же набором осей без a (k = 1 — якорь); без опоры в БД — в missing."""
    obs, missing = [], defaultdict(int)
    for c in sd["cells"]:
        if c["k"] != k:
            continue
        for a, lab in c["axes"].items():
            if k == 1:
                ref = sd["anchor"]
            else:
                ref = sd["idx"].get(c["sig"] - {(a, lab)})
            if ref is None:
                missing[a] += 1
                continue
            obs.append((a, lab, c, ref))
    return obs, missing


def delta(c, r):
    d = {m: (None if c[m] is None or r[m] is None else c[m] - r[m]) for m in METRICS}
    if c["months"] and r["months"] and len(c["months"]) == len(r["months"]):
        d["mon"] = [cm[1] - rm[1] for cm, rm in zip(c["months"], r["months"])]
    return d


def collapse(items):
    """Поправка 3: одинаковые (trades_sig клетки, trades_sig опоры) — одно наблюдение."""
    seen, out = set(), []
    for it in items:
        key = (it[2]["tsig"], it[3]["tsig"])
        if key[0] is None or key not in seen:
            seen.add(key)
            out.append(it)
    return out


def group_of(r):
    return "много" if r >= THR_MANY else ("мало" if r >= THR_LITTLE else "ничего")


def noise_of(vals, m):
    v = np.array([x for x in vals if x is not None and x == x], dtype=np.float64)
    if len(v) < 2 or m < 2:
        return None, None
    s = float(v.std())
    return s, s * math.sqrt(2 * math.log(m))


def axis_stats(items, anc, noise_sh, noise_usd, raw_n):
    """Сводка оси на уровне по наблюдениям (после collapse): средние Δ, разброс, размер r, группа, флаги устойчивости и шума."""
    n = len(items)
    D = [delta(c, r) for _, _, c, r in items]
    out = dict(n=n, n_raw=raw_n, dead=all(c["tsig"] is not None and c["tsig"] == r["tsig"] for _, _, c, r in items))
    for m in METRICS:
        v = [d[m] for d in D if d[m] is not None]
        out["d_" + m] = dict(mean=float(np.mean(v)), min=float(np.min(v)), max=float(np.max(v))) if v else None
    dusd = np.array([d["usd"] for d in D if d["usd"] is not None])
    ddd = np.array([d["dd"] for d in D if d["dd"] is not None])
    out["rP"] = float(np.abs(dusd).mean() / abs(anc["usd"])) if len(dusd) else None
    out["rD"] = float(np.abs(ddd).mean() / anc["dd"]) if len(ddd) else None
    r = max(x for x in (out["rP"], out["rD"]) if x is not None) if len(dusd) or len(ddd) else None
    out["r"] = r
    if n < 2:
        out["group"] = out["gP"] = out["gD"] = "не оценена"
    else:
        out["group"] = "ничего" if out["dead"] else group_of(r)
        out["gP"] = "ничего" if out["dead"] else group_of(out["rP"] or 0.0)
        out["gD"] = "ничего" if out["dead"] else group_of(out["rD"] or 0.0)
    if len(dusd):
        pos, neg = float((dusd > 0).mean()), float((dusd < 0).mean())
        out["share_pos"] = pos
        out["sign_stable"] = max(pos, neg) >= SIGN_STABLE
    mon = [d["mon"] for d in D if "mon" in d]
    if mon:
        mm = np.array(mon).mean(axis=0)
        tot = float(mm.sum())
        out["months_same_sign"] = int(sum(1 for x in mm if x * tot > 0))
        out["months_stable"] = out["months_same_sign"] >= MONTHS_STABLE
        out["d_months"] = [float(x) for x in mm]
    sh = [abs(d["sh"]) for d in D if d["sh"] is not None]
    out["max_abs_dsh"] = float(max(sh)) if sh else None
    out["above_noise_sh"] = bool(sh and noise_sh is not None and max(sh) > noise_sh)
    out["max_abs_dusd"] = float(np.abs(dusd).max()) if len(dusd) else None
    out["above_noise_usd"] = bool(len(dusd) and noise_usd is not None and np.abs(dusd).max() > noise_usd)
    dn = [d["n"] for d in D if d["n"] is not None]
    out["share_removing"] = float(np.mean([x < 0 for x in dn])) if dn else None
    return out


def direction(c, r):
    return "-" if c["n"] < r["n"] else ("+" if c["n"] > r["n"] else "0")


# ---- нулевая модель (поправка 1) ---------------------------------------------------------------------------------------------------
def dd_batch(D):
    """Просадка по рядам суточных P&L (B × дней): max(пик − накопленное), пик от 0 — как maxdd клеток."""
    cs = np.cumsum(D, axis=1)
    peak = np.maximum(np.maximum.accumulate(cs, axis=1), 0.0)
    return (peak - cs).max(axis=1)


def relation(ck, cn, rk, rn):
    """'same' — те же сделки с тем же P&L; 'selection' — P&L общих сделок совпадает, наборы различаются (отбор: сделки убраны и, из-за занятой монеты,
    часть добавлена); 'other' — P&L общих сделок меняется (выход / вход другой). ck / rk — отсортированные уникальные ключи."""
    common, i1, i2 = np.intersect1d(ck, rk, assume_unique=True, return_indices=True)
    if len(common) and not np.allclose(cn[i1], rn[i2], atol=1e-6):
        return "other"
    return "same" if len(common) == len(ck) == len(rk) else "selection"


def null_draw(keys_large, net_large, day_large, m, rng, B, ndays):
    """Δ Σ $ и Δ просадки у большего множества сделок с m = |n_C − n_R| случайно убранными (B розыгрышей без возвращения): векторы |Δ$|, |Δdd|."""
    n = len(net_large)
    dl = np.bincount(day_large, weights=net_large, minlength=ndays)[:ndays]
    dd0 = float(dd_batch(dl[None, :])[0])
    if m <= 0 or m >= n:
        z = np.zeros(B)
        return z, z.copy()
    idx = np.argpartition(rng.random((B, n)), m - 1, axis=1)[:, :m]
    removed = np.take(net_large, idx)
    onehot = np.zeros((n, ndays))
    onehot[np.arange(n), np.minimum(day_large, ndays - 1)] = 1.0
    dsum = removed.sum(axis=1)
    rem_day = np.zeros((B, n))
    np.put_along_axis(rem_day, idx, removed, axis=1)
    dkeep = dl[None, :] - rem_day @ onehot
    return np.abs(dsum), np.abs(dd_batch(dkeep) - dd0)


# ---- пересечение сделок и кластеры ------------------------------------------------------------------------------------------------
def affected(ck, cn, ak, an):
    """Сделки, затронутые против якоря: есть только у одного из них или P&L отличается (ключи отсортированы, уникальны)."""
    inter, i1, i2 = np.intersect1d(ck, ak, assume_unique=True, return_indices=True)
    diff_pnl = inter[np.abs(cn[i1] - an[i2]) > 1e-6]
    only = np.setxor1d(ck, ak, assume_unique=True)
    return np.union1d(only, diff_pnl)


def overlap(a, b):
    if len(a) == 0 or len(b) == 0:
        return 0.0
    return len(np.intersect1d(a, b, assume_unique=True)) / min(len(a), len(b))


def components(nodes, edges):
    par = {x: x for x in nodes}

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for a, b in edges:
        par[find(a)] = find(b)
    g = defaultdict(list)
    for x in nodes:
        g[find(x)].append(x)
    return [sorted(v) for v in g.values() if len(v) >= 2]


# ---- взаимодействия порядка k ------------------------------------------------------------------------------------------------------
def boot_idx(n, B=BOOT_B, block=BOOT_BLOCK, seed=BOOT_SEED):
    """Индексы блочного бутстрепа по суткам — как `tk674_run.synergy` (один и тот же набор на все клетки)."""
    nb = -(-n // block)
    rng = np.random.default_rng(seed)
    idx = (rng.integers(0, n, size=(B, nb))[:, :, None] + np.arange(block)) % n
    return idx.reshape(B, -1)[:, :n]


def interaction(c, sd, idx_boot=None):
    """Чистая интерактивность порядка k: Σ_{T⊆S} (−1)^{k−|T|} y(T), T = ∅ — якорь; нужны все подклетки. None, если какой-то нет."""
    items = sorted(c["axes"].items())
    k = len(items)
    usd, daily = 0.0, None
    for r in range(k + 1):
        sign = -1 if (k - r) % 2 else 1
        for T in itertools.combinations(items, r):
            cell = sd["anchor"] if r == 0 else (c if r == k else sd["idx"].get(frozenset(T)))
            if cell is None or cell["usd"] is None or cell["daily"] is None:
                return None
            usd += sign * cell["usd"]
            daily = sign * cell["daily"] if daily is None else daily + sign * cell["daily"]
    out = dict(usd=float(usd), daily_sum=float(daily.sum()))
    if idx_boot is not None and len(daily) == idx_boot.shape[1]:
        sums = daily[idx_boot].sum(axis=1)
        out.update(lo=float(np.quantile(sums, 0.025)), hi=float(np.quantile(sums, 0.975)), p_le0=float((sums <= 0).mean()))
    return out


# ---- сделки клеток (srv, код TK-673) -----------------------------------------------------------------------------------------------
class Trades:
    """Сделки клетки за всё окно: ключи (монета, сигнал) отсортированы, P&L, сутки; склейка окон — как `tk674_run._row_job`."""

    def __init__(self, code):
        sys.path.insert(0, code)
        import tk604_stats as st
        import tk625_run as r5
        import tk638_post as p8
        self.st, self.p8 = st, p8
        self.coins = {c: i for i, c in enumerate(r5.Uni("h775").coins())}
        self.cache = {}

    def get(self, cell, side):
        k = cell["ckey"]
        if k in self.cache:
            return self.cache[k]
        sd = "L" if side == "long" else "S"
        dirs = {w: (cell["dirs"][w], sd) for w in ("wa", "wb", "wc")}
        tables, _ = self.p8.read_tables(dirs)
        ser = self.st.splice(tables, list(self.coins), daily=None)
        keys = np.array([self.coins[s] for s in ser.sym_name], dtype=np.int64) * (1 << 41) + np.asarray(ser.key_ms, dtype=np.int64)
        o = np.argsort(keys, kind="stable")
        res = (keys[o], np.asarray(ser.val, dtype=np.float64)[o], np.asarray(ser.day, dtype=np.int64)[o])
        self.cache[k] = res
        return res


# ---- расчёт уровня ----------------------------------------------------------------------------------------------------------------
def compute_level(side, sd, k, mcount, tr=None, do_null=True, boot=True):
    obs, missing = observations(sd, k)
    anc = sd["anchor"]
    cells_k = [c for c in sd["cells"] if c["k"] == k]
    s_sh, noise_sh = noise_of([c["sh"] for c in cells_k], mcount)
    s_usd, noise_usd = noise_of([c["usd"] for c in cells_k], mcount)
    by = defaultdict(list)
    for o in obs:
        by[(o[0], o[2]["grid"])].append(o)
    rng = np.random.default_rng(NULL_SEED)
    axes_out, obs_out = [], []
    for (a, grid), items in sorted(by.items()):
        items = sorted(items, key=lambda o: o[2]["ckey"])
        uniq = collapse(items)
        st = axis_stats(uniq, anc, noise_sh, noise_usd, len(items))
        st.update(axis=a, grid=grid, side=side, k=k, missing=missing.get(a, 0))
        dirn = defaultdict(int)
        for _, lab, c, r in uniq:
            dirn[direction(c, r)] += 1
        st["dirs"] = dict(dirn)
        if tr is not None and do_null and st["n"] >= 2 and not st["dead"]:
            sums = np.zeros(NULL_B), np.zeros(NULL_B)
            cnt = 0
            for _, lab, c, r in uniq:
                ck, cn, cd = tr.get(c, side)
                rk, rn, rd = tr.get(r, side)
                rel = relation(ck, cn, rk, rn)
                if rel != "selection":
                    continue
                big = (ck, cn, cd) if len(ck) > len(rk) else (rk, rn, rd)
                m = abs(len(ck) - len(rk))
                nd = len(anc["daily"])
                dz, dd = null_draw(big[0], big[1], big[2], m, rng, NULL_B, nd)
                sums = sums[0] + dz, sums[1] + dd
                cnt += 1
            st["null_cells"] = cnt
            st["null_applicable"] = cnt / st["n"] >= NULL_MIN_SHARE
            if cnt:
                rb = np.maximum(sums[0] / cnt / abs(anc["usd"]), sums[1] / cnt / anc["dd"])
                st["r_null"] = float(rb.mean())
                st["r_null_p95"] = float(np.quantile(rb, 0.95))
                st["not_distinguishable_from_random"] = bool(st["null_applicable"] and st["r"] is not None and st["r"] <= st["r_null_p95"])
        axes_out.append(st)
        kept = {id(o[2]) for o in uniq}
        for _, lab, c, r in items:
            d = delta(c, r)
            obs_out.append(dict(axis=a, label=lab, grid=grid, cell=c["ckey"], ref=r["ckey"], dir=direction(c, r), dup=id(c) not in kept,
                                others={x: y for x, y in c["axes"].items() if x != a}, **{"c_" + m: c[m] for m in METRICS},
                                **{"r_" + m: r[m] for m in METRICS}, **{"d_" + m: d[m] for m in METRICS}))
    res = dict(side=side, k=k, n_cells=len(cells_k), by_grid={g: sum(1 for c in cells_k if c["grid"] == g) for g in ("полная", "отбор")},
               sigma_sh=s_sh, noise_sh=noise_sh, sigma_usd=s_usd, noise_usd=noise_usd, m=mcount, axes=axes_out, missing=dict(missing))
    ints = []
    if k >= 2:
        ib = boot_idx(len(anc["daily"])) if boot else None
        for c in cells_k:
            it = interaction(c, sd, ib)
            if it is not None:
                ints.append(dict(ckey=c["ckey"], cell=c["cell"], axes=c["axes"], grid=c["grid"], tsig=c["tsig"], n=c["n"], usd_cell=c["usd"], **it))
    res["interactions"] = ints
    return res, obs_out


def level1_clusters(sd, side, tr, anc_axes_stats):
    """Уровень 1: пересечение затронутых сделок осей (среднее по парам значений |A∩B| / min), ребро ≥ OVERLAP_EDGE, среди осей не «ничего»."""
    ak, an, ad = tr.get(sd["anchor"], side)
    sets = defaultdict(list)
    for c in sd["cells"]:
        if c["k"] != 1 or len(c["axes"]) != 1:
            continue
        a = next(iter(c["axes"]))
        ck, cn, cd = tr.get(c, side)
        sets[a].append(affected(ck, cn, ak, an))
    ok = {a for a, st in anc_axes_stats.items() if st["group"] in ("много", "мало") and a in sets}
    ok = {a for a in ok if np.mean([len(x) for x in sets[a]]) >= MIN_AFFECTED}
    names = sorted(ok)
    M = {}
    for a, b in itertools.combinations(names, 2):
        M[(a, b)] = float(np.mean([overlap(x, y) for x in sets[a] for y in sets[b]]))
    edges = [p for p, v in M.items() if v >= OVERLAP_EDGE]
    return dict(names=names, overlap={"%s|%s" % p: v for p, v in M.items() if v > 0.1}, edges=["%s|%s" % e for e in edges], families=components(names, edges),
                excluded_small=sorted(a for a in anc_axes_stats if a in sets and a not in ok and anc_axes_stats[a]["group"] in ("много", "мало")))


def proxy_clusters(sd, k, obs_ref):
    """Уровни ≥ 2 (приближение по дневным Δ): узел = (ось, направление Δ сделок); ряд = среднее Δ по суткам по клеткам узла; ребро при корреляции ≥ CORR_EDGE."""
    ser = defaultdict(list)
    for a, lab, c, r in obs_ref:
        if c["daily"] is None or r["daily"] is None:
            continue
        ser[(a, direction(c, r))].append(c["daily"] - r["daily"])
    nodes = {nd: np.mean(v, axis=0) for nd, v in ser.items() if len(v) >= 2}
    names = sorted(nodes)
    edges = []
    for x, y in itertools.combinations(names, 2):
        if x[0] == y[0]:
            continue
        sx, sy = nodes[x], nodes[y]
        if sx.std() > 0 and sy.std() > 0 and np.corrcoef(sx, sy)[0, 1] >= CORR_EDGE:
            edges.append((x, y))
    comp = components(names, edges)
    fam = []
    for cmp_ in comp:
        axes = sorted({a for a, _ in cmp_})
        if len(axes) >= 2:
            fam.append(axes)
    return dict(nodes=len(names), edges=["%s/%s|%s/%s" % (x + y) for x, y in edges], families=fam)


def agreement(fa, fb, names):
    """Согласие двух разбиений на семьи: доля пар осей из `names`, у которых совпадает ответ «в одной семье / нет» (индекс Рэнда); пар, где семья есть хоть в одном, — отдельно."""
    def fam(fs):
        m = {}
        for i, g in enumerate(fs):
            for a in g:
                m[a] = i
        return m
    ma, mb = fam(fa), fam(fb)
    same = tot = both = either = 0
    for x, y in itertools.combinations(names, 2):
        a = x in ma and y in ma and ma[x] == ma[y]
        b = x in mb and y in mb and mb[x] == mb[y]
        tot += 1
        same += a == b
        either += a or b
        both += a and b
    return dict(pairs=tot, same=same, rand=same / tot if tot else None, pairs_in_family_either=either, pairs_in_family_both=both)


def noise_check(db):
    """Сверка метода шума с L-349: σ Шарпа клеток записи 337 × √(2 ln m), m — различные ключи стороны под c539 + h775 (L: 2,03 → 9,39; шорт 1,57 → 7,36 при m на тот момент)."""
    cells, m = load_rows(db)
    out = {}
    for side in ("long", "short"):
        for e in (333, 336, 337):
            v = [c["sh"] for c in cells if c["side"] == side and c["entry"] == e]
            out["%s:%d" % (side, e)] = dict(n=len(v), m=m[side], sigma_noise=noise_of(v, m[side]))
    return out


def run(db, out, levels, code=None, do_trades=True, do_null=True):
    os.makedirs(out, exist_ok=True)
    cells, mcount = load_rows(db)
    sides, stat = prepare(cells)
    meta = dict(db=db, uni=UNI, win=WIN, m=mcount, levels_in_db={}, excluded={"%s:%s" % k: v for k, v in stat["excluded"].items()}, dup_keys=stat["dup_keys"],
                dup_diff=stat["dup_diff"], anchors={s: dict(ckey=sd["anchor"]["ckey"], n=sd["anchor"]["n"], usd=sd["anchor"]["usd"], dd=sd["anchor"]["dd"],
                                                            sh=sd["anchor"]["sh"], gap=sd["anchor"]["gap"]) for s, sd in sides.items()},
                thresholds=dict(many=THR_MANY, little=THR_LITTLE), bins=sorted({c["bin"] for c in cells}), entries=sorted({c["entry"] for c in cells}))
    tr = Trades(code) if (do_trades and code) else None
    for side, sd in sides.items():
        meta["levels_in_db"][side] = {str(k): sum(1 for c in sd["cells"] if c["k"] == k) for k in sorted({c["k"] for c in sd["cells"]})}
        stats1 = None
        for k in levels:
            res, obs = compute_level(side, sd, k, mcount[side], tr, do_null)
            if k == 1:
                items, _ = observations(sd, 1)
                res["clusters_proxy"] = proxy_clusters(sd, 1, collapse(items))
            if k == 1 and tr is not None:
                st1 = {x["axis"]: x for x in res["axes"] if x["grid"] == "полная"}
                res["clusters"] = level1_clusters(sd, side, tr, st1)
                res["cluster_agreement"] = agreement(res["clusters"]["families"], res["clusters_proxy"]["families"], res["clusters"]["names"])
            elif k >= 2:
                items, _ = observations(sd, k)
                res["clusters"] = proxy_clusters(sd, k, collapse(items))
            with open(os.path.join(out, "level-%s-%d.json" % (side, k)), "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False)
            with open(os.path.join(out, "obs-%s-%d.json" % (side, k)), "w", encoding="utf-8") as f:
                json.dump(obs, f, ensure_ascii=False)
            print(side, "уровень", k, "клеток", res["n_cells"], "осей", len(res["axes"]), "взаимодействий", len(res["interactions"]), flush=True)
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return meta


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compute")
    p.add_argument("--db", default="/data/chron/chron.sqlite")
    p.add_argument("--out", required=True)
    p.add_argument("--levels", default="1,2,3,4")
    p.add_argument("--code", default="/data/p42-tk674c/code")
    p.add_argument("--no-trades", action="store_true")
    p.add_argument("--no-null", action="store_true")
    q = sub.add_parser("check")
    q.add_argument("--db", default="/data/chron/chron.sqlite")
    a = ap.parse_args()
    if a.cmd == "check":
        print(json.dumps(noise_check(a.db), ensure_ascii=False, indent=1))
        return
    if a.cmd == "compute":
        run(a.db, a.out, [int(x) for x in a.levels.split(",")], a.code, not a.no_trades, not a.no_null)


if __name__ == "__main__":
    main()
