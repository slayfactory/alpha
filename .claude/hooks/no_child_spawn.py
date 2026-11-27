#!/usr/bin/env python3
"""PreToolUse (Bash/PowerShell/bb workflow): треды создаёт только координатор (владелец 26.11:
«а почему треды может создавать исполнитель? почему не заблокировано?»).

В дочернем треде (у треда есть parentThreadId) — отказ на `bb thread spawn|create|fork`,
`bb workflows run` и инструмент bb_workflow_run. Не удалось узнать родителя — тоже отказ."""
import json, os, re, shutil, subprocess, sys

SPAWN = re.compile(r"\bthread\s+(?:spawn|create|fork)\b|\bworkflows?\s+run\b", re.I)
MSG = ("Треды создаёт только координатор (владелец 26.11). Дочерний тред не запускает треды и workflow: "
       "нужен ещё исполнитель/проверяющий — `bb thread tell <координатор>` с просьбой.")


def is_child():
    tid = os.environ.get("BB_THREAD_ID")
    if not tid:
        return False  # не bb-тред (терминал владельца)
    bb = shutil.which("bb") or shutil.which(os.environ.get("BB_CLI") or "") or "bb"
    try:
        out = subprocess.run([bb, "thread", "show", tid, "--json"], capture_output=True,
                             text=True, encoding="utf-8", timeout=20).stdout
        return json.loads(out)["thread"].get("parentThreadId") is not None
    except Exception:
        return True


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return 0
    name = d.get("tool_name") or ""
    if name in ("Bash", "PowerShell"):
        hit = SPAWN.search((d.get("tool_input") or {}).get("command") or "")
    else:
        hit = "workflow_run" in name
    if hit and is_child():
        print(MSG, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
