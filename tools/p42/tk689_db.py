#!/usr/bin/env python3
"""TK-693 (В-179): влияние осей на прибыль и просадку (TK-689) в БД летописи — таблицы `infl_*` в `/data/chron/chron.sqlite` (схема v8, `tools/chron.py`).
Запись прогона, чтение (тот же вид словарей, что у `level-*.json`), сверка с JSON, запросы. Только стандартная библиотека; запросы с ПК идут на srv по ssh.

    python tools/p42/tk689_db.py runs
    python tools/p42/tk689_db.py top --side long --level 2 [--grid полная] [--by r|rP|rD|excess] [--n 10] [--cat natr] [--run N]
    python tools/p42/tk689_db.py rollup [--side long] [--level 2] [--grid полная] [--run N]      # по вкладкам «Признаки» (правило TK-693, критик thr_m27kv64m9h)
    # на srv: import-json DIR[,DIR] --features ml_features.json --tool-dir CODE_DIR | verify --run N --json DIR[,DIR] | export-json --run N --out DIR | check
Прогон = данные (отпечатки клеток уровней) × код инструмента; повтор с тем же ключом не пишется, новые клетки или код — новый прогон, прежние остаются."""
import argparse
import datetime
import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import sys

MET = ("n", "usd", "mean", "sh", "dd", "ddp", "gap", "rr")
SIDES = ("long", "short")
GRIDS = ("полная", "отбор")
SRV_DB = "/data/chron/chron.sqlite"
TZ = datetime.timezone(datetime.timedelta(hours=4))
BOOL_KEYS = ("dead", "sign_stable", "months_stable", "above_noise_sh", "above_noise_usd", "null_applicable", "not_distinguishable_from_random")
OPT_KEYS = ("share_pos", "sign_stable", "months_same_sign", "months_stable", "d_months", "null_cells", "null_applicable", "r_null", "r_null_p95", "r_null_p", "r_null_d",
            "not_distinguishable_from_random")      # ключи, которых в словаре оси может не быть (NULL в БД = ключа нет)
PRIMARY_FLOOR = 3                                   # назначено (правило TK-693), не измерено: категория с меньшим числом осей показывается, но не ранжируется


def sha1(b):
    return hashlib.sha1(b if isinstance(b, bytes) else b.encode("utf-8")).hexdigest()


def fl(x):
    """NaN в БД — NULL (R:R не определён)."""
    return None if x is None or x != x else x


def bl(x):
    return None if x is None else int(bool(x))


def _here():
    return os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()


def chron_mod():
    """tools/chron.py: рядом с инструментом (на srv — копия схемы v8 в каталоге кода), в репо — каталог выше, иначе боевой /data/chron."""
    for d in (_here(), os.path.join(_here(), ".."), "/data/chron"):
        if os.path.exists(os.path.join(d, "chron.py")):
            if d not in sys.path:
                sys.path.insert(0, d)
            import chron
            return chron
    raise RuntimeError("chron.py не найден (srv: /data/chron, репо: tools/)")


def connect(path=None, ro=False):
    return chron_mod().connect(path or os.environ.get("CHRON_DB") or SRV_DB, ro=ro)


def now():
    return datetime.datetime.now(TZ).strftime("%Y-%m-%dT%H:%M+04:00")


# ---- вкладка «Признаки» --------------------------------------------------------------------------------------------------------------
def feat_table(path):
    """({имя: {n, cat, cat_short, cat_title, grp}}, версия = sha1[:10] файла). Категория — набор (вкладка) ml_features.json."""
    raw = open(path, "rb").read()
    d = json.loads(raw.decode("utf-8"))
    ft = {}
    for s in d["sets"]:
        for g in s["groups"]:
            for f in g["features"]:
                if f["name"] in ft:
                    raise ValueError("признак %s в двух наборах" % f["name"])
                ft[f["name"]] = dict(n=f["n"], cat=s["id"], cat_short=s["short"], cat_title=s["title"], grp=g.get("title") or "")
    return ft, sha1(raw)[:10]


def axis_feature(axis, ft, lead):
    """(признак, ведущий ключ или None): «F:<столбец>» — столбец; иначе ось по имени, иначе по ведущему ключу семейства (lead)."""
    if axis.startswith("F:"):
        return ft.get(axis[2:]), None
    if axis in ft:
        return ft[axis], None
    k = lead.get(axis)
    return (ft.get(k), k) if k and ft.get(k) else (None, None)


