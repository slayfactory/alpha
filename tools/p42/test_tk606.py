"""TK-606: данные вкладки «Признаки» (ml_features.json) — схема, ссылки на источники, повторы имён, цифры только у готовых наборов.
TK-678: наборы параметров движка по темам (94 ключа), «База» / «Перебор» и сквозной номер n у каждого признака, «что значит» — не больше 12 слов."""
import json
import os
import sys

import jsonschema

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load(name):
    return json.load(open(os.path.join(HERE, name), encoding="utf-8"))


def check_features():
    # tk593_page.py тянет pandas при импорте; нужная функция — отдельный разбор исходника
    import ast
    src = open(os.path.join(HERE, "tk593_page.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "check_features")
    ns = {}
    exec(compile(ast.Module([fn], []), "tk593_page.py", "exec"), ns)
    return ns["check_features"]


def test_schema_and_semantics():
    f = load("ml_features.json")
    jsonschema.validate(f, load("ml_features.schema.json"))
    check_features()(f)


def test_counts_and_known_names():
    f = load("ml_features.json")
    n = {s["id"]: sum(len(g["features"]) for g in s["groups"]) for s in f["sets"]}
    assert n == {"pcoins": 4, "plevel": 8, "pfresh": 11, "pentry": 8, "psplit": 4, "pstop": 13, "pvol": 9, "pexit": 21, "pport": 9, "pexec": 7,
                 "f12": 12, "flow": 30, "setup": 47, "toxic": 13, "breakability": 6, "history": 4, "impulse": 6, "coin": 10, "tcount": 9}, n
    names = {x["name"] for s in f["sets"] for g in s["groups"] for x in g["features"]}
    assert {"lturn24", "bd_thin30", "mk60_1800", "bk60", "SHARP"} <= names


def test_engine_keys_base_and_search():
    """TK-678: 94 ключа движка в наборах p*, ключи master-версии params.rs — все на странице; оси перебора среди столбцов таблицы — ровно пять."""
    import re
    f = load("ml_features.json")
    feats = [(s, x) for s in f["sets"] for g in s["groups"] for x in g["features"]]
    eng = {x["name"] for s, x in feats if x["src"] == ["engine"]}
    assert len(eng) == 94 and all(s["id"].startswith("p") and s["numbers"] == [] for s, x in feats if x["src"] == ["engine"])
    rs = open(os.path.join(HERE, "..", "..", "src", "breakout", "params.rs"), encoding="utf-8").read()
    keys = set(re.findall(r'^\s+"([a-z0-9_]+)" =>', rs, re.M)) - {"lo", "hi"}    # значения ключа fo
    assert len(keys) >= 82 and keys <= eng, sorted(keys - eng)
    assert sorted(x["name"] for s, x in feats if x["src"] != ["engine"] and x["search"] != "нет") == ["SHARP", "cons60", "n24", "tpm60", "zv6h"]
    assert all(x["base"] == "—" for s, x in feats if x["src"] != ["engine"])
    hist = next(s for s in f["sets"] if s["id"] == "history")
    assert {"n_ok", "ok10_d14"} <= {x["name"] for g in hist["groups"] for x in g["features"]}
    assert all(x["search"] == "нет" for g in hist["groups"] for x in g["features"])
    assert sorted(x["n"] for s, x in feats) == list(range(1, 232))
    bad = [x["name"] for s, x in feats if len(x["what"].split()) > 12 or re.search(r"TK-|docs/|бп|ключ пула|столбец таблицы", x["what"] + x["base"] + x["search"])]
    assert not bad, bad


def test_semantic_checks_catch_bad_data():
    chk = check_features()
    f = load("ml_features.json")
    for mutate, msg in [
        (lambda d: d["sets"][0]["groups"][0]["features"][1].update(name=d["sets"][0]["groups"][0]["features"][0]["name"]), "повторяется"),
        (lambda d: d["sets"][0]["groups"][0]["features"][1].update(n=1), "номера признаков"),
        (lambda d: d["sets"][-1]["groups"][-1]["features"][-1].update(n=999), "номера признаков"),
        (lambda d: d["sets"][10]["groups"][0]["features"][0].update(name="mk"), "повторяется"),
        (lambda d: d["sets"][0]["groups"][0]["features"][0].update(src=["нет-такого"]), "не из sources"),
        (lambda d: d["sets"][13].update(status="running", numbers=["+0,5"]), "цифр быть не должно"),
    ]:
        d = json.loads(json.dumps(f))
        mutate(d)
        try:
            chk(d)
        except AssertionError as e:
            assert msg in str(e), (msg, str(e))
        else:
            raise AssertionError("проверка не поймала: " + msg)
