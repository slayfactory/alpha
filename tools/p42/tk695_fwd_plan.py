#!/usr/bin/env python3
"""TK-695 (дополнение, В-181): сутки вперёд 14.11 → последние опубликованные (прогноз вперёд П-42 читает их 30.11, `tk616_forward.py`).
  python3 tk695_fwd_plan.py plan   <каталог листинга tk621_list> <TradFi-список> <выход>   # вселенная = крипто-контракты с aggTrades в окне (правило TK-621), missing.tsv для tk621_dl.py
  python3 tk695_fwd_plan.py report <рабочий каталог>                                          # итог одной строкой JSON"""
import json
import os
import re
import sys
from collections import defaultdict


def plan(lst, tradfi, out):
    skip = set(open(tradfi, encoding="utf-8").read().split())
    rows = [l.rstrip("\n").split("\t") for l in open(f"{lst}/files.tsv", encoding="utf-8")]
    uni = sorted({r[1] for r in rows if r[0] == "aggTrades"} - skip)
    keep = [r for r in rows if r[1] in set(uni)]
    os.makedirs(out, exist_ok=True)
    with open(f"{out}/missing.tsv", "w", encoding="utf-8") as f:                  # формат tk621_dl.py: вид, символ, сутки, размер, etag
        f.writelines("\t".join(r) + "\n" for r in keep)
    with open(f"{out}/files-1m.tsv", "w", encoding="utf-8") as f:                 # вид `klines1m` — как читают tk686_june.py tree / check
        f.writelines("\t".join(["klines1m" if r[0] == "klines" else r[0]] + r[1:]) + "\n" for r in keep)
    json.dump(uni, open(f"{out}/universe.json", "w"))
    days = sorted({r[2] for r in keep if r[0] == "aggTrades"})
    by = defaultdict(lambda: [0, 0])
    for r in keep:
        by[r[0]][0] += 1
        by[r[0]][1] += int(r[3])
    P = {"first_day": days[0], "last_day": days[-1], "days": len(days), "contracts": len(uni), "excluded_tradfi": len({r[1] for r in rows if r[0] == "aggTrades"} & skip),
         "files": {k: v[0] for k, v in by.items()}, "gib": {k: round(v[1] / 2**30, 2) for k, v in by.items()}, "files_per_day": {d: sum(1 for r in keep if r[0] == "aggTrades" and r[2] == d) for d in days}}
    json.dump(P, open(f"{out}/plan.json", "w"), indent=1)
    print(json.dumps(P, ensure_ascii=False))


def report(w):
    def j(p):
        return json.load(open(f"{w}/{p}", encoding="utf-8")) if os.path.exists(f"{w}/{p}") else {}

    def txt(p):
        return open(f"{w}/{p}", encoding="utf-8", errors="replace").read() if os.path.exists(f"{w}/{p}") else ""

    def last(pat, s):
        r = re.findall(pat, s)
        return r[-1] if r else None
    P, prog, chk = j("plan/plan.json"), j("dl.progress.json"), j("check.json")
    print(json.dumps({"month": "fwd", "window": [P.get("first_day"), P.get("last_day")], "contracts": P.get("contracts"), "files": P.get("files"), "gib": P.get("gib"),
                      "downloaded_now": prog, "storefill": last(r"STOREFILL DONE[^\n]*", txt("sf.log")), "check_problems": chk.get("problems"), "check_expected": chk.get("expected"),
                      "manifest": last(r"манифест: verified[^\n]*", txt("man.log")), "stubs": "нет (zip остаются для tk626_prep.sh)",
                      "free_gib_before": txt("df-before").strip(), "free_gib_after": txt("df-after").strip()}, ensure_ascii=False))


if __name__ == "__main__":
    {"plan": lambda: plan(*sys.argv[2:5]), "report": lambda: report(sys.argv[2])}[sys.argv[1]]()
