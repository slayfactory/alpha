#!/usr/bin/env python3
"""TK-726, П-42 этап A, шаг 1, пп. 1б–1д (PS-1054 + PS-1060 п. 6, 7): вклад выбывших монет, окна-двойники, края окон, январь. Только numpy и csv (pandas не нужен).

  python3 tk726_seam.py run [--out tk726.sqlite] [--report seam.txt]     # все проверки по сделкам на диске → таблица `tk726_seam` (длинный вид) и текст

Трактовки, зафиксированные ДО чтения результатов (протокол допускает обе; выбрана строгая):
  • Σ $ — как во всём проекте: gross − fees (без фандинга) по дате входа; ключ сделки в 1в — (sym, sig_ms, entry_ms, kind, net) в абсолютных мс.
  • 1б: недобор линии = среднее суточного P&L за 01.02–13.11 (286 сут.: FJ + SEL, ряды TK-718) минус среднее N1 (11 сут.); вклад = Σ $ 17 монет на wc / 42 сут.; решают три линии S1, S2, L401 («состав существенен» — если у любой вклад ≥ 25 % недобора; линия без недобора — не оценивается), остальные девять — описательно.
  • 1в: окно зачёта 01–13.11; сутки 01.11 (первые) исключены из обоих порогов и печатаются отдельно; расхождение = (|W∖T| + |T∖W|) / |W| по мультимножествам ключей; ΔΣ = Σ(T) − Σ(W) по всем сделкам окна без первых суток; клетка проходит при расхождении ≤ 1 % и |ΔΣ| ≤ 1 $. A сверяется с wc на всех 539, B — с wc на 522 монетах состава n1.
  • 1г: единица — сутки; статистика Δ = $/сделку(первые 2 суток окон) − $/сделку(остальные) по 10 окнам (m01…m06, wa, wb, wc, n1); «остальные» — циркулярный блочный бутстреп суток внутри окна, «первые 2 суток» — окна целиком (10 единиц), 20 000 повторов, зерно 20261127; блок ⌈n^{1/3}⌉ суток и 21 сутки.
  • 1д: Δ = $/сделку(01–10.01) − $/сделку(11–31.01); оба набора суток — циркулярный блочный бутстреп, блок ⌈31^{1/3}⌉ и 21 (не длиннее набора).
  • Исход 1г / 1д: «края нет» — весь 95 % интервал внутри ±0,2 $ на сделку при обоих блоках; «край есть» — интервал не накрывает 0 и выходит за ±0,2 при обоих; иначе «не ясно» (январь / N1 помечаются, не снимаются)."""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import math
import os
import sqlite3
import sys
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SEED = 20261127                         # PS-1053 п. 11
NBOOT = 20000
DAY_MS = 86_400_000
EPOCH0 = int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
EQ_MARGIN = 0.2                         # $ на сделку (PS-1060 п. 7)
SHARE_MAX, DSUM_MAX = 0.01, 1.0         # 1в (PS-1054)
CONTRIB_MIN = 0.25                      # 1б (PS-1054)
ROOT = os.environ.get("TK726_ROOT", "/data/p42-tk726")
OUT_DB = ROOT + "/tk726.sqlite"
TK718 = "/data/p42-tk718/tk718.sqlite"
TRADES_718 = "/data/p42-tk718/trades/tk718c-h1-A"
TRADES = ROOT + "/trades"
C539 = "/data/p42-tk621/universe/symbols-crypto.txt"
N1_UNI = "/data/p42-tk700/universe/n1.txt"
D = datetime.date.fromisoformat

