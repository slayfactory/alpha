"""TK-684: метрики и сумма «шорт + лонг» вида «Варианты» (tk684_metrics.py) и опись вариантов (tk684_variants.json)."""
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk684_metrics as mt  # noqa: E402


def test_gap_longest_matches_tk663_examples():
    assert mt.gap_longest([1, 1, -1, -1, 1, 1, 3, -2, 0, 0]) == 5      # максимумы на 0, 1, 6; зазор 2…6 = 5 суток, хвост 3
    assert mt.gap_longest([-1.0, -1.0, 3.0]) == 3                      # первый максимум на третьи сутки
    assert mt.gap_longest([1, 0, 0, 0]) == 3                           # хвост длиннее зазоров
    assert mt.gap_longest([-1, -1, -1]) == 3                           # максимумов нет — вся длина
    assert mt.gap_longest([1, -1, 1]) == 2                             # касание пика — не новый максимум


def test_sharpe_dd_rr_by_hand():
    s = [1.0, -1.0, 2.0, 0.0]
    m, sd = 0.5, math.sqrt((0.25 + 2.25 + 2.25 + 0.25) / 3)
    assert abs(mt.sharpe(s) - m / sd * math.sqrt(365)) < 1e-12
    assert mt.sharpe([0.0, 0.0]) is None and mt.sharpe([1.0]) is None
    assert mt.maxdd([2, -3, 1, 5, -1]) == 3 and mt.maxdd([-2, 1]) == 2 and mt.maxdd([1, 2]) == 0
    assert mt.rr([4, 2, -1, -3]) == 1.5 and mt.rr([1, 2]) is None and mt.rr([]) is None


def test_metrics_line():
    m = mt.metrics([0, 0, 2, 3], [3.0, -1.0, -4.0, 5.0], ndays=5)
    assert m["n"] == 4 and m["sum"] == 3.0 and m["mean"] == 0.75 and m["worst"] == -4.0
    assert m["dd"] == 4.0 and m["ddp"] == 4.0 / 250 * 100 and m["gap"] == 3          # ряд 2 0 −4 5 0: максимумы на 0 и 3
    assert abs(m["rr"] - 4.0 / 2.5) < 1e-12
    e = mt.metrics([], [], ndays=5)
    assert e["n"] == 0 and e["sum"] == 0 and e["mean"] is None and e["sharpe"] is None and e["worst"] is None and e["gap"] == 5


def test_combine_is_sum_of_daily_series_with_double_margin():
    a = {"d": [0, 1, 3], "v": [2.0, -3.0, 1.0]}
    b = {"d": [1, 2], "v": [4.0, -2.0]}
    c = mt.combine(a, b, ndays=4)
    sa, sb = mt.daily(a["d"], a["v"], 4), mt.daily(b["d"], b["v"], 4)
    both = [x + y for x, y in zip(sa, sb)]
    assert c["n"] == 5 and c["sum"] == 2.0 and c["mean"] == 0.4 and c["worst"] == -3.0
    assert c["dd"] == mt.maxdd(both) == 2.0 and c["ddp"] == 2.0 / 500 * 100 and c["gap"] == mt.gap_longest(both)
    assert abs(c["sharpe"] - mt.sharpe(both)) < 1e-12 and abs(c["rr"] - (7 / 3) / (5 / 2)) < 1e-12
    # сумма просадок частей не обязана равняться просадке суммы; итог — всегда сумма итогов
    assert c["sum"] == mt.metrics(a["d"], a["v"], 4)["sum"] + mt.metrics(b["d"], b["v"], 4)["sum"]


def test_fwd_metrics_use_describe_rows_gap():
    # чтение вперёд (TK-710, describe_rows): первые сутки — максимум, даже отрицательные; gap_longest считает зазор с шага −1
    assert mt.gap_under([-1.0, -1.0, 3.0]) == 1 and mt.gap_longest([-1.0, -1.0, 3.0]) == 3
    assert mt.gap_under([1, -1, 1]) == 2 and mt.gap_under([1, 2, 3]) == 0 and mt.gap_under([-1, -1, -1]) == 2
    m = mt.fwd_metrics([0, 0, 2], [3.0, -1.0, -4.0], ndays=4)
    assert m["n"] == 3 and m["sum"] == -2.0 and m["gap"] == mt.gap_under([2.0, 0.0, -4.0, 0.0]) == 3 and m["dd"] == 4.0 and m["ddp"] == 4.0 / 250 * 100
    a, b = {"d": [0], "v": [2.0]}, {"d": [0, 1], "v": [1.0, -4.0]}
    c = mt.fwd_combine(a, b, ndays=3)
    assert c["n"] == 3 and c["sum"] == -1.0 and c["ddp"] == c["dd"] / 500 * 100 and c["gap"] == mt.gap_under([3.0, -4.0, 0.0])


