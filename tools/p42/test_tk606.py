"""TK-606: данные вкладки «Признаки» (ml_features.json) — схема, ссылки на источники, повторы имён, цифры только у готовых наборов.
TK-678: наборы параметров движка по темам (100 ключей), «База» / «Перебор» и сквозной номер n у каждого признака, «что значит» — не больше 12 слов."""
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
    assert n == {"pcoins": 5, "plevel": 11, "pfresh": 11, "pentry": 13, "psplit": 4, "pstop": 14, "pvol": 9, "pexit": 23, "pport": 11, "pexec": 7,
                 "f12": 12, "flow": 30, "setup": 47, "toxic": 13, "breakability": 6, "history": 8, "impulse": 6, "coin": 10, "tcount": 9, "windows": 20, "fast": 50, "trader": 43, "time": 6}, n
    names = {x["name"] for s in f["sets"] for g in s["groups"] for x in g["features"]}
    assert {"lturn24", "bd_thin30", "mk60_1800", "bk60", "SHARP"} <= names


def test_engine_keys_base_and_search():
    """TK-678: 108 ключей движка в наборах p*, ключи master-версии params.rs — все на странице; оси перебора среди столбцов таблицы — ровно пять."""
    import re
    f = load("ml_features.json")
    feats = [(s, x) for s in f["sets"] for g in s["groups"] for x in g["features"]]
    eng = {x["name"] for s, x in feats if x["src"] == ["engine"]}
    assert len(eng) == 108 and all(s["id"].startswith("p") and s["numbers"] == [] for s, x in feats if x["src"] == ["engine"])
    rs = open(os.path.join(HERE, "..", "..", "src", "breakout", "params.rs"), encoding="utf-8").read()
    keys = set(re.findall(r'^\s+"([a-z0-9_]+)" =>', rs, re.M)) - {"lo", "hi"}    # значения ключа fo
    assert len(keys) >= 82 and keys <= eng, sorted(keys - eng)
    five = {"SHARP", "cons60", "n24", "tpm60", "zv6h"}
    vals = json.load(open(os.path.join(HERE, "..", "..", "docs", "findings", "p42-titration-coverage-2026-11-22", "search-values.json"), encoding="utf-8"))   # TK-680: «Перебор» единичек
    c539 = json.load(open(os.path.join(HERE, "..", "..", "docs", "findings", "p42-titration-coverage-2026-11-23", "audit.json"), encoding="utf-8"))["filt"]     # TK-696: значения столбцов в строках перебора c539
    c539 = {k.split(":", 1)[1].rsplit(".", 1)[0] for k in c539}
    assert {x["name"] for s, x in feats if x["src"] != ["engine"] and x["search"] != "нет"} == five | {n for n in vals if n not in eng} | {x["name"] for s, x in feats if x["src"] != ["engine"] and x["name"] in c539}
    assert all(x["search"] != "нет" for s, x in feats if x["name"] in vals)
    ok10 = ["ok10_d14", "ok10_d3", "ok10_d30", "ok10_d5", "ok10_d7"]      # TK-680: единички PS-960 / PS-961 — «≥ 1 / 2 / 3; ≤ 0 / 1 / 2»
    assert all(x["base"] == "—" for s, x in feats if x["src"] != ["engine"])
    hist = next(s for s in f["sets"] if s["id"] == "history")
    assert {"ok10_d7", "ok10_d14"} <= {x["name"] for g in hist["groups"] for x in g["features"]}
    assert all((x["search"] != "нет") == (x["name"] in ok10) for g in hist["groups"] for x in g["features"])
    assert sorted(x["n"] for s, x in feats) == list(range(1, 369))
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
        (lambda d: d["sets"][0]["groups"][0]["features"][0].update(rel=[999]), "rel ведёт"),
        (lambda d: d["sets"][0]["groups"][0]["features"][0].update(rel=[1]), "rel ведёт"),
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


def test_tk682_links_and_statuses():
    """TK-682: «Связан с №» и статус — из описи tk682_audit.py; номера строк не сдвинуты."""
    import subprocess, sys
    assert subprocess.run([sys.executable, os.path.join(HERE, "tk682_audit.py"), "--check"], capture_output=True).returncode == 0
    f = load("ml_features.json")
    feats = {x["n"]: x for s in f["sets"] for g in s["groups"] for x in g["features"]}
    assert len(feats) >= 251 and feats[251]["name"] == "turn24h" and feats[23]["name"] == "ban"
    assert 251 in feats[103]["rel"] and feats[103]["st"] == "дубль № 251" and feats[150]["st"] == "всегда 0"
    assert all(n in feats[k].get("rel", []) for n, x in feats.items() if x.get("st", "").startswith("дубль №") for k in [int(x["st"].split("№")[1])])
