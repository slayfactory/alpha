#!/usr/bin/env bash
# TK-739: код с ПК на srv (/data/p42-tk739/code) — весь tools/p42 (без archive) + библиотеки скилла (selection_bias, effective_n). Запуск: bash tools/p42/tk739_sync.sh
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../_ssh.sh"
cd "$(dirname "${BASH_SOURCE[0]}")"
H=root@5.231.25.224
ssh "${SSH_ID[@]}" $H 'mkdir -p /data/p42-tk739/code /data/p42-tk739/res /data/p42-tk739/log /data/p42-tk739/trades /data/p42-tk739/gmap'
tar czf - --exclude=archive --exclude=__pycache__ . | ssh "${SSH_ID[@]}" $H 'tar xzf - -C /data/p42-tk739/code'
scp "${SSH_ID[@]}" -q ../../.claude/skills/alpha-research/scripts/selection_bias.py ../../.claude/skills/alpha-research/scripts/effective_n.py $H:/data/p42-tk739/code/
echo "sync ok"
