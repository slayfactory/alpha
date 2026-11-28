#!/usr/bin/env bash
# TK-735: код с ПК на srv (/data/p42-tk735/code) вместе с библиотеками TK-727 / TK-728, которые он импортирует.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../_ssh.sh"
cd "$(dirname "${BASH_SOURCE[0]}")"
scp "${SSH_ID[@]}" -q tk735_*.py tk727_lib.py tk727_s5.py tk727_s5_an.py tk727_s5lib.py tk728_lib.py tk728_labels.py root@5.231.25.224:/data/p42-tk735/code/
scp "${SSH_ID[@]}" -q ../../.claude/skills/alpha-research/scripts/selection_bias.py root@5.231.25.224:/data/p42-tk735/code/
