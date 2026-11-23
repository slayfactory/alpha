#!/usr/bin/env bash
# TK-700 (srv): строки базы окон H1 — по готовности дампов `tk700_sig.sh all` (done.log с rc=0) запускает tk700_lvl.sh long / short. Ждёт локально (юнит), не опросом с ПК.
# Использование: tk700_h1lvl.sh <окно> [окно …]     (TK671_ROOT по умолчанию /data/p42-tk700/p671)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
for w in "$@"; do
  until grep -q "^all $w rc=0" "/data/p42-tk700/prod/all-$w/done.log" 2>/dev/null; do sleep 20; done
  for side in long short; do
    [ -s "${TK671_ROOT:-/data/p42-tk700/p671}/prod/lvl-$w-$side/rows.parquet" ] && continue
    bash "$HERE/tk700_lvl.sh" "$w" "$side"
  done
done
echo "h1lvl done: $*" >> "${TK671_ROOT:-/data/p42-tk700/p671}/chain.log"
