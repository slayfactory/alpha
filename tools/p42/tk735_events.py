#!/usr/bin/env python3
"""TK-735 (поправка L-526): события окна m01 для P4 FJ — `tk625_events.py` без изменений, только чтение сделок из склада тиков вместо zip (zip m01 — пустые файлы после перевода в склад,
`tk671_ticks.StoreTicks` — тот же `span` / `day`, сверен TK-671). Запуск как у `tk625_events.py`:
  SIGFEAT_BASE_DATE=2025-12-12 SIGFEAT_DAYS=80 python3 tk735_events.py --t0 2025-12-12 --eng /data/p42-tk735/eng-m01 --cache /data/p42-tk621/m01/cache --trades /data/p42-tk621/m01/aggtrades --lo 2026-01-01 --hi 2026-01-31 --out /data/p42-tk735/res/ev-m01.csv --procs 4
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, "/data/p42-tk728/code")     # tk671_ticks (читатель склада тиков)
sys.path.insert(0, "/data/p42-tk625/code")     # sigfeat_lib / tk625_events — первыми
import tk625_events as E  # noqa: E402
import tk671_ticks as TT  # noqa: E402


def _store_ticks(root, sym, keep=4):
    return TT.StoreTicks(root, sym, keep=keep)


E.L.Ticks = _store_ticks     # Pool — fork: подмена доходит до процессов

if __name__ == "__main__":
    E.main()
