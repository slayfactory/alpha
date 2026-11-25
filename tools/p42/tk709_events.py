#!/usr/bin/env python3
"""TK-709 (протокол P-TK709, §4, §8 ступень 1): события «премия ≤ −X %» из `premiumIndexKlines` 1m (TK-707) по c539 → БД.
Эпизод = серия подряд идущих минут с `low ≤ −X`, событие = первая минута (разрыв > 1 мин — новый эпизод). Читается только премия, цен нет.
Запуск (srv, юнит): python3 tk709_events.py <БД.sqlite> <run_id> [первый день 2026-01-01] [последний день 2026-11-23]
Пишет `events(run_id, sym, x, t_ms, low, p_open_next, p_end5, p_end15, p_end60, eplen)`; повтор с тем же run_id — отказ."""
import csv
import datetime as dt
import io
import os
import sqlite3
import sys
import zipfile

import numpy as np

PREM = "/data/p42-premium"
UNI = "/data/p42-tk621/universe/symbols-crypto.txt"
XS = (0.05, 0.10, 0.20)
HS = (5, 15, 60)
MIN = 60_000


def symbols():
    return [s for s in open(UNI, encoding="utf-8").read().replace(",", "\n").split() if s]


def day_range(d0, d1):
    d = dt.date.fromisoformat(d0)
    while d <= dt.date.fromisoformat(d1):
        yield d.isoformat()
        d += dt.timedelta(days=1)


def read_zip(path):
    """(open_ms, open, low, close) строк суток; единицы времени мс (µs → мс); пусто — нет строк."""
    out = []
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            for r in csv.reader(io.TextIOWrapper(f, encoding="utf-8")):
                if not r or not r[0][:1].isdigit():
                    continue
                t = int(r[0])
                if t > 10 ** 14:
                    t //= 1000
                out.append((t, float(r[1]), float(r[3]), float(r[4])))
    return out


def load_symbol(sym, d0, d1):
    rows = []
    for day in day_range(d0, d1):
        p = f"{PREM}/{sym}/{sym}-1m-{day}.zip"
        if os.path.exists(p):
            try:
                rows += read_zip(p)
            except (zipfile.BadZipFile, OSError):
                pass
    if not rows:
        return None
    a = np.array(rows, dtype=np.float64)
    a = a[np.argsort(a[:, 0], kind="stable")]
    t = a[:, 0].astype(np.int64)
    keep = np.concatenate([[True], np.diff(t) != 0])      # дубли минут — первая строка
    a, t = a[keep], t[keep]
    return t, a[:, 1], a[:, 2], a[:, 3]                   # t, open, low, close


def events_of(t, op, low, cl, x):
    """[(t_ms, low, eplen)] — первые минуты эпизодов `low ≤ −x`; серия — подряд идущие минуты."""
    beyond = low <= -x
    if not beyond.any():
        return []
    cont = np.zeros(len(t), bool)
    cont[1:] = beyond[:-1] & (t[1:] - t[:-1] == MIN)
    start = beyond & ~cont
    idx = np.nonzero(start)[0]
    out = []
    n = len(t)
    for i in idx:
        j = i
        while j + 1 < n and beyond[j + 1] and t[j + 1] - t[j] == MIN:
            j += 1
        out.append((i, j - i + 1))
    return out


def at(t, arr, ms):
    k = int(np.searchsorted(t, ms))
    return float(arr[k]) if k < len(t) and t[k] == ms else float("nan")


def main():
    db, run = sys.argv[1], sys.argv[2]
    d0 = sys.argv[3] if len(sys.argv) > 3 else "2026-01-01"
    d1 = sys.argv[4] if len(sys.argv) > 4 else "2026-11-23"
    con = sqlite3.connect(db)
    con.execute("create table if not exists events(run_id text, sym text, x real, t_ms integer, low real, p_open_next real, p_end5 real, p_end15 real, p_end60 real, eplen integer)")
    con.execute("create index if not exists ev_run on events(run_id, x, sym, t_ms)")
    if con.execute("select count(*) from events where run_id=?", (run,)).fetchone()[0]:
        raise SystemExit("run_id уже есть: " + run)
    n_sym = 0
    for sym in symbols():
        z = load_symbol(sym, d0, d1)
        if z is None:
            continue
        t, op, low, cl = z
        rows = []
        for x in XS:
            for i, ln in events_of(t, op, low, cl, x):
                tm = int(t[i])
                rows.append((run, sym, x, tm, float(low[i]), at(t, op, tm + MIN), at(t, cl, tm + 5 * MIN), at(t, cl, tm + 15 * MIN), at(t, cl, tm + 60 * MIN), ln))
        con.executemany("insert into events values (?,?,?,?,?,?,?,?,?,?)", rows)
        n_sym += 1
        if n_sym % 50 == 0:
            con.commit()
            print(n_sym, "symbols", flush=True)
    con.commit()
    print("done", n_sym, "symbols")


if __name__ == "__main__":
    main()
