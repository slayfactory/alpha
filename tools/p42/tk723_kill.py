#!/usr/bin/env python3
"""TK-723: таблица остановки базы B шорта до n = 365 из путей бутстрепа FJ (З1 проверяющего TK-722, поправка к PS-1042).

  python3 tk723_kill.py --out <json> [--kill-out docs/research/freeze/tk723-kill365-fj.json]   # srv: ряд B FJ из `tk721_day` base

Пути — те же, что мощность e-процесса TK-722 (`tk722_checks.cmd_prep`: FJ 150 сут., блоки 21, 10 000, `_boot_long` до 365, зерно [722, 2]).
Таблицы DD_q[n], GAP_q[n], q = 95 / 99 / 99,5, n = 1…365; доля ложных остановок — путей FJ, где (а) или (б) срабатывает хотя бы при одном n ≤ 365;
уровень — первый из q95 → q99 → q99,5 с долей ≤ 0,10 (PS-1041 п. 4 на горизонте таблицы). Гонка на тех же путях: первая остановка против первого E ≥ 20
(μ₁ FJ, σ FJ, как PS-1046 п. 5). Без движка."""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk720_fwd as F  # noqa: E402
import tk722_checks as C  # noqa: E402
import tk721_stress as S  # noqa: E402

LEVELS = (95.0, 99.0, 99.5)
MAX_FALSE = 0.10
NEVER = 10 ** 6
VERSION = "tk723-kill365-fj-1"


def qkey(q):
    return "q%g" % q


def first_fire(dd, gap, dq, gq):
    """Первые сутки (1…N) остановки по путям: просадка строго > DD_q[n] или перехай строго > GAP_q[n]; NEVER — не сработала."""
    fire = (dd > np.asarray(dq)[None, :] + F.EPS) | (gap > np.asarray(gq)[None, :] + F.EPS)
    return np.where(fire.any(axis=1), fire.argmax(axis=1) + 1, NEVER)


def first_accept(paths, mu1, sd, e_accept=C.E_ACCEPT):
    """Первые сутки (1…N), когда ln E_n ≥ ln e_accept; NEVER — не дошло."""
    hit = C.log_e(paths, mu1, sd) >= math.log(e_accept)
    return np.where(hit.any(axis=1), hit.argmax(axis=1) + 1, NEVER)


def kill_tables_fj(paths, mu1, sd, levels=LEVELS, max_false=MAX_FALSE, at=(60, 180, 365)):
    """Таблицы остановки на путях (P, N), доля ложных остановок по n ≤ N и n ≤ k, уровень по лестнице, гонка с e-процессом на тех же путях."""
    paths = np.asarray(paths, float)
    dd, gap = F.running_dd_gap(paths)
    acc = first_accept(paths, mu1, sd)
    out, level = {}, None
    for q in levels:
        dq = [round(float(x), 6) for x in np.percentile(dd, q, axis=0)]
        gq = [float(x) for x in np.percentile(gap, q, axis=0)]
        st = first_fire(dd, gap, dq, gq)
        out[qkey(q)] = dict(dd=dq, gap=gq, false_stop=float((st <= paths.shape[1]).mean()), false_stop_at={str(k): float((st <= k).mean()) for k in at},
                            stop_before_works=float((st < acc).mean()), works_before_stop=float((acc < st).mean()), same_day=float(((acc == st) & (st < NEVER)).mean()),
                            neither=float(((st == NEVER) & (acc == NEVER)).mean()))
        if level is None and out[qkey(q)]["false_stop"] <= max_false:
            level = q
    out["level"] = level if level is not None else levels[-1]
    out["level_ok"] = level is not None
    out["works_at"] = {str(k): float((acc <= k).mean()) for k in at}
    cs = np.cumsum(paths, axis=1)
    out["cone"] = {k: [round(float(x), 6) for x in np.percentile(cs, v, axis=0)] for k, v in (("q05", 5), ("q50", 50), ("q95", 95))}
    return out


