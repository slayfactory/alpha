#!/usr/bin/env python3
"""П-42 TK-684 / TK-696: пакеты вида «Веер» вкладки «Эквити» (:8090) — суточные ряды всех клеток перебора TK-674 на составе c539 из летописи.

Использование (srv, только чтение БД; в ml_page_update.sh — под systemd-run, Nice 19): <venv>/bin/python tk684_fan.py <chron.sqlite> <каталог выхода data/fan>
Источник — таблица metric, uni = 'c539', src LIKE 'tools/p42/tk674%', окно 01.07–13.11: в extra уже лежат суточные $ (daily, 136 суток по дню входа, ставка 500 $) и метрики клетки;
CSV сделок не читаются, движок не запускается (В-213). Отбор (правила перебора TK-674): стоп patch.s ∈ {1; 1,5; 2; 3; 4} (без s = стоп 5 % и s ∈ {6, 8, 12} — вне базы, В-173);
клетки SHARP_* (порог с заглядыванием вперёд) — вне; клетки с фильтром bd_* на прежних таблицах — вне (PS-993 (б), L-384: не читаются до пересчёта PS-996; их строки TK690-xb / TK690-recount входят и заменяют прежние);
одинаковые сделки (extra.trades_sig) в пределах стороны — одна клетка (база, затем клетки вариантов `chron`, затем меньший уровень, затем меньший id).
Уровень = число осей клетки, кроме s (плюс фильтры); составная ось считается один раз: split (e2t / w2 / s2 и e2 при них), e1t (+ e2), cw (cl, ch, cm), sg (sb, sr, sd), nf (+ natr_min), ev (ea, el, eh), sm (+ sl, nx, cw-семья).
0 — семья базы (только стоп), 1 — единичка, 2 — пара, 3 — тройка, 4 — четвёрка, 5 — пять осей и больше. База — строка entry = 334 (L-334, якоря c539).
Сверка до записи (расхождение = код 1, файлы не пишутся): Σ daily == usd, Шарп / просадка / перехай, пересчитанные из daily (tk684_metrics), == числам летописи; n_win + n_loss ≤ n.
Выход: index.json (оси, базы, счётчики, размеры, файлы) и на каждую сторону и уровень — один или несколько файлов по ≤ PART клеток (TK-696, 25.11: четвёрки шорта ступеней 1–3 — миллион и больше клеток):
<L|S>-<уровень>[-<часть>].bin.gz — Int16 суточный P&L в центах × N × days, затем Float32 × N × len(fields) (n, usd, sharpe, dd, gap, nw, mw, nl, ml, worst, split, mn, mx — минимум и максимум накопленного ряда, $), затем UInt32 id строки metric × N,
затем UInt16 пары (№ оси, № значения) всех клеток подряд и UInt8 число пар у каждой клетки; <имя>.json.gz — {"k": ключи клеток}. Клетки внутри уровня идут по возрастанию id, части — подряд (idmin / idmax в index.json).
Память: суточные ряды держатся целыми центами (int16), не float64; сверка с летописью — по float до записи.
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import sys
from collections import Counter
from sys import intern

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk684_metrics as mt  # noqa: E402

UNI, WIN, ANCHOR_ENTRY = "c539", "01.07–13.11", 334
RECOUNT = ("TK690-recount", "TK690-xb")     # пересчёт PS-994 / PS-995: строки заменяют прежние строки тех же клеток (контроли K1 / K3 / all — дубли, не берём)
STOPS = {1.0, 1.5, 2.0, 3.0, 4.0}
SPLIT = ("e2t", "w2", "s2")
ANCHOR = {"long": "s3", "short": "s4"}      # базы сторон (L-334)
# составные оси: ключи, которые ось задаёт вместе (ось → (ключи-родители, зависимые ключи)); порядок — от большей к меньшей
FAMILY = (("sm", {"sm"}, {"sl", "nx", "cw", "cl", "ch", "cm"}), ("nf", {"nf"}, {"natr_min"}), ("ev", {"ev"}, {"ea", "el", "eh"}), ("cw", {"cw"}, {"cl", "ch", "cm"}),
          ("sg", {"sg"}, {"sb", "sr", "sd"}), ("e1t", {"e1t"}, {"e2"}), ("split", set(SPLIT), {"e2"}))
FIELDS = ["n", "usd", "sharpe", "dd", "gap", "nw", "mw", "nl", "ml", "worst", "split", "mn", "mx"]
DAYS = 136
LV_MAX = 5      # 5 = «пять осей и больше» (единичные клетки импорта)
SIDE = {"long": "L", "short": "S"}
PART = 150_000      # клеток в файле: пакет ≈ 50 МБ распакованным, браузер грузит части параллельно


def fnum(x):
    f = float(x)
    return str(int(f)) if f == int(f) else repr(f)


def axes_of(patch, filt):
    """[(ось, значение)] клетки без стопа; составная ось (FAMILY) — одна ось со значением «ключ=значение …»; split — флаг раздельного входа."""
    split = any(k in patch for k in SPLIT)
    rest, out = {k: v for k, v in patch.items() if k != "s"}, []
    for name, parents, deps in FAMILY:
        if parents & rest.keys():
            used = sorted((parents | deps) & rest.keys())
            out.append((name, " ".join(f"{k}={fnum(rest[k])}" for k in used)))
            for k in used:
                del rest[k]
    out += [(k, fnum(v)) for k, v in sorted(rest.items())]
    for f in filt:
        name, val = (str(f[0]), str(f[1])) if isinstance(f, (list, tuple)) else str(f).partition("_")[::2]
        out.append((name, val))
    return out, split


def keep_cells():
    """{(сторона, клетка)} вариантов страницы (поле chron в tk684_variants.json рядом): при одинаковых сделках остаётся клетка варианта."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tk684_variants.json")
    return {(v["side"], v["chron"]) for v in json.load(open(p, encoding="utf-8"))["variants"] if v.get("chron")} if os.path.exists(p) else set()


