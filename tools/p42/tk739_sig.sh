#!/usr/bin/env bash
# TK-739 (G-map, §13.81.4 п. 1.2): дамп сигналов B* без фильтра живости (natr_min = 0) на окне — P42_SIGSX_ONLY=1: портфель и P&L не считаются.
# Использование: tk739_sig.sh <окно m02…m06|wa|wb|wc> <каталог выхода> [потоки]. Строка B* — `tk739_gates.py bstar-line` (natr_min = 0). Вселенная — c539 (окно m0x: `universe/<окно>.txt`, SEL: symbols-crypto).
set -uo pipefail
WIN=${1:?окно}; OUT=${2:?каталог}; THR=${3:-3}
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=${TK739_BIN:-/data/p42-tk738/bin/alpha-tk738-plain.66eab54e}
eval "$(python3 "$HERE/tk700_win.py" env "$WIN")"
case $WIN in m0*) SYMS=/data/p42-tk700/universe/$WIN.txt ;; *) SYMS=/data/p42-tk621/universe/symbols-crypto.txt ;; esac
COINS=$(cat "$SYMS")
STORE="--tick-store /data/p42-tk599/tick-store --tick-store-cap-gb 0 --tick-store-min-free-gb 5"
SIGCACHE=/data/p42-tk739/gmap/sigcache-$WIN
mkdir -p "$OUT" "$SIGCACHE"
LINE=$(python3 "$HERE/tk739_gates.py" bstar-line)
echo "BSX $LINE natr_min=0" > "$OUT/variants.txt"
S=$(date +%s)
P42_T0=$T0 P42_SIGSX_ONLY=1 P42_SIGSX=$SIGSX "$BIN" breakout tick --cache "$TREE/cache" --trades "$TREE/aggtrades" --variants "$OUT/variants.txt" --symbols "$COINS" --out "$OUT/dumps" \
  --threads "$THR" --cap-gb 4 --sig-cache "$SIGCACHE" $STORE > "$OUT/sig.log" 2>&1
echo "bstar $WIN rc=$? sec=$(( $(date +%s) - S )) sigsx=$SIGSX syms=$(echo $COINS | wc -w)" | tee -a "$OUT/done.log"
