#!/usr/bin/env python3
"""TK-695: список TradFi-контрактов (В-175) — `contracts.csv` tradfi=1 ∪ exchangeInfo `TRADIFI_PERPETUAL` (как `tk686_tradfi_enum.py`); файл — по монете в строке.
  python3 tk695_tradfi.py <contracts.csv> <exchange.json> <выход>"""
import csv
import json
import sys

T = {r["symbol"] for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")) if r["tradfi"] == "1"}
ex = json.load(open(sys.argv[2], encoding="utf-8"))
T |= {s for s, v in ex.items() if isinstance(v, dict) and v.get("contractType") == "TRADIFI_PERPETUAL"}
open(sys.argv[3], "w", encoding="utf-8").write("\n".join(sorted(T)) + "\n")
print("tradfi", len(T))
