#!/usr/bin/env python3
"""TK-684: вид «Варианты» вкладки «Эквити» страницы :8090 — сделки вариантов лонга и шорта для выбора и суммы «шорт + лонг».
Только чтение готовых выходов (движок не запускается): трейды каталогов из tk684_variants.json, склейка окон `tk604_stats.splice` (gross − fees, без фандинга, ставка 500 $;
сутки — по дню входа), охрана G7 по всему окну — как `tk643_run.load_cell`. Метрики — tk684_metrics.py.
Сверка (расхождение = остановка, код 1): метрики каждой линии == принятым — строка `metric` БД летописи (поле chron варианта: клетка, h775, последняя запись)
и числа ref варианта (из текста записи летописи, округлённые: допуск 0,006; сделки и перехай — точно).
Запуск на srv: python3 tk684_equity.py <код TK-639 (/data/p42-tk639/code)> <tk684_variants.json> <chron.sqlite> <variants.json> <variants_check.json>   (ml_page_update.sh — перед сборкой)
Дни вперёд (TK-696, 25.11): у варианта с `fwd` ({dir: каталог линии в чтении «по вчера» w1, cell: клетка в point-<id>.json}) и блока `forward` в описи — сделки чтения TK-710 (входы from … to, UTC; те же строки, что
в describe_rows: gross − fees), сверка с `thru[cell].through` точки (n, Σ, Шарп, просадка, перехай); окно 01.07–13.11 и принятые числа не меняются.
Выход: variants.json — {d0, days, stake, margin, start_ms, end_ms, groups, default, forward: {read, label, days, start_ms, end_ms}, variants: [{id, side, group, label, status (пометка линии в таблице), key, t (закрытие, мс, по возрастанию), d (сутки входа), v ($), fwd: {t, d, v}}]};
variants_check.json — ожидаемые метрики для клик-теста: single {id: метрики}, both {«лонг|шорт»: метрики суммы} по всем парам; fwd: {single, both} — то же за дни вперёд."""
import csv
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import numpy as np

code, src_path, chron_path, out_path, check_path = sys.argv[1:6]
sys.path.insert(0, code)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk684_metrics as mt  # noqa: E402     # первым: модули движка сами ставят свой каталог в начало sys.path, а в нём лежит старая копия tk684_metrics.py
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk638_post as p8  # noqa: E402

SRC = json.load(open(src_path, encoding="utf-8"))
assert mt.NDAYS == st.NDAYS, "окно tk684_metrics / tk604_stats разное"
u = r5.Uni(SRC["universe"])
ENG = {"long": "L", "short": "S"}
TOL = 0.006


def load(side, d):
    """(ряд, P&L сделок) варианта по трём окнам; охрана G7 — по всему окну (клетки без выхода rt держат сделки до 3,4 сут.)."""
    tables, daily = p8.read_tables({w.name: (d[w.name] if isinstance(d, dict) else d.replace("{w}", w.name), ENG[side]) for w in st.TRADE_WINDOWS})
    for w in st.TRADE_WINDOWS:
        tb, dl = tables[w.name], daily[w.name]
        assert abs(float(sum(dl.values())) - float(tb.net.sum())) <= 1e-4 + 2e-6 * len(tb.net), ("G7 окна", w.name, d)
    ser = st.splice(tables, u.coins(), daily=None)
    _, pnl = p8.kinds_pnl(tables)
    assert len(pnl) == len(ser.val) and np.allclose(pnl, ser.val, atol=1e-6), d
    return ser


def chron_row(db, cell, side):
    """(принятые метрики, каталоги сделок по окнам) — строка `metric` клетки на вселенной варианта, основное окно (последняя по id)."""
    # строки без каталогов сделок (отбор / заморозка / поправки L-426, L-434 — только числа) не считаются: берётся последняя строка клетки с extra.dirs
    r = db.execute("select trades, usd, sharpe, extra from metric where cell = ? and side = ? and uni = ? and win = ? and extra like '%\"dirs\"%' order by id desc limit 1", (cell, side, SRC["universe"], SRC.get("win", "01.07–13.11"))).fetchone()
    assert r, "летопись: нет строки metric для клетки %s (%s)" % (cell, side)
    ex = json.loads(r[3] or "{}")
    out = {"n": r[0], "sum": r[1], "sharpe": r[2], "dd": ex.get("maxdd"), "gap": (ex.get("kpi") or {}).get("gap_longest"), "worst": (ex.get("lmax") or {}).get("worst"), "rr": (ex.get("rr") or {}).get("rr")}
    return {k: v for k, v in out.items() if v is not None}, ex.get("dirs") or {}