def test_variants_list():
    v = json.load(open(os.path.join(HERE, "tk684_variants.json"), encoding="utf-8"))
    ids = [x["id"] for x in v["variants"]]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[a-z][a-z0-9]*", i) for i in ids)
    assert not set(ids) & {"shape", "bases", "where", "curve", "comp", "short", "long", "both"}          # слова хеша вкладки
    groups = [g["id"] for g in v["groups"]]
    for x in v["variants"]:
        assert x["side"] in ("long", "short") and x["group"] in groups and ("{w}" in x["dir"] if "dir" in x else "chron" in x)      # каталог сделок — dir или строка летописи (chron)
        assert len(x["label"].replace("·", " ").split()) <= 6 and not re.search(r"TK-|L-\d|В-\d|бп", x["label"]), x["label"]
        assert "ref" in x or "chron" in x
    for s in ("long", "short"):
        assert v["default"][s] in [x["id"] for x in v["variants"] if x["side"] == s]
        labels = [(x["group"], x["label"]) for x in v["variants"] if x["side"] == s]
        assert len(labels) == len(set(labels))


def test_daily_metrics_of_ready_series():
    s = [1.0, -1.0, 2.0, 0.0]                                          # накопленный 1 0 2 2: просадка 1, максимумы в сутки 0 и 2
    m = mt.daily_metrics(s)
    assert m["dd"] == 1.0 and m["gap"] == 2 and abs(m["sharpe"] - mt.sharpe(s)) < 1e-12
    assert mt.daily_metrics([0, 0, 0]) == {"sharpe": None, "dd": 0.0, "gap": 3}


def test_fan_axes_of():
    import pytest
    pytest.importorskip("numpy")
    import tk684_fan as fan
    assert fan.fnum(1.0) == "1" and fan.fnum(0.25) == "0.25"
    assert fan.axes_of({"s": 3.0, "rf": 0.0}, []) == ([("rf", "0")], False)                            # стоп — не ось
    assert fan.axes_of({"s": 4.0, "e2": 0.3}, []) == ([("e2", "0.3")], False)                          # e2 без e2t — обычная ось
    assert fan.axes_of({"s": 4.0, "e2": 0.5, "e2t": 1.0}, []) == ([("split", "e2=0.5 e2t=1")], True)    # раздельный вход — одна ось
    assert fan.axes_of({"s": 3.0}, [["q", 100], "tu_60"]) == ([("q", "100"), ("tu", "60")], False)      # фильтры: пара и «имя_значение»
    assert fan.axes_of({"s": 4.0, "a": 1, "cw": 60, "cl": 0, "ch": 10.5, "cm": 0.5, "e": 0.3}, []) == ([("cw", "ch=10.5 cl=0 cm=0.5 cw=60"), ("a", "1"), ("e", "0.3")], False)    # cw + cl / ch / cm — одна ось (TK-696)
    assert fan.axes_of({"s": 4.0, "e2": 0, "e2t": 2, "n_bounce": 1, "natr_min": 0.6, "nf": 1}, []) == ([("nf", "natr_min=0.6 nf=1"), ("split", "e2=0 e2t=2"), ("n_bounce", "1")], True)    # L-384 место 1: три оси
    assert fan.axes_of({"s": 4.0, "e1t": 2, "e2": 0, "natr_min": 0.6}, []) == ([("e1t", "e1t=2 e2=0"), ("natr_min", "0.6")], False)    # natr_min без nf — своя ось