# ---- запись --------------------------------------------------------------------------------------------------------------------------
def axis_row(run, side, k, a, ft, lead, fam):
    f, ld = axis_feature(a["axis"], ft, lead)
    r = dict(run=run, side=side, k=k, grid=a["grid"], axis=a["axis"], feat_no=f and f["n"], cat=f and f["cat"], cat_short=f and f["cat_short"], lead=ld,
             n=a["n"], n_raw=a["n_raw"], dead=bl(a["dead"]))
    for m in MET:
        d = a.get("d_" + m)
        r["d_%s_mean" % m], r["d_%s_min" % m], r["d_%s_max" % m] = (fl(d["mean"]), fl(d["min"]), fl(d["max"])) if d else (None, None, None)
    r.update(r_p=a["rP"], r_d=a["rD"], r=a["r"], grp=a["group"], grp_p=a["gP"], grp_d=a["gD"], share_pos=a.get("share_pos"), sign_stable=bl(a.get("sign_stable")),
             months_same_sign=a.get("months_same_sign"), months_stable=bl(a.get("months_stable")),
             d_months=json.dumps(a["d_months"]) if a.get("d_months") is not None else None, max_abs_dsh=a.get("max_abs_dsh"), above_noise_sh=bl(a.get("above_noise_sh")),
             max_abs_dusd=a.get("max_abs_dusd"), above_noise_usd=bl(a.get("above_noise_usd")), share_removing=a.get("share_removing"), missing=a.get("missing"),
             dirs=json.dumps(a.get("dirs"), ensure_ascii=False), null_cells=a.get("null_cells"), null_applicable=bl(a.get("null_applicable")), r_null=a.get("r_null"),
             r_null_p95=a.get("r_null_p95"), r_null_p=a.get("r_null_p"), r_null_d=a.get("r_null_d"), not_random=None if "not_distinguishable_from_random" not in a else
             bl(a["not_distinguishable_from_random"]), cl=fam[0], fam=fam[1], fam_ord=fam[2])
    return r


def ins(conn, table, row):
    cols = list(row)
    cur = conn.execute("INSERT INTO %s(%s) VALUES (%s)" % (table, ",".join(cols), ",".join("?" * len(cols))), [row[c] for c in cols])
    return cur.lastrowid


def insmany(conn, table, rows):
    if rows:
        cols = list(rows[0])
        conn.executemany("INSERT INTO %s(%s) VALUES (%s)" % (table, ",".join(cols), ",".join("?" * len(cols))), [[r[c] for c in cols] for r in rows])


def cell_index(conn, side, uni, win):
    """{ckey: id строки metric} (наименьший id — как берёт инструмент при повторе ключа в двух бинарях)."""
    return dict(conn.execute("SELECT ckey, min(id) FROM metric_cur WHERE side=? AND uni=? AND win=? AND ckey IS NOT NULL GROUP BY ckey", (side, uni, win)).fetchall())


