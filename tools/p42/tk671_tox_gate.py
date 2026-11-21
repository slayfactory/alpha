"""TK-671: ворота порта TOX13 на Rust (`alpha-feat tox`) против Python (`tk600_feat.py tox`) на настоящих строках сигналов.

  python3 tk671_tox_gate.py --rust-bin <alpha-feat> [--out-dir /data/p42-tk671/tox-gate] [--procs 3] [--threads 3] [--reuse-python]

Выборка из `<side>-sigsx.csv` прогона (sym, sig_ms — мс от `--base-date`):
  dense  — `--dense-coins` монет по `--dense-rows` подряд идущих строк (плотно по монето-дням, как в боевом счёте; чётные монеты — лонг, нечётные — шорт;
           первые 3 монеты — с начала окна: строки без предыдущих суток), остальные — со случайной позиции;
  spread — `--spread` строк по остальным монетам (лонг и шорт поровну).
Для каждой выборки: Python (`--procs` процессов) и Rust (`--threads` потоков, склад тиков только на чтение) на одних и тех же строках; сравнение по 13 столбцам:
точное равенство или относительная погрешность ≤ `--tol` (1e-9), картина NaN одна и та же. Печатает таблицу и пишет `summary.json`, `mismatch-*.csv`.
Дни окна (wa) — до 29.08.2026, прямое окно (≥ 14.11) не читается.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd

TOX = ["vpin1h", "vpin6h", "kyle30", "kyle240", "amih30", "amih240", "bigimb300", "bigimb1800", "sac300", "sac1800", "mk10_300", "mk60_300", "mk60_1800"]
DAY_MS = 86_400_000
HERE = os.path.dirname(os.path.abspath(__file__))


def load_sigsx(root: str, run: str, side: str) -> pd.DataFrame:
    d = pd.read_csv(f"{root}/{run}/{side}-sigsx.csv", usecols=["sym", "sig_ms"])
    d["side"] = side
    return d


def pick(a) -> dict:
    rng = np.random.default_rng(a.seed)
    fr = {s: load_sigsx(a.sigsx_root, a.run, s) for s in ("long", "short")}
    used: set = set()
    dense_parts = []
    for i in range(a.dense_coins):
        side = "long" if i % 2 == 0 else "short"
        cnt = fr[side].groupby("sym").size()
        cands = sorted(set(cnt[cnt >= a.dense_rows + 50].index) - used)
        sym = str(rng.choice(cands))
        used.add(sym)
        g = fr[side][fr[side].sym == sym].sort_values("sig_ms", kind="stable").reset_index(drop=True)
        off = 0 if i < 3 else int(rng.integers(0, len(g) - a.dense_rows + 1))
        dense_parts.append(g.iloc[off: off + a.dense_rows])
    dense = pd.concat(dense_parts, ignore_index=True)
    rest = pd.concat([fr["long"], fr["short"]], ignore_index=True)
    rest = rest[~rest.sym.isin(used)]
    spread = rest.sample(n=a.spread, random_state=a.seed).reset_index(drop=True)
    return {"dense": dense, "spread": spread}


def write_eps(df: pd.DataFrame, out_dir: str, name: str):
    df = df.copy().reset_index(drop=True)
    df["ep_id"] = [f"{name}{i}" for i in range(len(df))]
    df["day"] = (df["sig_ms"].astype("int64") // DAY_MS).astype("int64")
    pq = f"{out_dir}/eps-{name}.parquet"
    df[["ep_id", "sym", "side", "sig_ms", "day"]].to_parquet(pq)
    csv = f"{out_dir}/eps-{name}.csv"
    df[["sym", "sig_ms", "side"]].to_csv(csv, index=False)
    return pq, csv, df


def run_python(pq: str, out_pq: str, a) -> float:
    env = dict(os.environ, TK594_TRADES=a.trades, PYTHONUTF8="1")
    t = time.time()
    subprocess.run([sys.executable, f"{HERE}/tk600_feat.py", "tox", pq, out_pq, "--base-date", a.base_date, "--procs", str(a.procs)], check=True, env=env,
                   stdout=subprocess.DEVNULL)
    return time.time() - t


def run_rust(csv: str, out_csv: str, a) -> tuple[float, dict]:
    t = time.time()
    r = subprocess.run([a.rust_bin, "tox", "--eps", csv, "--out", out_csv, "--base-date", a.base_date, "--threads", str(a.threads), "--trades", a.trades,
                        "--store", a.store], check=True, capture_output=True, text=True)
    sec = time.time() - t
    info = json.loads(r.stdout.strip().splitlines()[-1])
    return sec, info


def compare(df: pd.DataFrame, py_pq: str, rs_csv: str, tol: float, mm_path: str) -> dict:
    py = pd.read_parquet(py_pq).set_index("ep_id").loc[df["ep_id"]].reset_index(drop=True)
    rs = pd.read_csv(rs_csv, dtype={"sym": str}, float_precision="round_trip")
    assert len(rs) == len(df) and (rs["sym"].to_numpy() == df["sym"].to_numpy()).all() and (rs["sig_ms"].to_numpy() == df["sig_ms"].to_numpy()).all(), "порядок строк"
    cols, bad_rows = {}, []
    for k in TOX:
        a, b = py[k].to_numpy(float), rs[k].to_numpy(float)
        na, nb = np.isnan(a), np.isnan(b)
        both = ~na & ~nb
        eq = both & (a == b)
        with np.errstate(invalid="ignore", divide="ignore"):
            rel = np.where(both & ~eq, np.abs(a - b) / np.maximum(np.abs(a), np.abs(b)), 0.0)
        mism_nan = na != nb
        over = both & (rel > tol)
        cols[k] = {"rows": int(len(a)), "finite_both": int(both.sum()), "nan_both": int((na & nb).sum()), "nan_mismatch": int(mism_nan.sum()),
                   "exact_equal": int(eq.sum()), "max_rel_err": float(rel.max()) if both.any() else 0.0, "over_tol": int(over.sum()),
                   "py_nan_share": float(na.mean())}
        for i in np.flatnonzero(mism_nan | over)[:200]:
            bad_rows.append({"col": k, "i": int(i), "sym": df["sym"][i], "sig_ms": int(df["sig_ms"][i]), "side": df["side"][i], "py": a[i], "rust": b[i]})
    pd.DataFrame(bad_rows).to_csv(mm_path, index=False)
    ok = all(c["nan_mismatch"] == 0 and c["over_tol"] == 0 for c in cols.values())
    return {"ok": ok, "cols": cols, "values": int(len(df) * len(TOX)), "exact_equal": int(sum(c["exact_equal"] for c in cols.values())),
            "max_rel_err": max(c["max_rel_err"] for c in cols.values())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rust-bin", required=True)
    ap.add_argument("--out-dir", default="/data/p42-tk671/tox-gate")
    ap.add_argument("--sigsx-root", default="/data/p42-tk671/pilot/out")
    ap.add_argument("--run", default="p60n2")
    ap.add_argument("--base-date", default="2026-06-17")
    ap.add_argument("--trades", default="/data/p42-tk621/wa/aggtrades")
    ap.add_argument("--store", default="/data/p42-tk599/tick-store")
    ap.add_argument("--dense-coins", type=int, default=20)
    ap.add_argument("--dense-rows", type=int, default=500)
    ap.add_argument("--spread", type=int, default=1000)
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--seed", type=int, default=671)
    ap.add_argument("--tol", type=float, default=1e-9)
    ap.add_argument("--sets", default="dense,spread")
    ap.add_argument("--reuse-python", action="store_true", help="не пересчитывать Python, если py-<набор>.parquet уже есть (время — из .info.json)")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    sets = pick(a)
    summary = {"base_date": a.base_date, "tol": a.tol, "procs": a.procs, "threads": a.threads, "sets": {}}
    for name in a.sets.split(","):
        pq, csv, df = write_eps(sets[name], a.out_dir, name)
        py_pq, rs_csv = f"{a.out_dir}/py-{name}.parquet", f"{a.out_dir}/rust-{name}.csv"
        t_rs, info = run_rust(csv, rs_csv, a)
        if a.reuse_python and os.path.exists(py_pq):
            with open(py_pq + ".info.json", encoding="utf-8") as f:
                t_py = float(json.load(f)["seconds"])
        else:
            t_py = run_python(pq, py_pq, a)
        cmp = compare(df, py_pq, rs_csv, a.tol, f"{a.out_dir}/mismatch-{name}.csv")
        n = len(df)
        cmp.update({"rows": n, "coins": int(df.sym.nunique()), "coin_days": int(len(df.assign(d=df.sig_ms // DAY_MS).groupby(["sym", "d"]))),
                    "rust_seconds": t_rs, "python_seconds": t_py, "rust_ms_per_row": 1000 * t_rs / n, "python_ms_per_row": 1000 * t_py / n,
                    "rust_info": {k: v for k, v in info.items() if k != "coverage"}})
        summary["sets"][name] = cmp
        print(f"== {name}: строк {n}, монет {cmp['coins']}, монето-дней {cmp['coin_days']}; Rust {t_rs:.1f} с ({cmp['rust_ms_per_row']:.2f} мс/строка), "
              f"Python {t_py:.1f} с ({cmp['python_ms_per_row']:.2f} мс/строка); {'PASS' if cmp['ok'] else 'FAIL'}; точно равных {cmp['exact_equal']}/{cmp['values']}")
        print(f"{'столбец':12} {'оба конечны':>11} {'оба NaN':>8} {'NaN-расх.':>9} {'точно':>7} {'max отн.':>10} {'>tol':>5}")
        for k, c in cmp["cols"].items():
            print(f"{k:12} {c['finite_both']:11d} {c['nan_both']:8d} {c['nan_mismatch']:9d} {c['exact_equal']:7d} {c['max_rel_err']:10.2e} {c['over_tol']:5d}")
    with open(f"{a.out_dir}/summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    if not all(s["ok"] for s in summary["sets"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
