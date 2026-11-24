#!/usr/bin/env python3
"""TK-699 шаг 3: чтение пересчёта клеток c539 (запись L-402): сколько клеток пересчитано и сколько оставлено по записям, какие верхние строки троек (L-355 + L-363) и четвёрок (L-385)
шорта поменяли место после пересчёта. Только чтение БД.

  tk699_read.py --out read-L402.json [--top 15]
Текущее значение клетки: строка TK690-xb (bd-пересчёт), иначе основная (01.07–13.11); после пересчёта — строка TK699-f12 поверх текущей. Охраны (а)–(г) как `tk674_run.guards`
(база шорта — запись L-334, стоп 4 %: клетка s4). Ранг — по Шарпу среди клеток, прошедших охраны; справа — п. 35: Σ $, сделки, просадка $ и % маржи, перехай (самый долгий / средний / хвост, сут.).
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import time

MAIN, XB, F12 = "01.07–13.11", "TK690-xb", "TK699-f12"
DB = "/data/chron/chron.sqlite"
GUARD_N, GUARD_M = 81, (9, 9, 9, 9, 5)           # охрана (а) c539 шорт
BASE_CKEY = "bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20"


def con_ro():
    for k in range(30):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=120)
            c.execute("select 1 from metric limit 1")
            return c
        except sqlite3.OperationalError as e:
            print("retry", k, e, flush=True)
            time.sleep(20)
    raise SystemExit("БД занята")


def rows(con, win, entries=None, side="short"):
    out = {}
    q = "select id, entry, cell, ckey, trades, usd, sharpe, extra from metric where uni='c539' and side=? and win=?"
    args = [side, win]
    if entries:
        q += " and entry in (%s)" % ",".join("?" * len(entries))
        args += list(entries)
    for r in con.execute(q, args):
        x = json.loads(r[7])
        out[r[3]] = dict(id=r[0], entry=r[1], cid=r[2], ckey=r[3], n=r[4], usd=r[5], sharpe=r[6], months=x["months"], maxdd=x["maxdd"], maxdd_pct=x["maxdd_pct"],
                         kpi=x["kpi"], lmax=x["lmax"])
    return out


def guards(r, b):
    k_base = sum(1 for m in b["months"] if m[1] > 0)
    a = r["n"] >= GUARD_N and all(m[0] >= mn for m, mn in zip(r["months"], GUARD_M))
    bb = sum(1 for m in r["months"] if m[1] > 0) >= min(4, k_base)
    c = r["maxdd"] <= 2 * b["maxdd"]
    d = r["lmax"]["usd"] <= b["lmax"]["usd"] + 1e-9
    return a and bb and c and d


def line(r, base):
    k = r["kpi"]
    r = dict(r, sharpe=r["sharpe"] if r["sharpe"] is not None else float("nan"), usd=r["usd"] if r["usd"] is not None else float("nan"))
    return "%s | %d сд. %+.0f $ Шарп %.2f (%+.2f к базе) просадка %.0f $ (%.0f %% маржи) перехай %d / %.1f / %d" % (
        r["ckey"].split(" F=", 1)[-1][:90] if " F=" in r["ckey"] else r["cid"][:90], r["n"], r["usd"], r["sharpe"], r["sharpe"] - base["sharpe"], r["maxdd"], r["maxdd_pct"],
        k["gap_longest"], k["gap_mean"], k["gap_tail"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--singles-only", action="store_true")
    a = ap.parse_args()
    con = con_ro()
    base = rows(con, MAIN, entries=(334,))[BASE_CKEY]
    sets = dict(troiki=(355, 363), chetvyorki=(385,))
    xb = rows(con, XB)
    f12 = rows(con, F12)
    rep = dict(base=dict(n=base["n"], usd=base["usd"], sharpe=base["sharpe"], maxdd=base["maxdd"], maxdd_pct=base["maxdd_pct"], kpi=base["kpi"]), recount_rows=len(f12), sets={})
    for name, ents in ({} if a.singles_only else sets).items():
        main_rows = rows(con, MAIN, entries=ents)
        old = {k: xb.get(k, r) for k, r in main_rows.items()}
        new = {k: f12.get(k, r) for k, r in old.items()}
        per_entry = collections.Counter()
        kept_entry = collections.Counter()
        for k, r in main_rows.items():
            (per_entry if k in f12 else kept_entry)[r["entry"]] += 1
        ok_old = sorted((r for r in old.values() if guards(r, base)), key=lambda r: -(r["sharpe"] or -99.0))
        ok_new = sorted((r for r in new.values() if guards(r, base)), key=lambda r: -(r["sharpe"] or -99.0))
        rank_old = {r["ckey"]: i + 1 for i, r in enumerate(ok_old)}
        rank_new = {r["ckey"]: i + 1 for i, r in enumerate(ok_new)}
        top_old = ok_old[:a.top]
        top_new = ok_new[:a.top]
        changed_ck = {k for k in main_rows if k in f12}
        print("== %s (L-%s): клеток %d; пересчитано (TK699-f12) %s; оставлено %s; охраны проходят: до %d, после %d" % (
            name, "+".join(map(str, ents)), len(main_rows), dict(per_entry), dict(kept_entry), len(ok_old), len(ok_new)), flush=True)
        print("  верх до пересчёта (по Шарпу, охраны а–г):", flush=True)
        for r in top_old:
            n_ = new[r["ckey"]]
            print("   #%d -> #%s %s | %s" % (rank_old[r["ckey"]], rank_new.get(r["ckey"], "вне охран"), "пересчитана" if r["ckey"] in changed_ck else "та же", line(r, base)), flush=True)
            if r["ckey"] in changed_ck:
                print("          после: %s" % line(n_, base), flush=True)
        rec = [r for r in ok_new if r["ckey"] in changed_ck][:5]
        print("  лучшие из пересчитанных (после; ранг после / ранг до среди охран):", flush=True)
        for r in rec:
            print("   #%d (было %s) | %s | до: %s" % (rank_new[r["ckey"]], rank_old.get(r["ckey"], "вне охран"), line(r, base), line(old[r["ckey"]], base).split("|", 1)[1].strip()), flush=True)
        print("  верх после пересчёта:", flush=True)
        for r in top_new:
            print("   #%d (было %s) %s | %s" % (rank_new[r["ckey"]], rank_old.get(r["ckey"], "вне охран / вне"), "пересчитана" if r["ckey"] in changed_ck else "та же", line(r, base)), flush=True)
        rep["sets"][name] = dict(cells=len(main_rows), recounted=dict(per_entry), kept=dict(kept_entry), guard_pass_old=len(ok_old), guard_pass_new=len(ok_new),
                                 top_old=[dict(ckey=r["ckey"], rank_old=rank_old[r["ckey"]], rank_new=rank_new.get(r["ckey"]), recounted=r["ckey"] in changed_ck, old=line(r, base),
                                               new=line(new[r["ckey"]], base)) for r in top_old],
                                 top_new=[dict(ckey=r["ckey"], rank_new=rank_new[r["ckey"]], rank_old=rank_old.get(r["ckey"]), recounted=r["ckey"] in changed_ck, new=line(r, base)) for r in top_new])
    # единички TK-680 (L-336, PS-960) с токеном f12: до / после, к базе стороны
    COLS10 = {"age_h", "approach_dur_min", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h", "depth_min_bp", "depth_mean_bp", "since_touch_prior_h", "touch_prior_none"}
    rep["singles"] = {}
    for side, bcell in (("short", "s4"), ("long", "s3")):
        bq = [r for r in rows(con, MAIN, entries=(334,), side=side).values() if r["cid"] == bcell and " F=" not in r["ckey"]]
        b = bq[0]
        f12s = rows(con, F12, side=side)
        out = []
        for k, r in rows(con, MAIN, entries=(336,), side=side).items():
            if " F=" not in k or not ({t.replace("<=", ">=").split(">=")[0] for t in k.split(" F=", 1)[1].split("&")} & COLS10):
                continue
            n = f12s.get(k, r)
            out.append(((n["sharpe"] if n["sharpe"] is not None else -99.0) - b["sharpe"], r, n, k in f12s))
        out.sort(key=lambda x: -x[0])
        print("== одиночки L-336 с токеном f12, %s: клеток %d, пересчитано %d; база %s: %s" % (side, len(out), sum(1 for x in out if x[3]), bcell, line(b, b)), flush=True)
        for d, r, n, ch in out[:8]:
            print("   %s | до: %s | после: %s%s" % (r["ckey"].split(" F=", 1)[1][:50], line(r, b).split("|", 1)[1].strip(), line(n, b).split("|", 1)[1].strip(), "" if ch else " (та же)"), flush=True)
        best_old = max(((r["sharpe"] if r["sharpe"] is not None else -99.0) - b["sharpe"]) for _, r, _, _ in out) if out else None
        rep["singles"][side] = dict(cells=len(out), recounted=sum(1 for x in out if x[3]), base=dict(cid=bcell, n=b["n"], usd=b["usd"], sharpe=b["sharpe"]), best_gain_old=best_old,
                                    best_gain_new=out[0][0] if out else None)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
