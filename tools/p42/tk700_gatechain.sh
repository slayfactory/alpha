#!/usr/bin/env bash
# TK-700 (srv): ворота строк базы на wa / wb / wc — строки базы (по дампам TK-671 и ключам G1), Rust-признаки, coin / bd, сборка, сверка с таблицей TK-671 (tk700_gate_rows.py). Корень — /data/p42-tk700/gate/p671.
# Использование: tk700_gatechain.sh <окно> [окно …]   (по порядку; шаг пропускается, если выход есть)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
export TK671_ROOT=/data/p42-tk700/gate/p671
PY=/data/tk592/venv/bin/python
mkdir -p "$TK671_ROOT/rows-gate"
# известные расхождения с таблицей TK-671 (24.11, wa / wb / wc): признаки истории уровня — у строк источников 0 / 1 в таблице TK-671 возраст уровня < 0 (age_h) и пустая глубина; у TK-700 возраст ≥ 0; в осях TK-674 этих столбцов нет
ALLOW=age_h,pierce_ep,pierce_max_bp,touch_ep,since_touch_h,depth_min_bp,depth_mean_bp,approach_dur_min,since_touch_prior_h,touch_prior_none
for W in "$@"; do
  for S in long short; do
    [ -s "$TK671_ROOT/prod/lvl-$W-$S/rows.parquet" ] || TK700_TWIN_DUMPS=/data/p42-tk700/gate/g1/$W/dumps bash "$HERE/tk700_lvl.sh" "$W" "$S" "/data/p42-tk671/prod/siglv-$W-$S/dumps"
    [ -s "$TK671_ROOT/fam/rust/$W-$S/tox.csv" ] && grep -q "all_flow_deriv rc=0" "$TK671_ROOT/fam/rust/$W-$S/steps.log" 2>/dev/null || bash "$HERE/tk700_feat.sh" "$W" "$S" 2
    bash "$HERE/tk700_asm.sh" "$W" "$S"
    $PY "$HERE/tk700_gate_rows.py" --mine "$TK671_ROOT/asm/$W-$S.parquet" --ref "/data/p42-tk671/asm/$W-$S.parquet" --out "$TK671_ROOT/rows-gate/$W-$S.json" ${TK700_GATE_ARGS---allow-diff $ALLOW} 2>&1 | tee -a "$TK671_ROOT/chain.log"
  done
done
echo "gatechain done: $*" >> "$TK671_ROOT/chain.log"
