#!/usr/bin/env bash
# TK-700 (srv): столбцы FAST (TK-681, `alpha-feat fast`, 50 штук) на строках базы окон и пересборка таблицы признаков с ними.
# Использование: tk700_fastchain.sh <корень h1|gate> <шаг fast|asm|both> <окно …>   (h1: m01…m06 → /data/p42-tk700/p671; gate: wa / wb / wc → /data/p42-tk700/gate/p671)
# fast — Rust-шаг (пропускается, если fast.csv и строка «fast … rc=0» в steps.log есть); asm — только сборка (`tk700_asm.sh … -`: семейства coin / bd не пересчитываются, берутся готовые).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
case ${1:?h1|gate} in h1) export TK671_ROOT=/data/p42-tk700/p671 ;; gate) export TK671_ROOT=/data/p42-tk700/gate/p671 ;; *) echo "корень: h1|gate"; exit 2 ;; esac
STEP=${2:?fast|asm|both}; shift 2
for W in "$@"; do
  for S in long short; do
    D=$TK671_ROOT/fam/rust/$W-$S
    if [ "$STEP" != asm ] && ! { [ -s "$D/fast.csv" ] && grep -q "^fast .*rc=0" "$D/steps.log" 2>/dev/null; }; then
      bash "$HERE/tk700_feat.sh" "$W" "$S" 2 fast || { echo "fast $W $S: сбой"; exit 1; }
    fi
    if [ "$STEP" != fast ]; then
      bash "$HERE/tk700_asm.sh" "$W" "$S" - || { echo "asm $W $S: сбой"; exit 1; }
    fi
  done
done
echo "fastchain done: $*" >> "$TK671_ROOT/chain.log"
