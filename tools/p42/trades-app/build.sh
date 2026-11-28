#!/usr/bin/env bash
# TK-742: сборка приложения «Сделки» (React + shadcn/ui + Tailwind + lightweight-charts) на Софии (node 18; ПК владельца и srv без node — AGENTS.md «Машины»).
#   bash tools/p42/trades-app/build.sh
# Исходники → /root/tk742-build на Софии → `npm ci` (или `npm install`, если package-lock.json ещё нет — файл возвращается сюда) → tsc --noEmit → vite build → dist/ рядом со скриптом:
#   trades.js, trades.css, LICENSE-lightweight-charts.txt (Apache-2.0, NOTICE). Каталог dist не в git; ml_page_update.sh кладёт его на srv в <страница>/trades-app/.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../../_ssh.sh"
cd "$(dirname "$0")"
KEY=("${SSH_ID[@]}" -o ConnectTimeout=15 -o BatchMode=yes)
H=root@13.140.29.171
D=/root/tk742-build
ssh "${KEY[@]}" "$H" "mkdir -p $D && cd $D && rm -rf src dist"
tar -c --exclude=node_modules --exclude=dist . | ssh "${KEY[@]}" "$H" "cd $D && tar -x"
if [ -f package-lock.json ]; then INST="npm ci --no-audit --no-fund"; else INST="npm install --no-audit --no-fund"; fi
ssh "${KEY[@]}" "$H" "set -o pipefail; systemd-run --quiet --wait --pipe --nice=19 -p MemoryMax=3G -p WorkingDirectory=$D -E PATH=/usr/local/bin:/usr/bin:/bin -E HOME=/root sh -c '$INST && npm run typecheck && npm run build && cp node_modules/lightweight-charts/LICENSE dist/LICENSE-lightweight-charts.txt' 2>&1 | tail -25"
rm -rf dist && mkdir dist
ssh "${KEY[@]}" "$H" "cd $D/dist && tar -c ." | tar -x -C dist
[ -f package-lock.json ] || ssh "${KEY[@]}" "$H" "cat $D/package-lock.json" > package-lock.json
ls -l dist
