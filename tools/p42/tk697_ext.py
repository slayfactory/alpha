#!/usr/bin/env python3
"""П-42 TK-697: столбцы набора `t697` для сигналов базы, которых нет в таблице TK-671 (критик 23.11, FIX 2).

Таблица TK-671 держит по строке на уровень и эпизод — первый сигнал эпизода; следующие сигналы того же уровня (`ep_no` > 1, тот же `sym` и `lp_e8`) строк в ней не имеют
(wb / wc: 35–38 % сигналов базы), и у них любой столбец таблицы пуст. Здесь строки собираются из эпизодов базы h775 (`/data/p42-tk634/feat/h775/<окно>-<L|S>/eps-<L|S>.parquet`: `sym`, `sig_ms`, `level`, `lp_e8`)
по ключу (`sym`, `sig_ms`) и считаются теми же функциями, что и основные семейства: `m697`, `h697` (только минутки и дневные свечи), `mk_rel_s60 / s240 / s1440` (`tk671_mkt`) и `l697`.
Источник дампа и номер уровня для `l697` — `src` и `level` строки таблицы с той же ценой уровня (`sym`, `level_px_e8` = `lp_e8`) и сигналом не позже (`eps.level` — нумерация базы h775, с дампом не связана);
нет такой строки — `src = −1`, столбцы `l697` пусты (NaN). Монеты без ряда минуток в кэше (TradFi вне `c539`) — все столбцы по минуткам пусты.

  python3 tk697_ext.py --win wb --side long --out /data/p42-tk697/fam/ext/wb-long.parquet [--procs 2]

`row_id` строк — отрицательные (−1, −2, …), чтобы не пересекаться с таблицей. Результат: `row_id, sym, sig_ms, src` + 43 столбца набора (порядок `tk680_axes.T697_NAMES`)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FEAT34 = "/data/p42-tk634/feat"
PROD = "/data/p42-tk671/prod"
DAILY = "/data/p42-tk697/daily/daily.parquet"
SIDE_L = {"long": "L", "short": "S"}
REL = ["mk_rel_s60", "mk_rel_s240", "mk_rel_s1440"]


def ext_rows(eps, tab, win, side, which="missing"):
    """Строки сигналов базы без строки таблицы (`which="missing"`) или все сигналы базы (`"all"`, для самопроверки). `eps` — sym, sig_ms, level, lp_e8; `tab` — row_id, src, sym, level, sig_ms,
    level_px_e8, abs_ms, base_pass. Возвращает DataFrame row_id (< 0), sym, sig_ms, level, level_px_e8, abs_ms, src, win, side."""
    import pandas as pd
    off = np.unique((tab["abs_ms"] - tab["sig_ms"]).to_numpy(np.int64))
    if len(off) != 1:
        raise SystemExit("abs_ms − sig_ms не постоянна в таблице TK-671: %s" % off[:5])
    tb = tab[tab["base_pass"] > 0]
    key = tb[["sym", "sig_ms"]].drop_duplicates()
    m = eps[["sym", "sig_ms", "level", "lp_e8"]].merge(key, on=["sym", "sig_ms"], how="left", indicator=True)
    if which == "missing":
        m = m[m["_merge"] == "left_only"]
    m = m.drop(columns="_merge").drop_duplicates(["sym", "sig_ms"]).sort_values("sig_ms", kind="stable").reset_index(drop=True)
    m["level_px_e8"] = m["lp_e8"].astype(np.int64)
    sib = tb[["sym", "level", "level_px_e8", "sig_ms", "src", "base_pass"]].rename(columns={"sig_ms": "sib_ms", "level": "sib_level"})
    sib["_pri"] = (sib["base_pass"] != 1).astype(np.int8)       # при равных минутах — строка с битом 1 (как `add_columns`)
    sib = sib.sort_values(["sib_ms", "_pri"], ascending=[True, False], kind="stable").drop(columns=["base_pass", "_pri"])
    sib["level_px_e8"] = sib["level_px_e8"].abs().astype(np.int64)
    m["level_px_e8"] = m["level_px_e8"].abs()
    b = pd.merge_asof(m.sort_values("sig_ms", kind="stable"), sib, left_on="sig_ms", right_on="sib_ms", by=["sym", "level_px_e8"], direction="backward")
    b = b.sort_values("sig_ms", kind="stable").reset_index(drop=True)
    b["src"] = b["src"].fillna(-1).astype(np.int64)
    b["level"] = b["sib_level"].fillna(-1).astype(np.int64)      # номер уровня в дампе источника (`eps.level` — нумерация базы h775, с дампом не связана)
    b["abs_ms"] = b["sig_ms"].astype(np.int64) + int(off[0])
    b["row_id"] = -(np.arange(len(b), dtype=np.int64) + 1)
    b["win"], b["side"] = win, side
    return b[["row_id", "sym", "sig_ms", "level", "level_px_e8", "abs_ms", "src", "win", "side"]]


def compute(win, side, procs=2, which="missing"):
    import pandas as pd
    import tk671_mkt as mkt
    import tk697_cols as m6
    import tk697_hl as hl
    import tk697_lv as lv
    sl = SIDE_L[side]
    eps = pd.read_parquet(os.path.join(FEAT34, "h775", "%s-%s" % (win, sl), "eps-%s.parquet" % sl), columns=["sym", "sig_ms", "level", "lp_e8"])
    tab = pd.read_parquet(os.path.join(PROD, "lvl-%s-%s" % (win, side), "rows.parquet"), columns=["row_id", "src", "sym", "level", "sig_ms", "level_px_e8", "abs_ms", "base_pass"])
    rows = ext_rows(eps, tab, win, side, which)
    info = dict(win=win, side=side, which=which, eps=int(len(eps)), rows=int(len(rows)), src_borrowed=int((rows["src"] >= 0).sum()))
    t0 = time.time()
    m = m6.compute(rows, win, side, procs=procs)
    h = hl.compute(rows, pd.read_parquet(DAILY), win, side)
    k = mkt.compute(rows, win, side, procs=procs)[REL]
    rl = rows[rows["src"] >= 0]
    if len(rl):
        lcols = lv.compute(rl, win, side, procs=procs)
    else:
        lcols = pd.DataFrame(columns=list(lv.COLUMNS), dtype=np.float64)
    lcols = lcols.reindex(rows["row_id"].to_numpy(np.int64))
    out = pd.concat([m, h, lcols, k], axis=1)
    out = out[[c for c in m.columns] + [c for c in h.columns] + [c for c in lv.COLUMNS] + REL]
    res = rows[["row_id", "sym", "sig_ms", "src"]].set_index("row_id").join(out)
    info["seconds"] = round(time.time() - t0, 1)
    info["non_nan_share"] = {c: round(float(np.isfinite(res[c]).mean()), 4) if len(res) else 0.0 for c in out.columns}
    return res, info


CACHE = "/data/p42-tk621/%s/cache"


def check(res, win, side):
    """Самопроверка (`--which all`): у сигналов, которые есть в таблице TK-671, значения совпадают с основными семействами `fam/<семейство>/<окно>-<сторона>.parquet` и столбцами таблицы `mk_rel_s*`."""
    import pandas as pd
    import tk680_axes as ax
    tab = pd.read_parquet(os.path.join(PROD, "lvl-%s-%s" % (win, side), "rows.parquet"), columns=["row_id", "sym", "sig_ms", "base_pass"])
    tab = tab[tab["base_pass"] > 0].drop_duplicates(["sym", "sig_ms"]).set_index("row_id")
    j = res.reset_index().merge(tab.reset_index(), on=["sym", "sig_ms"], how="inner", suffixes=("_e", ""))
    j = j[[os.path.exists(os.path.join(CACHE % win, "%s.b42" % x)) for x in j["sym"]]].reset_index(drop=True)      # TradFi без минуток (вне c539): столбцы таблицы TK-671 у них из другого источника
    bad, tot = {}, 0
    for fam_name, cols in ax.T697_FAMS.items():
        x = pd.read_parquet(ax.T697_FAM % (fam_name, win, side)).loc[j["row_id"].to_numpy()]
        for c in cols:
            u, v = j[c].to_numpy(np.float64), x[c].to_numpy(np.float64)
            bad[c] = int((~((u == v) | (np.isnan(u) & np.isnan(v)) | (np.abs(u - v) <= 1e-9 * (1 + np.abs(v))))).sum())
    tb = pd.read_parquet("/data/p42-tk671/data/%s.parquet" % side, columns=["win", "row_id"] + REL)
    tb = tb[tb["win"].astype(str) == win].set_index("row_id").loc[j["row_id"].to_numpy()]
    for c in REL:
        u, v = j[c].to_numpy(np.float64), tb[c].to_numpy(np.float64)
        bad[c] = int((~((u == v) | (np.isnan(u) & np.isnan(v)) | (np.abs(u - v) <= 1e-9 * (1 + np.abs(v))))).sum())
    tot = int(len(j))
    return dict(matched=tot, bad_by_col={c: n for c, n in bad.items() if n}, ok=bool(tot > 0 and not any(bad.values())))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--which", default="missing", choices=["missing", "all"])
    a = ap.parse_args()
    res, info = compute(a.win, a.side, a.procs, a.which)
    if a.which == "all":
        info["check"] = check(res, a.win, a.side)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    res.to_parquet(a.out)
    with open(a.out + ".info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in info.items() if k != "non_nan_share"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
