#!/usr/bin/env python3
"""П-42 TK-696: веер вкладки «Эквити» (:8090) с 01.01.2026 — расширение готового веера tk684_fan.py (136 суток, 01.07–13.11) рядами января–июня из массового прогона TK-700.

Использование (srv, только чтение; под systemd-run, Nice 19, память ≤ 16 ГБ): <venv>/bin/python tk684_fan_ext.py <каталог веера v2> <mass.sqlite TK-700> <каталог выхода>
Источник Jan–Jun: /data/p42-tk700/mass/mass.sqlite — прогоны h1-{jan..jun}-{A, A-short, H, B, B-short} (без h1-probe-*); cell.db_id = chron.metric.id клетки веера; mass_day.day — сутки от 01.01.2026
(янв 0–30, … июн 151–180; 01.07 = 181); pnl = gross − fees по дню входа; в mass_day только сутки со сделками, остальные — 0. Сутки ≥ 181 в h1-прогонах не берутся.
Хвост 14–24.11 (сутки 317–327): прогон h2n-A-short (all1, шорт A, TK-700 n1) — клетки, чей ряд летописи в h2-chron-A-short совпал с пакетом (клетка есть в прогоне); поле tail = 1.
Клетка расширяется на январь–июнь, если: её id веера == cell.db_id клетки набора прогонов, есть хотя бы один месяц с рядом, ни в одном из месяцев нет строки mass_skip (покрытие признака < 0,7 — месяц не посчитан),
месяц не посчитан двумя прогонами сразу, ни одни сутки не вышли за int16 (±327,67 $), сторона клетки набора == сторона клетки веера. Остальные клетки — как были: нули до 01.07, поле ext = 0.
Ряд клетки = int16 центы [0..180] (массовый прогон, ext) + [181..316] (летопись, как в v2) + [317..327] (хвост, tail); n = n_летопись + Σ n; usd = usd_летопись + Σ pnl; Шарп / просадка / перехай / mn / mx — по окну клетки
(с 01.01 у ext, с 01.07 у остальных; по 24.11 у tail, по 13.11 у остальных; нули вне окна не считаются; формулы tk684_metrics);
nw / mw / nl / ml = 0, worst = NaN (в mass_day нет сделок — R:R и худшая сделка «—»). Поля ext и tail (последние в fields): ext = 1 — ряд с 01.01, 0 — с 01.07; tail = 1 — ряд по 24.11, 0 — по 13.11. Число суток чётное: Float32Array пакета (поля клеток) начинается с кратного 4 смещения.
Сверка до записи (расхождение = код 1, файлы не пишутся): векторные метрики == tk684_metrics.daily_metrics на 2000 расширенных клетках; h2-chron-A-short (all1, шорт A, сутки 181–316) == суточные ряды летописи в пакетах веера.
Выход: тот же формат, что у tk684_fan.py: index.json (days 328, start_ms 01.01, off 181, tail_from 317, ext_n, tail_n), <имя>.bin.gz (Int16 × N × 317, Float32 × N × 15, UInt32 id, пары осей, число пар), <имя>.json.gz (ключи — копия), check.json."""
from __future__ import annotations

import gzip
import json
import math
import os
import shutil
import sqlite3
import sys
import calendar

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk684_metrics as mt  # noqa: E402

OFF = 181                                   # сутки 01.07 от 01.01
D0, D1, D2 = OFF, 136, 11
DAYS = D0 + D1 + D2                          # 01.01–24.11
assert DAYS % 2 == 0
TAIL0 = D0 + D1                              # 317 — первые сутки хвоста
TAIL_RUN = "h2n-A-short"
START_MS = calendar.timegm((2026, 1, 1, 0, 0, 0)) * 1000
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun")
CLASSES = ("A", "A-short", "H", "B", "B-short")
RUNS = [(f"h1-{m}-{c}", i) for i, m in enumerate(MONTHS) for c in CLASSES]
CHECK_RUN = "h2-chron-A-short"
CHUNK = 2_000_000
ROW = np.dtype([("cid", "<i8"), ("day", "<i4"), ("pnl", "<f8"), ("n", "<i4")])


