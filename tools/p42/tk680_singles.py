#!/usr/bin/env python3
"""П-42 §13.73.7 (TK-680): единички — уровень 1 карты TK-674 для осей, которых в ней нет (`tk680_axes.py`). Счёт — стадиями `tk674_run.py` при TK674_LEVEL=1.
  python3 tk680_singles.py select          — srv, без P&L: пороги столбцов (квантили по кандидатам базы), клетки, «есть / считаем» по БД летописи
                                             → res/level674b1-<сторона>.json, res/thr680.json
  python3 tk680_singles.py map             — srv, из БД: по оси — все значения рядом с базой, охраны (а)–(д), плато, «активная» ось → res/map674b1-<сторона>.json
  python  tk680_singles.py md <каталог с map674b1-*.json> [--out файл.md]   — таблицы для отчёта (локально)
Доллары — ставка 500 $; Sharpe — годовой по суточному ряду; без бп, без медиан."""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_axes as ax  # noqa: E402
import tk680_axes as a80  # noqa: E402

COLS_FILE = os.path.join(HERE, "tk680_cols.json")     # имена столбцов покрытия (docs/findings/p42-titration-coverage-2026-11-22/cols.json)
ENGNAME = {"L": "long", "S": "short"}
OPS = {"le": "<=", "ge": ">="}
THR_NAME = {"hist": "thr680h.json", "grid": "thr680g.json", "ctx": "thr680c.json", "t681": "thr680t.json", "new": "thr680n.json", "t697": "thr680p.json", "t697b": "thr680q.json"}.get(a80.SET, "thr680.json")     # пороги набора «история пробоев» (§13.73.8) — свой файл, заморозка thr680.json не трогается


def cosig15(sym, ms):
    """Число других монет с сигналом (кандидатом базы той же стороны) в (t − 15 мин, t) — только прошлое; как `sigfeat_join.py`, на кандидатах базы h775."""
    import numpy as np
    ms = np.asarray(ms, dtype=np.int64)
    order = np.argsort(ms, kind="stable")
    sm, ss = ms[order], [str(sym[i]) for i in order]
    out = np.zeros(len(ms))
    for i in range(len(ms)):
        i0, i1 = np.searchsorted(sm, ms[i] - 15 * 60_000, "right"), np.searchsorted(sm, ms[i], "left")
        out[i] = len(set(ss[i0:i1]) - {str(sym[i])})
    return out


def coverage_cols():
    if a80.SET == "hist":
        return list(a80.HIST_COLS)
    if a80.SET == "ctx":
        return list(a80.CTX_COLS)
    if a80.SET == "t681":
        return list(a80.FAST_NAMES)
    if a80.SET == "t697":
        return list(a80.T697_NAMES)
    if a80.SET == "t697b":
        return list(a80.T697B_NAMES)
    return json.load(open(COLS_FILE, encoding="utf-8"))


def fast_join(side, df):
    """Набор t681: к строкам df (win, row_id, sym, meta_sig_ms) — 50 столбцов FAST из `fast.csv` TK-681; ключ окно + row_id, строки сверяются по монете и sig_ms; строка без пары — ошибка."""
    import pandas as pd
    parts = []
    for w in a80.T681_WINS:
        d = a80.T681_FASTDIR % (w, ENGNAME[side])
        key = pd.read_csv(d + "/eps-key.csv", usecols=["row_id", "sym", "sig_ms"])
        fc = pd.read_csv(d + "/fast.csv")
        if len(key) != len(fc) or not (key["sym"].to_numpy() == fc["sym"].to_numpy()).all() or not (key["sig_ms"].to_numpy() == fc["sig_ms"].to_numpy()).all():
            raise SystemExit("FAST %s %s: fast.csv и eps-key.csv не совпадают построчно" % (w, side))
        if list(fc.columns[2:]) != a80.FAST_NAMES:
            raise SystemExit("FAST %s %s: столбцы fast.csv ≠ списку поправки" % (w, side))
        fc = fc.rename(columns={"sym": "f_sym", "sig_ms": "f_ms"})
        fc.insert(0, "row_id", key["row_id"].to_numpy())
        fc.insert(0, "win", w)
        parts.append(fc)
    m = df.merge(pd.concat(parts, ignore_index=True), on=["win", "row_id"], how="left", validate="one_to_one")
    if m["f_sym"].isna().any() or not (m["f_sym"].to_numpy() == m["sym"].to_numpy()).all() or not (m["f_ms"].to_numpy() == m["meta_sig_ms"].to_numpy()).all():
        raise SystemExit("FAST %s: строки кандидатов не совпадают с fast.csv (монета / sig_ms / нет пары)" % side)
    return m.drop(columns=["f_sym", "f_ms"])


