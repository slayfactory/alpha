"""TK-689: оси, уровни, эффекты, группы, нулевая модель, взаимодействия (tk689_influence.py) — синтетические клетки, без БД."""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk689_influence as ti  # noqa: E402

AL = ti.parse_ckey("ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12")[0]
AS = ti.parse_ckey("bm=1 e=0.15 e2=0.05 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20")[0]


def axes(ck, anc=AL):
    k, f = ti.parse_ckey(ck)
    return ti.assign_axes(k, f, anc)


def test_assign_axes_families_and_companions():
    base = "ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 s=3 t24=12"
    assert axes(base + " rf=0.2 rs=20") == {}
    assert axes(base) == {"rf": "rf=0,rs=0"}                                                  # rf=0 → canon убирает rf и rs: одна ось, rs спутник
    assert axes("ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=5 s=3 t24=12") == {"rs": "5"}
    assert axes("ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=2 t24=12") == {"s": "2"}
    a = axes("ea=1.5 ev=1 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12")
    assert list(a) == ["ev"] and "ea=1.5" in a["ev"] and "ev=1" in a["ev"]
    a = axes("ew=5 fh=4 fm=50 lat=4 latx=4 natr_min=3.24 nf=8 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12")
    assert list(a) == ["nf"]                                                                  # natr_min — спутник nf
    a = axes("e2t=3 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12 w2=80")
    assert list(a) == ["split"]
    a = axes("ch=12.7 cl=0 cm=0.5 cw=60 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 sm=0.5 t24=12")
    assert list(a) == ["chan"]                                                                # sm — спутник канала
    a = axes("ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 sl=3.5 sm=0.5 t24=12")
    assert list(a) == ["sl"]


def test_assign_axes_short_e2_companions_and_filters():
    s = "bm=1 e=0.15 ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=2 s=4 t24=12 tb=0.1 tt=20"
    assert list(axes(s + " e1t=3", AS)) == ["e1t"]                                            # e2 = 0 (нет в ключе) — спутник e1t
    assert list(axes(s + " xb=0.8", AS)) == ["xb"]
    assert list(axes(s + " e2=0.05 e2t=2 w2=70", AS)) == ["split"]
    assert list(axes(s + " e2=0.1", AS)) == ["split"]                                         # одиночный e2 у шорта — split
    a = axes("ew=5 fh=4 fm=50 lat=4 latx=4 pb=1 pk=1 rf=0.2 rs=20 s=3 t24=12 F=cons60<=2&n24>=140000")
    assert a == {"F:cons60": "<=2", "F:n24": ">=140000"}
    k, f = ti.parse_ckey("a=1 F=x>=q20")
    assert k == {"a": "1"} and f == ["x>=q20"]


def test_excluded():
    assert ti.excluded({"ew": "0", "s": "3"}) == "ew0"
    assert ti.excluded({"ew": "5", "s": "5"}) == "s5"
    assert ti.excluded({"ew": "5", "s": "5", "e2t": "2"}) == "s5_split"
    assert ti.excluded({"ew": "5", "s": "4"}) is None
    assert ti.excluded({"s": "3"}) == "ew0" and ti.excluded({"ew": "5"}) == "s5"             # ключ со значением по умолчанию (ew = 0, s = 5) в ckey не входит


