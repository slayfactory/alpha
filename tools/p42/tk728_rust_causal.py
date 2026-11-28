#!/usr/bin/env python3
"""П-42 этап A, шаг 4а (TK-728, §13.78.4): тест на заглядывание Rust-семейств (f12, flow + deriv `all`, tcount, tsize, tox, fast) на 5 монето-сутках с обрезанными сделками. srv.
Сделки читаются только со склада тиков (`/data/p42-tk599/tick-store`, формат v2): zip суток в деревьях окон — пустышки. Для строки (монета, момент сигнала) строится личный склад `S_r`:
сутки до суток сигнала — ссылки на файлы склада, сутки сигнала — файл, обрезанный до сделок `off ≤ сигнал` (кодек `tk728_trunc_store.py`; размер и mtime zip в заголовке прежние — бинарь принимает файл как свой),
сутки после сигнала — файла нет (промах → разбор zip-пустышки → ошибка запуска; ошибка записывается как ошибка, не как «прошло»; запасной вариант `trunc_keep` — сутки после сигнала ссылками, записывается отдельно).
Бинарь считает ту же строку на обрезанном складе; результат сверяется с прежним выходом (`--ref`, полные сделки) по всем столбцам текстом (кроме ep_id). Любое расхождение значения признака = заглядывание.
Контроли (метод 2, до чтения обрезанного): `ctrl` — все сутки ссылками, все строки монето-суток одним запуском (f12 — все строки монеты из прежнего sigsx); `ctrl1` — то же по одной строке; `rt1` — по одной строке, сутки сигнала перекодированы кодеком без обрезки.
Все три = прежний выход (0 расхождений, 0 ошибок) — иначе обрезанные счёты не читаются. Метод 1: кодек без обрезки воспроизводит файлы склада (нагрузки блоков, массивы, заголовок; сжатый блок Q у части файлов отличается байтами — версия zstd — и распаковывается в ту же нагрузку) — на файлах суток сигнала и нескольких соседних.
Положительный контроль силы теста: `early30` — обрезка за 30 минут до сигнала должна менять признаки.
Не обрезаются (только сделки): минутные свечи `--cache` (в том числе BTC / ETH для корреляций f12), метрики / премия / фандинг у `all`.
  python3 tk728_rust_causal.py --phase control --ref /data/p42-tk700/gate/p671/fam/rust/wb-long --win wb --side long --out /data/p42-tk728/res/rustcausal
  python3 tk728_rust_causal.py --phase trunc  ...    (только после control без расхождений и ошибок)"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk728_lib as L  # noqa: E402
import tk728_trunc_store as TS  # noqa: E402

STORE0 = "/data/p42-tk599/tick-store"
BIN = dict(f12="/data/p42-tk699/bin/alpha-feat-tf", all="/data/p42-tk639/bin/alpha-feat", tcount="/data/p42-tk639/bin/alpha-feat", tsize="/data/p42-tk639/bin/alpha-feat",
           tox="/data/p42-tk671/bin/alpha-feat-tox-ed78ac4c408073f5f7343538c99b1cf3", fast="/data/p42-tk681/bin/alpha-feat-tk681")
ENV = dict(TK594_METRICS="/data/p42-tk600/metrics-all", TK594_PREMIUM="/data/p42-tk600/premium-all", TK594_FUNDING_M="/data/p42-tk600/fund-all")
DAY_MS = 86_400_000
KEYS = {"f12": "sigsx", "all": "flow", "tcount": "ts", "tsize": "ts", "tox": "tox", "fast": "tox"}
RUN_TIMEOUT = 1500
CTRL = ("ctrl", "ctrl1", "rt1")


def tree_of(win):
    out = subprocess.run([sys.executable, os.path.join(HERE, "tk700_win.py"), "env", win], capture_output=True, text=True, check=True).stdout
    return dict(x.split("=", 1) for x in out.split() if "=" in x)


def build_store(root, sym, sig_abs, mode, cut_abs=None):
    """Личный склад монеты `root/<sym>/`. mode: ctrl (все сутки ссылками) | rt (сутки сигнала перекодированы без обрезки, остальные ссылками) | trunc (сутки сигнала обрезаны по `cut_abs` — по умолчанию момент сигнала, позже суток сигнала файлов нет) |
    trunc_keep (как trunc, сутки позже — ссылками). Возвращает сведения об обрезке суток сигнала."""
    src = os.path.join(STORE0, sym)
    dst = os.path.join(root, sym)
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(dst)
    day0 = (sig_abs // DAY_MS) * DAY_MS
    cut_abs = sig_abs if cut_abs is None else cut_abs
    sig_day = datetime.datetime.fromtimestamp(sig_abs / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
    info = dict(sig_day=sig_day, cut_off=int(cut_abs - day0), have_sig_file=False, later_days_kept=0, later_days_dropped=0)
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".tk"):
            continue
        day = fn[len(sym) + 1:-3]
        link = os.path.join(dst, fn)
        real = os.path.join(src, fn)
        if day == sig_day:
            info["have_sig_file"] = True
            if mode == "ctrl":
                os.symlink(real, link)
            else:
                info.update(TS.cut_file(real, link, None if mode == "rt" else int(cut_abs - day0)))
        elif day < sig_day:
            os.symlink(real, link)
        elif mode in ("ctrl", "rt", "trunc_keep"):
            os.symlink(real, link)
            info["later_days_kept"] += 1
        else:
            info["later_days_dropped"] += 1
    return info


def write_eps(work, step, rows, input_rows=None):
    """Входной файл шага из строк прежних входов (как есть, текстом)."""
    key = KEYS[step]
    df = pd.DataFrame(input_rows if input_rows is not None else [r[key] for r in rows], dtype=str)
    p = os.path.join(work, "in-%s.csv" % key)
    df.to_csv(p, index=False)
    return p


def run_job(job):
    """Один запуск бинаря: строки → CSV выхода. job: dict(step, work, store, rows, t0, tree, side, ...)."""
    step, work, store, rows = job["step"], job["work"], job["store"], job["rows"]
    os.makedirs(work, exist_ok=True)
    out = os.path.join(work, step + ".csv")
    if os.path.exists(out):
        os.remove(out)
    trades = os.path.join(job["tree"], "aggtrades")
    env = dict(os.environ, TK594_TRADES=trades, **ENV)
    inp = write_eps(work, step, rows, job.get("input_rows"))
    if step == "f12":
        cmd = [BIN[step], "f12", "--sigsx", inp, "--side", job["side"], "--out", out, "--cache", os.path.join(job["tree"], "cache"), "--trades", trades, "--base-date", job["t0"], "--days", "80", "--threads", "1", "--store", store]
    else:
        cmd = [BIN[step], step, "--eps", inp, "--out", out, "--base-date", job["t0"], "--trades", trades, "--store", store, "--threads", "1"]
        if step == "all":
            cmd += ["--store-cap-gb", "0"]
    t = time.time()
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        rc, err = r.returncode, (r.stderr or r.stdout or "")
    except subprocess.TimeoutExpired:
        rc, err = -9, "timeout %d с" % RUN_TIMEOUT
    sec = round(time.time() - t, 1)
    with open(os.path.join(work, step + ".err"), "w", encoding="utf-8") as f:
        f.write(err[-4000:])
    if rc != 0 or not os.path.exists(out):
        return dict(job=job, ok=False, rc=rc, err=err[-400:], sec=sec, got=None)
    got = pd.read_csv(out, dtype=str, keep_default_na=False)
    return dict(job=job, ok=True, rc=rc, err="", sec=sec, got=got)


def keyed(df, epmap):
    """Строки выхода по ключу (sym, sig_ms); у выходов без этих столбцов (`all`) — через ep_id → ключ."""
    if "sym" in df.columns and "sig_ms" in df.columns:
        return {(r["sym"], int(r["sig_ms"])): r for r in df.to_dict("records")}
    return {epmap[r["ep_id"]]: r for r in df.to_dict("records") if r["ep_id"] in epmap}


def compare(got, ref, rows, ref_epmap, ignore=()):
    """Сверка выхода с прежним по (sym, sig_ms), все столбцы текстом, кроме ep_id и `ignore`. Возвращает (строк сверено, ячеек сверено, [расхождения], [ошибки])."""
    got_epmap = {r["flow"]["ep_id"]: (r["sym"], int(r["sig_ms"])) for r in rows}
    refi, gi = keyed(ref, ref_epmap), keyed(got, got_epmap)
    n_rows = n_cells = 0
    bad, errs = [], []
    for row in rows:
        k = (row["ts"]["sym"], int(row["ts"]["sig_ms"]))
        g, r = gi.get(k), refi.get(k)
        if g is None:
            errs.append(dict(key=list(k), err="строки нет в новом выходе"))
            continue
        if r is None:
            errs.append(dict(key=list(k), err="строки нет в прежнем выходе"))
            continue
        cols = [c for c in got.columns if c != "ep_id" and c not in ignore and c in r]
        n_rows += 1
        n_cells += len(cols)
        for c in cols:
            if str(g[c]) != str(r[c]):
                bad.append(dict(key=list(k), col=c, got=str(g[c])[:40], ref=str(r[c])[:40]))
        extra = [c for c in got.columns if c != "ep_id" and c not in ignore and c not in r]
        if extra:
            errs.append(dict(key=list(k), err="столбцов нет в прежнем выходе: %s" % extra[:5]))
    return n_rows, n_cells, bad, errs


def pick(df, sym, sig):
    m = df[(df["sym"] == sym) & (df["sig_ms"].astype(np.int64) == int(sig))]
    return None if len(m) == 0 else m.iloc[0].to_dict()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--win", required=True)
    ap.add_argument("--side", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-cd", type=int, default=5)
    ap.add_argument("--max-rows", type=int, default=4)
    ap.add_argument("--steps", default="tcount,tsize,tox,fast,all,f12")
    ap.add_argument("--phase", choices=("control", "trunc", "all"), default="control")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--check-extra", type=int, default=3, help="соседних суток монеты (до суток сигнала) для метода 1")
    a = ap.parse_args()
    env = tree_of(a.win)
    t0, tree = env["T0"], env["TREE"]
    t0_ms = int(pd.Timestamp(t0, tz="UTC").timestamp() * 1000)
    steps = a.steps.split(",")
    ref = {s: pd.read_csv(os.path.join(a.ref, s + ".csv"), dtype=str, keep_default_na=False) for s in steps}
    ts0 = pd.read_csv(os.path.join(a.ref, "eps-ts.csv"), dtype=str, keep_default_na=False)
    inp = dict(ts=ts0,
               tox=pd.read_csv(os.path.join(a.ref, "eps-tox.csv"), dtype=str, keep_default_na=False),
               flow=pd.read_csv(os.path.join(a.ref, "eps-flow.csv"), dtype=str, keep_default_na=False),
               sigsx=pd.read_csv(os.path.join(a.ref, "sigsx.csv"), dtype=str, keep_default_na=False))
    ref_epmap = {r["ep_id"]: (r["sym"], int(r["sig_ms"])) for r in inp["flow"].to_dict("records")}
    # монето-сутки: случайные (зерно этапа) среди тех, где от 2 до max-rows строк базы
    ts = ts0.copy()
    ts["abs"] = ts["sig_ms"].astype(np.int64) + t0_ms
    ts["day"] = pd.to_datetime(ts["abs"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    g = ts.groupby(["sym", "day"]).size()
    cand = g[(g >= 2) & (g <= a.max_rows)].index.tolist()
    rng = np.random.default_rng(L.SEED)
    chosen = [cand[i] for i in sorted(rng.choice(len(cand), size=min(a.n_cd, len(cand)), replace=False))]
    print("кандидатов монето-суток %d, выбрано %d: %s" % (len(cand), len(chosen), chosen), flush=True)
    os.makedirs(a.out, exist_ok=True)
    cd_rows = {}
    for sym, day in chosen:
        cd_rows[(sym, day)] = [dict(sym=sym, day=day, sig_ms=int(sig), abs=int(sig) + t0_ms, **{k: pick(inp[k], sym, sig) for k in inp}) for sig in ts[(ts["sym"] == sym) & (ts["day"] == day)]["sig_ms"]]
    n_rows = sum(len(v) for v in cd_rows.values())
    work_root = os.path.join(a.out, "work")
    ctrl_path = os.path.join(a.out, "control.json")
    single = {(k[0], k[1], r["sig_ms"]): [r] for k, rows in cd_rows.items() for r in rows}
    sx_by_sym = {sym: inp["sigsx"][inp["sigsx"]["sym"] == sym].to_dict("records") for sym, _ in chosen}

    def run_all(jobs, label):
        out = []
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            for k, r in enumerate(ex.map(run_job, jobs)):
                out.append(r)
                print("%s %d/%d %s %s rc=%s %.0f с" % (label, k + 1, len(jobs), r["job"]["tag"], r["job"]["step"], r["rc"], r["sec"]), flush=True)
        return out

    def mk_jobs(variant, groups, mode, cut_back_ms=0):
        """groups: ключ → строки одного запуска. f12 в пакетном контроле `ctrl` получает все строки монеты из прежнего sigsx (тогда `ep_no` — порядковый номер среди входных строк монеты — совпадает с прежним);
        в остальных запусках — только строки теста, `ep_no` не сравнивается."""
        jobs, infos = [], {}
        for key, rows in groups.items():
            tagk = "-".join(str(x) for x in key)
            store = os.path.join(work_root, variant, tagk, "store")
            infos[tagk] = build_store(store, key[0], rows[0]["abs"], mode, rows[0]["abs"] - cut_back_ms)
            for s in steps:
                jobs.append(dict(step=s, work=os.path.join(work_root, variant, tagk, s), store=store, rows=rows, t0=t0, tree=tree, side=a.side, tag="%s/%s" % (variant, tagk), key=key,
                                 input_rows=(sx_by_sym[key[0]] if s == "f12" and variant == "ctrl" else None), ignore=("ep_no",) if s == "f12" and variant != "ctrl" else ()))
        return jobs, infos

    def fold(res_steps, variant, results):
        for r in results:
            s = r["job"]["step"]
            d = res_steps[s].setdefault(variant, dict(rows_compared=0, cells_compared=0, diff_cells=0, rows_with_diff=0, diff_columns={}, diff_examples=[], errors=[], sec=0.0))
            d["sec"] = round(d["sec"] + r["sec"], 1)
            if not r["ok"]:
                d["errors"].append(dict(tag=r["job"]["tag"], rc=r["rc"], err=r["err"]))
                continue
            nr, nc, bad, errs = compare(r["got"], ref[s], r["job"]["rows"], ref_epmap, r["job"]["ignore"])
            d["rows_compared"] += nr
            d["cells_compared"] += nc
            d["diff_cells"] += len(bad)
            d["rows_with_diff"] += len({tuple(b["key"]) for b in bad})
            for b in bad:
                d["diff_columns"][b["col"]] = d["diff_columns"].get(b["col"], 0) + 1
            d["diff_examples"] += bad[:5]
            d["errors"] += [dict(tag=r["job"]["tag"], **e) for e in errs]

    if a.phase in ("control", "all"):
        t_start = time.time()
        res = dict(phase="control", seed=L.SEED, win=a.win, side=a.side, ref=a.ref, store0=STORE0, chosen=[list(c) for c in chosen], n_rows=n_rows, steps={s: {} for s in steps}, store_info={},
                   variants=dict(ctrl="все строки монето-суток одним запуском, все сутки склада ссылками; f12 — все строки монеты из прежнего sigsx (ep_no сравнивается)",
                                 ctrl1="по одной строке, все сутки ссылками (f12: ep_no не сравнивается — порядковый номер строки среди входных)",
                                 rt1="по одной строке, сутки сигнала перекодированы кодеком без обрезки, остальные ссылками (тот же путь, что у обрезанного счёта, но без обрезки)"))
        m1 = []
        for (sym, day), rows in cd_rows.items():
            days = sorted(x[len(sym) + 1:-3] for x in os.listdir(os.path.join(STORE0, sym)) if x.endswith(".tk"))
            for d in [d for d in days if d <= day][-(a.check_extra + 1):]:
                m1.append(TS.check_file(os.path.join(STORE0, sym, "%s-%s.tk" % (sym, d))))
        res["method1"] = dict(files=len(m1), all_equal_arrays=all(x["arrays_equal"] for x in m1), all_equal_raw=all(x["raw_a_equal"] and x["raw_q_equal"] for x in m1), all_equal_head=all(set(x["head_diff"]) <= {"comp_q"} for x in m1),
                              all_equal_bytes=all(x["bytes_equal"] for x in m1), comp_a_equal=sum(x["comp_a_equal"] for x in m1), comp_q_equal=sum(x["comp_q_equal"] for x in m1),
                              not_equal=[x["path"] for x in m1 if not x["bytes_equal"]], flags_nonzero=[x["path"] for x in m1 if x["flags"]], detail=m1)
        print("метод 1: файлов %d, массивы равны %s, нагрузки блоков равны %s, заголовки равны (кроме длины сжатого блока Q) %s, блок A байт-в-байт %d, блок Q байт-в-байт %d" % (
            len(m1), res["method1"]["all_equal_arrays"], res["method1"]["all_equal_raw"], res["method1"]["all_equal_head"], res["method1"]["comp_a_equal"], res["method1"]["comp_q_equal"]), flush=True)
        for variant, groups, mode in (("ctrl", cd_rows, "ctrl"), ("ctrl1", single, "ctrl"), ("rt1", single, "rt")):
            jobs, infos = mk_jobs(variant, groups, mode)
            res["store_info"][variant] = infos
            fold(res["steps"], variant, run_all(jobs, variant))
        res["control_pass"] = bool(res["method1"]["all_equal_arrays"] and res["method1"]["all_equal_raw"] and res["method1"]["all_equal_head"]
                                   and all(res["steps"][s][v]["diff_cells"] == 0 and not res["steps"][s][v]["errors"] and res["steps"][s][v]["rows_compared"] == n_rows for s in steps for v in CTRL))
        res["runtime_s"] = round(time.time() - t_start, 1)
        json.dump(res, open(ctrl_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("КОНТРОЛЬ: %s" % ("прошёл" if res["control_pass"] else "НЕ ПРОШЁЛ"), {s: {v: (res["steps"][s][v]["diff_cells"], len(res["steps"][s][v]["errors"])) for v in CTRL} for s in steps}, flush=True)
        if a.phase == "control" or not res["control_pass"]:
            return

    if a.phase in ("trunc", "all"):
        ctrl = json.load(open(ctrl_path, encoding="utf-8")) if os.path.exists(ctrl_path) else None
        if not ctrl or not ctrl.get("control_pass") or ctrl["chosen"] != [list(c) for c in chosen]:
            print("контроль не пройден или не запускался — обрезанные счёты не читаются", flush=True)
            sys.exit(3)
        t_start = time.time()
        res = dict(phase="trunc", seed=L.SEED, win=a.win, side=a.side, ref=a.ref, store0=STORE0, chosen=ctrl["chosen"], n_rows=n_rows, steps={s: {} for s in steps},
                   control=dict(pass_=True, method1={k: v for k, v in ctrl["method1"].items() if k != "detail"}, diff_cells={s: {v: ctrl["steps"][s][v]["diff_cells"] for v in CTRL} for s in steps},
                                errors={s: {v: len(ctrl["steps"][s][v]["errors"]) for v in CTRL} for s in steps}, rows_compared={s: {v: ctrl["steps"][s][v]["rows_compared"] for v in CTRL} for s in steps}, variants=ctrl["variants"]),
                   not_truncated="минутные свечи --cache (в том числе BTCUSDT / ETHUSDT у f12), metrics / premium / funding у all; сделки других монет не подключаются (личный склад только монеты строки); f12: ep_no (порядковый номер среди входных строк монеты) не сравнивается")
        jobs, infos = mk_jobs("trunc", single, "trunc")
        res["rows"] = [dict(tag="-".join(str(x) for x in key), sym=key[0], day=key[1], sig_ms=key[2], **infos["-".join(str(x) for x in key)]) for key in single]
        results = run_all(jobs, "trunc")
        fold(res["steps"], "trunc", results)
        retry = [r["job"] for r in results if not r["ok"]]
        if retry:
            print("ошибки обрезанного счёта: %d, повтор с сутками после сигнала ссылками (trunc_keep)" % len(retry), flush=True)
            kept, jobs2 = {}, []
            for j in retry:
                tagk = j["tag"].split("/", 1)[1]
                store = os.path.join(work_root, "trunc_keep", tagk, "store")
                if tagk not in kept:
                    kept[tagk] = build_store(store, j["key"][0], j["rows"][0]["abs"], "trunc_keep")
                jobs2.append(dict(j, store=store, work=os.path.join(work_root, "trunc_keep", tagk, j["step"]), tag="trunc_keep/%s" % tagk))
            res["trunc_keep_info"] = kept
            fold(res["steps"], "trunc_keep", run_all(jobs2, "trunc_keep"))
        # положительный контроль силы теста: обрезка за 30 минут до сигнала должна менять признаки с окнами до сигнала
        jobs3, _ = mk_jobs("early30", single, "trunc", cut_back_ms=30 * 60 * 1000)
        fold(res["steps"], "early30", run_all(jobs3, "early30"))
        res["runtime_s"] = round(time.time() - t_start, 1)
        res["summary"] = {s: dict(rows=res["steps"][s]["trunc"]["rows_compared"], diff_cells=res["steps"][s]["trunc"]["diff_cells"], rows_with_diff=res["steps"][s]["trunc"]["rows_with_diff"], errors=len(res["steps"][s]["trunc"]["errors"]),
                              early30_rows_with_diff=res["steps"][s]["early30"]["rows_with_diff"], early30_errors=len(res["steps"][s]["early30"]["errors"]), sec=res["steps"][s]["trunc"]["sec"]) for s in steps}
        json.dump(res, open(os.path.join(a.out, "rustcausal.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps(dict(n_rows=n_rows, summary=res["summary"], runtime_s=res["runtime_s"]), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
