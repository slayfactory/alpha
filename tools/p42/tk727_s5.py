"""TK-727, шаг 5 (§13.78.5, §13.78.7 п. 1–3, 10, 13, 16; решения по краям — L-477): сделки и подходы → признаки V, F2…F6, проверка V, анализ.

  tk727_s5.py features [--out DIR] [--procs N] [--only SYM,..]   # сделки (клетки B, S1, S2, L401; окна как у TK-726) и подходы SEL (ep/*.csv) → признаки по свечам
  tk727_s5.py check    [--out DIR]                               # ДО чтения результатов: V против natr_e8 таблицы подходов, вход против уровня (описательно)
  tk727_s5.py analyze  [--out DIR] [--procs N]                   # α по модели «сутки + монета», механизм, прогноз периодов, F1…F6 (см. tk727_s5_an.py)

Время: сигнал — абсолютные мс (t0 окна + sig_ms); минута сетки G0 m = (мс − G0) // 60000; все признаки — на закрытой минуте t = m − 1 (как `natr[sig_ms // 60000 − 1]` движка).
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys
import time

import numpy as np

import tk727_lib as L

OUT = "/data/p42-tk727"
SYMS = "/data/p42-tk621/universe/symbols-crypto.txt"
EP_DIR = "/data/p42-tk604/ep"
T718 = "/data/p42-tk718/trades/tk718c-h1-A"
T726 = "/data/p42-tk726/trades"
EPOCH0 = int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
DAY_MS = L.DAY_MS
L_DAYS = 7        # ключ якоря s4 (`l_days` не задан → 7)
TAU = 0.002       # `tau` не задан → 0,2 % цены
VOL_DAYS_MIN = 10  # F4: не меньше 10 ненулевых суток из 20


def day_of(iso: str) -> int:
    return (datetime.date.fromisoformat(iso) - datetime.date(2026, 1, 1)).days


# окна: имя → (первые, последние зачётные сутки, каталог сделок) — как `tk726_seam.WINDOWS`
WINDOWS = {
    "m01": ("2026-01-01", "2026-01-31", T718), "m02": ("2026-02-01", "2026-02-28", T718), "m03": ("2026-03-01", "2026-03-31", T718),
    "m04": ("2026-04-01", "2026-04-30", T718), "m05": ("2026-05-01", "2026-05-31", T718), "m06": ("2026-06-01", "2026-06-30", T718),
    "wa": ("2026-07-01", "2026-08-25", T726 + "/tk726-sel-A"), "wb": ("2026-08-26", "2026-10-02", T726 + "/tk726-sel-A"),
    "wc": ("2026-10-03", "2026-11-13", T726 + "/tk726-sel-A"), "n1": ("2026-11-14", "2026-11-24", T726 + "/tk726-n1-A"),
}
WIN_NAMES = list(WINDOWS)
FAM = tuple(range(7180015, 7180024))        # 9 членов B, якорь s4 = 7180023
LINES = {"S1": 7180005, "S2": 7180003, "L401": 758805}
ALL_CIDS = FAM + tuple(LINES.values())
FEATS = ("V", "price", "F2", "turn24", "F3", "F4", "F5", "F6")


def read_syms() -> list[str]:
    return sorted(x.strip() for x in open(SYMS, encoding="utf-8").read().strip().split(",") if x.strip())


# ───────────────────────────── чтение ─────────────────────────────

def read_trade_file(path: str, t0_ms: int) -> dict:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    n = len(rows)
    a = dict(sym=np.array([r["sym"] for r in rows], dtype="U24"))
    for k in ("gross", "fees", "funding"):
        a[k] = np.array([float(r[k]) for r in rows], np.float64)
    for k in ("entry_px_e8", "sig_ms", "entry_ms", "exit_ms"):
        a[k] = np.array([int(r[k]) for r in rows], np.int64)
    for k in ("sig_ms", "entry_ms", "exit_ms"):
        a[k] = a[k] + int(t0_ms)
    a["pnl"] = a["gross"] - a["fees"]
    a["eday"] = (a["entry_ms"] - EPOCH0) // DAY_MS
    assert len(a["sym"]) == n
    return a


def load_trades(cids=ALL_CIDS) -> dict:
    """Сделки клеток в зачётных сутках окон: dict массивов (cid, win — номер окна, sym, sig_ms, entry_ms, exit_ms [абс. мс], px, pnl, eday)."""
    parts = []
    for wi, w in enumerate(WIN_NAMES):
        lo, hi, root = WINDOWS[w]
        t0 = json.load(open(os.path.join(root, "t0.json"), encoding="utf-8"))[w]
        for cid in cids:
            p = os.path.join(root, w, "%d-trades.csv" % cid)
            if not os.path.exists(p):
                continue
            a = read_trade_file(p, t0)
            keep = (a["eday"] >= day_of(lo)) & (a["eday"] <= day_of(hi))
            for k in list(a):
                a[k] = a[k][keep]
            n = int(keep.sum())
            a["cid"] = np.full(n, cid, np.int64)
            a["win"] = np.full(n, wi, np.int8)
            parts.append(a)
    keys = ("cid", "win", "sym", "sig_ms", "entry_ms", "exit_ms", "entry_px_e8", "pnl", "gross", "fees", "funding", "eday")
    return {k: np.concatenate([p[k] for p in parts]) for k in keys}


def load_ep(syms: set) -> dict:
    """Подходы SEL: строки `ep/{wa,wb,wc}.csv` с `ok_t60 = 1` и меткой `y_n`, монеты c539, зачётные сутки окон (L-477 «О2»)."""
    cols = {k: [] for k in ("sym", "win", "sig_ms", "side", "level", "y_n", "lp_e8", "natr_e8", "eday")}
    for w in ("wa", "wb", "wc"):
        lo, hi, root = WINDOWS[w]
        t0 = json.load(open(os.path.join(root, "t0.json"), encoding="utf-8"))[w]
        with open(os.path.join(EP_DIR, w + ".csv"), encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r["ok_t60"] != "1" or r["sym"] not in syms or r["y_n"] not in ("0", "1"):
                    continue
                ms = t0 + int(r["sig_ms"])
                ed = (ms - EPOCH0) // DAY_MS
                if not (day_of(lo) <= ed <= day_of(hi)):
                    continue
                cols["sym"].append(r["sym"])
                cols["win"].append(WIN_NAMES.index(w))
                cols["sig_ms"].append(ms)
                cols["side"].append(1 if r["side"] == "long" else 0)
                cols["level"].append(int(r["level"]))
                cols["y_n"].append(int(r["y_n"]))
                cols["lp_e8"].append(int(r["lp_e8"]))
                cols["natr_e8"].append(int(r["natr_e8"]))
                cols["eday"].append(ed)
    out = {k: np.array(v) for k, v in cols.items()}
    out["sym"] = out["sym"].astype("U24")
    for k in ("win", "side"):
        out[k] = out[k].astype(np.int8)
    for k in ("sig_ms", "lp_e8", "natr_e8", "eday", "level", "y_n"):
        out[k] = out[k].astype(np.int64)
    return out


# ───────────────────────────── признаки по свечам ─────────────────────────────

def coin_features(S: dict, tickd: np.ndarray, m: np.ndarray, lvl: np.ndarray, tr=None, rr=None) -> dict:
    """Признаки на закрытой минуте t = m − 1 для событий монеты (m — минуты сетки G0, lvl — цена уровня): V (доля), цена закрытия, F2, оборот 24 ч, F3, F4, F5, F6."""
    n = len(S["c"])
    t = m.astype(np.int64) - 1
    ok_t = (t >= 0) & (t < n)
    tt = np.clip(t, 0, n - 1)
    nat = L.natr(S, 30, tr)
    js = L.jump_share(S, 60, rr)
    V = np.where(ok_t, nat[tt], np.nan)
    price = np.where(ok_t, S["c"][tt], np.nan)
    tick = tickd[np.clip(tt // 1440, 0, len(tickd) - 1)].astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        F2 = np.where(tick > 0, (tick * 1e-8 / price) / V, np.nan)
    cs = np.concatenate([[0.0], np.cumsum(S["qv"])])
    turn = np.where(ok_t & (t >= 1439), cs[tt + 1] - cs[np.maximum(tt + 1 - 1440, 0)], np.nan)
    hour = ((m.astype(np.int64) * L.MIN_MS + L.G0_MS) // 3_600_000) % 24
    F3 = (hour // 4).astype(np.int64)
    # F4: объём 60 минут / средний такой же в те же минуты суток за 20 суток
    q60 = np.full(n, np.nan)
    q60[59:] = cs[60:n + 1] - cs[:n - 59]
    q_now = np.where(ok_t & (t >= 59), q60[tt], np.nan)
    ref = np.full((len(t), 20), np.nan)
    for k in range(1, 21):
        i = tt - 1440 * k
        v = np.where(ok_t & (i >= 59), q60[np.maximum(i, 0)], np.nan)
        ref[:, k - 1] = v
    nz = np.nansum(np.where(np.isfinite(ref), ref > 0, 0), axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        refm = np.nanmean(ref, axis=1) if len(ref) else np.array([])
        F4 = np.where((nz >= VOL_DAYS_MIN) & (refm > 0), q_now / refm, np.nan)
    # F5: доля объёма за L_DAYS суток в полосе ±TAU вокруг уровня (средняя цена минуты = qv / vol)
    vw = np.full(n, np.nan)
    pos = (S["vol"] > 0) & S["pres"]
    vw[pos] = S["qv"][pos] / S["vol"][pos]
    qv = S["qv"]
    win = L_DAYS * 1440
    F5 = np.full(len(t), np.nan)
    for i in range(len(t)):
        ti = int(t[i])
        if not ok_t[i] or ti < win - 1 or not np.isfinite(lvl[i]) or lvl[i] <= 0:
            continue
        seg, q = vw[ti - win + 1:ti + 1], qv[ti - win + 1:ti + 1]
        tot = q.sum()
        if tot <= 0:
            continue
        with np.errstate(invalid="ignore"):
            inb = np.abs(seg / lvl[i] - 1.0) <= TAU
        F5[i] = q[inb & np.isfinite(seg)].sum() / tot
    F6 = np.where(ok_t, js[tt], np.nan)
    return dict(V=V, price=price, F2=F2, turn24=turn, F3=F3.astype(np.float64), F4=F4, F5=F5, F6=F6)


def coin_task(arg):
    sym, idx, m, lvl = arg
    S = L.load_coin(sym, with_vol=True)
    tickd = L.tick_by_day(S)
    f = coin_features(S, tickd, m, lvl)
    return sym, idx, f


def cmd_features(a):
    syms = read_syms()
    os.makedirs(os.path.join(a.out, "s5"), exist_ok=True)
    tr = load_trades()
    ep = load_ep(set(syms))
    print(f"сделок {len(tr['pnl'])}, подходов SEL {len(ep['y_n'])}", flush=True)
    m_tr = (tr["sig_ms"] - L.G0_MS) // L.MIN_MS
    m_ep = (ep["sig_ms"] - L.G0_MS) // L.MIN_MS
    lvl_tr = tr["entry_px_e8"].astype(np.float64) * 1e-8   # приближение: уровень = цена входа (L-477)
    lvl_ep = ep["lp_e8"].astype(np.float64) * 1e-8
    ev_sym = np.concatenate([tr["sym"], ep["sym"]])
    ev_m = np.concatenate([m_tr, m_ep])
    ev_l = np.concatenate([lvl_tr, lvl_ep])
    ntr = len(m_tr)
    feats = {k: np.full(len(ev_m), np.nan) for k in FEATS}
    only = set(a.only.split(",")) if a.only else None
    tasks = []
    for s in sorted(set(ev_sym.tolist())):
        if only is not None and s not in only:
            continue
        idx = np.where(ev_sym == s)[0]
        tasks.append((s, idx, ev_m[idx], ev_l[idx]))
    t0 = time.time()
    import multiprocessing as mp

    with mp.Pool(a.procs) as pool:
        for k, (sym, idx, f) in enumerate(pool.imap_unordered(coin_task, tasks, chunksize=1)):
            for key in FEATS:
                feats[key][idx] = f[key]
            if k % 25 == 0:
                print(f"[{k + 1}/{len(tasks)}] {sym} событий {len(idx)}  {time.time() - t0:.0f} с", flush=True)
    for name, d, sl in (("trades", tr, slice(0, ntr)), ("ep", ep, slice(ntr, None))):
        z = dict(d)
        for key in FEATS:
            z[key] = feats[key][sl]
        np.savez_compressed(os.path.join(a.out, "s5", name + ".npz"), **z)
    print(f"готово: {len(tasks)} монет за {time.time() - t0:.0f} с", flush=True)


# ───────────────────────────── проверка метода ─────────────────────────────

def match_v(tr: dict, ep: dict, sel_wins) -> dict:
    """Пары «сделка SEL — подход той же монеты в ту же минуту»: V·1e8 против natr_e8 (допуск 1 единица e8); вход против уровня — по ближайшему по времени подходу (≤ 2 с)."""
    key_ep = {}
    for i in range(len(ep["sym"])):
        key_ep.setdefault((str(ep["sym"][i]), int(ep["sig_ms"][i] // L.MIN_MS)), []).append(i)
    by_sym = {}
    for i in range(len(ep["sym"])):
        by_sym.setdefault(str(ep["sym"][i]), []).append(i)
    by_sym = {s: (np.array(v), ep["sig_ms"][np.array(v)]) for s, v in by_sym.items()}
    n_tr = n_match = n_eq = 0
    diffs, rel_entry = [], []
    worst = []
    for i in range(len(tr["sym"])):
        if int(tr["win"][i]) not in sel_wins:
            continue
        n_tr += 1
        s = str(tr["sym"][i])
        mm = int(tr["sig_ms"][i] // L.MIN_MS)
        js = key_ep.get((s, mm))
        if not js:
            continue
        n_match += 1
        v = tr["V"][i]
        e8 = float(ep["natr_e8"][js[0]])
        d = abs(v * 1e8 - e8) if np.isfinite(v) else np.inf
        diffs.append(d)
        if d <= 1.0:
            n_eq += 1
        elif len(worst) < 10:
            worst.append((s, int(tr["sig_ms"][i]), float(v * 1e8) if np.isfinite(v) else None, e8))
        if s in by_sym:
            idx, ms = by_sym[s]
            j = int(np.argmin(np.abs(ms - tr["sig_ms"][i])))
            if abs(int(ms[j]) - int(tr["sig_ms"][i])) <= 2000:
                lp = float(ep["lp_e8"][idx[j]])
                rel_entry.append((float(tr["entry_px_e8"][i]) / lp - 1.0) * 1e4)
    rel_entry = np.array(rel_entry)
    return dict(n_trades_sel=n_tr, n_matched=n_match, n_equal=n_eq, max_abs_diff_e8=float(np.max(diffs)) if diffs else None, worst=worst,
                entry_vs_level_bps=dict(n=int(len(rel_entry)), mean=float(rel_entry.mean()) if len(rel_entry) else None, abs_median=float(np.median(np.abs(rel_entry))) if len(rel_entry) else None,
                                        abs_p90=float(np.percentile(np.abs(rel_entry), 90)) if len(rel_entry) else None, abs_max=float(np.abs(rel_entry).max()) if len(rel_entry) else None))


def cmd_check(a):
    tr = dict(np.load(os.path.join(a.out, "s5", "trades.npz"), allow_pickle=False))
    ep = dict(np.load(os.path.join(a.out, "s5", "ep.npz"), allow_pickle=False))
    sel = {WIN_NAMES.index(w) for w in ("wa", "wb", "wc")}
    r = match_v(tr, ep, sel)
    # охват признаков (число определённых значений) — без исходов
    cov = {k: float(np.isfinite(tr[k]).mean()) for k in FEATS}
    cov_ep = {k: float(np.isfinite(ep[k]).mean()) for k in FEATS}
    passed = bool(r["n_matched"] >= 100 and r["n_equal"] == r["n_matched"])
    out = dict(check="V = NATR30[t−1] по свечам против natr_e8 таблицы подходов (SEL, пара — монета и та же минута), допуск 1 единица e8", passed=passed, **r,
               coverage_trades=cov, coverage_ep=cov_ep, n_trades=int(len(tr["pnl"])), n_ep=int(len(ep["y_n"])))
    json.dump(out, open(os.path.join(a.out, "method_check_s5.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("passed", "n_trades_sel", "n_matched", "n_equal", "max_abs_diff_e8", "entry_vs_level_bps")}, ensure_ascii=False))
    if not passed:
        print("worst:", r["worst"])
        sys.exit(1)


def cmd_analyze(a):
    chk = os.path.join(a.out, "method_check_s5.json")
    if not (os.path.exists(chk) and json.load(open(chk, encoding="utf-8")).get("passed")):
        sys.exit("analyze: нет успешного method_check_s5.json — сперва `check` (§13.78.5: проверка метода до чтения)")
    from tk727_s5_an import analyze

    analyze(a.out, a.procs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["features", "check", "analyze"])
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    {"features": cmd_features, "check": cmd_check, "analyze": cmd_analyze}[a.cmd](a)


if __name__ == "__main__":
    main()
