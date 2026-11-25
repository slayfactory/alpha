#!/usr/bin/env python3
"""TK-699 шаг 3: дельта таблиц составных фильтров (ftab) на исправленных столбцах f12 — какие смыслы фильтров (`F=…`) клеток c539 изменили единички.

Ничего не пишет в БД и в корни TK-674. Среда — как у стадии `ftab` TK-674 (env-rest.sh: X680, c539), код — /data/p42-tk674c/code-bd (тот же `feat_tabs`, `_pass`,
`parse_sem`, что писали прежние таблицы); таблица признаков: старая `data/<сторона>.parquet`, новая — `data/<сторона>.parquet.tk699-new`.

  tk699_delta.py analyze --scope scope-cells.json --out delta.json [--sides S,L]
      столбцы таблицы кандидатов (c539) по окнам: строк изменено на столбец; для каждого смысла (сторона, sem) из охвата: единичка на старой и новой таблице;
      смысл с `bd_*` — единички bd из xb-таблицы (`/data/p42-tk674c-xb/ftab674xs-all`, `res/ftabcols674x-S.json`) без изменений, остальное — пересчёт;
      контроль: единичка на старой таблице против столбца прежнего файла таблицы (ftab674<суффикс> / xb-таблица партии) — расхождений 0, иначе стоп;
      выход: delta.json (смыслы: changed / строк в окнах), `sems-new.npz` (новые единички смыслов с изменением) и `rows.json` (ключи строк по окнам).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

import numpy as np

CODE = os.environ.get("TK699_CODE674", "/data/p42-tk674c/code-bd")
sys.path.insert(0, CODE)
COLS10 = ["age_h", "approach_dur_min", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h", "depth_min_bp", "depth_mean_bp", "since_touch_prior_h",
          "touch_prior_none"]
# до подмены файлов: старый — data/<сторона>.parquet, новый — .tk699-new; после `tk699_f12tf.py swap`: старый — .parquet.pre-f12, новый — .parquet (TK699_AFTER_SWAP=1)
if os.environ.get("TK699_AFTER_SWAP") == "1":
    OLDP, NEWP = "/data/p42-tk671/data/%s.parquet.pre-f12", "/data/p42-tk671/data/%s.parquet"
else:
    OLDP, NEWP = "/data/p42-tk671/data/%s.parquet", "/data/p42-tk671/data/%s.parquet.tk699-new"
R674 = "/data/p42-tk674c"
XB = "/data/p42-tk674c-xb"


def keys_of(tab):
    return np.array(["%s|%d" % (s, int(m)) for s, m in zip(tab["sym"], tab["ms"])])


def read_csv_cols(path):
    import pandas as pd
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def dir_index(side):
    """{sem: [(каталог таблицы, столбец)]} по файлам res/ftabcols674*-<сторона>.json (прежние таблицы) и xb-таблицам партий троек."""
    idx = {}
    for root in [R674] + sorted(glob.glob("/data/p42-tk680n*")):           # партии TK-674 и единички TK-680 (корни p42-tk680n*, таблица ftab6741)
        for f in sorted(glob.glob(root + "/res/ftabcols674*-%s.json" % side)):
            suf = os.path.basename(f)[len("ftabcols674"):-len("-%s.json" % side)]
            d = "%s/ftab674%s" % (root, suf)
            if not os.path.isdir(d):
                continue
            for sem, name in json.load(open(f, encoding="utf-8")).items():
                idx.setdefault(sem, []).append((d, name, suf if root == R674 else "t680:" + suf))
    return idx


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("analyze")
    p.add_argument("--scope", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--sides", default="S,L")
    a = ap.parse_args()
    analyze(a)


def analyze(a):
    import tk654_run as r54
    import tk674_run as R
    import tk680_axes as a80
    st, r5, _, _ = R.t._srv()
    u = r5.Uni(R.UNI)
    eps = r54.episodes_h775()
    scope = json.load(open(a.scope, encoding="utf-8"))
    wins = [w.name for w in st.TRADE_WINDOWS]
    res = dict(wins=wins, sides={})
    npz = {}
    keys_out = {}
    allb_cols = json.load(open(R674 + "/res/ftabcols674x-S.json", encoding="utf-8"))
    for side in a.sides.split(","):
        sn = R.SIDENAME[side]
        sems = sorted({s["sem"] for s in scope["sems"] if s["side"] == sn})
        if not sems:
            continue
        a80.PARQUET = OLDP
        t_old = R.feat_tabs(side, st, u, eps)
        a80.PARQUET = NEWP
        t_new = R.feat_tabs(side, st, u, eps)
        import tk680_singles as s80
        for tabs_, par_ in ((t_old, OLDP), (t_new, NEWP)):       # столбцы набора единичек TK-680 (age_h, depth_*, since_touch_* ... — их нет в столбцах осей X680)
            a80.PARQUET = par_
            s80.add_columns(side, st, tabs_)
        info = dict(n_sems=len(sems), cols={}, sems={}, control={})
        # строки совпадают
        for w in wins:
            ko, kn = keys_of(t_old[w]), keys_of(t_new[w])
            assert np.array_equal(ko, kn), "строки таблиц старого / нового файла различаются"
            keys_out["%s-%s" % (w, side)] = ko
        # столбцы: сколько строк кандидатов изменили значение
        for c in COLS10:
            if c not in t_old[wins[0]]:
                info["cols"][c] = dict(present=False)
                continue
            d = {}
            for w in wins:
                o, n = np.asarray(t_old[w][c], dtype=np.float64), np.asarray(t_new[w][c], dtype=np.float64)
                same = (o == n) | (np.isnan(o) & np.isnan(n))
                d[w] = [int((~same).sum()), len(o)]
            info["cols"][c] = dict(present=True, rows=d, same_array=all(np.array_equal(np.asarray(t_old[w][c]), np.asarray(t_new[w][c]), equal_nan=True) for w in wins))
        # принадлежность строк (stale)
        import pandas as pd
        pf = pd.read_parquet(NEWP % sn, columns=["win", "sym", "meta_sig_ms", "meta_base_pass", "meta_tk699_stale"])
        pf = pf[pf["meta_base_pass"] > 0]
        stale = {}
        for w in wins:
            key = pd.DataFrame(dict(sym=[str(x) for x in t_new[w]["sym"]], meta_sig_ms=np.asarray(t_new[w]["ms"], dtype=np.int64)))
            m = key.merge(pf[pf["win"].astype(str) == w][["sym", "meta_sig_ms", "meta_tk699_stale"]].drop_duplicates(["sym", "meta_sig_ms"]), on=["sym", "meta_sig_ms"], how="left")
            stale[w] = [int((m["meta_tk699_stale"] == 1).sum()), int(m["meta_tk699_stale"].isna().sum()), len(m)]
        info["stale_rows"] = stale          # [stale, нет строки в таблице TK-671, строк кандидатов]
        # единички смыслов
        idx = dir_index(side)
        allb = {w: read_csv_cols("%s/ftab674xs-all/%s-S.csv" % (XB, w)) for w in wins} if side == "S" else {}
        allb_key = {w: (allb[w]["sym"] + "|" + allb[w]["sig_ms"]).to_numpy() for w in allb}
        order_w, allb_arr = {}, {}
        for w in allb:
            o = pd.Series(np.arange(len(allb_key[w])), index=allb_key[w]).reindex(keys_out["%s-%s" % (w, side)])
            order_w[w] = None if o.isna().any() else o.to_numpy(dtype=np.int64)
            for c_ in allb[w].columns[2:]:
                allb_arr[(w, c_)] = allb[w][c_].to_numpy()
        cache = {}

        def tok_pass(tabs, w, f_, op_, x_, tag):
            k = (tag, w, f_, op_, x_)
            if k not in cache:
                cache[k] = np.asarray(R._pass(f_, x_, tabs[w], op_))
            return cache[k]

        def sem_pass(tabs, w, toks, tag):
            return np.logical_and.reduce([tok_pass(tabs, w, f_, op_, x_, tag) for f_, op_, x_ in toks])

        ctrl_ok = ctrl_n = ctrl_bad = 0
        ctrl_missing = 0
        old_vecs, by_dir, bad_sems = {}, {}, set()
        for sem in sems:
            toks = R.parse_sem(sem)
            miss = [f_ for f_, _, _ in toks if f_ not in t_old[wins[0]] and f_ != "sharp"]
            if miss:
                info["sems"][sem] = dict(missing=miss)
                continue
            bd = [tk for tk in sem.split("&") if tk.startswith("bd_")]
            rest = [tk for tk in toks if not tk[0].startswith("bd_")]
            nd, vecs, vecs_old = {}, {}, {}
            for w in wins:
                if bd:
                    bdv = np.ones(len(keys_out["%s-%s" % (w, side)]), dtype=bool)
                    if order_w[w] is None:
                        info["sems"][sem] = dict(missing=["bd-строки: ключей нет в -all-b"])
                        break
                    for b in bd:
                        bdv &= allb_arr[(w, allb_cols[b])][order_w[w]] == "1.0"
                else:
                    bdv = True
                vo = sem_pass(t_old, w, rest, "o") & bdv
                vn = sem_pass(t_new, w, rest, "n") & bdv
                vecs_old[w], vecs[w] = vo, vn
                nd[w] = int((vo != vn).sum())
            else:
                ch = any(nd.values())
                info["sems"][sem] = dict(changed=ch, ndiff=nd, bd=bool(bd))
                old_vecs[sem] = vecs_old
                for w in wins:
                    npz["%s|%s|%s" % (side, w, sem)] = np.packbits(vecs[w])
                src = None
                for d, name, suf in idx.get(sem, []):
                    if not bd:
                        src = (d, name)
                        break
                    if suf.startswith("3"):
                        xd = "%s/ftab6743%sxb" % (XB, suf[1:])
                        if os.path.isdir(xd):
                            src = (xd, name)
                            break
                if src is None:
                    ctrl_missing += 1
                else:
                    by_dir.setdefault(src[0], []).append((sem, src[1]))
        # контроль: единичка на старой таблице против столбца прежнего файла (bd-смыслы — против xb-таблицы партии); по файлам
        for d, items in sorted(by_dir.items()):
            for w in wins:
                df = read_csv_cols("%s/%s-%s.csv" % (d, w, side))
                kk = (df["sym"] + "|" + df["sig_ms"]).to_numpy()
                pos = pd.Series(np.arange(len(kk)), index=kk).reindex(keys_out["%s-%s" % (w, side)])   # таблицы единичек TK-680 — по всем кандидатам h775: берём строки c539 по ключу
                same_keys = not pos.isna().any()
                pos = pos.to_numpy(dtype=np.int64) if same_keys else None
                for sem, name in items:
                    okw = same_keys and bool(np.array_equal(df[name].to_numpy()[pos] == "1.0", old_vecs[sem][w]))
                    if not okw:
                        bad_sems.add((sem, d, name))
            ctrl_n += len(items)
        ctrl_bad = len(bad_sems)
        ctrl_ok = ctrl_n - ctrl_bad
        bad_examples = sorted(bad_sems)[:5]
        info["control"] = dict(checked=ctrl_n, ok=ctrl_ok, bad=ctrl_bad, no_table=ctrl_missing, bad_examples=bad_examples)
        info["n_changed"] = sum(1 for v in info["sems"].values() if v.get("changed"))
        info["n_missing"] = sum(1 for v in info["sems"].values() if "missing" in v)
        res["sides"][side] = info
        print("== %s: смыслов %d, изменили единичку %d, без столбца в таблице %d; контроль %d / %d (расхождений %d, без таблицы %d); stale %s" %
              (side, len(sems), info["n_changed"], info["n_missing"], ctrl_ok, ctrl_n, ctrl_bad, ctrl_missing, stale), flush=True)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    np.savez_compressed(os.path.splitext(a.out)[0] + "-sems.npz", **npz)
    json.dump({k: v.tolist() for k, v in keys_out.items()}, open(os.path.splitext(a.out)[0] + "-rows.json", "w"))


if __name__ == "__main__":
    main()
