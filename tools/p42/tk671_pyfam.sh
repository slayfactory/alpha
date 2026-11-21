#!/usr/bin/env bash
# П-42 §13.71 (TK-671): Python-семейства (time, fund, t668 с дампами уровней, coin, bd, hist) на всех 6 файлах строк (окно × сторона) → /data/p42-tk671/fam/<имя>/<окно>-<side>.parquet (+ .info.json); время — fam/pyfam.log.
# Использование: tk671_pyfam.sh <потоки> [семейство…]   (по умолчанию все шесть; один процесс на семейство-файл, до <потоков> процессов внутри)
set -uo pipefail
PR=$1; shift
FAMS=${*:-time fund t668 coin bd hist}
PY=/data/tk592/venv/bin/python; C=/data/p42-tk671/code/p; F=/data/p42-tk671/fam
for FAM in $FAMS; do
  mkdir -p $F/$FAM
  for W in wa wb wc; do for S in long short; do
    EXTRA=""; [ "$FAM" = t668 ] && EXTRA="--levels-dir /data/p42-tk671/prod/siglv-$W-$S/dumps"
    T=$(date +%s)
    PYTHONPATH=$C $PY $C/tk671_$FAM.py --rows /data/p42-tk671/prod/lvl-$W-$S/rows.parquet --win $W --side $S --out $F/$FAM/$W-$S.parquet --procs $PR $EXTRA > $F/$FAM/$W-$S.log 2>&1
    echo "$FAM $W $S rc=$? sec=$(( $(date +%s) - T )) procs=$PR load=$(cut -d' ' -f1 /proc/loadavg)" | tee -a $F/pyfam.log
  done; done
done
echo PYFAM-DONE >> $F/pyfam.log