def t697_join(side, df):
    """Набор t697: к строкам df (win, row_id, sym, meta_sig_ms) — 40 столбцов семейств m697 / h697 / l697 (`/data/p42-tk697/fam`); ключ окно + row_id, строки сверяются по монете и sig_ms rows.parquet TK-671."""
    import pandas as pd
    parts = []
    for w in a80.T681_WINS:
        fam = pd.read_parquet(a80.T697_ROWS % (w, ENGNAME[side]), columns=["row_id", "sym", "sig_ms"]).rename(columns={"sym": "f_sym", "sig_ms": "f_ms"}).set_index("row_id")
        n = len(fam)
        for f, cols in a80.T697_FAMS.items():
            x = pd.read_parquet(a80.T697_FAM % (f, w, ENGNAME[side]))
            if list(x.columns) != cols or len(x) != n or not x.index.equals(fam.index):
                raise SystemExit("семейство %s %s %s: столбцы или строки не совпали с rows.parquet / списком поправки" % (f, w, side))
            fam = fam.join(x, how="left", validate="one_to_one")
        fam = fam.reset_index()
        fam.insert(0, "win", w)
        parts.append(fam)
    m = df.merge(pd.concat(parts, ignore_index=True), on=["win", "row_id"], how="left", validate="one_to_one")
    if m["f_sym"].isna().any() or not (m["f_sym"].to_numpy() == m["sym"].to_numpy()).all() or not (m["f_ms"].to_numpy() == m["meta_sig_ms"].to_numpy()).all():
        raise SystemExit("t697 %s: строки кандидатов не совпадают с rows.parquet (монета / sig_ms / нет пары)" % side)
    m = m.drop(columns=["f_sym", "f_ms"])
    ext = []                                        # критик 23.11 FIX 2: сигналы базы без строки таблицы (следующие сигналы того же уровня) — строки из `tk697_ext.py`, ключ (sym, sig_ms) не пересекается с таблицей
    for w in a80.T681_WINS:
        e = pd.read_parquet(a80.T697_EXT % (w, ENGNAME[side])).reset_index().rename(columns={"sig_ms": "meta_sig_ms"}).drop(columns="src")
        e["win"], e["meta_base_pass"] = w, 1
        ext.append(e)
    return pd.concat([m] + ext, ignore_index=True)


def t697b_join(side):
    """Набор t697b: строки сигналов базы со столбцами `btc_mv15`, `mk_btc_s60` (`tk697_b.py`, файл на окно и сторону, ключ (sym, sig_ms)); все сигналы базы h775, строка таблицы TK-671 не нужна —
    значения столбцов зависят только от монеты и минуты сигнала. Дубли ключа — ошибка; порядок и состав столбцов сверяются со списком поправки."""
    import pandas as pd
    parts = []
    for w in a80.T681_WINS:
        x = pd.read_parquet(a80.T697B_FAM % (w, ENGNAME[side])).reset_index(drop=True).rename(columns={"sig_ms": "meta_sig_ms"})
        if list(x.columns) != ["sym", "meta_sig_ms"] + a80.T697B_NAMES:
            raise SystemExit("t697b %s %s: столбцы файла ≠ списку поправки" % (w, side))
        if x.duplicated(["sym", "meta_sig_ms"]).any():
            raise SystemExit("t697b %s %s: дубли (sym, sig_ms)" % (w, side))
        x["win"], x["meta_base_pass"] = w, 1
        parts.append(x)
    return pd.concat(parts, ignore_index=True)


