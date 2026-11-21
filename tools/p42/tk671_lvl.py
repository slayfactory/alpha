#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), шаг lvl: дампы сигналов движка (`P42_SIGSX`, без фильтров) по источникам уровней → строки-эпизоды подхода + таблицы `ftab` для разметки меток.

  python3 tk671_lvl.py build --side long|short --win wa|wb|wc --dumps <каталог с <источник>/<side>-sigsx.csv> --out <каталог> [--gap-min 60] [--sources p30n1,...]

Эпизод = первый сигнал уровня и каждый следующий, если с прошлого сигнала ЭТОГО уровня прошло > `gap-min` минут (§13.71 п. 3). Строки окна ограничены зачётными сутками окна (lo…hi, `tk604_stats`):
сигналы раньше — только для разметки эпизодов (состояние уровня), строки не создают. Одинаковые `(sym, sig_ms, level_px_e8)` из разных источников — одна строка (маска источников, метка — по первому источнику
в порядке `SOURCES`). Выход: `rows.parquet` (строки), `ftab-<источник>.csv` (`sym,sig_ms,ROW`: 1.0 у строк, закреплённых за источником, 0.0 у ВСЕХ остальных его сигналов, в т.ч. вне зачётных суток: сигнал без строки в таблице движок пропускает), `lvl.json` (счётчики).
Время в файлах — мс от T0 окна (как в дампе движка); абсолютные мс — столбец `abs_ms`."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SOURCES = ["p30n1", "p30n2", "p30n3", "p60n1", "p60n2", "p60n3", "p240n1", "p240n2", "p240n3", "lxD", "lxW", "lxP"]
MIN_MS, DAY_MS = 60_000, 86_400_000
COLS = ["sym", "level", "sig_ms", "dist_e8", "level_px_e8", "natr_e8", "born", "end", "first_bar", "members"]
KEEP = ["src", "sym", "level", "sig_ms", "dist_e8", "level_px_e8", "natr_e8", "born", "end", "first_bar", "members", "n_ep"]


def read_sigsx(path):
    t = pacsv.read_csv(path, convert_options=pacsv.ConvertOptions(include_columns=COLS, column_types={"sym": pa.string(), "members": pa.string()}))
    return t.to_pandas()


def mark_episodes(df, gap_ms):
    """df — один источник; возвращает df с `ep` (начало эпизода) и `n_ep` (число сигналов эпизода у начала), порядок — как в файле дампа сохраняется через `pos`."""
    d = df.copy()
    d["pos"] = np.arange(len(d), dtype=np.int64)
    d = d.sort_values(["sym", "level", "sig_ms"], kind="stable")
    same = (d["sym"].to_numpy()[1:] == d["sym"].to_numpy()[:-1]) & (d["level"].to_numpy()[1:] == d["level"].to_numpy()[:-1])
    gap = np.diff(d["sig_ms"].to_numpy())
    start = np.ones(len(d), dtype=bool)
    start[1:] = ~(same & (gap <= gap_ms))
    d["ep"] = start
    d["ep_id"] = np.cumsum(start)
    d["n_ep"] = d.groupby("ep_id")["sig_ms"].transform("size")
    return d.sort_values("pos", kind="stable")


def build(side, win, dumps, out, gap_min=60, sources=None):
    import tk604_stats as st
    w = {x.name: x for x in st.TRADE_WINDOWS}[win]
    st.check_dates(w.t0, w.hi)
    lo_ms = (w.lod - w.t0d).days * DAY_MS
    hi_ms = ((w.hid - w.t0d).days + 1) * DAY_MS          # исключая
    gap_ms = gap_min * MIN_MS
    os.makedirs(out, exist_ok=True)
    parts, stat, per = [], {"side": side, "win": win, "gap_min": gap_min, "lo_ms": lo_ms, "hi_ms": hi_ms, "sources": {}}, {}
    for si, src in enumerate(sources or SOURCES):
        path = os.path.join(dumps, src, f"{side}-sigsx.csv")
        df = mark_episodes(read_sigsx(path), gap_ms)
        inwin = ((df["sig_ms"] >= lo_ms) & (df["sig_ms"] < hi_ms)).to_numpy()
        rows = df[df["ep"].to_numpy() & inwin].copy()
        rows["src"] = si
        parts.append(rows[KEEP])
        per[src] = df[["sym", "sig_ms"]]
        stat["sources"][src] = {"signals": int(len(df)), "signals_win": int(inwin.sum()), "levels": int(df.groupby(["sym", "level"]).ngroups), "episodes_win": int(len(rows))}
    allr = pa.concat_tables([pa.Table.from_pandas(p, preserve_index=False) for p in parts]).to_pandas()
    # дедупликация: одна строка на (sym, sig_ms, level_px_e8); метка — по первому источнику; маска — все источники
    allr["bit"] = np.left_shift(1, allr["src"].to_numpy(dtype=np.int64))
    g = allr.groupby(["sym", "sig_ms", "level_px_e8"], sort=False)
    first = g["src"].idxmin()
    mask = g["bit"].sum()
    rows = allr.loc[first.to_numpy()].reset_index(drop=True)
    rows["mask"] = mask.to_numpy()
    rows["abs_ms"] = rows["sig_ms"].astype(np.int64) + w.t0_ms
    rows["side"] = side
    rows["win"] = win
    rows = rows.drop(columns=["bit"]).sort_values(["sig_ms", "dist_e8", "sym", "level"], kind="stable").reset_index(drop=True)
    rows.insert(0, "row_id", np.arange(len(rows), dtype=np.int64))          # ключ строки внутри (окно, сторона): порядок файла
    pq.write_table(pa.Table.from_pandas(rows, preserve_index=False), os.path.join(out, "rows.parquet"), compression="zstd")
    # ftab: 1.0 у строк, закреплённых за источником (по ключу (sym, sig_ms, level)), 0.0 у остальных сигналов окна
    stat["rows"] = int(len(rows)); stat["rows_before_dedup"] = int(len(allr))
    for si, src in enumerate(sources or SOURCES):
        sel = rows[rows["src"] == si]
        sig = per[src]
        keys = set(zip(sel["sym"].tolist(), sel["sig_ms"].tolist()))
        v = np.fromiter(((s, m) in keys for s, m in zip(sig["sym"].tolist(), sig["sig_ms"].tolist())), dtype=bool, count=len(sig))
        tab = sig.assign(ROW=np.where(v, "1.0", "0.0")).drop_duplicates(["sym", "sig_ms"])
        tab.to_csv(os.path.join(out, f"ftab-{src}.csv"), index=False)
        stat["sources"][src]["rows_assigned"] = int(len(sel))
        stat["sources"][src]["ftab_rows"] = int(len(tab))
    with open(os.path.join(out, "lvl.json"), "w", encoding="utf-8") as f:
        json.dump(stat, f, ensure_ascii=False, indent=1)
    return stat


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--side", required=True, choices=["long", "short"])
    b.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    b.add_argument("--dumps", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--gap-min", type=int, default=60)
    b.add_argument("--sources")
    a = ap.parse_args()
    s = build(a.side, a.win, a.dumps, a.out, a.gap_min, a.sources.split(",") if a.sources else None)
    print(json.dumps({k: v for k, v in s.items() if k != "sources"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
