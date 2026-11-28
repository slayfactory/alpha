#!/usr/bin/env python3
"""TK-744 (В-195): чтение сделок V1 (`tt=0 fh=0`) рядом с V0nt (TK-741) — только srv. Подключает этап `fh0` к `tk741_lib` (каталоги `/data/p42-tk744/trades/tk744-fh0-*`, cid 7441001); формулы метрик — `tk736_lib.py` (В-169)."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk741_lib as T  # noqa: E402

ROOT = os.environ.get("TK744_ROOT", "/data/p42-tk744")
RES = ROOT + "/res"
T.CID["fh0"] = 7441001
_run_dir741 = T.run_dir


def run_dir(stage, win):
    if stage != "fh0":
        return _run_dir741(stage, win)
    part = "h1" if win.startswith("m") else "n1" if win == "n1" else "sel"
    return "%s/trades/tk744-fh0-%s" % (ROOT, part)


T.run_dir = run_dir   # T.read_win / T.load зовут run_dir из своего модуля
