#!/usr/bin/env python3
"""TK-695: итог месяца одной строкой JSON из файлов шагов (`/data/p42-tk695/<месяц>/`): контракты, файлы, ГиБ, дыры, проверка, свободный диск.
  python3 tk695_report.py <месяц>"""
import json
import os
import re
import sys

m = sys.argv[1]
W = f"/data/p42-tk695/{m}"


def j(p):
    return json.load(open(f"{W}/{p}", encoding="utf-8")) if os.path.exists(f"{W}/{p}") else {}


def txt(p):
    return open(f"{W}/{p}", encoding="utf-8", errors="replace").read() if os.path.exists(f"{W}/{p}") else ""


def last(pat, s):
    r = re.findall(pat, s)
    return r[-1] if r else None


plan, prog, chk = j("plan/plan.json"), j("dl/log/progress.json"), j("check.json")
sf = last(r"STOREFILL DONE[^\n]*", txt("sf.log"))
cv = last(r"convert:[^\n]*", txt("convert.log"))
stub = last(r"сделано: пустышек[^\n]*", txt("stub.log"))
man = last(r"манифест: verified[^\n]*", txt("man.log"))
R = {"month": m, "window": plan.get("window"), "contracts": plan.get("contracts"),
     "listing_files": {k: v["files"] for k, v in plan.get("listing", {}).items()},
     "downloaded": {"done": prog.get("done"), "skipped": prog.get("skipped"), "miss": prog.get("miss"), "gib": round(prog.get("bytes", 0) / 2**30, 2)},
     "convert": cv, "storefill": sf, "check_problems": chk.get("problems"), "check_expected": chk.get("expected"),
     "manifest": man, "stub": stub, "free_gib_before": txt("df-before").strip(), "free_gib_after": txt("df-after").strip()}
print(json.dumps(R, ensure_ascii=False))
