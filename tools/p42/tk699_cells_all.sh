#!/bin/bash
# TK-699 шаг 3: пересчёт клеток c539 — шорт, затем лонг; в каждой стороне сначала K3 (контроль, стоп при расхождении), затем клетки с изменившейся единичкой.
# Запуск юнитом (окружение — env-rest-l.sh, код — /data/p42-tk674c/code-bd): systemd-run --unit tk699-cells -p CPUWeight=800 -p MemoryMax=40G -p OOMScoreAdjust=1000 -p Nice=10 \
#   -p WorkingDirectory=/data/p42-tk674c/code-bd -E TK674_ENVFILE=/data/p42-tk674c/env-rest-l.sh bash /data/p42-tk699/code/tk699_cells_all.sh L-402
set -uo pipefail
ENTRY=$1
for SIDE in S L; do
  bash /data/p42-tk699/code/tk699_cells.sh $SIDE k3 $ENTRY || exit $?
  bash /data/p42-tk699/code/tk699_cells.sh $SIDE changed $ENTRY || exit $?
done
echo "== $(date -u +%FT%TZ) tk699-cells: готово" >> /data/p42-tk699/r674/log/cells699.log
