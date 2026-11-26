#!/usr/bin/env python3
"""PreToolUse: треды создаёт только координатор (владелец 26.11:
«а почему треды может создавать исполнитель? почему не заблокировано?»).

В дочернем треде T3 (в базе T3 у треда есть lineage.parentThreadId) — отказ на delegate_task,
t3_thread_launch, create_threads, Workflow и `t3 thread …` в оболочке. Вне T3 (нет T3CODE_THREAD_ID) —
тред владельца, пропуск. Не удалось прочитать базу — тоже отказ."""
import json, os, re, sqlite3, sys

SPAWN = re.compile(r"\bt3(?:\.exe)?\b.*\bthread\s+start\b", re.I)
TOOLS = ("delegate_task", "t3_thread_launch", "create_threads")
DB = os.path.expanduser("~/.t3/userdata/statev2.sqlite")
MSG = ("Треды создаёт только координатор (владелец 26.11). Дочерний тред не запускает треды и workflow: "
       "нужен ещё исполнитель/проверяющий — напиши это в итоговом отчёте координатору.")


def is_child():
    tid = os.environ.get("T3CODE_THREAD_ID")
    if not tid:
        return False
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
        row = c.execute("select payload_json from orchestration_v2_projection_threads where thread_id=?", (tid,)).fetchone()
        return bool((json.loads(row[0]).get("lineage") or {}).get("parentThreadId"))
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
        hit = name == "Workflow" or name.endswith(TOOLS)
    if hit and is_child():
        print(MSG, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
