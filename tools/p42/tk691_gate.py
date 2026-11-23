#!/usr/bin/env python3
"""TK-691 / П-42 §13.77 (PS-988): ворота кандидатов вперёд на c539 (в выборке wa / wb / wc, сутки ≤ 13.11, `--symbols` = symbols-crypto.txt, замороженные бинари). Запускать на srv юнитом.
Каталог заморозок `--freeze-dir` (на srv копия `docs/research/freeze/tk626 / tk638 / tk641 / tk670-frozen.json`) — единственный источник ключей, бинарей и охран; код только исполняет.

  python3 tk691_gate.py ds   --freeze-dir … --bin <alpha-mk> --ds-bin <alpha-lx>                          # G-670a(c539): `D_S` побайтно = клетка `lx=1` в БД TK-674 (c539, c64d5ed7) → g670a/gate.json
  python3 tk691_gate.py n24  --freeze-dir … --bin <alpha-mk> --feat-bin <alpha-feat> --prod-bin <alpha-tk683-plain.9387e0a9> --n 250000
                                                                                                         # G-638a(c539): путь точки на c539 против боевого бинаря с той же таблицей → g638a/gate.json
  python3 tk691_gate.py mk2  --gmk-root /data/p42-tk691/gmk                                              # согласованность G-626c(c539): выходы `mk2` = клетка `mk=2` в БД TK-674 → g626c-eq.json
  python3 tk691_gate.py describe --gmk-root … --n24-root … --ds-root … [--out …]                         # описательно рядом: n, Σ $, Sharpe, просадка, перехай, R:R по клеткам c539
G-626c(c539) — без этого файла: `tk626_gmk.py run / report --symbols symbols-crypto.txt` (замороженный бинарь, Холм m = 4).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402
import tk616_forward as fw  # noqa: E402
import tk638_feat as ft  # noqa: E402

SYMS = "/data/p42-tk621/universe/symbols-crypto.txt"
TREES = "/data/p42-tk621"
STORE = "/data/p42-tk599/tick-store"
CELLS674 = "/data/p42-tk674c/cells"
MK2_REF = CELLS674 + "/%s-L/pc8c770ba7b/mk2"               # клетка `mk=2` (c539, бинарь c64d5ed7) в БД TK-674
LX1_REF = CELLS674 + "/%s-S/p75fc245d9a/lx1"               # клетка `lx=1 tf=60` (c539, бинарь c64d5ed7)
PROD_MD5 = "9387e0a9328ff606016ae5451fc7a682"                # боевой бинарь TK-683 (гейт TK-683 PASS, якоря L-334)
FILES_L = ("long-trades.csv", "long-daily.csv", "long-coinday.csv", "long-entryrej.csv")
FILES_MK2 = FILES_L + ("long-maker.json",)
FILES_S = ("short-trades.csv", "short-daily.csv", "short-coinday.csv", "short-entryrej.csv")
GuardError = fw.GuardError


def paths_of(freeze_dir):
    p = {k: os.path.join(freeze_dir, n) for k, n in (("f", "tk626-frozen.json"), ("c1", fw.CAND_NAME), ("c2", fw.CAND2_NAME), ("c3", fw.CAND3_NAME))}
    for n, f in p.items():
        if not os.path.exists(f):
            raise GuardError("нет файла заморозки %s" % f)
    return p


def load_chain(freeze_dir):
    """Цепочка tk626 → tk638 → tk641 → tk670 (охраны C1 — загрузчиками `tk616_forward`; sha256 звеньев сверяются внутри)."""
    p = paths_of(freeze_dir)
    fz = fw.load_freeze(p["f"])
    c1 = fw.load_candidate(p["c1"], fz, p["f"])
    c2 = fw.load_candidate2(p["c2"], fz, p["f"], c1, p["c1"])
    c3 = fw.load_candidate3(p["c3"], fz, p["f"], c1, p["c1"], c2, p["c2"])
    return p, fz, c1, c2, c3


def link_trees(root, wins):
    os.makedirs(root, exist_ok=True)
    for w in wins:
        link, src = os.path.join(root, w.name), os.path.join(TREES, w.name)
        if not os.path.lexists(link):
            os.symlink(src, link)
        elif os.path.realpath(link) != os.path.realpath(src):
            raise GuardError("%s указывает не на %s" % (link, src))


def eq_files(files, x_dir, y_dir):
    out = {}
    for f in files:
        x, y = os.path.join(x_dir, f), os.path.join(y_dir, f)
        sx, sy = (fw.sha256_stream(x) if os.path.exists(x) else None), (fw.sha256_stream(y) if os.path.exists(y) else None)
        out[f] = {"sha256": sx, "ref_sha256": sy, "equal": sx is not None and sx == sy}
    return out


def dump(res, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(res, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)


# ---- G-670a(c539): `D_S` -------------------------------------------------------------------------------------------------------------------

def cmd_ds(a):
    """Замороженный ds-бинарь на c539 ТЕМ ЖЕ путём, что точка (`tk616_forward replay-ds`); эталон — клетка `lx=1` TK-674 (подкаталоги `<окно>/S_lx1` — ссылки)."""
    p, fz, c1, c2, c3 = load_chain(a.freeze_dir)
    ref = os.path.join(a.root, "ref")
    for w in rf.WINDOWS:
        d_ = os.path.join(ref, w.name)
        os.makedirs(d_, exist_ok=True)
        link = os.path.join(d_, fw.DS_REF_SUB)
        if not os.path.lexists(link):
            os.symlink(LX1_REF % w.name, link)
    out = os.path.join(a.root, "gate.json")
    argv = ["replay-ds", "--freeze", p["f"], "--freeze-commit", "tk691-gate", "--freeze-sha256", fw.sha256_text(p["f"]),
            "--candidate", p["c1"], "--candidate-commit", "tk691-gate", "--candidate-sha256", fw.sha256_text(p["c1"]),
            "--candidate2", p["c2"], "--candidate2-commit", "tk691-gate", "--candidate2-sha256", fw.sha256_text(p["c2"]),
            "--candidate3", p["c3"], "--candidate3-commit", "tk691-gate", "--candidate3-sha256", fw.sha256_text(p["c3"]),
            "--bin", a.bin, "--ds-bin", a.ds_bin, "--ref-dir", ref, "--replay-root", os.path.join(a.root, "run"), "--windows-root", TREES, "--symbols-file", SYMS,
            "--tick-store", STORE, "--threads", str(a.threads), "--out", out]
    code = fw.main(argv)
    res = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else {"ok": False}
    res["gate"] = "G-670a(c539)"
    res["ref"] = "клетка `lx=1 tf=60` в БД TK-674 (c539, бинарь c64d5ed7): " + LX1_REF
    dump(res, out)
    return code


# ---- G-638a(c539): `L_n24` -----------------------------------------------------------------------------------------------------------------

def read_counts_rows(path):
    syms, ms, n24, m24 = fw.read_counts(path)
    return {(s, int(m)): (int(n), bool(x)) for s, m, n, x in zip(syms, ms, n24, m24)}


def cmd_n24(a):
    """Путь точки (`run_n24`: дамп → tcount → таблица → замороженный бинарь) на c539 при N из аргумента; эталон — тот же вариант с той же таблицей на боевом бинаре TK-683."""
    p, fz, c1, c2, c3 = load_chain(a.freeze_dir)
    cand = copy.deepcopy(c1)
    cand["filter"]["N"] = float(a.n)                                       # ТОЛЬКО порог (правило tk638 на c539, `tk691_n24.py`); имя фильтра, ключи и vid клетки — замороженные
    md5, _ = fw.check_binary(a.bin, fz)
    fmd5, _ = fw.check_feat_binary(a.feat_bin, cand)
    pmd5 = rr.file_hashes(a.prod_bin)[1]
    if pmd5 != PROD_MD5:
        raise GuardError("боевой бинарь %s: md5 %s ≠ %s" % (a.prod_bin, pmd5, PROD_MD5))
    wins = rf.WINDOWS
    root_f, root_p = os.path.join(a.root, "frozen"), os.path.join(a.root, "prod")
    for r in (root_f, root_p):
        link_trees(r, wins)
    ntree = fw.check_tree_days(root_f, wins, fw.d(fw.LISTING_AFTER))
    text = rr.read_symbols(SYMS)[0]
    rn = rr.SubprocessRunner()
    pf = fw.UPaths(root_f, uni="replay", tick_store=STORE)
    tabs, info = fw.run_n24(pf, rn, text, a.bin, a.threads, wins, cand, a.feat_bin, STORE, lambda w: (w.lod, w.hid))
    res = {"gate": "G-638a(c539)", "N": a.n, "bin_md5": md5, "feat_bin_md5": fmd5, "prod_bin_md5": pmd5, "keys": cand["cells"][fw.CAND_CELL]["keys"], "tree_files": ntree, "n24": info, "windows": {}, "ok": False}
    out = os.path.join(a.root, "gate.json")
    if tabs is None:
        dump(res, out)
        print("G-638a(c539): клетка не прочитана: %s" % info["reason"])
        return 3
    req = fw.n24_req(cand)
    need = rr.Engine.stream_need(req)
    pp = fw.UPaths(root_p, uni="replay", tick_store=STORE)
    for w in wins:                                                          # та же таблица (байт в байт) на боевом бинаре
        dst = pp.need_dir(w.name, need)
        os.makedirs(dst, exist_ok=True)
        shutil.copyfile(os.path.join(pf.need_dir(w.name, need), "ftab.csv"), os.path.join(dst, "ftab.csv"))
    rr.Engine(pp, rn, text, a.prod_bin, a.threads, None, ensure_tables=lambda needs: None, windows=tuple(wins), prefix="n").launch([req])
    tk638 = set(l.strip() for l in open(SYMS, encoding="utf-8").read().replace("\n", ",").split(",") if l.strip())
    for w in wins:
        files = eq_files(FILES_L, os.path.join(pf.out(w.name), req.vid), os.path.join(pp.out(w.name), req.vid))
        mine = read_counts_rows(os.path.join(pf.need_dir(w.name, need), "counts.csv"))
        sym, c = ft.read_counts(ft.feat_path("/data/p42-tk638/feat", "h775", w.name, "L"))
        theirs = {(s, int(m)): (int(n), bool(x)) for s, m, n, x in zip(sym, c["sig_ms"], c["n24"], c["m24"]) if s in tk638}
        same_rows = mine == theirs
        res["windows"][w.name] = {"files": files, "tcount_rows": len(mine), "tk638_rows_c539": len(theirs), "tcount_equal_tk638": same_rows,
                                  "equal": all(v["equal"] for v in files.values()) and same_rows}
    nk = info["no_key"]
    res["guards"] = {"no_key": nk, "m24_share": info["m24_share"], "engine": {w: info["windows"][w]["engine"] for w in info["windows"]}}
    g8 = nk == 0 and all(x["engine"] and x["engine"]["cands"] == info["windows"][w]["rows"] and x["engine"]["dropped"] == info["windows"][w]["excluded"] for w, x in info["windows"].items())
    res["guards"]["g8_cands_dropped"] = bool(g8)
    res["ok"] = bool(g8 and all(v["equal"] for v in res["windows"].values()) and info["m24_share"] <= fw.M24_MAX_SHARE)
    dump(res, out)
    print("G-638a(c539): %s (%s); no_key %s; m24 %.3f %%; %s" % ("ПРОЙДЕНЫ" if res["ok"] else "НЕ ПРОЙДЕНЫ", ", ".join("%s %s" % (n, "=" if v["equal"] else "≠") for n, v in res["windows"].items()),
                                                                nk, 100 * info["m24_share"], out))
    return 0 if res["ok"] else 3


# ---- согласованность G-626c(c539): `mk2` ---------------------------------------------------------------------------------------------------

def cmd_mk2(a):
    p, fz, c1, c2, c3 = load_chain(a.freeze_dir)
    cell = c2["cells"][fw.CAND2_CELL]
    req = rf.Req("L", cell["keys"], False)
    res = {"gate": "G-626c(c539) согласованность", "vid": req.vid, "windows": {}}
    for w in rf.WINDOWS:
        files = eq_files(FILES_MK2, os.path.join(a.gmk_root, "out", w.name, req.vid), MK2_REF % w.name)
        res["windows"][w.name] = {"files": files, "equal": all(v["equal"] for v in files.values())}
    res["ok"] = all(v["equal"] for v in res["windows"].values())
    out = os.path.join(a.gmk_root, "g626c-eq.json")
    dump(res, out)
    print("G-626c(c539), согласованность mk2 с БД TK-674: %s (%s); %s" % ("РАВНО" if res["ok"] else "РАСХОДИТСЯ", ", ".join("%s %s" % (n, "=" if v["equal"] else "≠") for n, v in res["windows"].items()), out))
    return 0 if res["ok"] else 3


# ---- описательно рядом ---------------------------------------------------------------------------------------------------------------------

def cell_row(u, side, dirs, st, p8, t43, s674):
    ser, kinds, pnl = s674.load(u, side, dirs)
    m = s674.metrics(ser, kinds, pnl)
    m.pop("daily", None)
    m.pop("dayn", None)
    return m


def cmd_describe(a):
    import tk604_stats as st
    import tk625_run as r5
    import tk638_post as p8
    import tk643_run as t43
    import tk674_stats as s674
    p, fz, c1, c2, c3 = load_chain(a.freeze_dir)
    u = r5.Uni("c539")
    vids = {"L_base": fz["cells"]["L_base"]["vid"], "S_base": fz["cells"]["S_base"]["vid"], "S_mk1": fz["cells"]["S_mk1"]["vid"], "L_mk2": c2["cells"][fw.CAND2_CELL]["vid"]}
    srcs = {k: ({w.name: (os.path.join(a.gmk_root, "out", w.name, v), "L" if k.startswith("L") else "S") for w in st.TRADE_WINDOWS}) for k, v in vids.items()}
    n24v = fw.n24_req(c1).vid
    srcs["L_n24"] = {w.name: (os.path.join(a.n24_root, "frozen", "out", "replay", w.name, n24v), "L") for w in st.TRADE_WINDOWS}
    dsv = rf.Req("S", c3["cells"][fw.CAND3_CELL]["keys"], False).vid
    srcs["D_S"] = {w.name: (os.path.join(a.ds_root, "run", "out", "replay", w.name, dsv), "S") for w in st.TRADE_WINDOWS}
    res = {}
    for k, dirs in srcs.items():
        side = "L" if k.startswith("L") else "S"
        try:
            res[k] = cell_row(u, side, dirs, st, p8, t43, s674)
        except (SystemExit, OSError, ValueError, KeyError) as e:
            res[k] = {"error": "%s: %s" % (type(e).__name__, e)}
    if a.out:
        dump(res, a.out)
    for k, m in res.items():
        if "error" in m:
            print("%-7s ОШИБКА %s" % (k, m["error"]))
            continue
        kp = m["kpi"]
        print("%-7s n %4d  Σ %8.2f $  %6.3f $/сд  Sharpe %5.2f  просадка %6.1f $ (%4.1f %% маржи)  перехай %3d / %5.1f / %3d сут  R:R %.2f  стопов %d" % (
            k, m["n"], m["usd"], m["mean_usd"], m["sharpe"], m["maxdd"], m["maxdd_pct"], kp["gap_longest"], kp["gap_mean"], kp["gap_tail"], m["rr"]["rr"], m["rr"]["stops"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["ds", "n24", "mk2", "describe"])
    ap.add_argument("--freeze-dir", default="/data/p42-tk691/freeze")
    ap.add_argument("--root", default="")
    ap.add_argument("--bin", default="/data/p42-tk616/alpha-mk.396989e9")
    ap.add_argument("--ds-bin", default="/data/p42-tk670/bin/alpha-lx.2815279d")
    ap.add_argument("--feat-bin", default="/data/p42-tk638/bin/alpha-feat.69fcf7cd")
    ap.add_argument("--prod-bin", default="/data/p42-tk683/bin/alpha-tk683-plain.9387e0a9")
    ap.add_argument("--n", type=float, default=250000.0, help="порог N24 лонга на c539 (`tk691_n24.py`)")
    ap.add_argument("--threads", type=int, default=rr.THREADS)
    ap.add_argument("--gmk-root", default="/data/p42-tk691/gmk")
    ap.add_argument("--n24-root", default="/data/p42-tk691/g638a")
    ap.add_argument("--ds-root", default="/data/p42-tk691/g670a")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    a.root = a.root or {"ds": "/data/p42-tk691/g670a", "n24": "/data/p42-tk691/g638a"}.get(a.cmd, "")
    try:
        return {"ds": cmd_ds, "n24": cmd_n24, "mk2": cmd_mk2, "describe": cmd_describe}[a.cmd](a)
    except GuardError as e:
        print("ОСТАНОВКА (охрана): %s" % e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
