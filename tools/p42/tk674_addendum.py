#!/usr/bin/env python3
"""TK-674: данные для дополнения к L-353 по PS-994 + PS-995 (одна таблица «L-353 → после пересчёта»).

  srv (код code-xbc, окружение /tmp/env3.sh): TK674_UNI=c539 TK674_SIDES=S python3 tk674_addendum.py [--out файл.md]

Читает: карту до (`/data/p42-tk674c/res/map674bsx-S.json`) и после (`/data/p42-tk674c-x690b/res/map674bsx-S.json` + `pair-mapbsx-S-xb.md`), списки пересчёта PS-994 (`-x690/tmp/recount.json`, метка TK690-recount)
и PS-995 (`-x690b/tmp/bd.json`, метка TK690-xb), строки клеток из БД, счётчики фильтра движка `filter_[cands,dropped,no_key,no_value]` из meta.json клеток.
«Фильтр не определён на X %» = (no_key + no_value) / (cands − dropped): доля допущенных фильтром кандидатов, прошедших по NaN / без строки таблицы (в движке NaN и «нет ключа» проходят, src/breakout/ftab.rs); по окнам wa / wb / wc.
Это доля допущенных кандидатов, а не сделок (сделки — подмножество); по сделкам — у финальных кандидатов (PS-993 (д))."""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_run as run  # noqa: E402

R, R2, R5 = "/data/p42-tk674c", "/data/p42-tk674c-x690", "/data/p42-tk674c-x690b"
WIN = ("wa", "wb", "wc")
M_L353 = 76430                                               # счётчик испытаний L-353 (шорт): как в карте до


def noise(m, sd):
    return sd * math.sqrt(2 * math.log(max(m, 2)))


FAMS = {"bd": "/data/p42-tk674c-xb/fam/bd", "cc": "/data/p42-tk690/fam/coin"}      # признаки: bd — пересчёт PS-995 (ω с 01.07), cc — TK-690 (fam/coin)
PROD = "/data/p42-tk690/prod"


def feats(cid):
    """Признаки cc_* / bd_* в имени клетки: cc_1h.le_q40 → cc_1h."""
    return sorted(set(re.findall(r"((?:cc|bd)_[a-z0-9]+)\.(?:le|ge)", cid)))


def nan_keys(w):
    """{признак: {(монета, sig_ms) строк окна w, где признак NaN}} по fam/bd (xb) и fam/coin (cc_*); и число строк окна."""
    import pandas as pd
    rws = pd.read_parquet(os.path.join(PROD, "lvl-%s-short" % w, "rows.parquet"))[["row_id", "sym", "sig_ms"]]
    key = {int(r): (str(sy), int(ms)) for r, sy, ms in zip(rws["row_id"], rws["sym"], rws["sig_ms"])}
    out = {}
    for fam in FAMS.values():
        d = pd.read_parquet(os.path.join(fam, "%s-short.parquet" % w))
        for c in d.columns:
            if c.startswith(("cc_", "bd_")):
                out[c] = {key[int(i)] for i in d.index[d[c].isna().to_numpy()]}
    return out, len(rws)