class Cell:
    """Клетка веера; суточный ряд — целые центы (int16), метрики — float32 в порядке FIELDS."""
    __slots__ = ("id", "side", "cell", "level", "axes", "cents", "sig", "anchor", "keep", "f")

    def rank(self):
        return (self.anchor, self.keep, -self.level, -self.id)


def cum_range(cents):
    """(минимум, максимум) накопленного ряда в $ — со стартом 0, как рисует холст."""
    c = np.cumsum(cents.astype(np.int64))
    return min(0, int(c.min())) / 100, max(0, int(c.max())) / 100


def main():
    db, out_dir = sys.argv[1:3]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    keep = keep_cells()
    rec = {(side, cell) for side, cell in con.execute("SELECT side, cell FROM metric WHERE uni = ? AND win IN (?, ?) AND side IN ('long','short')", (UNI,) + RECOUNT)}
    stat, cells, bad = Counter(), {}, []
    for mid, entry, side, cell, win, src, extra in con.execute("SELECT id, entry, side, cell, win, src, extra FROM metric WHERE uni = ? AND win IN (?, ?, ?) AND src LIKE 'tools/p42/tk674%' AND side IN ('long','short') ORDER BY id", (UNI, WIN) + RECOUNT):
        e = json.loads(extra)
        stat["строк"] += 1
        patch, filt = e.get("patch") or {}, e.get("filt") or e.get("filt_sem") or []
        if win == WIN and (side, cell) in rec:
            stat["вне: заменены строкой пересчёта (TK690)"] += 1
            continue
        if "s" not in patch:
            stat["вне: стоп 5 % (нет s)"] += 1
            continue
        s = float(patch["s"])
        if s not in STOPS:
            stat["вне: стоп 6 / 8 / 12 %"] += 1
            continue
        if "SHARP" in cell or any("SHARP" in str(f) for f in filt):
            stat["вне: SHARP_*"] += 1
            continue
        if win == WIN and any(str(f[0] if isinstance(f, (list, tuple)) else f).startswith("bd_") for f in filt):
            stat["вне: bd_* на прежних таблицах (PS-996)"] += 1
            continue
        if not e.get("daily") or len(e["daily"]) != DAYS:
            stat["вне: нет суточного ряда"] += 1
            continue
        if not e["n"]:
            stat["вне: нет сделок"] += 1
            continue
        ax, split = axes_of(patch, filt)
        d = np.asarray(e["daily"], dtype=np.float64)
        rr = e.get("rr") or {}
        # сверка с числами летописи
        m = mt.daily_metrics(d)
        if abs(d.sum() - e["usd"]) > 0.006 or abs(m["dd"] - e["maxdd"]) > 0.006 or (e.get("sharpe") is not None and (m["sharpe"] is None or abs(m["sharpe"] - e["sharpe"]) > 1e-6 * max(1, abs(e["sharpe"])))) \
                or ("kpi" in e and m["gap"] != e["kpi"]["gap_longest"]) or rr.get("n_win", 0) + rr.get("n_loss", 0) > e["n"] or np.abs(d).max() > 327:
            bad.append((mid, cell, round(float(d.sum()), 2), e["usd"], m, e["maxdd"], e.get("sharpe"), (e.get("kpi") or {}).get("gap_longest")))
            continue
        cents = np.rint(d * 100).astype("<i2")
        mn, mx = cum_range(cents)
        c = Cell()
        c.id, c.side, c.cell, c.level, c.axes = mid, side, cell, min(len(ax), LV_MAX), tuple((intern(a), intern(v)) for a, v in ax)
        c.cents, c.sig, c.anchor, c.keep = cents, e.get("trades_sig") or f"id{mid}", entry == ANCHOR_ENTRY and cell == ANCHOR[side], (side, cell) in keep
        c.f = np.asarray([e["n"], e["usd"], e["sharpe"] if e.get("sharpe") is not None else np.nan, e["maxdd"], (e.get("kpi") or {}).get("gap_longest", np.nan),
                          rr.get("n_win", 0), rr.get("mean_win") or 0.0, rr.get("n_loss", 0), rr.get("mean_loss") or 0.0, (e.get("lmax") or {}).get("worst", np.nan), float(split), mn, mx], dtype="<f4")
        k = (side, c.sig)
        old = cells.get(k)
        if old is None or c.rank() > old.rank():
            cells[k] = c
        if old is not None:
            stat["вне: те же сделки (trades_sig)"] += 1
    for k, v in stat.items():
        print(f"{k}: {v}")
    if bad:
        print(f"РАСХОЖДЕНИЕ с летописью: {len(bad)} клеток, первые: {bad[:5]}")
        sys.exit(1)
    os.makedirs(out_dir, exist_ok=True)
    names = sorted({a for c in cells.values() for a, _ in c.axes})
    vals = {a: set() for a in names}
    for c in cells.values():
        for a, v in c.axes:
            vals[a].add(v)
    vals = {a: sorted(vals[a], key=lambda v: (0, float(v)) if v.replace(".", "", 1).replace("-", "", 1).isdigit() else (1, v)) for a in names}
    ai = {a: i for i, a in enumerate(names)}
    vi = {a: {v: i for i, v in enumerate(vals[a])} for a in names}
    assert len(names) < 65536 and all(len(v) < 65536 for v in vals.values()), "оси или значения не помещаются в UInt16"
    index = {"days": DAYS, "stake": 500, "margin": 250, "fields": FIELDS, "axes": [{"name": a, "vals": vals[a]} for a in names], "files": {}, "anchor": {}, "stat": dict(stat), "part": PART}
    sample = {}
    for side in ("long", "short"):
        anc = [c for c in cells.values() if c.side == side and c.anchor]
        assert len(anc) == 1, f"{side}: баз {len(anc)}"
        index["anchor"][side] = {"id": anc[0].id, "level": anc[0].level, "cell": anc[0].cell}
        for lv in sorted({c.level for c in cells.values() if c.side == side}):
            cs = sorted((c for c in cells.values() if c.side == side and c.level == lv), key=lambda c: c.id)
            parts = [cs[i:i + PART] for i in range(0, len(cs), PART)]
            for pi, pc in enumerate(parts):
                name = f"{SIDE[side]}-{lv}" + (f"-{pi}" if len(parts) > 1 else "")
                pairs = np.asarray([x for c in pc for a, v in c.axes for x in (ai[a], vi[a][v])], dtype="<u2")
                counts = np.asarray([len(c.axes) for c in pc], dtype="u1")
                body = np.stack([c.cents for c in pc]).tobytes() + np.stack([c.f for c in pc]).tobytes() + np.asarray([c.id for c in pc], dtype="<u4").tobytes() + pairs.tobytes() + counts.tobytes()
                with open(f"{out_dir}/{name}.bin.gz", "wb") as fh:
                    fh.write(gzip.compress(body, 6, mtime=0))
                meta = {"k": [c.cell for c in pc]}
                with open(f"{out_dir}/{name}.json.gz", "wb") as fh:
                    fh.write(gzip.compress(json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode(), 6, mtime=0))
                index["files"][name] = {"side": side, "level": lv, "n": len(pc), "pairs": int(len(pairs) // 2), "bytes": len(body), "gz": os.path.getsize(f"{out_dir}/{name}.bin.gz"), "meta_gz": os.path.getsize(f"{out_dir}/{name}.json.gz"),
                                        "split": int(sum(c.f[10] for c in pc)), "idmin": pc[0].id, "idmax": pc[-1].id}
                sample[name] = [pc[0], pc[-1]] + [c for c in pc if c.anchor]
                print(f"{name}: клеток {len(pc)}, из них раздельный вход {index['files'][name]['split']}, {len(body) / 1e6:.1f} МБ → gz {index['files'][name]['gz'] / 1e6:.1f} МБ, ключи gz {index['files'][name]['meta_gz'] / 1e6:.1f} МБ", flush=True)
    index["n"] = len(cells)
    json.dump(index, open(f"{out_dir}/index.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    # выборка для клик-теста: первая, последняя и база каждого файла — числа прямо из летописи
    chk = {name: [{"id": c.id, "cell": c.cell, "f": [None if np.isnan(x) else float(x) for x in c.f], "cum": round(float(c.cents.astype(np.int64).sum()) / 100, 2)} for c in cs] for name, cs in sample.items()}
    json.dump(chk, open(f"{out_dir}/check.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"TK-684 веер: клеток {len(cells)} (лонг {sum(1 for c in cells.values() if c.side == 'long')}, шорт {sum(1 for c in cells.values() if c.side == 'short')}), осей {len(names)}, сверено с летописью")


if __name__ == "__main__":
    main()
