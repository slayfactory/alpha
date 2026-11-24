#!/usr/bin/env python3
"""TK-701: ворота ленты. Сверка с эталоном, не со склада на zip (без --store-verify и повторных сверок zip):
  dec  — суммы декодированной ленты (оборот, оборот покупок тейкера) против минуток биржи `klines/<монета>/<сутки>.csv` (`quote_volume`, `taker_buy_quote_volume`) на выборке монета-суток: итог суток ≤ 1e-4 и доля минут с точным совпадением ≥ 0,9 (граница минуты у агрегата сделок размывает единичные минуты);
  path — столбцы пути `g_*` таблицы ленты против файлов пути TK-698 (`/data/p42-tk698/path`), точно на сделках, где обе ленты полные.
Запуск на srv: python3 tk701_gate.py dec <линия> <окно> <число монета-суток>   |   python3 tk701_gate.py path <линия> <окно> [<каталог таблиц>]"""
import os
import sys

import numpy as np
import pandas as pd

import tk701_tape as T


def gate_dec(line, win, n_days):
    tr = T.load_trades(line, win)
    tr["date"] = pd.to_datetime(tr["entry_ms"], unit="ms").dt.strftime("%Y-%m-%d")
    pick = tr.drop_duplicates(["sym", "date"]).sample(frac=1.0, random_state=701)
    worst_q = worst_b = 0.0
    minutes = bad = done = exact = 0
    for r in pick.itertuples():
        if done >= n_days:
            break
        kp = "/data/p42-tk599/%s/klines/%s/%s.csv" % (win, r.sym, r.date)
        if not os.path.exists(kp):
            continue
        done += 1
        tp = T.StoreTape(win, r.sym)
        d = (pd.Timestamp(r.date) - pd.Timestamp("1970-01-01")).days
        x = tp.day(d)
        k = pd.read_csv(kp, usecols=["open_time", "quote_volume", "taker_buy_quote_volume"])
        t, p, v, buy = x
        m = (t // 60000) - (d * 1440)
        q = np.bincount(m, weights=v, minlength=1440)
        qb = np.bincount(m, weights=np.where(buy, v, 0.0), minlength=1440)
        km = ((k["open_time"].to_numpy() // 60000) - d * 1440).astype(int)
        kq, kb = k["quote_volume"].to_numpy(), k["taker_buy_quote_volume"].to_numpy()
        eq = np.abs(q[km] - kq) / np.maximum(kq, 1.0)
        eb = np.abs(qb[km] - kb) / np.maximum(kb, 1.0)
        minutes += len(km)
        exact += int(((eq <= 1e-6) & (eb <= 1e-6)).sum())
        dq = abs(float(q.sum()) - float(kq.sum())) / max(float(kq.sum()), 1.0)
        db_ = abs(float(qb.sum()) - float(kb.sum())) / max(float(kb.sum()), 1.0)
        bad += int(dq > 1e-4) + int(db_ > 1e-4)
        worst_q, worst_b = max(worst_q, dq), max(worst_b, db_)
        print(r.sym, r.date, "store/zip", tp.n_store, tp.n_zip, "day rel err quote %.2e taker-buy %.2e, exact minutes %.3f" % (dq, db_, float(((eq <= 1e-6) & (eb <= 1e-6)).mean())), flush=True)
    share = exact / max(minutes, 1)
    print("GATE dec: coin-days", done, "minutes", minutes, "exact-minute share %.3f" % share, "day totals bad", bad, "worst day quote %.2e taker-buy %.2e" % (worst_q, worst_b))
    return bad == 0 and share >= 0.9


def gate_path(line, win, tdir):
    a = pd.read_parquet(os.path.join(tdir, "%s-%s.parquet" % (line, win)))
    b = pd.read_parquet("%s/path/%s-%s.parquet" % (T.TR_ROOT, line, win))
    m = a.merge(b, on=["sym", "entry_ms"], how="inner", validate="one_to_one")
    ok = m["g_tape_ok"].to_numpy(bool) & m["tape_ok"].to_numpy(bool)
    n_a, n_b = int(m["g_tape_ok"].sum()), int(m["tape_ok"].sum())
    bad = 0
    for c in ("mfe", "mae", "t_mfe", "t_mae", "exit_move", "giveback", "hold_min"):
        d = np.abs(m.loc[ok, "g_" + c].to_numpy(float) - m.loc[ok, c].to_numpy(float))
        nb = int((~(d <= 1e-9)).sum())
        bad += nb
        print(c, "max abs diff", float(np.nanmax(d)) if len(d) else None, "bad", nb)
    for c in ("green", "green_first"):
        nb = int((m.loc[ok, "g_" + c].to_numpy(bool) != m.loc[ok, c].to_numpy(bool)).sum())
        bad += nb
        print(c, "bad", nb)
    nb = int((m.loc[ok, "g_ticks"].to_numpy() != m.loc[ok, "ticks"].to_numpy()).sum())
    bad += nb
    print("ticks bad", nb)
    print("GATE path:", line, win, "rows", len(m), "tape_ok new/old/both", n_a, n_b, int(ok.sum()), "bad", bad)
    return bad == 0


if __name__ == "__main__":
    if sys.argv[1] == "dec":
        sys.exit(0 if gate_dec(sys.argv[2], sys.argv[3], int(sys.argv[4])) else 1)
    sys.exit(0 if gate_path(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else os.path.join(T.OUT_ROOT, "tape")) else 1)
