#!/usr/bin/env python3
"""PostToolUse: дочерний тред молчит до итога (владелец 27.11: «чтобы дочерние треды вообще молчали и только
когда отдают отдавали текст»). Координатор получает только итог (`bb thread tell` / итоговый ответ),
текст между вызовами инструментов никто не читает — он только жжёт токены.

Если в ответе модели, сделавшем этот вызов, есть текст — подсказка в контекст: больше не писать.
Дочерний = у треда BB_THREAD_ID есть parent_thread_id (чтение ~/.bb/bb.db, без вызова bb)."""
import json, os, sqlite3, sys

DB = os.path.expanduser("~/.bb/bb.db")
MSG = ("SILENCE (owner 27.11): you wrote text before this tool call. Child threads write NO text between tool calls — "
       "no narration, no progress lines. Text only in the final report (≤ 5 lines, in ENGLISH) or for a blocker.")


def is_child():
    tid = os.environ.get("BB_THREAD_ID")
    if not tid or not os.path.exists(DB):
        return False
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
        row = c.execute("select parent_thread_id from threads where id=?", (tid,)).fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def spoke_before(path, tool_id):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 200_000))
        lines = f.read().decode("utf-8", "ignore").splitlines()
    entries = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "assistant":
            entries.append(e.get("message") or {})
    mid = next((m.get("id") for m in reversed(entries)
                if any(c.get("id") == tool_id for c in m.get("content") or [] if isinstance(c, dict))), None)
    if not mid:
        return False
    return any(c.get("type") == "text" and c.get("text", "").strip()
               for m in entries if m.get("id") == mid for c in m.get("content") or [] if isinstance(c, dict))


def main():
    d = json.load(sys.stdin)
    path, tool_id = d.get("transcript_path"), d.get("tool_use_id")
    if not path or not tool_id or not os.path.exists(path) or not is_child():
        return
    if spoke_before(path, tool_id):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": MSG}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