def add_columns(side, st, tabs):
    """Столбцы таблицы TK-671 к строкам кандидатов базы (ключ окно + sym + sig_ms, строка с meta_base_pass > 0, бит 1 раньше бита 2); нет строки — пусто (проходит)."""
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq
    have = set(pq.ParquetFile(a80.PARQUET % ENGNAME[side]).schema.names)
    if a80.SET == "ctx":
        crypto = None
        if os.environ.get("TK674_UNI") == "c539":      # §13.73.18: на c539 «другие монеты» — только крипто-монеты (сигналы TradFi в составе нет)
            import tk625_run as r5
            crypto = sorted(r5.Uni("c539").coins())
        for w in st.TRADE_WINDOWS:
            sym, ms = np.asarray(tabs[w.name]["sym"]).astype(str), np.asarray(tabs[w.name]["ms"], dtype=np.int64)
            if crypto is None:
                tabs[w.name]["cosig15"] = cosig15(sym, ms)
                continue
            keep = np.isin(sym, crypto)
            v = np.full(len(ms), np.nan)
            v[keep] = cosig15(sym[keep], ms[keep])
            tabs[w.name]["cosig15"] = v
    extra = set()
    if a80.SET == "t681":
        have |= set(a80.FAST_NAMES)
    if a80.SET == "t697":
        have |= set(a80.T697_FAM_NAMES)
    if a80.SET == "t697b":
        have |= set(a80.T697B_NAMES)
    if a80.SET == "hist":
        extra = set(pq.ParquetFile(a80.HIST_EXTRA % ENGNAME[side]).schema.names) - have
        have |= extra
        rid = pd.read_parquet(a80.PARQUET % ENGNAME[side], columns=["row_id"])["row_id"].to_numpy()
    cl = a80.column_list(coverage_cols())
    srcs = lambda src: src if isinstance(src, tuple) else (src,)
    absent = [name for name, src, _ in cl if not all(x in have for x in srcs(src)) and name not in tabs[st.TRADE_WINDOWS[0].name]]
    cl = [c for c in cl if c[0] not in absent]
    need = sorted({x for _, src, _ in cl for x in srcs(src) if x in have})
    fast = set(a80.FAST_NAMES) if a80.SET == "t681" else (set(a80.T697_FAM_NAMES) if a80.SET == "t697" else (set(a80.T697B_NAMES) if a80.SET == "t697b" else set()))
    df = pd.read_parquet(a80.PARQUET % ENGNAME[side], columns=["win", "sym", "meta_sig_ms", "meta_base_pass"] + (["row_id"] if fast else []) + [x for x in need if x not in extra and x not in fast])
    if extra:                                       # ok10_d3 / d5: файл tk680_hist.py, строки в порядке таблицы TK-671 (ключ сверяется)
        ex = pd.read_parquet(a80.HIST_EXTRA % ENGNAME[side])
        if len(ex) != len(df) or not ((ex["win"].astype(str).to_numpy() == df["win"].astype(str).to_numpy()) & (ex["row_id"].to_numpy() == rid)).all():
            raise SystemExit("таблица ok10_d3 / d5 %s: строки не совпадают с таблицей TK-671" % side)
        for x in sorted(extra & set(need)):
            df[x] = ex[x].to_numpy()
    df = df[df["meta_base_pass"] > 0].copy()
    df["win"], df["sym"] = df["win"].astype(str), df["sym"].astype(str)
    if fast:
        df = t697b_join(side) if a80.SET == "t697b" else (t697_join(side, df) if a80.SET == "t697" else fast_join(side, df))
    df["_pri"] = (df["meta_base_pass"] != 1).astype(int)
    df = df.sort_values("_pri", kind="stable").drop_duplicates(["win", "sym", "meta_sig_ms"])
    rep = {}
    for w in st.TRADE_WINDOWS:
        tab = tabs[w.name]
        key = pd.DataFrame(dict(sym=[str(x) for x in tab["sym"]], meta_sig_ms=np.asarray(tab["ms"], dtype=np.int64)))
        m = key.merge(df[df["win"] == w.name], on=["sym", "meta_sig_ms"], how="left", validate="one_to_one")
        rep[w.name] = (int(m["meta_base_pass"].notna().sum()), len(m))
        for name, src, _ in cl:
            if name in tab:                         # столбцы, которые уже даёт таблица TK-654 / TK-638 (tpm60, cons60, mv5, pierce_ep, spread_tau, n24), не подменяются
                continue
            if not all(x in have for x in srcs(src)):
                raise SystemExit("столбец %s: нет в таблице TK-671 и в таблице кандидатов" % name)
            if isinstance(src, tuple):
                num, den = m[src[0]].to_numpy(np.float64), m[src[1]].to_numpy(np.float64)
                with np.errstate(invalid="ignore", divide="ignore"):
                    tab[name] = np.where(den > 0, num / den, np.nan)
            else:
                tab[name] = m[src].to_numpy(np.float64)
    print("таблица TK-671 %s: строк кандидатов со столбцами %s" % (side, ", ".join("%s %d/%d" % (k, a, b) for k, (a, b) in rep.items())), flush=True)
    for w in st.TRADE_WINDOWS:
        tabs[w.name]["absent680"] = absent
    return rep


def _win_cover(tabs, st, sel, cname):
    """Набор t697 (критик 23.11, PS-993 (в)/(д)): определённость столбца по окнам — {окно: [определён, кандидатов]} по кандидатам базы в отборе `sel`; охрана 70 % читает минимум по окнам."""
    import numpy as np
    if a80.SET not in ("t697", "t697b"):
        return None
    return {w.name: [int(np.isfinite(tabs[w.name][cname][sel[w.name]]).sum()), int(sel[w.name].sum())] for w in st.TRADE_WINDOWS}


def _thresholds(v, cname=None):
    """[(метка, порог ≤), …], [(метка, порог ≥), …] столбца по значениям кандидатов базы в зачётном диапазоне."""
    import numpy as np
    import tk638_run as t8
    u = np.unique(v)
    if len(u) <= 1:
        return [], []
    if a80.SET == "ctx":
        return list(a80.CTX_COLS[cname]), []
    if a80.SET == "hist":                           # числа пробоев: пороги заданы поправкой §13.73.8, не данными
        return list(a80.HIST_THR[0]), list(a80.HIST_THR[1])
    if a80.SET == "t697" and cname in a80.T697_COUNT:      # счётчики уровней: пороги — числа (поправка TK-697), как у ok10_*
        return list(a80.HIST_THR[0]), list(a80.HIST_THR[1])
    if len(u) <= 4:
        return [("v%s" % ax.num(x), float(x)) for x in u[:-1]], [("v%s" % ax.num(x), float(x)) for x in u[1:]]
    out, seen = [], set()
    for q in a80.QS:
        x = t8.sig2(float(np.quantile(v, q / 100, method="linear")))
        if x not in seen:
            seen.add(x)
            out.append(("q%d" % q, x))
    if a80.SET == "t697b" and a80.T697B_ZERO[1] not in seen:      # TK-697 вторая поправка: порог 0 (знак хода BTC) к квантилям; по возрастанию порога — соседи плато
        out = sorted(out + [a80.T697B_ZERO], key=lambda t: t[1])
    return out, list(out)


def reroot_level(src, base_ck, base_cell, anchors):
    """§13.73.17: набор PS-974 на новой вселенной — те же клетки (ключи, пороги, фильтры, оси без изменений), плюс клетка базы (на h775 она была в БД); anchors — только база."""
    out = dict(src)
    if anchors:
        out.update(axes=[], skipped=[], cells={base_ck: base_cell}, eligible=0)
    else:
        out["cells"] = dict({base_ck: base_cell}, **src["cells"])
    return out


