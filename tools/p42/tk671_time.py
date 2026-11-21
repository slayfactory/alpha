#!/usr/bin/env python3
"""П-42 §13.71 (TK-671), семейство `time`: признаки времени сигнала — только из `abs_ms` (мс UTC), чистый векторный numpy, данные не читаются.

  python3 tk671_time.py --rows <rows.parquet> --win wa --side long --out <out.parquet> [--procs N]
Модуль: `compute(rows, win, side, procs=3) -> DataFrame` (индекс `row_id`, float64, NaN нет; строки не выбрасываются). Пишет `<out>.info.json` (строки, секунды, мс/строка, доля не-NaN по столбцам, метки корзин килзон).

Столбцы (72):
  t_hour          час суток UTC, 0…23
  t_min           минута часа, 0…59 (целая)
  t_wday          день недели UTC, 0 = пн … 6 = вс (та же схема, что `dow` в `tk622_killzones.buckets`)
  t_to_fund_min   минут до ближайшего СЛЕДУЮЩЕГО фандинга 00 / 08 / 16 UTC, в (0; 480] (ровно в 08:00:00.000 → 480: следующий — 16:00); дробная (мс)
  t_sess_asia / t_sess_eu / t_sess_us   0/1 — сессия по ЧАСУ UTC сигнала; константы `SESSIONS` ниже (договорные границы, не измерены; сессии перекрываются, флаги независимы):
                  Азия [0; 8), Европа [7; 16), США [13; 21) — открытия 00:00 / 07:00 / 13:30 как в корзинах «открытие сессии» TK-622, США округлены вниз до часа
  kz00 … kz64     65 корзин килзон TK-622 / TK-651 (`tk622_killzones.buckets`, порядок и определения без изменений), 0/1; расшифровка схема/метка — `KZ_LABELS`, `info.json` и docstring `tk622_killzones`
Охрана: `abs_ms` ≥ 14.11.2026 00:00 UTC (проверка вперёд) → ошибка."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk622_killzones as kz  # noqa: E402

DAY_MS, HOUR_MS, MIN_MS = 86_400_000, 3_600_000, 60_000
FUND_PERIOD_MS = 8 * HOUR_MS
LAST_MS = int(datetime.datetime(2026, 11, 14, tzinfo=datetime.timezone.utc).timestamp() * 1000)     # 14.11.2026 00:00 UTC: с этой мс читать нельзя (проверка вперёд)
SESSIONS = {"t_sess_asia": (0, 8), "t_sess_eu": (7, 16), "t_sess_us": (13, 21)}     # [час начала; час конца) UTC; константы заданы один раз здесь
BASE_COLS = ["t_hour", "t_min", "t_wday", "t_to_fund_min"] + list(SESSIONS)
_B = kz.buckets(np.zeros(1, dtype=np.int64))
KZ_LABELS = {"kz%02d" % i: "%s | %s" % (s, lab) for i, (s, lab, _) in enumerate(_B)}
KZ_COLS = list(KZ_LABELS)
COLS = BASE_COLS + KZ_COLS
assert len(KZ_COLS) == 65, len(KZ_COLS)


def check_abs_ms(ms):
    if len(ms) and int(np.max(ms)) >= LAST_MS:
        raise SystemExit("охрана: abs_ms ≥ 14.11.2026 — не читается (проверка вперёд)")


def time_features(abs_ms):
    """dict столбец → float64-массив по `abs_ms` (мс UTC, int64)."""
    ts = np.asarray(abs_ms, dtype=np.int64)
    check_abs_ms(ts)
    tod = ts % DAY_MS
    hour = tod // HOUR_MS
    out = {"t_hour": hour, "t_min": (ts % HOUR_MS) // MIN_MS, "t_wday": ((ts // DAY_MS) + 3) % 7,
           "t_to_fund_min": (FUND_PERIOD_MS - ts % FUND_PERIOD_MS) / MIN_MS}
    for name, (a, b) in SESSIONS.items():
        out[name] = (hour >= a) & (hour < b)
    for name, (_, _, mask) in zip(KZ_COLS, kz.buckets(ts)):
        out[name] = mask
    return {k: np.asarray(v, dtype=np.float64) for k, v in out.items()}


def check_rows(rows, win, side):
    for col, want in (("win", win), ("side", side)):
        if col in rows.columns and not (rows[col] == want).all():
            raise ValueError("rows.%s ≠ %s" % (col, want))
    if "row_id" not in rows.columns or not rows["row_id"].is_unique:
        raise ValueError("rows.row_id отсутствует или не уникален")


def compute(rows, win, side, procs=3):
    import pandas as pd
    check_rows(rows, win, side)
    f = time_features(rows["abs_ms"].to_numpy(dtype=np.int64))
    return pd.DataFrame(f, index=pd.Index(rows["row_id"].to_numpy(), name="row_id"))[COLS]


def write_info(path, df, seconds, extra=None):
    n = len(df)
    info = dict(rows=n, seconds=round(seconds, 4), ms_per_row=round(1000.0 * seconds / max(n, 1), 6),
                nonnan={c: round(float(df[c].notna().mean()), 6) for c in df.columns})
    info.update(extra or {})
    with open(path + ".info.json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=1)
    return info


def main():
    import pandas as pd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--win", required=True, choices=["wa", "wb", "wc"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=3)
    a = ap.parse_args()
    rows = pd.read_parquet(a.rows, columns=["row_id", "abs_ms", "side", "win"])
    t = time.time()
    df = compute(rows, a.win, a.side, a.procs)
    sec = time.time() - t
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    df.to_parquet(a.out)
    info = write_info(a.out, df, sec, dict(kz_labels=KZ_LABELS, sessions=SESSIONS))
    print(json.dumps({k: info[k] for k in ("rows", "seconds", "ms_per_row")}))


if __name__ == "__main__":
    main()