def fj_series():
    c = sqlite3.connect("file:%s?mode=ro" % S.DB, uri=True)
    b = np.zeros(C.ND)
    for d, v in c.execute("SELECT day, pnl FROM tk721_day WHERE series='base'"):
        b[d] = v
    return b[C.FJ[0]:C.FJ[1]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--kill-out", default="")
    ap.add_argument("--freeze", default=os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk720-frozen.json"))
    a = ap.parse_args(argv)
    x = fj_series()
    mo = C.moments(x)
    assert len(x) == 150 and abs(mo["mean"] - 0.5610832111) < 1e-8 and abs(mo["sd"] - 4.8150457655) < 1e-8, "ряд FJ ≠ TK-722"
    idx = C._boot_long(len(x), C.KILL_N365, np.random.default_rng([C.SEED722, 2]))  # первый розыгрыш TK-722 (мощность e-процесса FJ)
    paths = x[idx]
    kt = kill_tables_fj(paths, mo["mean"], mo["sd"])
    assert abs(kt["works_at"]["180"] - 0.247) < 1e-9 and abs(kt["works_at"]["365"] - 0.6617) < 1e-9, "пути ≠ мощности TK-722"
    # справочно: остановка, когда B ничего не стоит (те же пути, ряд FJ минус среднее)
    h0 = (x - x.mean())[idx]
    dd0, gap0 = F.running_dd_gap(h0)
    lv = kt[qkey(kt["level"])]
    st0 = first_fire(dd0, gap0, lv["dd"], lv["gap"])
    out = dict(fj=dict(n=len(x), sum=float(x.sum()), mean=mo["mean"], sd=mo["sd"]), level=kt["level"], level_ok=kt["level_ok"], works_at=kt["works_at"],
               h0_stop_at={str(k): float((st0 <= k).mean()) for k in (60, 180, 365)},
               levels={qkey(q): {k: v for k, v in kt[qkey(q)].items() if k not in ("dd", "gap")} | dict(dd={str(k): kt[qkey(q)]["dd"][k - 1] for k in (5, 20, 60, 90, 120, 180, 270, 365)},
                                                                                                gap={str(k): kt[qkey(q)]["gap"][k - 1] for k in (5, 20, 60, 90, 120, 180, 270, 365)}) for q in LEVELS},
               cone={k: {str(m): kt["cone"][k][m - 1] for m in (20, 60, 120, 180, 365)} for k in kt["cone"]})
    if a.kill_out:
        kill = dict(version=VERSION, task="TK-723", freeze_sha256=F.sha256_file(a.freeze), level=kt["level"],
                    source="бутстреп ряда B на FJ (сутки 31…180, 150 сут.), блоки 21, 10 000 путей по 365 сут. (`tk722_checks._boot_long`, зерно [722, 2] — те же пути, что мощность e-процесса TK-722)",
                    rule="PS-1042 §4 с поправкой TK-723: остановка при n сутках форварда (n = 1…365), если просадка $ ряда с 25.11 строго больше DD_q[n] или перехай строго больше GAP_q[n]; q = level",
                    level_rule="первый из q95 → q99 → q99.5, у которого доля путей FJ с хотя бы одним срабатыванием при n ≤ 365 ≤ 0,10 (PS-1041 п. 4 на горизонте таблицы)",
                    expect=dict(label="FJ", per_day=mo["mean"], window="сутки 31…180 (01.02–30.06)", sharpe=F.sharpe(x)),
                    **{qkey(q): dict(dd=kt[qkey(q)]["dd"], gap=kt[qkey(q)]["gap"], false_stop=kt[qkey(q)]["false_stop"]) for q in LEVELS}, cone=kt["cone"])
        with open(a.kill_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(kill, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        out["kill_sha256"] = F.sha256_file(a.kill_out)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