def select_from():
    """TK680_FROM=<каталог счёта PS-974>: уровень и пороги берутся из него без пересчёта (решений отбора нет); TK680_ANCHORS=1 — только база стороны."""
    import shutil
    import tk674_run as run
    assert run.LEVEL == 1 and ax.REBASE and run.UNI != "h775", "TK674_LEVEL=1 TK674_REBASE=1 TK674_UNI=c539"
    src_root, anchors = os.environ["TK680_FROM"], os.environ.get("TK680_ANCHORS") == "1"
    store, bins = run.chron_store(ro=True), run.eq_bins()
    os.makedirs(os.path.join(run.ROOT, "res"), exist_ok=True)
    if not anchors:
        import numpy as np
        import tk643_run as t
        import tk654_run as r54
        st, r5, _, _ = t._srv()
        u = r5.Uni(run.UNI)
        crypto, eps = sorted(u.coins()), r54.episodes_h775()
    for side in ax.SIDES:
        src = json.load(open(os.path.join(src_root, "res", os.path.basename(run.level_path(side))), encoding="utf-8"))
        base = a80.make_cell(side, "base", "base", {})
        assert base["ckey"] == ax.base_ckey(side)
        out = reroot_level(src, base["ckey"], dict(cid=base["cid"], patch=base["patch"], filt=base["filt"], sig=base["sig"]), anchors)
        if not anchors:                             # покрытие столбцов — по кандидатам базы только крипто-монет (охрана 70 % PS-974 п. 3 читает n / n_all)
            tabs = run.feat_tabs(side, st, u, eps)
            sel = {w.name: r54.in_scored_range(w, tabs[w.name]["ms"]) & np.isin(np.asarray(tabs[w.name]["sym"]).astype(str), crypto) for w in st.TRADE_WINDOWS}
            for a in out["axes"]:
                if a["kind"] == "col":
                    cn = a["axis"].split(".")[0]
                    allv = np.concatenate([(np.where(tabs[w.name]["m24"].astype(bool), np.nan, tabs[w.name]["n24"]) if cn == "n24" else tabs[w.name][cn])[sel[w.name]] for w in st.TRADE_WINDOWS])
                    a["n"], a["n_all"] = int(np.isfinite(allv).sum()), int(len(allv))
                    nw = _win_cover(tabs, st, sel, cn)
                    if nw:
                        a["n_win"] = nw
        found = store.cells_lookup(ENGNAME[side], run.UNI, run.WIN, list(out["cells"]), bins=bins)
        out.update(n_cells=len(out["cells"]), n_have=len(found), n_new=len(out["cells"]) - len(found))
        json.dump(out, open(run.level_path(side), "w", encoding="utf-8"), ensure_ascii=False)
        sigs = {tuple(tuple(x) for x in c["sig"]) for k, c in out["cells"].items() if k not in found and c["sig"]}
        print("== %s (%s): клеток %d, в БД есть %d, считаем %d; проходов сигналов %d" % (side, run.UNI, out["n_cells"], out["n_have"], out["n_new"], len(sigs)), flush=True)
    tp = os.path.join(run.ROOT, "res", THR_NAME)
    shutil.copyfile(os.path.join(src_root, "res", THR_NAME), tp)
    print("select_from готово; пороги %s sha256 %s" % (tp, _sha(tp)))