def test_fan_pack_layout_parts_and_axes(tmp_path, monkeypatch):
    """TK-696: файлы по ≤ PART клеток, размер == N·D·2 + N·NF·4 + N·4 + пары·4 + N, id уровня идут подряд (idmin / idmax), оси — пары UInt16, mn / mx — границы накопленного ряда."""
    import gzip
    import random
    import sqlite3
    pytest = __import__("pytest")
    np = pytest.importorskip("numpy")
    import tk684_fan as fan
    monkeypatch.setattr(fan, "PART", 3)
    db = sqlite3.connect(tmp_path / "c.sqlite")
    db.execute("create table metric(id integer primary key, entry int, side text, cell text, uni text, win text, src text, extra text)")
    random.seed(7)

    def row(i, side, cell, patch, filt, entry=405, sig=None):
        d = [round(random.uniform(-3, 4) * (random.random() < 0.6), 4) for _ in range(136)]
        m = mt.daily_metrics(d)
        e = {"daily": d, "n": 50 + i, "usd": round(sum(d), 6), "sharpe": m["sharpe"], "maxdd": m["dd"], "kpi": {"gap_longest": m["gap"]}, "rr": {"n_win": 10, "mean_win": 2.0, "n_loss": 5, "mean_loss": -1.5},
             "lmax": {"worst": -7.5}, "patch": patch, "filt": filt, "trades_sig": sig or f"sig{i}"}
        db.execute("insert into metric values(?,?,?,?,?,?,?,?)", (i, entry, side, cell, "c539", "01.07–13.11", "tools/p42/tk674_run.py", json.dumps(e)))
        return d

    row(1, "long", "s3", {"s": 3}, [], entry=334)
    row(2, "short", "s4", {"s": 4}, [], entry=334)
    series = {i: row(i, "short", f"cell{i}", {"s": 4, "rf": i / 10, "q": 100}, [["TPM60", "q80"]] if i % 2 else []) for i in range(3, 12)}
    row(12, "short", "dup", {"s": 4, "rf": 0.3, "q": 100}, [], sig="sig5")      # те же сделки, что у клетки 5 (той же ступени): остаётся одна
    db.commit()
    out = tmp_path / "fan"
    monkeypatch.setattr(sys, "argv", ["tk684_fan.py", str(tmp_path / "c.sqlite"), str(out)])
    fan.main()
    I = json.load(open(out / "index.json", encoding="utf-8"))
    assert I["n"] == 11 and I["fields"][-2:] == ["mn", "mx"] and I["part"] == 3
    lv3 = sorted((n, f) for n, f in I["files"].items() if n.startswith("S-3"))
    assert [f["n"] for _, f in lv3] == [3, 1] and [n for n, _ in lv3] == ["S-3-0", "S-3-1"]      # осей три (rf, q, фильтр): клетки 3, 7, 9, 11 — клетка 5 заменена «dup» (те же сделки, меньший уровень)
    D, NF = I["days"], len(I["fields"])
    for name, f in I["files"].items():
        body = gzip.decompress(open(out / f"{name}.bin.gz", "rb").read())
        n, t = f["n"], f["pairs"]
        assert len(body) == f["bytes"] == n * D * 2 + n * NF * 4 + n * 4 + t * 4 + n
        ids = np.frombuffer(body, "<u4", n, n * D * 2 + n * NF * 4)
        assert list(ids) == sorted(ids) and f["idmin"] == ids[0] and f["idmax"] == ids[-1]
        counts = np.frombuffer(body, "u1", n, len(body) - n)
        assert int(counts.sum()) == t
        cents = np.frombuffer(body, "<i2", n * D).reshape(n, D)
        F = np.frombuffer(body, "<f4", n * NF, n * D * 2).reshape(n, NF)
        for k, i in enumerate(ids):
            if int(i) in series:
                assert np.array_equal(cents[k], np.rint(np.asarray(series[int(i)]) * 100).astype("<i2"))
                cs = np.cumsum(cents[k].astype(np.int64))
                assert abs(F[k, 11] - min(0, cs.min()) / 100) < 1e-4 and abs(F[k, 12] - max(0, cs.max()) / 100) < 1e-4
        meta = json.loads(gzip.decompress(open(out / f"{name}.json.gz", "rb").read()))
        assert len(meta["k"]) == n
    names = [a["name"] for a in I["axes"]]
    assert "rf" in names and "q" in names and "TPM60" in names


def test_fan_ext_vector_metrics_match_scalar():
    """TK-696: tk684_fan_ext.vmetrics (numpy, целые центы) == tk684_metrics.daily_metrics по ряду; суточные ряды 317 суток, нулевые, плоские, с касанием пика."""
    np = __import__("numpy")
    import tk684_fan_ext as ext
    rng = np.random.default_rng(3)
    S = (rng.normal(0, 400, (300, 317)) * (rng.random((300, 317)) < 0.3)).astype(np.int32)
    S[0], S[1], S[2], S[3] = 0, np.where(np.arange(317) % 2, 100, -100), np.where(np.arange(317) < 10, 100, 0), np.where(np.arange(317) == 316, 5, 0)
    S[4, :4] = [100, -100, 100, 0]                                       # касание пика — не новый максимум
    sh, dd, gp, mn, mx = ext.vmetrics(S)
    for i in range(len(S)):
        m = mt.daily_metrics(S[i] / 100.0)
        c = np.cumsum(S[i]) / 100.0
        assert (m["sharpe"] is None and np.isnan(sh[i])) or abs(m["sharpe"] - sh[i]) <= 1e-9 * max(1, abs(m["sharpe"])), i
        assert abs(m["dd"] - dd[i]) < 1e-9 and m["gap"] == gp[i], (i, m, dd[i], gp[i])
        assert abs(min(0, c.min()) - mn[i]) < 1e-9 and abs(max(0, c.max()) - mx[i]) < 1e-9
