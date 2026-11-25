#!/usr/bin/env python3
"""TK-700 (В-183): набор клеток для прогона на январе–июне 2026 — все клетки c539 с Σ P&L > 1 $ за 01.07–13.11 (ставка 500 $) из БД летописи → таблица `cell` в БД результатов.

  python3 tk700_cells.py extract --cellset all1 [--min-usd 1] [--bins long:<md5>,short:<md5>] [--with-daily] [--db /data/p42-tk700/mass/mass.sqlite]
  python3 tk700_cells.py derive  --cellset all2 --base all1 [--win TK699-f12]       # all1 с подменой по строкам исправленного f12 (TK-699), с посуточным рядом f12
  python3 tk700_cells.py stats   --cellset all1                       # число клеток по сторонам / фильтрам / ключам прохода сигналов, версии бинарей

Источник — `/data/chron/chron.sqlite` (только чтение, `metric` без исправленных строк `metric_fix`): uni = c539, win = 01.07–13.11, usd > порога. Повтор (сторона, ckey) — берётся строка бинаря своей стороны (`--bins`: лонг c400af11 = tk683k, шорт 562f6899 = tk683l), иначе последняя по id;
клетки других бинарей в набор не входят (сверка «байт в байт» только против своего бинаря; их число — `other_bin_skipped` в параметрах набора).
`line` — полная строка варианта движка (`extra.line`); `sig` — ключ прохода сигналов (`tk674_axes.sigkey`: клетки с равным ключом делят проход сигналов в одном вызове движка);
`feats` — признаки фильтра (для охвата по месяцам); `ref_n` / `ref_usd` (и `ref_daily` с --with-daily) — числа БД для сверки на Jul–Nov."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CHRON_DB = "/data/chron/chron.sqlite"
MASS_DB = "/data/p42-tk700/mass/mass.sqlite"
EQBINS = "/data/p42-tk674c/res/eqbins674.json"                                                  # бинари движка с равным счётом клеток TK-674
BINS = "long:c400af112bda3396d807b2ba882aea8b,short:562f689967dde28ebce44f4a00f1b7af"   # бинари БД: лонг — tk683k, шорт — tk683l (у лонга строк 562f6899 нет)
SCHEMA = """
CREATE TABLE IF NOT EXISTS cellset(name TEXT PRIMARY KEY, created TEXT, params TEXT);
CREATE TABLE IF NOT EXISTS cell(cellset TEXT, cid INTEGER, side TEXT, ckey TEXT, line TEXT, sig TEXT, filt TEXT, feats TEXT, ref_n INTEGER, ref_usd REAL, ref_daily TEXT,
                                db_id INTEGER, bin TEXT, PRIMARY KEY(cellset, cid));
