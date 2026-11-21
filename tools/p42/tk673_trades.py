#!/usr/bin/env python3
"""TK-673: выгрузка готовых сделок 6 линий (S_base, S_mk1, D_S, L_base, L_n24, L_mk2) в trades-<линия>.csv, окно 01.07–13.11 (h775), без движка (В-213).
Загрузка — `tk663_shape.load_lines` (сверка n и Σ с карточкой TK-662) + D_S из выходов TK-668 (`/data/p42-tk668/e4/cells/<окно>/S_lx1`, `tk638_post.load_cell`); сверка D_S: 1 237 сделок / +725,53 $.
Колонки: sym, t_in, t_out (мс UTC), day_in, day_out (сутки от 01.07), hour_out, hold_min, pnl ($, 500 $, после комиссий), kind (вид выхода), side, btc_aligned (ход BTC за сделку, + = в пользу).
Запуск на srv: /opt/venv/bin/python tk673_trades.py <код TK-639> <каталог tk663_shape> <каталог выхода>"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

code, shape_dir, outdir = sys.argv[1:4]
sys.path.insert(0, shape_dir)
import tk663_shape as sh  # noqa: E402

raw = sh.load_lines(code)
st, p8 = sh.st, sys.modules["tk638_post"]
import tk625_run as r5  # noqa: E402
u = r5.Uni("h775")
W = [w.name for w in st.TRADE_WINDOWS]
d = {w: ("/data/p42-tk668/e4/cells/%s/S_lx1" % w, "S") for w in W}
ser, kinds, pnl = p8.load_cell(u, "S", d)
n, s = len(ser.val), float(np.sum(ser.val))
assert n == 1237 and abs(s - 725.53) < 0.01, ("сверка D_S", n, s)
raw["D_S"] = pd.DataFrame({"sym": [str(x) for x in ser.sym_name], "t_in": np.asarray(ser.t_in, np.int64), "t_out": np.asarray(ser.t_out, np.int64),
                           "day_in": np.asarray(ser.day, np.int64), "pnl": np.asarray(ser.val, float), "kind": [str(k) for k in kinds], "side": "S"})
D0 = st.D0
sh.D0 = D0
d0_ms = int(dt.datetime.combine(D0, dt.time(), dt.timezone.utc).timestamp() * 1000)
gb0_ms = d0_ms - sh.DAY_MS
btc, miss = sh.load_btc(["/data/p42-tk596/jul/klines/BTCUSDT", "/data/p42-tk596/aug/klines/BTCUSDT", "/data/klines/binance/BTCUSDT"], gb0_ms)
os.makedirs(outdir, exist_ok=True)
for k in ["S_base", "S_mk1", "D_S", "L_base", "L_n24", "L_mk2"]:
    df = sh.enrich(raw[k], btc, d0_ms, gb0_ms)
    df.to_csv(os.path.join(outdir, "trades-%s.csv" % k), index=False)
    print(k, len(df), round(float(df.pnl.sum()), 2), int(df.day_out.max()), int(df.day_in.max()))
mv, rg = sh.btc_days(btc, st.NDAYS, gb0_ms)
pd.DataFrame({"day": range(st.NDAYS), "btc_move": mv, "btc_range": rg}).to_csv(os.path.join(outdir, "btc-days.csv"), index=False)
print("ND", st.NDAYS, "D0", D0)
