#!/usr/bin/env python3
"""П-42 TK-697 (№ 8): дневные свечи 1d USD-M с data.binance.vision для монет списка (по умолчанию c539 + BTC / ETH) → /data/p42-tk697/daily/<SYM>/.
  python3 tk697_dl_daily.py <список монет> <месяц от YYYY-MM> <месяц до YYYY-MM> <день от YYYY-MM-DD> <день до YYYY-MM-DD> [параллель=16]
месячные zip `monthly/klines/<SYM>/1d/<SYM>-1d-<ММ>.zip` для месяцев [от; до], дневные `daily/klines/<SYM>/1d/<SYM>-1d-<день>.zip` для дней [от; до] (неполный последний месяц);
zip как опубликован + sha256 по `.CHECKSUM` (общая `dl_batch.run_chunk`); не-200 / sha — `/data/p42-tk697/daily/log-batch/miss.tsv` (до листинга монеты 404 — ожидаемо).
Данные позже 13.11.2026 24:00 UTC не качаются (изоляция: 14.11 и позже не принимаются)."""
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dl_batch as dl  # noqa: E402

ROOT = "/data/p42-tk697/daily"
BASE = dl.BASE
LAST_DAY = dt.date(2026, 11, 13)


def months(m0, m1):
    y, m = int(m0[:4]), int(m0[5:7])
    while f"{y:04d}-{m:02d}" <= m1:
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def tasks(syms, m0, m1, d0, d1):
    d0, d1 = dt.date.fromisoformat(d0), dt.date.fromisoformat(d1)
    if d1 > LAST_DAY:
        raise SystemExit("охрана: дни позже 13.11.2026 не качаются")
    for s in syms:
        for m in months(m0, m1):
            yield "1d-m", s, m, f"{BASE}/monthly/klines/{s}/1d/{s}-1d-{m}.zip", f"{ROOT}/{s}/{s}-1d-{m}.zip", ROOT
        d = d0
        while d <= d1:
            yield "1d-d", s, d.isoformat(), f"{BASE}/daily/klines/{s}/1d/{s}-1d-{d.isoformat()}.zip", f"{ROOT}/{s}/{s}-1d-{d.isoformat()}.zip", ROOT
            d += dt.timedelta(days=1)


def main():
    syms = [s for s in open(sys.argv[1]).read().replace(",", "\n").split() if s]
    for x in ("BTCUSDT", "ETHUSDT"):
        if x not in syms:
            syms.append(x)
    m0, m1, d0, d1 = sys.argv[2:6]
    par = int(sys.argv[6]) if len(sys.argv) > 6 else 16
    todo = [t for t in tasks(syms, m0, m1, d0, d1) if not os.path.exists(t[4])]
    print(f"монет {len(syms)}, задач {len(todo)}", flush=True)
    ok = miss = 0
    for i in range(0, len(todo), dl.CHUNK):
        o, m = dl.run_chunk(todo[i:i + dl.CHUNK], par)
        ok, miss = ok + o, miss + m
        print(f"{min(i + dl.CHUNK, len(todo))}/{len(todo)} ok {ok} miss {miss}", flush=True)
    os.makedirs(f"{ROOT}/log-batch", exist_ok=True)
    open(f"{ROOT}/log-batch/done", "w").write("1")


if __name__ == "__main__":
    main()