# окна: имя → (первые, последние зачётные сутки (ISO), каталог сделок)
WINDOWS = {
    "m01": ("2026-01-01", "2026-01-31", TRADES_718), "m02": ("2026-02-01", "2026-02-28", TRADES_718), "m03": ("2026-03-01", "2026-03-31", TRADES_718),
    "m04": ("2026-04-01", "2026-04-30", TRADES_718), "m05": ("2026-05-01", "2026-05-31", TRADES_718), "m06": ("2026-06-01", "2026-06-30", TRADES_718),
    "wa": ("2026-07-01", "2026-08-25", TRADES + "/tk726-sel-A"), "wb": ("2026-08-26", "2026-10-02", TRADES + "/tk726-sel-A"), "wc": ("2026-10-03", "2026-11-13", TRADES + "/tk726-sel-A"),
    "n1": ("2026-11-14", "2026-11-24", TRADES + "/tk726-n1-A"),
}
TWIN_RANGE = ("2026-11-01", "2026-11-13")


def day_of(iso):
    return (D(iso) - D("2026-01-01")).days


# ───────────────────────────── чтение сделок ─────────────────────────────

FIELDS = ("sym", "kind", "entry_px_e8", "gross", "fees", "funding", "net", "sig_ms", "entry_ms", "exit_ms")


def read_trades(path, t0_ms):
    """Сделки одного файла: массивы; время — абсолютное (мс эпохи), день входа / выхода — сутки от 01.01.2026; pnl = gross − fees."""
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    a = {k: np.array([r[k] for r in rows], dtype=object) for k in ("sym", "kind")}
    for k in ("gross", "fees", "funding", "net"):
        a[k] = np.array([float(r[k]) for r in rows], dtype=np.float64)
    for k in ("entry_px_e8", "sig_ms", "entry_ms", "exit_ms"):
        a[k] = np.array([int(r[k]) for r in rows], dtype=np.int64)
    for k in ("sig_ms", "entry_ms", "exit_ms"):
        a[k] = a[k] + int(t0_ms)
    a["pnl"] = a["gross"] - a["fees"]
    a["eday"] = (a["entry_ms"] - EPOCH0) // DAY_MS
    a["xday"] = (a["exit_ms"] - EPOCH0) // DAY_MS
    return a


def take(a, mask):
    return {k: v[mask] for k, v in a.items()}


def in_days(a, lo_day, hi_day):
    return (a["eday"] >= lo_day) & (a["eday"] <= hi_day)


def day_arrays(a, lo_day, hi_day):
    """pnl и число сделок по суткам входа lo…hi (включительно)."""
    nd = hi_day - lo_day + 1
    m = in_days(a, lo_day, hi_day)
    d = (a["eday"][m] - lo_day).astype(np.int64)
    return np.bincount(d, weights=a["pnl"][m], minlength=nd), np.bincount(d, minlength=nd).astype(np.int64)


# ───────────────────────────── статистика ─────────────────────────────

def block_len(n):
    return max(1, math.ceil(round(n ** (1.0 / 3.0), 9)))


def circ_sums(p, k, L, nboot, rng):
    """Циркулярный блочный бутстреп рядов p, k (одна длина m): суммы по nboot повторам, блок L (не длиннее ряда)."""
    m = len(p)
    if m == 0:
        return np.zeros(nboot), np.zeros(nboot)
    L = max(1, min(L, m))
    nb = math.ceil(m / L)
    st = rng.integers(0, m, size=(nboot, nb))
    idx = ((st[:, :, None] + np.arange(L)[None, None, :]) % m).reshape(nboot, nb * L)[:, :m]
    return p[idx].sum(axis=1), k[idx].sum(axis=1)


def ratio(sp, sn):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sn > 0, sp / np.where(sn > 0, sn, 1), np.nan)


def pct95(x):
    x = x[np.isfinite(x)]
    if len(x) < 100:
        return float("nan"), float("nan"), len(x)
    lo, hi = np.percentile(x, [2.5, 97.5])
    return float(lo), float(hi), len(x)


def classify(lo, hi, margin=EQ_MARGIN):
    """Исход по интервалу разницы: inside — внутри ±margin («края нет»); не накрывает 0 и выходит за ±margin — «край есть»; иначе «не ясно»."""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return "не ясно"
    if lo > -margin and hi < margin:
        return "края нет"
    if (lo > 0 or hi < 0):
        return "край есть"
    return "не ясно"


