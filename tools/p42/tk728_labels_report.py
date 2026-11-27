#!/usr/bin/env python3
"""П-42 этап A, шаг 4в (TK-728, §13.78.4): метки `y` (ход 1 % цены) и `y_n` (ход 1 NATR) — проверка метода до чтения, затем доли по третям V и согласие меток. srv.
Порядок (протокол, «проверка метода до чтения»): (1) `y` равна эталону `/data/p42-tk671/fam/bkep/<окно>.csv`, `y_n` равна `/data/p42-tk604/ep/<окно>.csv` на общих эпизодах — любое расхождение = стоп (rc 3), цифры не печатаются;
(2) доли `y = 1` и `y_n = 1` по третям `natr_e8` (границы — общие по SEL, зачётные сутки wa / wb / wc: 01.07–13.11), согласие меток и каппа Коэна; интервалы — блочный бутстреп по суткам (блок ⌈n^{1/3}⌉ = 6 и 21), зерно этапа; в БД `t728_label`.
  python3 tk728_labels_report.py --lab /data/p42-tk728/res --db /data/p42-tk728/res728.sqlite --out /data/p42-tk728/res/labels.json [--check-only]"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk728_lib as L  # noqa: E402
import tk728_db as D  # noqa: E402

REF_Y = "/data/p42-tk671/fam/bkep/%s.csv"
REF_YN = "/data/p42-tk604/ep/%s.csv"
WINS = {"wa": ("2026-06-17", "2026-07-01", "2026-08-25"), "wb": ("2026-07-25", "2026-08-26", "2026-10-02"), "wc": ("2026-09-01", "2026-10-03", "2026-11-13")}
DAY_MS = 86_400_000
RUN = "A4"
KEY = ["side", "sym", "level", "sig_ms"]


def ms_of(d: str) -> int:
    return int(pd.Timestamp(d, tz="UTC").timestamp() * 1000)


def load_lab(path):
    df = pd.read_csv(path, dtype={"y": "Float64", "y_n": "Float64"})
    return df


def compare(mine: pd.DataFrame, ref: pd.DataFrame, col: str):
    """Общие эпизоды (ключ side, sym, level, sig_ms), где значение есть у обоих: (число общих, число расхождений, только у меня, только у эталона)."""
    a = mine[KEY + [col]].dropna(subset=[col]).set_index(KEY)[col]
    b = ref[KEY + [col]].dropna(subset=[col]).set_index(KEY)[col]
    j = a.to_frame("a").join(b.to_frame("b"), how="inner")
    return len(j), int((j["a"].astype(float) != j["b"].astype(float)).sum()), len(a) - len(j), len(b) - len(j)


def day_counts(day, cell, nday):
    """Числа эпизодов по суткам для четырёх клеток (y, y_n): (nday, 4)."""
    out = np.zeros((nday, 4), dtype=np.float64)
    for c in range(4):
        out[:, c] = np.bincount(day[cell == c], minlength=nday)
    return out


def stats_from_cells(c):
    """c — (…, 4) суммы клеток (y,y_n) = 00, 01, 10, 11 → доли, согласие, каппа."""
    n = c.sum(axis=-1)
    y = (c[..., 2] + c[..., 3]) / n
    yn = (c[..., 1] + c[..., 3]) / n
    agree = (c[..., 0] + c[..., 3]) / n
    pe = y * yn + (1 - y) * (1 - yn)
    kap = np.where(pe < 1, (agree - pe) / (1 - pe), np.nan)
    return n, y, yn, agree, kap


def boot_stats(cells_by_day, block, seed, nboot=L.N_BOOT):
    nday = len(cells_by_day)
    rng = np.random.default_rng(seed)
    idx = L.block_idx(nday, rng, nboot, block)
    tot = cells_by_day[idx].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        _, y, yn, agree, kap = stats_from_cells(tot)
    return np.stack([y, yn, agree, kap], axis=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lab", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    # (1) проверка метода до чтения результатов
    check, bad, parts = {}, False, []
    for w, (t0, lo, hi) in WINS.items():
        mine = load_lab(os.path.join(a.lab, "lab-%s.csv" % w))
        ref_y = pd.read_csv(REF_Y % w, dtype={"y": "Float64"})
        ref_n = pd.read_csv(REF_YN % w, dtype={"y_n": "Float64"})
        cy = compare(mine, ref_y, "y")
        cn = compare(mine, ref_n, "y_n")
        check[w] = dict(y=dict(common=cy[0], mismatch=cy[1], only_mine=cy[2], only_ref=cy[3]), y_n=dict(common=cn[0], mismatch=cn[1], only_mine=cn[2], only_ref=cn[3]), rows=len(mine))
        bad |= cy[1] > 0 or cn[1] > 0 or cy[0] == 0 or cn[0] == 0
        mine = mine.copy()
        mine["win"] = w
        mine["abs_ms"] = mine["sig_ms"].astype(np.int64) + ms_of(t0)
        parts.append(mine[(mine.abs_ms >= ms_of(lo)) & (mine.abs_ms < ms_of(hi) + DAY_MS)])
    print(json.dumps(check, ensure_ascii=False), flush=True)
    if bad:
        print("СТОП: метод не прошёл проверку (расхождение с эталоном или нет общих эпизодов); результаты не читаются", flush=True)
        sys.exit(3)
    if a.check_only:
        return
    # (2) таблицы
    df = pd.concat(parts, ignore_index=True)
    df = df[(df.ok_t60 == 1) & df["y"].notna() & df["y_n"].notna() & (df.natr_e8 > 0)].copy()
    day0 = ms_of("2026-07-01")
    df["day"] = ((df["abs_ms"] - day0) // DAY_MS).astype(np.int64)
    nday = int(df["day"].max()) + 1
    (q1, q2), third = L.terciles(df["natr_e8"].to_numpy(np.float64))
    df["third"] = third
    df["cell"] = (df["y"].astype(int) * 2 + df["y_n"].astype(int)).astype(np.int8)
    scopes = {"все": np.ones(len(df), bool), "треть 1 (низкая V)": third == 0, "треть 2": third == 1, "треть 3 (высокая V)": third == 2}
    blocks = (L.block_len(nday), 21)
    res, rows = dict(n_days=nday, terciles_natr_e8=[q1, q2], blocks=list(blocks), n_ep=len(df), by_win={w: int((df.win == w).sum()) for w in WINS}), []
    res["scopes"] = {}
    for name, m in scopes.items():
        cd = day_counts(df["day"].to_numpy()[m], df["cell"].to_numpy()[m], nday)
        n, y, yn, agree, kap = stats_from_cells(cd.sum(axis=0))
        bs = [boot_stats(cd, b, L.SEED) for b in blocks]
        iv = []
        for bb in bs:
            lo_, hi_ = np.nanpercentile(bb, [2.5, 97.5], axis=0)
            iv.append((lo_, hi_))
        r = dict(n=int(n), y=float(y), yn=float(yn), agree=float(agree), kappa=float(kap), cells=[int(x) for x in cd.sum(axis=0)])
        for k, (b, (lo_, hi_)) in enumerate(zip(blocks, iv)):
            r["ci_block%d" % b] = dict(y=[float(lo_[0]), float(hi_[0])], yn=[float(lo_[1]), float(hi_[1])], agree=[float(lo_[2]), float(hi_[2])], kappa=[float(lo_[3]), float(hi_[3])])
        res["scopes"][name] = r
        c6, c21 = r["ci_block%d" % blocks[0]], r["ci_block21"]
        rows.append((name, name, int(n), float(y), c6["y"][0], c6["y"][1], float(yn), c6["yn"][0], c6["yn"][1], float(agree), float(kap),
                     json.dumps(dict(block=blocks[0], ci_block21=c21, kappa_ci_block6=c6["kappa"], agree_ci_block6=c6["agree"], cells=r["cells"]), ensure_ascii=False)))
    # разность долей y_n − y (интервал) по всему SEL: «ход 1 NATR» против «ход 1 %»
    res["terciles_label"] = "границы третей — общие по SEL (natr_e8 эпизодов); V = NATR 30 мин (natr_e8 / 1e8)"
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    db = D.connect(a.db)
    D.new_part(db, RUN + "-lab", ["t728_label"], "4в: метки y и y_n по третям V", "SEL c539, независимые эпизоды wa/wb/wc, зерно %d" % L.SEED)
    D.put(db, "t728_label", RUN + "-lab", [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11]) for r in rows])
    D.text(db, RUN + "-lab", "terciles_natr_e8", [q1, q2])
    db.commit()
    for name, r in res["scopes"].items():
        print(name, "n=%d y=%.4f y_n=%.4f agree=%.4f kappa=%.4f" % (r["n"], r["y"], r["yn"], r["agree"], r["kappa"]), flush=True)


if __name__ == "__main__":
    main()
