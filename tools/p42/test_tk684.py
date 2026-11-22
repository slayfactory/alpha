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


def test_variants_list():
    v = json.load(open(os.path.join(HERE, "tk684_variants.json"), encoding="utf-8"))
    ids = [x["id"] for x in v["variants"]]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[a-z][a-z0-9]*", i) for i in ids)
    assert not set(ids) & {"shape", "bases", "where", "curve", "comp", "short", "long", "both"}          # слова хеша вкладки
    groups = [g["id"] for g in v["groups"]]
    for x in v["variants"]:
        assert x["side"] in ("long", "short") and x["group"] in groups and "{w}" in x["dir"]
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