def combine(c1, c2):
    return c1 if c1 == c2 else "не ясно"


def edge_test(wins, block, nboot=NBOOT, seed=SEED, first=2):
    """wins: [(pnl[d], n[d])] по окнам. Δ = $/сделку(первые `first` суток окон) − $/сделку(остальные). Окна — единицы «первых суток»; остальные сутки — блоки внутри окна."""
    rng = np.random.default_rng(seed)
    E = [(float(p[:first].sum()), float(k[:first].sum())) for p, k in wins]
    R = [(p[first:], k[first:].astype(np.float64)) for p, k in wins]
    pe, ne = sum(e[0] for e in E), sum(e[1] for e in E)
    pr, nr = sum(float(r[0].sum()) for r in R), sum(float(r[1].sum()) for r in R)
    point = (pe / ne if ne else float("nan")) - (pr / nr if nr else float("nan"))
    idx = rng.integers(0, len(E), size=(nboot, len(E)))
    ep = np.array([e[0] for e in E])[idx].sum(axis=1)
    en = np.array([e[1] for e in E])[idx].sum(axis=1)
    rp, rn = np.zeros(nboot), np.zeros(nboot)
    for p, k in R:
        a, b = circ_sums(p, k, block, nboot, rng)
        rp += a
        rn += b
    diff = ratio(ep, en) - ratio(rp, rn)
    lo, hi, nok = pct95(diff)
    return dict(point=point, lo=lo, hi=hi, n_ok=nok, e_trades=ne, r_trades=nr, e_days=2 * len(E), r_days=int(sum(len(r[0]) for r in R)), block=block)


def split_test(g1, g2, block, nboot=NBOOT, seed=SEED):
    """g1, g2: (pnl[d], n[d]) — два набора суток. Δ = $/сделку(g1) − $/сделку(g2); наборы — циркулярные блоки по отдельности."""
    rng = np.random.default_rng(seed)
    (p1, k1), (p2, k2) = g1, g2
    k1, k2 = k1.astype(np.float64), k2.astype(np.float64)
    point = (p1.sum() / k1.sum() if k1.sum() else float("nan")) - (p2.sum() / k2.sum() if k2.sum() else float("nan"))
    a1, b1 = circ_sums(p1, k1, block, nboot, rng)
    a2, b2 = circ_sums(p2, k2, block, nboot, rng)
    lo, hi, nok = pct95(ratio(a1, b1) - ratio(a2, b2))
    return dict(point=float(point), lo=lo, hi=hi, n_ok=nok, e_trades=float(k1.sum()), r_trades=float(k2.sum()), e_days=len(p1), r_days=len(p2), block=block)


def split_test_labeled(p, k, n_first, block, nboot=NBOOT, seed=SEED):
    """Один ряд суток (p, k длины m): первые `n_first` суток — набор 1, остальные — набор 2. Циркулярные блоки по всему ряду, метка набора идёт за вытянутыми сутками
    (блок 21 при 31 сутках не вырождается; PS-1053 п. 1: «рядом — тот же интервал при блоке 21 сутки»). Δ = $/сделку(набор 1) − $/сделку(набор 2)."""
    rng = np.random.default_rng(seed)
    k = np.asarray(k, np.float64)
    m = len(p)
    L = max(1, min(block, m))
    nb = math.ceil(m / L)
    st = rng.integers(0, m, size=(nboot, nb))
    idx = ((st[:, :, None] + np.arange(L)[None, None, :]) % m).reshape(nboot, nb * L)[:, :m]
    lab = (np.arange(m) < n_first).astype(np.float64)
    pf, nf = (p[idx] * lab[idx]).sum(axis=1), (k[idx] * lab[idx]).sum(axis=1)
    pr, nr = (p[idx] * (1 - lab[idx])).sum(axis=1), (k[idx] * (1 - lab[idx])).sum(axis=1)
    point = (p[:n_first].sum() / k[:n_first].sum() if k[:n_first].sum() else float("nan")) - (p[n_first:].sum() / k[n_first:].sum() if k[n_first:].sum() else float("nan"))
    lo, hi, nok = pct95(ratio(pf, nf) - ratio(pr, nr))
    return dict(point=float(point), lo=lo, hi=hi, n_ok=nok, e_trades=float(k[:n_first].sum()), r_trades=float(k[n_first:].sum()), e_days=n_first, r_days=m - n_first, block=block)


