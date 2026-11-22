#!/usr/bin/env python3
"""П-42 §13.76 (TK-685): стадии разбора сделок по группам исхода строго по PS-970 + поправка PS-971 (ключ присоединения).
Стадии: trades → path --win wa|wb|wc → pathgate → join → half1 → half2 → plots → report. Половина 2 читается один раз (замок `half2.lock`).
Запуск (srv): `cd /data/p42-tk685/code && /data/tk592/venv/bin/python tk685_run.py <стадия>`; выход — `/data/p42-tk685/out/`."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk604_stats as st  # noqa: E402
import tk685_lib as lib  # noqa: E402
import tk685_trades as T  # noqa: E402

OUT = os.environ.get("TK685_OUT", "/data/p42-tk685/out")
MATRIX = "/data/p42-tk671/data/{}.parquet"
TAPE_ROOT = "/data/p42-tk599/{}/aggtrades"
SETS = tuple(T.SETS)
SIGN = {"L": 1, "S": -1}
ALPHA_SET = 0.05 / 8
B = 4000
GATE_TAPE, GATE_ENTRY, GATE_JOIN = 0.97, 0.95, 0.95        # G-tape 0,99 → 0,97 и допуск входа — поправка PS-972 (13.76б)
ENTRY_TOL_BP = 1.5
SLIP = 1e-4                                                  # проскальзывание движка на ногу (1 бп): вход = цена ленты, сдвинутая против позиции
MIN_GROUP = 30
CONTRASTS = ("C1", "C2")


def p(*a):
    return os.path.join(OUT, *a)


def jdump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def load_trades(name):
    return pd.read_csv(p("trades", name + ".csv"))


# ───────────────────────────── trades ─────────────────────────────

def half_metrics(df, h):
    """Метрики набора по половине (сделки с входом в половине; суточный ряд — сутки половины, выход за границу — в последние сутки половины)."""
    lo, hi = (0, lib.HALF_SPLIT_DAY) if h == 1 else (lib.HALF_SPLIT_DAY, st.NDAYS)
    sub = df[df["half"] == h]
    return lib.set_metrics(sub["day_out"].to_numpy() - lo, sub["net"].to_numpy(), nd=hi - lo)


def group_table(sub):
    rows = []
    for g in (lib.LOSS, lib.ZERO, lib.PROFIT):
        s = sub[sub["group"] == g]
        rows.append({"group": lib.GROUP_NAMES[g], "n": int(len(s)), "share": float(len(s) / len(sub)) if len(sub) else float("nan"),
                     "sum": float(s["net"].sum()), "mean": float(s["net"].mean()) if len(s) else float("nan")})
    return rows


def kind_table(sub):
    return {lib.GROUP_NAMES[g]: {k: [int(len(x)), float(x["net"].sum())] for k, x in sub[sub["group"] == g].groupby("kind")} for g in (lib.LOSS, lib.ZERO, lib.PROFIT)}


def stage_trades():
    """4 набора → out/trades/<набор>.csv; печать — только то, что не раскрывает половину 2 (весь счёт — в json)."""
    full = {}
    for name in SETS:
        df = T.load_set(name)
        df["row"] = np.arange(len(df))
        os.makedirs(p("trades"), exist_ok=True)
        df.to_csv(p("trades", name + ".csv"), index=False)
        sizes = T.group_sizes(df)
        full[name] = {"n": len(df), "sum": float(df["net"].sum()), "sizes": {"%s-%d" % k: v for k, v in sizes.items()},
                      "metrics": {"all": lib.set_metrics(df["day_out"].to_numpy(), df["net"].to_numpy()), "h1": half_metrics(df, 1), "h2": half_metrics(df, 2)},
                      "groups_all": group_table(df), "groups_h1": group_table(df[df["half"] == 1]), "groups_h2": group_table(df[df["half"] == 2]),
                      "kinds_all": kind_table(df), "kinds_h1": kind_table(df[df["half"] == 1])}
        h1 = df[df["half"] == 1]
        m1 = full[name]["metrics"]["h1"]
        print("%s n=%d Σ=%.3f | h1: n=%d Σ=%.2f mean=%.3f sharpe=%.2f dd=%.1f$ rr=%.2f | h1 groups net: %s | gross-rule h1: %s" % (
            name, len(df), df["net"].sum(), m1["n"], m1["sum"], m1["mean"], m1["sharpe"], m1["maxdd"], m1["rr"],
            [g["n"] for g in full[name]["groups_h1"]], sizes[("gross", 1)]))
    jdump(full, p("trades", "summary.json"))


# ───────────────────────────── path ─────────────────────────────

def stage_path(win):
    w = {x.name: x for x in st.TRADE_WINDOWS}[win]
    os.environ["SIGFEAT_BASE_DATE"] = w.t0
    os.environ["SIGFEAT_DAYS"] = "74"
    import tk671_ticks as TK
    root = TAPE_ROOT.format(win)
    last = st.LAST_MS - w.t0_ms - 1
    cols_k = ["mfe_k%d" % m for m in lib.CHECK_MIN] + ["mae_k%d" % m for m in lib.CHECK_MIN]
    for name in SETS:
        df = load_trades(name)
        sub = df[df["win"] == win].sort_values(["sym", "entry_ms"], kind="stable")
        sign = SIGN[T.SETS[name][0]]
        rows, cur, tk = [], None, None
        for r in sub.itertuples():
            if r.sym != cur:
                cur, tk = r.sym, TK.StoreTicks(root, r.sym)
            a, b = int(r.entry_ms), min(int(r.exit_ms), last)
            t, px, full = tk.span(a, b)
            res = lib.path_one(t, px, a, int(r.exit_ms), int(r.entry_px_e8), sign, float(r.fees))
            te, pe, _ = tk.span(a - 1000, a + 1000)
            miss_d = [d_ for d_ in range(max(a // st.DAY_MS, 0), min(b // st.DAY_MS, 73) + 1) if tk.day(d_) is None]
            miss = len(miss_d)
            row = {"row": int(r.row), "ticks": res["ticks"], "days_missing": miss, "tape_ok": bool(full and res["ticks"] > 0 and int(r.exit_ms) <= last),
                   "entry_ok": lib.entry_in_range(te, pe, a, int(r.entry_px_e8)), "entry_dist_bp": lib.entry_dist_bp(te, pe, a, int(r.entry_px_e8)),
                   "entry_dist_adj_bp": lib.entry_dist_bp(te, pe, a, int(r.entry_px_e8) / (1.0 + sign * SLIP)),
                   "miss_dates": ";".join((w.t0d + datetime.timedelta(days=d_)).isoformat() for d_ in miss_d)}
            for k in ("mfe", "mae", "t_mfe", "t_mae", "green", "green_first", "exit_move", "giveback", "giveback_share", "hold_min", "mae_to_exit_min"):
                row[k] = res[k]
            for m, v in zip(lib.CHECK_MIN, res["mfe_k"]):
                row["mfe_k%d" % m] = v
            for m, v in zip(lib.CHECK_MIN, res["mae_k"]):
                row["mae_k%d" % m] = v
            rows.append(row)
        out = pd.DataFrame(rows)
        os.makedirs(p("path"), exist_ok=True)
        out.to_csv(p("path", "%s-%s.csv" % (name, win)), index=False)
        print("%s %s: %d trades, tape_ok %d, entry_ok %d" % (name, win, len(out), int(out["tape_ok"].sum()), int(out["entry_ok"].sum())))


def stage_pathgate():
    """Ворота пути с поправкой 13.76б: G-tape ≥ 97 %, G-tape-entry — вход в [min − 1,5 бп, max + 1,5 бп] ленты за ±1 с у ≥ 95 % сделок с лентой."""
    res, bad = {}, []
    for name in SETS:
        df = load_trades(name)
        parts = [pd.read_csv(p("path", "%s-%s.csv" % (name, w))) for w in ("wa", "wb", "wc")]
        pa = pd.concat(parts, ignore_index=True)
        assert len(pa) == len(df) and set(pa["row"]) == set(df["row"]), (name, len(pa), len(df))
        pa = pa.merge(df[["row", "group", "half", "net", "fees", "kind", "sym", "win", "day_in"]], on="row")
        pa.to_csv(p("path", name + ".csv"), index=False)
        n = len(pa)
        with_tape = pa["entry_dist_bp"].notna()
        g_tape = pa["tape_ok"].mean()
        g_exact = float((pa.loc[with_tape, "entry_dist_adj_bp"] == 0).mean())
        g_tol = float((pa.loc[with_tape, "entry_dist_bp"] <= ENTRY_TOL_BP).mean())
        used = "точная (без проскальзывания)" if g_exact >= GATE_ENTRY else "запасной допуск 1,5 бп"
        g_entry = g_exact if g_exact >= GATE_ENTRY else g_tol
        ex = pa[~pa["tape_ok"]]
        cd = ex.assign(dates=ex["miss_dates"].fillna("")).assign(date=lambda x: x["dates"].str.split(";")).explode("date")
        cd = cd[cd["date"] != ""].groupby(["sym", "date"]).size().reset_index(name="trades")
        res[name] = {"n": n, "tape_ok": int(pa["tape_ok"].sum()), "g_tape": float(g_tape), "n_with_tape_1s": int(with_tape.sum()), "g_entry": g_entry, "g_entry_exact": g_exact, "g_entry_tol": g_tol, "entry_check": used,
                     "entry_strict_share": float((pa.loc[with_tape, "entry_dist_bp"] == 0).mean()), "entry_max_bp": float(pa["entry_dist_bp"].max()),
                     "excluded": int(len(ex)), "excluded_coins": {k: int(v) for k, v in ex["sym"].value_counts().items()},
                     "excluded_by_group": {"h1": [int(((ex["half"] == 1) & (ex["group"] == g)).sum()) for g in (0, 1, 2)], "all": [int((ex["group"] == g).sum()) for g in (0, 1, 2)]},
                     "excluded_days_missing_gt0": int((ex["days_missing"] > 0).sum()), "coin_days": cd.to_dict("records")}
        print("%s: G-tape %d/%d = %.2f %% (≥ 97 %%), исключено %d (монет %d, монета-суток %d; по группам половины 1: убыток / около нуля / прибыль = %s); G-tape-entry: точная %.2f %%, допуск 1,5 бп %.2f %%, строго внутри %.1f %%, макс. %.2f бп (использована: %s)" % (
            name, res[name]["tape_ok"], n, g_tape * 100, len(ex), ex["sym"].nunique(), len(cd), res[name]["excluded_by_group"]["h1"], g_exact * 100, g_tol * 100, res[name]["entry_strict_share"] * 100, res[name]["entry_max_bp"], used))
        if g_tape < GATE_TAPE or g_entry < GATE_ENTRY:
            bad.append(name)
    jdump(res, p("path", "gate.json"))
    if bad:
        sys.exit("ворота пути не пройдены: %s" % bad)


# ───────────────────────────── join (PS-970 п. 5 + поправка PS-971) ─────────────────────────────

def pick_rows(df, mat, side):
    """Ключ (win, sym, sig_ms); дубль — строка с ближайшей к entry_px_e8 подразумеваемой ценой сигнала, далее min meta_dist_e8, далее порядок матрицы (PS-971).
    Возвращает (индекс строки матрицы или −1, код правила: 0 нет строки, 1 без дубля, 2 по цене, 3 по meta_dist_e8, 4 по порядку)."""
    key = mat[["win", "sym", "meta_sig_ms"]].copy()
    key["ri"] = np.arange(len(mat))
    key["lp"] = mat["meta_level_px_e8"].to_numpy(float)
    key["dist"] = mat["meta_dist_e8"].to_numpy(float)
    where = {}
    for k, ri in zip(zip(key["win"], key["sym"], key["meta_sig_ms"]), key["ri"]):
        where.setdefault(k, []).append(ri)
    lp, dist = key["lp"].to_numpy(), key["dist"].to_numpy()
    idx = np.full(len(df), -1, np.int64)
    rule = np.zeros(len(df), np.int8)
    for a, (w, s, sg, ep) in enumerate(zip(df["win"], df["sym"], df["sig_ms"], df["entry_px_e8"])):
        ids = where.get((w, s, int(sg)))
        if not ids:
            continue
        if len(ids) == 1:
            idx[a], rule[a] = ids[0], 1
            continue
        ids = np.array(ids)
        impl = lp[ids] * (1.0 - dist[ids] / 1e8) if side == "L" else lp[ids] * (1.0 + dist[ids] / 1e8)
        err = np.abs(impl - ep)
        if not np.all(np.isnan(err)) and np.sum(err == np.nanmin(err)) == 1:
            idx[a], rule[a] = ids[int(np.nanargmin(err))], 2
            continue
        tie = ids[err == np.nanmin(err)] if not np.all(np.isnan(err)) else ids
        d = dist[tie]
        if not np.all(np.isnan(d)) and np.sum(d == np.nanmin(d)) == 1:
            idx[a], rule[a] = tie[int(np.nanargmin(d))], 3
        else:
            idx[a], rule[a] = tie[0], 4
    return idx, rule


def stage_join():
    import pyarrow.parquet as pq
    os.makedirs(p("feat"), exist_ok=True)
    report = {}
    for side, f in (("L", "long"), ("S", "short")):
        pf = pq.ParquetFile(MATRIX.format(f))
        fc = lib.feature_columns(pf.schema_arrow.names)
        assert len(fc) == 222, len(fc)
        mat = pq.read_table(MATRIX.format(f), columns=["win", "sym", "meta_sig_ms", "meta_level_px_e8", "meta_dist_e8"]).to_pandas()
        mat["sym"] = mat["sym"].astype(str)
        mat["win"] = mat["win"].astype(str)
        for name in [s for s in SETS if T.SETS[s][0] == side]:
            df = load_trades(name)
            idx, rule = pick_rows(df, mat, side)
            ok = idx >= 0
            cover = float(ok.mean())
            cnt = [int((rule == c).sum()) for c in range(5)]
            rep = {"n": len(df), "joined": int(ok.sum()), "cover": cover, "rules": {"нет строки": cnt[0], "без дубля": cnt[1], "по цене": cnt[2], "по meta_dist_e8": cnt[3], "по порядку": cnt[4]},
                   "gate": bool(cover >= GATE_JOIN)}
            print("%s: присоединено %d / %d = %.2f %% (ворота ≥ 95 %%: %s); без строки %d, без дубля %d, по цене %d, по meta_dist_e8 %d, по порядку %d" % (
                name, ok.sum(), len(df), cover * 100, "ДА" if rep["gate"] else "НЕТ", *cnt))
            report[name] = rep
            if not rep["gate"]:
                continue
            take = idx[ok]
            cols = {}
            for c0 in range(0, len(fc), 60):
                part = pq.read_table(MATRIX.format(f), columns=fc[c0:c0 + 60]).take(take).to_pandas()
                for c in part.columns:
                    cols[c] = part[c].to_numpy(float)
            feat = pd.DataFrame(cols)
            feat.insert(0, "dup", (rule[ok] > 1))
            feat.insert(0, "row", df["row"].to_numpy()[ok])
            feat.to_parquet(p("feat", name + ".parquet"), index=False)
    jdump(report, p("feat", "join.json"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["trades", "path", "pathgate", "join", "half1", "half2", "plots", "report"])
    ap.add_argument("--win", choices=["wa", "wb", "wc"])
    a = ap.parse_args()
    if a.stage == "trades":
        stage_trades()
    elif a.stage == "path":
        stage_path(a.win)
    elif a.stage == "pathgate":
        stage_pathgate()
    elif a.stage == "join":
        stage_join()
    else:
        import tk685_stages as S
        getattr(S, "stage_" + a.stage)()


if __name__ == "__main__":
    main()
