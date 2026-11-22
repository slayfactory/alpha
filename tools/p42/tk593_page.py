"""П-42 TK-593: сборка страницы П-42 :8090 (один HTML, данные внутри). Вкладки: «Цели TK-598», «Эквити» (вид «Варианты»), «Признаки».

Использование: <venv>/bin/python tk593_page.py <template.html> <выход: каталог страницы> [progress.json] [features.json] [variants.json]
Пишет <каталог>/index.html (страница статическая: после правки JSON — запустить сборку ещё раз).
Цели TK-598 — progress.json → targets (по умолчанию <каталог>/data/progress.json; источник — tools/p42/ml_progress.json, проверяется по ml_progress.schema.json рядом со скриптом).
TK-606: вкладка «Признаки» — features.json (по умолчанию <каталог>/data/features.json; источник — tools/p42/ml_features.json, схема ml_features.schema.json,
плюс проверки: ссылки src ∈ sources, имена признаков не повторяются, у набора «идёт» нет цифр).
TK-684: вкладка «Эквити» — variants.json (по умолчанию <каталог>/data/variants.json; строит tk684_equity.py: сделки вариантов лонга и шорта, сверка с летописью — там же).
"""
from __future__ import annotations

import json
import os
import sys

import jsonschema

tpl_path, out_dir = sys.argv[1:3]
progress_path, features_path, variants_path = (sys.argv[3 + i] if len(sys.argv) > 3 + i else f"{out_dir}/data/{n}.json" for i, n in enumerate(("progress", "features", "variants")))
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
        assert x["group"] in [g["id"] for g in v["groups"]] and x["t"] and len(x["t"]) == len(x["d"]) == len(x["v"]), f"variants.json: {x['id']}"
        assert x["t"] == sorted(x["t"]) and min(x["d"]) >= 0 and max(x["d"]) < v["days"], f"variants.json: {x['id']} — порядок закрытий или сутки вне окна"


def blob(x):
    return json.dumps(x, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")


def main():
    progress = json.load(open(progress_path, encoding="utf-8"))
    jsonschema.validate(progress, json.load(open(os.path.join(HERE, "ml_progress.schema.json"), encoding="utf-8")))
    features = json.load(open(features_path, encoding="utf-8"))
    jsonschema.validate(features, json.load(open(os.path.join(HERE, "ml_features.schema.json"), encoding="utf-8")))
    check_features(features)
    variants = json.load(open(variants_path, encoding="utf-8"))
    check_variants(variants)
    tpl = open(tpl_path, encoding="utf-8").read()
    parts = {"/*__PROGRESS__*/": blob({"targets": progress["targets"]}), "/*__FEATURES__*/": blob(features), "/*__VARIANTS__*/": blob(variants)}
    assert all(tpl.count(k) == 1 for k in parts), "шаблон: метки данных"
    html = tpl
    for k, v in parts.items():
        html = html.replace(k, v)
    os.makedirs(f"{out_dir}/data", exist_ok=True)
    open(f"{out_dir}/index.html", "w", encoding="utf-8").write(html)
    print("index.html", len(html.encode("utf-8")), "байт; признаки", features["updated"], f"({len(features['sets'])} наборов); вариантов", len(variants["variants"]), flush=True)


if __name__ == "__main__":
    main()
