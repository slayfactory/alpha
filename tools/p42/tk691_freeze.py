#!/usr/bin/env python3
"""П-42 §13.77 (TK-691, PS-988 / PS-989): перезаморозка вперёд на c539 — доказательства до коммита и сборка `tk691-frozen.json` (пятое звено цепочки tk626 → tk638 → tk641 → tk670 → tk691).
  proof  — (на srv, только имена / размеры / времена файлов, содержимое не открывается) суток ≥ 14.11 нет ни в деревьях окон, ни в каталогах результатов TK-691, маркеров `READ-*` вперёд нет
  build  — файл из готовых результатов ворот c539 (`g638a/gate.json`, `g670a/gate.json`, `gmk/results.json` + `gmk/g626c-eq.json`, `describe.json`), `n24-c539.json` и `proof.json`; на ПК
  python3 tk691_freeze.py proof [--out proof.json]
  python3 tk691_freeze.py build --proof … --n24 … --n24-h775 … --g638a … --g670a … --g626c … --g626c-eq … --describe … [--freeze-dir docs/research/freeze] --out docs/research/freeze/tk691-frozen.json"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk616_forward as fw  # noqa: E402

LAST_DAY = datetime.date(2026, 11, 13)
ROOT = "/data/p42-tk691"
READ_DIRS = (fw.ROOT, "/data/p42-tk616/fwd", "/data/p42-tk626", ROOT)
SKIP_DIRS = ("code", "freeze", "__pycache__", "tick-store", "wa", "wb", "wc")     # код / копии заморозок; ссылки на склад тиков и деревья окон (деревья проверяются отдельно)
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
NAMES = ("tk626-frozen.json", fw.CAND_NAME, fw.CAND2_NAME, fw.CAND3_NAME)
SEL_H775 = {"L_n24": ("tk638-frozen.json", "cell_h775"), "L_mk2": ("tk641-frozen.json", "describe_h775"), "D_S": ("tk670-frozen.json", "cell_h775")}


def utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inventory(root):
    """Опись файлов результатов ворот: верхний каталог → файлов, байт, первые / последние времена; имена с датой ≥ 14.11 (должно не быть). Содержимое не открывается; ссылки не раскрываются."""
    tops, late, n = {}, [], 0
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not os.path.islink(os.path.join(dp, d)))
        for f in sorted(fn):
            p = os.path.join(dp, f)
            st = os.stat(p)
            n += 1
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            t = tops.setdefault(rel.split("/")[0] if "/" in rel else ".", {"files": 0, "bytes": 0, "first": st.st_mtime, "last": st.st_mtime})
            t["files"] += 1
            t["bytes"] += st.st_size
            t["first"], t["last"] = min(t["first"], st.st_mtime), max(t["last"], st.st_mtime)
            for m in _DATE.finditer(rel):
                if datetime.date.fromisoformat(m.group(1)) > LAST_DAY:
                    late.append(rel)
    return {"root": root, "files": n, "by_top_dir": {k: {"files": v["files"], "bytes": v["bytes"], "first_mtime_utc": utc(v["first"]), "last_mtime_utc": utc(v["last"])} for k, v in sorted(tops.items())},
            "names_with_day_after_13_11": late}


def cmd_proof(a):
    import tk670_freeze as f0
    f0.READ_DIRS = READ_DIRS
    trees = f0.tree_days()
    if not all(r["data_to_le_13_11"] and r["aggtrades"]["ok_le_13_11"] and r["klines"]["ok_le_13_11"] for r in trees.values()):
        raise SystemExit("STOP: в деревьях окон есть сутки позже 13.11")
    marks = f0.read_markers()
    if marks["markers"]:
        raise SystemExit("STOP: маркеры чтения вперёд: %s" % marks["markers"][:5])
    inv = inventory(ROOT)
    if inv["names_with_day_after_13_11"]:
        raise SystemExit("STOP: в каталогах результатов TK-691 имена с сутками позже 13.11: %s" % inv["names_with_day_after_13_11"][:5])
    out = {"now_utc": utc(datetime.datetime.now(datetime.timezone.utc).timestamp()), "last_day": str(LAST_DAY), "trees": trees, "read_markers": marks, "tk691_results": inv, "ok": True}
    return emit(out, a.out)


def emit(out, path):
    js = json.dumps(out, indent=1, ensure_ascii=False, default=float)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(js)
    print(js)
    return 0


# ---- сборка файла ---------------------------------------------------------------------------------------------------------------------------

def load(path):
    return json.load(open(path, encoding="utf-8"))


def link_of(freeze_dir, name, version, note=None):
    p = os.path.join(freeze_dir, name)
    d = {"file": "docs/research/freeze/" + name, "version": version, "sha256": fw.sha256_text(p), "untouched": True}
    if note:
        d["note"] = note
    return d


def gate_row(gate, passed, result, describe, why):
    return {"gate": gate, "passed": bool(passed), "verdict": "ПРОЙДЕНЫ" if passed else "НЕ ПРОЙДЕНЫ", "description": describe, "result": result["path"], "result_sha256": result["sha256"], "result_note": why}


def file_ref(path, srv_path):
    return {"path": srv_path, "sha256": fw.sha256_text(path)}


def build(a):
    fd = a.freeze_dir
    t3 = load(os.path.join(fd, fw.CAND3_NAME))
    t1, t2 = load(os.path.join(fd, fw.CAND_NAME)), load(os.path.join(fd, fw.CAND2_NAME))
    proof, n24c, n24h = load(a.proof), load(a.n24), load(a.n24_h775)
    g638, g670, g626, geq, desc = load(a.g638a), load(a.g670a), load(a.g626c), load(a.g626c_eq), load(a.describe)
    if not proof.get("ok"):
        raise SystemExit("STOP: proof.ok ≠ true")
    if geq.get("ok") is not True:
        raise SystemExit("STOP: согласованность mk2 с БД TK-674 расходится (g626c-eq.json): остановка и вопрос критику, не «не прошла» (PS-988 п. 3)")
    if n24c["N"] != a.n or n24c["universe"] != "symbols-crypto.txt":
        raise SystemExit("STOP: n24-c539.json: N %s / вселенная %s ≠ %s / symbols-crypto.txt" % (n24c["N"], n24c["universe"], a.n))
    gmk = g626["gmk"]["L_mk2"]
    passed = {"L_n24": g638.get("ok") is True and g638.get("N") == a.n, "L_mk2": bool(gmk["pass"]), "D_S": g670.get("ok") is True}
    not_read = [k for k in fw.CAND4_GATED if not passed[k]]
    alpha = dict(t3["alpha"])
    for k in not_read:
        alpha[fw.CAND4_FALLBACK[k]] = round(alpha[fw.CAND4_FALLBACK[k]] + alpha[k], 10)
        alpha[k] = 0.0
    un = copy.deepcopy(t3["universe"])
    un["read"], un["decision"] = [fw.CAND4_DECISION], fw.CAND4_DECISION
    src = {"g638a": file_ref(a.g638a, "/data/p42-tk691/g638a/gate.json"), "g670a": file_ref(a.g670a, "/data/p42-tk691/g670a/gate.json"), "g626c": file_ref(a.g626c, "/data/p42-tk691/gmk/results.json"),
           "g626c_eq": file_ref(a.g626c_eq, "/data/p42-tk691/gmk/g626c-eq.json"), "describe": file_ref(a.describe, "/data/p42-tk691/describe.json")}
    h775 = {k: load(os.path.join(fd, f))["selection"][key] for k, (f, key) in SEL_H775.items()}
    h775["L_mk2"] = h775["L_mk2"]["L_mk2"]
    out = {
        "version": fw.CAND4_VERSION,
        "protocol": "П-42 §13.77 (TK-691, PS-988 + поправка PS-989): перезаморозка проверки вперёд на c539 (только крипта, 539 контрактов, В-175): единственное изменение заморозки — читается и решает `crypto`; порог N лонга по правилу tk638 на c539; ворота кандидатов на c539",
        "decision": "постановление критика `thr_m27kv64m9h` по TK-691 (23.11.2026, обязательное) + ответ критика на вопрос (а): TradFi не получаем заново, `universe.read = [crypto]`, чтения `all` / `tradfi` — «не читается: данных TradFi нет (В-175)»; решает координатор `thr_ty6ae9r46y`",
        "read_before_commit": "суток ≥ 14.11 не читалось и не считалось ни по одной вселенной: деревья окон wa / wb / wc кончаются 13.11 (по именам файлов), в каталогах результатов TK-691 имён с сутками ≥ 14.11 нет, маркеров `READ-*` вперёд нет (`proof`)",
        "proof": {"tool": "tools/p42/tk691_freeze.py proof (srv, только имена / размеры / времена файлов; содержимое не открывалось)", "done_utc": proof["now_utc"], "proof_json": file_ref(a.proof, "/data/p42-tk691/proof.json"),
                  "last_allowed_day": proof["last_day"], "trees": {w: {"t0": r["t0"], "data_to": r["data_to"], "aggtrades": r["aggtrades"], "klines": r["klines"]} for w, r in proof["trees"].items()},
                  "read_markers": proof["read_markers"], "tk691_results": proof["tk691_results"]["by_top_dir"]},
        "base_freeze": link_of(fd, "tk626-frozen.json", "tk626-frozen-1"),
        "prev_candidate": link_of(fd, fw.CAND_NAME, fw.CAND_VERSION),
        "prev_candidate2": link_of(fd, fw.CAND2_NAME, fw.CAND2_VERSION),
        "prev_candidate3": link_of(fd, fw.CAND3_NAME, fw.CAND3_VERSION, "α этого файла перекрывают α tk670, tk641, tk638 и tk626 (цепочка tk626 → tk638 → tk641 → tk670 → tk691)"),
        "binary": t3["binary"], "binary_note": "замороженный бинарь клеток tk626 / tk638 / tk641 (`alpha-mk.396989e9`) и ds-бинарь `D_S` (`alpha-lx.2815279d`) не меняются; движка в этой заморозке нет",
        "ds_binary": t3["ds_binary"],
        "universe": un,
        "universe_note": "решающая и единственная читаемая вселенная — `crypto` (c539: 539 контрактов, база sha256 `ac662b40…`, она уже была предзаписанной вселенной чтения во всех четырёх файлах); `all` и `tradfi` — «не читается: данных TradFi нет (В-175)», в точке 30.11 не печатаются; "
                         "`check_universe_crypto`: базовый список crypto ⊆ списка точки, правило прироста (первые сутки > 13.11 и ≤ D), нет списка решающей вселенной — остановка до маркера чтения",
        "point_shares": t3["point_shares"], "points": t3["points"], "windows": t3["windows"],
        "cells": "без изменений: ключи, vid и семейство клеток — в tk626 / tk638 / tk641 / tk670; здесь клеток нет (бинарь и `--symbols` не меняются)",
        "filter": {"name": t1["filter"]["name"], "feature": t1["filter"]["feature"], "N": a.n, "N_unit": t1["filter"]["N_unit"],
                   "N_origin": "порог по правилу tk638 на c539 (`tools/p42/tk691_n24.py`): общий квантиль 0,6 по n24 кандидатов `L_base` в зачётных диапазонах трёх окон (m24 исключён), linear, 2 значащие цифры: сырой %.1f → %g, %d кандидатов из %d строк; прежний N = 220 000 (h775) не применяется; кандидат выбран на h775 — найдено на этих данных" % (
                       n24c["q_raw_0.60"], a.n, n24c["n_scored"], n24c["n_all_rows"]),
                   "N_h775_check": {"q_raw": n24h["q_raw_0.60"], "N": n24h["N"], "n_scored": n24h["n_scored"], "tk638_N": t1["filter"]["N"]}, "n24_c539": file_ref(a.n24, "/data/p42-tk691/res/n24-c539.json"),
                   "n24_h775": file_ref(a.n24_h775, "/data/p42-tk691/res/n24-h775.json"), "table_rule": "таблица `--ftab` строится на точке тем же `alpha-feat tcount` (md5 `69fcf7cd…`) с этим N; `tk616_forward` подставляет N поверх `filter.N` tk638 (`apply_candidate4`)"},
        "gates": {
            "L_n24": gate_row("G-638a(c539)", passed["L_n24"], src["g638a"], "путь точки (дамп → tcount → таблица N = %g → замороженный бинарь) на c539 побайтно как тот же вариант на боевом бинаре TK-683 `alpha-tk683-plain.9387e0a9`; охраны: no_key = 0, cands / dropped = пересчёту, доля m24 ≤ 1 %%, n24 = строкам tcount TK-638 по c539" % a.n,
                      "ok = %s; m24 %s; no_key %s" % (g638["ok"], "%.3f %%" % (100 * g638["guards"]["m24_share"]) if "guards" in g638 else "—", g638.get("guards", {}).get("no_key", "—"))),
            "L_mk2": gate_row("G-626c(c539)", passed["L_mk2"], src["g626c"], "`tk626_gmk.py run / report --symbols symbols-crypto.txt` замороженным бинарём, Холм m = 4, односторонний 5 %%, ≥ 100 000 повторов; g1 ∧ g2 ∧ g3 у `L_mk2`; рядом — согласованность `mk2` с БД TK-674 побайтно (ok = %s)" % geq["ok"],
                      "g1 %s g2 %s g3 %s; p %.4f, порог Холма %.4f; месяцев плюс %d из %d; ΣS %.2f $, n %d" % (gmk["g1"], gmk["g2"], gmk["g3"], gmk["p"], gmk["holm_threshold"], gmk["positive_months"], gmk["counted_months"], gmk["sum"], gmk["n"])),
            "D_S": gate_row("G-670a(c539)", passed["D_S"], src["g670a"], "замороженный ds-бинарь, ключи `S_base` + `lx=1 tf=60`, c539, wa / wb / wc: `short-{trades,daily,coinday,entryrej}.csv` побайтно = клетка `lx=1` в БД TK-674 (c539, бинарь c64d5ed7); `short-sigsx.csv` пакетный путь не пишет (как G-670c); C2 доля пар без klines ≤ 1 %",
                      "ok = %s" % g670.get("ok")),
        },
        "gates_note": "`S_mk1` — базовая клетка семейства, не кандидат: ворот нет (постановление критика, ответ (в)), G_mk по ней — описательно в `gmk/results.json`, α 0,005 как в tk670; у `L_n24` и `D_S` своих статистических ворот нет (повтор G-638a / G-670a, ответ (б))",
        "not_read": not_read,
        "alpha": alpha, "alpha_k": {k: {p: v * s for p, s in t3["point_shares"].items()} for k, v in alpha.items()},
        "alpha_return_rule": "постановление критика п. 2: ворота не пройдены → клетка не читается, α возвращается: `L_n24` / `L_mk2` → `S_base`, `D_S` → `S_mk1`; Σ 0,05; доли точек без изменений; смена решающей вселенной α не берёт; новых кандидатов и повторного отбора нет",
        "cost_model": t3["cost_model"],
        "reading_rule": {"one_reading": "одно прочтение на точку: клетки пакета и кандидаты — один прогон; вселенная чтения и решающая — только `crypto`; маркер `READ-<точка>.started` создаётся атомарно ДО движка; повтор — только решением координатора",
                         "L1_2026-11-30": "ТОЛЬКО «описательно, c539»: n, $ (500 $), Sharpe, просадка ($ и % маржи 250 $), перехай P&L; решения нет (α_k = 0); слов «подтверждено / опровергнуто» нет; чтений 775 / TradFi рядом нет (данных нет, В-175)",
                         "L2_L5": "`tk616_forward.decide` (§13.28 п. 7 дословно) на c539: первое решение — L2 (31.12); α_k = α × доля точки; единица статистики — сутки, блочный бутстреп (блок 4, ≥ 100 000 повторов); опровергнуто — только на L5",
                         "C1": "α, решающая вселенная и N — ТОЛЬКО из этого файла; файла нет — остановка (`REQUIRE_CANDIDATE4`); sha256 четырёх предыдущих звеньев сверяются; `not_read` = клетки без пройденных ворот",
                         "dates": "30.11 (входы ≤ 26.11), 31.12, 31.01.2027, 28.02, 31.03.2027 — как у цепочки"},
        "decision_rule": t3["decision_rule"].replace("решающая вселенная `all`", "решающая вселенная `crypto`"),
        "anchors": {"mark": "найдено на этих данных (кандидаты выбраны на h775, выборка 01.07–13.11 — ею же проверяются ворота c539); числа — описательно, α не двигают",
                    "window": "01.07–13.11, 136 суток, ставка 500 $, плечо 2 (маржа 250 $)",
                    "c539_describe": desc, "c539_describe_source": src["describe"], "base_L_c539_L334": {"trades": 298, "usd": 325.70, "sharpe": 5.22, "note": "L-334: клетка базы лонга TK-674 (стоп 3 %, бинарь c64d5ed7), не клетка заморозки; число сделок совпало с `L_base` заморозки на c539 (298), Σ и Sharpe отличаются — рядом `c539_describe.L_base`"},
                    "base_S_c539_L334": {"trades": 172, "usd": 133.93, "sharpe": 3.63, "note": "L-334: клетка базы шорта TK-674 (стоп 4 %), не клетка заморозки; сделок столько же (172), Σ и Sharpe отличаются — рядом `c539_describe.S_base`"}, "h775": h775,
                    "h775_note": "h775 — клетки кандидатов в выборке до перезаморозки (tk638 / tk670 `selection.cell_h775`, tk641 `selection.describe_h775`)"},
        "gates_source": {k: v for k, v in src.items()},
        "tool_note": "`tk616_forward.py` с пятым звеном (`load_candidate4` / `apply_candidate4` / `resolve_candidate4`, `check_universe_crypto`) и тестами `tests/test_tk691.py`, `tests/test_tk691_gate.py` — коммитом ДО чтения L1 (30.11); правил этого файла не меняет",
        "ref": "docs/research/P-42-level-breakout.md §13.77 (PS-988, поправка PS-989); kb/task/TK-691.md; tools/p42/tk691_n24.py, tk691_gate.py, tk691_freeze.py",
    }
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, ensure_ascii=False, default=float)
        f.write("\n")
    print("записан %s: ворота %s; не читаются %s; α %s" % (a.out, {k: ("ПРОЙДЕНЫ" if v else "НЕ ПРОЙДЕНЫ") for k, v in passed.items()}, not_read or "—", alpha))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["proof", "build"])
    ap.add_argument("--out", default="")
    ap.add_argument("--freeze-dir", default=os.path.join(HERE, "..", "..", "docs", "research", "freeze"))
    ap.add_argument("--proof", default="")
    ap.add_argument("--n24", default="")
    ap.add_argument("--n24-h775", default="")
    ap.add_argument("--g638a", default="")
    ap.add_argument("--g670a", default="")
    ap.add_argument("--g626c", default="")
    ap.add_argument("--g626c-eq", default="")
    ap.add_argument("--describe", default="")
    ap.add_argument("--n", type=float, default=250000.0)
    a = ap.parse_args(argv)
    if a.cmd == "proof":
        return cmd_proof(a)
    for k in ("proof", "n24", "n24_h775", "g638a", "g670a", "g626c", "g626c_eq", "describe", "out"):
        if not getattr(a, k):
            ap.error("build: нужен --%s" % k.replace("_", "-"))
    return build(a)


if __name__ == "__main__":
    sys.exit(main())
