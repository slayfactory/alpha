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
    assert ti.overlap(np.array([2, 3, 9]), np.array([3, 9])) == 1.0 and ti.overlap(np.array([1]), np.array([2])) == 0.0
    assert ti.components(["a", "b", "c", "d"], [("a", "b"), ("b", "c")]) == [["a", "b", "c"]]


def test_noise_formula():
    v = [1.0, 3.0, 5.0, 3.0]
    s, n = ti.noise_of(v, 1000)
    assert abs(s - np.std(v)) < 1e-12 and abs(n - s * (2 * np.log(1000)) ** 0.5) < 1e-12
    assert ti.noise_of([1.0], 1000) == (None, None)
