### Landing

Invoked at the end of every other playbook. alpha has no pull requests: trunk is `master`, one branch per topic.

1. **Branch and worktree.** Work on a topic branch in its own worktree (`.claude/worktrees/<name>`, or `bb thread spawn --new-environment worktree`). One writer per branch.
2. **Commits.** Small and ordered, each landable. Stage only your files (`git add <files>`, never `-A`). Subject in Russian, short, imperative. End every commit message with the attribution line from the session reminder.
3. **Gate.** Locally (build server): only the changed module — `bash tools/vps-check.sh <worktree> test <module filter>` plus `cargo fmt --check`; never the full suite. Then push the branch: GitHub CI (GC: fmt + lint + all tests; Quality: strict lints, lint budget, deps) runs on every branch push and is the full gate (owner 12.11). Never wait for CI (owner 12.11): land after local module tests + verdict; check CI later (`gh run list --branch <name>`) and fix a red run as a new task. Speedups also need the byte-for-byte gate against the previous binary.
4. **Independent verdict.** A verifier thread on a different model than the worker (models.md `review and verdicts`) reads the diff and the gate output and says land or return, with file:line evidence.
5. **Merge.** Only after green local module tests and a land verdict (CI is not waited for): merge into `master`, push. Finished branch gets tag `archive/<name>` and is deleted locally and on origin. Never delete `archive/*` tags, never force-push, never rewrite `master`.

**Report:** branch, head SHA, gate command and result, verifier verdict, merge SHA or why not merged.

### Programs (many units, several branches or phases)

Coordinator owns the program, never the code; workers and verifiers are local bb child threads (`references/bb-runtime.md`). A single task or 1–2 files with an obvious approach is not a program: do it directly.

1. **Frame.** Done predicate is countable ("all N units landed, each with a land verdict"). A fork an experiment can settle → throwaway prototype; ask the owner only money / direction / risk (В-53).
2. **Queue.** `data/orchestrate/<slug>/units.tsv` (gitignored; row = id, state, branch, head SHA, brief path, verdict), edited in place. Rolling window ≤ 10 children, refilled as each finishes, never blocking batches. A completion is a queue event: read the report, update the row, spawn the next; deep review is a verifier unit. Before reporting to the owner account for every child (`bb thread list --parent-thread "$BB_THREAD_ID"`): arrived, respawned, or scope absorbed.
3. **Standing orders.** Every spawn and resume carries `.claude/preferences.md`. A directive you restate twice goes into that file. Never resume-chain a brief; respawn fresh with consolidated scope.
4. **Branch and merge order.** One writer per branch. Workers never rebase, retarget, merge or force-push. A dependent unit branches from its parent's branch or waits for the parent's merge; paste the upstream report into the downstream brief. Land each verified unit at once (steps 4–5), in dependency order; master stays green before upper units start. Conflicts and rebases are tasks for a worker.
5. **Phase gates.** A phase closes when the coordinator has read each unit's gate output and a different-model verifier says land. First unit goes through the whole path (brief → worker → verdict → merge) and fixes the template before fan-out. At ~70 % of the time budget stop spawning and land what is verified. One cheap VERIFY command: the worker runs it, the coordinator spot-checks; a separate verifier only for expensive or high-risk units.
6. **Failure.** Never resume an agent just to check on it; probe branch, `units.tsv`, srv counter. Retry by mode: cap/oom → smaller scope; network → as is; tool error → other model; unknown → once; after two retries abandon and replan. A late result is reconciled against current master before it is accepted. Mid-run discoveries: fix only what blocks the frontier, park the rest.
7. **Close.** Every child has a terminal row (done / abandoned); predicate confirmed on the real artifact; recurring corrections go to preferences.md. Reply to the owner per preferences item 4.