def edge_by_window(wins, first=2):
    """Описательно (после чтения 1г, в счётчик испытаний): по окну — сделок и $/сделку первых `first` суток и остальных."""
    out = []
    for p, k in wins:
        pe, ne, pr, nr = float(p[:first].sum()), int(k[:first].sum()), float(p[first:].sum()), int(k[first:].sum())
        out.append(dict(ne=ne, ue=pe / ne if ne else float("nan"), nr=nr, ur=pr / nr if nr else float("nan")))
    return out


# ───────────────────────────── 1б, 1в, 1г ─────────────────────────────

def contribution(a, dropped, lo_day, hi_day):
    """Σ $, сделок монет `dropped` по суткам входа lo…hi; на сутки — Σ / число суток."""
    m = in_days(a, lo_day, hi_day) & np.isin(a["sym"], list(dropped))
    nd = hi_day - lo_day + 1
    return dict(n=int(m.sum()), usd=float(a["pnl"][m].sum()), per_day=float(a["pnl"][m].sum()) / nd, days=nd)


def twin_key_counter(a, mask):
    return Counter(zip(a["sym"][mask].tolist(), a["sig_ms"][mask].tolist(), a["entry_ms"][mask].tolist(), a["kind"][mask].tolist(), np.round(a["net"][mask], 9).tolist()))


def twin_compare(wc, tw, coins=None, rng_days=TWIN_RANGE):
    """wc, tw — сделки (абсолютное время). Сравнение на зачёте 01–13.11: без первых суток — расхождение и ΔΣ; первые сутки — отдельно."""
    lo, hi = day_of(rng_days[0]), day_of(rng_days[1])
    out = {}
    for tag, l in (("rest", lo + 1), ("first", lo)):
        h = hi if tag == "rest" else lo
        mw = in_days(wc, l, h)
        if coins is not None:
            mw &= np.isin(wc["sym"], list(coins))
        mt = in_days(tw, l, h)
        cw, ct = twin_key_counter(wc, mw), twin_key_counter(tw, mt)
        miss, extra = sum((cw - ct).values()), sum((ct - cw).values())
        nw = int(mw.sum())
        out[tag] = dict(n_wc=nw, n_tw=int(mt.sum()), only_wc=miss, only_tw=extra, share=(miss + extra) / nw if nw else float("nan"),
                        usd_wc=float(wc["pnl"][mw].sum()), usd_tw=float(tw["pnl"][mt].sum()), dsum=float(tw["pnl"][mt].sum() - wc["pnl"][mw].sum()))
    r = out["rest"]
    out["ok"] = bool(r["share"] <= SHARE_MAX and abs(r["dsum"]) <= DSUM_MAX)
    return out


def forced_last_day(a, hi_day):
    m = (a["kind"] == "forced") & (a["xday"] == hi_day)
    return int(m.sum()), float(a["pnl"][m].sum()), int(((a["kind"] == "forced")).sum())


# ───────────────────────────── запуск ─────────────────────────────