def put_run(conn, meta, results, obs, ft, feat_ver, lead, tool, source, run_key, levels, fps=None, git_sha=None, note=None, created=None, cells=None):
    """Один прогон одной транзакцией. results {(сторона, k): словарь level-*.json}, obs {(сторона, k): список obs-*.json}.
    → (id прогона, True) или (id уже записанного прогона с тем же ключом, False). cells {сторона: {ckey: id}} — для JSON без id клеток."""
    ex = conn.execute("SELECT id FROM infl_run WHERE run_key=?", (run_key,)).fetchone()
    if ex:
        return ex[0], False
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not conn.execute("SELECT 1 FROM infl_feat WHERE ver=?", (feat_ver,)).fetchone():
            conn.executemany("INSERT INTO infl_feat(ver, n, name, cat, cat_short, cat_title, grp) VALUES (?,?,?,?,?,?,?)",
                             [(feat_ver, f["n"], nm, f["cat"], f["cat_short"], f["cat_title"], f["grp"]) for nm, f in sorted(ft.items(), key=lambda x: x[1]["n"])])
        run = ins(conn, "infl_run", dict(
            run_key=run_key, created=created or now(), uni=meta["uni"], win=meta["win"], stake=500.0, anchor_entry=334, levels=json.dumps(sorted(levels)),
            tool=json.dumps(tool, sort_keys=True), git_sha=git_sha, source=source, feat_ver=feat_ver, note=note, bins=json.dumps(meta.get("bins")),
            entries=json.dumps(meta.get("entries")), thresholds=json.dumps(meta.get("thresholds")), anchors=json.dumps(meta["anchors"], ensure_ascii=False),
            levels_in_db=json.dumps(meta["levels_in_db"]), excluded=json.dumps(meta.get("excluded"), ensure_ascii=False), dup_keys=meta.get("dup_keys"),
            dup_diff=meta.get("dup_diff"), m_long=meta["m"]["long"], m_short=meta["m"]["short"]))
        for (side, k), r in sorted(results.items()):
            ins(conn, "infl_level", dict(run=run, side=side, k=k, n_cells=r["n_cells"], cells_full=r["by_grid"]["полная"], cells_sel=r["by_grid"]["отбор"], sigma_sh=r["sigma_sh"],
                                         noise_sh=r["noise_sh"], sigma_usd=r["sigma_usd"], noise_usd=r["noise_usd"], m=r["m"], missing=json.dumps(r["missing"], ensure_ascii=False),
                                         fp=(fps or {}).get("%s:%d" % (side, k))))
            cl = r.get("clusters") or {}
            rr = {(a["axis"], a["grid"]): a["r"] for a in r["axes"]}
            member = {}
            for g, c in cl.items():
                for nm in c["names"]:
                    member[(nm, g)] = ("name", None, None)
                for nm in c["small"]:
                    member[(nm, g)] = ("small", None, None)
                for fi, fam in enumerate(c["families"]):
                    for oi, nm in enumerate(fam):
                        member[(nm, g)] = ("name", fi, oi)
                ins(conn, "infl_cluster", dict(run=run, side=side, k=k, grid=g, n_names=len(c["names"]), n_small=len(c["small"]), pairs=c["pairs"], n_fam=len(c["families"])))
                for fi, fam in enumerate(c["families"]):
                    ins(conn, "infl_family", dict(run=run, side=side, k=k, grid=g, fam=fi, size=len(fam), rep_axis=max(fam, key=lambda x: rr.get((x, g)) or 0)))
            aid = {}
            for a in r["axes"]:
                aid[(a["axis"], a["grid"])] = ins(conn, "infl_axis", axis_row(run, side, k, a, ft, lead, member.get((a["axis"], a["grid"]), (None, None, None))))
            idx = (cells or {}).get(side) or {}
            rows = []
            for o in obs.get((side, k), []):
                row = dict(axis_id=aid[(o["axis"], o["grid"])], label=o["label"], cell=o.get("cid") or idx.get(o["cell"]), ref=o.get("rid") or idx.get(o["ref"]), dir=o["dir"],
                           dup=bl(o["dup"]), partners="+".join(sorted(x[2:] if x.startswith("F:") else x for x in o["others"])), others=json.dumps(o["others"], ensure_ascii=False))
                for m in MET:
                    row["c_" + m] = fl(o["c_" + m])
                    row["d_" + m] = fl(o["d_" + m])
                rows.append(row)
                if len(rows) >= 20000:
                    insmany(conn, "infl_obs", rows)
                    rows = []
            insmany(conn, "infl_obs", rows)
            insmany(conn, "infl_inter", [dict(run=run, side=side, k=k, grid=x["grid"], cell=x.get("cid") or idx.get(x["ckey"]), axes=json.dumps(x["axes"], ensure_ascii=False, sort_keys=True),
                                              tsig=x.get("tsig"), n=x["n"], usd_cell=x["usd_cell"], usd=x["usd"], daily_sum=x["daily_sum"], lo=x.get("lo"), hi=x.get("hi"),
                                              p_le0=x.get("p_le0")) for x in r["interactions"]])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return run, True


# ---- чтение --------------------------------------------------------------------------------------------------------------------------
def _axis_dict(r):
    a = dict(n=r["n"], n_raw=r["n_raw"], dead=bool(r["dead"]))
    for m in MET:
        a["d_" + m] = None if r["d_%s_mean" % m] is None and r["d_%s_min" % m] is None else dict(mean=r["d_%s_mean" % m], min=r["d_%s_min" % m], max=r["d_%s_max" % m])
    a.update(rP=r["r_p"], rD=r["r_d"], r=r["r"], group=r["grp"], gP=r["grp_p"], gD=r["grp_d"], share_pos=r["share_pos"], sign_stable=r["sign_stable"],
             months_same_sign=r["months_same_sign"], months_stable=r["months_stable"], d_months=json.loads(r["d_months"]) if r["d_months"] else None,
             max_abs_dsh=r["max_abs_dsh"], above_noise_sh=bool(r["above_noise_sh"]), max_abs_dusd=r["max_abs_dusd"], above_noise_usd=bool(r["above_noise_usd"]),
             share_removing=r["share_removing"], axis=r["axis"], grid=r["grid"], side=r["side"], k=r["k"], missing=r["missing"], dirs=json.loads(r["dirs"]),
             null_cells=r["null_cells"], null_applicable=r["null_applicable"], r_null=r["r_null"], r_null_p95=r["r_null_p95"], r_null_p=r["r_null_p"], r_null_d=r["r_null_d"],
             not_distinguishable_from_random=r["not_random"])
    for key in OPT_KEYS:
        if a.get(key) is None:
            a.pop(key, None)
        elif key in BOOL_KEYS:
            a[key] = bool(a[key])
    return a


