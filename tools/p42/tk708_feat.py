#!/usr/bin/env python3
"""TK-708: четыре линии TK-674 (L-428: L1, L2 — лонг с фильтром; S1, S2 — шорт без фильтра) в проверке вперёд.
S1 / S2 — только ключи движка (`ckey` без значений по умолчанию), таблиц нет. L1 / L2 — таблица `--ftab` по потоку клетки: дамп сигналов движка → `alpha-feat f12` (cons60) → `alpha-feat flow`
(big1800) → `alpha-feat tox` (vpin1h) → значения (`float32` → `float64`, как parquet TK-671) → составной столбец «проходит» (правило `tk674_run._pass`: «определён и не проходит» — исключён, пусто проходит).
Охрана NaN (решение координатора, жёсткая): доля пустых значений признака линии среди кандидатов суток выше максимальной суточной доли в исследовании — линия на эти сутки «нет данных»
(все кандидаты суток исключены, сделок нет), сутки и доли печатаются в `info`. Модуль не импортирует `tk616_forward` (хук импортирует его)."""
from __future__ import annotations

import csv
import datetime
import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402

GuardError = rf.GuardError
DAY_MS = rf.DAY_MS
FEATS = ("big1800", "cons60", "vpin1h")
FEAT_BIN_NAMES = ("f12", "flow", "tox")
FV_PASS = 0.5                                          # `fv` столбца: 0,0 — исключён, 1,0 — проходит (как у остальных таблиц `--ftab`)
F12_DAYS = 80                                          # сетка суток минуток (`--days`), как у таблиц TK-599 / TK-671
FILTER_RE = re.compile(r"^([a-z0-9_]+)(<=|>=)([0-9.eE+-]+)$")