def stream(con, sql, args=()):
    cur = con.execute(sql, args)
    while True:
        rows = cur.fetchmany(CHUNK)
        if not rows:
            return
        yield rows


def vmetrics(S):
    """Метрики суточных рядов (m × D, целые центы): Шарп, просадка, перехай, минимум и максимум накопленного ($) — как tk684_metrics (старт накопленного 0, перехай с хвостом)."""
    m, D = S.shape
    x = S / 100.0
    mean, sd = x.mean(1), x.std(1, ddof=1)
    sharpe = np.where(sd > 0, mean / np.where(sd > 0, sd, 1.0) * math.sqrt(365), np.nan)
    c = np.cumsum(S, 1, dtype=np.int64)
    pk = np.maximum(np.maximum.accumulate(c, 1), 0)
    dd = (pk - c).max(1) / 100.0
    prev = np.concatenate([np.zeros((m, 1), np.int64), pk[:, :-1]], 1)
    rec = c > prev
    ar = np.arange(D)
    last = np.maximum.accumulate(np.where(rec, ar, -1), 1)
    before = np.concatenate([np.full((m, 1), -1), last[:, :-1]], 1)
    gap = np.maximum(np.where(rec, ar - before, 0).max(1), D - 1 - last[:, -1])
    return sharpe, dd, gap.astype(np.float64), np.minimum(c.min(1), 0) / 100.0, np.maximum(c.max(1), 0) / 100.0