def cmd_select():
    if os.environ.get("TK680_FROM"):
        return select_from()
    import numpy as np
    import tk643_run as t
    import tk654_run as r54
    import tk674_run as run
    assert run.LEVEL == 1 and ax.REBASE, "TK674_LEVEL=1 TK674_REBASE=1"
    st, r5, _, _ = t._srv()
    u = r5.Uni(run.UNI)
    eps = r54.episodes_h775()
    store = run.chron_store(ro=True)
    bins = run.eq_bins()
    thr_out = {}
    for side in ax.SIDES:
        cells, axes, skipped = {}, [], []
        base_ck = ax.base_ckey(side)

        def put(axis, label, patch, sem=None):
            c = a80.make_cell(side, axis, label, patch, sem)
            if c["ckey"] == base_ck:
                return base_ck
            cells.setdefault(c["ckey"], c)
            return c["ckey"]
        for name, vals, base in a80.engine_axes(side):
            axes.append(dict(axis=name, kind="key", base=base, vals=[[lab, base_ck if lab == base else put(name, lab, patch)] for lab, patch in vals]))
        tabs = run.feat_tabs(side, st, u, eps)
        sc = {w.name: r54.in_scored_range(w, tabs[w.name]["ms"]) for w in st.TRADE_WINDOWS}
        if a80.SET in ("t697", "t697b") and run.UNI != "h775":     # TK-697: пороги — по кандидатам базы состава c539 (крипто-монеты), как охрана покрытия в `select_from`
            crypto = sorted(u.coins())
            sc = {w.name: sc[w.name] & np.isin(np.asarray(tabs[w.name]["sym"]).astype(str), crypto) for w in st.TRADE_WINDOWS}
        thr_out[side] = {}
        absent = tabs[st.TRADE_WINDOWS[0].name]["absent680"]
        skipped += [[c, "в таблице TK-671 нет"] for c in absent]
        for cname, src, dirs in a80.column_list(coverage_cols()):
            if cname in absent:
                continue
            allv = np.concatenate([tabs[w.name][cname][sc[w.name]] for w in st.TRADE_WINDOWS])
            if cname == "n24":
                allv = np.where(np.concatenate([tabs[w.name]["m24"][sc[w.name]] for w in st.TRADE_WINDOWS]).astype(bool), np.nan, allv)
            fin = allv[np.isfinite(allv)]
            le, ge = _thresholds(fin, cname)
            if not le and not ge:
                skipped.append([cname, "одно значение у кандидатов базы"])
                continue
            nw = _win_cover(tabs, st, sc, cname)
            thr_out[side][cname] = dict(n=int(len(fin)), n_all=int(len(allv)), le=le, ge=ge, **({"n_win": nw} if nw else {}))
            for d in dirs:
                vals = []
                for lab, x in (le if d == "le" else ge):
                    cut = int(((fin > x) if d == "le" else (fin < x)).sum())
                    if cut == 0:                    # порог никого не убирает — клетка равна базе
                        continue
                    vals.append([lab, put("%s.%s" % (cname, d), lab, {}, (cname, OPS[d], x)), x, cut])
                if vals:
                    vals = (vals + [["off", base_ck]]) if d == "le" else ([["off", base_ck]] + vals)      # «off» рядом с самым мягким порогом
                    axes.append(dict(axis="%s.%s" % (cname, d), kind="col", base="off", vals=vals, n=int(len(fin)), n_all=int(len(allv)), **({"n_win": nw} if nw else {})))
        found = store.cells_lookup(ENGNAME[side], run.UNI, run.WIN, list(cells), bins=bins)
        nk = sum(1 for c in cells.values() if not c["filt"])
        sigs = {tuple(tuple(x) for x in c["sig"]) for k, c in cells.items() if k not in found and c["sig"]}
        out = dict(side=side, n=1, cap=None, taken=[], eligible=len(axes), left_out=0, active_subs=0, subs=0, axes=axes, skipped=skipped, n_cells=len(cells), n_have=len(found),
                   n_new=len(cells) - len(found), cells={k: dict(cid=c["cid"], patch=c["patch"], filt=c["filt"], sig=c["sig"]) for k, c in cells.items()})
        json.dump(out, open(run.level_path(side), "w", encoding="utf-8"), ensure_ascii=False)
        print("== %s: осей %d (ключи %d, столбцы %d), клеток %d (ключи %d, столбцы %d), в БД есть %d, считаем %d; проходов сигналов %d; столбцов без оси %d" % (
            side, len(axes), sum(1 for a in axes if a["kind"] == "key"), sum(1 for a in axes if a["kind"] == "col"), len(cells), nk, len(cells) - nk, len(found),
            len(cells) - len(found), len(sigs), len(skipped)), flush=True)
    tp = os.path.join(run.ROOT, "res", THR_NAME)
    json.dump(thr_out, open(tp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    print("select готово; пороги %s sha256 %s" % (tp, _sha(tp)))


def _sha(path):
    import hashlib
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


MARGIN_POS, ACCOUNT = 250.0, 2500.0            # маржа позиции (ставка 500 $, плечо 2) и счёт, $ (В-89…В-91)


def _concurrency(row):
    """Наибольшее число одновременно открытых позиций клетки по файлам сделок окон (вход … выход), максимум по окнам."""
    import numpy as np
    import tk604_stats as st
    best = 0
    for d in row["extra"]["dirs"].values():
        f = os.path.join(d, "%s-trades.csv" % row["side_name"])
        tb = st.read_trades_csv(f)
        if not len(tb.entry_ms):
            continue
        ev = np.concatenate([np.stack([tb.entry_ms, np.ones(len(tb.entry_ms))], 1), np.stack([tb.exit_ms, -np.ones(len(tb.exit_ms))], 1)])
        ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]      # при равном времени выход раньше входа
        best = max(best, int(np.cumsum(ev[:, 1]).max()))
    return best


def _lvl_stop(row):
    """[сдвинуто зажимом sm, вплотную] — сумма `lvl_stop_[floored,clamped]` из meta.json клетки по окнам; None, если счётчика нет (клетка считалась старым бинарём)."""
    tot = [0, 0]
    for d in row["extra"]["dirs"].values():
        meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
        v = meta.get("lvl_stop_[floored,clamped]", (meta.get(row["side_name"]) or {}).get("lvl_stop_[floored,clamped]"))
        if v is None:
            return None
        tot[0] += int(v[0])
        tot[1] += int(v[1])
    return tot


def _nan_keys(side):
    """{столбец: {окно: множество (монета, sig_ms) кандидатов с пустым значением}} — набор «история пробоев» (§13.73.8: пустое проходит фильтр)."""
    import numpy as np
    import tk643_run as t
    import tk654_run as r54
    import tk674_run as run
    st, r5, _, _ = t._srv()
    tabs = run.feat_tabs(side, st, r5.Uni(run.UNI), r54.episodes_h775())
    out = {}
    for c in a80.nan_cols():
        out[c] = {}
        for w in st.TRADE_WINDOWS:
            tab = tabs[w.name]
            bad = ~np.isfinite(tab[c])
            out[c][w.name] = {(str(s), int(m)) for s, m, b in zip(tab["sym"], tab["ms"], bad) if b}
    return out


def _nan_trades(row, keys):
    """(число, Σ $ gross − fees) зачётных сделок клетки, чей кандидат прошёл с пустым значением столбца; зачётные — как в `tk604_stats.splice` (вход в сутках окна;
    файл сделок держит и разгонные сутки — их считать нельзя: у них нет истории, значение пусто всегда)."""
    import tk604_stats as st
    n, usd = 0, 0.0
    for w in st.TRADE_WINDOWS:
        tb = st.read_trades_csv(os.path.join(row["extra"]["dirs"][w.name], "%s-trades.csv" % row["side_name"]))
        d = st.abs_day(w, tb.entry_ms)
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        for i in range(len(tb.sym)):
            if lo_i <= d[i] <= hi_i and (str(tb.sym[i]), int(tb.sig_ms[i])) in keys[w.name]:
                n += 1
                usd += float(tb.gross[i] - tb.fees[i])
    return n, usd


def _prefix(label):
    return re.sub(r"[-\d.]+$", "", label)


def cmd_map():
    import numpy as np
    import tk674_run as run
    store = run.chron_store(ro=True)
    bins = run.eq_bins()
    for side in ax.SIDES:
        lv = json.load(open(run.level_path(side), encoding="utf-8"))
        inv = run.load_invalid(side)
        base_ck = ax.base_ckey(side)
        nank = _nan_keys(side) if a80.SET in ("hist", "t681", "t697", "t697b") else None
        rows = store.cells_lookup(ENGNAME[side], run.UNI, run.WIN, list(lv["cells"]) + [base_ck], bins=bins)
        brow = rows[base_ck]
        bsh, busd = brow["sharpe"], brow["usd"]
        missing = [0]

        def eval_axis(a):
            labs = [v[0] for v in a["vals"]]
            cks = [v[1] for v in a["vals"]]
            cand, cells = [], []
            for i, (lab, ck) in enumerate(zip(labs, cks)):
                r = rows.get(ck)
                if r is None:
                    why = inv.get(lv["cells"][ck]["cid"]) if ck in lv["cells"] else None
                    missing[0] += int(why is None)
                    cells.append(dict(label=lab, ckey=ck, invalid=why or "нет в БД"))
                    continue
                sh = r["sharpe"] if r["sharpe"] is not None else float("nan")
                nb = []
                for j in (i - 1, i + 1):
                    if 0 <= j < len(labs) and (a["kind"] == "col" or _prefix(labs[j]) == _prefix(lab)) and cks[j] in rows and rows[cks[j]]["sharpe"] is not None:
                        nb.append(rows[cks[j]]["sharpe"])
                nbm = float(np.mean(nb)) if nb else None
                g = dict(run.guards(r, brow), g=bool(nb) and sh == sh and nbm >= 0.8 * sh and nbm >= bsh)
                rep = run.rep_cell((ck, dict(r, sharpe=sh), g, nbm), bsh)
                rep.update(label=lab, is_base=ck == base_ck, guards={k: bool(v) for k, v in g.items()})
                if len(a["vals"][i]) > 2:
                    rep.update(thr=a["vals"][i][2], cut=a["vals"][i][3])
                if nank is not None and ck != base_ck and a["axis"].split(".")[0] in nank:
                    nn, nu = _nan_trades(dict(r, side_name=ENGNAME[side]), nank[a["axis"].split(".")[0]])
                    rep.update(nan_n=nn, nan_usd=nu)
                if a["axis"].startswith("sm_") and ck != base_ck:      # §13.73.14: счётчики зажима sm (новый бинарь; клетка из БД со старым бинарём — без счётчика)
                    lv_ = _lvl_stop(dict(r, side_name=ENGNAME[side]))
                    if lv_ is not None:
                        rep.update(lvl_floored=lv_[0], lvl_clamped=lv_[1])
                if a["axis"] == "p":                   # число позиций меняет капитал под риском: маржа в работе и просадка — в долях счёта
                    nc = _concurrency(dict(r, side_name=ENGNAME[side]))
                    rep.update(conc=nc, conc_margin=nc * MARGIN_POS, conc_margin_pct=nc * MARGIN_POS / ACCOUNT * 100.0, maxdd_acct_pct=r["extra"]["maxdd"] / ACCOUNT * 100.0)
                cells.append(rep)
                if ck != base_ck and sh == sh:
                    cand.append((ck, dict(r, sharpe=sh), g, nbm))
            full = [c for c in cand if all(c[2][x] for x in "abcdg")]
            win = run.pick(full)
            wr = run.rep_cell(win, bsh)
            active = bool(wr and wr["sharpe"] > bsh and wr["usd"] >= busd)
            low = bool(a.get("n_all") and a["n"] / a["n_all"] < a80.COVER_MIN)       # §13.73.14: столбец определён менее чем у COVER_MIN кандидатов — активным не бывает
            nw = a.get("n_win")
            if nw:                                  # t697 (PS-993 (в)/(д)): охрана — минимум доли по окнам (окно без кандидатов не считается)
                low = any(n_all and n / n_all < a80.COVER_MIN for n, n_all in nw.values())
            return dict(axis=a["axis"], kind=a["kind"], base=a["base"], cells=cells, guarded=len(full), winner=wr, active=active and not low, gain=(wr["sharpe"] - bsh) if wr else None,
                        n=a.get("n"), n_all=a.get("n_all"), low_cover=low, **({"n_win": nw} if nw else {}))

        out_axes = []
        for a in lv["axes"]:
            oa = eval_axis(a)
            old = a80.GRID_OLD.get(a["axis"]) if a80.SET == "grid" else None
            if old is not None:                        # §13.73.9: вердикт оси и по прежней сетке (соседи плато — прежние)
                po = eval_axis(dict(a, vals=[v for v in a["vals"] if v[0] in old[side == "S"]]))
                oa.update(old_active=po["active"], old_winner=po["winner"] and next(c["label"] for c in po["cells"] if c.get("ckey") == po["winner"]["ckey"]))
            out_axes.append(oa)
        missing = missing[0]
        if missing:
            raise SystemExit("%s: в БД нет %d клеток единичек" % (side, missing))
        def m_of(uni):                              # §13.73.18: канонический ключ клетки — один раз (клетки TK-674 и TK-680 на c539 в одном пространстве ключей; база есть в обоих)
            return store.c.execute("SELECT count(DISTINCT ckey) FROM metric_cur WHERE ckey IS NOT NULL AND side = ? AND uni = ? AND win = ?", (ENGNAME[side], uni, run.WIN)).fetchone()[0]
        m_c = m_of(run.UNI)
        m_h = m_of("h775") if run.UNI != "h775" else 0      # §13.73.17: состав c539 выбран по результату h775 — клетки h775 в счётчике испытаний
        m_side = m_c + m_h
        shs = np.array([rows[k]["sharpe"] for k in lv["cells"] if k in rows and rows[k]["sharpe"] is not None])
        sd = float(shs.std()) if len(shs) > 1 else float("nan")
        by_sig = {}
        for k in lv["cells"]:
            if k in rows:
                by_sig.setdefault(rows[k]["extra"].get("trades_sig"), []).append(rows[k]["cell"])
        bsig = brow["extra"].get("trades_sig")
        bx = brow["extra"]
        out = dict(side=side, level=1, base=dict(ckey=base_ck, sharpe=bsh, usd=busd, n=brow["trades"], maxdd=bx["maxdd"], maxdd_pct=bx["maxdd_pct"], kpi=bx["kpi"],
                                                 **run.loss_fields(bx)),
                   axes=out_axes, n_cells=len(shs), m=m_side, m_c=m_c, m_h=m_h, sharpe_sd=sd, noise_max=sd * float(np.sqrt(2 * np.log(max(m_side, 2)))), noise_max_c=sd * float(np.sqrt(2 * np.log(max(m_c, 2)))),
                   same_as_base=sorted(by_sig.get(bsig, [])), skipped=lv["skipped"], invalid=inv,
                   thr_sha256=_sha(os.path.join(run.ROOT, "res", THR_NAME)))
        json.dump(out, open(os.path.join(run.ROOT, "res", "map674b1-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False)
        print("== %s: осей %d, клеток %d, m %d (%s %d + h775 %d), шум ≈ %.2f; активных осей %d (ключи %d, столбцы %d); клеток со сделками базы %d" % (
            side, len(out_axes), len(shs), m_side, run.UNI, m_c, m_h, out["noise_max"], sum(a["active"] for a in out_axes), sum(a["active"] for a in out_axes if a["kind"] == "key"),
            sum(a["active"] for a in out_axes if a["kind"] == "col"), len(out["same_as_base"])), flush=True)
    print("map единичек готово")


def _f(x, nd=2):
    return "—" if x is None or x != x else ("%+.*f" % (nd, x)).replace(".", ",")


def _line(lab, c, bsh):
    g = c.get("guards")
    return "| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        lab, c["n"], _f(c["usd"], 0), _f(c["mean_usd"]), _f(c["sharpe"]).lstrip("+"), _f(c["sharpe"] - bsh), _f(-c["maxdd"], 0), _f(-c["maxdd_pct"], 1),
        "%.0f" % c["gap_longest"], "%.1f" % c["gap_mean"], "%.0f" % c["p90"], "%.0f" % c["tail"],
        "якорь" if g is None else ("да" if all(g[x] for x in "abcd") else "нет: " + "".join(x for x in "abcd" if not g[x])) + (", плато" if g and g["g"] else ""))


def cmd_md(d, out=None):
    """Таблицы единичек: у каждой оси первой строкой якорь (база стороны), ниже — все значения оси."""
    L = []
    for side, sname in (("L", "Лонг"), ("S", "Шорт")):
        m = json.load(open(os.path.join(d, "map674b1-%s.json" % side), encoding="utf-8"))
        b = m["base"]
        bk = b["kpi"]
        anchor = dict(n=b["n"], usd=b["usd"], mean_usd=b["mean_usd"], sharpe=b["sharpe"], maxdd=b["maxdd"], maxdd_pct=b["maxdd_pct"], gap_longest=bk["gap_longest"],
                      gap_mean=bk["gap_mean"], p90=bk["p90"], tail=bk["gap_tail"])
        act = [a for a in m["axes"] if a["active"]]
        L.append("## %s\n" % sname)
        L.append("База (якорь): стоп %g %%, ставка 500 $. Клеток единичек %d, испытаний стороны m = %d, шум отбора (σ Sharpe клеток × √(2 ln m)) ≈ %s. Активных осей %d из %d. Найдено на этих данных.\n" % (
            b["s"], m["n_cells"], m["m"], ("%.2f" % m["noise_max"]).replace(".", ","), len(act), len(m["axes"])))
        L.append("Перехай (В-169, метод TK-663, `gap_longest`): сутки между новыми максимумами кривой по закрытию, UTC — самый длинный (включая отрезок от начала окна до первого максимума и открытый хвост), средний, p90 суток под водой, хвост на конец окна; это не `kpi.longest` В-120 в картах TK-674 и PS-979 (там 12 / 40). Убыток на полный стоп: %s $%s; реализованный средний убыток по стопам — другая величина.\n" % (
            ("%.1f" % b["lstop"]).replace(".", ","), " (обе части A + B; только часть A ≈ %s $)" % ("%.1f" % (b["lstop"] / 2)).replace(".", ",") if "e2=" in b["ckey"] else ""))
        if m.get("m_h"):                                # §13.73.17: вселенная c539 — состав выбран по результату h775, пороги — по кандидатам h775 с TradFi
            L.append("Вселенная без TradFi (c539): состав выбран на этих данных (по результату h775); пороги столбцов подобраны на этих данных (кандидаты h775, с TradFi). m = %d — клетки c539 %d + клетки h775 %d; только c539: m = %d, шум ≈ %s.\n" % (
                m["m"], m["m_c"], m["m_h"], m["m_c"], ("%.2f" % m["noise_max_c"]).replace(".", ",")))
        L.append("### Активные оси (%s)\n" % sname.lower())
        L.append("| Ось | Значение | Сделок | Σ $ | $ на сделку | Sharpe | Δ Sharpe | Просадка $ | Просадка, % маржи | Перехай: самый длинный, сут | средний | p90 | хвост | Охраны |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        L.append("| — " + _line("база", anchor, b["sharpe"]))
        for a in sorted(act, key=lambda a: -a["gain"]):
            w = a["winner"]
            lab = next(c["label"] for c in a["cells"] if c.get("ckey") == w["ckey"])
            L.append("| `%s` " % a["axis"] + _line(lab, dict(w, guards=dict(a=1, b=1, c=1, d=1, g=1)), b["sharpe"]))
        L.append("\n### Все оси (%s)\n" % sname.lower())
        for a in m["axes"]:
            note = " — активная" if a["active"] else ""
            if a.get("n_all"):
                note += "; определён у %d из %d кандидатов базы (%s %%)%s" % (a["n"], a["n_all"], ("%.0f" % (100.0 * a["n"] / a["n_all"])), ", активным не бывает (определён менее чем у %d %% кандидатов%s)" % (round(100 * a80.COVER_MIN), " хотя бы в одном окне" if a.get("n_win") else "") if a.get("low_cover") else "")
                if a.get("n_win"):
                    note += "; по окнам: " + ", ".join("%s %d из %d (%.0f %%)" % (w, n, n_all, 100.0 * n / n_all if n_all else 0.0) for w, (n, n_all) in a["n_win"].items())
            if "old_active" in a:                      # §13.73.9: вердикт по прежней сетке рядом
                note += "; по прежней сетке — %s%s" % ("активная (%s)" % a["old_winner"] if a["old_active"] else "не активная",
                                                       ", смена — от новых соседей плато" if a["old_active"] != a["active"] else "")
            L.append("**`%s`**%s\n" % (a["axis"], note))
            L.append("| Значение | Сделок | Σ $ | $ на сделку | Sharpe | Δ Sharpe | Просадка $ | Просадка, % маржи | Перехай: самый длинный, сут | средний | p90 | хвост | Охраны |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            L.append(_line("якорь (база)", anchor, b["sharpe"]))
            for c in a["cells"]:
                if c.get("invalid"):
                    L.append("| %s | — | движок отверг: %s |" % (c["label"], c["invalid"]))
                elif not c.get("is_base"):
                    lab = c["label"] + ((" (порог %s, убрано %d)" % (ax.num(c["thr"]).replace(".", ","), c["cut"])) if "thr" in c else "")
                    if "nan_n" in c:
                        lab += "; с пустым значением: %d сд., %s $" % (c["nan_n"], _f(c["nan_usd"], 0))
                    if "lvl_floored" in c:
                        lab += "; стоп сдвинут зажимом sm: %d сд., вплотную к входу: %d сд." % (c["lvl_floored"], c["lvl_clamped"])
                    L.append(_line(lab, c, b["sharpe"]))
            if a["axis"] == "p":
                L.append("\nМаржа в работе (позиция 250 $, счёт 2 500 $): " + "; ".join("p = %s — одновременно до %d позиций, %d $ (%d %% счёта), просадка %s %% счёта" % (
                    c["label"], c["conc"], c["conc_margin"], c["conc_margin_pct"], ("%.1f" % c["maxdd_acct_pct"]).replace(".", ",")) for c in a["cells"] if "conc" in c) + ".")
            L.append("")
        if m["skipped"]:
            L.append("Столбцы без оси (%s): %s.\n" % (sname.lower(), "; ".join("`%s` — %s" % (c, w) for c, w in m["skipped"])))
    txt = "\n".join(L) + "\n"
    if out:
        open(out, "w", encoding="utf-8", newline="\n").write(txt)
    else:
        sys.stdout.write(txt)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "select":
        cmd_select()
    elif cmd == "map":
        cmd_map()
    elif cmd == "md" and len(sys.argv) >= 3:
        cmd_md(sys.argv[2], sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None)
    else:
        raise SystemExit(__doc__)
