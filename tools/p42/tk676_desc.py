#!/usr/bin/env python3
"""П-42 §13.74 (TK-676), шаг 2 — описательный разбор базовых сделок обеих сторон, БЕЗ движка и без P&L клеток: время удержания, волатильность на сигнале
(NATR 5 / 15 / 30 / 60 баров и реализованная 5 / 15 / 30 / 60 минут — окна по владельцу 22.11) против неблагоприятной глубины сделки (минутные бары от минуты
входа до минуты выхода). Нужен, чтобы сетка стопа не была слепой: какие коэффициенты вообще достижимы.
  python3 tk676_desc.py            → out/desc676.json, out/desc676.md, out/desc676-<сторона>.npz
Данные: базовые сделки h775 (G-base = готовая база, `/data/p42-tk674/gbase/<окно>-<сторона>/base/…-trades.csv`) в зачётных диапазонах wa / wb / wc и минутные
кэши окон `/data/p42-tk621/<окно>/cache/<SYM>.b42`; сутки ≥ 14.11 и январь–июнь не читаются. Волатильность на минуте `sig_min − 1` (закрытая минутка до сигнала),
как `exit_natr` движка; реализованная — корень суммы квадратов простых 1-минутных доходностей окна, как `rv_of` (`src/breakout/data.rs`).
Глубина (MAE) — по минутным барам от минуты входа до минуты выхода: ориентир для диапазонов, не P&L (внутри минуты порядок цен неизвестен, пропущенный
безубыток после первого тейка глубину не сокращает)."""
from __future__ import annotations

import csv
import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_feat as tf  # noqa: E402
import tk604_stats as st  # noqa: E402

ROOT = os.environ.get("TK676_ROOT", "/data/p42-tk676")
GB = os.environ.get("TK676_GBASE", "/data/p42-tk674/gbase")
CACHE = "/data/p42-tk621/%s/cache/%s.b42"
N_MIN = 80 * 1440
STAKE = 500.0
ENG = {"L": "long", "S": "short"}
WINDOWS = ("wa", "wb", "wc")
PER = (5, 15, 30, 60)
SRC = ["N5", "N15", "N30", "N60", "R5", "R15", "R30", "R60"]     # коды `vs` 1…8
MULT = (1, 2, 3, 4, 6, 8, 12)
FIXED = (1.0, 2.0, 3.0, 4.0, 5.0)                                  # фиксированный стоп, % — для сравнения


def read_b42(path):
    with open(path, "rb") as f:
        buf = f.read()
    if buf[:4] != b"B42\x01":
        raise ValueError("не .b42: " + path)
    n = struct.unpack("<I", buf[4:8])[0]
    if n != N_MIN:
        raise ValueError("%s: n = %d" % (path, n))
    return np.frombuffer(buf, dtype="<i8", count=5 * n, offset=8).reshape(5, n)


def natr_series(o, h, l, c, p):
    """NATR как `natr_of` движка: среднее TR за p баров / закрытие минуты, дыры пропускаются, нужно ≥ ⌈p/2⌉ баров; доля цены (float), −1 — нет значения."""
    n = len(o)
    valid = o != 0
    idx = np.where(valid, np.arange(n), -1)
    last = np.maximum.accumulate(idx)
    prev_idx = np.concatenate([[-1], last[:-1]])
    prevc = np.where(prev_idx >= 0, c[np.maximum(prev_idx, 0)], 0)
    tr = h - l
    tr = np.where(prevc != 0, np.maximum(tr, np.maximum(np.abs(h - prevc), np.abs(l - prevc))), tr)
    tr = np.where(valid, tr, 0)
    cs = np.concatenate([[0], np.cumsum(tr)])
    cnt = np.concatenate([[0], np.cumsum(valid.astype(np.int64))])
    out = np.full(n, -1.0)
    t = np.arange(p - 1, n)
    k = cnt[t + 1] - cnt[t + 1 - p]
    s = cs[t + 1] - cs[t + 1 - p]
    ok = valid[t] & (k * 2 >= p)
    tt = t[ok]
    out[tt] = s[ok] / (k[ok] * c[tt].astype(float))
    return out


def rv_series(o, c, p):
    """Реализованная волатильность как `rv_of`: √Σ r² по p последним доходностям окна (простые, обе минутки есть), нужно ≥ ⌈p/2⌉; доля цены, −1 — нет значения."""
    n = len(o)
    valid = o != 0
    r = np.zeros(n)
    ok = np.zeros(n, dtype=bool)
    ok[1:] = valid[1:] & valid[:-1]
    num = (c[1:] - c[:-1]).astype(float)
    den = np.where(c[:-1] != 0, c[:-1], 1).astype(float)
    r[1:] = np.where(ok[1:], num / den, 0.0)
    sq = np.concatenate([[0.0], np.cumsum(r * r)])
    cnt = np.concatenate([[0], np.cumsum(ok.astype(np.int64))])
    out = np.full(n, -1.0)
    t = np.arange(p, n)
    k = cnt[t + 1] - cnt[t + 1 - p]
    good = valid[t] & (k * 2 >= p)
    tt = t[good]
    out[tt] = np.sqrt(sq[tt + 1] - sq[tt + 1 - p])
    return out


