#!/usr/bin/env bash
# TK-700 (srv): Python-семейства (coin, bd) на строках базы окна и сборка таблицы признаков окна × стороны (tk671_assemble.py --skip-missing) → $TK671_ROOT/asm/<окно>-<side>.parquet (+ .cov.json).
# Использование: tk700_asm.sh <окно m01…m06|wa|wb|wc> <side long|short> [семейства через пробел; по умолчанию «coin bd»; «-» — только сборка]
# Rust-признаки (tk700_feat.sh) и строки базы (tk700_lvl.sh) должны быть готовы. Семейства time / fund / t668 / hist / bk / mkt в осях TK-674 не нужны (tk700_cov.py) и не строятся: assemble их пропускает.
set -uo pipefail
WIN=${1:?окно}; SIDE=${2:?сторона}; FAMS=${3:-coin bd}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=${TK671_ROOT:-/data/p42-tk700/p671}; export TK671_ROOT=$ROOT
PY=${TK700_PY:-/data/tk592/venv/bin/python}
export PYTHONUTF8=1
case $WIN in m0*) export TK700_WINSET=h1 ;; n1) export TK700_WINSET=n1 ;; esac
for F in $FAMS; do
  [ "$F" = "-" ] && continue
  mkdir -p "$ROOT/fam/$F"
  S=$(date +%s)
  $PY "$HERE/tk700_fam.py" "$F" --rows "$ROOT/prod/lvl-$WIN-$SIDE/rows.parquet" --win "$WIN" --side "$SIDE" --out "$ROOT/fam/$F/$WIN-$SIDE.parquet" --procs "${TK700_PROCS:-2}" > "$ROOT/fam/$F/$WIN-$SIDE.log" 2>&1
  echo "$F $WIN $SIDE rc=$? sec=$(( $(date +%s) - S )) $(tail -1 "$ROOT/fam/$F/$WIN-$SIDE.log" | cut -c1-160)" | tee -a "$ROOT/chain.log"
done
S=$(date +%s)
$PY "$HERE/tk671_assemble.py" --win "$WIN" --side "$SIDE" --out "$ROOT/asm" --skip-missing > "$ROOT/asm-$WIN-$SIDE.log" 2>&1
echo "asm $WIN $SIDE rc=$? sec=$(( $(date +%s) - S )) $(tail -1 "$ROOT/asm-$WIN-$SIDE.log" | cut -c1-160)" | tee -a "$ROOT/chain.log"
