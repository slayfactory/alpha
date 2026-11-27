"""TK-729: прогон по монетам c539 на srv. Фаза base — C0 + 6 MR; фаза abl — абляции при выбранных (a, q) из select.json (после анализа фазы base).

Запуск (один юнит systemd-run, мультипроцесс по монетам):
    python tk729_run.py --out /data/tk729 --phase base --procs 3
    python tk729_run.py --out /data/tk729 --phase abl --procs 3
Результат — res/<phase>/<SYM>.npz: runs (имена), out (runs, NR, 328), present (328, есть ли свечи за сутки), turn (средний суточный оборот, USDT), natr (средний NATR30).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk729_cfg as C  # noqa: E402
import tk729_lib as L  # noqa: E402


def variants_for(phase: str, out: str) -> list[dict]:
    if phase == "base":
        return C.base_variants()
    sel = json.load(open(os.path.join(out, "select.json"), encoding="utf-8"))  # {"dirs": [{"name":..., "c":..., "hlmax":...}, ...]}
    seen, vs = set(), []
    for d in sel["dirs"]:
        key = (d["c"], d["hlmax"])
        if key in seen:
            continue
        seen.add(key)
        vs += [v for v in C.ablation_variants(d["c"], d["hlmax"]) if (v["kind"] == "ABf") == (phase == "frz")]
    return vs


def process(args):
    sym, phase, out = args
    dst = os.path.join(out, "res", phase, sym + ".npz")
    if os.path.exists(dst):
        return sym, 0.0, "skip"
    t0 = time.time()
    S = L.load_coin(sym)
    F = C.coin_features(S, C.btc_block_cached())
    vs = variants_for(phase, out)
    res = np.stack([C.run_variant(S, F, v) for v in vs])
    pres_day = S["pres"].reshape(L.NDAYS, 1440).any(axis=1)[L.DAY0:]
    qv_day = S["qv"].reshape(L.NDAYS, 1440).sum(axis=1)[L.DAY0:]
    turn = float(qv_day[pres_day].mean()) if pres_day.any() else 0.0
    nat = F["natr30"][L.DAY0 * 1440:]
    nat = float(np.nanmean(nat)) if np.isfinite(nat).any() else float("nan")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp.npz"
    np.savez(tmp, runs=np.array([v["name"] for v in vs]), out=res, present=pres_day, turn=turn, natr=nat)
    os.replace(tmp, dst)
    return sym, time.time() - t0, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/data/tk729")
    ap.add_argument("--phase", choices=("base", "abl", "frz"), required=True)
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    syms = L.read_syms()
    if a.only:
        syms = [s for s in syms if s in set(a.only.split(","))]
    if a.limit:
        syms = syms[: a.limit]
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    done = 0
    with mp.Pool(a.procs) as pool:
        for sym, dt, st in pool.imap_unordered(process, [(s, a.phase, a.out) for s in syms]):
            done += 1
            with open(os.path.join(a.out, "progress-%s.txt" % a.phase), "w") as f:
                f.write("%d/%d %.0f%% %s %.1fs elapsed %.0fs\n" % (done, len(syms), 100.0 * done / len(syms), sym, dt, time.time() - t0))
    print("готово", done, "монет за", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