def vol_all(a):
    o, h, l, c = a[0], a[1], a[2], a[3]
    v = [natr_series(o, h, l, c, p) for p in PER] + [rv_series(o, c, p) for p in PER]
    return np.stack(v)


def load_trades(side):
    """Базовые сделки стороны в зачётных диапазонах окон: список записей с монетой, окном и индексами минут окна."""
    rows = []
    for wn in WINDOWS:
        w = st.WINS[wn] if hasattr(st, "WINS") else {x.name: x for x in st.HIST_WINDOWS}[wn]
        f = os.path.join(GB, "%s-%s" % (wn, side), "base", ENG[side] + "-trades.csv")
        with open(f, encoding="utf-8", newline="") as fh:
            raw = list(csv.DictReader(fh))
        ent = np.array([int(r["entry_ms"]) for r in raw], dtype=np.int64) + w.t0_ms
        m = tf.in_range(ent, w)
        for r, keep in zip(raw, m):
            if keep:
                rows.append(dict(w=wn, sym=r["sym"], sig=int(r["sig_min"]), em=int(r["entry_min"]), xm=int(r["exit_min"]), px=int(r["entry_px_e8"]), kind=r["kind"],
                                 net=float(r["net"]), ems=int(r["entry_ms"]), xms=int(r["exit_ms"])))
    return rows


def q(x, ps):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    return [float(np.percentile(x, p)) if len(x) else float("nan") for p in ps]


def hold_table(rows):
    """Время удержания, мин: n, среднее, p25 / p50 / p75 / p90 — все / выигрыши / проигрыши / стоп-выходы."""
    hold = np.array([(r["xms"] - r["ems"]) / 60000.0 for r in rows])
    net = np.array([r["net"] for r in rows])
    kind = np.array([r["kind"] for r in rows])
    out = {}
    for name, m in (("все", np.ones(len(rows), bool)), ("выигрыши", net > 0), ("проигрыши", net < 0), ("стоп-выходы", kind == "stop")):
        x = hold[m]
        out[name] = dict(n=int(m.sum()), mean=float(x.mean()) if len(x) else float("nan"), p25=q(x, [25])[0], p50=q(x, [50])[0], p75=q(x, [75])[0], p90=q(x, [90])[0])
    return out


def spearman(a, b):
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 5:
        return float("nan")
    ra = np.argsort(np.argsort(a[ok])).astype(float)
    rb = np.argsort(np.argsort(b[ok])).astype(float)
    return float(np.corrcoef(ra, rb)[0, 1])


def describe(side):
    rows = load_trades(side)
    n = len(rows)
    cache = {}
    V = np.full((n, 8), np.nan)
    MAE = np.full(n, np.nan)
    for i, r in enumerate(rows):
        key = (r["w"], r["sym"])
        if key not in cache:
            if len(cache) > 40:
                cache.clear()
            a = read_b42(CACHE % (r["w"], r["sym"]))
            cache[key] = (a, vol_all(a))
        a, vv = cache[key]
        t = r["sig"] - 1
        for j in range(8):
            if vv[j][t] >= 0:
                V[i, j] = vv[j][t] * 100.0                          # проценты цены
        e = r["px"] / 1e8
        lo, hi = r["em"], r["xm"]
        seg_l = a[2][lo:hi + 1]
        seg_h = a[1][lo:hi + 1]
        seg_ok = a[0][lo:hi + 1] != 0
        if seg_ok.any():
            if side == "L":
                MAE[i] = max(0.0, (e - float(seg_l[seg_ok].min()) / 1e8) / e * 100.0)
            else:
                MAE[i] = max(0.0, (float(seg_h[seg_ok].max()) / 1e8 - e) / e * 100.0)
    net = np.array([r["net"] for r in rows])
    kind = np.array([r["kind"] for r in rows])
    hold = np.array([(r["xms"] - r["ems"]) / 60000.0 for r in rows])
    win, loss = net > 0, net < 0
    res = dict(side=side, n=n, hold=hold_table(rows), vstat={}, surv={}, fixed={}, corr={}, mae={})
    res["mae"] = {nm: dict(mean=float(np.nanmean(MAE[m])), q50=q(MAE[m], [50])[0], q75=q(MAE[m], [75])[0], q90=q(MAE[m], [90])[0], q95=q(MAE[m], [95])[0], n=int(m.sum()))
                  for nm, m in (("выигрыши", win), ("проигрыши", loss))}
    for j, nm in enumerate(SRC):
        v = V[:, j]
        res["vstat"][nm] = dict(valid=int(np.isfinite(v).sum()), mean=float(np.nanmean(v)), q10=q(v, [10])[0], q50=q(v, [50])[0], q90=q(v, [90])[0])
        sv, sf = {}, {}
        for nm2, m in (("выигрыши", win), ("проигрыши", loss)):
            ratio = MAE[m] / v[m]
            ok = np.isfinite(ratio)
            sv[nm2] = {str(k): (float((ratio[ok] <= k).mean()) if ok.any() else float("nan")) for k in MULT}
            sv[nm2]["q50"], sv[nm2]["q90"] = q(ratio, [50])[0], q(ratio, [90])[0]
        res["surv"][nm] = sv
        res["corr"][nm] = dict(win=spearman(v[win], MAE[win]), loss=spearman(v[loss], MAE[loss]), all=spearman(v, MAE))
    for nm2, m in (("выигрыши", win), ("проигрыши", loss)):
        res["fixed"][nm2] = {str(d): float((MAE[m] <= d).mean()) for d in FIXED}
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    np.savez(os.path.join(ROOT, "out", "desc676-%s.npz" % side), V=V, MAE=MAE, net=net, kind=kind, hold=hold, sym=np.array([r["sym"] for r in rows]),
             w=np.array([r["w"] for r in rows]), sig=np.array([r["sig"] for r in rows]), src=np.array(SRC))
    return res


