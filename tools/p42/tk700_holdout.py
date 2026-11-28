#!/usr/bin/env python3
"""TK-700: раннер проверки замороженных кандидатов TK-674 на отложенных месяцах (январь–июнь 2026, В-170). Шаги: plan → gate → read; читается один раз (замок `tk700_lock`).

  python3 tk700_holdout.py plan --freeze docs/research/freeze/tk700-frozen.json [--dry]
  python3 tk700_holdout.py gate --freeze … [--dry] [--out <gate.json>]          # проверки до чтения; печатает «READ-READY» или список отказов; P&L не читает
  python3 tk700_holdout.py read --freeze … [--dry] [--threads 2] [--cap-gb 6]   # замок → ftab по замороженным порогам → движок на окнах → метрики; один раз
  python3 tk700_holdout.py selftest                                             # охрана движка и замок на пустой заморозке (без данных)

`--dry` — сухой прогон на окнах Jul–Nov (wa / wb / wc: читать можно) с поддельной заморозкой (клетки из БД, их числа — справочные): тот же код, свой замок (`/data/p42-tk700/lock-dry`), таблицы ворот (`gate/p671/asm`),
вселенная c539; результат сверяется с числами БД (сделки, Σ $) — если сходится, раннер и таблицы признаков воспроизводят счёт TK-674. Без `--dry` — окна m01…m06 (`TK700_WINSET=h1`), замок `/data/p42-tk700/lock`.
Фильтры клеток — абсолютные пороги из ключа `F=…` (`parse`): пустое значение признака проходит, «определён и не проходит» — исключён (как `tk674_run._pass`); n24 — по `m24`. Таблица `--ftab` — столбец на смысл фильтра, строки — кандидаты базы окна (sym, sig_ms).
Метрики (`tk674_stats.metrics`): Σ $ (ставка 500 $), $/сделку, annualized daily Sharpe, просадка $ и % маржи, перехай (сутки между новыми максимумами), по месяцам — рядом с базой той же стороны (В-169)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")
import tk700_lock as lk  # noqa: E402
import tk700_win as w7  # noqa: E402

ROOT = os.environ.get("TK700_ROOT", "/data/p42-tk700")
DRY_WINS = ("wa", "wb", "wc")
GATE_ASM = ROOT + "/gate/p671/asm"
TICK_STORE = ["--tick-store", "/data/p42-tk599/tick-store", "--tick-store-cap-gb", "3", "--tick-store-min-free-gb", "5"]
COVER_MIN = 0.7
FV_PASS = 0.5
SIDE_LETTER = {"long": "L", "short": "S"}
TOL_USD = 1e-6


def modes(dry):
    """Корни и окна режима. H1: окна отложенной выборки; dry: wa / wb / wc."""
    if not dry and os.environ.get("TK700_WINSET") != "n1":                      # n1 — окно дельты 14.11–24.11 (`tk700_win.apply_n1`), замок и корни те же
        os.environ["TK700_WINSET"] = "h1"
    import tk604_stats as st
    return dict(dry=dry, st=st, lock=ROOT + ("/lock-dry" if dry else "/lock"), out=ROOT + ("/dry" if dry else "/read"), wins=[w.name for w in st.TRADE_WINDOWS])


def sem_of(parts):
    import tk674_axes as ax
    return "&".join(sorted("%s%s%s" % (f, o, ax.num(x)) for f, o, x in parts))


def load_fz(path):
    d = json.load(open(path, encoding="utf-8"))
    if d.get("version") != "tk700-frozen-1":
        raise SystemExit("версия заморозки %r" % d.get("version"))
    d["_sha"] = lk.sha256_file(path)
    return d


def asm_dir(m):
    return GATE_ASM if m["dry"] else ROOT + "/p671/asm"


def window_universe(m, name):
    return w7.C539 if m["dry"] else w7.universe_file(name)


def tree_of(m, name):
    return "/data/p42-tk621/" + name


# ---- таблицы кандидатов и фильтры ---------------------------------------------------------------------------------------------------

def load_tabs(m, side):
    """{окно: tab} — строки кандидатов базы окна (одна на (sym, sig_ms); бит 1 раньше бита 2, как `tk680_singles.add_columns`) со столбцами таблицы признаков."""
    import numpy as np
    import pandas as pd
    st = m["st"]
    tabs = {}
    for w in st.TRADE_WINDOWS:
        p = os.path.join(asm_dir(m), "%s-%s.parquet" % (w.name, side))
        d = pd.read_parquet(p)
        d = d[d["meta_base_pass"] > 0].copy()
        d["sig_ms"] = d["meta_abs_ms"].astype("int64") - int(w.t0_ms)
        d["_pri"] = (d["meta_base_pass"] != 1).astype(int)
        d = d.sort_values("_pri", kind="stable").drop_duplicates(["meta_sym", "sig_ms"]).sort_values(["meta_sym", "sig_ms"], kind="stable")
        tab = {"sym": d["meta_sym"].astype(str).to_numpy(object), "ms": d["sig_ms"].to_numpy(np.int64)}
        for c in d.columns:
            if not c.startswith("meta_") and c not in ("row_id", "sym", "sig_ms", "_pri"):
                tab[c] = d[c].to_numpy(np.float64)
        tabs[w.name] = tab
    return tabs


def passes(tab, parts):
    """Клетка оставляет кандидата: признак ≤ / ≥ порога; пустое значение проходит (`tk674_run._pass`); n24 — по m24; sharp — `cons60 == 0 & mv5 ≥ x`."""
    import numpy as np
    ok = np.ones(len(tab["sym"]), dtype=bool)
    for feat, op, x in parts:
        with np.errstate(invalid="ignore"):
            if feat == "sharp":
                cn, mv = tab["cons60"], tab["mv5"]
                fin = np.isfinite(cn) & np.isfinite(mv)
                p = ~(fin & ~((cn == 0) & (mv >= x)))
            else:
                if feat not in tab:
                    raise SystemExit("признак %s: нет в таблице признаков окна" % feat)
                v = np.where(tab["m24"] > 0, np.nan, tab["n24"]) if feat == "n24" else tab[feat]
                if op == "<=":
                    p = ~(np.isfinite(v) & ~(v <= x))
                elif op == ">=":
                    p = ~(np.isfinite(v) & ~(v >= x))
                else:
                    raise SystemExit("знак %s у признака %s" % (op, feat))
        ok &= p
    return ok


def filter_cols(cells):
    """{смысл фильтра: столбец fNNN} — детерминированно по отсортированным смыслам (как `tk674_run.ftab_cols`)."""
    sems = sorted({sem_of(c["filter"]) for c in cells if c["filter"]})
    return {s: "f%03d" % i for i, s in enumerate(sems)}


def write_ftab(path, tab, cols, cells):
    sem_parts = {sem_of(c["filter"]): c["filter"] for c in cells if c["filter"]}
    pm = {sem: passes(tab, parts) for sem, parts in sem_parts.items()}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms," + ",".join(cols[s] for s in sem_parts) + "\n")
        for i in range(len(tab["sym"])):
            f.write("%s,%d,%s\n" % (tab["sym"][i], int(tab["ms"][i]), ",".join("1.0" if pm[s][i] else "0.0" for s in sem_parts)))
    return {s: int(p.sum()) for s, p in pm.items()}


# ---- план / ворота ------------------------------------------------------------------------------------------------------------------

def coverage(m, fz):
    """Охват столбцов фильтров клеток по месяцам (доля не-пустых; охрана 0,7)."""
    import pandas as pd
    out = []
    for c in fz["cells"]:
        for feat, _, _ in c["filter"]:
            col = "n24" if feat == "n24" else feat
            for w in m["wins"]:
                d = pd.read_parquet(os.path.join(asm_dir(m), "%s-%s.parquet" % (w, c["side"])))
                d = d[d["meta_base_pass"] > 0]
                if feat == "sharp":
                    share = float((d["cons60"].notna() & d["mv5"].notna()).mean()) if len(d) else 0.0
                    present = "cons60" in d.columns and "mv5" in d.columns
                else:
                    present = col in d.columns
                    share = float(d[col].notna().mean()) if present and len(d) else 0.0
                out.append(dict(cell=c["id"], side=c["side"], feat=feat, win=w, rows=int(len(d)), present=present, share=round(share, 4), ok=bool(present and share >= COVER_MIN)))
    return out


def cmd_plan(a):
    fz = load_fz(a.freeze)
    m = modes(a.dry)
    print("заморозка sha256 %s, клеток %d + баз %d; окна %s; режим %s" % (fz["_sha"][:16], len(fz["cells"]), len(fz["bases"]), ",".join(m["wins"]), "dry (wa/wb/wc)" if a.dry else "H1"))
    for c in fz["bases"] + fz["cells"]:
        print("  %-10s %-5s %s" % (c["id"], c["side"], c["ckey"]), "| ref", c.get("ref"))
    for w in m["wins"]:
        u = w7.read_symbols(window_universe(m, w))
        print("  вселенная %s: %d" % (w, len(u)))
    cov = coverage(m, fz)
    bad = [x for x in cov if not x["ok"]]
    for x in cov:
        print("  охват %-8s %-5s %-6s %-4s строк %4d доля %.3f %s" % (x["cell"], x["side"], x["feat"], x["win"], x["rows"], x["share"], "ok" if x["ok"] else "НИЖЕ 0,7 / нет столбца"))
    lj = lk.read_lock(m["lock"], fz["_sha"]) if os.path.isdir(lk.lock_dir(m["lock"], fz["_sha"])) else None
    print("замок: %s" % ("нет (читалось 0 раз)" if not os.path.isdir(lk.lock_dir(m["lock"], fz["_sha"])) else (lj["state"] if lj else "оборван после mkdir")))
    return 3 if bad else 0


def check(res, name, ok, note=""):
    res["checks"].append(dict(name=name, ok=bool(ok), note=note))


def cmd_gate(a):
    fz = load_fz(a.freeze)
    m = modes(a.dry)
    res = dict(sha=fz["_sha"], dry=a.dry, utc=lk.utc_now(), checks=[])
    check(res, "версия заморозки", fz["version"] == "tk700-frozen-1", fz["version"])
    b = fz["binary"]
    check(res, "бинарь md5", os.path.exists(b["path"]) and hashlib.md5(open(b["path"], "rb").read()).hexdigest() == b["md5"], b["path"])
    for t in fz["thresholds"]:
        check(res, "порог sha %s" % os.path.basename(t["path"]), os.path.exists(t["path"]) and lk.sha256_file(t["path"]) == t["sha256"])
    if not a.dry:
        for k, t in fz["tables"].items():
            check(res, "таблица признаков sha %s" % k, os.path.exists(t["path"]) and lk.sha256_file(t["path"]) == t["sha256"])
        for w, u in fz["universe"]["windows"].items():
            check(res, "вселенная sha %s" % w, os.path.exists(u["path"]) and lk.sha256_file(u["path"]) == u["sha256"], "n=%s" % u["n"])
        for n, h in fz["code"].items():
            p = os.path.join(HERE, n)
            check(res, "код sha %s" % n, os.path.exists(p) and lk.sha256_file(p) == h, "изменён после заморозки" if os.path.exists(p) and lk.sha256_file(p) != h else "")
    for x in coverage(m, fz):
        check(res, "охват %s %s %s" % (x["cell"], x["feat"], x["win"]), x["ok"], "доля %.3f" % x["share"])
    flags = [("G1 ключи строк базы", ROOT + "/gate/g1/g1.json"), ("строки базы wa/wb/wc", ROOT + "/gate/rows-gate.json"), ("прогрев 20 суток", ROOT + "/gate/warm20.json")]
    for nm, p in flags:
        ok = os.path.exists(p) and bool(json.load(open(p, encoding="utf-8")).get("passed"))
        check(res, "ворота: " + nm, ok or a.dry and nm.startswith("прогрев"), p if ok else "нет / не пройдены: " + p)
    try:
        w7.check_cmd(["x", "breakout", "tick", "--out", ROOT + "/gate/fence-probe"], {})
        check(res, "охрана движка отказывает без жетона", False, "команда прошла")
    except w7.FenceError:
        check(res, "охрана движка отказывает без жетона", True)
    ld = lk.lock_dir(m["lock"], fz["_sha"])
    check(res, "замок свободен", not os.path.isdir(ld), ld)
    res["passed"] = all(c["ok"] for c in res["checks"])
    out = a.out or (ROOT + "/gate/holdout-gate-%s%s.json" % ("dry-" if a.dry else "", fz["_sha"][:12]))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for c in res["checks"]:
        if not c["ok"]:
            print("ОТКАЗ:", c["name"], c["note"])
    print("READ-READY" if res["passed"] else "НЕ ГОТОВО", "(%d проверок, провалено %d) → %s" % (len(res["checks"]), sum(1 for c in res["checks"] if not c["ok"]), out))
    return 0 if res["passed"] else 3


# ---- чтение -------------------------------------------------------------------------------------------------------------------------

def engine(m, w, side, variants, out, ftab, env_extra, binary, threads, cap_gb, log):
    d = tree_of(m, w.name)
    os.makedirs(out, exist_ok=True)
    vf = out + ".variants.txt"
    with open(vf, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join("%s %s\n" % v for v in variants))
    coins = ",".join(w7.read_symbols(window_universe(m, w.name)))
    cmd = [binary, "breakout", "tick", "--cache", d + "/cache", "--trades", d + "/aggtrades", "--variants", vf, "--symbols", coins, "--out", out, "--threads", str(threads), "--cap-gb", str(cap_gb)] + TICK_STORE
    if ftab:
        cmd += ["--ftab", ftab]
    if os.environ.get("TK743_ORDERS"):                                      # TK-743: журнал заявок сделок и уровни сетапа (`<сторона>-orders.csv`, `<сторона>-setup.csv`); счёт и сделки те же
        cmd += ["--keep-orders"]
    env = dict(os.environ)
    env.update({"P42_T0": w.t0, "P42_SIDES": side})
    env.update(env_extra)
    with open(log, "w", encoding="utf-8") as lf:
        subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, check=True)


def stop_pct(line):
    for t in line.split():
        if t.startswith("s="):
            return float(t[2:])
    return 5.0


def cmd_read(a):
    import numpy as np
    fz = load_fz(a.freeze)
    m = modes(a.dry)
    st = m["st"]
    import tk625_run as r5
    import tk638_post as p8
    import tk674_stats as ms
    root = os.path.join(m["out"], fz["_sha"][:12])
    lj_path, nonce = lk.claim(m["lock"], fz["_sha"], info=dict(freeze=os.path.abspath(a.freeze), dry=a.dry, wins=m["wins"]))      # второй read с тем же sha — LockError
    token = lk.token_env(lj_path, nonce)
    lk.set_state(m["lock"], fz["_sha"], nonce, "started")
    t_start = time.time()
    try:
        binary = fz["binary"]["path"]
        if hashlib.md5(open(binary, "rb").read()).hexdigest() != fz["binary"]["md5"]:
            raise SystemExit("бинарь %s ≠ заморозке" % binary)
        coins = w7.read_symbols(w7.C539)
        results = {}
        for side in sorted({c["side"] for c in fz["cells"]}):
            sl = SIDE_LETTER[side]
            cells = [c for c in fz["bases"] + fz["cells"] if c["side"] == side]
            cols = filter_cols(cells)
            tabs = load_tabs(m, side)
            dirs = {c["id"]: {} for c in cells}
            for w in st.TRADE_WINDOWS:
                tab = tabs[w.name]
                ftab = os.path.join(root, "ftab", "%s-%s.csv" % (w.name, side))
                kept = write_ftab(ftab, tab, cols, cells) if cols else {}
                variants = []
                for c in cells:
                    line = c["line"] + ((" fa=%s fo=lo fv=%g" % (cols[sem_of(c["filter"])], FV_PASS)) if c["filter"] else "")
                    variants.append((c["id"], line))
                out = os.path.join(root, "cells", "%s-%s" % (w.name, side))
                engine(m, w, side, variants, out, ftab if cols else None, token, binary, a.threads, a.cap_gb, os.path.join(root, "log-%s-%s.txt" % (w.name, side)))
                for c in cells:
                    dirs[c["id"]][w.name] = (os.path.join(out, c["id"]), sl)
                print("движок %s-%s: %d вариантов, строк ftab %d, пройдено фильтров %s" % (w.name, side, len(variants), len(tab["sym"]), kept), flush=True)
            for c in cells:
                tables, daily = p8.read_tables(dirs[c["id"]])
                errs = []
                for w in st.TRADE_WINDOWS:
                    tb, dl = tables[w.name], daily[w.name]
                    tot_d, tot_n = float(sum(dl.values())), float(tb.net.sum())
                    if abs(tot_d - tot_n) > 1e-4 + 2e-6 * len(tb.net):
                        errs.append("G7 %s: Σ daily %+.6f ≠ Σ net %+.6f" % (w.name, tot_d, tot_n))
                ser = st.splice(tables, coins, daily=None)
                kinds, pnl = p8.kinds_pnl(tables)
                if len(pnl) != len(ser.val) or not np.allclose(pnl, ser.val, atol=1e-6):
                    errs.append("P&L сделок ≠ val ряда")
                tmax = max([int(tables[w.name].exit_ms.max()) + w.t0_ms for w in st.TRADE_WINDOWS if len(tables[w.name].exit_ms)] or [0])
                if tmax >= st.LAST_MS:
                    errs.append("G-tape: выход позже последних суток окон")
                if c["filter"]:
                    import tk604_run as r4
                    for w in st.TRADE_WINDOWS:
                        meta = json.load(open(os.path.join(dirs[c["id"]][w.name][0], "meta.json"), encoding="utf-8"))[side]
                        fc = r4.filter_counters(meta)
                        if fc is None or fc["no_key"]:
                            errs.append("G-key: no_key %s %s" % (w.name, fc))
                mt = ms.metrics(ser, kinds, pnl, s_pct=stop_pct(c["line"]))
                mt.update(errs=errs, ckey=c["ckey"], base=bool(c.get("base")), tmax_ms=int(tmax))
                results[c["id"]] = mt
        ref = {}
        for c in fz["bases"] + fz["cells"]:
            r = c.get("ref")
            if r and a.dry:
                got = results[c["id"]]
                ref[c["id"]] = dict(ref_trades=r["trades"], got_trades=got["n"], ref_usd=r["usd"], got_usd=got["usd"], same=bool(r["trades"] == got["n"] and abs(r["usd"] - got["usd"]) <= TOL_USD * max(1.0, abs(r["usd"]))))
        os.makedirs(root, exist_ok=True)
        slim = {k: {kk: vv for kk, vv in v.items() if kk not in ("daily", "dayn")} for k, v in results.items()}
        json.dump(dict(sha=fz["_sha"], dry=a.dry, wins=m["wins"], results=slim, ref_compare=ref, seconds=round(time.time() - t_start, 1)), open(os.path.join(root, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump({k: dict(daily=v["daily"], dayn=v["dayn"]) for k, v in results.items()}, open(os.path.join(root, "daily.json"), "w", encoding="utf-8"))
        lk.set_state(m["lock"], fz["_sha"], nonce, "done", extra=dict(seconds=round(time.time() - t_start, 1)))
    except BaseException as e:
        try:
            lk.set_state(m["lock"], fz["_sha"], nonce, "failed", extra=dict(error=repr(e)[:300]))
        finally:
            raise
    report(results, ref)
    return 0 if all(r["same"] for r in ref.values()) else (3 if a.dry else 0)


def report(results, ref):
    print("%-10s %-5s %5s %10s %8s %7s %9s %7s %s" % ("клетка", "стор.", "n", "Σ $", "$/сд.", "Sharpe", "DD $ (%)", "перех.", "ошибки"))
    for k, r in results.items():
        kp = r["kpi"]
        print("%-10s %-5s %5d %+10.2f %+8.3f %7.2f %6.0f (%2.0f%%) %3d/%3.0f/%3d %s" % (k, "base" if r["base"] else "cell", r["n"], r["usd"], r["mean_usd"], r["sharpe"], r["maxdd"], r["maxdd_pct"], kp["gap_longest"], kp["gap_mean"], kp["gap_tail"], r["errs"] or ""))
    for k, v in ref.items():
        print("сверка с БД %s: сделки %s / %s, Σ $ %+.4f / %+.4f → %s" % (k, v["ref_trades"], v["got_trades"], v["ref_usd"], v["got_usd"], "РАВНО" if v["same"] else "РАЗНО"))


def cmd_selftest(a):
    import tempfile
    d = tempfile.mkdtemp(prefix="tk700-self-")
    sha = "ab" * 32
    p, nonce = lk.claim(d, sha)
    try:
        lk.claim(d, sha)
        raise SystemExit("selftest: второй claim прошёл")
    except lk.LockError:
        pass
    lk.set_state(d, sha, nonce, "started")
    assert w7.token_ok(lk.token_env(p, nonce))
    try:
        w7.check_cmd(["x", "breakout", "tick", "--out", "/data/p42-tk700/z"], {})
        raise SystemExit("selftest: охрана пропустила")
    except w7.FenceError:
        pass
    w7.check_cmd(["x", "breakout", "tick", "--out", "/data/p42-tk700/z"], lk.token_env(p, nonce))
    print("selftest ok:", d)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("plan", "gate", "read"):
        s = sp.add_parser(n)
        s.add_argument("--freeze", required=True)
        s.add_argument("--dry", action="store_true")
        if n == "gate":
            s.add_argument("--out", default=None)
        if n == "read":
            s.add_argument("--threads", type=int, default=2)
            s.add_argument("--cap-gb", type=int, default=6)
    sp.add_parser("selftest")
    a = ap.parse_args(argv)
    return {"plan": cmd_plan, "gate": cmd_gate, "read": cmd_read, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
