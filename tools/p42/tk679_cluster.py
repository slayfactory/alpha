#!/usr/bin/env python3
"""П-42, TK-679, §13.75 (PS-967, В-173): кластеры сигналов без потолка позиций — описательный разбор по готовым строкам шага 0
(`touch/<окно>-<сторона>/{p100000r0w0,p3r0w0,free}`); движок не запускается. Вызов — `tk679_run.py cluster`.
Определения — §13.75: `k_W` сигнала = 1 + число других монет с сигналом той же стороны в (t − W, t] (W = 5, 15 мин; W = 0 — та же
календарная минута, непричинно); корзины 1 / 2–3 / 4+; $ = gross − fees при ставке 500 $; сутки — по входу; зачётные диапазоны окон."""
from __future__ import annotations

import csv
import json
import math
import os

import numpy as np

import tk604_feat as f4
import tk604_stats as st
import tk636_feat as f36
import tk663_shape as sh

MARGIN, ACCOUNT = 250.0, 2500.0
MIN = 60_000
ENG = {"L": "long", "S": "short"}
SGN = {"L": 1.0, "S": -1.0}
SIDENAME = {"L": "Лонг", "S": "Шорт"}
ROW_P, ROW_3, ROW_F = "p100000r0w0", "p3r0w0", "free"
WS = (0, 5, 15)
BUCKETS = ("одна", "2–3", "4+")
SIZES = ((1, 1, "1"), (2, 2, "2"), (3, 3, "3"), (4, 5, "4–5"), (6, 9, "6–9"), (10, 10 ** 9, "10+"))
REFS = ("BTCUSDT", "ETHUSDT")
BLOCK, REPS, SEED = 6, 10_000, 679
CACHE = "/data/p42-tk621/%s/cache/%s.b42"
PEER = "/data/p42-tk636/feat/h775/%s-%s.csv"
NDAYS = (st.LAST_DAY - st.D0).days + 1


def bucket(k):
    return np.where(k >= 4, 2, np.where(k >= 2, 1, 0))


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_trades(d, side, w):
    """Сделки строки окна: абсолютное время, сутки входа, $ (gross − fees), вид выхода, отметка зачётного диапазона."""
    r = read_rows(os.path.join(d, "%s-trades.csv" % ENG[side]))
    i = lambda k: np.array([int(x[k]) for x in r], dtype=np.int64)          # noqa: E731
    f = lambda k: np.array([float(x[k]) for x in r], dtype=float)           # noqa: E731
    sig, ent, ext = i("sig_ms"), i("entry_ms"), i("exit_ms")
    return dict(sym=np.array([x["sym"] for x in r], dtype=object), level=i("level"), sig_rel=sig, sig=sig + w.t0_ms, ent=ent + w.t0_ms, ext=ext + w.t0_ms,
                pnl=f("gross") - f("fees"), kind=np.array([x["kind"] for x in r], dtype=object), day=st.abs_day(w, ent),
                ok=f4.in_range(ent + w.t0_ms, w), win=np.array([w.name] * len(r), dtype=object))


def load_sigs(d, side, w):
    """Дамп всех сигналов прохода (`P42_SIGSX`): столбцы 0 — монета, 1 — уровень, 2 — время сигнала от T0 окна, мс."""
    with open(os.path.join(d, "%s-sigsx.csv" % ENG[side]), encoding="utf-8", newline="") as f:
        r = list(csv.reader(f))[1:]
    sig = np.array([int(x[2]) for x in r], dtype=np.int64)
    out = dict(sym=np.array([x[0] for x in r], dtype=object), level=np.array([int(x[1]) for x in r], dtype=np.int64), sig_rel=sig, sig=sig + w.t0_ms,
               win=np.array([w.name] * len(r), dtype=object))
    out["ok"] = f4.in_range(out["sig"], w)
    return out


def cat(parts, mask_key=None):
    keys = parts[0].keys()
    out = {}
    for k in keys:
        out[k] = np.concatenate([p[k][p[mask_key]] if mask_key else p[k] for p in parts])
    return out


