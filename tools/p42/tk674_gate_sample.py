#!/usr/bin/env python3
"""П-42 TK-674: выборка клеток плана шага 3 (`plan674bsx-*.json`) для гейта нового бинаря «байт в байт» (без движка и без P&L).
Запуск на srv в среде шага 3 (TK674_X680 …): `python3 tk674_gate_sample.py <каталог вывода>`.
Берёт клетки без столбцовых фильтров (таблицы признаков стадии `ftab` не нужны): по семействам ключей TK-681 (`nf`, `ev`, `natr_min`, `natr_p`) —
равномерно по порядку плана (детерминированно), плюс несколько клеток прочих ключей и `e1t` = 1 / 2 / 3 (в плане шага 3 их нет: ось не активна;
клетки базы стороны, у шорта `e2` = 0, как требует TK-688). Пишет `<сторона>.variants.txt` (строки «cid строка-варианта», как в `cells/<окно>-<сторона>/*.variants.txt`)
и `sample.json` (ckey, cid, семейство)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_run as run  # noqa: E402

FAMILIES = ("nf", "ev", "natr_min", "natr_p")
PER_FAMILY = {"L": 6, "S": 12}
OTHER = {"L": 14, "S": 8}


def pick(seq, n):
    if n <= 0 or not seq:
        return []
    if len(seq) <= n:
        return list(seq)
    step = len(seq) / n
    return [seq[int(i * step)] for i in range(n)]


def main(out):
    st, r5, _, _ = run.t._srv()
    u = r5.Uni(run.UNI)
    os.makedirs(out, exist_ok=True)
    sample = []
    for side in ("L", "S"):
        plan = json.load(open(os.path.join(run.ROOT, "res", "plan674%s-%s.json" % (run.SUF, side)), encoding="utf-8"))
        items = [(k, v) for k, v in sorted(plan["cells"].items()) if not v["filt"] and not v.get("found")]
        used, rows = set(), []
        for fam in FAMILIES:
            have = [(k, v) for k, v in items if fam in v["patch"]]
            for k, v in pick(have, PER_FAMILY[side]):
                if k not in used:
                    used.add(k)
                    rows.append((fam, k, v))
        rest = [(k, v) for k, v in items if k not in used]
        for k, v in pick(rest, OTHER[side]):
            rows.append(("other", k, v))
        for n in (1, 2, 3):
            patch = {"e1t": n, "ew": 5}
            if side == "S":
                patch["e2"] = 0
            rows.append(("e1t", "e1t=%d" % n, dict(cid="e1t%d" % n, patch=patch)))
        with open(os.path.join(out, "%s.variants.txt" % side), "w", encoding="utf-8") as f:
            for fam, k, v in rows:
                f.write("%s %s\n" % (v["cid"], run.t.variant_line(r5, u, side, v["patch"])))
                sample.append(dict(side=side, family=fam, ckey=k, cid=v["cid"]))
    json.dump(sample, open(os.path.join(out, "sample.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    by = {}
    for s in sample:
        by[(s["side"], s["family"])] = by.get((s["side"], s["family"]), 0) + 1
    print(len(sample), "клеток:", ", ".join("%s %s %d" % (a, b, n) for (a, b), n in sorted(by.items())))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(run.ROOT, "gate-sample"))
