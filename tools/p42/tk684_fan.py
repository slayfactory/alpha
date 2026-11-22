#!/usr/bin/env python3
"""П-42 TK-684: пакеты вида «Веер» вкладки «Эквити» (:8090) — суточные ряды всех клеток перебора TK-674 из летописи.

Использование (srv, только чтение БД; в ml_page_update.sh — под systemd-run, Nice 19): <venv>/bin/python tk684_fan.py <chron.sqlite> <каталог выхода data/fan>
Источник — таблица metric, src LIKE 'tools/p42/tk674%': в extra уже лежат суточные $ (daily, 136 суток по дню входа, ставка 500 $) и метрики клетки;
CSV сделок не читаются, движок не запускается (В-213). Отбор (правила перебора TK-674): стоп patch.s ∈ {1; 1,5; 2; 3; 4} (без s = стоп 5 % и s ∈ {6, 8, 12} — вне базы, В-173);
клетки SHARP_* (порог с заглядыванием вперёд) — вне; одинаковые сделки (extra.trades_sig) в пределах стороны — одна клетка (меньший уровень, затем меньший id).
Уровень = число осей в patch, кроме s, + число фильтров; раздельный вход (e2t / w2 / s2 и e2 при e2t) — одна ось «split». 0 — семья базы (только стоп), 1 — единичка, 2 — пара, 3 — тройка, 4 — четвёрка, 5 — пять осей и больше.
Сверка до записи (расхождение = код 1, файлы не пишутся): Σ daily == usd, Шарп / просадка / перехай, пересчитанные из daily (tk684_metrics), == числам летописи; n_win + n_loss ≤ n.
Выход: index.json (оси, базы, счётчики, размеры) и на каждую сторону и уровень — <L|S>-<уровень>.bin.gz: Int16 суточный P&L в центах × N × days, затем Float32 × N × len(fields), затем Uint32 id строки metric × N;
<L|S>-<уровень>.json.gz: ключи клеток и их оси ([[№ оси, № значения], …] по словарям index.json).
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk684_metrics as mt  # noqa: E402

STOPS = {1.0, 1.5, 2.0, 3.0, 4.0}
SPLIT = ("e2t", "w2", "s2")
ANCHOR = {"long": "s3", "short": "s4"}      # базы сторон: строки tk674_import.py
FIELDS = ["n", "usd", "sharpe", "dd", "gap", "nw", "mw", "nl", "ml", "worst", "split"]
DAYS = 136
LV_MAX = 5      # 5 = «пять осей и больше» (единичные клетки импорта)
SIDE = {"long": "L", "short": "S"}


def fnum(x):
    f = float(x)
    return str(int(f)) if f == int(f) else repr(f)


def axes_of(patch, filt):
    """[(ось, значение)] клетки без стопа; раздельный вход — одна ось split."""
    split = any(k in patch for k in SPLIT)
    out, sp = [], []
    for k in sorted(patch):
        if k == "s":
            continue
        if k in SPLIT or (k == "e2" and "e2t" in patch):
            sp.append(f"{k}={fnum(patch[k])}")
        else:
            out.append((k, fnum(patch[k])))
    if split:
        out.append(("split", " ".join(sp)))
    for f in filt:
        name, val = (str(f[0]), str(f[1])) if isinstance(f, (list, tuple)) else str(f).partition("_")[::2]
        out.append((name, val))
    return out, split


def main():
    db, out_dir = sys.argv[1:3]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT id, side, cell, src, extra FROM metric WHERE src LIKE 'tools/p42/tk674%' AND side IN ('long','short') ORDER BY id").fetchall()
    stat, cells, bad = Counter(), {}, []
    for mid, side, cell, src, extra in rows:
        e = json.loads(extra)
        stat["строк"] += 1
        patch, filt = e.get("patch") or {}, e.get("filt") or e.get("filt_sem") or []
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
        if not e.get("daily") or len(e["daily"]) != DAYS:
            stat["вне: нет суточного ряда"] += 1
            continue
        if not e["n"]:
            stat["вне: нет сделок"] += 1
            continue
        ax, split = axes_of(patch, filt)
        d = np.asarray(e["daily"], dtype=np.float64)
        rr, c = e.get("rr") or {}, {}
        c.update(id=mid, side=side, cell=cell, level=min(len(ax), LV_MAX), axes=ax, s=s, daily=d, sig=e.get("trades_sig") or f"id{mid}", anchor=src.endswith("tk674_import.py") and cell == ANCHOR[side],
                 f=[e["n"], e["usd"], e["sharpe"] if e.get("sharpe") is not None else np.nan, e["maxdd"], (e.get("kpi") or {}).get("gap_longest", np.nan),
                    rr.get("n_win", 0), rr.get("mean_win") or 0.0, rr.get("n_loss", 0), rr.get("mean_loss") or 0.0, (e.get("lmax") or {}).get("worst", np.nan), float(split)])
        # сверка с числами летописи
        m = mt.daily_metrics(d)
        if abs(d.sum() - e["usd"]) > 0.006 or abs(m["dd"] - e["maxdd"]) > 0.006 or (e.get("sharpe") is not None and (m["sharpe"] is None or abs(m["sharpe"] - e["sharpe"]) > 1e-6 * max(1, abs(e["sharpe"])))) \
                or ("kpi" in e and m["gap"] != e["kpi"]["gap_longest"]) or rr.get("n_win", 0) + rr.get("n_loss", 0) > e["n"] or np.abs(d).max() > 327:
            bad.append((mid, cell, round(float(d.sum()), 2), e["usd"], m, e["maxdd"], e.get("sharpe"), (e.get("kpi") or {}).get("gap_longest")))
        k = (side, c["sig"])
        old = cells.get(k)
        if old is None or (c["anchor"], -c["level"], -c["id"]) > (old["anchor"], -old["level"], -old["id"]):
            cells[k] = c
        if old is not None:
            stat["вне: те же сделки (trades_sig)"] += 1
    for k, v in stat.items():
        print(f"{k}: {v}")
    if bad:
        print(f"РАСХОЖДЕНИЕ с летописью: {len(bad)} клеток, первые: {bad[:5]}")
        sys.exit(1)
    os.makedirs(out_dir, exist_ok=True)
    names = sorted({a for c in cells.values() for a, _ in c["axes"]})
    vals = {a: sorted({v for c in cells.values() for x, v in c["axes"] if x == a}, key=lambda v: (0, float(v)) if v.replace(".", "", 1).replace("-", "", 1).isdigit() else (1, v)) for a in names}
    ai = {a: i for i, a in enumerate(names)}
    vi = {a: {v: i for i, v in enumerate(vals[a])} for a in names}
    index = {"days": DAYS, "stake": 500, "margin": 250, "fields": FIELDS, "axes": [{"name": a, "vals": vals[a]} for a in names], "files": {}, "anchor": {}, "stat": dict(stat)}
    for side in ("long", "short"):
        anc = [c for c in cells.values() if c["side"] == side and c["anchor"]]
        assert len(anc) == 1, f"{side}: баз {len(anc)}"
        index["anchor"][side] = {"id": anc[0]["id"], "level": anc[0]["level"], "cell": anc[0]["cell"]}
        for lv in sorted({c["level"] for c in cells.values() if c["side"] == side}):
            cs = sorted((c for c in cells.values() if c["side"] == side and c["level"] == lv), key=lambda c: c["id"])
            cents = np.rint(np.stack([c["daily"] for c in cs]) * 100).astype("<i2")
            body = cents.tobytes() + np.asarray([c["f"] for c in cs], dtype="<f4").tobytes() + np.asarray([c["id"] for c in cs], dtype="<u4").tobytes()
            name = f"{SIDE[side]}-{lv}"
            with open(f"{out_dir}/{name}.bin.gz", "wb") as fh:
                fh.write(gzip.compress(body, 6, mtime=0))
            meta = {"k": [c["cell"] for c in cs], "s": [c["s"] for c in cs], "a": [[[ai[a], vi[a][v]] for a, v in c["axes"]] for c in cs]}
            with open(f"{out_dir}/{name}.json.gz", "wb") as fh:
                fh.write(gzip.compress(json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode(), 6, mtime=0))
            index["files"][name] = {"side": side, "level": lv, "n": len(cs), "bytes": len(body), "gz": os.path.getsize(f"{out_dir}/{name}.bin.gz"), "meta_gz": os.path.getsize(f"{out_dir}/{name}.json.gz"),
                                    "split": int(sum(c["f"][-1] for c in cs))}
            print(f"{name}: клеток {len(cs)}, из них раздельный вход {index['files'][name]['split']}, {len(body) / 1e6:.1f} МБ → gz {index['files'][name]['gz'] / 1e6:.1f} МБ, ключи gz {index['files'][name]['meta_gz'] / 1e6:.2f} МБ")
    index["n"] = len(cells)
    json.dump(index, open(f"{out_dir}/index.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    # выборка для клик-теста: первая, последняя и база каждого файла — числа прямо из летописи
    sample = {}
    for c in cells.values():
        sample.setdefault(f"{SIDE[c['side']]}-{c['level']}", []).append(c)
    chk = {name: [{"id": c["id"], "cell": c["cell"], "f": [None if isinstance(x, float) and np.isnan(x) else x for x in c["f"]], "cum": round(float(np.rint(c["daily"] * 100).sum()) / 100, 2)}
                  for c in (sorted(cs, key=lambda c: c["id"])[:1] + sorted(cs, key=lambda c: c["id"])[-1:] + [c for c in cs if c["anchor"]])] for name, cs in sample.items()}
    json.dump(chk, open(f"{out_dir}/check.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"TK-684 веер: клеток {len(cells)} (лонг {sum(1 for c in cells.values() if c['side'] == 'long')}, шорт {sum(1 for c in cells.values() if c['side'] == 'short')}), осей {len(names)}, сверено с летописью")


if __name__ == "__main__":
    main()
