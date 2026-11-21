#!/bin/bash
# П-42 §13.72 (TK-672): обе стороны одной библиотеки подряд. Запуск — юнитом: systemd-run --unit tk672-fit-<lib> -p Nice=10 bash tk672_fit_all.sh <cb|lgb> <потоки> [train|score]
#   train — N*, модели, models.sha256 (метка TRAIN-DONE); score — проверка sha256 и оценки (метка FIT-DONE). Оценки — только после GO критика.
set -euo pipefail
LIB=${1:?cb|lgb}; TH=${2:-4}; PH=${3:-train}
PY=/data/tk592/venv/bin/python; P=/data/p42-tk672/code/p; OUT=/data/p42-tk672/fit/$LIB
mkdir -p "$OUT"
export PYTHONUTF8=1 OMP_NUM_THREADS=$TH
for S in long short; do
  nice -n 10 "$PY" "$P/tk672_fit.py" --lib "$LIB" --side "$S" --data "/data/p42-tk672/data/$S.parquet" --out "$OUT/$S" --threads "$TH" --step "$PH"
done
if [ "$PH" = score ]; then date -u +%FT%TZ > "$OUT/FIT-DONE"; else date -u +%FT%TZ > "$OUT/TRAIN-DONE"; fi
