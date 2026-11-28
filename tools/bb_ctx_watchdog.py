"""Сторож контекста детей координатора (bb automation, без модели).

Каждые 10 мин: живой ребёнок координатора в статусе idle с контекстом >= порога —
очистить, сказать читать передачу дел своей карточки, сообщить координатору.
Живой = самый новый тред в своём не удалённом окружении, обновлялся за RECENT_H ч
(старые версии vN делят worktree с новой; закрытые задачи — окружение destroyed).
WATCH_FILE: строка «thr_…» — следить всегда, «-thr_…» — никогда.
Тихо, если делать нечего.
"""
import json
import shutil
import os
import subprocess
import sys
import time

COORD = os.environ.get("COORD_THREAD", "thr_ty6ae9r46y")
LIMIT = int(os.environ.get("CTX_LIMIT", "255000"))
RECENT_H = 24
WATCH_FILE = os.environ.get(
    "WATCH_FILE", os.path.expanduser("~/.bb/thread-storage/thr_ty6ae9r46y/watch.txt")
)
BB = os.environ.get("BB_CLI") or "bb"
if os.name == "nt" and os.path.exists(BB + ".cmd"):
    BB += ".cmd"  # Windows: bare BB_CLI is a shell script
BB = shutil.which(BB) or BB

RESTART = (
    "Context cleared by the watchdog at {n}k. git merge master first. "
    "Read ONLY the latest handoff section («Передача дел») of your kb/task card "
    "and continue from its «Next». Write a new handoff section before 250k. "
    "Send progress lines and the final report with `bb thread tell {coord}` — "
    "a plain final answer does not reach the coordinator."
)


def bb(*args):
    r = subprocess.run([BB, *args], capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout


def main():
    force, skip = set(), set()
    if os.path.exists(WATCH_FILE):
        with open(WATCH_FILE, encoding="utf-8") as f:
            for x in f:
                x = x.strip()
                if x.startswith("-"):
                    skip.add(x[1:])
                elif x and not x.startswith("#"):
                    force.add(x)
    code, out = bb("thread", "list", "--parent-thread", COORD, "--json")
    if code != 0:
        print(out)
        return 1
    newest, forced = {}, []
    for t in json.loads(out):
        if t["id"] in force:
            forced.append(t)
        if t.get("archivedAt") or not t.get("environmentId"):
            continue
        cur = newest.get(t["environmentId"])
        if cur is None or t["createdAt"] > cur["createdAt"]:
            newest[t["environmentId"]] = t
    now_ms = time.time() * 1000
    done = []
    live = {t["id"]: t for t in [*newest.values(), *forced]}
    for t in live.values():
        if t["id"] in skip or t.get("status") != "idle":
            continue
        if t["id"] not in force:
            if now_ms - (t.get("updatedAt") or 0) > RECENT_H * 3600 * 1000:
                continue
            code, sh = bb("thread", "show", t["id"], "--json")
            env = json.loads(sh).get("environment") if code == 0 else None
            if not env or (env.get("lifecycle") or {}).get("phase") != "active":
                continue
        code, ctx = bb("thread", "context", t["id"], "--json")
        if code != 0:
            continue
        usage = json.loads(ctx).get("usage") or {}
        n = (usage.get("snapshot") or usage).get("usedTokens") or 0
        if n < LIMIT:
            continue
        if bb("thread", "clear", t["id"])[0] != 0:
            print(f"clear failed {t['id']}")
            continue
        # idle after the restarted turn must wake the coordinator (28.11: TK-736 sat idle 1 h 20 min)
        bb("thread", "update", t["id"], "--parent-notifications", "auto")
        bb("thread", "tell", t["id"], RESTART.format(n=n // 1000, coord=COORD))
        done.append(f"{t['id']} ({t.get('title')}) {n // 1000}k")
    if done:
        msg = "watchdog: cleared + restarted " + "; ".join(done)
        bb("thread", "tell", COORD, msg)
        print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
