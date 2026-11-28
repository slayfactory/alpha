#!/usr/bin/env python3
"""TK-728 шаг 4а: запуск существующих ворот G-causal (`tk671_gate_fund`, `tk671_gate_bd`) с другим каталогом выхода — сами ворота пишут в фиксированный каталог прежней задачи (`OUT`), его не перезаписываем.
  python3 tk728_gate_wrap.py <модуль> <каталог выхода> <аргументы ворот …>"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if len(sys.argv) < 4:
    sys.exit(__doc__)
mod = importlib.import_module(sys.argv[1])
if not hasattr(mod, "OUT"):
    sys.exit("в модуле нет OUT")
mod.OUT = sys.argv[2]
os.makedirs(mod.OUT, exist_ok=True)
sys.argv = [sys.argv[1] + ".py"] + sys.argv[3:]
mod.main()