def t0_ms(day):
    return int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def day_of(abs_ms):
    return datetime.date.fromordinal(719163 + int(abs_ms) // DAY_MS)


# ---- ключи клеток --------------------------------------------------------------------------------------------------------------------

def parse_filter(text):
    """`big1800<=0.29&cons60<=1&vpin1h>=0.2` → [(признак, знак, порог float64)]; незнакомый признак или знак — `GuardError`."""
    out = []
    for tok in text.split("&"):
        m = FILTER_RE.match(tok)
        if not m or m.group(1) not in FEATS:
            raise GuardError("фильтр %r: токен %r не из %s и не `<=` / `>=`" % (text, tok, FEATS))
        out.append((m.group(1), m.group(2), float(m.group(3))))
    if len({f for f, _, _ in out}) != len(out):
        raise GuardError("фильтр %r: признак повторяется" % text)
    return out


def filter_of(ckey):
    """`ckey` клетки TK-674 → (ключи движка без `F=`, [(признак, знак, порог)], текст фильтра или "")."""
    if " F=" in ckey:
        plain, text = ckey.split(" F=", 1)
        return plain, parse_filter(text), text
    return ckey, [], ""


def cells_of(t674, cols=None):
    """{id: {side, plain, filter, text, ckey, col, keys, vid}} — четыре линии файла заморозки TK-674 (`candidates`), в порядке файла. `keys` — ключи запроса движка: у лонга с фильтром
    `<plain> fa=<col> fo=lo fv=0.5` (`cols` — {id: имя столбца}; по умолчанию `f708_<id>`), иначе `plain`. Потоки лонг-линий (plain) различны — таблица потока одна на линию."""
    out = {}
    for c in t674["candidates"]:
        plain, flt, text = filter_of(c["ckey"])
        side = {"long": "L", "short": "S"}.get(c["side"])
        if side is None:
            raise GuardError("линия %s: сторона %r" % (c["id"], c["side"]))
        if bool(flt) != (side == "L"):
            raise GuardError("линия %s: фильтр %s у стороны %s (L1 / L2 — с фильтром, S1 / S2 — без)" % (c["id"], "есть" if flt else "нет", side))
        col = (cols or {}).get(c["id"], "f708_%s" % c["id"]) if flt else ""
        keys = "%s fa=%s fo=lo fv=%g" % (plain, col, FV_PASS) if flt else plain
        out[c["id"]] = {"side": side, "plain": plain, "filter": flt, "text": text, "ckey": c["ckey"], "col": col, "keys": keys, "vid": rf.Req(side, keys, False).vid}
    needs = [rr.Engine.stream_need(rf.Req("L", v["keys"], False)).hash for v in out.values() if v["side"] == "L"]
    if len(set(needs)) != len(needs):
        raise GuardError("потоки лонг-линий совпадают: таблицы фильтров легли бы в один каталог")
    return out


def req_of(cell):
    return rf.Req(cell["side"], cell["keys"], False)


# ---- охрана бинарей --------------------------------------------------------------------------------------------------------------------

def check_bins(paths, spec):
    """Бинари признаков (f12 / flow / tox) = замороженным md5 и sha256 — до чтения данных. `paths` — {имя: путь}, `spec` — {имя: {md5, sha256, ...}}. → {имя: (md5, sha256)}."""
    out = {}
    for n in FEAT_BIN_NAMES:
        p = (paths or {}).get(n, "")
        if not p or not os.path.exists(p):
            raise GuardError("--%s-bin: нет файла %r (нужен `alpha-feat` линий L1 / L2)" % (n, p))
        sha, md5 = rr.file_hashes(p)
        b = spec[n]
        if md5 != b["md5"] or sha != b["sha256"]:
            raise GuardError("alpha-feat (%s) %s: md5 %s / sha256 %s… ≠ замороженным %s / %s…" % (n, p, md5, sha[:12], b["md5"], b["sha256"][:12]))
        out[n] = (md5, sha)
    return out


# ---- дамп потока ------------------------------------------------------------------------------------------------------------------------

def read_sigsx_keys(path):
    """(монеты, sig_ms) кандидатов потока из `long-sigsx.csv` дампа (по позициям: в заголовке бинаря нет `natr_e8`); порядок файла; дубль ключа — остановка."""
    syms, ms = [], []
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        head = next(rd, None)
        if not head or head[0] != "sym" or head[2] != "sig_ms":
            raise GuardError("сигнальный дамп %s: заголовок %r" % (path, head))
        for r in rd:
            if r:
                syms.append(r[0])
                ms.append(int(r[2]))
    if len(set(zip(syms, ms))) != len(syms):
        raise GuardError("сигнальный дамп %s: ключ (sym, sig_ms) не уникален" % path)
    return syms, np.array(ms, dtype=np.int64)


def done_mark(path):
    return path + ".ok"


def dump_stream(paths, runner, eng, w, need, reuse=False):
    """Сигнальный дамп движка по потоку `need` окна `w` (`P42_SIGSX_ONLY`). `reuse` (только ворота): дамп с меткой `.ok` прошлого прогона не пересчитывается. → (монеты, sig_ms, путь дампа)."""
    dump = os.path.join(paths.sx(w.name), need.vid, "long-sigsx.csv")
    if reuse and os.path.exists(dump) and os.path.exists(done_mark(dump)):
        return (*read_sigsx_keys(dump), dump)
    if os.path.exists(done_mark(dump)):
        os.remove(done_mark(dump))
    vfile = os.path.join(paths.var, "sx-%s-%s.txt" % (w.name, need.vid))
    os.makedirs(paths.var, exist_ok=True)
    with open(vfile, "w", encoding="utf-8", newline="\n") as f:
        f.write("%s %s\n" % (need.vid, need.keys))
    cmd, env = eng.command(w, vfile, paths.sx(w.name), None, sigsx_only=True)
    log = os.path.join(paths.log, "sx-%s-%s-%s.log" % (getattr(paths, "uni", ""), w.name, need.vid))
    if runner.run(cmd, env, log) != 0:
        raise GuardError("движок (сигнальный дамп %s), окно %s: код возврата ≠ 0\n%s" % (getattr(paths, "uni", ""), w.name, rr.tail(log)))
    if not os.path.exists(dump):
        raise GuardError("нет сигнального дампа %s" % dump)
    syms, ms = read_sigsx_keys(dump)
    open(done_mark(dump), "w").close()
    return syms, ms, dump


# ---- признаки ---------------------------------------------------------------------------------------------------------------------------

def num(x):
    return float("nan") if x is None or x == "" else float(x)


def read_cols(path, cols, what):
    """CSV `alpha-feat`: {столбец: [строки]} по заголовку; нет файла или столбца — `GuardError`."""
    if not os.path.exists(path):
        raise GuardError("нет выхода %s: %s" % (what, path))
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        miss = [c for c in cols if c not in (rd.fieldnames or [])]
        if miss:
            raise GuardError("выход %s %s: нет столбцов %s (заголовок %s)" % (what, path, miss, rd.fieldnames))
        out = {c: [] for c in cols}
        for r in rd:
            for c in cols:
                out[c].append(r[c])
    return out


def run_tool(runner, cmd, log, what, w, out, reuse=False):
    """`alpha-feat` с проверкой кода возврата; метка `.ok` выхода пишется после успеха; `reuse` (только ворота) — выход с меткой прошлого прогона не пересчитывается."""
    if reuse and os.path.exists(out) and os.path.exists(done_mark(out)):
        return
    if os.path.exists(done_mark(out)):
        os.remove(done_mark(out))
    if runner.run(cmd, None, log) != 0:
        raise GuardError("alpha-feat %s, окно %s: код возврата ≠ 0\n%s" % (what, w.name, rr.tail(log)))
    if not os.path.exists(out):
        raise GuardError("alpha-feat %s, окно %s: нет выхода %s" % (what, w.name, out))
    open(done_mark(out), "w").close()


def f12_summary(log):
    """Итог-JSON `alpha-feat f12` — последняя строка журнала, начинающаяся с `{`."""
    try:
        lines = [x for x in open(log, encoding="utf-8", errors="replace").read().splitlines() if x.startswith("{")]
    except OSError:
        lines = []
    if not lines:
        raise GuardError("alpha-feat f12: в журнале %s нет итога-JSON" % log)
    return json.loads(lines[-1])


def compute_features(paths, runner, w, syms, ms, dump, bins, threads, tick_store, days, tag, flow_mode="flow", reuse=False):
    """Три признака на строках дампа (в порядке дампа): {cons60, big1800, vpin1h: float64[n]} (float32-значения, как parquet TK-671) + итоги охран."""
    n = len(syms)
    d_ = os.path.join(paths.var, "feat-%s-%s-%s" % (getattr(paths, "uni", ""), w.name, tag))
    os.makedirs(d_, exist_ok=True)
    key = lambda name: os.path.join(d_, name)                                           # noqa: E731
    store = ["--store", tick_store] if tick_store else []
    f12, f12log = key("f12.csv"), key("f12.log")
    run_tool(runner, [bins["f12"], "f12", "--sigsx", dump, "--side", "long", "--out", f12, "--cache", paths.cache(w.name), "--trades", paths.trades(w.name),
                      "--base-date", w.t0, "--days", str(days), "--threads", str(threads)] + store, f12log, "f12", w, f12, reuse)
    sm = f12_summary(f12log)
    if sm.get("locate_fail") != 0 or sm.get("natr_mismatch") != 0:
        raise GuardError("alpha-feat f12, окно %s: locate_fail %s, natr_mismatch %s (нужно 0 / 0)" % (w.name, sm.get("locate_fail"), sm.get("natr_mismatch")))
    c = read_cols(f12, ("sym", "sig_ms", "side", "cons60", "turn24h"), "f12")
    fk = list(zip(c["sym"], (int(x) for x in c["sig_ms"])))
    if len(fk) != n or len(set(fk)) != n or set(fk) != set(zip(syms, (int(x) for x in ms))):
        raise GuardError("alpha-feat f12, окно %s: ключи (sym, sig_ms) выхода (%d строк) ≠ ключам дампа (%d строк)" % (w.name, len(fk), n))
    order = {k: i for i, k in enumerate(zip(syms, (int(x) for x in ms)))}
    perm = np.array([order[k] for k in fk], dtype=np.int64)                              # строка f12 i → строка дампа perm[i]
    cons = np.full(n, np.nan)
    cons[perm] = [num(x) for x in c["cons60"]]
    eps = key("eps-flow.csv")
    with open(eps, "w", encoding="utf-8", newline="\n") as f:
        f.write("ep_id,sym,side,sig_ms,turn24h\n" + "".join("e%d,%s,%s,%s,%s\n" % (i, c["sym"][i], c["side"][i], c["sig_ms"][i], c["turn24h"][i]) for i in range(n)))
    flow = key("flow.csv")
    run_tool(runner, [bins["flow"], flow_mode, "--eps", eps, "--out", flow, "--base-date", w.t0, "--trades", paths.trades(w.name), "--threads", str(threads)] + (
        ["--store", tick_store, "--store-cap-gb", "0"] if tick_store else []), key("flow.log"), flow_mode, w, flow, reuse)
    fc = read_cols(flow, ("ep_id", "big1800"), "flow")
    ids = {"e%d" % i: i for i in range(n)}
    if len(fc["ep_id"]) != n or set(fc["ep_id"]) != set(ids):
        raise GuardError("alpha-feat %s, окно %s: ep_id выхода (%d строк) ≠ входу (%d строк)" % (flow_mode, w.name, len(fc["ep_id"]), n))
    big = np.full(n, np.nan)
    big[perm[[ids[x] for x in fc["ep_id"]]]] = [num(x) for x in fc["big1800"]]
    tin = key("eps-tox.csv")
    with open(tin, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms,side\n" + "".join("%s,%d,long\n" % (s, m) for s, m in zip(syms, ms)))
    tox = key("tox.csv")
    run_tool(runner, [bins["tox"], "tox", "--eps", tin, "--out", tox, "--base-date", w.t0, "--trades", paths.trades(w.name), "--threads", str(threads)] + (["--store", tick_store] if tick_store else []),
             key("tox.log"), "tox", w, tox, reuse)
    tc = read_cols(tox, ("sym", "sig_ms", "vpin1h"), "tox")
    tk = list(zip(tc["sym"], (int(x) for x in tc["sig_ms"])))
    if len(tk) != n or tk != list(zip(syms, (int(x) for x in ms))):
        raise GuardError("alpha-feat tox, окно %s: строки выхода (%d) ≠ входу по порядку (%d)" % (w.name, len(tk), n))
    vp = np.array([num(x) for x in tc["vpin1h"]])
    f32 = lambda a: np.asarray(a, dtype=np.float64).astype(np.float32).astype(np.float64)   # noqa: E731  — как parquet TK-671: значение float32, порог сравнивается после расширения
    return {"cons60": f32(cons), "big1800": f32(big), "vpin1h": f32(vp)}, {"f12": sm}


def passes(v, op, thr):
    """Правило `tk674_run._pass`: «определён и не проходит» — исключён, пусто (NaN, ±inf) проходит."""
    with np.errstate(invalid="ignore"):
        ok = (v <= thr) if op == "<=" else (v >= thr)
    return ~(np.isfinite(v) & ~ok)


def composite(vals, flt):
    """Составной столбец линии: И по признакам фильтра. → bool[n] (True — проходит)."""
    return np.logical_and.reduce([passes(vals[f], op, x) for f, op, x in flt])


def day_stats(vals, feats, ms, off):
    """{сутки: {строк, признак: пустых}} по суткам сигнала (UTC). Признаки — `feats`."""
    days = (np.asarray(ms, dtype=np.int64) + off) // DAY_MS
    out = {}
    for dd in np.unique(days):
        sel = days == dd
        out[day_of(int(dd) * DAY_MS)] = dict({"rows": int(sel.sum())}, **{f: int(np.isnan(vals[f][sel]).sum()) for f in feats})
    return out


def guard_days(stats, feats, nan_guard):
    """Сутки «нет данных»: доля пустых значений признака среди кандидатов суток > максимальной суточной доли исследования (`nan_guard[признак]`). → [{day, feat, nan, rows, share, max}]."""
    out = []
    for dd, s in sorted(stats.items()):
        for f in feats:
            thr = nan_guard[f]
            share = s[f] / s["rows"]
            if share > thr:
                out.append({"day": dd.isoformat(), "feat": f, "nan": s[f], "rows": s["rows"], "share": share, "max": thr})
    return out


def missing_files(paths, w, syms, ms, off):
    """Сколько строк потока приходится на (монета, сутки) без файла aggTrades суток сигнала / предыдущих суток (печать в `info`, порога нет). Только имена файлов."""
    have = {}
    for s in set(syms):
        p = os.path.join(paths.trades(w.name), s)
        have[s] = {m.group(1) for m in (re.search(r"(\d{4}-\d{2}-\d{2})", x) for x in (os.listdir(p) if os.path.isdir(p) else [])) if m}
    cur = prev = 0
    for s, m in zip(syms, ms):
        dd = day_of(int(m) + off)
        cur += dd.isoformat() not in have[s]
        prev += (dd - datetime.timedelta(days=1)).isoformat() not in have[s]
    return cur, prev


def write_ftab(path, syms, ms, name, col):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms,%s\n" % name + "".join("%s,%d,%s\n" % (s, m, "1.0" if v else "0.0") for s, m, v in zip(syms, ms, col)))


def build_filter_table(paths, runner, eng, w, need, cell, bins, threads, tick_store, days, lohi, nan_guard=None, flow_mode="flow", reuse=False):
    """Таблица `--ftab` лонг-линии в окне `w`: дамп потока → признаки → составной столбец (охрана NaN по суткам) → `ftab.csv` в `paths.need_tab`. `lohi` — зачётные даты окна (печать долей).
    → (информация окна, значения признаков, монеты, sig_ms) — значения нужны воротам."""
    syms, ms, dump = dump_stream(paths, runner, eng, w, need, reuse)
    vals, extra = compute_features(paths, runner, w, syms, ms, dump, bins, threads, tick_store, days, need.vid, flow_mode, reuse)
    feats = [f for f, _, _ in cell["filter"]]
    off = t0_ms(w.t0d)
    stats = day_stats(vals, feats, ms, off)
    flagged = guard_days(stats, feats, nan_guard) if nan_guard else []
    bad = {datetime.date.fromisoformat(x["day"]) for x in flagged}
    keep = composite(vals, cell["filter"])
    if bad:
        keep = keep & np.array([day_of(int(m) + off) not in bad for m in ms], dtype=bool)
    tab = paths.need_tab(w.name, need)
    write_ftab(tab, syms, ms, cell["col"], keep)
    cr = [lohi[0] <= dd <= lohi[1] for dd in (day_of(int(m) + off) for m in ms)]
    cr = np.array(cr, dtype=bool)
    miss_cur, miss_prev = missing_files(paths, w, syms, ms, off)
    info = {"rows": len(syms), "credited": int(cr.sum()), "credited_range": [lohi[0].isoformat(), lohi[1].isoformat()], "excluded": int((~keep).sum()),
            "nan": {f: {"all": int(np.isnan(vals[f]).sum()), "credited": int((np.isnan(vals[f]) & cr).sum())} for f in feats},
            "rows_on_missing_day": miss_cur, "rows_on_missing_prev_day": miss_prev, "guard_days": flagged, "guard_rows": int(sum(stats[datetime.date.fromisoformat(x)]["rows"] for x in {y["day"] for y in flagged})),
            "ftab_sha256": rr.file_hashes(tab)[0], "f12": extra["f12"]}
    return info, vals, syms, ms


def filter_meta(paths, w, vid):
    """Счётчики фильтра движка окна (`meta.json`): кандидатов, отброшено, no_key, no_value; None — прогона окна нет."""
    p = os.path.join(paths.out(w.name), vid, "meta.json")
    if not os.path.exists(p):
        return None
    st = json.load(open(p, encoding="utf-8")).get(rf.ENGINE_SIDE["L"], {}).get("filter_[cands,dropped,no_key,no_value]")
    return {"cands": int(st[0]), "dropped": int(st[1]), "no_key": int(st[2]), "no_value": int(st[3])} if st else None


# ---- прогон линий -----------------------------------------------------------------------------------------------------------------------

def read_tables(paths, req, wins):
    return {w.name: rf.read_trades_csv(os.path.join(paths.out(w.name), req.vid, rf.ENGINE_SIDE[req.side] + "-trades.csv")) for w in wins}


def run_cells(paths, runner, symbols_text, bin_path, threads, wins, cells, bins, tick_store, lohi_of, days=F12_DAYS, nan_guard=None, flow_mode="flow", reuse=False):
    """Четыре линии одной вселенной. S1 / S2 — один вызов движка с замороженными ключами; каждая лонг-линия — своя таблица потока и свой вызов (`ensure_tables` не нужен: таблица записана до него).
    Любая `GuardError` линии — линия НЕ читается (причина в `info`), остальные идут. `nan_guard` — {линия: {признак: макс. суточная доля}}.
    → ({линия: {окно: таблица сделок}}, {линия: info})."""
    eng = rr.Engine(paths, runner, symbols_text, bin_path, threads, None, ensure_tables=lambda needs: None, windows=tuple(wins), prefix="t")
    tabs, info = {}, {}
    shorts = {i: c for i, c in cells.items() if c["side"] == "S"}
    if shorts:
        try:
            eng.launch([req_of(c) for c in shorts.values()])
            for i, c in shorts.items():
                tabs[i] = read_tables(paths, req_of(c), wins)
                info[i] = {"status": "read", "reason": "", "side": "S", "keys": c["keys"], "vid": c["vid"]}
        except GuardError as e:
            for i, c in shorts.items():
                tabs.pop(i, None)
                info[i] = {"status": "skip", "reason": str(e), "side": "S", "keys": c["keys"], "vid": c["vid"]}
    for i, c in cells.items():
        if c["side"] != "L":
            continue
        req = req_of(c)
        info[i] = x = {"status": "skip", "reason": "", "side": "L", "keys": c["keys"], "vid": c["vid"], "filter": c["text"], "windows": {}}
        try:
            need = rr.Engine.stream_need(req)
            for w in wins:
                x["windows"][w.name] = build_filter_table(paths, runner, eng, w, need, c, bins, threads, tick_store, days, lohi_of(w), (nan_guard or {}).get(i), flow_mode, reuse)[0]
            eng.launch([req])
            tabs[i] = read_tables(paths, req, wins)
        except GuardError as e:
            x["reason"] = str(e)
            for w in wins:
                m = filter_meta(paths, w, req.vid)
                if m and w.name in x["windows"]:
                    x["windows"][w.name]["engine"] = m
            continue
        for w in wins:
            x["windows"][w.name]["engine"] = filter_meta(paths, w, req.vid)
        x["status"] = "read"
        x["no_key"] = sum(v["engine"]["no_key"] for v in x["windows"].values())
    return tabs, info


GATE_FILES = ("trades.csv", "daily.csv", "coinday.csv", "entryrej.csv")


def compare_gate(paths, cells, wins, gate_cells):
    """sha256 `long-/short-{trades,daily,coinday,entryrej}.csv` линий против `tk674-gate-cells.json` (`cells.<id>.windows.<окно>.files.<файл>.sha256`). → {линия: {окно: {файл: {sha256, ref_sha256, equal}}}}."""
    out = {}
    for i, c in cells.items():
        pre = rf.ENGINE_SIDE[c["side"]] + "-"
        out[i] = {}
        for w in wins:
            files = {}
            for f in GATE_FILES:
                p = os.path.join(paths.out(w.name), c["vid"], pre + f)
                sx = rr.file_hashes(p)[0] if os.path.exists(p) else None
                sy = gate_cells["cells"][i]["windows"][w.name]["files"][f]["sha256"]
                files[f] = {"sha256": sx, "ref_sha256": sy, "equal": sx is not None and sx == sy}
            out[i][w.name] = files
    return out