CREATE INDEX IF NOT EXISTS cell_side_sig ON cell(cellset, side, sig, cid);
"""


def connect(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=120)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


def sig_of(line):
    import tk674_axes as ax
    patch = {}
    for t in line.split():
        if "=" in t:
            k, v = t.split("=", 1)
            try:
                patch[k] = float(v)
            except ValueError:
                pass
    return json.dumps([list(x) for x in ax.sigkey(patch)], separators=(",", ":"))


def cmd_extract(a):
    import tk700_freeze as fr
    out = connect(a.db)
    if out.execute("SELECT 1 FROM cellset WHERE name=?", (a.cellset,)).fetchone():
        raise SystemExit("набор %s уже есть: другое имя или DELETE вручную" % a.cellset)
    src = sqlite3.connect("file:%s?mode=ro" % CHRON_DB, uri=True)
    t0 = time.time()
    sel = "m.id, m.side, m.ckey, m.bin, m.trades, m.usd, json_extract(m.extra, '$.line')" + (", json_extract(m.extra, '$.daily')" if a.with_daily else ", NULL")
    sql = "SELECT %s FROM metric m WHERE m.uni=? AND m.win=? AND m.usd>? AND m.id NOT IN (SELECT metric FROM metric_fix)" % sel
    args = [a.uni, a.win, a.min_usd]
    if a.mod:
        sql += " AND m.id %% %d = %d" % (a.mod, a.rem)
    bins = dict(x.split(":") for x in a.bins.split(","))
    best = {}
    n_all = 0
    for id_, side, ckey, bin_, trades, usd, line, daily in src.execute(sql, args):
        n_all += 1
        if not ckey:
            continue
        k = (side, ckey)
        pref = 1 if bin_ == bins.get(side) else 0
        if k not in best or (pref, id_) > (best[k][0], best[k][1]):
            best[k] = (pref, id_, bin_, trades, usd, line, daily)
    rows, skipped = [], 0
    for (side, ckey), (pref, id_, bin_, trades, usd, line, daily) in sorted(best.items()):
        if not pref:
            skipped += 1
            continue
        eng, flt = fr.split_ckey(ckey)
        line = line or eng
        rows.append((side, ckey, line, sig_of(line), json.dumps(flt), ",".join(sorted({f for f, _, _ in flt})), int(trades), float(usd), daily, int(id_), bin_))
    out.execute("INSERT INTO cellset VALUES(?,?,?)", (a.cellset, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(dict(
        uni=a.uni, win=a.win, min_usd=a.min_usd, bins=bins, mod=a.mod, rem=a.rem, with_daily=a.with_daily, rows_db=n_all, other_bin_skipped=skipped))))
    out.executemany("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [(a.cellset, i + 1) + r for i, r in enumerate(rows)])
    out.commit()
    print("набор %s: строк БД %d, клеток %d (другого бинаря пропущено %d), %.0f с → %s" % (a.cellset, n_all, len(rows), skipped, time.time() - t0, a.db))
    return 0


def cmd_derive(a):
    """Набор `--cellset` = набор `--base` с подменой по строкам исправленного f12 (TK-699, `metric.win = TK699-f12`, приказ координатора 24.11): ключ (сторона, ckey);
    клетка со строкой f12 — ref_n / ref_usd / ref_daily / line / sig / db_id / bin из строки f12, при usd ≤ порога клетка уходит; строка f12 вне базы с usd > порога — добавляется (cid после максимума базы);
    клетки без строки f12 — как были (тот же cid). Бинарь строки f12, не равный своему, но равный по `eqbins674.json` (счёт TK-674 одинаков), — бинарь стороны; иной бинарь — клетка пропущена."""
    import tk700_freeze as fr
    out = connect(a.db)
    if out.execute("SELECT 1 FROM cellset WHERE name=?", (a.cellset,)).fetchone():
        raise SystemExit("набор %s уже есть: другое имя или DELETE вручную" % a.cellset)
    if not out.execute("SELECT 1 FROM cellset WHERE name=?", (a.base,)).fetchone():
        raise SystemExit("нет базового набора %s" % a.base)
    bins = dict(x.split(":") for x in a.bins.split(","))
    eq = set(json.load(open(a.eqbins, encoding="utf-8"))) if os.path.exists(a.eqbins) else set()
    src = sqlite3.connect("file:%s?mode=ro" % a.chron, uri=True)
    t0 = time.time()
    sel = "m.id, m.side, m.ckey, m.bin, m.trades, m.usd, json_extract(m.extra, '$.line')" + (", json_extract(m.extra, '$.daily')" if a.with_daily else ", NULL")
    rows = {}
    for id_, side, ckey, bin_, trades, usd, line, daily in src.execute(
            "SELECT %s FROM metric m WHERE m.uni=? AND m.win=? AND m.id NOT IN (SELECT metric FROM metric_fix)" % sel, (a.uni, a.win)):
        if ckey and ((side, ckey) not in rows or id_ > rows[side, ckey][0]):
            rows[side, ckey] = (id_, bin_, int(trades), float(usd), line, daily)
    out.execute("CREATE TEMP TABLE f12k(side TEXT, ckey TEXT, PRIMARY KEY(side, ckey)) WITHOUT ROWID")
    out.executemany("INSERT INTO f12k VALUES(?,?)", list(rows))
    old = {(s, k): (cid, flt, feats) for cid, s, k, flt, feats in out.execute(
        "SELECT cid, side, ckey, filt, feats FROM cell WHERE cellset=? AND EXISTS(SELECT 1 FROM f12k k WHERE k.side=cell.side AND k.ckey=cell.ckey)", (a.base,))}
    nxt = out.execute("SELECT max(cid) FROM cell WHERE cellset=?", (a.base,)).fetchone()[0] + 1
    new, n_drop, n_rep, n_add, n_bin_eq, n_bin_skip = [], 0, 0, 0, 0, 0
    for (side, ckey), (id_, bin_, trades, usd, line, daily) in sorted(rows.items()):
        own = bins[side]
        if bin_ != own and bin_ not in eq:
            n_bin_skip += 1
            out.execute("DELETE FROM f12k WHERE side=? AND ckey=?", (side, ckey))      # чужой бинарь: клетка базы остаётся как была
            continue
        if usd <= a.min_usd:
            n_drop += (side, ckey) in old
            continue
        n_bin_eq += bin_ != own
        eng, flt = fr.split_ckey(ckey)
        line = line or eng
        if (side, ckey) in old:
            cid, filt, feats = old[side, ckey]
            n_rep += 1
        else:
            cid, filt, feats = nxt, json.dumps(flt), ",".join(sorted({f for f, _, _ in flt}))
            nxt += 1
            n_add += 1
        new.append((a.cellset, cid, side, ckey, line, sig_of(line), filt, feats, trades, usd, daily, int(id_), own))
    out.execute("INSERT INTO cell SELECT ?, cid, side, ckey, line, sig, filt, feats, ref_n, ref_usd, ref_daily, db_id, bin FROM cell WHERE cellset=? "
                "AND NOT EXISTS(SELECT 1 FROM f12k k WHERE k.side=cell.side AND k.ckey=cell.ckey)", (a.cellset, a.base))
    n_keep = out.execute("SELECT count(*) FROM cell WHERE cellset=?", (a.cellset,)).fetchone()[0]
    out.executemany("INSERT INTO cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", new)
    out.execute("INSERT INTO cellset VALUES(?,?,?)", (a.cellset, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps(dict(
        base=a.base, fix_win=a.win, uni=a.uni, min_usd=a.min_usd, bins=bins, with_daily=a.with_daily, rows_f12=len(rows), kept_unchanged=n_keep, replaced=n_rep, added=n_add, dropped_le_min=n_drop,
        bin_equal_by_eqbins=n_bin_eq, other_bin_skipped=n_bin_skip))))
    out.commit()
    print("набор %s из %s по %s: строк f12 %d → заменено %d, добавлено %d, убрано (usd ≤ %g) %d, бинарь по eqbins674 %d, чужой бинарь %d; без строки f12 — как были %d; %.0f с" % (
        a.cellset, a.base, a.win, len(rows), n_rep, n_add, a.min_usd, n_drop, n_bin_eq, n_bin_skip, n_keep, time.time() - t0))
    return 0


def cmd_stats(a):
    c = connect(a.db)
    p = c.execute("SELECT params FROM cellset WHERE name=?", (a.cellset,)).fetchone()
    print("параметры:", p[0] if p else "нет набора")
    for side, n, nf, ns in c.execute("SELECT side, count(*), sum(filt!='[]'), count(DISTINCT sig) FROM cell WHERE cellset=? GROUP BY side", (a.cellset,)):
        print("  %-5s клеток %d, с фильтром %d, ключей прохода сигналов %d" % (side, n, nf or 0, ns))
    print("  признаки фильтров:", [r[0] for r in c.execute("SELECT feats FROM cell WHERE cellset=? AND feats!='' GROUP BY feats ORDER BY count(*) DESC LIMIT 8", (a.cellset,))])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    e = sp.add_parser("extract")
    e.add_argument("--cellset", required=True)
    e.add_argument("--db", default=MASS_DB)
    e.add_argument("--uni", default="c539")
    e.add_argument("--win", default="01.07–13.11")
    e.add_argument("--min-usd", type=float, default=1.0)
    e.add_argument("--bins", default=BINS)
    e.add_argument("--with-daily", action="store_true")
    e.add_argument("--mod", type=int, default=0)
    e.add_argument("--rem", type=int, default=0)
    d = sp.add_parser("derive")
    d.add_argument("--cellset", required=True)
    d.add_argument("--base", default="all1")
    d.add_argument("--db", default=MASS_DB)
    d.add_argument("--chron", default=CHRON_DB)
    d.add_argument("--uni", default="c539")
    d.add_argument("--win", default="TK699-f12")
    d.add_argument("--min-usd", type=float, default=1.0)
    d.add_argument("--bins", default=BINS)
    d.add_argument("--eqbins", default=EQBINS)
    d.add_argument("--no-daily", dest="with_daily", action="store_false")
    s = sp.add_parser("stats")
    s.add_argument("--cellset", required=True)
    s.add_argument("--db", default=MASS_DB)
    a = ap.parse_args(argv)
    return {"extract": cmd_extract, "derive": cmd_derive, "stats": cmd_stats}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
