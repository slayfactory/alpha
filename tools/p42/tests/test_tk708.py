#!/usr/bin/env python3
"""TK-708 (L-428): шестое звено цепочки `tk708-frozen.json` в `tk616_forward.py` и построитель `tk708_feat.py` — четыре линии TK-674 (L1, L2 — лонг с фильтром признаков, S1, S2 — шорт без фильтра) на c539.
Синтетика, заготовки — `test_tk616_forward.py` (`Env`, `N24Runner`) и `test_tk691.py` (цепочка до звена 5). Запуск: `python3 tools/p42/tests/test_tk708.py` (на srv)."""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime
import json
import os
import subprocess
import sys
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402
import test_tk616_forward as T  # noqa: E402
import test_tk670 as U  # noqa: E402
import test_tk691 as V  # noqa: E402
import tk616_forward as fw  # noqa: E402
import tk708_feat as f7  # noqa: E402

fw.REQUIRE_CANDIDATE5 = False                         # прежние тесты — без `tk708-frozen.json`; охрана «нет звена 6 — остановка» проверяется в `strict5()`
E7_BYTES = b"synthetic-tk674-engine"
FB_BYTES = {"f12": b"synthetic-alpha-feat-f12", "flow": b"synthetic-alpha-feat-flow", "tox": b"synthetic-alpha-feat-tox"}
P1 = "ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12"
P2 = "busy=0 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12 tpk=3"
SK1 = "bm=1 e=0.15 e2t=2 ew=5 fh=4 fm=50 lat=4 latx=4 n_bounce=1 natr_p=60 pb=1 pk=1 rf=0.2 rr=1 rs=2 s=4 t24=12 tb=0.1 tt=20"
SK2 = "bm=1 e=0.15 e2t=2 ew=5 fh=4 fm=50 lat=4 latx=4 n_bounce=1 natr_min=0.6 nf=1 pb=1 pk=1 q=100 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20"
LINES = [("L1", "long", P1 + " F=big1800<=0.29&cons60<=1&vpin1h>=0.2"), ("L2", "long", P2 + " F=big1800<=0.29&vpin1h>=0.2"), ("S1", "short", SK1), ("S2", "short", SK2)]
NAN_GUARD = {"L1": {"big1800": 0.5, "cons60": 0.5, "vpin1h": 0.5}, "L2": {"big1800": 0.5, "vpin1h": 0.5}}
TOOLS = ("f12", "flow", "all", "tox")
D, h = T.D, T.h


@contextlib.contextmanager
def strict5():
    fw.REQUIRE_CANDIDATE5 = True
    try:
        yield
    finally:
        fw.REQUIRE_CANDIDATE5 = False


@contextlib.contextmanager
def strict_all5():
    with V.strict_all(), strict5():
        yield


# ---- заготовки: файлы звена 5 и файл отбора TK-674 -------------------------------------------------------------------------------------

def make_bins(e):
    e.e7 = os.path.join(e.root, "alpha-tk674")
    open(e.e7, "wb").write(E7_BYTES)
    e.fb = {}
    for n, b in FB_BYTES.items():
        e.fb[n] = os.path.join(e.root, "alpha-feat-" + n)
        open(e.fb[n], "wb").write(b)