def k_of(sym, ms, wmin):
    """`k_W` по набору (монета, время): число разных монет в (t − W, t] (вместе с самой); W = 0 — в той же календарной минуте."""
    n = len(ms)
    k = np.ones(n, dtype=np.int64)
    if not n:
        return k
    if wmin == 0:
        by = {}
        for s, m in zip(sym, ms // MIN):
            by.setdefault(int(m), set()).add(s)
        return np.array([len(by[int(m)]) for m in ms // MIN], dtype=np.int64)
    o = np.argsort(ms, kind="stable")
    s_, m_ = sym[o], ms[o]
    lo = np.searchsorted(m_, m_ - wmin * MIN, side="right")
    hi = np.searchsorted(m_, m_, side="right")
    for j in range(n):
        k[o[j]] = len(set(s_[lo[j]:hi[j]]) | {s_[j]})
    return k


def chains(sym, ms, wmin):
    """Цепочки с разрывом между соседними сигналами ≤ W: [(число монет, число сигналов)]."""
    if not len(ms):
        return []
    o = np.argsort(ms, kind="stable")
    s_, m_ = sym[o], ms[o]
    cut = np.nonzero(np.diff(m_) > wmin * MIN)[0] + 1
    return [(len(set(part)), len(part)) for part in np.split(s_, cut)]


def size_table(ch):
    tot = sum(n for _, n in ch) or 1
    return [dict(size=name, groups=sum(1 for c, _ in ch if a <= c <= b), items=sum(n for c, n in ch if a <= c <= b),
                 share=sum(n for c, n in ch if a <= c <= b) / tot) for a, b, name in SIZES]


def moves(side, tr):
    """Ход BTC и ETH в сторону пробоя за 5 и 15 закрытых минут до минуты сигнала: c[t−1]/c[t−1−W] − 1, доля цены; нет данных — NaN."""
    out = {"%s%d" % (r[:3].lower(), W): np.full(len(tr["sig"]), np.nan) for r in REFS for W in (5, 15)}
    for wn in sorted(set(tr["win"])):
        m = np.nonzero(tr["win"] == wn)[0]
        t = tr["sig_rel"][m] // MIN
        for r in REFS:
            o, c = f36.load_b42(CACHE % (wn, r))[:2]
            for W in (5, 15):
                a, b = t - 1, t - 1 - W
                good = (b >= 0) & (a < len(c))
                v = np.full(len(m), np.nan)
                ga, gb = a[good], b[good]
                has = (o[ga] > 0) & (o[gb] > 0)
                x = np.full(len(ga), np.nan)
                x[has] = c[ga][has] / c[gb][has] - 1.0
                v[good] = x
                out["%s%d" % (r[:3].lower(), W)][m] = SGN[side] * v
    return out


def peers(side, tr):
    out = {k: np.full(len(tr["sig"]), np.nan) for k in ("peer15", "peerall15")}
    for wn in sorted(set(tr["win"])):
        tab = {}
        for r in read_rows(PEER % (wn, side)):
            tab[(r["sym"], int(r["sig_ms"]))] = tuple(float(r[k]) if r[k] != "" else np.nan for k in ("peer15", "peerall15"))
        for i in np.nonzero(tr["win"] == wn)[0]:
            v = tab.get((tr["sym"][i], int(tr["sig_rel"][i])))
            if v:
                out["peer15"][i], out["peerall15"][i] = v
    return out


def ranks(x):
    o = np.argsort(x, kind="stable")
    r = np.empty(len(x))
    r[o] = np.arange(len(x), dtype=float)
    xs = x[o]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        r[o[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return r


def spearman(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 3:
        return None, int(m.sum())
    ra, rb = ranks(np.asarray(a[m], float)), ranks(np.asarray(b[m], float))
    if ra.std() == 0 or rb.std() == 0:
        return None, int(m.sum())
    return float(np.corrcoef(ra, rb)[0, 1]), int(m.sum())


def nmean(x):
    x = x[np.isfinite(x)]
    return float(x.mean()) if len(x) else None


def stat(tr, mask, days=None):
    """Строка таблицы по сделкам маски; days — (первые, последние) сутки для Шарпа и перехая (по умолчанию все 136)."""
    lo, hi = days or (0, NDAYS - 1)
    p, d, kind = tr["pnl"][mask], tr["day"][mask], tr["kind"][mask]
    n = int(mask.sum())
    sd = np.bincount(d - lo, weights=p, minlength=hi - lo + 1)[:hi - lo + 1] if n else np.zeros(hi - lo + 1)
    u = sh.uw(sd)
    nr = mask & ~np.isin(tr["sym"], REFS)
    out = dict(n=n, usd=float(p.sum()), mean=float(p.mean()) if n else None, stop=float((kind == "stop").mean()) if n else None,
               be_loss=float(((kind == "be") & (p < 0)).mean()) if n else None, sharpe=sh.sharpe(sd), maxdd=float(u["maxdd"]), maxdd_pct=float(u["maxdd"]) / MARGIN * 100.0,
               gap_longest=int(u["gap_longest"]), gap_mean=None if math.isnan(u["gap_mean"]) else float(u["gap_mean"]), gap_tail=int(u["tail"]), n_noref=int(nr.sum()))
    for key in ("btc5", "btc15", "eth5", "eth15"):
        v = tr[key][nr]
        out[key] = nmean(v)
        out[key + "_abs"] = nmean(np.abs(v))
    v = tr["btc5"][nr]
    v = v[np.isfinite(v)]
    out["btc5_ge02"] = float((v >= 0.002).mean()) if len(v) else None
    for key in ("peer15", "peerall15"):
        out[key] = nmean(tr[key][mask])
    return out


def boot_diff(tr, a, b):
    """Разница среднего $ на сделку (маска a − маска b): блочный бутстреп по суткам входа, блок 6, 10 000 повторов, интервал 95 %."""
    sa, na = np.bincount(tr["day"][a], weights=tr["pnl"][a], minlength=NDAYS), np.bincount(tr["day"][a], minlength=NDAYS)
    sb, nb = np.bincount(tr["day"][b], weights=tr["pnl"][b], minlength=NDAYS), np.bincount(tr["day"][b], minlength=NDAYS)
    if not na.sum() or not nb.sum():
        return None
    rng = np.random.default_rng(SEED)
    nblk = -(-NDAYS // BLOCK)
    starts = rng.integers(0, NDAYS, size=(REPS, nblk))
    idx = ((starts[:, :, None] + np.arange(BLOCK)[None, None, :]) % NDAYS).reshape(REPS, -1)[:, :NDAYS]
    A, NA, B, NB = sa[idx].sum(1), na[idx].sum(1), sb[idx].sum(1), nb[idx].sum(1)
    good = (NA > 0) & (NB > 0)
    dd = A[good] / NA[good] - B[good] / NB[good]
    return dict(diff=float(sa.sum() / na.sum() - sb.sum() / nb.sum()), lo=float(np.quantile(dd, 0.025)), hi=float(np.quantile(dd, 0.975)), reps=int(good.sum()), block=BLOCK)


def load_of(parts):
    """Нагрузка строки по окнам (все сделки окна): максимум одновременных позиций, маржа в % счёта, минут с > 3 позициями."""
    out = {}
    for wn, t in parts.items():
        if not len(t["ent"]):
            out[wn] = dict(max_pos=0, margin_pct=0.0, min_over3=0.0, at=None)
            continue
        ev = np.concatenate([t["ent"], t["ext"]])
        dl = np.concatenate([np.ones(len(t["ent"]), dtype=np.int64), -np.ones(len(t["ext"]), dtype=np.int64)])
        o = np.lexsort((dl, ev))                                # при равном времени сначала выходы
        ev, cnt = ev[o], np.cumsum(dl[o])
        j = int(np.argmax(cnt))
        over = float(np.diff(ev)[cnt[:-1] > 3].sum()) / MIN
        out[wn] = dict(max_pos=int(cnt[j]), margin_pct=float(cnt[j]) * MARGIN / ACCOUNT * 100.0, min_over3=over, at=int(ev[j]))
    return out


def side_block(root, side):
    P, T3, F, S, meta = {}, {}, {}, {}, {}
    for w in st.TRADE_WINDOWS:
        base = os.path.join(root, "touch", "%s-%s" % (w.name, side))
        P[w.name], T3[w.name], F[w.name] = (load_trades(os.path.join(base, r), side, w) for r in (ROW_P, ROW_3, ROW_F))
        S[w.name] = load_sigs(os.path.join(base, ROW_P), side, w)
        mt = json.dumps(json.load(open(os.path.join(base, ROW_P, "meta.json"), encoding="utf-8")), ensure_ascii=False)
        if "be_mode: 3" in mt:
            raise SystemExit("%s %s: bm = 3 в базе' — стопы после тейка считать отдельно (§13.73.10 п. 4)" % (w.name, side))
        meta[w.name] = mt
    wins = [w.name for w in st.TRADE_WINDOWS]
    sg = cat([S[n] for n in wins], "ok")                        # сигналы зачётных диапазонов
    tr = cat([P[n] for n in wins], "ok")                        # сделки строки p=100000 зачётных диапазонов
    t3, fr = cat([T3[n] for n in wins], "ok"), cat([F[n] for n in wins], "ok")
    key = lambda t: {(s, int(lv), int(m)) for s, lv, m in zip(t["sym"], t["level"], t["sig"])}      # noqa: E731
    kp_all, kf_all = key(cat([P[n] for n in wins])), key(cat([F[n] for n in wins]))
    skeys = {(s, int(m)) for s, m in zip(sg["sym"], sg["sig"])}
    miss = sum(1 for s, m in zip(tr["sym"], tr["sig"]) if (s, int(m)) not in skeys)
    # охрана: сигнал сделки есть в дампе (сделки у края зачётного диапазона могут иметь сигнал до его начала)
    if miss > max(3, 0.01 * len(tr["sig"])):
        raise SystemExit("%s: %d сделок из %d без сигнала в дампе" % (side, miss, len(tr["sig"])))
    for t in (tr, t3, fr):
        t.update(moves(side, t))
        t.update(peers(side, t))
    res = dict(signals=int(len(sg["sig"])), trades=int(len(tr["sig"])), trades_no_sig=miss, load=load_of(P), load_p3=load_of(T3), k={}, sizes={}, fate={}, tables={}, per_window={})
    ks_sig = {W: k_of(sg["sym"], sg["sig"], W) for W in WS}
    kmap = {W: {(s, int(m)): int(k) for s, m, k in zip(sg["sym"], sg["sig"], ks_sig[W])} for W in WS}
    for W in WS:
        k_main = np.array([kmap[W].get((s, int(m)), 1) for s, m in zip(tr["sym"], tr["sig"])], dtype=np.int64)
        k_own = k_of(tr["sym"], tr["sig"], W)
        tr["k%d" % W], tr["kown%d" % W] = k_main, k_own
        bs = bucket(ks_sig[W])
        res["k"][W] = dict(signals=[int((bs == b).sum()) for b in range(3)], trades=[int((bucket(k_main) == b).sum()) for b in range(3)],
                           trades_own=[int((bucket(k_own) == b).sum()) for b in range(3)], k_max_signals=int(ks_sig[W].max()) if len(ks_sig[W]) else 0)
        res["tables"][W] = dict(main=[stat(tr, bucket(k_main) == b) for b in range(3)], own=[stat(tr, bucket(k_own) == b) for b in range(3)])
        res["per_window"][W] = {}
        for w in st.TRADE_WINDOWS:
            dd = ((w.lod - st.D0).days, (w.hid - st.D0).days)
            res["per_window"][W][w.name] = [stat(tr, (bucket(k_main) == b) & (tr["win"] == w.name), dd) for b in range(3)]
    for W in (1, 5, 15):
        res["sizes"][W] = dict(signals=size_table(chains(sg["sym"], sg["sig"], W)), trades=size_table(chains(tr["sym"], tr["sig"], W)))
    b15 = bucket(ks_sig[15])
    sk = [(s, int(lv), int(m)) for s, lv, m in zip(sg["sym"], sg["level"], sg["sig"])]
    inp, inf = np.array([x in kp_all for x in sk], dtype=bool), np.array([x in kf_all for x in sk], dtype=bool)
    for b in range(3):
        m = b15 == b
        res["fate"][BUCKETS[b]] = dict(signals=int(m.sum()), entered=int((m & inp).sum()), pauses=int((m & ~inp & inf).sum()), other=int((m & ~inp & ~inf).sum()),
                                       entered_not_free=int((m & inp & ~inf).sum()))
    all_ = np.ones(len(tr["sig"]), dtype=bool)
    res["anchors"] = {"p=100000": stat(tr, all_), "база' p=3": stat(t3, np.ones(len(t3["sig"]), dtype=bool)), "free (без пауз)": stat(fr, np.ones(len(fr["sig"]), dtype=bool))}
    res["anchors_window"] = {w.name: {"p=100000": stat(tr, tr["win"] == w.name, ((w.lod - st.D0).days, (w.hid - st.D0).days)),
                                      "база' p=3": stat(t3, t3["win"] == w.name, ((w.lod - st.D0).days, (w.hid - st.D0).days))} for w in st.TRADE_WINDOWS}
    k15 = tr["k15"].astype(float)
    nr = ~np.isin(tr["sym"], REFS)
    nanref = lambda v: np.where(nr, v, np.nan)                  # noqa: E731
    res["spearman"] = {name: spearman(k15, v) for name, v in (("peerall15", tr["peerall15"]), ("peer15", tr["peer15"]), ("btc_mv5 (ход BTC 5 мин в сторону пробоя)", nanref(tr["btc5"])),
                                                              ("модуль хода BTC 5 мин", nanref(np.abs(tr["btc5"]))))}
    res["boot"] = boot_diff(tr, bucket(tr["k15"]) == 2, bucket(tr["k15"]) == 0)
    one, many = res["tables"][15]["main"][0]["btc5_abs"], res["tables"][15]["main"][2]["btc5_abs"]
    res["rule"] = dict(one=one, many=many, ratio=(many / one) if one and many is not None else None)
    res["peer_cover"] = float(np.isfinite(tr["peerall15"]).mean()) if len(tr["sig"]) else None
    return res


# ───────────────────────────── отчёт ─────────────────────────────

def usd(x, nd=0):
    return "—" if x is None else ("%+.*f $" % (nd, x)).replace("-", "−")


def pc(x, nd=0, sign=False):
    return "—" if x is None else (("%+.*f %%" if sign else "%.*f %%") % (nd, x * 100)).replace("-", "−")


def num(x, nd=2):
    return "—" if x is None else ("%.*f" % (nd, x)).replace("-", "−")


HEAD = "| строка | сделок | Σ $ | $ на сделку | жёстких стопов | `be` с убытком | Шарп (год) | просадка $ | просадка, % маржи | перехай: длинный / средний / хвост, сут |"
HEAD2 = "| строка | сделок без BTC/ETH | BTC 5 мин | BTC 15 мин | ETH 5 мин | ETH 15 мин | \\|BTC\\| 5 мин | \\|BTC\\| 15 мин | \\|ETH\\| 5 мин | \\|ETH\\| 15 мин | BTC 5 мин ≥ 0,2 % | peer15 | peerall15 |"


def row(name, s):
    return "| %s | %d | %s | %s | %s | %s | %s | %s | %s | %d / %s / %d |" % (
        name, s["n"], usd(s["usd"]), usd(s["mean"], 2), pc(s["stop"]), pc(s["be_loss"]), num(s["sharpe"]), usd(-s["maxdd"] if s["maxdd"] else 0.0), pc(s["maxdd_pct"] / 100), s["gap_longest"],
        num(s["gap_mean"], 1), s["gap_tail"])


def row2(name, s):
    return "| %s | %d | %s | %s |" % (name, s["n_noref"], " | ".join(pc(s[k], 3, True) for k in ("btc5", "btc15", "eth5", "eth15")) + " | " + " | ".join(
        pc(s[k], 3) for k in ("btc5_abs", "btc15_abs", "eth5_abs", "eth15_abs")), " | ".join((pc(s["btc5_ge02"]), num(s["peer15"]), num(s["peerall15"]))))


def sep(h):
    return "|" + "---|" * (h.count("|") - 1 - h.count("\\|"))


def report(res):
    L = ["# TK-679, §13.75 (PS-967): кластеры сигналов без потолка позиций — h775, 01.07–13.11, база' (стоп лонга 3 %, шорта 4 %), строка `p=100000`", "",
         "Измерено; описательно, «найдено на этих данных»; лучшая корзина не выбирается. Ставка 500 $, маржа 250 $; $ = gross − fees; сутки — по входу; Шарп — годовой по суткам (все сутки, пустые = 0).",
         "`k_W` — по всем сигналам прохода (главный счёт); корзина сделки — по `k_W` её сигнала. Ход BTC / ETH — в сторону пробоя, по закрытым минутам до минуты сигнала, % цены; сделки самих BTCUSDT / ETHUSDT в столбцах хода исключены.", ""]
    for side, r in res.items():
        L += ["## %s" % SIDENAME[side], "", "Сигналов в зачётных диапазонах: %d; сделок строки `p=100000`: %d (без сигнала в дампе — %d). Покрытие `peerall15`: %s." % (
            r["signals"], r["trades"], r["trades_no_sig"], pc(r["peer_cover"])), "", "### Якоря", "", HEAD, sep(HEAD)]
        L += [row(k, v) for k, v in r["anchors"].items()]
        for wn, a in r["anchors_window"].items():
            L += [row("%s · %s" % (wn, k), v) for k, v in a.items()]
        L += ["", "### Нагрузка без потолка", "", "| окно | строка | максимум позиций разом | маржа, % счёта 2 500 $ | минут с > 3 позициями |", "|---|---|---|---|---|"]
        for wn in r["load"]:
            for nm, src in (("p=100000", r["load"]), ("база' p=3", r["load_p3"])):
                L.append("| %s | %s | %d | %s | %.0f |" % (wn, nm, src[wn]["max_pos"], pc(src[wn]["margin_pct"] / 100), src[wn]["min_over3"]))
        L += ["", "### Размеры групп (цепочки с разрывом ≤ W; размер — число разных монет)", "", "| W, мин | набор | " + " | ".join("%s: групп (доля)" % s[2] for s in SIZES) + " |",
              "|---|---|" + "---|" * len(SIZES)]
        for W, v in r["sizes"].items():
            for nm, key in (("сигналы", "signals"), ("сделки", "trades")):
                L.append("| %s | %s | %s |" % (W, nm, " | ".join("%d (%s)" % (x["groups"], pc(x["share"])) for x in v[key])))
        L += ["", "### Сколько сигналов и сделок в корзинах", "", "| W, мин | набор | одна | 2–3 | 4+ |", "|---|---|---|---|---|"]
        for W, v in r["k"].items():
            for nm, key in (("сигналы", "signals"), ("сделки (k по всем сигналам)", "trades"), ("сделки (k только по сделкам)", "trades_own")):
                L.append("| %s | %s | %d | %d | %d |" % (W, nm, *v[key]))
        L += ["", "### Куда делись сигналы (корзина по W = 15 мин)", "", "| корзина | сигналов | вошло (`p=100000`) | не вошло из-за пауз | не вошло по другим причинам | вошло, но нет в `free` |",
              "|---|---|---|---|---|---|"]
        L += ["| %s | %d | %d | %d | %d | %d |" % (b, v["signals"], v["entered"], v["pauses"], v["other"], v["entered_not_free"]) for b, v in r["fate"].items()]
        for W, v in r["tables"].items():
            wn = "та же минута (непричинно)" if int(W) == 0 else "W = %s мин" % W
            for nm, key in (("k по всем сигналам", "main"), ("k только по вошедшим сделкам", "own")):
                L += ["", "### Корзины, %s — %s" % (wn, nm), "", HEAD, sep(HEAD)] + [row(BUCKETS[b], v[key][b]) for b in range(3)]
                if key == "main":
                    L += ["", HEAD2, sep(HEAD2)] + [row2(BUCKETS[b], v[key][b]) for b in range(3)]
            L += ["", "По окнам (k по всем сигналам):", "", HEAD, sep(HEAD)]
            for w_, rows in r["per_window"][W].items():
                L += [row("%s · %s" % (w_, BUCKETS[b]), rows[b]) for b in range(3)]
        L += ["", "### Связь `k_15` с признаками (ранговая корреляция, сделки строки `p=100000`)", "", "| признак | ρ | сделок |", "|---|---|---|"]
        L += ["| %s | %s | %d |" % (k, num(v[0]), v[1]) for k, v in r["spearman"].items()]
        b = r["boot"]
        L += ["", "Разница $ на сделку «4+» − «одна» (W = 15 мин): %s" % ("нет сделок в одной из корзин" if not b else "%s [%s; %s] (блочный бутстреп по суткам, блок %d, %d повторов, 95 %%)" % (
            usd(b["diff"], 2), usd(b["lo"], 2), usd(b["hi"], 2), b["block"], b["reps"])), ""]
        q = r["rule"]
        L += ["Модуль хода BTC за 5 мин (W = 15 мин): «одна» %s, «4+» %s, отношение %s." % (pc(q["one"], 3), pc(q["many"], 3), "—" if q["ratio"] is None else num(q["ratio"])), ""]
    ok = all(r["rule"]["ratio"] is not None and r["rule"]["ratio"] >= 2.0 for r in res.values())
    L += ["## Правило §13.75", "", "«Группы — это общее движение рынка»: %s (нужно отношение ≥ 2 на обеих сторонах)." % ("подтверждено описательно" if ok else "не подтверждено"), ""]
    return "\n".join(L) + "\n", ok


def run(root, sides, q):
    res = {side: side_block(root, side) for side in sides}
    text, ok = report(json.loads(json.dumps(res)))
    json.dump(dict(cluster=res, confirmed=ok), open(q("res", "cluster679.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(q("out", "cluster679.md"), "w", encoding="utf-8").write(text)
    return text
