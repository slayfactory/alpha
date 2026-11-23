#!/usr/bin/env python3
"""TK-674 PS-995 (§13.73.33): таблица `ftab674xb` = `ftab674xs` с пересчитанными `bd_*`-столбцами по всем строкам wa / wb / wc.

  python3 tk674_xb.py need     — какие сутки архива bookDepth нужны строкам (как читает `tk671_bd.compute`), чего нет на диске → res/xb-need.json
  python3 tk674_xb.py dl       — докачка недостающих (монета, сутки) с data.binance.vision: 404 = «биржа файла не даёт» (записывается), любая другая ошибка — повтор, не NaN
  python3 tk674_xb.py check    — каждая нужная пара: файл есть или 404 в охвате; печать числа 404 и монет без архива по окнам; иначе стоп (код 5)
  python3 tk674_xb.py fam      — `fam/bd/<окно>-short.parquet` теми же функциями, что TK-671 / TK-690 (`tk671_bd.compute`), нижняя граница ω — первые сутки строк (01.07)
  python3 tk674_xb.py build    — `ftab674xb`: столбцы без `bd_*` — байты `ftab674xs`; столбец с `bd_*` = остаток (столбец xs без `bd_*`-условий) И столбцы единичек `bd_*` новой таблицы (-all-b)
  python3 tk674_xb.py controls — (1) столбцы без `bd_*` равны xs; (2) признаки / столбцы против прежнего счёта TK-690 там, где оба определены; охрана NaN

Среда: TK674XB_ROOT (по умолчанию /data/p42-tk674c-xb); python — /data/tk592/venv/bin/python (как `tk671_pyfam.sh`); скрипты TK-671 — /data/p42-tk671/code/p (боевая копия, как у TK-690).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import glob
import json
import os
import shutil
import sys
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ROOT = os.environ.get("TK674XB_ROOT", "/data/p42-tk674c-xb")
PROD = os.environ.get("TK674XB_PROD", "/data/p42-tk690/prod")
BOOK = os.environ.get("TK674XB_BOOK", "/data/p42-book")
ZIP = BOOK + "/zip"
C671 = "/data/p42-tk671/code/p"
WINS = ("wa", "wb", "wc")
SN = "short"
EPOCH = dt.date(1970, 1, 1)
OMEGA_LO = dt.date(2026, 7, 1)                  # первые сутки строк wa (t0 окна 17.06 — предыстория, строк там нет)
MIN_FREE_GB = 12.0                              # охрана диска: ниже — докачка встаёт, ничего не удаляется
say = lambda *a: print(*a, flush=True)


class XbError(SystemExit):
    pass


def q(*a):
    p = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def day_str(d):
    return (EPOCH + dt.timedelta(int(d))).isoformat()


def omega_lo_d():
    return (OMEGA_LO - EPOCH).days


def rows_path(w):
    return os.path.join(PROD, "lvl-%s-%s" % (w, SN), "rows.parquet")


# ---- какие сутки нужны ------------------------------------------------------------------------------------------------------------
def need_days(s, lo_d, hi_d, holes_d):
    """{(индекс строки): (сутки σ, сутки σ − 1 ч)} для строк в ω (как `tk671_bd.omega_mask` + `_coin_job`); возвращает маску и два массива суток."""
    d1 = np.floor(s / 86400.0).astype(np.int64)
    d2 = np.floor((s - 3600.0) / 86400.0).astype(np.int64)
    ok = (d1 >= lo_d) & (d1 <= hi_d)
    for h in holes_d:
        ok &= d1 != h
    return ok, d1, d2


def need_pairs(sym, s, lo_d, hi_d, holes_d):
    """Множество (монета, сутки ISO), которые `tk671_bd.compute` читает для строк (sym[i], σ = s[i])."""
    ok, d1, d2 = need_days(np.asarray(s, dtype=np.float64), lo_d, hi_d, holes_d)
    out = set()
    for sy, a, b in zip(np.asarray(sym)[ok], d1[ok], d2[ok]):
        out.add((str(sy), day_str(a)))
        out.add((str(sy), day_str(b)))
    return out


def has_snapshot_in_hour(ts, sigma):
    """В отсортированных секундах снимков `ts` есть хотя бы один в [σ − 3600, σ] (иначе признаки bd у строки не определены — у биржи нет данных за час до сигнала)."""
    ts = np.asarray(ts, dtype=np.float64)
    return bool(((ts >= sigma - 3600.0) & (ts <= sigma)).any())


def have_pairs(zdir):
    """Пары (монета, сутки), у которых файл есть и не пуст (чтение каталогов, не обход дерева)."""
    out = set()
    if not os.path.isdir(zdir):
        return out
    for sy in os.listdir(zdir):
        d = os.path.join(zdir, sy)
        if not os.path.isdir(d):
            continue
        for e in os.scandir(d):
            if e.name.endswith(".zip") and "-bookDepth-" in e.name and e.stat().st_size > 0:
                out.add((sy, e.name.split("-bookDepth-")[1][:10]))
    return out


def coverage_status(paths):
    """{(монета, сутки): статус} из файлов охвата (sym,day,status,bytes); у пары берётся последняя строка, «ok» не затирается ошибкой."""
    st = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                k = (r["sym"], r["day"])
                if st.get(k) == "ok" and r["status"] != "ok":
                    continue
                st[k] = r["status"]
    return st


def load_need():
    sys.path.insert(0, C671)
    import tk671_bd as bd
    import pandas as pd
    lo_d = omega_lo_d()
    per = {}
    for w in WINS:
        rows = pd.read_parquet(rows_path(w))
        s = bd.row_seconds(rows, w)
        per[w] = need_pairs(rows["sym"].astype(str).to_numpy(), s, lo_d, bd.OMEGA_HI_D, bd.HOLES_D)
    return per


def cmd_need(a):
    per = load_need()
    have = have_pairs(ZIP)
    allp = set().union(*per.values())
    miss = sorted(p for p in allp if p not in have)
    out = dict(omega_lo=OMEGA_LO.isoformat(), need=len(allp), missing=len(miss), per_window={w: dict(need=len(p), missing=len([x for x in p if x not in have])) for w, p in per.items()},
               files_on_disk=len(have), missing_pairs=[list(x) for x in miss])
    json.dump(out, open(q("res", "xb-need.json"), "w", encoding="utf-8"))
    say("нужно пар %d, файлов на диске %d, недостаёт %d; по окнам %s" % (len(allp), len(have), len(miss), json.dumps(out["per_window"])))


# ---- докачка ----------------------------------------------------------------------------------------------------------------------
def cmd_dl(a):
    import bookdepth_dl as dl
    per = load_need()
    have = have_pairs(ZIP)
    todo = sorted(p for p in set().union(*per.values()) if p not in have)
    cov = q("res", "xb-coverage.csv")
    prev = coverage_status([cov]) if os.path.exists(cov) else {}
    todo = [p for p in todo if prev.get(p) != "404"]                    # 404 уже записан — не дёргать биржу заново
    say("к докачке %d пар (из них 404 уже записаны: %d)" % (len(todo), len([1 for p in prev.values() if p == "404"])))
    lock, stop = threading.Lock(), threading.Event()
    n = dict(ok=0, n404=0, err=0)
    new = not os.path.exists(cov)
    with open(cov, "a", encoding="utf-8") as fh:
        if new:
            fh.write("sym,day,status,bytes\n")
        for rnd in range(a.rounds):
            if not todo:
                break

            def run(p):
                if stop.is_set():
                    return None
                if shutil.disk_usage(BOOK).free < MIN_FREE_GB * 1e9:
                    stop.set()
                    return None
                return dl.fetch(BOOK, p[0], p[1])
            left = []
            with cf.ThreadPoolExecutor(a.workers) as ex:
                for p, r in zip(todo, ex.map(run, todo)):
                    if r is None:
                        left.append(p)
                        continue
                    with lock:
                        fh.write("%s,%s,%s,%d\n" % r)
                    if r[2] == "ok":
                        n["ok"] += 1
                    elif r[2] == "404":
                        n["n404"] += 1
                    else:
                        left.append(p)
            fh.flush()
            say("круг %d: ok %d, 404 %d, осталось с ошибкой / не тронуто %d" % (rnd + 1, n["ok"], n["n404"], len(left)))
            todo = left
            if stop.is_set():
                say("СТОП: свободного места < %.0f ГБ — ничего не удалено" % MIN_FREE_GB)
                raise XbError(3)
    if todo:
        say("НЕ ОБЪЯСНЕНО 404: %d пар с сетевой ошибкой после %d кругов: %s" % (len(todo), a.rounds, todo[:5]))
        raise XbError(4)
    say("DONE ok=%d 404=%d" % (n["ok"], n["n404"]))


def cmd_check(a):
    per = load_need()
    have = have_pairs(ZIP)
    covs = sorted(glob.glob(BOOK + "/coverage-*.csv")) + [q("res", "xb-coverage.csv")]
    st = coverage_status([c for c in covs if os.path.exists(c)])
    allp = set().union(*per.values())
    bad = sorted(p for p in allp if p not in have and st.get(p) != "404")
    n404 = sorted(p for p in allp if p not in have and st.get(p) == "404")
    rep = dict(need=len(allp), on_disk=len([p for p in allp if p in have]), n404=len(n404), unexplained=len(bad), per_window={})
    for w, p in per.items():
        coins = sorted({x[0] for x in p})
        none = [c for c in coins if not any((c, d) in have for d in {x[1] for x in p if x[0] == c})]
        rep["per_window"][w] = dict(pairs=len(p), n404=len([x for x in p if x in set(n404)]), coins=len(coins), coins_without_any_file=len(none), coins_without_file=none)
    days404 = {}
    for _, d in n404:
        days404[d] = days404.get(d, 0) + 1
    rep["n404_by_day"] = dict(sorted(days404.items()))
    json.dump(rep, open(q("res", "xb-archive.json"), "w", encoding="utf-8"), ensure_ascii=False)
    say("нужно пар %d: на диске %d, 404 %d, без объяснения %d" % (rep["need"], rep["on_disk"], rep["n404"], rep["unexplained"]))
    for w, v in rep["per_window"].items():
        say("  %s: пар %d, 404 %d, монет %d, монет без единого файла %d" % (w, v["pairs"], v["n404"], v["coins"], v["coins_without_any_file"]))
    if bad:
        say("СТОП: %d пар без файла и без 404 (первые %s)" % (len(bad), bad[:5]))
        raise XbError(5)


# ---- признаки bd_* по всем строкам ------------------------------------------------------------------------------------------------
def cmd_fam(a):
    """`fam/bd/<окно>-short.parquet` — `tk671_bd.compute` боевой копии TK-671 (как в TK-690); единственное отличие — нижняя граница ω (с 14.09 на 01.07), дыры (08.10) прежние."""
    sys.path.insert(0, C671)
    import pandas as pd
    import tk671_bd as bd
    bd.OMEGA_LO_D = omega_lo_d()
    for w in WINS:
        rows = pd.read_parquet(rows_path(w))
        stats = {}
        df = bd.compute(rows, w, SN, a.procs, ZIP, None, stats)
        out = q("fam", "bd", "%s-%s.parquet" % (w, SN))
        df.to_parquet(out)
        info = dict(omega_lo=OMEGA_LO.isoformat(), nonnan={c: round(float(df[c].notna().mean()), 6) for c in df.columns}, **stats)
        json.dump(info, open(out + ".info.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say("fam %s: строк %d, в ω %d, монет с архивом %d из %d, bd_imb02 определён у %.1f %%" % (w, stats["rows"], stats["rows_omega"], stats["coins_with_archive"], stats["coins"], 100.0 * info["nonnan"]["bd_imb02"]))


# ---- сборка ftab674xb -------------------------------------------------------------------------------------------------------------
def is_bd(tok):
    return tok.startswith("bd_")


def split_sem(sem):
    """(остаток без bd-условий, список bd-условий) канонического смысла фильтра «a<=x&b>=y&…»."""
    toks = sem.split("&")
    return "&".join(t for t in toks if not is_bd(t)), [t for t in toks if is_bd(t)]


def plan_bd_columns(cols):
    """{имя столбца: (имя остатка | None, [имена столбцов единичек bd])} для всех столбцов с bd-условием; стоп, если остатка или единички нет в таблице."""
    out = {}
    for sem, name in cols.items():
        res, bds = split_sem(sem)
        if not bds:
            continue
        try:
            singles = [cols[b] for b in bds]
            rn = cols[res] if res else None
        except KeyError as e:
            raise XbError("столбца нет в таблице: %s (смысл %s)" % (e, sem))
        out[name] = (rn, singles)
    return out


def rebuild_bd(xs, allb, cols):
    """xs, allb — DataFrame строк-токенов с ключом (sym, sig_ms); возвращает копию xs, где столбцы с bd-условием = остаток(xs) И единички bd (allb)."""
    import pandas as pd
    plan = plan_bd_columns(cols)
    key = xs["sym"] + "|" + xs["sig_ms"]
    al = allb.set_index(allb["sym"] + "|" + allb["sig_ms"])
    miss = [k for k in key if k not in al.index]
    if miss:
        raise XbError("ключей xs нет в -all-b: %d (%s)" % (len(miss), miss[:3]))
    al = al.loc[key.to_numpy()]
    new = {}
    for name, (rn, singles) in plan.items():
        v = np.ones(len(xs), dtype=bool) if rn is None else (xs[rn].to_numpy() == "1.0")
        for sn in singles:
            v = v & (al[sn].to_numpy() == "1.0")
        new[name] = np.where(v, "1.0", "0.0")
    out = xs.copy()
    for name, v in new.items():
        out[name] = v
    return out, plan


def rebuild_bd_batch(t, allb, cols_b, cols_x, xs):
    """Таблица партии шага 4 `t` (строки-токены, ключ (sym, sig_ms)) → копия, где столбцы с bd-условием = остаток И единички bd из `allb` (PS-996 разд. 2 п. 1; имя единички в
    `allb` — по смыслу в `cols_x`). Остаток без bd: столбец таблицы партии, если он есть; иначе И единичек без bd — из таблицы партии, а если единички в ней нет — из `xs`
    (на общих единичках таблицы партий равны xs: контроль (а') сборки). Возвращает (таблица, {откуда взят остаток})."""
    key = (t["sym"] + "|" + t["sig_ms"]).to_numpy()

    def aligned(df, what):
        idx = df.set_index(df["sym"] + "|" + df["sig_ms"])
        miss = [k for k in key if k not in idx.index]
        if miss:
            raise XbError("ключей таблицы партии нет в %s: %d (%s)" % (what, len(miss), miss[:3]))
        return idx.loc[key]

    al, xi = aligned(allb, "-all-b"), aligned(xs, "xs")

    def single(tok):
        if tok in cols_b:
            return t[cols_b[tok]].to_numpy() == "1.0", "batch"
        if tok in cols_x:
            return xi[cols_x[tok]].to_numpy() == "1.0", "xs"
        raise XbError("единички нет ни в таблице партии, ни в xs: %s" % tok)

    out, st = t.copy(), dict(bd_cols=0, rem_col=0, rem_and_batch=0, rem_and_xs=0)
    for sem, name in cols_b.items():
        res, bds = split_sem(sem)
        if not bds:
            continue
        v = np.ones(len(t), dtype=bool)
        if res in cols_b:
            v = t[cols_b[res]].to_numpy() == "1.0"
            st["rem_col"] += 1
        elif res:
            srcs = set()
            for tok in res.split("&"):
                s_, w_ = single(tok)
                v = v & s_
                srcs.add(w_)
            st["rem_and_xs" if "xs" in srcs else "rem_and_batch"] += 1
        for b in bds:
            if b not in cols_x:
                raise XbError("единички bd нет в -all-b: %s" % b)
            v = v & (al[cols_x[b]].to_numpy() == "1.0")
        out[name] = np.where(v, "1.0", "0.0")
        st["bd_cols"] += 1
    return out, st


def read_table(path):
    import pandas as pd
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def write_table(df, path):
    df.to_csv(path, index=False, lineterminator="\n")


def cmd_build(a):
    """ftab674xb/<окно>-S.csv: xs с заменой столбцов bd; на выходе круговая проверка записи (xs → тот же файл байт в байт) и сводка."""
    xsd = os.path.join("/data/p42-tk690", "ftab674xs")
    alld = q("ftab674xs-all")
    cols = json.load(open(a.cols, encoding="utf-8"))
    rep = {}
    for w in WINS:
        xs = read_table(os.path.join(xsd, "%s-S.csv" % w))
        p_rt = q("tmp", "roundtrip-%s.csv" % w)
        write_table(xs, p_rt)
        if open(p_rt, "rb").read() != open(os.path.join(xsd, "%s-S.csv" % w), "rb").read():
            raise XbError("круговая запись xs ≠ файл (%s): формат писателя не совпадает" % w)
        allb = read_table(os.path.join(alld, "%s-S.csv" % w))
        if list(allb.columns) != list(xs.columns):
            raise XbError("заголовки xs и -all-b различаются (%s)" % w)
        out, plan = rebuild_bd(xs, allb, cols)
        write_table(out, q("ftab674xb", "%s-S.csv" % w))
        rep[w] = dict(rows=len(out), bd_columns=len(plan), changed_cells=int(sum((out[n].to_numpy() != xs[n].to_numpy()).sum() for n in plan)),
                      changed_rows=int(np.any([out[n].to_numpy() != xs[n].to_numpy() for n in plan], axis=0).sum()))
        say("build %s: строк %d, столбцов bd %d, изменено клеток %d, строк с изменением %d" % (w, rep[w]["rows"], rep[w]["bd_columns"], rep[w]["changed_cells"], rep[w]["changed_rows"]))
    json.dump(rep, open(q("res", "xb-build.json"), "w", encoding="utf-8"))


def cmd_controls(a):
    """(1) столбцы без bd у xb = xs (число расхождений 0); (2а) bd-признаки нового счёта = прежнего (TK-690) там, где прежние определены; (2б) -all-b = прежний -all на строках, где все bd-признаки прежнего определены;
    (2в) xb = -all-b на строках, где остаток xs = остатку -all-b. Расхождение — стоп (код 6)."""
    import pandas as pd
    cols = json.load(open(a.cols, encoding="utf-8"))
    plan = plan_bd_columns(cols)
    bdcols = set(plan)
    rep, bad = {}, []
    for w in WINS:
        xs = read_table(os.path.join("/data/p42-tk690", "ftab674xs", "%s-S.csv" % w))
        xb = read_table(q("ftab674xb", "%s-S.csv" % w))
        nb = [c for c in xs.columns if c not in bdcols]
        d1 = int(sum((xs[c].to_numpy() != xb[c].to_numpy()).sum() for c in nb))
        fo = pd.read_parquet(os.path.join("/data/p42-tk690", "fam", "bd", "%s-%s.parquet" % (w, SN)))
        fn = pd.read_parquet(q("fam", "bd", "%s-%s.parquet" % (w, SN)))
        if not fo.index.equals(fn.index):
            raise XbError("row_id у старого и нового fam/bd различаются (%s)" % w)
        both = fo.notna().all(axis=1).to_numpy()
        with np.errstate(invalid="ignore"):
            d2 = int(((fo.to_numpy()[both] != fn.to_numpy()[both]) & ~(np.isnan(fo.to_numpy()[both]) & np.isnan(fn.to_numpy()[both]))).sum())
        fully_new = int(fn.notna().all(axis=1).sum())
        rows = pd.read_parquet(rows_path(w))[["row_id", "sym", "sig_ms"]]
        rk = (rows["sym"].astype(str) + "|" + rows["sig_ms"].astype(str)).to_numpy()
        old_all = read_table(os.path.join("/data/p42-tk690", "ftab674xs-all", "%s-S.csv" % w))
        new_all = read_table(q("ftab674xs-all", "%s-S.csv" % w))
        kk = lambda df: (df["sym"] + "|" + df["sig_ms"]).to_numpy()
        ok_rows = set(rk[both])
        sel_o = np.array([k in ok_rows for k in kk(old_all)])
        o2 = old_all.set_index(pd.Index(kk(old_all)))[sorted(bdcols)]
        n2 = new_all.set_index(pd.Index(kk(new_all)))[sorted(bdcols)]
        common = o2.index[sel_o]
        d3 = int((o2.loc[common].to_numpy() != n2.loc[common].to_numpy()).sum())
        # (2в): xb = -all-b там, где остаток xs = остатку -all-b
        xbi = xb.set_index(pd.Index(kk(xb)))
        xsi = xs.set_index(pd.Index(kk(xs)))
        nai = new_all.set_index(pd.Index(kk(new_all))).loc[xbi.index]
        d4 = n4 = 0
        for name, (rn, _) in plan.items():
            same = np.ones(len(xbi), dtype=bool) if rn is None else (xsi[rn].to_numpy() == nai[rn].to_numpy())
            n4 += int(same.sum())
            d4 += int((xbi[name].to_numpy()[same] != nai[name].to_numpy()[same]).sum())
        # (охрана NaN, FIX 2 критика) строки, где bd остался NaN: дыра 08.10 / сутки без файла (404) / прочее — прочее обязано быть 0
        sys.path.insert(0, C671)
        import tk671_bd as bd
        rws = pd.read_parquet(rows_path(w))
        sec = bd.row_seconds(rws, w)
        nan_rows = fn.isna().any(axis=1).to_numpy()
        day = np.floor(sec / 86400.0).astype(np.int64)
        on_hole = np.isin(day, np.array(bd.HOLES_D, dtype=np.int64))
        covs = glob.glob(BOOK + "/coverage-*.csv") + [q("res", "xb-coverage.csv")]
        st404 = {k for k, v in coverage_status([c for c in covs if os.path.exists(c)]).items() if v == "404"}
        d2s = np.floor((sec - 3600.0) / 86400.0).astype(np.int64)
        no_file = np.zeros(len(rws), dtype=bool)                  # одна из нужных пар (монета, сутки) — 404
        gap = np.zeros(len(rws), dtype=bool)                      # файл есть, но снимков за час до σ у биржи нет (обрыв данных биржи)
        gap_last = {}                                             # (сутки σ, последний снимок суток) → число монет: что именно оборвано у биржи
        symv = rws["sym"].astype(str).to_numpy()
        for i in np.flatnonzero(nan_rows & ~on_hole):
            ds = sorted({day_str(day[i]), day_str(d2s[i])})
            if any((symv[i], d_) in st404 for d_ in ds):
                no_file[i] = True
                continue
            tm = bd.load_sym_fast(ZIP, symv[i], ds)
            if tm is None or not has_snapshot_in_hour(tm[0], sec[i]):
                gap[i] = True
                last = None if tm is None else dt.datetime.fromtimestamp(float(tm[0][tm[0] <= sec[i]].max()) if (tm[0] <= sec[i]).any() else 0.0, dt.timezone.utc).strftime("%H:%M")
                gap_last[(day_str(day[i]), last)] = gap_last.get((day_str(day[i]), last), 0) + 1
        other = nan_rows & ~on_hole & ~no_file & ~gap
        nan_rep = dict(rows=len(rws), nan_rows=int(nan_rows.sum()), on_hole=int((nan_rows & on_hole).sum()), no_file_404=int(no_file.sum()), exchange_gap_no_snapshot_hour=int(gap.sum()),
                       gap_days_last_snapshot={"%s %s" % k: v for k, v in sorted(gap_last.items(), key=lambda kv: str(kv[0]))}, other=int(other.sum()))
        say("NaN bd %s: строк %d, без bd %d — дыра 08.10 %d, сутки без файла (404) %d, обрыв данных биржи (файл есть, снимков за час до σ нет) %d %s, прочее %d" %
            (w, nan_rep["rows"], nan_rep["nan_rows"], nan_rep["on_hole"], nan_rep["no_file_404"], nan_rep["exchange_gap_no_snapshot_hour"], json.dumps(nan_rep["gap_days_last_snapshot"], ensure_ascii=False), nan_rep["other"]))
        if nan_rep["other"]:
            bad.append("%s NaN bd без объяснения: %d" % (w, nan_rep["other"]))
        rep[w] = dict(nan=nan_rep, non_bd_diff=d1, features_diff_where_old_defined=d2, rows_old_all_defined=int(both.sum()), rows_new_all_defined=fully_new, allb_vs_old_diff=d3, xb_vs_allb_diff=d4, xb_vs_allb_checked=n4)
        say("controls %s: (1) не-bd расхождений %d | (2а) признаков %d (строк с прежними определёнными %d, с новыми %d из %d) | (2б) -all-b vs -all %d | (2в) xb vs -all-b %d из %d" %
            (w, d1, d2, int(both.sum()), fully_new, len(fn), d3, d4, n4))
        for k, v in (("(1)", d1), ("(2а)", d2), ("(2б)", d3), ("(2в)", d4)):
            if v:
                bad.append("%s %s: %d" % (w, k, v))
    json.dump(rep, open(q("res", "xb-controls.json"), "w", encoding="utf-8"))
    if bad:
        say("СТОП: контроль не пройден: %s" % "; ".join(bad))
        raise XbError(6)
    say("КОНТРОЛИ 1, 2а, 2б, 2в: 0 расхождений")


def cmd_build_batch(a):
    """ftab6743<партия>xb/<окно>-S.csv: таблица партии шага 4 с заменой столбцов bd (PS-996 разд. 2 п. 1; остаток без столбца — `rebuild_bd_batch`). Контроли до записи, любой провал — стоп (код 6):
    (а) столбцы без bd равны таблице партии (байты); (а') единички без bd таблицы партии равны единичкам xs там, где есть в обеих; (б) bd-столбец равен составному столбцу -all-b на строках, где остаток
    таблицы партии равен остатку -all-b (имя остатка в -all-b — по смыслу); (г) та же формула с ПРЕЖНЕЙ -all даёт прежние bd-столбцы таблицы партии на окне wa (как xs: wb / wc прежних таблиц собраны по другим bd,
    их расхождение — предмет пересчёта, печатается числом)."""
    colsx = json.load(open(a.colsx, encoding="utf-8"))
    cols = json.load(open(os.path.join(a.src_res, "ftabcols6743%s-S.json" % a.tag), encoding="utf-8"))
    src = os.path.join(a.src_root, "ftab6743%s" % a.tag)
    plan = plan_bd_columns_batch(cols)
    bdn = set(plan)
    rep, bad = {}, []
    for w in WINS:
        t = read_table(os.path.join(src, "%s-S.csv" % w))
        p_rt = q("tmp", "roundtrip-%s-%s.csv" % (a.tag, w))
        write_table(t, p_rt)
        if open(p_rt, "rb").read() != open(os.path.join(src, "%s-S.csv" % w), "rb").read():
            raise XbError("круговая запись таблицы партии ≠ файл (%s %s): формат писателя не совпадает" % (a.tag, w))
        xs = read_table(os.path.join("/data/p42-tk690", "ftab674xs", "%s-S.csv" % w))
        allb = read_table(q("ftab674xs-all", "%s-S.csv" % w))
        old = read_table(os.path.join("/data/p42-tk690", "ftab674xs-all", "%s-S.csv" % w))
        out, st = rebuild_bd_batch(t, allb, cols, colsx, xs)
        d_a = int(sum((out[c].to_numpy() != t[c].to_numpy()).sum() for c in t.columns if c not in bdn))
        xi = xs.set_index(xs["sym"] + "|" + xs["sig_ms"]).loc[(t["sym"] + "|" + t["sig_ms"]).to_numpy()]
        n_a2 = d_a2 = 0
        for sem, name in cols.items():
            if "&" not in sem and not sem.startswith("bd_") and sem in colsx:
                n_a2 += 1
                d_a2 += int((t[name].to_numpy() != xi[colsx[sem]].to_numpy()).any())
        ai = allb.set_index(allb["sym"] + "|" + allb["sig_ms"]).loc[(t["sym"] + "|" + t["sig_ms"]).to_numpy()]
        n_b = d_b = 0
        for sem, name in cols.items():
            res, bds = split_sem(sem)
            if not bds or sem not in colsx or (res and (res not in cols or res not in colsx)):
                continue
            same = np.ones(len(t), dtype=bool) if not res else (t[cols[res]].to_numpy() == ai[colsx[res]].to_numpy())
            n_b += int(same.sum())
            d_b += int((out[name].to_numpy()[same] != ai[colsx[sem]].to_numpy()[same]).sum())
        out_g, _ = rebuild_bd_batch(t, old, cols, colsx, xs)
        d_g = {c: int((out_g[c].to_numpy() != t[c].to_numpy()).sum()) for c in bdn}
        d_g_cells, d_g_cols = sum(d_g.values()), sum(1 for v in d_g.values() if v)
        changed = int(sum((out[c].to_numpy() != t[c].to_numpy()).sum() for c in bdn))
        write_table(out, q("ftab6743%sxb" % a.tag, "%s-S.csv" % w))
        rep[w] = dict(rows=len(t), **st, non_bd_diff=d_a, singles_vs_xs_cols=n_a2, singles_vs_xs_bad=d_a2, vs_allb_checked=n_b, vs_allb_diff=d_b, old_formula_diff_cells=d_g_cells, old_formula_diff_cols=d_g_cols, changed_cells=changed)
        say("batch %s %s: строк %d, bd-столбцов %d (остаток: столбец %d, И единичек партии %d, с xs %d) | (а) %d (а') %d из %d | (б) %d из %d | (г) формула на прежней -all: %d клеток в %d столбцах | изменено клеток %d" %
            (a.tag, w, len(t), st["bd_cols"], st["rem_col"], st["rem_and_batch"], st["rem_and_xs"], d_a, d_a2, n_a2, d_b, n_b, d_g_cells, d_g_cols, changed))
        for k_, v_ in (("(а)", d_a), ("(а')", d_a2), ("(б)", d_b)) + ((("(г)", d_g_cells),) if w == "wa" else ()):
            if v_:
                bad.append("%s %s: %d" % (w, k_, v_))
    json.dump(rep, open(q("res", "xb-batch-%s.json" % a.tag), "w", encoding="utf-8"))
    if bad:
        say("СТОП: контроль не пройден: %s" % "; ".join(bad))
        raise XbError(6)
    say("партия %s: контроли (а), (а'), (б), (г wa): 0 расхождений" % a.tag)


def plan_bd_columns_batch(cols):
    """{имя столбца с bd-условием: смысл} таблицы партии (остаток без столбца допустим — `rebuild_bd_batch`)."""
    return {name: sem for sem, name in cols.items() if split_sem(sem)[1]}


def cmd_lists(a):
    """Списки cid партии шага 4 для пересчёта (PS-996 разд. 2): `bd-<партия>.json` — действительные клетки класса bd; `k3-<партия>.json` — 30 действительных клеток без bd (seed 674, по классам A / B / cc: по 10, нехватку
    добирают из остальных классов; меньше 30 — стоп, код 6)."""
    import random
    import tk674_emptycols as ec
    lv = json.load(open(os.path.join(a.src_res, "level674bs3%s-S.json" % a.tag), encoding="utf-8"))["cells"]
    invf = os.path.join(a.src_res, "invalid674bs3%s-S.json" % a.tag)
    inv = json.load(open(invf, encoding="utf-8")) if os.path.exists(invf) else {}
    by = {"A": [], "B": [], "cc": [], "bd": []}
    for k, c in sorted(lv.items()):
        if c["cid"] in inv:
            continue
        cl = ec.filt_class(c.get("filt"))
        if cl in by:
            by[cl].append(c["cid"])
    rnd = random.Random(674)
    pick, rest = [], []
    for cl in ("A", "B", "cc"):
        xs = sorted(set(by[cl]))
        rnd.shuffle(xs)
        pick += xs[:10]
        rest += xs[10:]
    rnd.shuffle(rest)
    pick += rest[:max(0, 30 - len(pick))]
    if len(pick) < 30:
        say("СТОП: в партии %s меньше 30 действительных клеток без bd (%d)" % (a.tag, len(pick)))
        raise XbError(6)
    bd = sorted(set(by["bd"]))
    json.dump(sorted(pick), open(os.path.join(a.out_dir, "k3-%s.json" % a.tag), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(bd, open(os.path.join(a.out_dir, "bd-%s.json" % a.tag), "w", encoding="utf-8"), ensure_ascii=False)
    say("lists %s: K3 %d (A %d, B %d, cc %d в партии), bd-клеток %d" % (a.tag or "base", len(pick), len(by["A"]), len(by["B"]), len(by["cc"]), len(bd)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("fam")
    p.add_argument("--procs", type=int, default=3)
    p.set_defaults(f=cmd_fam)
    for nm, fn in (("build", cmd_build), ("controls", cmd_controls)):
        p = sp.add_parser(nm)
        p.add_argument("--cols", default="/data/p42-tk674c/res/ftabcols674x-S.json")
        p.set_defaults(f=fn)
    p = sp.add_parser("build-batch")
    p.add_argument("--tag", required=True)
    p.add_argument("--colsx", default="/data/p42-tk674c/res/ftabcols674x-S.json")
    p.add_argument("--src-root", default="/data/p42-tk674c")
    p.add_argument("--src-res", default="/data/p42-tk674c/res")
    p.set_defaults(f=cmd_build_batch)
    p = sp.add_parser("lists")
    p.add_argument("--tag", required=True)
    p.add_argument("--src-res", default="/data/p42-tk674c/res")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(f=cmd_lists)
    sp.add_parser("need").set_defaults(f=cmd_need)
    p = sp.add_parser("dl")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--rounds", type=int, default=4)
    p.set_defaults(f=cmd_dl)
    sp.add_parser("check").set_defaults(f=cmd_check)
    a = ap.parse_args()
    sys.exit(a.f(a) or 0)


if __name__ == "__main__":
    main()
