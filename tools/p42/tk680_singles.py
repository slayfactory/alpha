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
THR_NAME = "thr680h.json" if a80.SET == "hist" else "thr680.json"     # пороги набора «история пробоев» (§13.73.8) — свой файл, заморозка thr680.json не трогается


def coverage_cols():
    if a80.SET == "hist":
        return list(a80.HIST_COLS)
    return json.load(open(COLS_FILE, encoding="utf-8"))


def add_columns(side, st, tabs):
    """Столбцы таблицы TK-671 к строкам кандидатов базы (ключ окно + sym + sig_ms, строка с meta_base_pass > 0, бит 1 раньше бита 2); нет строки — пусто (проходит)."""
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq
    have = set(pq.ParquetFile(a80.PARQUET % ENGNAME[side]).schema.names)
    extra = set()
    if a80.SET == "hist":
        extra = set(pq.ParquetFile(a80.HIST_EXTRA % ENGNAME[side]).schema.names) - have
        have |= extra
        rid = pd.read_parquet(a80.PARQUET % ENGNAME[side], columns=["row_id"])["row_id"].to_numpy()
    cl = a80.column_list(coverage_cols())
    srcs = lambda src: src if isinstance(src, tuple) else (src,)
    absent = [name for name, src, _ in cl if not all(x in have for x in srcs(src)) and name not in tabs[st.TRADE_WINDOWS[0].name]]
    cl = [c for c in cl if c[0] not in absent]
    need = sorted({x for _, src, _ in cl for x in srcs(src) if x in have})
    df = pd.read_parquet(a80.PARQUET % ENGNAME[side], columns=["win", "sym", "meta_sig_ms", "meta_base_pass"] + [x for x in need if x not in extra])
    if extra:                                       # ok10_d3 / d5: файл tk680_hist.py, строки в порядке таблицы TK-671 (ключ сверяется)
        ex = pd.read_parquet(a80.HIST_EXTRA % ENGNAME[side])
        if len(ex) != len(df) or not ((ex["win"].astype(str).to_numpy() == df["win"].astype(str).to_numpy()) & (ex["row_id"].to_numpy() == rid)).all():
            raise SystemExit("таблица ok10_d3 / d5 %s: строки не совпадают с таблицей TK-671" % side)
        for x in sorted(extra & set(need)):
            df[x] = ex[x].to_numpy()
    df = df[df["meta_base_pass"] > 0].copy()
    df["win"], df["sym"] = df["win"].astype(str), df["sym"].astype(str)
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


def _thresholds(v):
    """[(метка, порог ≤), …], [(метка, порог ≥), …] столбца по значениям кандидатов базы в зачётном диапазоне."""
    import numpy as np
    import tk638_run as t8
    u = np.unique(v)
    if len(u) <= 1:
        return [], []
    if a80.SET == "hist":                           # числа пробоев: пороги заданы поправкой §13.73.8, не данными
        return list(a80.HIST_THR[0]), list(a80.HIST_THR[1])
    if len(u) <= 4:
        return [("v%s" % ax.num(x), float(x)) for x in u[:-1]], [("v%s" % ax.num(x), float(x)) for x in u[1:]]
    out, seen = [], set()
    for q in a80.QS:
        x = t8.sig2(float(np.quantile(v, q / 100, method="linear")))
        if x not in seen:
            seen.add(x)
            out.append(("q%d" % q, x))
    return out, list(out)