def load_level(conn, run, side, k):
    """(res, obs) уровня прогона в виде level-*.json / obs-*.json (без r_ в obs и без ckey; id клеток — cid / rid)."""
    L = conn.execute("SELECT * FROM infl_level WHERE run=? AND side=? AND k=?", (run, side, k)).fetchone()
    if not L:
        return None, None
    res = dict(side=side, k=k, n_cells=L["n_cells"], by_grid={"полная": L["cells_full"], "отбор": L["cells_sel"]}, sigma_sh=L["sigma_sh"], noise_sh=L["noise_sh"],
               sigma_usd=L["sigma_usd"], noise_usd=L["noise_usd"], m=L["m"], axes=[], missing=json.loads(L["missing"]), interactions=[])
    arows = conn.execute("SELECT * FROM infl_axis WHERE run=? AND side=? AND k=? ORDER BY id", (run, side, k)).fetchall()
    res["axes"] = [_axis_dict(r) for r in arows]
    cls = conn.execute("SELECT * FROM infl_cluster WHERE run=? AND side=? AND k=? ORDER BY CASE grid WHEN 'полная' THEN 0 ELSE 1 END", (run, side, k)).fetchall()
    if cls:
        res["clusters"] = {}
        for c in cls:
            g = c["grid"]
            fams = []
            for f in conn.execute("SELECT fam FROM infl_family WHERE run=? AND side=? AND k=? AND grid=? ORDER BY fam", (run, side, k, g)):
                fams.append([x[0] for x in conn.execute("SELECT axis FROM infl_axis WHERE run=? AND side=? AND k=? AND grid=? AND fam=? ORDER BY fam_ord", (run, side, k, g, f[0]))])
            names = [x[0] for x in conn.execute("SELECT axis FROM infl_axis WHERE run=? AND side=? AND k=? AND grid=? AND cl='name' ORDER BY axis", (run, side, k, g))]
            small = [x[0] for x in conn.execute("SELECT axis FROM infl_axis WHERE run=? AND side=? AND k=? AND grid=? AND cl='small' ORDER BY axis", (run, side, k, g))]
            res["clusters"][g] = dict(grid=g, names=names, families=fams, small=small, pairs=c["pairs"])
    for x in conn.execute("SELECT * FROM infl_inter WHERE run=? AND side=? AND k=? ORDER BY id", (run, side, k)):
        d = dict(cid=x["cell"], axes=json.loads(x["axes"]), grid=x["grid"], tsig=x["tsig"], n=x["n"], usd_cell=x["usd_cell"], usd=x["usd"], daily_sum=x["daily_sum"])
        for key in ("lo", "hi", "p_le0"):
            if x[key] is not None:
                d[key] = x[key]
        res["interactions"].append(d)
    obs = []
    for o in conn.execute("SELECT o.*, a.axis, a.grid FROM infl_obs o JOIN infl_axis a ON a.id = o.axis_id WHERE a.run=? AND a.side=? AND a.k=? ORDER BY o.id", (run, side, k)):
        d = dict(axis=o["axis"], label=o["label"], grid=o["grid"], cid=o["cell"], rid=o["ref"], dir=o["dir"], dup=bool(o["dup"]), others=json.loads(o["others"]))
        for m in MET:
            d["c_" + m] = o["c_" + m]
            d["d_" + m] = o["d_" + m]
        obs.append(d)
    return res, obs


def load_run(conn, run=None):
    """(res, obs, meta, runs): по умолчанию для каждого (сторона, k) — последний прогон уровня (вид infl_cur); run=N — только его уровни.
    meta — как meta.json последнего из взятых прогонов; runs {(сторона, k): id прогона}."""
    if run is None:
        pairs = [(r["side"], r["k"], r["run"]) for r in conn.execute("SELECT side, k, run FROM infl_cur ORDER BY side, k")]
    else:
        pairs = [(r["side"], r["k"], run) for r in conn.execute("SELECT side, k FROM infl_level WHERE run=? ORDER BY side, k", (run,))]
    if not pairs:
        raise SystemExit("в БД нет результатов влияния осей (infl_level пуст)")
    res, obs, runs = {}, {}, {}
    for side, k, rid in pairs:
        res[(side, k)], obs[(side, k)] = load_level(conn, rid, side, k)
        runs[(side, k)] = rid
    last = conn.execute("SELECT * FROM infl_run WHERE id=?", (max(runs.values()),)).fetchone()
    meta = dict(run=last["id"], created=last["created"], uni=last["uni"], win=last["win"], m=dict(long=last["m_long"], short=last["m_short"]),
                levels_in_db=json.loads(last["levels_in_db"]), excluded=json.loads(last["excluded"]) if last["excluded"] else {}, dup_keys=last["dup_keys"], dup_diff=last["dup_diff"],
                anchors=json.loads(last["anchors"]), thresholds=json.loads(last["thresholds"]), bins=json.loads(last["bins"]), entries=json.loads(last["entries"]))
    return res, obs, meta, runs


