"""П-42 TK-593: сборка страницы П-42 :8090 (один HTML, данные внутри). Вкладки: «Эквити» (виды «Варианты» и «Веер»), «Сделки» (TK-741), «Признаки».

Использование: <venv>/bin/python tk593_page.py <template.html> <выход: каталог страницы> [features.json] [variants.json]
Пишет <каталог>/index.html (страница статическая: после правки JSON — запустить сборку ещё раз).
TK-696: вкладка «Цели TK-598» убрана (владелец 23.11: «удаляй»); данные целей и схема — docs/archive/page-8090-removed-tabs-2026-11-22/.
TK-606: вкладка «Признаки» — features.json (по умолчанию <каталог>/data/features.json; источник — tools/p42/ml_features.json, схема ml_features.schema.json,
плюс проверки: ссылки src ∈ sources, имена признаков не повторяются, у набора «идёт» нет цифр).
TK-684: вид «Веер» — код tk684_fan.js рядом со скриптом вшивается в страницу, данные data/fan/ (tk684_fan.py) страница подгружает сама.
TK-684: вкладка «Эквити» — variants.json (по умолчанию <каталог>/data/variants.json; строит tk684_equity.py: сделки вариантов лонга и шорта, сверка с летописью — там же).
"""
from __future__ import annotations

import json
import os
import sys

import jsonschema

tpl_path, out_dir = sys.argv[1:3]
features_path, variants_path = (sys.argv[3 + i] if len(sys.argv) > 3 + i else f"{out_dir}/data/{n}.json" for i, n in enumerate(("features", "variants")))
HERE = os.path.dirname(os.path.abspath(__file__))


def check_features(f):
    """Смысловые проверки features.json сверх схемы (TK-606)."""
    names = [x["name"] for s in f["sets"] for g in s["groups"] for x in g["features"]]
    dup = sorted({n for n in names if names.count(n) > 1})
    assert not dup, f"features.json: имя признака повторяется: {dup}"
    ns = sorted(x["n"] for s in f["sets"] for g in s["groups"] for x in g["features"])
    assert ns == list(range(1, len(ns) + 1)), "features.json: номера признаков n — с повторами или пропусками (должны быть 1…N)"
    bad_rel = sorted({(x["n"], k) for s in f["sets"] for g in s["groups"] for x in g["features"] for k in x.get("rel", []) if k == x["n"] or k not in ns})
    assert not bad_rel, f"features.json: rel ведёт на несуществующий или свой номер: {bad_rel}"
    bad = sorted({(x["name"], k) for s in f["sets"] for g in s["groups"] for x in g["features"] for k in x["src"] if k not in f["sources"]})
    assert not bad, f"features.json: src не из sources: {bad}"
    assert len({s["id"] for s in f["sets"]}) == len(f["sets"]), "features.json: id набора повторяется"
    for s in f["sets"]:
        assert s["status"] == "done" or not s["numbers"], f"features.json: у набора {s['id']} со статусом «идёт» цифр быть не должно"
        assert s["numbers"] == [] or s["reports"], f"features.json: у набора {s['id']} есть цифры, но нет отчёта"


def check_variants(v):
    """Смысловые проверки variants.json (TK-684); равенство принятым числам проверяет tk684_equity.py при построении файла."""
    ids = [x["id"] for x in v["variants"]]
    assert len(ids) == len(set(ids)) and v["days"] == 136, "variants.json: id варианта повторяется или окно не 136 суток"
    for s in ("long", "short"):
        assert v["default"][s] in [x["id"] for x in v["variants"] if x["side"] == s], f"variants.json: default {s}"
    for x in v["variants"]:
        assert x["group"] in [g["id"] for g in v["groups"]] and len(x["t"]) == len(x["d"]) == len(x["v"]), f"variants.json: {x['id']}"   # TK-744: вариант без сделок в окне допустим
        assert x["t"] == sorted(x["t"]) and min(x["d"], default=0) >= 0 and max(x["d"], default=0) < v["days"], f"variants.json: {x['id']} — порядок закрытий или сутки вне окна"


def blob(x):
    return json.dumps(x, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")


def main():
    features = json.load(open(features_path, encoding="utf-8"))
    jsonschema.validate(features, json.load(open(os.path.join(HERE, "ml_features.schema.json"), encoding="utf-8")))
    check_features(features)
    variants = json.load(open(variants_path, encoding="utf-8"))
    check_variants(variants)
    tpl = open(tpl_path, encoding="utf-8").read()
    fan_idx = f"{out_dir}/data/fan/index.json"      # версия данных веера — адреса пакетов с ?v=<время каталога> (кэш браузера)
    fan_v = str(int(os.path.getmtime(fan_idx))) if os.path.exists(fan_idx) else ""
    fan_js = open(os.path.join(HERE, "tk684_fan.js"), encoding="utf-8").read().replace("/*__FANV__*/''", repr(fan_v))
    trades_js = open(os.path.join(HERE, "tk741_trades.js"), encoding="utf-8").read()
    parts = {"/*__FANJS__*/": fan_js, "/*__TRADESJS__*/": trades_js, "/*__FEATURES__*/": blob(features), "/*__VARIANTS__*/": blob(variants)}
    assert all(tpl.count(k) == 1 for k in parts), "шаблон: метки данных"
    html = tpl
    for k, v in parts.items():
        html = html.replace(k, v)
    os.makedirs(f"{out_dir}/data", exist_ok=True)
    open(f"{out_dir}/index.html", "w", encoding="utf-8").write(html)
    print("index.html", len(html.encode("utf-8")), "байт; признаки", features["updated"], f"({len(features['sets'])} наборов); вариантов", len(variants["variants"]), flush=True)


if __name__ == "__main__":
    main()