def stage(dirs):
    """{окно: каталог сделок} → копии в SRC["stage"]: готовый каталог клетки копируется, упакованный (cells-arch/<окно>-<сторона>/<пакет>.tar.zst) достаётся одним членом архива; движок не запускается."""
    out = {}
    for w, d in dirs.items():
        sub = d.split("/cells/", 1)[1]                                  # <окно>-<сторона>/<пакет>/<клетка>
        dest = os.path.join(SRC["stage"], sub)
        if not os.path.isfile(os.path.join(dest, "meta.json")):
            shutil.rmtree(dest, ignore_errors=True)
            win, batch, cell = sub.split("/", 2)
            if os.path.isdir(d):
                shutil.copytree(d, dest)
            else:
                os.makedirs(os.path.join(SRC["stage"], win), exist_ok=True)
                subprocess.run(["tar", "--zstd", "-xf", "%s/%s/%s.tar.zst" % (SRC["arch"], win, batch), "-C", os.path.join(SRC["stage"], win), "%s/%s" % (batch, cell)], check=True)
            assert os.path.isfile(os.path.join(dest, "meta.json")), "нет каталога клетки после распаковки: " + dest
        out[w] = dest
    return out


def load_fwd(side, d, fw):
    """Сделки линии за дни вперёд: {t (закрытие, мс), d (сутки входа от from), v ($)}; строки — `<сторона>-trades.csv` каталога w1 чтения (время от T0 окна), вход в [from, to] по дате UTC."""
    t0 = int(dt.datetime.combine(dt.date.fromisoformat(fw["t0"]), dt.time(), dt.timezone.utc).timestamp() * 1000)
    f0, f1 = dt.date.fromisoformat(fw["from"]), dt.date.fromisoformat(fw["to"])
    rows = []
    with open(os.path.join(d, side + "-trades.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            t_in, t_out = int(r["entry_ms"]) + t0, int(r["exit_ms"]) + t0
            day = dt.datetime.fromtimestamp(t_in / 1000, dt.timezone.utc).date()
            if f0 <= day <= f1:
                rows.append((t_out, t_in, (day - f0).days, round(float(r["gross"]), 6) - round(float(r["fees"]), 6)))
    rows.sort()
    return {"t": [x[0] for x in rows], "d": [x[2] for x in rows], "v": [round(x[3], 6) for x in rows]}


def compare(vid, got, want, tol, what):
    bad = []
    for k, w in want.items():
        g = got[k]
        ok = g == w if k in ("n", "gap") else g is not None and abs(g - w) <= tol
        if not ok:
            bad.append("%s: %s = %r, принято %r" % (vid, k, g, w))
    return ["%s — %s" % (b, what) for b in bad]


db = sqlite3.connect("file:%s?mode=ro" % chron_path, uri=True)
ids = [v["id"] for v in SRC["variants"]]
assert len(ids) == len(set(ids)) and all(i != "-" and "/" not in i for i in ids), "tk684_variants.json: id повторяется или недопустим"
assert {v["group"] for v in SRC["variants"]} <= {g["id"] for g in SRC["groups"]} and all(v["side"] in ENG for v in SRC["variants"]), "tk684_variants.json: группа / сторона"
assert all(len(v["label"].replace("·", " ").split()) <= 6 for v in SRC["variants"]), "tk684_variants.json: подпись длиннее 6 слов"
for s in ENG:
    assert SRC["default"][s] in [v["id"] for v in SRC["variants"] if v["side"] == s], "tk684_variants.json: default " + s
D0_MS = int(dt.datetime.combine(st.D0, dt.time(), dt.timezone.utc).timestamp() * 1000)
FW = SRC.get("forward")
FW_DAYS = ((dt.date.fromisoformat(FW["to"]) - dt.date.fromisoformat(FW["from"])).days + 1) if FW else 0
FW_POINT = json.load(open(FW["point"], encoding="utf-8"))["thru"] if FW else {}
out, single, bad, fsingle = [], {}, [], {}
for v in SRC["variants"]:
    assert "ref" in v or "chron" in v, v["id"] + ": нет принятых чисел (ref / chron)"
    want, dirs = chron_row(db, v["chron"], v["side"]) if "chron" in v else ({}, {})
    ser = load(v["side"], v["dir"] if "dir" in v else stage(dirs))      # нет dir — каталоги из строки летописи (extra.dirs)
    order = np.lexsort((ser.t_in, ser.t_out))
    one = {"id": v["id"], "side": v["side"], "group": v["group"], "label": v["label"], "status": v.get("status", ""), "key": v["key"],
           "t": [int(ser.t_out[i]) for i in order], "d": [int(ser.day[i]) for i in order], "v": [round(float(ser.val[i]), 6) for i in order]}
    assert min(int(x) for x in ser.t_in) >= D0_MS and max(int(x) for x in ser.t_in) < st.LAST_MS, v["id"] + ": вход вне 01.07–13.11"
    m = mt.metrics(one["d"], one["v"])
    if "chron" in v:
        bad += compare(v["id"], m, want, 1e-5, "летопись, metric «%s»" % v["chron"])
    if "ref" in v:
        bad += compare(v["id"], m, {k: x for k, x in v["ref"].items() if k != "entry"}, TOL, v["ref"].get("entry", "ref"))
    single[v["id"]] = m
    if "fwd" in v:
        assert FW, v["id"] + ": fwd без блока forward"
        one["fwd"] = load_fwd(v["side"], v["fwd"]["dir"], FW)
        fm = mt.fwd_metrics(one["fwd"]["d"], one["fwd"]["v"], FW_DAYS)
        th = FW_POINT[v["fwd"]["cell"]]["through"]
        bad += compare(v["id"], fm, {"n": th["n"], "sum": th["sum"], "sharpe": th["sharpe"], "dd": th["maxdd"], "gap": th["gap_longest"]}, 1e-5, "вперёд, чтение %s, %s" % (FW["read"], v["fwd"]["cell"]))
        fsingle[v["id"]] = fm
    out.append(one)
    print("%-6s %-5s %4d сд., Σ %+9.2f $, Шарп %5.2f, просадка %6.2f $ (%4.1f %%), перехай %3d сут., R:R %.2f, худшая %+.2f $  %s" % (
        v["id"], v["side"], m["n"], m["sum"], m["sharpe"] or float("nan"), m["dd"], m["ddp"], m["gap"], m["rr"] or float("nan"), m["worst"], v["key"]))
if bad:
    print("TK-684: линии не равны принятым числам:\n  " + "\n  ".join(bad), file=sys.stderr)
    sys.exit(1)
both = {"%s|%s" % (a["id"], b["id"]): mt.combine(a, b) for a in out if a["side"] == "long" for b in out if b["side"] == "short"}
fboth = {"%s|%s" % (a["id"], b["id"]): mt.fwd_combine(a["fwd"], b["fwd"], FW_DAYS) for a in out if a["side"] == "long" and "fwd" in a for b in out if b["side"] == "short" and "fwd" in b}
fwd_end = max([max(x["fwd"]["t"]) for x in out if "fwd" in x] + [int(dt.datetime.combine(dt.date.fromisoformat(FW["to"]) + dt.timedelta(days=1), dt.time(), dt.timezone.utc).timestamp() * 1000)]) if FW else None
page = {"d0": str(st.D0), "days": st.NDAYS, "stake": mt.STAKE, "margin": mt.MARGIN, "start_ms": D0_MS, "end_ms": max(max(x["t"]) for x in out),
        "groups": SRC["groups"], "default": SRC["default"], "variants": out}
if FW:
    page["forward"] = {"read": FW["read"], "label": "%s–%s.%s" % (FW["from"][8:], FW["to"][8:], FW["to"][5:7]), "days": FW_DAYS,
                       "start_ms": int(dt.datetime.combine(dt.date.fromisoformat(FW["from"]), dt.time(), dt.timezone.utc).timestamp() * 1000), "end_ms": fwd_end}
json.dump(page, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
json.dump({"single": single, "both": both, "fwd": {"single": fsingle, "both": fboth}}, open(check_path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
print("TK-684: вариантов %d (лонг %d, шорт %d), сверено с принятыми; пар %d" % (len(out), sum(x["side"] == "long" for x in out), sum(x["side"] == "short" for x in out), len(both)))
