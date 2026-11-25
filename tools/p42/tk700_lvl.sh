#!/usr/bin/env bash
# TK-700 (srv): эпизоды окна и строки БАЗЫ — `tk671_lvl.py build` по дампам `tk700_sig.sh all` (ключи базы — из дампов L_ind / S_ind / D_ind, ворота G1), затем только `base_pass > 0` → $TK671_ROOT/prod/lvl-<окно>-<side>/rows.parquet.
# Использование: tk700_lvl.sh <окно> <side long|short> [каталог дампов]   (по умолчанию /data/p42-tk700/prod/all-<окно>/dumps; для ворот TK700_WINSET не ставится)
# Полная таблица эпизодов остаётся в $TK671_ROOT/lvl-full/<окно>-<side> (нужна семействам истории: hist / bk — им нужны все эпизоды монеты, не только база).
set -uo pipefail
WIN=${1:?окно}; SIDE=${2:?сторона}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=${TK671_ROOT:-/data/p42-tk700/p671}
PY=${TK700_PY:-/data/tk592/venv/bin/python}
D=${3:-/data/p42-tk700/prod/all-$WIN/dumps}
TW=${TK700_TWIN_DUMPS:-$D}          # ворота wa: источники — прежние дампы TK-671, ключи базы — дампы L_ind / S_ind / D_ind ворот G1
case $WIN in wa|wb|wc) ;; n1) export TK700_WINSET=n1 ;; *) export TK700_WINSET=h1 ;; esac
export PYTHONUTF8=1
S=$(date +%s)
$PY "$HERE/tk671_lvl.py" build --side "$SIDE" --win "$WIN" --dumps "$D" --twin-dumps "$TW" --out "$ROOT/lvl-full/$WIN-$SIDE" > "$ROOT/lvl-$WIN-$SIDE.log" 2>&1 || { echo "lvl $WIN $SIDE rc=$?" | tee -a "$ROOT/chain.log"; exit 1; }
$PY "$HERE/tk700_prep.py" base --lvl "$ROOT/lvl-full/$WIN-$SIDE" --out "$ROOT/prod/lvl-$WIN-$SIDE" >> "$ROOT/lvl-$WIN-$SIDE.log" 2>&1 || { echo "base $WIN $SIDE rc=$?" | tee -a "$ROOT/chain.log"; exit 1; }
echo "lvl $WIN $SIDE rc=0 sec=$(( $(date +%s) - S )) $(tail -1 "$ROOT/lvl-$WIN-$SIDE.log" | cut -c1-200)" | tee -a "$ROOT/chain.log"
