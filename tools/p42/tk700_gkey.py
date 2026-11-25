#!/usr/bin/env python3
"""TK-700 (класс B, дыра G-key): столбцы TK-654 (mv5, tpm60, pierce_ep, spread_tau, cons60, zv6h, n24, m24) на ВСЕХ кандидатах дампа базы окна (`L_base` / `S_base`), как в БД Jul–Nov (В-172, точная модель).

  python3 tk700_gkey.py build --win m01 [--root /data/p42-tk700/gkey] [--procs 2] [--no-flow]   # TK-634 (f12 v6c + flow) и TK-638 (tcount) на дампах <root>/<окно>/dumps/{L_base,S_base} (`tk700_sig.sh <окно> base <root>/<окно>`)
  python3 tk700_gkey.py table --win m01 [--root …]                                  # <root>/<окно>/tab-{long,short}.parquet: sym, sig_ms, mv5, tpm60, pierce_ep, spread_tau, cons60, zv6h, n24, m24
  python3 tk700_gkey.py gate  --win wa [--root …]                                   # (без TK700_WINSET) сверка с прежними таблицами TK-634 / TK-638 / TK-654 по кандидатам вселенной дампа: равны байт в байт или список

Сборка — прежний код TK-634 / TK-638 (`tk634_feat.cmd_build`, `tk638_feat.cmd_build`) с подменой корней на `<root>`: прежние таблицы /data/p42-tk634, /data/p42-tk638 и базы TK-625 не трогаются.
Бинарь f12 / flow — тот же v6c (`992cde46`), что считал Jul–Nov: дамп базы без источника уровня (`tf_min` не задан) — исправление TK-699 на него не действует. На H1 окна m01…m06 (`TK700_WINSET=h1`), на воротах wa / wb / wc — без переменной."""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = "/data/p42-tk700/gkey"
SIDE = {"long": "L", "short": "S"}
COLS = ["mv5", "tpm60", "pierce_ep", "spread_tau", "cons60", "zv6h", "n24", "m24"]


def patch_roots(root, win):
    """Корни TK-625 / TK-634 / TK-638 → <root>; базы окна — ссылка на <root>/<окно>/dumps."""
    import tk625_run as r5
    import tk634_feat as f34
    import tk638_feat as f38
    r5.ROOT = os.path.join(root, "r625")
    f34.ROOT = os.path.join(root, "tk634")
    f38.ROOT = os.path.join(root, "tk638")
    f38.FEAT34 = os.path.join(f34.ROOT, "feat")
    link = os.path.join(r5.ROOT, "h775", "base", win)
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if not os.path.islink(link):
        os.symlink(os.path.join(root, win, "dumps"), link)
    return r5, f34, f38


def skip_flow(f34):
    """Столбцы flow (tfv*, vpin …) классу B не нужны (mv5 / tpm60 — из эпизодов f12): шаг `flow` заменён пустой таблицей — в csv TK-634 эти 16 столбцов пусты."""
    import numpy as np
    import pandas as pd
    import retitr_ftab as rfx

    def stub(prod, eps_path, flow_path, mode, procs, base_date):
        eps = pd.read_parquet(eps_path, columns=["ep_id"])
        d = pd.DataFrame({c: np.full(len(eps), np.nan) for c in f34.FLOW_COLS})
        d.insert(0, "ep_id", eps["ep_id"].to_numpy())
        d.to_parquet(flow_path)
    rfx.run_tk594 = stub


def cmd_build(a):
    r5, f34, f38 = patch_roots(a.root, a.win)
    if a.no_flow:
        skip_flow(f34)
    for s in ("L", "S"):
        ns = argparse.Namespace(uni="h775", window=a.win, side=s, procs=a.procs, force=False)
        f34.cmd_build(ns)
        f38.cmd_build(ns)
    return 0


