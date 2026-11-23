#!/usr/bin/env python3
"""TK-698, пункт 5а: путь сделки после входа (MFE / MAE в $ и в NATR) по ленте — готовые функции TK-685 (`tk685_lib.path_one`, склад тиков `/data/p42-tk599/tick-store`, лента окна `/data/p42-tk599/<окно>/aggtrades`).
Движок не запускается, склад не сверяется. NATR на сигнале = 30-минутный средний TR / close закрытой минуты `b = sig_min − 1` (как `sigfeat_lib.natr_series`, векторно; кэш `.b42` окна).
Один процесс — одно окно (SIGFEAT_BASE_DATE ставится до импорта склада): python3 tk698_path.py <окно> <строки через запятую>   → `/data/p42-tk698/path/<строка>-<окно>.parquet`"""
import datetime
import os
import sys

import numpy as np
import pandas as pd

ROOT = "/data/p42-tk698"
CACHE = "/data/p42-tk621/%s/cache/%s.b42"
NOTIONAL = 500.0


def natr_at(c_path, b, p=30):
    """NATR (доля) на закрытых минутах b (индексы от T0 окна): среднее TR за p минут / close; TR = max(h − l, |h − prevc|, |l − prevc|), prevc — close предыдущей минуты с данными."""
    with open(c_path, "rb") as f:
        head = f.read(8)
    n = int.from_bytes(head[4:8], "little")
    a = np.fromfile(c_path, dtype="<i8", offset=8, count=5 * n).reshape(5, n)
    o, h, l, c = a[0], a[1].astype(np.float64), a[2].astype(np.float64), a[3].astype(np.float64)
    valid = o != 0
    idx = np.where(valid, np.arange(n), -1)
    np.maximum.accumulate(idx, out=idx)
    prev_idx = np.concatenate([[-1], idx[:-1]])
    prevc = np.where(prev_idx >= 0, c[np.maximum(prev_idx, 0)], 0.0)
    tr = np.where(valid, np.maximum(h - l, np.where(prevc != 0, np.maximum(np.abs(h - prevc), np.abs(l - prevc)), 0.0)), 0.0)
    ptr = np.concatenate([[0.0], np.cumsum(tr)])
    pcnt = np.concatenate([[0], np.cumsum(valid.astype(np.int64))])
    out = np.full(len(b), np.nan)
    for i, t in enumerate(b):
        if t < p - 1 or t >= n or not valid[t]:
            continue
        k = pcnt[t + 1] - pcnt[t + 1 - p]
        if k * 2 < p:
            continue
        out[i] = (ptr[t + 1] - ptr[t + 1 - p]) / k / c[t]
    return out


def main(win, lines):
    import tk698_lib as L
    t0d = datetime.date.fromisoformat(L.WINDOWS[win][0])
    os.environ["SIGFEAT_BASE_DATE"] = t0d.isoformat()
    os.environ["SIGFEAT_DAYS"] = "74"
    import tk671_ticks as TK
    import tk685_lib as lib
    root = "/data/p42-tk599/%s/aggtrades" % win
    t0 = L.t0_ms(win)
    last = L.LAST_MS - t0 - 1
    os.makedirs(os.path.join(ROOT, "path"), exist_ok=True)
    for line in lines.split(","):
        tr = pd.read_parquet(os.path.join(ROOT, "trades", line + ".parquet"))
        tr = tr[tr["win"] == win].sort_values(["sym", "entry_ms"], kind="stable")
        sign = 1 if line.startswith("L_") else -1
        rows, cur, tk = [], None, None
        for r in tr.itertuples():
            if r.sym != cur:
                cur, tk = r.sym, TK.StoreTicks(root, r.sym)
            a, b = int(r.entry_ms) - t0, min(int(r.exit_ms) - t0, last)
            t, px, full = tk.span(a, b)
            res = lib.path_one(t, px, a, int(r.exit_ms) - t0, int(r.entry_px_e8), sign, float(r.fees), notional=NOTIONAL)
            rows.append(dict(sym=r.sym, entry_ms=int(r.entry_ms), ticks=res["ticks"], tape_ok=bool(full and res["ticks"] > 0), mfe=res["mfe"], mae=res["mae"], t_mfe=res["t_mfe"], t_mae=res["t_mae"],
                             green=res["green"], green_first=res["green_first"], exit_move=res["exit_move"], giveback=res["giveback"], hold_min=res["hold_min"]))
        out = pd.DataFrame(rows)
        # NATR на сигнале (закрытая минута перед минутой сигнала)
        nat = np.full(len(out), np.nan)
        syms = out["sym"].to_numpy()
        sig = tr["sig_ms"].to_numpy(np.int64) - t0
        for s in np.unique(syms):
            m = syms == s
            p = CACHE % (win, s)
            if os.path.exists(p):
                nat[m] = natr_at(p, sig[m] // 60000 - 1)
        out["natr"] = nat
        out["mfe_natr"] = out["mfe"] / NOTIONAL / out["natr"]
        out["mae_natr"] = out["mae"] / NOTIONAL / out["natr"]
        out.to_parquet(os.path.join(ROOT, "path", "%s-%s.parquet" % (line, win)), index=False)
        print(line, win, len(out), "tape_ok", float(out["tape_ok"].mean()) if len(out) else None, "natr", float(np.isfinite(nat).mean()) if len(out) else None, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