def main():
    src, mass, out = sys.argv[1:4]
    I = json.load(open(f"{src}/index.json", encoding="utf-8"))
    assert I["days"] == D1 and I["fields"][-1] != "ext", "на входе веер v2 (136 суток)"
    NF0 = len(I["fields"])
    names = list(I["files"])
    # ── распаковка пакетов v2 в общие массивы (порядок строк — по файлам индекса)
    parts, off, n = {}, {}, 0
    for nm in names:
        f = I["files"][nm]
        off[nm] = n
        n += f["n"]
    N = n
    C = np.zeros((N, D1), "<i2")
    F0 = np.zeros((N, NF0), "<f4")
    ids = np.zeros(N, "<u4")
    side = np.zeros(N, np.int8)
    for nm in names:
        f, a = I["files"][nm], off[nm]
        body = gzip.decompress(open(f"{src}/{nm}.bin.gz", "rb").read())
        k, T = f["n"], f["pairs"]
        cb, fb = k * D1 * 2, k * NF0 * 4
        assert len(body) == cb + fb + k * 4 + T * 4 + k, f"{nm}: размер пакета"
        C[a:a + k] = np.frombuffer(body, "<i2", k * D1).reshape(k, D1)
        F0[a:a + k] = np.frombuffer(body, "<f4", k * NF0, cb).reshape(k, NF0)
        ids[a:a + k] = np.frombuffer(body, "<u4", k, cb + fb)
        side[a:a + k] = 1 if f["side"] == "short" else 0
        parts[nm] = body[cb + fb + k * 4:]       # пары осей и число пар — переписываются без изменений
    print(f"веер v2: {N} клеток, {len(names)} файлов", flush=True)
    order = np.argsort(ids, kind="stable")
    sid = ids[order].astype(np.int64)
    assert (np.diff(sid) > 0).all(), "id клеток веера не уникальны"

    con = sqlite3.connect(f"file:{mass}?mode=ro", uri=True)
    got = {r[0]: r for r in con.execute("SELECT run_id, cellset, state FROM mass_run")}
    for rid, _ in RUNS + [(CHECK_RUN, 0), (TAIL_RUN, 0)]:
        assert rid in got and got[rid][2] == "done", f"прогон {rid}: {got.get(rid)}"
    bad_rc = con.execute("SELECT count(*) FROM mass_batch WHERE run_id LIKE 'h1-%' AND run_id NOT LIKE 'h1-probe%' AND rc != 0").fetchone()[0]
    print(f"прогонов {len(RUNS)} готовы; пакетов mass_batch с rc != 0: {bad_rc}", flush=True)
    assert bad_rc == 0, "в h1-прогонах есть пакеты с ненулевым кодом"

    # наборы клеток: cid → (id веера, сторона)
    dbof, sdof = {}, {}
    for cs in sorted({got[r][1] for r, _ in RUNS + [(CHECK_RUN, 0), (TAIL_RUN, 0)]}):
        a = np.array(con.execute("SELECT cid, db_id, side = 'short' FROM cell WHERE cellset = ?", (cs,)).fetchall(), dtype=np.int64)
        d, s = np.full(a[:, 0].max() + 1, -1, np.int64), np.zeros(a[:, 0].max() + 1, np.int8)
        d[a[:, 0]], s[a[:, 0]] = a[:, 1], a[:, 2]
        dbof[cs], sdof[cs] = d, s
        print(f"набор {cs}: {len(a)} клеток", flush=True)

    def rows_of(cs, cid):
        """cid набора → (индексы строк веера, маска попавших); сторона набора и веера должна совпасть."""
        cid = cid[cid < len(dbof[cs])]
        db = dbof[cs][cid]
        p = np.clip(np.searchsorted(sid, db), 0, N - 1)
        ok = (sid[p] == db) & (db >= 0)
        row = order[p[ok]]
        return row, ok, cid

    PRE = np.zeros((N, D0), "<i2")
    usd_pre, n_pre = np.zeros(N), np.zeros(N)
    mm = np.zeros(N, np.uint8)                       # биты месяцев с рядом
    skipped, conflict, over, wrongside = (np.zeros(N, bool) for _ in range(4))
    seen_cells = 0
    for rid, mi in RUNS:
        cs = got[rid][1]
        touched = np.zeros(N, bool)
        nrows = 0
        for rows in stream(con, "SELECT cid, day, pnl, n FROM mass_day WHERE run_id = ? AND day < ?", (rid, OFF)):
            a = np.array(rows, dtype=ROW)
            nrows += len(a)
            row, ok, cid = rows_of(cs, a["cid"])
            a = a[a["cid"] < len(dbof[cs])][ok]
            wrongside[row[sdof[cs][a["cid"]] != side[row]]] = True
            cents = np.rint(a["pnl"] * 100).astype(np.int64)
            o = np.abs(cents) > 32767
            if o.any():
                over[row[o]] = True
            PRE[row, a["day"]] = np.clip(cents, -32768, 32767).astype("<i2")
            usd_pre += np.bincount(row, a["pnl"], N)
            n_pre += np.bincount(row, a["n"], N)
            touched[row] = True
        # месяц, посчитанный дважды (две разные клетки набора с одним id веера или клетка в двух прогонах месяца)
        conflict |= touched & ((mm & (1 << mi)) != 0)
        mm[touched] |= 1 << mi
        for rows in stream(con, "SELECT cid FROM mass_skip WHERE run_id = ?", (rid,)):
            cid = np.array(rows, dtype=np.int64)[:, 0]
            row, _, _ = rows_of(cs, cid)
            skipped[row] = True
        print(f"{rid}: строк mass_day {nrows}, клеток веера с рядом {int(touched.sum())}", flush=True)
    ext = (mm != 0) & ~skipped & ~conflict & ~over & ~wrongside
    stat = {"клеток веера": N, "с рядом хотя бы в одном месяце": int((mm != 0).sum()), "вне: есть mass_skip": int(((mm != 0) & skipped).sum()), "вне: месяц дважды": int(((mm != 0) & conflict).sum()),
            "вне: вышли за int16": int(((mm != 0) & over).sum()), "вне: сторона набора ≠ стороны веера": int(((mm != 0) & wrongside).sum()), "расширено": int(ext.sum())}
    stat["расширено по месяцам с рядом"] = {m: int((ext & ((mm >> i) & 1).astype(bool)).sum()) for i, m in enumerate(MONTHS)}
    print(json.dumps(stat, ensure_ascii=False), flush=True)

    # ── сверка: h2-chron-A-short == суточные ряды летописи в пакетах веера
    cs = got[CHECK_RUN][1]
    CH = np.zeros((N, D1), "<i2")
    inrun = np.zeros(N, bool)
    for rows in stream(con, "SELECT cid, day, pnl, n FROM mass_day WHERE run_id = ?", (CHECK_RUN,)):
        a = np.array(rows, dtype=ROW)
        row, ok, cid = rows_of(cs, a["cid"])
        a = a[a["cid"] < len(dbof[cs])][ok]
        d = a["day"] - OFF
        g = (d >= 0) & (d < D1)
        CH[row[g], d[g]] = np.clip(np.rint(a["pnl"][g] * 100), -32768, 32767).astype("<i2")
        inrun[row] = True
    cmp = inrun & (side == 1)
    diff = np.abs(CH[cmp].astype(np.int32) - C[cmp].astype(np.int32))
    mism = int((diff.max(1) > 1).sum()) if cmp.any() else 0
    chk = {"сверено клеток": int(cmp.sum()), "расхождение > 1 цента": mism, "макс. расхождение, центов": int(diff.max()) if cmp.any() else 0}
    print("сверка h2-chron-A-short:", json.dumps(chk, ensure_ascii=False), flush=True)
    del CH
    if not cmp.any() or mism > max(5, 0.001 * int(cmp.sum())):
        print("РАСХОЖДЕНИЕ с летописью — файлы не пишутся")
        sys.exit(1)

    # ── хвост 14–24.11: h2n-A-short; клетка с хвостом = клетка прогона h2-chron-A-short (её ряд июля–ноября совпал с летописью), без строк mass_skip
    cs = got[TAIL_RUN][1]
    TAIL = np.zeros((N, D2), "<i2")
    usd_tail, n_tail = np.zeros(N), np.zeros(N)
    tskip = np.zeros(N, bool)
    for rows in stream(con, "SELECT cid FROM mass_skip WHERE run_id = ?", (TAIL_RUN,)):
        tskip[rows_of(cs, np.array(rows, dtype=np.int64)[:, 0])[0]] = True
    cov, nout, nover = inrun & (side == 1) & ~tskip, 0, False
    for rows in stream(con, "SELECT cid, day, pnl, n FROM mass_day WHERE run_id = ? AND day >= ? AND day < ?", (TAIL_RUN, TAIL0, DAYS)):
        a = np.array(rows, dtype=ROW)
        row, ok, cid = rows_of(cs, a["cid"])
        a = a[a["cid"] < len(dbof[cs])][ok]
        keep = cov[row]
        nout += int((~keep).sum())
        row, a = row[keep], a[keep]
        cents = np.rint(a["pnl"] * 100).astype(np.int64)
        nover |= bool((np.abs(cents) > 32767).any())
        TAIL[row, a["day"] - TAIL0] = np.clip(cents, -32768, 32767).astype("<i2")
        usd_tail += np.bincount(row, a["pnl"], N)
        n_tail += np.bincount(row, a["n"], N)
    assert not nover, "хвост: сутки вышли за int16"
    tail = cov
    stat["хвост 14–24.11 (h2n-A-short)"] = {"клеток с хвостом": int(tail.sum()), "из них с рядом с 01.01": int((tail & ext).sum()), "строк прогона вне клеток с хвостом": nout, "клеток с хвостом и ненулевым рядом": int(((TAIL != 0).any(1) & tail).sum())}
    print(json.dumps(stat["хвост 14–24.11 (h2n-A-short)"], ensure_ascii=False), flush=True)

    # ── метрики клеток с рядом с 01.01 и / или с хвостом (окно клетки: с 01.01 или 01.07, по 24.11 или 13.11) и сверка векторных формул со скалярными
    F = np.zeros((N, NF0 + 2), "<f4")
    F[:, :NF0] = F0
    F[:, NF0], F[:, NF0 + 1] = ext, tail
    NW, MW, NL, ML, WORST = (I["fields"].index(k) for k in ("nw", "mw", "nl", "ml", "worst"))
    FI = {k: I["fields"].index(k) for k in ("n", "usd", "sharpe", "dd", "gap", "mn", "mx")}
    for ge, gt in ((True, True), (True, False), (False, True)):
        sel = np.flatnonzero((ext == ge) & (tail == gt))
        a0, a1 = (0 if ge else D0), (DAYS if gt else TAIL0)
        for s in range(0, len(sel), 20000):
            r = sel[s:s + 20000]
            S = np.zeros((len(r), DAYS), np.int32)
            if ge:
                S[:, :D0] = PRE[r]
            S[:, D0:TAIL0] = C[r]
            if gt:
                S[:, TAIL0:] = TAIL[r]
            S = S[:, a0:a1]
            sh, dd, gp, mn, mx = vmetrics(S)
            F[r, FI["n"]] = F0[r, FI["n"]] + (n_pre[r] if ge else 0) + (n_tail[r] if gt else 0)
            F[r, FI["usd"]] = F0[r, FI["usd"]].astype(np.float64) + (usd_pre[r] if ge else 0) + (usd_tail[r] if gt else 0)
            F[r, FI["sharpe"]], F[r, FI["dd"]], F[r, FI["gap"]], F[r, FI["mn"]], F[r, FI["mx"]] = sh, dd, gp, mn, mx
            F[r, NW] = F[r, MW] = F[r, NL] = F[r, ML] = 0
            F[r, WORST] = np.nan
            if s == 0:
                rs = np.random.default_rng(7).choice(len(r), min(2000, len(r)), replace=False)
                bad = 0
                for j in rs:
                    m = mt.daily_metrics(S[j] / 100.0)
                    bad += not ((m["sharpe"] is None and np.isnan(sh[j])) or (m["sharpe"] is not None and abs(m["sharpe"] - sh[j]) <= 1e-9 * max(1, abs(m["sharpe"])))) or abs(m["dd"] - dd[j]) > 1e-6 or m["gap"] != gp[j]
                print(f"сверка метрик с tk684_metrics (ext {int(ge)}, tail {int(gt)}): {len(rs)} клеток, расхождений {bad}", flush=True)
                if bad:
                    sys.exit(1)

    # ── запись
    os.makedirs(out, exist_ok=True)
    J = dict(I)
    J.update(days=DAYS, start_ms=START_MS, off=OFF, fields=I["fields"] + ["ext", "tail"], tail_from=TAIL0, ext_n=int(ext.sum()), tail_n=int(tail.sum()), ext_stat=stat, ext_check=chk, files={})
    chkj = {}
    old_chk = json.load(open(f"{src}/check.json", encoding="utf-8")) if os.path.exists(f"{src}/check.json") else {}
    for nm in names:
        f, a = I["files"][nm], off[nm]
        k = f["n"]
        cents = np.zeros((k, DAYS), "<i2")
        cents[:, D0:TAIL0] = C[a:a + k]
        e, t = ext[a:a + k], tail[a:a + k]
        cents[e, :D0] = PRE[a:a + k][e]
        cents[t, TAIL0:] = TAIL[a:a + k][t]
        body = cents.tobytes() + F[a:a + k].tobytes() + ids[a:a + k].tobytes() + parts[nm]
        with open(f"{out}/{nm}.bin.gz", "wb") as fh:
            fh.write(gzip.compress(body, 6, mtime=0))
        shutil.copyfile(f"{src}/{nm}.json.gz", f"{out}/{nm}.json.gz")
        g = dict(f)
        g.update(bytes=len(body), gz=os.path.getsize(f"{out}/{nm}.bin.gz"), ext=int(e.sum()), tail=int(t.sum()))
        J["files"][nm] = g
        keys = json.loads(gzip.decompress(open(f"{src}/{nm}.json.gz", "rb").read()))["k"]
        rows = []
        for ent in old_chk.get(nm, []):
            li = int(np.flatnonzero(ids[a:a + k] == ent["id"])[0])
            rows.append({"id": ent["id"], "cell": keys[li], "f": [None if np.isnan(x) else float(x) for x in F[a + li]], "cum": round(float(cents[li].astype(np.int64).sum()) / 100, 2)})
        chkj[nm] = rows
        print(f"{nm}: клеток {k}, расширено {int(e.sum())}, хвост {int(t.sum())}, {len(body) / 1e6:.1f} МБ → gz {g['gz'] / 1e6:.1f} МБ", flush=True)
    json.dump(J, open(f"{out}/index.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(chkj, open(f"{out}/check.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"TK-696 веер с 01.01: клеток {N}, расширено {int(ext.sum())}, с хвостом {int(tail.sum())}, суток {DAYS}")


if __name__ == "__main__":
    main()
