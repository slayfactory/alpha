#!/usr/bin/env python3
"""TK-700: заморозка кандидатов TK-674 для проверки на отложенных месяцах (январь–июнь 2026, В-170). Формат — как tk691-frozen.json (версия, ссылки на файлы с sha256, бинарь, вселенная, окна, клетки, правила чтения),
ключ замка «одно чтение» — sha256 этого файла (`tk700_lock.py`). Ключи клеток и фильтры — как в БД летописи (`tk674_axes.canon`), пороги — абсолютные числа в ключе `F=…`.

  python3 tk700_freeze.py make --cell long:'<ckey>' [--cell short:'<ckey>' …] --bin <бинарь> --asm /data/p42-tk700/p671/asm --out docs/research/freeze/tk700-frozen.json
        [--thr res/thr674-c539.json --thr res/thr680.json …] [--uni c539 --ref-win '01.07–13.11'] [--rule-file <текст правила решения>] [--wins m01,…,m06 | --dry]
  python3 tk700_freeze.py show <файл>
  python3 tk700_freeze.py sha  <файл>

`make` (на srv, БД `/data/chron/chron.sqlite` только чтение): для каждой клетки берёт строку БД (сторона, c539, окно июль–ноябрь) → полная строка движка `line`, md5 бинаря, прежние числа (справочно: сделки, Σ $, Sharpe);
добавляет базу каждой стороны (клетка без фильтра, В-169 — сравнение «против базы»); фиксирует sha256 таблиц признаков (`--asm`), порогов (`--thr`), списков монет окон, кода (tk700_*.py, tk674_axes.py, tk674_stats.py) и числа окон.
Таблицы признаков H1 строятся ДО заморозки (P&L они не содержат); заморозка закрепляет их sha, чтобы после чтения их нельзя было подменить. Файл неизменяем: новая версия — новый sha и новый замок."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("TK674_REBASE", "1")                 # база стопа ≤ 4 % (§13.73.2): клетки TK-674 считались на ней
import tk700_lock as lk  # noqa: E402
import tk700_win as w7  # noqa: E402

VERSION = "tk700-frozen-1"
CODE_FILES = ("tk700_win.py", "tk700_lock.py", "tk700_holdout.py", "tk700_freeze.py", "tk674_axes.py", "tk674_stats.py", "tk604_stats.py", "tk638_post.py")
SIDE = {"long": "L", "short": "S"}


def sha_of(path):
    return lk.sha256_file(path)


def parse_cell(s):
    side, ckey = s.split(":", 1)
    if side not in SIDE:
        raise SystemExit("--cell: сторона long | short, не %r" % side)
    return side, ckey.strip()


def split_ckey(ckey):
    """«k=v …  F=a<=x&b>=y» → (строка ключей движка без значений по умолчанию, [[признак, знак, порог], …])."""
    line, _, sem = ckey.partition(" F=") if " F=" in ckey else (ckey, "", "")
    if ckey.startswith("F="):
        line, sem = "", ckey[2:]
    parts = []
    for tok in sem.split("&") if sem else []:
        for op in ("<=", ">=", "=="):
            if op in tok:
                f, x = tok.split(op)
                parts.append([f, op, float(x)])
                break
        else:
            raise SystemExit("ckey: не разобран фильтр %r" % tok)
    return line.strip(), parts


def db_cell(store, side, uni, win, ckey):
    r = store.cells_lookup(side, uni, win, [ckey]).get(ckey)
    if r is None:
        raise SystemExit("клетка %s %s нет в БД (%s, %s, %s)" % (side, ckey, side, uni, win))
    return r


def base_cell(side, uni):
    """Клетка базы стороны (без фильтра): строка движка по `tk674_axes.cell_assign` + `u.extra` вселенной."""
    import tk674_axes as ax
    import tk643_run as t
    import tk625_run as r5
    u = r5.Uni(uni)
    patch, filt, ok, why = ax.cell_assign(SIDE[side], {})
    if not ok:
        raise SystemExit("база %s: %s" % (side, why))
    line = t.variant_line(r5, u, SIDE[side], patch)
    return dict(id="base-" + side, side=side, ckey=ax.canon(line, []), line=line, filter=[], base=True)


def cmd_make(a):
    cells, bases = [], []
    sys.path.insert(0, "/data/chron")
    store = None
    if not a.no_db:
        import chron
        store = chron.Store(chron.connect(chron.SRV_DB, ro=True))
    import tk674_axes as ax
    for k, c in enumerate(a.cell):
        side, ckey = parse_cell(c)
        line, flt = split_ckey(ckey)
        rec = dict(id="c%02d-%s" % (k + 1, side), side=side, ckey=ckey, line=line, filter=flt, base=False)
        if store is not None:
            r = db_cell(store, side, a.uni, a.ref_win, ckey)
            ex = r.get("extra") or {}
            rec.update(bin_md5=r["bin"], line=ex.get("line", line), ref=dict(win=a.ref_win, uni=a.uni, trades=r["trades"], usd=r["usd"], mean_usd=r["mean_usd"], sharpe=r["sharpe"]))
        cells.append(rec)
    for side in sorted({c["side"] for c in cells}):
        b = base_cell(side, a.uni)
        if store is not None:
            r = db_cell(store, side, a.uni, a.ref_win, b["ckey"])
            b.update(bin_md5=r["bin"], ref=dict(win=a.ref_win, uni=a.uni, trades=r["trades"], usd=r["usd"], mean_usd=r["mean_usd"], sharpe=r["sharpe"]))
        bases.append(b)
    wins = a.wins.split(",") if a.wins else list(w7.NAMES)
    binary = dict(path=a.bin, md5=hashlib.md5(open(a.bin, "rb").read()).hexdigest(), sha256=sha_of(a.bin))
    for c in cells + bases:
        if c.get("bin_md5") and c["bin_md5"] != binary["md5"]:
            raise SystemExit("клетка %s: бинарь БД %s ≠ --bin %s (бинарь на клетку — отдельной заморозкой)" % (c["id"], c["bin_md5"], binary["md5"]))
    tables = {}
    for w in wins:
        for s in ("long", "short"):
            p = os.path.join(a.asm, "%s-%s.parquet" % (w, s))
            tables["%s-%s" % (w, s)] = dict(path=p, sha256=sha_of(p))
    uni = {}
    for w in wins:
        p = w7.universe_file(w) if w in w7.NAMES else w7.C539
        uni[w] = dict(path=p, sha256=sha_of(p), n=len(w7.read_symbols(p)))
    out = dict(
        version=VERSION, created_utc=lk.utc_now(), decision="В-170: одна проверка отложенных месяцев; P&L до заморозки не строится и не читается",
        protocol=a.protocol, rule=open(a.rule_file, encoding="utf-8").read() if a.rule_file else None,
        binary=binary, universe=dict(name=a.uni, c539=dict(path=w7.C539, sha256=sha_of(w7.C539)), windows=uni),
        windows=[w7.spec(w) for w in wins] if all(w in w7.NAMES for w in wins) else wins,
        cells=cells, bases=bases, thresholds=[dict(path=p, sha256=sha_of(p)) for p in a.thr], tables=tables,
        code={n: sha_of(os.path.join(HERE, n)) for n in CODE_FILES if os.path.exists(os.path.join(HERE, n))},
        reading=dict(rule="одно чтение: замок tk700_lock (mkdir по sha этого файла); повтор — только reopen --chron <запись летописи>", stake_usd=500, margin_usd=250,
                     metrics="Σ $, $/сделку, annualized daily Sharpe (по всем суткам, ddof=1), просадка $ и % маржи, перехай (сутки между новыми максимумами: самый длинный / средний / хвост), по месяцам; против базы той же стороны"),
        defaults_note="ключ клетки — tk674_axes.canon (без значений по умолчанию движка); `line` — полная строка варианта движка из БД (extra.line), при её отсутствии — ключи ckey",
        ref_win=a.ref_win)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print("заморозка %s: клеток %d + баз %d, таблиц %d, окон %d → sha256 %s" % (a.out, len(cells), len(bases), len(tables), len(wins), sha_of(a.out)))
    return 0


def cmd_show(a):
    d = json.load(open(a.file, encoding="utf-8"))
    print("sha256", sha_of(a.file), "версия", d["version"], "создано", d["created_utc"])
    print("бинарь", d["binary"]["md5"], d["binary"]["path"])
    for c in d["cells"] + d["bases"]:
        print("  %-12s %-5s %s" % (c["id"], c["side"], c["ckey"]), "| ref", c.get("ref"))
    print("таблиц %d, порогов %d, окон %d" % (len(d["tables"]), len(d["thresholds"]), len(d["windows"])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    m = sp.add_parser("make")
    m.add_argument("--cell", action="append", required=True)
    m.add_argument("--bin", required=True)
    m.add_argument("--asm", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--thr", action="append", default=[])
    m.add_argument("--uni", default="c539")
    m.add_argument("--ref-win", default="01.07–13.11")
    m.add_argument("--rule-file", default=None)
    m.add_argument("--protocol", default="")
    m.add_argument("--wins", default="")
    m.add_argument("--no-db", action="store_true", help="без БД (тесты): клетки без ref / bin_md5")
    s = sp.add_parser("show")
    s.add_argument("file")
    h = sp.add_parser("sha")
    h.add_argument("file")
    a = ap.parse_args(argv)
    if a.cmd == "sha":
        print(sha_of(a.file))
        return 0
    return {"make": cmd_make, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