def read_symbols(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip().replace(",", " ").split()


def load_window_trades(win, cid):
    lo, hi, root = WINDOWS[win]
    t0 = json.load(open(os.path.join(root, "t0.json"), encoding="utf-8"))[win]
    return read_trades(os.path.join(root, win, "%d-trades.csv" % cid), t0)


def load_twin_trades(name, cid):
    t0 = json.load(open(os.path.join(TRADES, "tk726-twin", "t0.json"), encoding="utf-8"))[name]
    return read_trades(os.path.join(TRADES, "tk726-twin", name, "%d-trades.csv" % cid), t0)


def baseline_n1(con718, ckey):
    """Среднее суточного P&L 01.02–13.11 (FJ 150 + SEL 136 сут.) и среднее N1 (11 сут.) из `tk718_ser`."""
    got = {g: np.frombuffer(p, np.float64) for g, p in con718.execute("SELECT grp, pnl FROM tk718_ser WHERE side='short' AND ckey=?", (ckey,))}
    h1, sel, n1 = got["H1"], got["SEL"], got["N1"]
    base = np.concatenate([h1[31:181], sel])
    return float(base.mean()), float(n1.mean()), len(base), len(n1)


def fmt(x, d=2):
    return "—" if x is None or (isinstance(x, float) and not math.isfinite(x)) else ("%." + str(d) + "f") % x


def cmd_run(a):
    out = sqlite3.connect(a.out, timeout=120)
    out.executescript("CREATE TABLE IF NOT EXISTS tk726_seam(item TEXT, cell TEXT, win TEXT, metric TEXT, value REAL, text TEXT);")
    out.execute("DELETE FROM tk726_seam")
    cells = list(out.execute("SELECT cid, name, ckey FROM tk726_cell ORDER BY cid"))
    con718 = sqlite3.connect("file:%s?mode=ro" % TK718, uri=True)
    rows = []
    put = lambda item, cell, win, metric, value=None, text="": rows.append((item, cell, win, metric, None if value is None else float(value), text))   # noqa: E731
    lines = []
    say = lines.append
    dropped = sorted(set(read_symbols(C539)) - set(read_symbols(N1_UNI)))
    n1_syms = set(read_symbols(N1_UNI))
    assert len(dropped) == 17, len(dropped)
    LINES = ("S1", "S2", "L401")
    summary = dict(b_fail=[], v_fail=[], g_status={}, j_status={})
    wtr = {}
    for cid, name, ckey in cells:
        wtr[cid] = {w: load_window_trades(w, cid) for w in WINDOWS}
    say("1б. Вклад 17 выбывших монет (%s…) на wc, 03.10–13.11 (42 сут.)" % ", ".join(dropped[:3]))
    say("клетка | сделок 17 монет | Σ $ | $/сут. | среднее 01.02–13.11 | среднее N1 | недобор | доля недобора")
    for cid, name, ckey in cells:
        c = contribution(wtr[cid]["wc"], dropped, day_of("2026-10-03"), day_of("2026-11-13"))
        base, n1m, nb, nn = baseline_n1(con718, ckey)
        short = base - n1m
        share = c["per_day"] / short if short > 0 else float("nan")
        put("1b", name, "wc", "n17", c["n"]); put("1b", name, "wc", "usd17", c["usd"]); put("1b", name, "wc", "per_day17", c["per_day"])
        put("1b", name, "", "base_mean", base); put("1b", name, "", "n1_mean", n1m); put("1b", name, "", "shortfall", short); put("1b", name, "", "share_of_shortfall", share)
        for w in ("wa", "wb", "wc"):
            lo, hi = day_of(WINDOWS[w][0]), day_of(WINDOWS[w][1])
            cw = contribution(wtr[cid][w], dropped, lo, hi)
            put("1b", name, w, "usd17_win", cw["usd"]); put("1b", name, w, "n17_win", cw["n"])
        say("%-13s | %4d | %8s | %6s | %6s | %7s | %7s | %s" % (name, c["n"], fmt(c["usd"]), fmt(c["per_day"]), fmt(base), fmt(n1m), fmt(short), fmt(share, 3)))
        if name in LINES and short > 0 and c["per_day"] >= CONTRIB_MIN * short:
            summary["b_fail"].append(name)
    say("1б итог: %s" % ("состав существенен у " + ", ".join(summary["b_fail"]) if summary["b_fail"] else "у S1, S2, L401 вклад 17 монет < 25 % недобора — состав не существенен"))

    say("")
    say("1в. Окна-двойники на 01–13.11 (без первых суток 01.11): расхождение сделок и ΔΣ, порог ≤ 1 % и ≤ 1 $")
    say("клетка | окно | сделок wc | сделок окна | только wc | только окна | расхождение | ΔΣ $ | проход | первые сутки: wc / окно сделок, ΔΣ")
    tw_ok = {}
    for cid, name, ckey in cells:
        for tname, coins in (("twA", None), ("twB", n1_syms)):
            res = twin_compare(wtr[cid]["wc"], load_twin_trades(tname, cid), coins)
            r, f = res["rest"], res["first"]
            for k, v in r.items():
                put("1v", name, tname, k, v)
            for k, v in f.items():
                put("1v", name, tname, "first_" + k, v)
            put("1v", name, tname, "ok", int(res["ok"]))
            tw_ok[(name, tname)] = res["ok"]
            say("%-13s | %s | %4d | %4d | %3d | %3d | %s | %s | %s | %d / %d, %s" % (name, tname, r["n_wc"], r["n_tw"], r["only_wc"], r["only_tw"], fmt(100 * r["share"], 2) + " %", fmt(r["dsum"], 3), "да" if res["ok"] else "НЕТ",
                                                                                       f["n_wc"], f["n_tw"], fmt(f["dsum"], 3)))
            if not res["ok"]:
                summary["v_fail"].append((name, tname))
    say("1в итог: проходят %d из %d пар «клетка × окно»; не проходят: %s" % (sum(tw_ok.values()), len(tw_ok), ", ".join("%s/%s" % x for x in summary["v_fail"]) or "нет"))

    say("")
    say("1г. Края окон: Δ = $/сделку(первые 2 суток окон) − $/сделку(остальные), 10 окон; интервал 95 % (блок ⌈n^{1/3}⌉ | 21)")
    say("клетка | Δ точка | интервал (блок) | интервал (21) | исход | сделок E / R")
    for cid, name, ckey in cells:
        wins = [day_arrays(wtr[cid][w], day_of(WINDOWS[w][0]), day_of(WINDOWS[w][1])) for w in WINDOWS]
        nrest = sum(len(p) - 2 for p, k in wins)
        b1 = block_len(nrest)
        r1, r2 = edge_test(wins, b1), edge_test(wins, 21)
        c1, c2 = classify(r1["lo"], r1["hi"]), classify(r2["lo"], r2["hi"])
        dec = combine(c1, c2)
        summary["g_status"][name] = dec
        for k, v in r1.items():
            put("1g", name, "block%d" % b1, k, v)
        for k, v in r2.items():
            put("1g", name, "block21", k, v)
        put("1g", name, "", "decision", None, dec)
        say("%-13s | %s | [%s; %s] б%d | [%s; %s] | %s | %d / %d" % (name, fmt(r1["point"], 3), fmt(r1["lo"], 3), fmt(r1["hi"], 3), b1, fmt(r2["lo"], 3), fmt(r2["hi"], 3), dec, r1["e_trades"], r1["r_trades"]))
    say("")
    say("1г (описательно, после чтения 1г — в счётчик испытаний). По окнам: $/сделку первых 2 суток (сделок) | остальных (сделок); Δ без n1 и без m01 — точка")
    for cid, name, ckey in cells:
        if name not in LINES and name != "BS-l7-tf60":
            continue
        wins = {w: day_arrays(wtr[cid][w], day_of(WINDOWS[w][0]), day_of(WINDOWS[w][1])) for w in WINDOWS}
        ebw = dict(zip(WINDOWS, edge_by_window(list(wins.values()))))
        for w, e in ebw.items():
            put("1g", name, w, "edge_usd_first2", e["ue"]); put("1g", name, w, "edge_n_first2", e["ne"]); put("1g", name, w, "edge_usd_rest", e["ur"]); put("1g", name, w, "edge_n_rest", e["nr"])
        pe = sum(e["ue"] * e["ne"] for e in ebw.values() if e["ne"])
        sub = [w for w in WINDOWS if w not in ("n1", "m01")]
        ne = sum(ebw[w]["ne"] for w in sub); nr = sum(ebw[w]["nr"] for w in sub)
        d_sub = sum(ebw[w]["ue"] * ebw[w]["ne"] for w in sub if ebw[w]["ne"]) / ne - sum(ebw[w]["ur"] * ebw[w]["nr"] for w in sub if ebw[w]["nr"]) / nr
        put("1g", name, "", "edge_point_without_n1_m01", d_sub)
        say("%-13s | %s | Δ без n1 и m01 %s" % (name, "; ".join("%s %s (%d) | %s (%d)" % (w, fmt(e["ue"]), e["ne"], fmt(e["ur"]), e["nr"]) for w, e in ebw.items()), fmt(d_sub, 3)))
    say("")
    say("1г (описательно). Выходы `forced` в последние сутки окна (число / Σ $; всего forced в окне) — S1, S2, L401")
    for cid, name, ckey in cells:
        if name not in LINES:
            continue
        parts = []
        for w in WINDOWS:
            nf, uf, nall = forced_last_day(wtr[cid][w], day_of(WINDOWS[w][1]))
            put("1g", name, w, "forced_last_n", nf); put("1g", name, w, "forced_last_usd", uf); put("1g", name, w, "forced_all", nall)
            parts.append("%s %d / %s (%d)" % (w, nf, fmt(uf), nall))
        say("%-5s | %s" % (name, "; ".join(parts)))

    say("")
    say("1д. Январь: Δ = $/сделку(01–10.01) − $/сделку(11–31.01); интервал 95 %")
    say("клетка | Δ точка | интервал (блок 4) | интервал (21) | исход | сделок 01–10 / 11–31 (метки суток идут за блоками)")
    j0 = day_of("2026-01-01")
    for cid, name, ckey in cells:
        p, k = day_arrays(wtr[cid]["m01"], j0, j0 + 30)
        r1, r2 = split_test_labeled(p, k, 10, block_len(31)), split_test_labeled(p, k, 10, 21)
        dec = combine(classify(r1["lo"], r1["hi"]), classify(r2["lo"], r2["hi"]))
        summary["j_status"][name] = dec
        for k_, v in r1.items():
            put("1d", name, "block%d" % r1["block"], k_, v)
        for k_, v in r2.items():
            put("1d", name, "block21", k_, v)
        put("1d", name, "", "decision", None, dec)
        say("%-13s | %s | [%s; %s] | [%s; %s] | %s | %d / %d" % (name, fmt(r1["point"], 3), fmt(r1["lo"], 3), fmt(r1["hi"], 3), fmt(r2["lo"], 3), fmt(r2["hi"], 3), dec, r1["e_trades"], r1["r_trades"]))

    say("")
    gs, js = Counter(summary["g_status"].values()), Counter(summary["j_status"].values())
    say("ИТОГ шага 1: 1б — %s; 1в — %s; 1г — %s; 1д — %s" % (
        "не пройден (N1 «состав урезан»)" if summary["b_fail"] else "пройден",
        "пройден" if not summary["v_fail"] else "НЕ пройден (%d пар) — числа N1 и января сняты до починки" % len(summary["v_fail"]),
        dict(gs), dict(js)))
    out.executemany("INSERT INTO tk726_seam VALUES(?,?,?,?,?,?)", rows)
    out.commit()
    text = "\n".join(lines)
    print(text)
    if a.report:
        open(a.report, "w", encoding="utf-8").write(text + "\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--out", default=OUT_DB)
    r.add_argument("--report", default="")
    a = ap.parse_args(argv)
    return {"run": cmd_run}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