def mk(entry, ckey, usd, dd, n, side="long", tsig=None, sh=1.0, daily=None, months=None):
    k, f = ti.parse_ckey(ckey)
    return dict(id=entry * 1000 + n, entry=entry, side=side, cell=ckey, ckey=ckey, bin="b", keys=k, filt=f, n=n, usd=usd, mean=usd / n, sh=sh, dd=dd, ddp=dd / 2.5,
                gap=5, rr=1.0, months=months or [[n // 5, usd / 5]] * 5, daily=daily if daily is not None else np.full(10, usd / 10.0), tsig=tsig or ckey, dirs=None)


BASE = "ew=5 rf=0.2 rs=20 s=3 t24=12"


def world():
    cells = [mk(334, BASE, 100.0, 20.0, 50, tsig="anchor"),
             mk(336, BASE.replace("rs=20", "rs=5"), 130.0, 22.0, 50, tsig="A1"),       # ось rs
             mk(336, BASE.replace("rs=20", "rs=5") + " tt=5", 150.0, 25.0, 40, tsig="AB"),   # tt поверх rs
             mk(336, BASE + " tt=5", 90.0, 18.0, 45, tsig="B1"),
             mk(336, BASE + " tt=10", 100.0, 20.0, 50, tsig="anchor")]                  # мёртвый: те же сделки
    sides, stat = ti.prepare(cells)
    return sides["long"], stat


def test_levels_reference_and_dead():
    sd, _ = world()
    assert sorted(c["k"] for c in sd["cells"]) == [0, 1, 1, 1, 2]       # 0 — якорь
    o1, miss = ti.observations(sd, 1)
    assert {(a, lab) for a, lab, c, r in o1} == {("rs", "5"), ("tt", "5"), ("tt", "10")}
    assert all(r is sd["anchor"] for *_, r in o1)
    o2, _ = ti.observations(sd, 2)
    ref = {a: r["ckey"] for a, lab, c, r in o2}
    assert "rs=5" in ref["tt"] and "tt=" not in ref["tt"] and "tt=5" in ref["rs"] and "rs=5" not in ref["rs"]   # C∖tt = ось rs; C∖rs = ось tt
    dead = [o for o in o1 if o[0] == "tt" and o[1] == "10"]
    st = ti.axis_stats(ti.collapse(dead), sd["anchor"], 5.0, 50.0, 1)
    assert st["dead"] and st["group"] == "не оценена"                  # n_k < 2 не группируется, но мёртвый ключ виден
    st = ti.axis_stats(dead * 2, sd["anchor"], 5.0, 50.0, 2)
    assert st["dead"] and st["group"] == "ничего"


def test_groups_by_thresholds_and_missing_reference():
    sd, _ = world()
    o1, _ = ti.observations(sd, 1)
    anc = sd["anchor"]
    rs = [o for o in o1 if o[0] == "rs"] * 2                      # n < 2 после collapse не должно быть: добавим второе значение
    st = ti.axis_stats(ti.collapse(rs), anc, 5.0, 50.0, 2)
    assert st["group"] == "не оценена" and st["n"] == 1 and st["n_raw"] == 2
    tt = [o for o in o1 if o[0] == "tt"]
    st = ti.axis_stats(ti.collapse(tt), anc, 5.0, 50.0, 2)
    assert st["n"] == 2 and not st["dead"]
    assert abs(st["rP"] - np.mean([abs(-10.0), abs(0.0)]) / 100.0) < 1e-12 and abs(st["rD"] - np.mean([2.0, 0.0]) / 20.0) < 1e-12
    assert st["group"] == "мало" and ti.group_of(0.30) == "много" and ti.group_of(0.0499) == "ничего" and ti.group_of(0.05) == "мало"
    assert st["above_noise_usd"] is False and st["share_removing"] == 0.5


def test_collapse_counts_identical_trade_sets_once():
    a = mk(336, BASE + " tt=5", 90.0, 18.0, 45, tsig="X")
    b = mk(336, BASE + " tt=10", 90.0, 18.0, 45, tsig="X")
    anc = mk(334, BASE, 100.0, 20.0, 50, tsig="anchor")
    got = ti.collapse([("tt", "5", a, anc), ("tt", "10", b, anc)])
    assert len(got) == 1


def test_interaction_order2_and_order3_additive_is_zero():
    anc = mk(334, BASE, 100.0, 20.0, 50, daily=np.full(10, 10.0))
    base = ti.parse_ckey(BASE)[0]
    sd = dict(anchor=anc, idx={})

    def put(ck, usd):
        c = mk(337, ck, usd, 10.0, 40, daily=np.full(10, usd / 10.0))
        c["axes"] = ti.assign_axes(c["keys"], c["filt"], base)
        c["sig"] = ti.sig_of(c["axes"])
        sd["idx"][c["sig"]] = c
        return c
    put(BASE.replace("s=3", "s=2"), 130.0)          # +30
    put(BASE.replace("rs=20", "rs=5"), 90.0)        # −10
    put(BASE + " tt=5", 120.0)                      # +20
    ab = put(BASE.replace("s=3", "s=2").replace("rs=20", "rs=5"), 125.0)       # аддитивно было бы 120 → +5
    ac = put(BASE.replace("s=3", "s=2") + " tt=5", 150.0)                      # аддитивно 150 → 0
    bc = put(BASE.replace("rs=20", "rs=5") + " tt=5", 110.0)                   # аддитивно 110
    abc = put(BASE.replace("s=3", "s=2").replace("rs=20", "rs=5") + " tt=5", 140.0)
    assert abs(ti.interaction(ab, sd)["usd"] - 5.0) < 1e-9 and abs(ti.interaction(ac, sd)["usd"]) < 1e-9
    # порядок 3: y(abc) − y(ab) − y(ac) − y(bc) + y(a) + y(b) + y(c) − y(0) = 140 − 125 − 150 − 110 + 130 + 90 + 120 − 100 = −5
    assert abs(ti.interaction(abc, sd)["usd"] + 5.0) < 1e-9
    sd["idx"].pop(next(k for k, v in sd["idx"].items() if v is bc))
    assert ti.interaction(abc, sd) is None                    # нет подклетки — взаимодействие не считается
    ib = ti.boot_idx(10, B=200)
    it = ti.interaction(ab, sd, ib)
    assert it["lo"] <= it["hi"] and 0.0 <= it["p_le0"] <= 1.0


def test_dd_batch_matches_definition_and_null_draw():
    d = np.array([[1.0, -2.0, 3.0, -4.0, 1.0], [-1.0, -1.0, 5.0, 0.0, 0.0]])
    assert np.allclose(ti.dd_batch(d), [4.0, 2.0])                  # пик от 0: накопл. 1, −1, 2, −2, −1 → просадка 4; −1, −2, 3 → 2
    rng = np.random.default_rng(1)
    net = np.array([5.0, -3.0, 2.0, 1.0])
    day = np.array([0, 0, 1, 2])
    z1, z2 = ti.null_draw(np.arange(4), net, day, 0, rng, 50, 3)
    assert not z1.any() and not z2.any()
    a, b = ti.null_draw(np.arange(4), net, day, 1, np.random.default_rng(2), 200, 3)
    assert set(np.round(np.unique(a), 6)) <= {5.0, 3.0, 2.0, 1.0} and (b >= 0).all()


def test_relation_selection_same_other():
    k1, n1 = np.array([1, 2, 3, 4]), np.array([1.0, 2.0, 3.0, 4.0])
    assert ti.relation(k1, n1, np.array([2, 4]), np.array([2.0, 4.0])) == "selection"
    assert ti.relation(np.array([2, 4]), np.array([2.0, 4.0]), k1, n1) == "selection"
    assert ti.relation(k1, n1, k1, n1) == "same"
    assert ti.relation(k1, n1, np.array([2, 4]), np.array([2.0, 4.5])) == "other"      # P&L общей сделки изменился
    assert ti.relation(k1, n1, np.array([2, 9]), np.array([2.0, 4.0])) == "selection"  # убрано и добавлено, P&L общей сделки тот же (занятая монета)


def test_affected_overlap_components():
    ak, an = np.array([1, 2, 3, 4]), np.array([1.0, 1, 1, 1])
    ck, cn = np.array([1, 2, 4, 9]), np.array([1.0, 2.0, 1.0, 1.0])
    assert list(ti.affected(ck, cn, ak, an)) == [2, 3, 9]
    assert abs(ti.jaccard(np.array([2, 3, 9]), np.array([3, 9])) - 2 / 3) < 1e-12
    assert ti.components(["a", "b", "c", "d"], [("a", "b"), ("b", "c")]) == [["a", "b", "c"]]


def test_noise_formula():
    v = [1.0, 3.0, 5.0, 3.0]
    s, n = ti.noise_of(v, 1000)
    assert abs(s - np.std(v)) < 1e-12 and abs(n - s * (2 * np.log(1000)) ** 0.5) < 1e-12
    assert ti.noise_of([1.0], 1000) == (None, None)


def test_upgma_average_linkage_not_chain():
    names = ["a", "b", "c", "d"]
    sims = {("a", "b"): 0.9, ("b", "c"): 0.6, ("c", "d"): 0.9}      # цепочка a–b–c–d: компоненты дали бы одну семью, среднее связывание — две
    fam = ti.upgma(names, sims, 0.7)
    assert sorted(map(tuple, fam)) == [("a", "b"), ("c", "d")]
    assert ti.upgma(["a"], {}, 0.5) == [] and ti.upgma(["a", "b"], {}, 0.5) == []


def test_jaccard():
    assert abs(ti.jaccard(np.array([1, 2, 3]), np.array([2, 3, 4])) - 0.5) < 1e-12 and ti.jaccard(np.array([]), np.array([1])) == 0.0


# ---- TK-693: влияние осей в БД летописи (tk689_db.py) --------------------------------------------------------------------------------
import json  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

import tk689_db as dbm  # noqa: E402
import tk689_report as rp  # noqa: E402

FEATURES = os.path.join(HERE, "ml_features.json")


def world_db():
    """Клетки уровней 1–2 с уникальными id (как строки metric) → (результаты уровней, obs, meta, клетки)."""
    cells = [mk(334, BASE, 100.0, 20.0, 50, tsig="anchor"),
             mk(336, BASE.replace("rs=20", "rs=5"), 130.0, 22.0, 50, tsig="A1"),
             mk(336, BASE.replace("rs=20", "rs=8"), 120.0, 21.0, 48, tsig="A2"),
             mk(336, BASE + " tt=5", 90.0, 18.0, 45, tsig="B1"),
             mk(336, BASE + " tt=10", 70.0, 17.0, 40, tsig="B2"),
             mk(337, BASE.replace("rs=20", "rs=5") + " tt=5", 150.0, 25.0, 40, tsig="AB"),
             mk(337, BASE.replace("rs=20", "rs=8") + " tt=5", 140.0, 24.0, 38, tsig="AB2"),
             mk(337, BASE.replace("rs=20", "rs=5") + " tt=10", 110.0, 23.0, 36, tsig="AB3"),
             mk(337, BASE.replace("rs=20", "rs=8") + " tt=10", 100.0, 22.0, 35, tsig="AB4")]
    for i, c in enumerate(cells):
        c["id"] = 100 + i
        c["rr"] = float("nan") if i == 4 else 1.0                                   # R:R не определён → NaN → NULL
    sides, stat = ti.prepare(cells)
    sd = sides["long"]
    results, obs = {}, {}
    for k in (1, 2):
        res, o = ti.compute_level("long", sd, k, 1000, None, False)
        if k == 1:
            res["clusters"] = {"полная": dict(grid="полная", names=["rs", "tt"], families=[["rs", "tt"]], small=[], pairs=1)}
        results[("long", k)], obs[("long", k)] = res, o
    meta = dict(uni="c539", win="01.07–13.11", m=dict(long=1000, short=1000), levels_in_db={"long": {"0": 1, "1": 4, "2": 4}}, excluded={}, dup_keys=0, dup_diff=0,
                anchors={"long": dict(ckey=BASE, n=50, usd=100.0, dd=20.0, sh=1.0, gap=5)}, thresholds=dict(many=0.3, little=0.05), bins=["b"], entries=[334, 336, 337])
    return results, obs, meta, cells


def db_with_cells(cells):
    """Временная БД летописи (схема v8) со строками metric под id клеток."""
    d = tempfile.mkdtemp()
    conn = dbm.connect(os.path.join(d, "t.sqlite"))
    conn.execute("PRAGMA foreign_keys=OFF")
    for c in cells:
        conn.execute("INSERT INTO metric(id, entry, side, cell, uni, win, trades, usd, src, h, created, ckey, bin) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (c["id"], c["entry"], "long", c["cell"], "c539", "01.07–13.11", c["n"], c["usd"], "x", "h%d" % c["id"], "2026-11-23", c["ckey"], "b"))
    conn.row_factory = dbm.sqlite3.Row
    return conn, d


def write_json(results, obs, meta, d):
    os.makedirs(d, exist_ok=True)
    for (side, k), r in results.items():
        json.dump(r, open(os.path.join(d, "level-%s-%d.json" % (side, k)), "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(obs[(side, k)], open(os.path.join(d, "obs-%s-%d.json" % (side, k)), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(meta, open(os.path.join(d, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False)


def put(conn, results, obs, meta, key="k1", **kw):
    ft, ver = dbm.feat_table(FEATURES)
    return dbm.put_run(conn, meta, results, obs, ft, ver, ti.LEAD, {"t": "1"}, "test", key, [1, 2], **kw)


def test_db_roundtrip_all_rows_equal_json():
    results, obs, meta, cells = world_db()
    conn, d = db_with_cells(cells)
    try:
        rid, new = put(conn, results, obs, meta)
        assert new and rid == 1
        jd = os.path.join(d, "json")
        write_json(results, obs, meta, jd)
        rows, diffs = dbm.verify_json(conn, rid, [jd])
        assert diffs == [] and rows == sum(len(r["axes"]) + len(r["interactions"]) for r in results.values()) + sum(len(o) for o in obs.values())
        res, ob = dbm.load_level(conn, rid, "long", 2)
        assert all(isinstance(a["dead"], bool) and isinstance(a["above_noise_sh"], bool) for a in res["axes"])
        assert any(o["c_rr"] is None for o in dbm.load_level(conn, rid, "long", 1)[1])          # NaN хранится как NULL
        assert res["interactions"][0]["cid"] in {c["id"] for c in cells}
        conn.execute("DROP TRIGGER infl_axis_noupdate")                         # порча одной строки видна сверке
        conn.execute("UPDATE infl_axis SET r_p = r_p + 0.001 WHERE id = (SELECT min(id) FROM infl_axis)")
        assert dbm.verify_json(conn, rid, [jd])[1]
    finally:
        conn.close()
        shutil.rmtree(d, ignore_errors=True)


def test_db_report_text_same_from_db_and_json():
    results, obs, meta, cells = world_db()
    conn, d = db_with_cells(cells)
    try:
        rid, _ = put(conn, results, obs, meta)
        res_db, obs_db, meta_db, runs = dbm.load_run(conn, rid)
        assert runs == {("long", 1): rid, ("long", 2): rid}
        F = rp.feat_numbers(FEATURES)
        rp.DRY = True
        for side, k in results:
            assert rp.level_table(res_db[(side, k)], F) == rp.level_table(results[(side, k)], F)
        assert rp.cluster_section(res_db) == rp.cluster_section(results)
        assert rp.interaction_section(res_db) == rp.interaction_section(results)
        assert rp.top_section(res_db, F) == rp.top_section(results, F) and rp.groups_section(res_db) == rp.groups_section(results)
        assert rp.natr_section(res_db, obs_db, meta_db, F, "p", "r") == rp.natr_section(results, obs, meta, F, "p", "r")
    finally:
        rp.DRY = False
        conn.close()
        shutil.rmtree(d, ignore_errors=True)


def test_db_run_idempotent_new_run_old_stays_and_immutable():
    results, obs, meta, cells = world_db()
    conn, d = db_with_cells(cells)
    try:
        r1, n1 = put(conn, results, obs, meta, "k1")
        r1b, n1b = put(conn, results, obs, meta, "k1")
        assert (n1, n1b, r1b) == (True, False, r1)
        n_axes = conn.execute("SELECT count(*) FROM infl_axis").fetchone()[0]
        r2, n2 = put(conn, {("long", 2): results[("long", 2)]}, {("long", 2): obs[("long", 2)]}, meta, "k2")
        assert n2 and r2 == 2 and conn.execute("SELECT count(*) FROM infl_axis WHERE run=?", (r1,)).fetchone()[0] == n_axes
        cur = {(r["side"], r["k"]): r["run"] for r in conn.execute("SELECT * FROM infl_cur")}
        assert cur == {("long", 1): 1, ("long", 2): 2}                          # уровень 2 — из последнего прогона, уровень 1 — из первого
        _, _, _, runs = dbm.load_run(conn)
        assert runs == cur
        for sql in ("UPDATE infl_run SET note='x'", "DELETE FROM infl_axis", "UPDATE infl_obs SET dup=1", "DELETE FROM infl_inter"):
            try:
                conn.execute(sql)
                assert False, sql
            except dbm.sqlite3.DatabaseError as e:
                assert "только добавляются" in str(e)
    finally:
        conn.close()
        shutil.rmtree(d, ignore_errors=True)


def test_db_failed_write_leaves_nothing():
    results, obs, meta, cells = world_db()
    conn, d = db_with_cells(cells)
    try:
        bad = dict(results)
        bad[("long", 2)] = dict(results[("long", 2)], axes=[dict(results[("long", 2)]["axes"][0], grid="не такая")])    # нарушает CHECK по сетке
        try:
            put(conn, bad, obs, meta, "k9")
            assert False
        except dbm.sqlite3.DatabaseError:
            pass
        assert conn.execute("SELECT (SELECT count(*) FROM infl_run) + (SELECT count(*) FROM infl_axis) + (SELECT count(*) FROM infl_obs)").fetchone()[0] == 0
    finally:
        conn.close()
        shutil.rmtree(d, ignore_errors=True)


def test_feat_table_and_composite_axes():
    ft, ver = dbm.feat_table(FEATURES)
    assert len(ver) == 10 and ft["natr_min"]["n"] == 1 and ft["natr_min"]["cat"] == "pcoins"
    f, ld = dbm.axis_feature("F:natr_bp", ft, ti.LEAD)
    assert f["n"] == 105 and ld is None
    f, ld = dbm.axis_feature("split", ft, ti.LEAD)                                  # составная ось — № ведущего ключа e2t
    assert ld == "e2t" and f["n"] == ft["e2t"]["n"]
    f, ld = dbm.axis_feature("sl", ft, ti.LEAD)
    assert ld is None and f["n"] == 38                                                # у «sl» есть свой №
    assert dbm.axis_feature("нет_такого", ft, ti.LEAD) == (None, None)


def test_stale_levels_by_fingerprint_and_tool():
    results, obs, meta, cells = world_db()
    conn, d = db_with_cells(cells)
    try:
        ft, ver = dbm.feat_table(FEATURES)
        fps = {"long:1": "a", "long:2": "b"}
        assert ti.stale_levels(conn, fps, {"t": "1"}, [1, 2]) == [1, 2]            # прогонов нет
        dbm.put_run(conn, meta, results, obs, ft, ver, ti.LEAD, {"t": "1"}, "test", "k1", [1, 2], fps=fps)
        assert ti.stale_levels(conn, fps, {"t": "1"}, [1, 2]) == []                # тот же вход и код
        assert ti.stale_levels(conn, dict(fps, **{"long:2": "c"}), {"t": "1"}, [1, 2]) == [2]     # новые клетки уровня 2
        assert ti.stale_levels(conn, fps, {"t": "2"}, [1, 2]) == [1, 2]            # код другой — всё заново
        s1 = ti.prepare(world_db()[3])[0]["long"]
        assert ti.level_fp(s1, 2) == ti.level_fp(ti.prepare(world_db()[3])[0]["long"], 2) and ti.level_fp(s1, 1) != ti.level_fp(s1, 2)
    finally:
        conn.close()
        shutil.rmtree(d, ignore_errors=True)


def test_rollup_rules_floor_mixing_and_composites():
    def row(cat, r, rn, lead=None, run=1, k=1, nul=1, dead=0):
        return dict(run=run, side="long", k=k, grid="полная", cat_short=cat, lead=lead, dead=dead, null_applicable=nul, r_null=rn, r=r, r_p=r, r_d=r / 2, r_null_p=None, r_null_d=None,
                    grp_p="много" if r >= 0.3 else "мало", grp_d="мало", grp="много" if r >= 0.3 else "мало", not_random=0, d_usd_mean=-10.0, d_dd_mean=-1.0)
    rows = [row("A", 0.5, 0.3), row("A", 0.4, 0.3), row("A", 0.6, 0.3), row("B", 0.9, 0.1), row("B", 0.8, 0.1, nul=0), row("C", 0.9, 0.0, lead="e2t"),
            row("C", 0.9, 0.0), row("C", 0.9, 0.0), row(None, 0.5, 0.0)]
    st = dbm.rollup_rows(rows)
    assert abs(st["A"]["ex"] - 0.2) < 1e-12 and st["B"]["n"] == 2 and st["B"]["n_null"] == 1 and st["A"]["many_p"] == 3
    assert abs(st["C"]["ex"] - 0.9) < 1e-12
    assert dbm.rank_by(st, "ex") == ["C", "A"]                                      # у B одна ось с нулевой моделью (< 3) — не ранжируется
    assert "без №" in st and "без №" not in dbm.rank_by(st, "ex")                  # «без №» печатается, не ранжируется
    st2 = dbm.rollup_rows([r for r in rows if not r["lead"]])
    assert dbm.rank_by(st2, "ex") == ["A"]                                          # без составной оси C — осей с нулевой моделью 2 (< 3): порядок меняется, это видно
    for extra in (row("A", 0.1, 0.0, run=2), row("A", 0.1, 0.0, k=2)):
        try:
            dbm.rollup_rows(rows + [extra])
            assert False
        except SystemExit as e:
            assert "смешаны" in str(e)