def cmd_select():
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
        for name, vals, base in ([] if a80.SET == "hist" else a80.engine_axes(side)):
            axes.append(dict(axis=name, kind="key", base=base, vals=[[lab, base_ck if lab == base else put(name, lab, patch)] for lab, patch in vals]))
        tabs = run.feat_tabs(side, st, u, eps)
        sc = {w.name: r54.in_scored_range(w, tabs[w.name]["ms"]) for w in st.TRADE_WINDOWS}
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
            le, ge = _thresholds(fin)
            if not le and not ge:
                skipped.append([cname, "одно значение у кандидатов базы"])
                continue
            thr_out[side][cname] = dict(n=int(len(fin)), n_all=int(len(allv)), le=le, ge=ge)
            for d in dirs:
                vals = []
                for lab, x in (le if d == "le" else ge):
                    cut = int(((fin > x) if d == "le" else (fin < x)).sum())
                    if cut == 0:                    # порог никого не убирает — клетка равна базе
                        continue
                    vals.append([lab, put("%s.%s" % (cname, d), lab, {}, (cname, OPS[d], x)), x, cut])
                if vals:
                    vals = (vals + [["off", base_ck]]) if d == "le" else ([["off", base_ck]] + vals)      # «off» рядом с самым мягким порогом
                    axes.append(dict(axis="%s.%s" % (cname, d), kind="col", base="off", vals=vals))
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
        rows = store.cells_lookup(ENGNAME[side], run.UNI, run.WIN, list(lv["cells"]) + [base_ck], bins=bins)
        brow = rows[base_ck]
        bsh, busd = brow["sharpe"], brow["usd"]
        out_axes, missing = [], 0
        for a in lv["axes"]:
            labs = [v[0] for v in a["vals"]]
            cks = [v[1] for v in a["vals"]]
            cand, cells = [], []
            for i, (lab, ck) in enumerate(zip(labs, cks)):
                r = rows.get(ck)
                if r is None:
                    why = inv.get(lv["cells"][ck]["cid"]) if ck in lv["cells"] else None
                    missing += int(why is None)
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
            out_axes.append(dict(axis=a["axis"], kind=a["kind"], base=a["base"], cells=cells, guarded=len(full), winner=wr, active=active, gain=(wr["sharpe"] - bsh) if wr else None))
        if missing:
            raise SystemExit("%s: в БД нет %d клеток единичек" % (side, missing))
        m_side = sum(r["n"] for r in store.cells_count() if r["side"] == ENGNAME[side] and r["uni"] == run.UNI and r["win"] == run.WIN)
        shs = np.array([rows[k]["sharpe"] for k in lv["cells"] if k in rows and rows[k]["sharpe"] is not None])
        sd = float(shs.std()) if len(shs) > 1 else float("nan")
        by_sig = {}
        for k in lv["cells"]:
            if k in rows:
                by_sig.setdefault(rows[k]["extra"].get("trades_sig"), []).append(rows[k]["cell"])
        bsig = brow["extra"].get("trades_sig")
        bx = brow["extra"]
        out = dict(side=side, level=1, base=dict(ckey=base_ck, sharpe=bsh, usd=busd, n=brow["trades"], mean_usd=bx["mean_usd"], maxdd=bx["maxdd"], maxdd_pct=bx["maxdd_pct"], kpi=bx["kpi"],
                                                 **run.loss_fields(bx)),
                   axes=out_axes, n_cells=len(shs), m=m_side, sharpe_sd=sd, noise_max=sd * float(np.sqrt(2 * np.log(max(m_side, 2)))),
                   same_as_base=sorted(by_sig.get(bsig, [])), skipped=lv["skipped"], invalid=inv,
                   thr_sha256=_sha(os.path.join(run.ROOT, "res", THR_NAME)))
        json.dump(out, open(os.path.join(run.ROOT, "res", "map674b1-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False)
        print("== %s: осей %d, клеток %d, m %d, шум ≈ %.2f; активных осей %d (ключи %d, столбцы %d); клеток со сделками базы %d" % (
            side, len(out_axes), len(shs), m_side, out["noise_max"], sum(a["active"] for a in out_axes), sum(a["active"] for a in out_axes if a["kind"] == "key"),
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
        L.append("### Активные оси (%s)\n" % sname.lower())
        L.append("| Ось | Значение | Сделок | Σ $ | $ на сделку | Sharpe | Δ Sharpe | Просадка $ | Просадка, % маржи | Перехай: самый длинный, сут | средний | p90 | хвост | Охраны |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        L.append("| — " + _line("база", anchor, b["sharpe"]))
        for a in sorted(act, key=lambda a: -a["gain"]):
            w = a["winner"]
            lab = next(c["label"] for c in a["cells"] if c.get("ckey") == w["ckey"])
            L.append("| `%s` " % a["axis"] + _line(lab, dict(w, guards=dict(a=1, b=1, c=1, d=1, g=1)), b["sharpe"]))
        L.append("\n### Все оси (%s)\n" % sname.lower())
        for a in m["axes"]:
            L.append("**`%s`**%s\n" % (a["axis"], " — активная" if a["active"] else ""))
            L.append("| Значение | Сделок | Σ $ | $ на сделку | Sharpe | Δ Sharpe | Просадка $ | Просадка, % маржи | Перехай: самый длинный, сут | средний | p90 | хвост | Охраны |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            L.append(_line("якорь (база)", anchor, b["sharpe"]))
            for c in a["cells"]:
                if c.get("invalid"):
                    L.append("| %s | — | движок отверг: %s |" % (c["label"], c["invalid"]))
                elif not c.get("is_base"):
                    lab = c["label"] + ((" (порог %s, убрано %d)" % (ax.num(c["thr"]).replace(".", ","), c["cut"])) if "thr" in c else "")
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
