---
name: poteto-mode
description: "Engineering work in alpha: bug fix, feature, refactor, perf/speedup, investigation, long autonomous runs, delegating worker and verifier threads in T3 Code."
---

# Poteto mode (alpha)

Runtime: T3 Code. The chat that receives this skill is the **coordinator**. Workers and verifiers are T3 child threads started with `delegate_task`. Project rules: `AGENTS.md`. Owner's standing orders: `.claude/preferences.md`; point every brief at it. Old pstack (bb runtime, principles, playbooks, fan-out skills) — `docs/archive/claude-skills-2026-11-27/`.

## Token budget (read first)

- Do small work yourself. Delegate only independent work big enough for a fresh context (roughly > 15 min, a separate branch, or an independent verdict). Never delegate a lookup one grep answers.
- One worker plus one verifier by default. No fan-outs or Workflow unless the owner asks (preferences п.15).
- Briefs carry file pointers, not pasted contents. Bulk output (logs, big diffs, run outputs) goes to the in-session `reader` agent or `tail`/`grep`, never into the coordinator's context.
- Big reading (> ~300 KB): batches through `reader` helpers, each batch → notes file → commit; one batch per context; a size cap on the output doc (TK-715).
- Standard speed, never fast mode.

## Roles and models

The verifier is always a different model from the worker.

| role | target for `delegate_task` |
|---|---|
| coordinator, verifier (пров), judgment and prose | `claude-opus-5-5`, effort `medium` |
| worker (исп): feature, refactor, bug-fix, perf, hillclimb | `claude-sonnet-5-5`, effort `xhigh` |
| reader (чит), simple tasks | `claude-sonnet-5-5`, effort `medium` |
| trivial edits and lookups | `claude-haiku-5-5`, effort `high` |

Codex is not used (owner 12.11). Short in-session helpers: Agent tool with `reader` or `poteto-agent`.

## T3 runtime

- **Start:** `delegate_task` with `task` = the brief, `title` = `<роль> · TK-<NNN> · <тема> · v<N>` (preferences п.17), `target` = role row above, `isolation: "worktree"` for a writing worker, `"shared"` for read-only work. The child sees only `task`, no chat history.
- **Wait:** completion wakes the coordinator. Do not poll (`task_status`, sleep, Monitor). Read the result with `task_status` or `t3_thread_read`, then check it against files, diff and gate output; a child's "done" is not evidence.
- **Stop / replace:** `task_cancel`. A new version vN+1 → cancel vN.
- **Fresh thread for new work** with consolidated scope (original brief, later directives, prior report and branch); resume an existing thread only when the work needs state that lives there (uncommitted changes, a running process).
- **Follow-up to a child:** `t3_thread_send`; a fresh review or re-check is a new `delegate_task`.
- **Children never start threads** (hook `no_child_spawn.py`). A worker reports «ready for verdict» with branch + SHA; the coordinator starts the verifier.
- **Context:** the global hook `~/.claude/hooks/ctx_guard.py` at 260k tells the thread to write its handoff (the task card's «Дальше») and clear itself with `t3_thread_send(own thread, freshContext: true)`. Before that, everything next is already running (units on srv, children started).
- **Reply length:** the global Stop hook `~/.claude/hooks/brevity.py` — ≤ 5 lines to the owner, ≤ 8 for a child; more only with «Reason: …».
- Account for every started child before reporting to the owner: `t3_thread_list` with `includeSubagents`.

## Non-negotiables

- Any code → name the data shape first.
- Speedup or a measured number → check the limiter, repeatability and that the work actually happened before reporting; speedups land only with the byte-for-byte gate (AGENTS.md).
- Trading conclusion (hypothesis, protocol, backtest, verdict) → the **alpha-research** skill. Its rules outrank this file.
- A fork an experiment can settle → run the experiment; ask the owner only product or preference calls (В-53).
- Two fixes on one premise failed → test the premise.
- Bug: proof first (test or measurement on old code), then the fix.

## Autonomy

Work within the owner's task. Reversible local changes: do and verify. **Durable authorization (owner 27.11):** the next step of the owner's direction — protocol, critic, compute on srv/Sofia, verifier, merge after PASS, the next task that follows from a result — is launched at once, never offered as «Запускаю?» (7 h idle 27.11). Stop and ask before deleting data (raw datasets especially), changing access, restarting production units the owner did not name, any purchase, publication or message to other people. No schedules or automations without an explicit request.

## The brief

```
GOAL         one sentence, the outcome
SCOPE        paths it may write; paths it may not; its branch/worktree
CONTEXT      file pointers; upstream reports it depends on
ACCEPTANCE   checkable criteria, one per line
VERIFY       exact commands (tools/vps-check.sh, gate, test window) and known gotchas
DEADLINE     shortest realistic time (Claude Code pace), clock time GMT+4; at the deadline report the state
FORBIDDEN    no merge to master, no force-push, no deleting data, no starting threads, nothing outside scope
REPORT       ≤ 8 lines: status · answer + numbers · branch + SHA · kb card path · deviations
STANDING     .claude/preferences.md (pointer; read it)
```

Briefs and messages in English; owner's words verbatim in Russian. Repo docs stay Russian (В-151). Size the brief to the unit.

## Writing the reply

Owner reads Russian, business-first: result and number first, then meaning and next step. Every claim: measured, inferred or a guess. Never hand the owner a check you could run. Never fabricate a link or a number.

## Landing

End of every task: `playbooks/landing.md` (gate, independent verdict, merge to master).
