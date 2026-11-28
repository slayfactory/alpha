#!/usr/bin/env python3
"""TK-739 (П-42 шаг 8, §13.81.2–§13.81.3): реальные ряды — Π на полной выборке (кандидат), вложенная проверка (45 разбиений), 9 путей CPCV, PBO, C′, плато коробки, правило O1–O7. Только srv; после `tk739_series.py series design`.

  python3 tk739_real.py full              # Π на 286 сутках → кандидат c* (res/full.json, tk739-candidate.json); PRINT: допущено, λ, плато, ŷ, граница
  python3 tk739_real.py nested            # 45 запусков Π, PBO, пути, C′ (блоки 7 и 21; 20 000), описательные → res/nested.json (+ s8_split / s8_path / s8_pbo)
  python3 tk739_real.py plateau           # после коробки (`tk739_series.py series box`): плато B, B′, Шарп соседей → res/plateau.json; исход O1–O7 → res/outcome.json
Метрики п. 9 (В-169): Σ $, сделок, $/сделку, Шарп (годовой из суточного ряда с нулями), просадка $ и % маржи 250 $, перехай (дни между максимумами), R:R, убыток на стоп, доля плюсовых суток.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")
import tk739_design as dz  # noqa: E402
import tk739_pow as pw  # noqa: E402
import tk739_proc as P  # noqa: E402

ROOT = os.environ.get("TK739_ROOT", "/data/p42-tk739")
RES = ROOT + "/res"
DB739 = ROOT + "/tk739.sqlite"
CAND = ROOT + "/tk739-candidate.json"
MARGIN = 250.0
FJ = 150


def jwrite(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True, default=_jd)


def _jd(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(type(o))


def fz():
    return json.load(open(ROOT + "/tk739-frozen.json", encoding="utf-8"))


def load_real():
    des = pw.load_design(fz()["S"])
    z = np.load(RES + "/series-design.npz")
    idx = {int(c): i for i, c in enumerate(z["cids"])}
    db = sqlite3.connect(DB739)
    ck2cid = dict(db.execute("SELECT ckey, cid FROM s8_point"))
    rows = [idx[ck2cid[ck]] for ck in des.ckeys]
    X, cnt = z["pnl"][rows], z["cnt"][rows].astype(float)
    base, base_n = X[des.base_idx], cnt[des.base_idx]
    b0 = np.load(RES + "/bstar_daily.npy")
    print("база (OAT base) против G-src: max |Δ| суточного $ = %.2e; сделок равны: %s" % (np.abs(base - b0).max(), bool((base_n == np.load(RES + "/bstar_n.npy")).all())))
    return des, X, cnt, base, base_n, z, rows


def metrics(pnl, cnt=None, tl=None, nstop=None, usd_stop=None):
    """Метрики п. 9 по суточному ряду (286 суток, нули входят)."""
    pnl = np.asarray(pnl, float)
    n = len(pnl)
    sd = pnl.std(ddof=1)
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    dd = float((peak - eq).max())
    new_hi = np.nonzero(eq >= peak - 1e-12)[0]
    gaps = np.diff(np.concatenate([[-1], new_hi, [n]])) if len(new_hi) else np.array([n])
    out = dict(sum_usd=float(pnl.sum()), mean_day=float(pnl.mean()), sharpe_ann=float(pnl.mean() / sd * np.sqrt(365)) if sd > 1e-12 else None, days=n, share_pos_days=float((pnl > 0).mean()),
               maxdd_usd=dd, maxdd_pct_margin=dd / MARGIN * 100.0, perehigh_max=int(gaps.max()), perehigh_mean=float(gaps.mean()), perehigh_p90=float(np.percentile(gaps, 90)))
    if cnt is not None:
        out["trades"] = int(np.sum(cnt))
        out["usd_per_trade"] = float(pnl.sum() / max(np.sum(cnt), 1))
    if tl is not None and tl[0] > 0 and tl[2] > 0:
        out["rr"] = float((tl[1] / tl[0]) / abs(tl[3] / tl[2]))
        out["worst_trade"] = float(tl[4])
    if nstop is not None:
        out["stops"] = int(nstop)
        out["usd_per_stop"] = float(usd_stop / nstop) if nstop else None
    return out


def cand_file(des, c, sel, z, rows):
    coords = des.coords[c]
    ck = des.ckeys[c]
    h = hashlib.sha256()
    h.update(ck.encode())
    h.update(np.ascontiguousarray(z["pnl"][rows[c]]).tobytes())
    return dict(candidate=dict(row=int(c), role=des.roles[c], label=des.labels[c], ckey=ck, coords={k: (list(v) if isinstance(v, tuple) else v) for k, v in coords.items()}, sha256=h.hexdigest()),
                select={k: v for k, v in sel.items()}, S=fz()["S"])


def cmd_full(a):
    des, X, cnt, base, base_n, z, rows = load_real()
    sel = P.select(np.arange(286), X, cnt, base, des.F, des.NB, des.NBm, seed=P.SEED, nboot=P.NB_BOOT, cand_ok=des.cand_ok)
    rep = dict(select=sel, n_points=int(len(X)))
    c = sel["c"]
    if c is None:
        rep["refused"] = True
        jwrite(RES + "/full.json", rep)
        print("Π на полной: ОТКАЗ (кандидата нет), допущено %d" % sel["n_adm"])
        return 0
    border, ax = dz.near_border(des.coords[c], None)
    rep.update(refused=False, border=bool(border), border_axis=ax, row=int(c), role=des.roles[c], label=des.labels[c], coords=des.coords[c])
    jwrite(RES + "/full.json", rep)
    jwrite(CAND, cand_file(des, c, sel, z, rows))
    print("Π на полной: кандидат строка %d (%s %s), допущено %d, λΣ %.3g λSR %.3g, LB %.3f, плато %.3f, ŷΣ %.3f $/сутки, у границы: %s %s" % (
        c, des.roles[c], des.labels[c], sel["n_adm"], sel["lam_sigma"], sel["lam_sr"], sel["lb"], sel["plateau"], sel["yhat"], border, ax))
    print("координаты:", json.dumps(des.coords[c], default=_jd, ensure_ascii=False))
    return 0


def cmd_nested(a):
    des, X, cnt, base, base_n, z, rows = load_real()
    spl = P.splits()
    nst = P.nested(X, cnt, base, base_n, des.F, des.NB, des.NBm, des.cand_ok, des.Rn, spl=spl, seed=P.SEED, nboot=P.NB_BOOT)
    paths, ser = P.path_series(nst["test_delta"], nst["split_ij"])
    c7, m7 = P.cprime(ser, 7)
    c21, m21 = P.cprime(ser, 21)
    om = np.array(nst["omega"])
    ch = nst["choices"]
    rep = dict(pbo=nst["pbo"], refusals=nst["refusals"], invalid=nst["invalid"], c7=c7, c21=c21, mean_diff_day=m7, n_splits=len(spl))
    fin = np.isfinite(om)
    rep["pbo_logit"] = float(np.mean(np.log(np.clip(om[fin], 1e-9, 1 - 1e-9) / (1 - np.clip(om[fin], 1e-9, 1 - 1e-9))) <= 0)) if fin.any() else None
    neg = [float(nst["test_delta"][s, spl[s][3]].sum()) < 0 for s in range(len(spl))]
    rep["share_neg"] = float(np.mean(neg))
    sr_tr, sr_te = [], []
    for s, (i, j, tr, te) in enumerate(spl):
        c = ch[s]
        if c is None:
            continue
        sr_tr.append(X[c, tr].mean() / max(X[c, tr].std(ddof=1), 1e-12))
        sr_te.append(X[c, te].mean() / max(X[c, te].std(ddof=1), 1e-12))
    if len(sr_tr) > 2:
        rep["train_test_sharpe"] = dict(n=len(sr_tr), corr=float(np.corrcoef(sr_tr, sr_te)[0, 1]), slope=float(np.polyfit(sr_tr, sr_te, 1)[0]))
    half = lambda g: 0 if g <= 4 else 1                             # noqa: E731  — группы 0–4 = FJ (150 суток), 5–9 = SEL
    hit = np.array([(c is None) or (not np.isfinite(w)) or (w <= 0.5) or (cnt[c, spl[s][3]].sum() == 0) for s, (c, w) in enumerate(zip(ch, om))])
    for h in (0, 1):
        m = np.array([half(i) == h and half(j) == h for (i, j, _t, _e) in spl])
        rep["pbo_half_%s" % ("FJ" if h == 0 else "SEL")] = dict(n=int(m.sum()), pbo=float(hit[m].mean()))
    rep["path_metrics"] = [metrics(paths[k]) for k in range(len(paths))]
    rep["rule_series_diff"] = metrics(ser)
    abs_ser = base + ser
    rep["rule_series_abs"] = metrics(abs_ser)
    rep["base"] = metrics(base, base_n)
    rep["base_note_abs"] = "абсолютный тестовый ряд = база + разность; «база построена на этих сутках» — решения не двигает"
    rep["choice_rows"] = [None if c is None else int(c) for c in ch]
    rep["choice_labels"] = [None if c is None else "%s %s" % (des.roles[c], des.labels[c]) for c in ch]
    rep["omega"] = [None if not np.isfinite(w) else float(w) for w in om]
    jwrite(RES + "/nested.json", rep)
    np.savez(RES + "/nested-series.npz", test_delta=nst["test_delta"], path=paths, rule=ser)
    db = sqlite3.connect(DB739)
    db.executescript("CREATE TABLE IF NOT EXISTS s8_split(s INTEGER PRIMARY KEY, g1 INTEGER, g2 INTEGER, choice INTEGER, omega REAL, sum_test REAL);"
                     "CREATE TABLE IF NOT EXISTS s8_path(p INTEGER, day INTEGER, delta REAL, PRIMARY KEY(p, day)) WITHOUT ROWID;"
                     "CREATE TABLE IF NOT EXISTS s8_pbo(key TEXT PRIMARY KEY, value REAL);")
    db.execute("DELETE FROM s8_split")
    db.execute("DELETE FROM s8_path")
    db.execute("DELETE FROM s8_pbo")
    db.executemany("INSERT INTO s8_split VALUES(?,?,?,?,?,?)", ((s, spl[s][0], spl[s][1], None if ch[s] is None else int(ch[s]), None if not np.isfinite(om[s]) else float(om[s]), float(nst["test_delta"][s, spl[s][3]].sum())) for s in range(len(spl))))
    db.executemany("INSERT INTO s8_path VALUES(?,?,?)", ((p, d, float(paths[p, d])) for p in range(len(paths)) for d in range(286)))
    db.executemany("INSERT INTO s8_pbo VALUES(?,?)", [("pbo", nst["pbo"]), ("c7", c7), ("c21", c21), ("refusals", nst["refusals"]), ("invalid", nst["invalid"])])
    db.commit()
    print("вложенная: PBO %.3f (порог ≤ 0,2), отказов Π %d из 45, недопустимых %d; C′(7) = %.3f, C′(21) = %.3f $/сутки; средняя разность %.3f; share_neg %.2f; PBO(логит) %s" % (
        nst["pbo"], nst["refusals"], nst["invalid"], c7, c21, m7, rep["share_neg"], rep["pbo_logit"]))
    print("тестовый ряд правила (разность): %s" % json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in rep["rule_series_diff"].items()}, ensure_ascii=False))
    return 0


def cmd_plateau(a):
    des, X, cnt, base, base_n, z, rows = load_real()
    cd = json.load(open(CAND, encoding="utf-8"))
    nst = json.load(open(RES + "/nested.json", encoding="utf-8"))
    full = json.load(open(RES + "/full.json", encoding="utf-8"))
    zb = np.load(RES + "/series-box.npz")
    pnl = zb["pnl"]
    nbx = len(pnl)
    sj, ss = pnl[:, :FJ].sum(1), pnl[:, FJ:].sum(1)
    dj, ds = (pnl[:, :FJ] - base[:FJ]).sum(1), (pnl[:, FJ:] - base[FJ:]).sum(1)
    sd = pnl.std(axis=1, ddof=1)
    sr = np.where(sd > 1e-9, pnl.mean(1) / np.where(sd > 1e-9, sd, 1) * np.sqrt(365), np.nan)
    rep = dict(neighbors=int(nbx), share_pos_FJ=float((sj > 0).mean()), share_pos_SEL=float((ss > 0).mean()), pair_share_pos_FJ=float((dj > 0).mean()), pair_share_pos_SEL=float((ds > 0).mean()),
               sharpe_mean=float(np.nanmean(sr)), sharpe_min=float(np.nanmin(sr)), B=bool((sj > 0).mean() >= 2 / 3 and (ss > 0).mean() >= 0.5))
    A_ = nst["pbo"] <= 0.2
    c7, c21 = nst["c7"], nst["c21"]
    border = bool(full.get("border"))
    if nst["invalid"] > 0:
        out, why = "O5", "недопустимое разбиение"
    elif full.get("refused"):
        out, why = "O2", "Π на полной выборке отказала"
    elif (not A_) or (not rep["B"]) or (c7 <= 0 and c21 <= 0):
        out, why = "O2", "¬A ∨ ¬B ∨ C′ ≤ 0 при обоих блоках"
    elif (c7 > 0) != (c21 > 0) or border:
        out, why = "O3", ("знак C′ разный" if (c7 > 0) != (c21 > 0) else "центр у границы диапазона (%s)" % full.get("border_axis"))
    else:
        out, why = "O1", "A ∧ B ∧ C′ при обоих блоках, центр не у границы"
    note = "нет плато на обучении (отказов Π > ½ из 45)" if nst["refusals"] > 22 else ""
    rep.update(A=bool(A_), pbo=nst["pbo"], c7=c7, c21=c21, border=border, outcome=out, why=why, note=note)
    jwrite(RES + "/plateau.json", rep)
    jwrite(RES + "/outcome.json", dict(outcome=out, why=why, A=bool(A_), B=rep["B"], C7=c7, C21=c21, border=border, note=note))
    print("плато B: ≥ ⅔ на FJ: %.3f (нужно ≥ 0,667), ≥ ½ на SEL: %.3f (≥ 0,5) → B %s; парное B′: FJ %.3f / SEL %.3f; Шарп соседей среднее %.2f, мин %.2f" % (
        rep["share_pos_FJ"], rep["share_pos_SEL"], rep["B"], rep["pair_share_pos_FJ"], rep["pair_share_pos_SEL"], rep["sharpe_mean"], rep["sharpe_min"]))
    print("ИСХОД %s: %s; A %s (PBO %.3f), B %s, C′(7) %.3f, C′(21) %.3f, граница %s %s" % (out, why, A_, nst["pbo"], rep["B"], c7, c21, border, note))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n, f in (("full", cmd_full), ("nested", cmd_nested), ("plateau", cmd_plateau)):
        sp.add_parser(n).set_defaults(fn=f)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