def make_t674(e, mutate=None):
    sha, md5 = T.sha_of(E7_BYTES)
    t = {"version": fw.T674_VERSION, "binary": {"freeze": {"path": "/x/alpha-tk674", "md5": md5, "sha256": sha}},
         "candidates": [{"id": i, "side": s, "ckey": k, "rank": 1} for i, s, k in LINES]}
    if mutate:
        mutate(t)
    path = fw.tk674_path(e.freeze)
    json.dump(t, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return t, path


def make_candidate5(e, cp4, t674, tp, mutate=None):
    """Звено 6 `tk708-frozen-1` против заморозки, звена tk691, файла TK-674 и бинарей среды `e`: клетки = пересчёт `f708.cells_of`, α нет, охрана NaN 0,5 по признакам, ворота пройдены."""
    cells = f7.cells_of(t674)
    sha, md5 = T.sha_of(E7_BYTES)
    c5 = {"version": fw.CAND5_VERSION, "base_freeze": {"version": "tk626-frozen-1", "sha256": fw.sha256_text(e.freeze)},
          "prev_candidate4": {"version": "tk691-frozen-1", "sha256": fw.sha256_text(cp4)}, "tk674": {"version": fw.T674_VERSION, "sha256": fw.sha256_text(tp)},
          "binary": {"md5": md5, "sha256": sha}, "feature_binaries": {n: {"path": e.fb[n], "md5": T.sha_of(b)[1], "sha256": T.sha_of(b)[0]} for n, b in FB_BYTES.items()},
          "f12_days": 80, "flow_mode": "flow", "cells": {i: {k: v[k] for k in fw.CAND5_CELL_FIELDS} for i, v in cells.items()},
          "nan_guard": {"max_daily_share": json.loads(json.dumps(NAN_GUARD))}, "gate": {"feat": {"ok": True, "result_sha256": "a" * 64}, "replay": {"ok": True, "result_sha256": "b" * 64}}}
    if mutate:
        mutate(c5)
    path = fw.candidate5_path(e.freeze)
    json.dump(c5, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return c5, path


class Ctx:
    """Среда цепочки до звена 6: `e` (Env), звенья 1–5 (`c…`, `cp…`), файл TK-674 (`t674`, `tp`) и звено 6 (`c5`, `cp5`)."""


def ctx5(t, mutate5=None, mutate_t=None, passed=(True, True, True)):
    x = Ctx()
    x.e, x.c, x.cp, x.c2, x.cp2, x.c3, x.cp3, x.c4, x.cp4 = V.cand4_env(t, passed)
    make_bins(x.e)
    x.t674, x.tp = make_t674(x.e, mutate_t)
    x.c5, x.cp5 = make_candidate5(x.e, x.cp4, x.t674, x.tp, mutate5)
    return x


def kw5(x, **kw):
    out = V.kw4(x.e, x.cp, x.cp2, x.cp3, x.cp4)
    sha = lambda p: fw.sha256_text(p) if os.path.exists(p) else ""                                          # noqa: E731  — файл мог быть удалён тестом охраны
    out.update(candidate5="", candidate5_commit="c0ffee", candidate5_sha256=sha(x.cp5), tk674="", tk674_commit="c0ffee", tk674_sha256=sha(x.tp),
               tk674_bin=x.e.e7, f12_bin=x.e.fb["f12"], flow_bin=x.e.fb["flow"], tox_bin=x.e.fb["tox"], no_tk708=False)
    out.update(kw)
    return out


def fz_after4(x):
    """Заморозка после звена tk691 (читается и решает crypto) + загруженное звено 5 — как делает `run_point`."""
    fz = fw.load_freeze(x.e.freeze)
    c = fw.load_candidate(x.cp, fz, x.e.freeze)
    c2 = fw.load_candidate2(x.cp2, fz, x.e.freeze, c, x.cp)
    c3 = fw.load_candidate3(x.cp3, fz, x.e.freeze, c, x.cp, c2, x.cp2)
    c4 = fw.load_candidate4(x.cp4, fz, x.e.freeze, c, x.cp, c2, x.cp2, c3, x.cp3)
    fz = fw.apply_candidate3(fw.apply_candidate2(fw.apply_candidate(fz, c), c2, True), c3)
    fz, _ = fw.apply_candidate4(fz, c, c4)
    return fz, c4


def load5(x):
    fz, c4 = fz_after4(x)
    return fw.load_candidate5(x.cp5, fz, x.e.freeze, c4, x.cp4, json.load(open(x.tp, encoding="utf-8")), x.tp)


# ---- подменный движок и alpha-feat --------------------------------------------------------------------------------------------------------

def fv(feat, sym, abs_ms):
    """Значения признаков — функция (признак, монета, абсолютное время сигнала: одно и то же в соседних окнах, иначе швы окон разойдутся); big1800 ∈ [0; 0,4), cons60 ∈ {0, 1, 2}, vpin1h ∈ [0,1; 1)."""
    x = h(feat, sym, abs_ms)
    if feat == "big1800":
        return x % 1000 / 2500.0
    if feat == "cons60":
        return float(x % 3)
    return 0.1 + x % 1000 / 1111.0


def arg(cmd, k):
    return cmd[cmd.index(k) + 1]


def read_dump(path):
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        return [(r[0], int(r[2])) for r in rd if r]


class Runner5(V.Rec):
    """Движок `N24Runner` (дамп, `--ftab` с произвольным столбцом) + `alpha-feat f12 / flow / all / tox` по значениям `fv`. `nan` — {признак: функция (монета, абс. время) → пусто}; `vals` — {(признак, монета, сдвиг): строка};
    `f12_json` — итог f12 (False — без итога); `drop_f12` — f12 теряет последнюю строку; `swap_tox` — tox меняет порядок; `drop_flow`; `fail` — {инструмент: подстрока команды}: при совпадении код 1;
    `extra_cands` — кандидатов движка сверх таблицы (no_key).
    Выходы движка дополняются `daily / coinday / entryrej` (для сверки sha256 ворот)."""

    def __init__(self, last_day, nan=None, vals=None, f12_json=None, drop_f12=False, swap_tox=False, drop_flow=False, fail=None, extra_cands=0):
        super().__init__(last_day)
        self.extra_cands = extra_cands
        self.nan, self.vals, self.drop_f12, self.swap_tox, self.drop_flow, self.fail, self.tools = nan or {}, vals or {}, drop_f12, swap_tox, drop_flow, fail or {}, []
        self.f12_json = {"locate_fail": 0, "natr_mismatch": 0, "rows": 0} if f12_json is None else f12_json

    def run(self, cmd, env=None, log=None):
        if len(cmd) > 1 and cmd[1] in TOOLS:
            self.tools.append(cmd)
            return self.tool(cmd, log)
        rc = super().run(cmd, env, log)
        if rc == 0 and "--variants" in cmd and not (env and env.get("P42_SIGSX_ONLY") == "1"):
            self.extras(cmd)
        return rc

    def extras(self, cmd):
        out = arg(cmd, "--out")
        for line in open(arg(cmd, "--variants"), encoding="utf-8"):
            vid = line.split(" ", 1)[0]
            for side in ("long", "short"):
                tr = os.path.join(out, vid, side + "-trades.csv")
                if os.path.exists(tr):
                    body = open(tr, encoding="utf-8").read()
                    for name in ("daily", "coinday", "entryrej"):
                        open(os.path.join(out, vid, "%s-%s.csv" % (side, name)), "w", encoding="utf-8", newline="").write("%s\n%s" % (name, body))

    def val(self, feat, sym, rel, t0ms):
        if (feat, sym, rel) in self.vals:
            return self.vals[(feat, sym, rel)]
        if feat in self.nan and self.nan[feat](sym, t0ms + rel):
            return ""
        return repr(fv(feat, sym, t0ms + rel))

    def tool(self, cmd, log):
        name = cmd[1]
        if name in self.fail and self.fail[name] in " ".join(cmd):
            open(log, "w").write("отказ\n")
            return 1
        t0ms = fw.t0_ms(D(arg(cmd, "--base-date")))
        out = arg(cmd, "--out")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if name == "f12":
            rows = read_dump(arg(cmd, "--sigsx"))[::-1]                                         # порядок выхода f12 ≠ порядку дампа: сопоставление — по ключу (sym, sig_ms)
            if self.drop_f12:
                rows = rows[:-1]
            with open(out, "w", encoding="utf-8", newline="") as f:
                f.write("sym,sig_ms,side,cons60,turn24h\n" + "".join("%s,%d,long,%s,1000000.0\n" % (s, m, self.val("cons60", s, m, t0ms)) for s, m in rows))
            with open(log, "w", encoding="utf-8") as f:
                f.write("прогон f12\n" + ("" if self.f12_json is False else json.dumps(dict(self.f12_json, rows=len(rows))) + "\n"))
        elif name in ("flow", "all"):
            rows = [line.rstrip("\n").split(",") for line in open(arg(cmd, "--eps"), encoding="utf-8")][1:][::-1]
            if self.drop_flow:
                rows = rows[:-1]
            with open(out, "w", encoding="utf-8", newline="") as f:
                f.write("ep_id,big1800\n" + "".join("%s,%s\n" % (r[0], self.val("big1800", r[1], int(r[3]), t0ms)) for r in rows))
            open(log, "w").write("ok\n")
        else:
            rows = [line.rstrip("\n").split(",") for line in open(arg(cmd, "--eps"), encoding="utf-8")][1:]
            if self.swap_tox:
                rows = rows[::-1]
            with open(out, "w", encoding="utf-8", newline="") as f:
                f.write("sym,sig_ms,vpin1h\n" + "".join("%s,%s,%s\n" % (r[0], r[1], self.val("vpin1h", r[0], int(r[1]), t0ms)) for r in rows))
            open(log, "w").write("ok\n")
        return 0


def expect_keep(cell, sym, rel, runner, t0ms):
    """Независимый пересчёт «проходит» линии: float32 значения → float64, пусто проходит."""
    ok = True
    for feat, op, thr in cell["filter"]:
        s = runner.val(feat, sym, rel, t0ms)
        if s == "":
            continue
        v = float(np.float32(float(s)))
        ok = ok and ((v <= thr) if op == "<=" else (v >= thr))
    return ok


def mk_paths(root, uni="crypto"):
    os.makedirs(root, exist_ok=True)
    return fw.UPaths(root, uni=uni, tick_store="", sig_cache="")


SYMS = ",".join(T.CRYPTO + ["NEWAUSDT"])


def win(x, name="w1"):
    return {w.name: w for w in fw.windows_of(fw.load_freeze(x.e.freeze))}[name]


def build(x, root, runner, cell_id="L1", nan_guard=None, reuse=False, research_keys=None):
    """`build_filter_table` линии `cell_id` в окне w1 (вселенная crypto). → (info, vals, syms, ms, paths, w, need, cell)."""
    w = win(x)
    paths = mk_paths(root)
    eng = rr.Engine(paths, runner, SYMS, x.e.e7, 1, None, ensure_tables=lambda needs: None, windows=(w,), prefix="t")
    cell = f7.cells_of(x.t674)[cell_id]
    need = rr.Engine.stream_need(f7.req_of(cell))
    info, vals, syms, ms = f7.build_filter_table(paths, runner, eng, w, need, cell, x.e.fb, 1, "", 80, (w.lod, w.hid), nan_guard, "flow", reuse, research_keys)
    return info, vals, syms, ms, paths, w, need, cell


def read_ftab(path):
    syms, ms, cols = rr.rfx.read_ftab(path)
    return syms, ms, cols


def expect_guard(fn, needle):
    T.expect_guard(fn, needle)


# ---- разбор ключей и клетки ----------------------------------------------------------------------------------------------------------

def test_parse_filter_filter_of_and_cells_of():
    assert f7.parse_filter("big1800<=0.29&cons60<=1&vpin1h>=0.2") == [("big1800", "<=", 0.29), ("cons60", "<=", 1.0), ("vpin1h", ">=", 0.2)]
    for bad, needle in (("foo<=1", "не из"), ("big1800<0.3", "не из"), ("big1800<=0.3&big1800>=0.1", "повторяется"), ("big1800<=", "не из"), ("big1800<=0.3&", "не из")):
        expect_guard(lambda: f7.parse_filter(bad), needle)
    assert f7.filter_of(P1) == (P1, [], "")
    plain, flt, text = f7.filter_of(LINES[0][2])
    assert plain == P1 and text == "big1800<=0.29&cons60<=1&vpin1h>=0.2" and [f for f, _, _ in flt] == ["big1800", "cons60", "vpin1h"]
    t674 = {"candidates": [{"id": i, "side": s, "ckey": k} for i, s, k in LINES]}
    cells = f7.cells_of(t674)
    assert list(cells) == ["L1", "L2", "S1", "S2"] and [c["side"] for c in cells.values()] == ["L", "L", "S", "S"]
    assert cells["L1"]["keys"] == P1 + " fa=f708_L1 fo=lo fv=0.5" and cells["L1"]["col"] == "f708_L1" and cells["L1"]["plain"] == P1 and cells["L2"]["plain"] == P2
    assert cells["S1"]["keys"] == SK1 and cells["S1"]["col"] == "" and cells["S1"]["filter"] == [] and cells["S2"]["keys"] == SK2
    assert all(c["vid"] == rf.Req(c["side"], c["keys"], False).vid for c in cells.values()) and len({c["vid"] for c in cells.values()}) == 4
    assert f7.cells_of(t674, {"L1": "abc"})["L1"]["keys"] == P1 + " fa=abc fo=lo fv=0.5"
    bad_short = {"candidates": [{"id": "S1", "side": "short", "ckey": SK1 + " F=big1800<=0.3"}]}
    expect_guard(lambda: f7.cells_of(bad_short), "фильтр есть")
    bad_long = {"candidates": [{"id": "L1", "side": "long", "ckey": P1}]}
    expect_guard(lambda: f7.cells_of(bad_long), "фильтр нет")
    same = {"candidates": [{"id": "L1", "side": "long", "ckey": P1 + " F=big1800<=0.3"}, {"id": "L2", "side": "long", "ckey": P1 + " F=vpin1h>=0.2"}]}
    expect_guard(lambda: f7.cells_of(same), "потоки лонг-линий совпадают")
    expect_guard(lambda: f7.cells_of({"candidates": [{"id": "X", "side": "both", "ckey": P1}]}), "сторона")


def test_pass_rule_composite_and_float32():
    n = float("nan")
    v = np.array([0.1, 0.29, 0.3, n, np.inf, -np.inf])
    assert f7.passes(v, "<=", 0.29).tolist() == [True, True, False, True, True, True]                       # граница проходит, пусто (NaN, ±inf) проходит
    assert f7.passes(v, ">=", 0.2).tolist() == [False, True, True, True, True, True]
    vals = {"a": np.array([0.1, 0.5, n, 0.1]), "b": np.array([0.9, 0.9, 0.9, 0.0])}
    assert f7.composite(vals, [("a", "<=", 0.3), ("b", ">=", 0.5)]).tolist() == [True, False, True, False]
    assert f7.composite(vals, [("a", "<=", 0.3)]).tolist() == [True, False, True, True]


# ---- охрана бинарей ----------------------------------------------------------------------------------------------------------------------

def test_check_bins_stops_on_other_bytes_or_missing_file():
    with T.tmp() as t:
        x = ctx5(t)
        spec = x.c5["feature_binaries"]
        got = f7.check_bins(x.e.fb, spec)
        assert set(got) == {"f12", "flow", "tox"} and got["f12"] == (T.sha_of(FB_BYTES["f12"])[1], T.sha_of(FB_BYTES["f12"])[0])
        open(x.e.fb["flow"], "wb").write(b"other")
        expect_guard(lambda: f7.check_bins(x.e.fb, spec), "alpha-feat (flow)")
        expect_guard(lambda: f7.check_bins({"f12": x.e.fb["f12"]}, spec), "--flow-bin")
        expect_guard(lambda: f7.check_bins({n: os.path.join(t, "нет") for n in f7.FEAT_BIN_NAMES}, spec), "нет файла")


# ---- таблица признаков ----------------------------------------------------------------------------------------------------------------------

def test_filter_table_equals_independent_recomputation_with_float32_and_key_mapping():
    with T.tmp() as t:
        x = ctx5(t)
        r0 = Runner5(D("2026-11-30"))
        info0, vals0, syms0, ms0, *_ = build(x, os.path.join(t, "p0"), r0)
        first = (syms0[0], int(ms0[0]))                                                                     # строка дампа с порогом на границе float32: float64 «0,29000000001 > 0,29», float32 — проходит
        r = Runner5(D("2026-11-30"), vals={("big1800",) + first: "0.29000000001", ("cons60",) + first: "0", ("vpin1h",) + first: "0.9"})
        info, vals, syms, ms, paths, w, need, cell = build(x, os.path.join(t, "p1"), r)
        assert list(syms) == list(syms0) and list(ms) == list(ms0) and len(syms) == info["rows"] > 30
        syms_t, ms_t, cols = read_ftab(paths.need_tab(w.name, need))
        assert [(s, int(m)) for s, m in zip(syms_t, ms_t)] == list(zip(syms, (int(m) for m in ms))) and set(cols) == {"f708_L1"}
        t0ms = fw.t0_ms(w.t0d)
        want = [expect_keep(cell, s, int(m), r, t0ms) for s, m in zip(syms, ms)]
        assert [bool(v) for v in cols["f708_L1"]] == want and 0 < sum(want) < len(want)
        assert want[0] is True and np.float64(0.29000000001) > 0.29 and float(np.float32(0.29000000001)) <= 0.29           # float32-значение проходит, float64 бы исключил
        assert vals["big1800"][0] == float(np.float32(0.29000000001)) and vals["vpin1h"][0] == float(np.float32(0.9))
        assert info["excluded"] == len(want) - sum(want) and info["guard_days"] == [] and info["guard_rows"] == 0
        assert info["nan"] == {"big1800": {"all": 0, "credited": 0}, "cons60": {"all": 0, "credited": 0}, "vpin1h": {"all": 0, "credited": 0}}
        assert info["f12"]["locate_fail"] == 0 and info["credited_range"] == [w.lod.isoformat(), w.hid.isoformat()]
        assert len(info["ftab_sha256"]) == 64 and info["credited"] > 0


def test_research_keys_make_rows_outside_the_parquet_read_empty_for_big1800_and_vpin1h_only_in_the_column():
    with T.tmp() as t:
        x = ctx5(t)
        info0, vals0, syms0, ms0, *_ = build(x, os.path.join(t, "p0"), Runner5(D("2026-11-30")))
        keys = [(s, int(m)) for s, m in zip(syms0, ms0)]
        known = set(keys[::2])
        info, vals, syms, ms, paths, w, need, cell = build(x, os.path.join(t, "p1"), Runner5(D("2026-11-30")), research_keys={w_name: known for w_name in ("w1",)})
        _, _, cols = read_ftab(paths.need_tab(w.name, need))
        miss = np.array([k not in known for k in keys])
        want = f7.composite({"cons60": vals0["cons60"], "big1800": np.where(miss, np.nan, vals0["big1800"]), "vpin1h": np.where(miss, np.nan, vals0["vpin1h"])}, cell["filter"])
        assert [bool(v) for v in cols["f708_L1"]] == want.tolist() and info["emulated_absent"] == int(miss.sum()) > 5
        assert want.tolist() != f7.composite(vals0, cell["filter"]).tolist()                                # на этих данных эмуляция меняет столбец
        assert all(np.array_equal(vals[f], vals0[f], equal_nan=True) for f in vals0) and info["nan"] == info0["nan"] and info["guard_days"] == []   # значения и охрана — настоящие
        assert "emulated_absent" not in info0

def test_empty_feature_passes_and_the_nan_guard_excludes_a_whole_day():
    with T.tmp() as t:
        x = ctx5(t)
        info0, vals0, syms0, ms0, paths0, w, need, cell = build(x, os.path.join(t, "p0"), Runner5(D("2026-11-30")))
        off = f7.t0_ms(w.t0d)
        days = {}
        for s, m in zip(syms0, ms0):
            days.setdefault(f7.day_of(int(m) + off), []).append((s, int(m)))
        day = max(days, key=lambda k: len(days[k]))
        assert len(days[day]) >= 3
        nan = {"vpin1h": lambda sym, ams: f7.day_of(ams) == day}                                            # на сутки day нет ни одного значения vpin1h
        r = Runner5(D("2026-11-30"), nan=nan)
        info, vals, syms, ms, paths, w, need, cell = build(x, os.path.join(t, "p1"), r)                      # без охраны: пусто проходит, строки суток остаются в потоке
        _s, _m, cols = read_ftab(paths.need_tab(w.name, need))
        keep = dict(zip(zip(syms, (int(m) for m in ms)), cols["f708_L1"]))
        assert info["guard_days"] == [] and info["nan"]["vpin1h"]["all"] == len(days[day]) and np.isnan(vals["vpin1h"]).sum() == len(days[day])
        assert any(keep[k] == 1.0 for k in days[day])
        assert all(bool(keep[(s, int(m))]) == expect_keep(cell, s, int(m), r, fw.t0_ms(w.t0d)) for s, m in zip(syms, ms))
        guard = {"big1800": 0.5, "cons60": 0.5, "vpin1h": 0.2}                                             # с охраной: доля пустых суток 1,0 > 0,2 → все кандидаты суток 0,0
        info2, vals2, syms2, ms2, paths2, w2, need2, cell2 = build(x, os.path.join(t, "p2"), r, nan_guard=guard)
        _s, _m, cols2 = read_ftab(paths2.need_tab(w2.name, need2))
        keep2 = dict(zip(zip(syms2, (int(m) for m in ms2)), cols2["f708_L1"]))
        assert [(g["day"], g["feat"], g["nan"], g["rows"]) for g in info2["guard_days"]] == [(day.isoformat(), "vpin1h", len(days[day]), len(days[day]))] and info2["guard_rows"] == len(days[day])
        assert all(keep2[k] == 0.0 for k in days[day]) and all(keep2[k] == keep[k] for k in keep if k not in days[day])
        one = {"vpin1h": lambda sym, ams: (sym, ams) == (days[day][0][0], off + days[day][0][1])}            # один пустой кандидат из n ≥ 3: доля 1/n ≤ 0,5 — сутки не помечены
        info3 = build(x, os.path.join(t, "p3"), Runner5(D("2026-11-30"), nan=one), nan_guard={"big1800": 0.5, "cons60": 0.5, "vpin1h": 0.5})[0]
        assert info3["guard_days"] == [] and info3["nan"]["vpin1h"]["all"] == 1


def test_day_stats_and_guard_days_compare_with_the_study_maximum():
    nan = float("nan")
    ms = [0, 1000, 2000, f7.DAY_MS + 5, f7.DAY_MS + 6]
    vals = {"a": np.array([nan, 1.0, nan, nan, 1.0]), "b": np.array([1.0, 1.0, 1.0, nan, nan])}
    st = f7.day_stats(vals, ["a", "b"], ms, 0)
    d0, d1 = datetime.date(1970, 1, 1), datetime.date(1970, 1, 2)
    assert st == {d0: {"rows": 3, "a": 2, "b": 0}, d1: {"rows": 2, "a": 1, "b": 2}}
    got = f7.guard_days(st, ["a", "b"], {"a": 0.6, "b": 0.99})
    assert [(g["day"], g["feat"], g["nan"], g["rows"], g["max"]) for g in got] == [(d0.isoformat(), "a", 2, 3, 0.6), (d1.isoformat(), "b", 2, 2, 0.99)] and got[0]["share"] == 2 / 3
    assert f7.guard_days(st, ["a", "b"], {"a": 2 / 3, "b": 1.0}) == []                                         # доля строго больше максимума; равная — не помечается
    assert [(g["day"], g["feat"]) for g in f7.guard_days(st, ["a", "b"], {"a": 0.5, "b": 1.0})] == [(d0.isoformat(), "a")]
    assert f7.guard_days(st, ["a"], {"a": 0.6, "b": 0.0}) == [{"day": d0.isoformat(), "feat": "a", "nan": 2, "rows": 3, "share": 2 / 3, "max": 0.6}]


def test_feature_tool_guards_stop_the_line_with_the_reason():
    cases = [(dict(f12_json={"locate_fail": 1, "natr_mismatch": 0}), "locate_fail 1"), (dict(f12_json={"locate_fail": 0, "natr_mismatch": 2}), "natr_mismatch 2"), (dict(f12_json=False), "нет итога-JSON"),
             (dict(fail={"f12": "f12"}), "alpha-feat f12"), (dict(drop_f12=True), "ключи (sym, sig_ms)"), (dict(fail={"flow": "flow"}), "alpha-feat flow"), (dict(drop_flow=True), "ep_id выхода"),
             (dict(fail={"tox": "tox"}), "alpha-feat tox"), (dict(swap_tox=True), "строки выхода")]
    for kw, needle in cases:
        with T.tmp() as t:
            x = ctx5(t)
            expect_guard(lambda: build(x, os.path.join(t, "p"), Runner5(D("2026-11-30"), **kw)), needle)


def test_reuse_skips_finished_outputs_and_a_changed_run_recomputes():
    with T.tmp() as t:
        x = ctx5(t)
        r = Runner5(D("2026-11-30"))
        root = os.path.join(t, "p")
        build(x, root, r)
        n = len(r.log) + len(r.tools)
        assert n == 4                                                                                       # дамп + f12 + flow + tox
        build(x, root, r, reuse=True)                                                                      # метки `.ok` всех выходов: ни одного вызова
        assert len(r.log) + len(r.tools) == n
        build(x, root, r)
        assert len(r.log) + len(r.tools) == 2 * n                                                          # без reuse: заново


# ---- четыре линии: прогон движком ------------------------------------------------------------------------------------------------------

def run_cells5(x, root, runner, **kw):
    w = win(x)
    paths = mk_paths(root)
    cells = f7.cells_of(x.t674)
    return f7.run_cells(paths, runner, SYMS, x.e.e7, 1, [w], cells, x.e.fb, "", lambda ww: (ww.lod, ww.hid), nan_guard=NAN_GUARD, **kw) + (paths, cells, w)


def test_run_cells_shorts_without_tables_in_one_call_and_each_long_with_its_own_table():
    with T.tmp() as t:
        x = ctx5(t)
        r = Runner5(D("2026-11-30"))
        tabs, info, paths, cells, w = run_cells5(x, os.path.join(t, "p"), r)
        assert set(tabs) == {"L1", "L2", "S1", "S2"}
        assert {i: v["status"] for i, v in info.items()} == {"L1": "read", "L2": "read", "S1": "read", "S2": "read"}
        assert all("windows" not in info[i] for i in ("S1", "S2")) and all(len(info[i]["windows"]["w1"]["engine"]) == 4 and info[i]["no_key"] == 0 for i in ("L1", "L2"))
        assert all(len(tabs[i]["w1"]) > 0 for i in tabs)
        engine = [(c, e_) for c, e_ in r.log if len(c) > 1 and c[1] == "breakout"]
        sx = [c for c, e_ in engine if e_ and e_.get("P42_SIGSX_ONLY") == "1"]
        runs = [c for c, e_ in engine if not (e_ and e_.get("P42_SIGSX_ONLY") == "1")]
        assert len(sx) == 2 and all("--ftab" not in c for c in sx)                                          # дамп потока — только у лонг-линий
        assert len(runs) == 3 and all(c[0] == x.e.e7 for c in runs)
        shorts = [c for c in runs if "--ftab" not in c]
        assert len(shorts) == 1 and {v.split(" ", 1)[0] for v in open(arg(shorts[0], "--variants"), encoding="utf-8")} == {cells["S1"]["vid"], cells["S2"]["vid"]}
        longs = sorted(arg(c, "--ftab") for c in runs if "--ftab" in c)
        assert len(longs) == 2 and len({os.path.dirname(p) for p in longs}) == 2 and all(os.path.exists(p) for p in longs)
        assert [c[1] for c in r.tools] == ["f12", "flow", "tox"] * 2                                         # три признака на каждую лонг-линию, у шорт-линий нет вызовов


def test_failed_line_is_skipped_and_the_others_are_read():
    with T.tmp() as t:
        x = ctx5(t)
        cells = f7.cells_of(x.t674)
        l1_stream = rr.Engine.stream_need(f7.req_of(cells["L1"])).vid
        r = Runner5(D("2026-11-30"), fail={"tox": l1_stream})
        tabs, info, *_ = run_cells5(x, os.path.join(t, "p"), r)
        assert set(tabs) == {"L2", "S1", "S2"} and info["L1"]["status"] == "skip" and "alpha-feat tox" in info["L1"]["reason"] and info["L2"]["status"] == "read" and info["S1"]["status"] == "read"
    with T.tmp() as t:
        x = ctx5(t)
        tabs, info, *_ = run_cells5(x, os.path.join(t, "p"), Runner5(D("2026-11-30"), extra_cands=3))        # кандидаты движка вне таблицы: no_key > 0 — линии L не читаются, S читаются
        assert set(tabs) == {"S1", "S2"} and all("no_key" in info[i]["reason"] for i in ("L1", "L2")) and all(info[i]["status"] == "skip" for i in ("L1", "L2"))
    with T.tmp() as t:
        x = ctx5(t)
        shorts_fail = Runner5(D("2026-11-30"), fail={})
        orig = shorts_fail.run

        def run(cmd, env=None, log=None):
            if len(cmd) > 1 and cmd[1] == "breakout" and "--ftab" not in cmd and not (env and env.get("P42_SIGSX_ONLY") == "1"):
                return 1                                                                                  # отказ движка на шорт-пакете
            return orig(cmd, env, log)

        shorts_fail.run = run
        tabs, info, *_ = run_cells5(x, os.path.join(t, "p"), shorts_fail)
        assert set(tabs) == {"L1", "L2"} and all(info[i]["status"] == "skip" and "код возврата" in info[i]["reason"] for i in ("S1", "S2")) and info["L1"]["status"] == "read"


# ---- файл звена 6: охраны загрузки ------------------------------------------------------------------------------------------------------

def test_candidate5_loads_and_guards_every_rule():
    with T.tmp() as t:
        x = ctx5(t)
        ok = load5(x)
        assert ok["version"] == "tk708-frozen-1" and list(ok["cells_run"]) == ["L1", "L2", "S1", "S2"] and ok["cells_run"]["L1"]["keys"] == P1 + " fa=f708_L1 fo=lo fv=0.5" and "alpha" not in ok
        fz, c4 = fz_after4(x)
        assert fw.apply_candidate5(fz, ok) is fz and not any(k.startswith(fw.CAND5_PREFIX) for k in fz["alpha"])
        expect_guard(lambda: fw.load_candidate5(x.cp5, fz, x.e.freeze, None, x.cp4, x.t674, x.tp), "цепочка")
        raw = fw.load_freeze(x.e.freeze)                                                                   # заморозка без звена tk691: читаются все вселенные
        expect_guard(lambda: fw.load_candidate5(x.cp5, raw, x.e.freeze, c4, x.cp4, x.t674, x.tp), "считаются на c539")
        taken = json.loads(json.dumps(fz))
        taken["alpha"]["T674_L1"] = 0.001
        expect_guard(lambda: fw.load_candidate5(x.cp5, taken, x.e.freeze, c4, x.cp4, x.t674, x.tp), "уже есть в заморозке")
        expect_guard(lambda: fw.apply_candidate5(taken, ok), "уже есть в заморозке")

    def bad(needle, mutate5=None, mutate_t=None):
        with T.tmp() as t:
            x = ctx5(t, mutate5, mutate_t)
            expect_guard(lambda: load5(x), needle)

    bad("версия кандидата 5", lambda c: c.__setitem__("version", "tk708-frozen-0"))
    bad("записан против другого файла", lambda c: c["base_freeze"].__setitem__("sha256", "0" * 64))
    bad("записан против другого файла", lambda c: c["prev_candidate4"].__setitem__("sha256", "0" * 64))
    bad("записан против другого файла", lambda c: c["tk674"].__setitem__("sha256", "0" * 64))
    bad("бинарь движка линий расходится", lambda c: c["binary"].__setitem__("md5", "0" * 32))
    bad("бинарь движка линий расходится", lambda c: c["binary"].__setitem__("sha256", "0" * 64))
    bad("feature_binaries", lambda c: c["feature_binaries"].pop("tox"))
    bad("feature_binaries", lambda c: c["feature_binaries"]["f12"].__setitem__("md5", "abc"))
    bad("f12_days", lambda c: c.__setitem__("f12_days", 0))
    bad("f12_days", lambda c: c.__setitem__("f12_days", True))
    bad("flow_mode", lambda c: c.__setitem__("flow_mode", "fast"))
    bad("линии", lambda c: c["cells"].pop("S2"))
    bad("поля", lambda c: c["cells"]["L1"].__setitem__("keys", c["cells"]["L1"]["keys"] + " x=1"))
    bad("поля", lambda c: c["cells"]["S1"].__setitem__("vid", "0" * 12))
    bad("alpha", lambda c: c.__setitem__("alpha", {"T674_L1": 0.005}))
    bad("nan_guard", lambda c: c["nan_guard"]["max_daily_share"].pop("L2"))
    bad("nan_guard", lambda c: c["nan_guard"]["max_daily_share"].__setitem__("S1", {}))
    bad("nan_guard", lambda c: c["nan_guard"]["max_daily_share"]["L1"].__setitem__("big1800", 1.5))
    bad("nan_guard", lambda c: c["nan_guard"]["max_daily_share"]["L1"].__setitem__("cons60", True))
    bad("nan_guard", lambda c: c["nan_guard"]["max_daily_share"]["L2"].__setitem__("cons60", 0.1))
    bad("nan_guard", lambda c: c["nan_guard"]["max_daily_share"]["L1"].pop("vpin1h"))
    bad("ворота feat", lambda c: c["gate"]["feat"].__setitem__("ok", False))
    bad("ворота replay", lambda c: c["gate"]["replay"].__setitem__("result_sha256", "abc"))
    bad("ворота feat", lambda c: c.pop("gate"))
    bad("версия", None, lambda t: t.__setitem__("version", "tk674-frozen-2"))


def test_resolve_candidate5_stops_before_the_marker_and_no_tk708_is_the_only_bypass():
    with strict5(), T.tmp() as t:
        x = ctx5(t)
        fz, c4 = fz_after4(x)

        def res(**kw):
            return fw.resolve_candidate5(x.e.args("L1", **kw5(x, **kw)), fz, c4, x.e.root)

        c5, info = res()
        assert info["candidate5_read"] is True and info["candidate5"] == "tk708" and info["candidate5_commit"] == "c0ffee" and info["candidate5_sha256"] == fw.sha256_text(x.cp5)
        assert info["tk674_sha256"] == fw.sha256_text(x.tp) and info["tk674_bin_md5"] == T.sha_of(E7_BYTES)[1] and set(info["feature_bins_md5"]) == {"f12", "flow", "tox"}
        c5n, info_n = res(no_tk708=True, tk674_bin="", f12_bin="")                                             # обход: бинари не нужны, чтения нет
        assert info_n["candidate5_read"] is False and "--no-tk708" in info_n["candidate5_note"] and c5n["version"] == "tk708-frozen-1" and "tk674_bin_md5" not in info_n
        expect_guard(lambda: res(tk674_bin=x.e.fb["f12"]), "бинарь линий TK-674")
        expect_guard(lambda: res(tk674_bin=""), "--tk674-bin")
        expect_guard(lambda: res(f12_bin=x.e.fb["tox"]), "alpha-feat (f12)")
        expect_guard(lambda: res(tox_bin=""), "--tox-bin")
        expect_guard(lambda: res(candidate5_sha256="0" * 64), "sha256")
        expect_guard(lambda: res(tk674_sha256="0" * 64), "sha256")
        expect_guard(lambda: res(candidate5_commit="", candidate5_sha256=""), "нет git")
        os.remove(x.tp)
        expect_guard(lambda: res(), "нет файла отбора")
        os.remove(x.cp5)
        expect_guard(lambda: res(), "нет файла кандидата")
        expect_guard(lambda: res(no_tk708=True), "нет файла кандидата")                                        # файл нужен всегда, `--no-tk708` его не отменяет
        expect_guard(lambda: res(candidate5=os.path.join(t, "нет.json")), "нет файла кандидата")
    with T.tmp() as t:                                                                                         # прежнее поведение (`REQUIRE_CANDIDATE5 = False`): нет файла — звена нет
        x = ctx5(t)
        fz, c4 = fz_after4(x)
        os.remove(x.cp5)
        assert fw.resolve_candidate5(x.e.args("L1", **kw5(x)), fz, c4, x.e.root) == (None, {})


# ---- точка: хук в run_point -------------------------------------------------------------------------------------------------------------

def run5(x, pid, today, last_day, runner=None, **kw):
    return T.run_c(x.e, pid, today, last_day, runner=runner or Runner5(last_day), **kw5(x, **kw))


def test_run_point_reads_four_lines_with_alpha_zero_and_writes_marker_and_info():
    with strict_all5(), T.tmp() as t:
        x = ctx5(t)
        e = x.e
        V.prep4(e, "L1", D("2026-11-30"))
        rc, r = run5(x, "L1", D("2026-12-01"), D("2026-11-30"))
        assert rc == 0
        marker = T.read_json(e.fwd, "READ-L1.started")
        assert marker["candidate5"] == "tk708" and marker["candidate5_read"] is True and marker["candidate5_sha256"] == fw.sha256_text(x.cp5) and marker["tk674_sha256"] == fw.sha256_text(x.tp)
        assert marker["tk674_bin_md5"] == T.sha_of(E7_BYTES)[1] and marker["feature_bins_md5"] == {n: T.sha_of(b)[1] for n, b in FB_BYTES.items()} and marker["candidate4"] == "tk691"
        p1 = T.read_json(e.fwd, "point-L1.json")
        keys = {"T674_%s|crypto" % i for i in ("L1", "L2", "S1", "S2")}
        assert keys <= set(p1["stats"]) and {k.split("|")[0] for k in p1["stats"]} == {"S_base", "S_mk1", "L_base", "L_mk2", "L_n24", "D_S"} | {k.split("|")[0] for k in keys}
        for k in keys:
            s = p1["stats"][k]
            assert s["alpha_k"] == 0.0 and s["decision"] == "описательно" and s["n"] > 0 and s["uni"] == "crypto", (k, s)
        info = T.read_json(e.fwd, "READ-L1.tk708.json")
        assert p1["tk708"] == info and {i: v["status"] for i, v in info["crypto"].items()} == {"L1": "read", "L2": "read", "S1": "read", "S2": "read"}
        assert all(info["crypto"][i]["windows"]["w1"]["guard_days"] == [] and info["crypto"][i]["no_key"] == 0 for i in ("L1", "L2")) and "windows" not in info["crypto"]["S1"]
        assert all(set(s) <= set(T.CRYPTO + ["NEWAUSDT"]) for s in r.symbols) and e.e7 in r.bins and e.bin in r.bins                  # линии идут бинарём TK-674, база — бинарём заморозки
        assert len(r.tools) == 6 and os.path.exists(os.path.join(e.fwd, "READ-L1.done"))
        out = fw.render(p1)
        assert "T674_L1 (линия TK-674, TK-708), вселенная crypto: прочитана" in out and "охрана NaN: суток «нет данных» 0" in out and "T674_S2" in out
        V.prep4(e, "L2", D("2026-12-31"))                                                                      # вторая точка: префикс-охрана линий (сделки точки L1 — префикс точки L2)
        rc2, r2 = run5(x, "L2", D("2027-01-01"), D("2026-12-31"))
        assert rc2 == 0
        p2 = T.read_json(e.fwd, "point-L2.json")
        assert all(p2["prefix"]["diffs"][k] == 0 for k in keys) and all(p2["stats"][k]["alpha_k"] == 0.0 and p2["stats"][k]["decision"] == "описательно" for k in keys)
        assert all(info_["status"] == "read" for info_ in p2["tk708"]["crypto"].values()) and set(p2["tk708"]["crypto"]["L1"]["windows"]) == {"w1", "w2"}


def test_run_point_skips_only_the_failed_line_and_prints_the_reason():
    with strict_all5(), T.tmp() as t:
        x = ctx5(t)
        e = x.e
        V.prep4(e, "L1", D("2026-11-30"))
        l2_stream = rr.Engine.stream_need(f7.req_of(f7.cells_of(x.t674)["L2"])).vid
        rc, r = run5(x, "L1", D("2026-12-01"), D("2026-11-30"), runner=Runner5(D("2026-11-30"), fail={"f12": l2_stream}))
        assert rc == 0
        p1 = T.read_json(e.fwd, "point-L1.json")
        assert "T674_L2|crypto" not in p1["stats"] and {"T674_L1|crypto", "T674_S1|crypto", "T674_S2|crypto", "S_base|crypto", "L_n24|crypto"} <= set(p1["stats"])
        x2 = p1["tk708"]["crypto"]
        assert x2["L2"]["status"] == "skip" and "alpha-feat f12" in x2["L2"]["reason"] and x2["L1"]["status"] == "read"
        out = fw.render(p1)
        assert "T674_L2 (линия TK-674, TK-708), вселенная crypto: НЕ ЧИТАЛАСЬ — " in out and os.path.exists(os.path.join(e.fwd, "READ-L1.done"))


def test_run_point_nan_guard_marks_days_without_data_and_prints_them():
    with strict_all5(), T.tmp() as t:
        x = ctx5(t)
        e = x.e
        V.prep4(e, "L1", D("2026-11-30"))
        day = D("2026-11-18")
        nan = {"big1800": lambda sym, ams: f7.day_of(ams) == day}                                            # на сутки 18.11 признака big1800 нет ни у одного кандидата (обе лонг-линии его читают)
        x.c5["nan_guard"]["max_daily_share"]["L1"]["big1800"] = 0.1
        x.c5["nan_guard"]["max_daily_share"]["L2"]["big1800"] = 0.1
        json.dump(x.c5, open(x.cp5, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        rc, r = run5(x, "L1", D("2026-12-01"), D("2026-11-30"), runner=Runner5(D("2026-11-30"), nan=nan))
        assert rc == 0
        p1 = T.read_json(e.fwd, "point-L1.json")
        for i in ("L1", "L2"):
            wx = p1["tk708"]["crypto"][i]["windows"]["w1"]
            assert [(g["day"], g["feat"]) for g in wx["guard_days"]] == [(day.isoformat(), "big1800")] and wx["guard_rows"] > 0 and wx["nan"]["big1800"]["all"] == wx["guard_rows"]
        out = fw.render(p1)
        assert "охрана NaN: суток «нет данных» 1 (2026-11-18; строк" in out
        rows = json.load(open(fw.tables_path(e.fwd, "L1"), encoding="utf-8"))["rows"]
        for i in ("L1", "L2"):
            assert rows["T674_%s|crypto" % i] and not any(fw.t0_ms(day) <= r_[1] < fw.t0_ms(day) + rf.DAY_MS for r_ in rows["T674_%s|crypto" % i])   # сделок на сутки без данных нет


def test_no_tk708_reads_no_line_and_the_other_cells_are_unchanged():
    with strict_all5(), T.tmp() as t:
        x = ctx5(t)
        e = x.e
        V.prep4(e, "L1", D("2026-11-30"))
        rc, r = run5(x, "L1", D("2026-12-01"), D("2026-11-30"), no_tk708=True, tk674_bin="", f12_bin="", flow_bin="", tox_bin="")
        assert rc == 0
        marker = T.read_json(e.fwd, "READ-L1.started")
        assert marker["candidate5_read"] is False and "--no-tk708" in marker["candidate5_note"] and "tk674_bin_md5" not in marker
        p1 = T.read_json(e.fwd, "point-L1.json")
        assert not any(k.startswith(fw.CAND5_PREFIX) for k in p1["stats"]) and p1["tk708"] == {} and r.tools == [] and e.e7 not in r.bins
        assert not os.path.exists(os.path.join(e.fwd, "READ-L1.tk708.json")) and "--no-tk708" in fw.render(p1)


def test_missing_or_wrong_candidate5_stops_before_the_marker_and_before_the_engine():
    with strict_all5(), T.tmp() as t:
        x = ctx5(t)
        e = x.e
        V.prep4(e, "L1", D("2026-11-30"))

        def go(**kw):
            r = Runner5(D("2026-11-30"))
            try:
                run5(x, "L1", D("2026-12-01"), D("2026-11-30"), runner=r, **kw)
            finally:
                assert not os.path.exists(os.path.join(e.fwd, "READ-L1.started")) and r.calls == 0

        expect_guard(lambda: go(tk674_bin=x.e.fb["tox"]), "бинарь линий TK-674")
        expect_guard(lambda: go(flow_bin=x.e.fb["f12"]), "alpha-feat (flow)")
        expect_guard(lambda: go(tk674_bin=""), "--tk674-bin")
        expect_guard(lambda: go(candidate5_sha256="0" * 64), "sha256")
        os.remove(x.cp5)
        expect_guard(lambda: go(), "нет файла кандидата")
        expect_guard(lambda: go(no_tk708=True), "нет файла кандидата")
    with T.tmp() as t:                                                                                         # без `strict5` (прежние тесты): нет файла — линии не читаются и не мешают
        x = ctx5(t)
        os.remove(x.cp5)
        V.prep4(x.e, "L1", D("2026-11-30"))
        with V.strict_all():
            rc, r = run5(x, "L1", D("2026-12-01"), D("2026-11-30"))
        assert rc == 0 and r.tools == [] and "candidate5" not in T.read_json(x.e.fwd, "READ-L1.started") and "T674_L1|crypto" not in T.read_json(x.e.fwd, "point-L1.json")["stats"]


def test_plan_prints_the_candidate5_line_or_the_stop_warning():
    def plan(x):
        return subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(HERE, "..", "tk616_forward.py"), "plan", "--freeze", x.e.freeze, "--root", x.e.fwd], capture_output=True, text=True,
                              encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))

    with T.tmp() as t:
        x = ctx5(t)
        out = plan(x)
        assert out.returncode == 0 and "tk708-frozen.json (tk708-frozen-1)" in out.stdout and "α нет (описательно)" in out.stdout and "'big1800': 0.5" in out.stdout and x.c5["cells"]["L1"]["vid"] in out.stdout, out.stdout + out.stderr
        os.remove(x.cp5)
        out2 = plan(x)
        assert out2.returncode == 0 and "кандидата tk708-frozen.json рядом с заморозкой нет — чтение точки остановится" in out2.stdout


# ---- ворота replay: sha256 выходов против шлюза TK-674 -------------------------------------------------------------------------------------

def gate_ref(res, mutate=None):
    """`tk674-gate-cells.json` из итога `replay-tk708` (эталон = выходам); `mutate(ref)` — подмена."""
    ref = {"cells": {i: {"windows": {w: {"files": {f: {"sha256": v["sha256"], "equal": True} for f, v in x["files"].items()}} for w, x in c["windows"].items()}} for i, c in res["cells"].items()}}
    if mutate:
        mutate(ref)
    return ref


def test_replay_tk708_compares_sha256_of_the_four_lines_with_the_gate():
    with strict_all5(), T.tmp() as t:
        x = ctx5(t)
        e = x.e
        root = os.path.join(t, "r")
        os.makedirs(root)
        T.write_list(os.path.join(root, "symbols-all.txt"), T.COINS)
        T.mk_tree(os.path.join(root, "win"), "", rf.WINDOWS, D("2026-11-13"), coins=T.COINS)
        ref_path = os.path.join(root, "tk674-gate-cells.json")

        def replay(name, ref, **kw):
            json.dump(ref, open(ref_path, "w", encoding="utf-8"))
            a = argparse.Namespace(**dict(kw5(x), freeze=e.freeze, freeze_commit="c0ffee", freeze_sha256=fw.sha256_text(e.freeze), bin=e.bin, threads=1, tick_store="", sig_cache="",
                                          replay_root=os.path.join(root, name), windows_root=os.path.join(root, "win"), symbols_file=os.path.join(root, "symbols-all.txt"), gate_cells=ref_path, out=""))
            for k, v in kw.items():
                setattr(a, k, v)
            return fw.replay_tk708(a, runner=Runner5(D("2026-11-13")), repo=e.root)

        blank = {"cells": {i: {"windows": {w.name: {"files": {f: {"sha256": "0" * 64} for f in f7.GATE_FILES}} for w in rf.WINDOWS}} for i in ("L1", "L2", "S1", "S2")}}
        assert replay("r0", blank) == 3                                                                      # эталон ≠ выходам: итог записан, код 3
        res = T.read_json(root, "r0", "gate-708c.json")
        assert res["ok"] is False and all(not v["equal"] for v in res["cells"].values()) and all(v["status"] == "read" for v in res["cells"].values()) and res["tk674_bin_md5"] == T.sha_of(E7_BYTES)[1]
        assert os.path.exists(os.path.join(root, "r0", "wa")) and res["candidate5_commit"] == "c0ffee"
        ref = {"cells": {i: {"windows": {w: {"files": {f: {"sha256": v["sha256"]} for f, v in wx["files"].items()}} for w, wx in c["windows"].items()}} for i, c in res["cells"].items()}}
        assert len({v["sha256"] for c in res["cells"].values() for wx in c["windows"].values() for v in wx["files"].values()}) > 8 and set(res["cells"]["L1"]["windows"]) == {"wa", "wb", "wc"}
        assert replay("r1", ref) == 0                                                                        # эталон = выходам (чистый корень): ok, код 0
        res1 = T.read_json(root, "r1", "gate-708c.json")
        assert res1["ok"] is True and all(v["equal"] and v["status"] == "read" for v in res1["cells"].values()) and set(res1["cells"]) == {"L1", "L2", "S1", "S2"}
        assert all(len(wx["files"]) == 4 and wx["equal"] for c in res1["cells"].values() for wx in c["windows"].values())
        assert res1["info"]["L1"]["windows"]["wa"]["guard_days"] == []
        bad = json.loads(json.dumps(ref))
        bad["cells"]["S1"]["windows"]["wb"]["files"]["trades.csv"]["sha256"] = "1" * 64                      # одно различие в одном файле одной линии — не пройдено
        assert replay("r2", bad, out=os.path.join(root, "g2.json")) == 3
        res2 = T.read_json(root, "g2.json")
        assert res2["cells"]["S1"]["equal"] is False and res2["cells"]["S1"]["windows"]["wb"]["equal"] is False and res2["cells"]["S1"]["windows"]["wa"]["equal"] and all(res2["cells"][i]["equal"] for i in ("L1", "L2", "S2"))
        late = os.path.join(root, "win", "wc", "klines", T.COINS[0], "%s-klines-2026-11-14.zip" % T.COINS[0])      # сутки ≥ 14.11 не читаются
        open(late, "w").close()
        expect_guard(lambda: replay("r3", ref), "позже 2026-11-13")
        os.remove(late)
        expect_guard(lambda: replay("r4", ref, gate_cells=os.path.join(root, "нет.json")), "--gate-cells")
        expect_guard(lambda: replay("r5", ref, no_tk708=True), "без --no-tk708")


def test_compare_gate_marks_missing_files_and_other_hashes():
    with T.tmp() as t:
        x = ctx5(t)
        cells = f7.cells_of(x.t674)
        w = win(x)
        paths = mk_paths(os.path.join(t, "p"))
        c = cells["S1"]
        d_ = os.path.join(paths.out(w.name), c["vid"])
        os.makedirs(d_)
        ref = {"cells": {"S1": {"windows": {"w1": {"files": {}}}}}}
        for f in f7.GATE_FILES:
            p = os.path.join(d_, "short-" + f)
            open(p, "wb").write(f.encode())
            ref["cells"]["S1"]["windows"]["w1"]["files"][f] = {"sha256": rr.file_hashes(p)[0]}
        got = f7.compare_gate(paths, {"S1": c}, [w], ref)["S1"]["w1"]
        assert all(v["equal"] for v in got.values()) and set(got) == set(f7.GATE_FILES)
        os.remove(os.path.join(d_, "short-daily.csv"))
        ref["cells"]["S1"]["windows"]["w1"]["files"]["coinday.csv"]["sha256"] = "0" * 64
        got2 = f7.compare_gate(paths, {"S1": c}, [w], ref)["S1"]["w1"]
        assert got2["daily.csv"] == {"sha256": None, "ref_sha256": ref["cells"]["S1"]["windows"]["w1"]["files"]["daily.csv"]["sha256"], "equal": False} and got2["coinday.csv"]["equal"] is False and got2["trades.csv"]["equal"]


def main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        try:
            f()
            print("ok   ", n)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL ", n, "-", type(e).__name__, e)
            tb = traceback.extract_tb(e.__traceback__)
            print("       %s:%d  %s" % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].line))
    print("%d тестов, %d провалов" % (len(tests), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