def md(R):
    L = ["# TK-676 — описательный разбор базовых сделок (шаг 2, без движка)", "",
         "Окно 01.07–13.11, h775, зачётные диапазоны wa / wb / wc. Найдено на этих данных, ждёт январь–июнь. Глубина — по минутным барам: ориентир для диапазонов, не P&L.", ""]
    for side in ("L", "S"):
        r = R[side]
        L += ["## %s — сделок %d" % ("лонг" if side == "L" else "шорт", r["n"]), "", "### Время удержания, минуты", "", "| группа | сделок | среднее | p25 | p50 | p75 | p90 |", "|---|---|---|---|---|---|---|"]
        for g, x in r["hold"].items():
            L.append("| %s | %d | %.1f | %.1f | %.1f | %.1f | %.1f |" % (g, x["n"], x["mean"], x["p25"], x["p50"], x["p75"], x["p90"]))
        L += ["", "### Волатильность на сигнале, % цены (закрытая минутка до сигнала)", "", "| источник | есть значение | среднее | q10 | q50 | q90 | ρ(V, глубина) выигрыши | проигрыши |", "|---|---|---|---|---|---|---|---|"]
        for nm in SRC:
            v, c = r["vstat"][nm], r["corr"][nm]
            L.append("| %s | %d | %.3f | %.3f | %.3f | %.3f | %.2f | %.2f |" % (nm, v["valid"], v["mean"], v["q10"], v["q50"], v["q90"], c["win"], c["loss"]))
        L += ["", "### Глубина против хода, % цены входа", "", "| группа | сделок | среднее | q50 | q75 | q90 | q95 |", "|---|---|---|---|---|---|---|"]
        for g, x in r["mae"].items():
            L.append("| %s | %d | %.2f | %.2f | %.2f | %.2f | %.2f |" % (g, x["n"], x["mean"], x["q50"], x["q75"], x["q90"], x["q95"]))
        L += ["", "Доля с глубиной ≤ фиксированного стопа: " + "; ".join("%s %% — выигрыши %.0f %% / проигрыши %.0f %%" % (d, 100 * r["fixed"]["выигрыши"][d], 100 * r["fixed"]["проигрыши"][d]) for d in map(str, FIXED)), "",
              "### Доля выигрышей с глубиной ≤ m·V (какой множитель волатильности им нужен)", "", "| источник | " + " | ".join("m=%d" % k for k in MULT) + " | q50 | q90 |", "|---|" + "---|" * (len(MULT) + 2)]
        for nm in SRC:
            s = r["surv"][nm]["выигрыши"]
            L.append("| %s | " % nm + " | ".join("%.0f %%" % (100 * s[str(k)]) for k in MULT) + " | %.1f | %.1f |" % (s["q50"], s["q90"]))
        L += ["", "### Доля проигрышей с глубиной ≤ m·V", "", "| источник | " + " | ".join("m=%d" % k for k in MULT) + " | q50 | q90 |", "|---|" + "---|" * (len(MULT) + 2)]
        for nm in SRC:
            s = r["surv"][nm]["проигрыши"]
            L.append("| %s | " % nm + " | ".join("%.0f %%" % (100 * s[str(k)]) for k in MULT) + " | %.1f | %.1f |" % (s["q50"], s["q90"]))
        L.append("")
    return "\n".join(L)


def main():
    R = {s: describe(s) for s in ("L", "S")}
    out = os.path.join(ROOT, "out")
    os.makedirs(out, exist_ok=True)
    json.dump(R, open(os.path.join(out, "desc676.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(os.path.join(out, "desc676.md"), "w", encoding="utf-8").write(md(R))
    print(md(R))


if __name__ == "__main__":
    main()