def read_tables(root, win, side_name):
    """Таблица кандидатов окна и стороны: ключи и столбцы TK-654 из собранных файлов (как `tk654_run.load_window` + n24 / m24 TK-638)."""
    import numpy as np
    import pandas as pd
    import retitr_ftab as rfx
    import tk638_feat as f38
    s = SIDE[side_name]
    f34 = os.path.join(root, "tk634", "feat", "h775")
    sym, ms, raw = rfx.read_ftab(os.path.join(f34, "%s-%s.csv" % (win, s)))
    eps = pd.read_parquet(os.path.join(f34, "%s-%s" % (win, s), "eps-%s.parquet" % s), columns=["sym", "sig_ms", "pierce_ep", "spread_tau", "cons60", "zv6h", "mv5"])
    if eps.duplicated(["sym", "sig_ms"]).any():
        raise SystemExit("%s-%s: дубль (sym, sig_ms) в эпизодах f12" % (win, s))
    m = pd.DataFrame(dict(sym=list(sym), sig_ms=ms)).merge(eps, on=["sym", "sig_ms"], how="left", validate="one_to_one")
    a_, b_ = raw["mv5"], m["mv5"].to_numpy(np.float64)
    bad = int((~((a_ == b_) | (np.isnan(a_) & np.isnan(b_)))).sum())
    if bad:
        raise SystemExit("G-key %s-%s: mv5 эпизодов ≠ таблице TK-634 в %d строках" % (win, s, bad))
    csym, c = f38.read_counts(f38.feat_path(os.path.join(root, "tk638", "feat"), "h775", win, s))
    if list(csym) != list(sym) or not np.array_equal(c["sig_ms"], ms):
        raise SystemExit("G-key %s-%s: строки TK-638 и TK-634 различаются" % (win, s))
    out = pd.DataFrame(dict(sym=list(sym), sig_ms=ms, mv5=raw["mv5"], tpm60=raw["tpm60"], pierce_ep=m["pierce_ep"].to_numpy(np.float64), spread_tau=m["spread_tau"].to_numpy(np.float64),
                            cons60=m["cons60"].to_numpy(np.float64), zv6h=m["zv6h"].to_numpy(np.float64), n24=c["n24"].astype(np.float64), m24=c["m24"].astype(np.float64)))
    return out


def cmd_table(a):
    patch_roots(a.root, a.win)
    for side in SIDE:
        t = read_tables(a.root, a.win, side)
        p = os.path.join(a.root, a.win, "tab-%s.parquet" % side)
        t.to_parquet(p)
        print("%s %s: кандидатов %d, пустых по столбцам: %s" % (a.win, side, len(t), {c: int(t[c].isna().sum()) for c in COLS if t[c].isna().any()}))
    return 0


def cmd_gate(a):
    """Мои таблицы (дамп c539) против прежних (`/data/p42-tk634|638/feat/h775`): строки по монетам моей вселенной, значения равны (`nan` на тех же местах)."""
    import numpy as np
    mine = {}
    patch_roots(a.root, a.win)
    rc = 0
    for side in SIDE:
        t = read_tables(a.root, a.win, side)
        s = SIDE[side]
        import retitr_ftab as rfx
        import tk638_feat as f38
        import pandas as pd
        sym, ms, raw = rfx.read_ftab(os.path.join("/data/p42-tk634/feat/h775", "%s-%s.csv" % (a.win, s)))
        eps = pd.read_parquet(os.path.join("/data/p42-tk634/feat/h775", "%s-%s" % (a.win, s), "eps-%s.parquet" % s), columns=["sym", "sig_ms", "pierce_ep", "spread_tau", "cons60", "zv6h"])
        old = pd.DataFrame(dict(sym=list(sym), sig_ms=ms, mv5=raw["mv5"], tpm60=raw["tpm60"])).merge(eps, on=["sym", "sig_ms"], how="left", validate="one_to_one")
        csym, c = f38.read_counts(f38.feat_path("/data/p42-tk638/feat", "h775", a.win, s))
        old["n24"], old["m24"] = c["n24"].astype(np.float64), c["m24"].astype(np.float64)
        old = old[old["sym"].isin(set(t["sym"]))].sort_values(["sym", "sig_ms"], kind="stable").reset_index(drop=True)
        new = t.sort_values(["sym", "sig_ms"], kind="stable").reset_index(drop=True)
        res = dict(rows_mine=int(len(new)), rows_old=int(len(old)), keys_equal=bool(len(new) == len(old) and (new["sym"].to_numpy() == old["sym"].to_numpy()).all() and (new["sig_ms"].to_numpy() == old["sig_ms"].to_numpy()).all()), bad={})
        if res["keys_equal"]:
            for col in COLS:
                x, y = new[col].to_numpy(np.float64), old[col].to_numpy(np.float64)
                nb = int(((x != y) & ~(np.isnan(x) & np.isnan(y))).sum())
                if nb:
                    res["bad"][col] = nb
        print("%s %s: строк мои %d / прежние %d, ключи равны %s, столбцы с расхождением: %s → %s" % (a.win, side, res["rows_mine"], res["rows_old"], res["keys_equal"], res["bad"] or "нет",
                                                                                              "PASS" if res["keys_equal"] and not res["bad"] else "FAIL"))
        mine[side] = res
        rc |= 0 if res["keys_equal"] and not res["bad"] else 3
    json.dump(mine, open(os.path.join(a.root, a.win, "gate.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "table", "gate"])
    ap.add_argument("--win", required=True)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--no-flow", action="store_true", help="без шага flow (классу B не нужен): ускорение вдвое; 16 столбцов flow в csv TK-634 пусты")
    a = ap.parse_args(argv)
    return {"build": cmd_build, "table": cmd_table, "gate": cmd_gate}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
