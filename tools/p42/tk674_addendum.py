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
    """Ключи (монета, sig_ms) сделок клетки по окнам; каталог клетки — из корня (извлекается из архива партии, если упакован)."""
    import csv
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tk674_pack as pk
    out = {}
    for w in WIN:
        ps = glob.glob(os.path.join(root, "cells", "%s-S" % w, "*", cid, "short-trades.csv"))
        if not ps and nm:
            ps = [os.path.join(pk.ensure_cell(os.path.join(root, "cells", "%s-S" % w, nm, cid)), "short-trades.csv")]
        if not ps:
            return None
        with open(ps[0], encoding="utf-8") as fh:
            out[w] = {(r["sym"], int(r["sig_ms"])) for r in csv.DictReader(fh)}
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
    p("")
    p("## Классы cc (PS-994) и bd (PS-995): X = доля сделок клетки с NaN у признака фильтра, по окнам")
    p("")
    p("| класс | клеток | со сделками во всех окнах | X > 0 хотя бы в одном окне | X > 50 % хотя бы в одном окне | максимум X по окнам wa / wb / wc, % | медиана X по окнам, % |")
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
        md = {w: sorted(x[w] for x in xs.values() if x[w] is not None)[len([1 for x in xs.values() if x[w] is not None]) // 2] if any(x[w] is not None for x in xs.values()) else 0 for w in WIN}
        p("| %s | %d (каталогов %d) | %d | %d | %d | %s | %s |" % (nm, len(cls[nm]), len(xs), len(full), len(gt0), len(gt50),
                                                              " / ".join("%.1f" % mx[w] for w in WIN), " / ".join("%.1f" % md[w] for w in WIN)))
    open(a.out, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
