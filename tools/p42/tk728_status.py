#!/usr/bin/env python3
"""П-42 этап A, шаг 4 (TK-728, §13.78.4): статусы признаков → `ml_features.json` (поле `st4`), набор `time` (4е), строки статусов в БД.
Статусы (протокол «Выход»): «в работе» / «копия № N» / «с заглядыванием» / «нет данных» / «убран». Приоритет, если подходит несколько: с заглядыванием > нет данных > копия > в работе. «Убран» шаг 4 не ставит: семейство истории (17) по поправке L-521 остаётся «в работе» до замены долей с усадкой.
Входы (каталог результатов шага — `cluster.json` 4б, `events.json` 4г, `oi.json` 4д, `causal.json` 4а):
  python3 tk728_status.py --res <каталог> --features tools/p42/ml_features.json [--db <res728.sqlite>] [--dry]
`causal.json`: {"lookahead": {"<признак>": "<причина>"}, ...} — признаки с заглядыванием (ранги по всему окну и семейства, у которых тест нашёл расхождение)."""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk728_lib as L  # noqa: E402

THR674 = ("tpm60", "cons60", "zv6h", "n24", "SHARP")      # 4а: пороги квантильных фильтров `thr674.json` (TPM60, CN60, ZV6, N24, SHARP) посчитаны по всему SEL
THR_MARK = "порог найден на этих данных"
# 4г, поправка L-521 (после чтения результата L-520, замечание критика): точка отсчёта — нуль перестановок, не 1; семейство из 17 не убирается, а заменяется долей с усадкой p̂
HIST_WHY = "4г (L-520, поправка L-521): ОР %.3f, нижняя граница %.3f, нуль перестановок %.3f — память слабая / не ясно; семейство заменяется долей с усадкой p̂, не убирается"
TIME_SET = dict(id="time", title="Время сигнала", short="Время", task="TK-671, TK-728", status="done", status_text="готово",
                about="Час, день недели, минуты до фандинга и сессия по времени сигнала (UTC); считаются только из момента сигнала.",
                numbers=[], reports=["docs/findings/p42-stage-a-step-4-2026-11-28.md"], protocol="docs/research/P-42-level-breakout.md, §13.78.4")
TIME_BASE = {"t_hour": "—", "t_wday": "—", "t_to_fund_min": "—", "t_sess_asia": "—", "t_sess_eu": "—", "t_sess_us": "—"}


def add_time_set(d):
    """4е: шесть признаков времени — набор `time`, номера — следующие за последним; источник `signal`."""
    if any(s["id"] == "time" for s in d["sets"]):
        return False
    last = max(f["n"] for s in d["sets"] for g in s["groups"] for f in g["features"])
    d["sources"]["signal"] = "момент сигнала (мс UTC), данные не читаются"
    feats = [dict(n=last + 1 + i, name=name, what=what, src=["signal"], base=TIME_BASE[name], search="нет") for i, (name, what) in enumerate(L.TIME_FEATURES)]
    d["sets"].append(dict(TIME_SET, groups=[dict(title="", features=feats)]))
    return True


def decide(d, res):
    """Статус каждого признака. Возвращает ({имя: (статус, причина)}, сводка)."""
    cl = json.load(open(os.path.join(res, "cluster.json"), encoding="utf-8"))
    rep = {x["name"]: x for x in cl["features"]}
    ev = json.load(open(os.path.join(res, "events.json"), encoding="utf-8"))
    oi = json.load(open(os.path.join(res, "oi.json"), encoding="utf-8"))
    causal = json.load(open(os.path.join(res, "causal.json"), encoding="utf-8"))
    look = dict(causal.get("lookahead", {}))
    oi_nodata = set(oi["rule_triggered"])
    out = {}
    for s in d["sets"]:
        for g in s["groups"]:
            for f in g["features"]:
                nm, st, why = f["name"], "в работе", ""
                c = rep.get(nm)
                if nm in look:
                    st, why = "с заглядыванием", look[nm]
                elif nm in oi_nodata:
                    st, why = "нет данных", "4д: охват в 0,3–0,5"
                elif c is not None and not c["is_rep"]:
                    st, why = "копия № %d" % c["rep_n"], "4б: |ρ| ≥ 0,7, кластер %d, представитель %s" % (c["cluster"], c["rep_name"])
                elif nm in L.HISTORY17:
                    st, why = "в работе", HIST_WHY % (ev["main"]["or_mh"], ev["main"]["or_lo"], ev["main"]["perm_or_mean"])
                out[nm] = (st, why)
    kind = collections.Counter(("копия" if v[0].startswith("копия") else v[0]) for v in out.values())
    return out, dict(kind)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--res", required=True)
    ap.add_argument("--features", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    d = json.load(open(a.features, encoding="utf-8"))
    added = add_time_set(d)
    out, summary = decide(d, a.res)
    for s in d["sets"]:
        for g in s["groups"]:
            for f in g["features"]:
                f["st4"] = out[f["name"]][0]
                if f["name"] in THR674 and THR_MARK not in f.get("mark", ""):
                    f["mark"] = (f["mark"] + " · " if f.get("mark") else "") + THR_MARK
    summary["time_set_added"] = added
    summary["features"] = len(out)
    print(json.dumps(summary, ensure_ascii=False))
    if a.dry:
        return
    d["updated"] = "28.11.2026"
    with open(a.features, "w", encoding="utf-8", newline=chr(10)) as fh:      # формат файла: indent=1, без завершающего перевода строки
        json.dump(d, fh, ensure_ascii=False, indent=1)
    if a.db:
        import tk728_db as D
        db = D.connect(a.db)
        n = 0
        for nm, (st, why) in out.items():
            n += db.execute("update t728_feat set status = ?, why = ? where run_key = 'A4' and name = ?", (st, why, nm)).rowcount
        D.text(db, "A4", "status_summary", summary)
        db.commit()
        print("t728_feat обновлено строк:", n)


if __name__ == "__main__":
    main()