def export_json(conn, run, out):
    """Прогон → каталог в прежнем виде (level-*, obs-*, meta.json): для графиков на ПК."""
    res, obs, meta, _ = load_run(conn, run)
    os.makedirs(out, exist_ok=True)
    for (side, k), r in res.items():
        json.dump(r, open(os.path.join(out, "level-%s-%d.json" % (side, k)), "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(obs[(side, k)], open(os.path.join(out, "obs-%s-%d.json" % (side, k)), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(meta, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(res)


# ---- сверка DB ↔ JSON ----------------------------------------------------------------------------------------------------------------
def _canon(x):
    if isinstance(x, float) and x != x:
        return None
    if isinstance(x, dict):
        d = {k: c for k, c in ((k, _canon(v)) for k, v in x.items()) if c is not None}
        return d or None                       # словарь из одних NaN / None = NULL в БД
    if isinstance(x, list):
        return [_canon(v) for v in x]
    return x


def _diff(a, b, path, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append("%s.%s: %s" % (path, k, "нет в БД" if k not in b else "нет в JSON"))
            else:
                _diff(a[k], b[k], path + "." + k, out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append("%s: длина %d / %d" % (path, len(a), len(b)))
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                _diff(x, y, "%s[%d]" % (path, i), out)
    elif a != b:
        out.append("%s: JSON %r / БД %r" % (path, a, b))


def verify_json(conn, run, dirs, limit=20):
    """Каждая строка JSON (оси, уровень, кластеры, взаимодействия, значения осей) = строка БД; id клеток → ключ клетки в metric. → (строк сверено, расхождения)."""
    ids = {}

    def ckey(i):
        if i not in ids:
            r = conn.execute("SELECT ckey FROM metric WHERE id=?", (i,)).fetchone()
            ids[i] = r[0] if r else None
        return ids[i]

    diffs, rows = [], 0
    for d in dirs:
        for side in SIDES:
            for k in range(1, 9):
                f = os.path.join(d, "level-%s-%d.json" % (side, k))
                if not os.path.exists(f):
                    continue
                jr, jo = json.load(open(f, encoding="utf-8")), json.load(open(os.path.join(d, "obs-%s-%d.json" % (side, k)), encoding="utf-8"))
                dr, do = load_level(conn, run, side, k)
                if dr is None:
                    diffs.append("%s %d: нет в БД" % (side, k))
                    continue
                pre = "%s/%d" % (side, k)
                head = {x: jr[x] for x in jr if x not in ("axes", "interactions", "clusters")}
                _diff(_canon(head), _canon({x: dr[x] for x in dr if x in head}), pre, diffs)
                _diff(_canon(jr["axes"]), _canon(dr["axes"]), pre + ".axes", diffs)
                _diff(_canon(jr.get("clusters") or {}), _canon(dr.get("clusters") or {}), pre + ".clusters", diffs)
                ji = [{x: v for x, v in i.items() if x not in ("ckey", "cell", "cid")} for i in jr["interactions"]]
                di = [{x: v for x, v in i.items() if x != "cid"} for i in dr["interactions"]]
                _diff(_canon(ji), _canon(di), pre + ".inter", diffs)
                for j, i in zip(jr["interactions"], dr["interactions"]):
                    if ckey(i["cid"]) != j["ckey"]:
                        diffs.append("%s.inter: клетка %s ≠ metric #%s" % (pre, j["ckey"], i["cid"]))
                jo2 = [{x: v for x, v in o.items() if x not in ("cell", "ref", "cid", "rid", "r_n", "r_usd", "r_mean", "r_sh", "r_dd", "r_ddp", "r_gap", "r_rr")} for o in jo]
                do2 = [{x: v for x, v in o.items() if x not in ("cid", "rid")} for o in do]
                _diff(_canon(jo2), _canon(do2), pre + ".obs", diffs)
                for j, o in zip(jo, do):
                    if ckey(o["cid"]) != j["cell"] or (o["rid"] is not None and ckey(o["rid"]) != j["ref"]):
                        diffs.append("%s.obs: клетка / опора %s ≠ metric #%s / #%s" % (pre, j["cell"], o["cid"], o["rid"]))
                rows += len(jr["axes"]) + len(jr["interactions"]) + len(jo)
                if len(diffs) > limit:
                    return rows, diffs[:limit] + ["…"]
    return rows, diffs


def import_json(conn, dirs, features, tool_dir=None, git_sha=None, note=None):
    """Прежние выгрузки compute (level-*, obs-*, meta.json) → один прогон (первый перенос: снимок отчёта TK-689 от 23.11). Тот же набор файлов — тот же ключ."""
    res, obs, meta, hashes = {}, {}, None, {}
    for d in dirs:
        for side in SIDES:
            for k in range(1, 9):
                f = os.path.join(d, "level-%s-%d.json" % (side, k))
                if os.path.exists(f):
                    res[(side, k)] = json.load(open(f, encoding="utf-8"))
                    obs[(side, k)] = json.load(open(os.path.join(d, "obs-%s-%d.json" % (side, k)), encoding="utf-8"))
                    hashes["%s:%d" % (side, k)] = sha1(open(f, "rb").read() + open(os.path.join(d, "obs-%s-%d.json" % (side, k)), "rb").read())
        meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
    ft, ver = feat_table(features)
    tool = {}
    for nm in ("tk689_influence.py", "tk674_axes.py"):
        p = os.path.join(tool_dir or "", nm)
        if tool_dir and os.path.exists(p):
            tool[nm] = sha1(open(p, "rb").read())[:10]
    cells = {s: cell_index(conn, s, meta["uni"], meta["win"]) for s in SIDES}
    lead = {}
    try:
        import tk689_influence as ti
        lead = ti.LEAD
    except ImportError:
        pass
    run_key = sha1(json.dumps(dict(source="import-json", hashes=hashes, tool=tool, feat=ver), sort_keys=True))
    return put_run(conn, meta, res, obs, ft, ver, lead, tool, "import-json:" + ",".join(os.path.basename(d.rstrip("/")) for d in dirs), run_key, sorted({k for _, k in res}),
                   fps=None, git_sha=git_sha, note=note, cells=cells)


# ---- запросы -------------------------------------------------------------------------------------------------------------------------
def table(rows, head, right=()):
    w = [max(len(str(x)) for x in col) for col in zip(head, *rows)] if rows else [len(h) for h in head]
    line = lambda r: "  ".join((str(x).rjust(w[i]) if i in right else str(x).ljust(w[i])) for i, x in enumerate(r))  # noqa: E731
    return "\n".join([line(head), line(["-" * x for x in w])] + [line(r) for r in rows])


def f(x, fmt="%.1f", sign=False):
    if x is None:
        return "—"
    s = fmt % x
    return ("+" + s) if sign and x > 0 else s


def pct(x):
    return "—" if x is None else "%.0f %%" % (100 * x)


def run_for(conn, side, k, run):
    """Прогон для (сторона, k): заданный или последний, где уровень есть; прогоны не смешиваются."""
    if run is not None:
        r = conn.execute("SELECT run FROM infl_level WHERE run=? AND side=? AND k=?", (run, side, k)).fetchone()
        return r[0] if r else None
    r = conn.execute("SELECT run FROM infl_cur WHERE side=? AND k=?", (side, k)).fetchone()
    return r[0] if r else None


def header(conn, run, side, k, grid):
    r = conn.execute("SELECT * FROM infl_run WHERE id=?", (run,)).fetchone()
    return "вселенная %s, окно %s, прогон #%d (%s), %s, уровень %d, сетка «%s»%s. Описательно, найдено на этих данных." % (
        r["uni"], r["win"], run, r["created"][:16].replace("T", " "), "лонг" if side == "long" else "шорт", k, grid,
        "; оси отобраны по результату на этих данных, эффект — условный" if grid == "отбор" else "")


def cmd_runs(conn, a):
    rows = []
    for r in conn.execute("SELECT r.*, (SELECT count(*) FROM infl_axis WHERE run=r.id) AS na, (SELECT count(*) FROM infl_obs o JOIN infl_axis x ON x.id=o.axis_id WHERE x.run=r.id) AS no,"
                          " (SELECT count(*) FROM infl_inter WHERE run=r.id) AS ni FROM infl_run r ORDER BY r.id"):
        rows.append((r["id"], r["created"][:16], r["uni"], r["levels"], r["source"][:28], r["na"], r["no"], r["ni"], (r["git_sha"] or "")[:9], r["run_key"][:8]))
    print(table(rows, ("#", "создан", "вселенная", "уровни", "источник", "осей", "значений", "взаимодейств.", "git", "ключ"), (0, 5, 6, 7)))


ORDER = {"r": "a.r", "rP": "a.r_p", "rD": "a.r_d", "excess": "(a.r - a.r_null)"}


def cmd_top(conn, a):
    k, side = a.level, a.side
    run = run_for(conn, side, k, a.run)
    if run is None:
        raise SystemExit("нет прогона с уровнем %d (%s)" % (k, side))
    q = ("SELECT a.* FROM infl_axis a WHERE a.run=? AND a.side=? AND a.k=? AND a.grid=? AND a.grp != 'не оценена'" + (" AND a.cat=?" if a.cat else "") +
         (" AND a.r_null IS NOT NULL" if a.by == "excess" else "") + " ORDER BY %s DESC, a.id LIMIT ?" % ORDER[a.by])
    args = [run, side, k, a.grid] + ([a.cat] if a.cat else []) + [a.n]
    rows = []
    for x in conn.execute(q, args):
        rows.append((x["axis"] + ("" if x["grid"] == "полная" else " ·отбор"), x["feat_no"] or "—", x["cat_short"] or "без №", x["n"], f(x["d_usd_mean"], "%.0f", True), f(x["d_dd_mean"], "%.1f", True),
                     f(x["d_ddp_mean"], "%.1f", True), f(x["d_sh_mean"], "%.2f", True), pct(x["r_p"]), pct(x["r_d"]), f(None if x["r_null"] is None else x["r"] - x["r_null"], "%.2f", True),
                     x["grp"], "случайное" if x["not_random"] else ""))
    print("Топ осей по %s · %s" % (a.by, header(conn, run, side, k, a.grid)))
    print(table(rows, ("ось", "№", "вкладка", "n", "Δ Σ $", "Δ просадка $", "Δ п.п. маржи", "Δ Шарп", "rP", "rD", "r − r_null", "группа", "нулевая"), (1, 3, 4, 5, 6, 7, 8, 9, 10)))


def _mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def rollup_rows(rows):
    """Строки infl_axis ОДНОГО (прогон, сторона, k, сетка) → {вкладка: показатели}. Смешение прогонов / уровней / сеток — остановка."""
    keys = {(r["run"], r["side"], r["k"], r["grid"]) for r in rows}
    if len(keys) > 1:
        raise SystemExit("роллап: смешаны прогоны / стороны / уровни / сетки: %s" % sorted(keys))
    by = {}
    for r in rows:
        by.setdefault(r["cat_short"] or "без №", []).append(r)
    out = {}
    for cat, xs in by.items():
        nu = [x for x in xs if x["null_applicable"] and x["r_null"] is not None]
        nup = [x for x in xs if x["null_applicable"] and x["r_null_p"] is not None]
        nud = [x for x in xs if x["null_applicable"] and x["r_null_d"] is not None]
        out[cat] = dict(n=len(xs), dead=sum(1 for x in xs if x["dead"]), n_null=len(nu), rP=_mean([x["r_p"] for x in xs]), rD=_mean([x["r_d"] for x in xs]),
                        many_p=sum(1 for x in xs if x["grp_p"] == "много"), many_d=sum(1 for x in xs if x["grp_d"] == "много"),
                        many_beyond=sum(1 for x in xs if x["grp"] == "много" and x["null_applicable"] and not x["not_random"]),
                        d_usd=_mean([x["d_usd_mean"] for x in xs]), d_dd=_mean([x["d_dd_mean"] for x in xs]),
                        ex=_mean([x["r"] - x["r_null"] for x in nu]), exP=_mean([x["r_p"] - x["r_null_p"] for x in nup]) if nup else None,
                        exD=_mean([x["r_d"] - x["r_null_d"] for x in nud]) if nud else None)
    return out


def rank_by(stats, key):
    ok = [(c, s) for c, s in stats.items() if s[key] is not None and s["n_null"] >= PRIMARY_FLOOR and c != "без №"]
    return [c for c, _ in sorted(ok, key=lambda cs: -cs[1][key])]


def cmd_rollup(conn, a):
    sides = (a.side,) if a.side else SIDES
    print("Роллап по вкладкам «Признаки» (правило TK-693; критик thr_m27kv64m9h: PASS + 4 правки). «Сильнее сдвигает» — только про эти таблицы; это не вывод об оси и не рекомендация.\n"
          "Первичная мера — среднее (r − r_null) по осям с нулевой моделью (размер сверх случайного выкидывания сделок); rP / rD — справочно, по всем осям. "
          "Порог показа «ранг» — не меньше %d осей с нулевой моделью: назначено (правило TK-693), не измерено. Прогоны не смешиваются." % PRIMARY_FLOOR)
    for side in sides:
        for k in ([a.level] if a.level else sorted({r[0] for r in conn.execute("SELECT k FROM infl_level WHERE side=?", (side,))})):
            run = run_for(conn, side, k, a.run)
            if run is None:
                continue
            for grid in ([a.grid] if a.grid else GRIDS):
                rows = conn.execute("SELECT * FROM infl_axis WHERE run=? AND side=? AND k=? AND grid=? AND grp != 'не оценена'", (run, side, k, grid)).fetchall()
                if not rows:
                    continue
                st = rollup_rows(rows)
                comp = [r for r in rows if r["lead"]]
                st2 = rollup_rows([r for r in rows if not r["lead"]])
                has_pd = any(s["exP"] is not None for s in st.values())
                keys = [("ex", "r − r_null (итог)")] + ([("exP", "rP − r_null_p"), ("exD", "rD − r_null_d")] if has_pd else [])
                rk = {key: rank_by(st, key) for key, _ in keys}
                rk2 = {key: rank_by(st2, key) for key, _ in keys}
                print("\n== %s" % header(conn, run, side, k, grid))
                body = []
                for cat, s in sorted(st.items(), key=lambda cs: (-(cs[1]["ex"] if cs[1]["ex"] is not None else -9), cs[0])):
                    body.append((cat, s["n"], s["dead"], s["n_null"], f(s["ex"], "%.2f", True)) + ((f(s["exP"], "%.2f", True), f(s["exD"], "%.2f", True)) if has_pd else ()) +
                                (pct(s["rP"]), pct(s["rD"]), "%d / %d" % (s["many_p"], s["many_d"]), s["many_beyond"], f(s["d_usd"], "%.0f", True), f(s["d_dd"], "%.1f", True),
                                 (rk["ex"].index(cat) + 1) if cat in rk["ex"] else "—", (rk2["ex"].index(cat) + 1) if cat in rk2["ex"] else "—"))
                print(table(body, ("вкладка", "осей", "мёртвых", "с нул. моделью", "r − r_null") + (("rP − r_null_p", "rD − r_null_d") if has_pd else ()) +
                            ("rP", "rD", "много П / У", "много сверх нулевой", "Δ Σ $ ср.", "Δ просадка $ ср.", "ранг", "ранг без составных"), tuple(range(1, 99))))
                if not has_pd:
                    print("rP / rD по отдельности против нулевой модели в этом прогоне не записаны (нулевая модель сохранена одним числом r_null = max(П, У)); ранг — по r − r_null.")
                for key, nm in keys:
                    same = rk[key] == rk2[key]
                    print("порядок по «%s»: %s; без составных осей: %s — %s" % (nm, " > ".join(rk[key]) or "—", " > ".join(rk2[key]) or "—",
                                                                               "порядок не изменился" if same else "ПОРЯДОК ИЗМЕНИЛСЯ"))
                if comp:
                    print("составные оси (№ взят у ведущего ключа; выбор назначен кодом TK-689): " + "; ".join("%s → %s (№ %s) → %s" % (r["axis"], r["lead"], r["feat_no"], r["cat_short"]) for r in comp))


# ---- запуск: на srv — прямо, с ПК — тем же файлом по ssh ----------------------------------------------------------------------------
def local_db():
    return os.environ.get("CHRON_DB") or (SRV_DB if os.path.isdir(os.path.dirname(SRV_DB)) else None)


def remote(argv):
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    cm = chron_mod()
    cmd = cm.ssh_cmd() + ["PYTHONIOENCODING=utf-8 python3 - " + " ".join(shlex.quote(x) for x in argv)]
    r = subprocess.run(cmd, input=src.encode("utf-8"), capture_output=True)
    sys.stdout.buffer.write(r.stdout)
    if r.returncode:
        sys.stderr.write(r.stderr.decode("utf-8", "replace")[-1500:])
    return r.returncode


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("runs")
    t = sub.add_parser("top")
    t.add_argument("--side", required=True, choices=SIDES)
    t.add_argument("--level", type=int, required=True)
    t.add_argument("--grid", default="полная", choices=GRIDS)
    t.add_argument("--by", default="r", choices=sorted(ORDER))
    t.add_argument("--n", type=int, default=10)
    t.add_argument("--cat", default=None, help="id вкладки (natr нет: pstop, pvol, pexit, flow, setup …)")
    t.add_argument("--run", type=int, default=None)
    r = sub.add_parser("rollup")
    r.add_argument("--side", choices=SIDES)
    r.add_argument("--level", type=int)
    r.add_argument("--grid", choices=GRIDS)
    r.add_argument("--run", type=int, default=None)
    i = sub.add_parser("import-json")
    i.add_argument("dirs")
    i.add_argument("--features", required=True)
    i.add_argument("--tool-dir")
    i.add_argument("--git-sha")
    i.add_argument("--note")
    v = sub.add_parser("verify")
    v.add_argument("--run", type=int, required=True)
    v.add_argument("--json", required=True)
    e = sub.add_parser("export-json")
    e.add_argument("--run", type=int, default=None)
    e.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not (a.db or local_db()):
        return remote(argv)
    db = a.db or local_db()
    if a.cmd in ("runs", "top", "rollup", "verify", "export-json"):
        conn = connect(db, ro=True)
    else:
        conn = connect(db)
    conn.row_factory = sqlite3.Row
    if a.cmd == "runs":
        cmd_runs(conn, a)
    elif a.cmd == "top":
        cmd_top(conn, a)
    elif a.cmd == "rollup":
        cmd_rollup(conn, a)
    elif a.cmd == "import-json":
        rid, new = import_json(conn, a.dirs.split(","), a.features, a.tool_dir, a.git_sha, a.note)
        print("прогон #%d %s" % (rid, "записан" if new else "уже был (тот же набор файлов)"))
    elif a.cmd == "verify":
        rows, diffs = verify_json(conn, a.run, a.json.split(","))
        print("сверено строк: %d; расхождений: %d" % (rows, len(diffs)))
        for d in diffs:
            print("  " + d)
        return 1 if diffs else 0
    elif a.cmd == "export-json":
        print("уровней выгружено: %d" % export_json(conn, a.run, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