def trades_of(root, cid, nm=None):
    """Ключи (монета, sig_ms) зачётных сделок клетки по окнам (вход в зачётных сутках окна, как в БД); каталог клетки — из корня (извлекается из архива партии, если упакован)."""
    import csv
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tk604_stats as st
    import tk674_pack as pk
    wins = {x.name: x for x in st.TRADE_WINDOWS}
    out = {}
    for w in WIN:
        ps = glob.glob(os.path.join(root, "cells", "%s-S" % w, "*", cid, "short-trades.csv"))
        if not ps and nm:
            ps = [os.path.join(pk.ensure_cell(os.path.join(root, "cells", "%s-S" % w, nm, cid)), "short-trades.csv")]
        if not ps:
            return None
        with open(ps[0], encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        day = st.abs_day(wins[w], [int(r["entry_ms"]) for r in rows])
        lo, hi = (wins[w].lod - st.D0).days, (wins[w].hid - st.D0).days
        out[w] = {(r["sym"], int(r["sig_ms"])) for r, d in zip(rows, day) if lo <= d <= hi}
    return out


FAST_COLS = ("sac10", "sac60", "accdv1_300")
FAST_CSV = "/data/p42-tk690/fam/rust/%s-short/fast.csv"      # сырые признаки FAST по ключу (монета, sig_ms): пусто = NaN; в таблице фильтров NaN уже свёрнут в флаг 1 (проходит)


def fast_feats(cid):
    """Признаки FAST в имени клетки: s4+sac10.le@t_q60 → sac10."""
    return sorted(set(re.findall(r"(%s)\.(?:le|ge)@t" % "|".join(FAST_COLS), cid)))


def fast_nan(w):
    """(ключи (монета, sig_ms) строк `fast.csv` окна w, {столбец: ключи с NaN})."""
    import pandas as pd
    d = pd.read_csv(FAST_CSV % w, usecols=["sym", "sig_ms", *FAST_COLS])
    ms = d["sig_ms"].astype("int64")
    return set(zip(d["sym"], ms)), {c: set(zip(d["sym"][d[c].isna()], ms[d[c].isna()])) for c in FAST_COLS}


PARQ_S = "/data/p42-tk671/data/short.parquet"


def col_of(tok):
    """Столбец фильтра в токене имени клетки: sac10.le@t_q60 → sac10, N24_q20 → N24; не фильтр-столбец → None."""
    m = re.match(r"^(.*?)(?:\.(?:le|ge))?(?:@t)?_q\d+$", tok)
    return m.group(1) if m else None


def cols_of(cid):
    return [c for c in (col_of(t) for t in cid.split("+")) if c]


def t671_empty(cols):
    """{окно: (ключи (монета, sig_ms) строк TK-671 шорта (meta_base_pass > 0, как в `tk674_emptycols.rows_report`), {столбец: ключи с NaN})} для столбцов, лежащих в parquet напрямую."""
    import pandas as pd
    import pyarrow.parquet as pq
    cols = [c for c in cols if c in pq.ParquetFile(PARQ_S).schema.names]
    df = pd.read_parquet(PARQ_S, columns=["win", "sym", "meta_sig_ms", "meta_base_pass", *cols])
    df = df[df["meta_base_pass"] > 0].copy()
    df["win"], df["sym"] = df["win"].astype(str), df["sym"].astype(str)
    df["_pri"] = (df["meta_base_pass"] != 1).astype(int)
    df = df.sort_values("_pri", kind="stable").drop_duplicates(["win", "sym", "meta_sig_ms"])
    out = {}
    for w in WIN:
        d = df[df["win"] == w]
        keys = list(zip(d["sym"], d["meta_sig_ms"].astype("int64")))
        out[w] = (set(keys), {c: {k for k, z in zip(keys, d[c].isna().to_numpy()) if z} for c in cols})
    return out


def x_share(tr, nk, fs):
    """{окно: (сделок, из них с NaN хотя бы у одного признака fs)} — по ключам сделок «клетки без cc / bd» (то, что фильтр мог бы допустить)."""
    return {w: (len(tr[w]), sum(1 for k in tr[w] if any(k in nk[w][0].get(f, ()) for f in fs))) for w in WIN}


def fmt_x(x):
    return " / ".join("—" if n == 0 else "%d из %d (%.0f %%)" % (k, n, 100.0 * k / n) for n, k in (x[w] for w in WIN))


def md_rows(path):
    """Строки таблиц карты после пересчёта: [(раздел, имя клетки, вся строка)]."""
    sec, out = None, []
    for ln in open(path, encoding="utf-8"):
        if ln.startswith("### А."):
            sec = "А"
        elif ln.startswith("### Б."):
            sec = "Б"
        elif ln.startswith("### Синергия"):
            sec = "синергия"
        m = re.match(r"\| `([^`]+)` \|", ln)
        if m and sec:
            out.append((sec, m.group(1), ln.rstrip("\n")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(R5, "res", "addendum-ps994-995.md"))
    a = ap.parse_args()
    old = json.load(open(os.path.join(R, "res", "map674bsx-S.json"), encoding="utf-8"))
    new = json.load(open(os.path.join(R5, "res", "map674bsx-S.json"), encoding="utf-8"))
    recount = set(json.load(open(os.path.join(R2, "tmp", "recount.json"), encoding="utf-8")))
    bd = set(json.load(open(os.path.join(R5, "tmp", "bd.json"), encoding="utf-8")))
    plan = json.load(open(os.path.join(R5, "res", "plan674bsx-S.json"), encoding="utf-8"))["cells"]
    ck = {c["cid"]: k for k, c in plan.items()}
    store = run.chron_store(ro=True)
    bins = run.eq_bins()
    rows = {}
    for win, cids in (("TK690-recount", recount), ("TK690-xb", bd)):
        keys = [ck[c] for c in cids if c in ck]
        got = store.cells_lookup(run.SIDENAME["S"], run.UNI, win, keys, bins=bins)
        for k, r in got.items():
            rows[plan[k]["cid"]] = r
    out = []
    p = out.append
    n_old, n_new = noise(M_L353, new["sharpe_sd"]), noise(new["m"], new["sharpe_sd"])
    p("## Сводка: карта шорта шага 3 до и после пересчёта")
    p("")
    p("| | L-353 (до) | после пересчёта |")
    p("|---|---|---|")
    p("| клеток карты | %d | %d (+%d: клетки PS-994 и 180 bd PS-995 снова в карте) |" % (old["n_cells"], new["n_cells"], new["n_cells"] - old["n_cells"]))
    p("| m (счётчик испытаний) | %d | %d (счётчик БД вырос от остатка троек шорта r02–r10, не от пересчёта) |" % (old["m"], new["m"]))
    p("| σ Шарпа клеток | %.2f | %.2f |" % (old["sharpe_sd"], new["sharpe_sd"]))
    p("| верхняя оценка шума | %.2f | %.2f при m L-353 (%d), %.2f при текущем m |" % (old["noise_max"], n_old, M_L353, n_new))
    allr = [(cid, r) for cid, r in rows.items() if r["sharpe"] == r["sharpe"]]
    p("")
    p("## Пересчитанные клетки (%d из %d с метками TK690-recount / TK690-xb имеют Шарп)" % (len(allr), len(recount) + len(bd)))
    cls = {"cc": [c for c in recount if "cc_" in c], "bd": sorted(bd), "A/B (остальные)": [c for c in recount if "cc_" not in c]}
    for nm, cl in cls.items():
        sh = [rows[c]["sharpe"] for c in cl if c in rows and rows[c]["sharpe"] == rows[c]["sharpe"]]
        usd = [rows[c]["usd"] for c in cl if c in rows]
        p("- **%s:** клеток %d, в БД %d; Шарп макс %.2f, Σ $ макс %+.0f; клеток с Шарпом выше базы 3,63 + шум: при m L-353 %d, при текущем m %d" % (
            nm, len(cl), len(sh), max(sh) if sh else float("nan"), max(usd) if usd else float("nan"),
            sum(1 for s in sh if s - 3.63 > n_old), sum(1 for s in sh if s - 3.63 > n_new)))
    mr = md_rows(os.path.join(R5, "res", "pair-mapbsx-S-xb.md"))
    nk = {w: nan_keys(w) for w in WIN}
    nm_of = {}                                                    # cid → хеш партии в основном корне (шаги 2–3): для извлечения сделок клетки без cc / bd
    for suf in ("bsx", "bs"):
        pc = json.load(open(os.path.join(R, "res", "plan674%s-S.json" % suf), encoding="utf-8"))["cells"]
        for h, cks in json.load(open(os.path.join(R, "res", "batches674%s-S.json" % suf), encoding="utf-8")).items():
            for k in cks:
                if k in pc:
                    nm_of.setdefault(pc[k]["cid"], h)
    p("")
    p("## Признаки cc_* / bd_*: доля строк окна, где признак NaN (все кандидатные строки окна)")
    p("")
    p("| признак | wa | wb | wc |")
    p("|---|---|---|---|")
    used = sorted({f for c in list(cls["cc"]) + list(cls["bd"]) for f in feats(c)})
    for f in used:
        p("| %s | %s |" % (f, " | ".join("%.1f %% (%d из %d)" % (100.0 * len(nk[w][0].get(f, ())) / nk[w][1], len(nk[w][0].get(f, ())), nk[w][1]) for w in WIN)))
    p("")
    p("## Прочитанные строки карты (таблицы А, Б, синергия): пересчитанные и с cc_* / bd_* в фильтре")
    p("")
    p("«Фильтр не определён на X % сделок» = доля сделок клетки (по окнам wa / wb / wc), у которых признак фильтра NaN. Справа — сделки клетки без cc / bd-условия: сколько из них с NaN и сколько таких допущено фильтром.")
    p("")
    p("| раздел | клетка | пересчитана | X по сделкам клетки wa / wb / wc | клетка без фильтра: сделок с NaN → допущено фильтром |")
    p("|---|---|---|---|---|")
    n_rec = n_cc = 0
    for sec, nm, ln in mr:
        isrec = nm in recount or nm in bd
        fs = feats(nm)
        if not (isrec or fs):
            continue
        n_rec += isrec
        n_cc += bool(fs)
        what = "PS-995 (bd)" if nm in bd else ("PS-994" if nm in recount else "нет")
        if not fs:
            p("| %s | `%s` | %s | без cc / bd | |" % (sec, nm, what))
            continue
        own = trades_of(R5 if nm in bd else (R2 if nm in recount else R), nm, nm_of.get(nm))
        cp = "+".join(x for x in nm.split("+") if not re.match(r"(cc|bd)_", x))
        cpt = trades_of(R, cp, nm_of.get(cp)) if cp in nm_of else None
        xo = fmt_x(x_share(own, nk, fs)) if own else "нет каталога клетки"
        if cpt and own:
            xc = " / ".join("%d → %d" % (sum(1 for k in cpt[w] if any(k in nk[w][0].get(f, ()) for f in fs)),
                                         sum(1 for k in own[w] if any(k in nk[w][0].get(f, ()) for f in fs))) for w in WIN)
        else:
            xc = "клетка без фильтра `%s` не найдена" % cp
        p("| %s | `%s` | %s | %s | %s |" % (sec, nm, what, xo, xc))
    p("")
    p("Строк карты (А + Б + синергия) %d; пересчитанных среди них %d; с cc_* / bd_* в фильтре %d." % (len(mr), n_rec, n_cc))
    syn = [(nm, nm in recount or nm in bd, feats(nm)) for sec, nm, ln in mr if sec == "синергия"]
    p("Синергия: строк %d; без cc / bd %d; с cc %d, с bd %d; пересчитанных (в списке PS-994 / PS-995) %d — cc-строки синергии прежние клетки, не из списка пересчёта." % (
        len(syn), sum(1 for _, _, f in syn if not f), sum(1 for _, _, f in syn if any(x.startswith("cc_") for x in f)),
        sum(1 for _, _, f in syn if any(x.startswith("bd_") for x in f)), sum(1 for _, r, _ in syn if r)))
    p("")
    p("## Строки таблиц А / Б с фильтром FAST (sac10 / sac60 / accdv1_300): сделки на строках, где признак пуст (NaN в `fast.csv`, фильтр такую строку пропускает: флаг таблицы 1)")
    p("")
    p("| клетка | таблицы | пересчитана | сделок (зачётных) | на пустых строках | Σ $ с / без них | Шарп с / без них |")
    p("|---|---|---|---|---|---|---|")
    fz = {w: fast_nan(w) for w in WIN}
    want = {}
    for sec, nm, ln in mr:
        if sec in ("А", "Б") and fast_feats(nm):
            want.setdefault(nm, []).append(sec)
    import tk674_emptycols as ec
    res_ec = {}
    for root, sel in ((R2, [c for c in want if c in recount]), (R, [c for c in want if c not in recount])):
        res_ec.update(ec.without_cells("S", sel, lambda w, sym, ms, c: (sym, ms) not in fz[w][0] or any((sym, ms) in fz[w][1][f] for f in fast_feats(c)), root))
    for nm, secs in want.items():
        e = res_ec.get("S:" + nm, {})
        if "error" in e or not e:
            p("| `%s` | %s | %s | нет каталога клетки | | | |" % (nm, "+".join(secs), "PS-994" if nm in recount else "нет"))
            continue
        p("| `%s` | %s | %s | %d | %d (%.0f %%) | %+.1f / %+.1f | %.2f / %.2f |" % (nm, "+".join(secs), "PS-994" if nm in recount else "нет", e["n"], e["n_empty"],
                                                                                  100.0 * e["n_empty"] / max(e["n"], 1), e["usd"], e["usd_without"], e["sharpe"], e["sharpe_without"]))
    p("")
    p("Доля строк окна с пустым признаком FAST (все кандидатные строки таблицы): " + "; ".join(
        "%s %s" % (f, " / ".join("%.1f %%" % (100.0 * len(fz[w][1][f]) / max(len(fz[w][0]), 1)) for w in WIN)) for f in FAST_COLS) + " (wa / wb / wc).")
    p("")
    p("## X по всем прочитанным строкам карты (А, Б, синергия) с фильтром по столбцу: доля зачётных сделок на пустых строках признака (NaN или нет строки таблицы), по окнам wa / wb / wc")
    p("")
    secs_of = {}
    for sec, nm, ln in mr:
        secs_of.setdefault(nm, []).append(sec)
    need = sorted({c for nm in secs_of for c in cols_of(nm) if c in ec.AXIS_COLS and not c.startswith(("cc_", "bd_"))})
    t671 = t671_empty(need)
    detail, summ = [], {}
    for nm, secs in secs_of.items():
        cs = cols_of(nm)
        if not cs:
            continue
        tr = trades_of(R5 if nm in bd else (R2 if nm in recount else R), nm, nm_of.get(nm))
        for c in cs:
            if c in nk[WIN[0]][0]:
                src, em = "fam cc / bd", (lambda w, k, c=c: k in nk[w][0][c])
            elif c in FAST_COLS:
                src, em = "fast.csv", (lambda w, k, c=c: k not in fz[w][0] or k in fz[w][1][c])
            elif c in t671[WIN[0]][1]:
                src, em = "TK-671", (lambda w, k, c=c: k not in t671[w][0] or k in t671[w][1][c])
            elif c in ec.A_NAMES:
                src, em = "таблицы TK-654 / TK-638 (полные строки; здесь не считалось)", None
            else:
                src, em = "не считалось (отношение / файл истории)", None
            if em is None or not tr:
                x, xs = None, "нет каталога клетки" if em and not tr else "—"
            else:
                x = {w: (len(tr[w]), sum(1 for k in tr[w] if em(w, k))) for w in WIN}
                xs = fmt_x(x)
            detail.append((nm, "+".join(secs), "PS-994" if nm in recount else ("PS-995" if nm in bd else "нет"), c, src, xs))
            s = summ.setdefault(c, dict(src=src, rows=0, measured=0, gt0=0, gt50=0, mx=0.0))
            s["rows"] += 1
            if x:
                pc = [100.0 * k / n for n, k in x.values() if n]
                s["measured"] += 1
                s["gt0"] += any(v > 0 for v in pc)
                s["gt50"] += any(v > 50 for v in pc)
                s["mx"] = max([s["mx"]] + pc)
    p("| столбец | источник | строк карты | измерено | X > 0 в каком-либо окне | X > 50 % в каком-либо окне | максимум X, % |")
    p("|---|---|---|---|---|---|---|")
    for c, s in sorted(summ.items(), key=lambda kv: (-kv[1]["gt50"], -kv[1]["gt0"], kv[0])):
        p("| %s | %s | %d | %d | %d | %d | %.1f |" % (c, s["src"], s["rows"], s["measured"], s["gt0"], s["gt50"], s["mx"]))
    p("")
    p("| клетка | разделы | пересчитана | столбец | источник | X: wa / wb / wc |")
    p("|---|---|---|---|---|---|")
    for d in detail:
        p("| `%s` | %s | %s | %s | %s | %s |" % d)
    p("")
    p("## Классы cc (PS-994) и bd (PS-995): X = доля сделок клетки с NaN у признака фильтра, по окнам")
    p("")
    p("| класс | клеток | со сделками во всех окнах | X > 0 хотя бы в одном окне | X > 50 % хотя бы в одном окне | максимум X по окнам wa / wb / wc, % | среднее X по клеткам, % |")
    p("|---|---|---|---|---|---|---|")
    for nm in ("cc", "bd"):
        xs = {}
        for c in cls[nm]:
            tr = trades_of(R5 if nm == "bd" else R2, c)
            if tr:
                xs[c] = {w: (100.0 * sum(1 for k in tr[w] if any(k in nk[w][0].get(f, ()) for f in feats(c))) / len(tr[w]) if tr[w] else None) for w in WIN}
        full = {c: x for c, x in xs.items() if all(v is not None for v in x.values())}
        gt0 = [c for c, x in xs.items() if any(v for v in x.values() if v)]
        gt50 = [c for c, x in xs.items() if any(v is not None and v > 50 for v in x.values())]
        mx = {w: max([x[w] for x in xs.values() if x[w] is not None] or [0]) for w in WIN}
        mn = {w: (lambda v: sum(v) / len(v) if v else 0.0)([x[w] for x in xs.values() if x[w] is not None]) for w in WIN}
        p("| %s | %d (каталогов %d) | %d | %d | %d | %s | %s |" % (nm, len(cls[nm]), len(xs), len(full), len(gt0), len(gt50),
                                                              " / ".join("%.1f" % mx[w] for w in WIN), " / ".join("%.1f" % mn[w] for w in WIN)))
    open(a.out, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
