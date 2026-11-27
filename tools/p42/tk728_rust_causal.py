#!/usr/bin/env python3
"""П-42 этап A, шаг 4а (TK-728, §13.78.4): тест на заглядывание Rust-семейств (f12, flow + deriv `all`, tcount, tsize, tox, fast) на 5 монето-сутках с обрезанными сделками. srv.
Для строки базы (монета, момент сигнала) строится отдельный корень сделок: сутки сигнала — новый zip с сделками до момента сигнала включительно (`transact_time ≤` сигнал), сутки позже сигнала убраны, раньше — ссылки на прежние zip.
Бинарь считает ту же строку на (1) полных сделках и (2) обрезанных; склад (`--store`) не подключается (оба счёта — по zip). Любое расхождение значения признака = заглядывание.
Проверка метода до чтения: счёт на полных сделках (1) равен прежнему выходу `alpha-feat` (TK-700, склад) по всем столбцам, строка в строку.
Не обрезаются (только сделки): минутные свечи `--cache` и метрики / премия / фандинг у `f12` / `all` — отдельный вывод в отчёте.
  python3 tk728_rust_causal.py --ref /data/p42-tk700/gate/p671/fam/rust/wb-long --win wb --side long --out /data/p42-tk728/res/rustcausal [--n-cd 5] [--max-rows 4] [--steps tcount,tsize,tox,fast,all,f12]"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import os
import subprocess
import sys
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk728_lib as L  # noqa: E402

BIN = dict(f12="/data/p42-tk699/bin/alpha-feat-tf", all="/data/p42-tk639/bin/alpha-feat", tcount="/data/p42-tk639/bin/alpha-feat", tsize="/data/p42-tk639/bin/alpha-feat",
           tox="/data/p42-tk671/bin/alpha-feat-tox-ed78ac4c408073f5f7343538c99b1cf3", fast="/data/p42-tk681/bin/alpha-feat-tk681")
ENV = dict(TK594_METRICS="/data/p42-tk600/metrics-all", TK594_PREMIUM="/data/p42-tk600/premium-all", TK594_FUNDING_M="/data/p42-tk600/fund-all")
DAY_MS = 86_400_000


def tree_of(win):
    out = subprocess.run([sys.executable, os.path.join(HERE, "tk700_win.py"), "env", win], capture_output=True, text=True, check=True).stdout
    return dict(x.split("=", 1) for x in out.split() if "=" in x)


def zip_name(sym, day):
    return "%s-aggTrades-%s.zip" % (sym, day)


def build_root(root, tree, sym, t0_ms, sig_ms, trunc):
    """Корень сделок монеты: trunc=False — все сутки ссылками; trunc=True — сутки сигнала обрезаны до сигнала включительно, сутки позже сигнала убраны. Возвращает число оставленных строк суток сигнала."""
    src = os.path.join(tree, "aggtrades", sym)
    dst = os.path.join(root, sym)
    os.makedirs(dst, exist_ok=True)
    abs_sig = t0_ms + sig_ms
    sig_day = datetime.datetime.fromtimestamp(abs_sig / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
    kept = -1
    for fn in sorted(os.listdir(src)):
        day = fn[len(sym + "-aggTrades-"):-4]
        link = os.path.join(dst, fn)
        if os.path.lexists(link):
            os.remove(link)
        if not trunc or day < sig_day:
            os.symlink(os.path.realpath(os.path.join(src, fn)), link)
        elif day == sig_day:
            with zipfile.ZipFile(os.path.join(src, fn)) as z:
                name = z.namelist()[0]
                lines = z.read(name).decode("utf-8").splitlines()
            hdr = lines[0].split(",")
            body = lines[1:] if not lines[0][:1].isdigit() else lines
            tcol = hdr.index("transact_time") if "transact_time" in hdr else 5
            rows = [ln for ln in body if int(ln.split(",")[tcol]) <= abs_sig]
            kept = len(rows)
            with zipfile.ZipFile(link, "w", zipfile.ZIP_DEFLATED) as zo:
                zo.writestr(name, "\n".join(([lines[0]] if body is not lines else []) + rows) + "\n")
    return kept


def run_bin(step, work, root, eps_row, t0, tree, threads=1):
    """Один счёт: строка → CSV выхода (одна строка)."""
    os.makedirs(work, exist_ok=True)
    env = dict(os.environ, TK594_TRADES=root, **ENV)
    out = os.path.join(work, step + ".csv")
    if os.path.exists(out):
        os.remove(out)
    if step == "f12":
        pd.DataFrame([eps_row["sigsx"]]).to_csv(os.path.join(work, "sigsx.csv"), index=False)
        cmd = [BIN[step], "f12", "--sigsx", os.path.join(work, "sigsx.csv"), "--side", eps_row["side"], "--out", out, "--cache", os.path.join(tree, "cache"), "--trades", root, "--base-date", t0, "--days", "80", "--threads", str(threads)]
    else:
        key = "flow" if step == "all" else ("tox" if step in ("tox", "fast") else "ts")
        pd.DataFrame([eps_row[key]]).to_csv(os.path.join(work, "eps-%s.csv" % key), index=False)
        cmd = [BIN[step], "all" if step == "all" else step, "--eps", os.path.join(work, "eps-%s.csv" % key), "--out", out, "--base-date", t0, "--trades", root, "--threads", str(threads)]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out):
        return None, (r.stderr or r.stdout)[-300:]
    return pd.read_csv(out, dtype=str, keep_default_na=False), ""


def pick_row(df, sym, sig):
    m = df[(df["sym"] == sym) & (df["sig_ms"].astype(np.int64) == int(sig))]
    return None if len(m) == 0 else m.iloc[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--win", required=True)
    ap.add_argument("--side", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-cd", type=int, default=5)
    ap.add_argument("--max-rows", type=int, default=4)
    ap.add_argument("--steps", default="tcount,tsize,tox,fast,all,f12")
    ap.add_argument("--control-only", action="store_true")
    a = ap.parse_args()
    env = tree_of(a.win)
    t0, tree = env["T0"], env["TREE"]
    t0_ms = int(pd.Timestamp(t0, tz="UTC").timestamp() * 1000)
    steps = a.steps.split(",")
    ref = {s: pd.read_csv(os.path.join(a.ref, s + ".csv"), dtype=str, keep_default_na=False) for s in steps}
    ts = pd.read_csv(os.path.join(a.ref, "eps-ts.csv"), dtype=str)
    tox = pd.read_csv(os.path.join(a.ref, "eps-tox.csv"), dtype=str)
    flow = pd.read_csv(os.path.join(a.ref, "eps-flow.csv"), dtype=str)
    sx = pd.read_csv(os.path.join(a.ref, "sigsx.csv"), dtype=str)
    # монето-сутки: случайные (зерно этапа) среди тех, где от 2 до max-rows строк базы
    ts["abs"] = ts["sig_ms"].astype(np.int64) + t0_ms
    ts["day"] = pd.to_datetime(ts["abs"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    g = ts.groupby(["sym", "day"]).size()
    cand = g[(g >= 2) & (g <= a.max_rows)].index.tolist()
    rng = np.random.default_rng(L.SEED)
    chosen = [cand[i] for i in sorted(rng.choice(len(cand), size=min(a.n_cd, len(cand)), replace=False))]
    print("кандидатов монето-суток %d, выбрано %d: %s" % (len(cand), len(chosen), chosen), flush=True)
    res = dict(chosen=[list(c) for c in chosen], rows=[], control_mismatch={}, diff={}, errors=[], seed=L.SEED)
    os.makedirs(a.out, exist_ok=True)
    rows = []
    for sym, day in chosen:
        for sig in ts[(ts["sym"] == sym) & (ts["day"] == day)]["sig_ms"]:
            rows.append((sym, day, sig))
    # метод до чтения: счёт на полных сделках = прежний выход
    ctrl_bad = {s: 0 for s in steps}
    trunc_diff = {s: 0 for s in steps}
    cmp_cols = {s: 0 for s in steps}
    diffcols = {s: {} for s in steps}
    for k, (sym, day, sig) in enumerate(rows):
        work = os.path.join(a.out, "r%02d" % k)
        eps = dict(ts=dict(sym=sym, sig_ms=sig), tox=pick_row(tox, sym, sig).to_dict(), flow=pick_row(flow, sym, sig).to_dict(), sigsx=pick_row(sx, sym, sig).to_dict(), side=a.side)
        kept_total = {}
        for variant in ("full", "trunc") if not a.control_only else ("full",):
            root = os.path.join(work, "root-" + variant)
            kept = build_root(root, tree, sym, t0_ms, int(sig), variant == "trunc")
            kept_total[variant] = kept
            for s in steps:
                got, err = run_bin(s, os.path.join(work, variant), root, eps, t0, tree)
                if got is None:
                    res["errors"].append(dict(row=k, sym=sym, sig_ms=sig, variant=variant, step=s, err=err))
                    continue
                r = pick_row(ref[s], sym, sig)
                if r is None:
                    res["errors"].append(dict(row=k, sym=sym, sig_ms=sig, variant=variant, step=s, err="нет строки в прежнем выходе"))
                    continue
                ign = {"ep_id"}
                cols = [c for c in got.columns if c not in ign and c in r.index]
                if variant == "full":
                    bad = [c for c in cols if str(got.iloc[0][c]) != str(r[c])]
                    ctrl_bad[s] += len(bad)
                    cmp_cols[s] += len(cols)
                    if bad:
                        res["control_mismatch"].setdefault(s, []).append(dict(row=k, cols=bad[:10]))
                else:
                    bad = [c for c in cols if str(got.iloc[0][c]) != str(r[c])]
                    trunc_diff[s] += len(bad)
                    for c in bad:
                        diffcols[s][c] = diffcols[s].get(c, 0) + 1
        res["rows"].append(dict(k=k, sym=sym, day=day, sig_ms=sig, kept_trades_sig_day=kept_total.get("trunc")))
        print("строка %d/%d %s %s: проверка метода — расхождений полного счёта с прежним %s" % (k + 1, len(rows), sym, day, {s: ctrl_bad[s] for s in steps}), flush=True)
        json.dump(res, open(os.path.join(a.out, "rustcausal.partial.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    res["control_cols_compared"] = cmp_cols
    res["control_diff_total"] = ctrl_bad
    res["trunc_diff_total"] = trunc_diff
    res["trunc_diff_columns"] = diffcols
    res["n_rows"] = len(rows)
    json.dump(res, open(os.path.join(a.out, "rustcausal.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: res[k] for k in ("n_rows", "control_diff_total", "trunc_diff_total", "trunc_diff_columns")}, ensure_ascii=False), flush=True)
    print("ошибок запуска: %d" % len(res["errors"]), flush=True)


if __name__ == "__main__":
    main()
