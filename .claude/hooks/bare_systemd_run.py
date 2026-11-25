#!/usr/bin/env python3
"""PreToolUse (Bash/PowerShell): отказ голому `systemd-run … CPUQuota` на сервере счёта (TK-133 С-49, правило 06.10).

calc выведен (В-102, TK-703: связка alsched/benchrun удалена): любой `systemd-run … CPUQuota` на нём — отказ.
Обход — только переменная ALPHA_BARE_SYSTEMD_RUN_OK=1."""
import json, os, re, sys

HOST = os.environ.get("RPV_GUARD_HEAVY_HOST", "89.163.242.211")
ON_CALC = re.compile(re.escape(HOST) + r"|(?:^|[\s@'\"])calc(?:$|[\s'\"])")
RUN = re.compile(r"(?:^|[\s;&|(`'\"])systemd-run\s")
QUOTA = re.compile(r"CPUQuota", re.I)
MSG = ("calc выведен (В-102): systemd-run … CPUQuota на нём запрещён, связка alsched/benchrun удалена (TK-703). "
       "Счёт и сборки — srv (юнит systemd-run на srv, CPUQuota по правилам AGENTS.md).")


def verdict(cmd):
    """None — пропустить; строка — причина отказа."""
    if os.environ.get("ALPHA_BARE_SYSTEMD_RUN_OK") == "1":
        return None
    if not (RUN.search(cmd) and QUOTA.search(cmd) and ON_CALC.search(cmd)):
        return None
    return MSG


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return 0
    if d.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    why = verdict((d.get("tool_input") or {}).get("command") or "")
    if why:
        print(why, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
