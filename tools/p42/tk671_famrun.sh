#!/usr/bin/env bash
# П-42 §13.71 (TK-671): прогон семейства (hist | bk) на всех 6 файлах строк (окно × сторона) → /data/p42-tk671/fam/<имя>/<окно>-<side>.parquet (+ .info.json); время — в done.log.
# Использование: tk671_famrun.sh <hist|bk>
set -uo pipefail
N=$1; O=/data/p42-tk671/fam/$N; mkdir -p $O
for W in wa wb wc; do for S in long short; do
  T=$(date +%s)
  PYTHONPATH=/data/p42-tk671/code/p /data/tk592/venv/bin/python /data/p42-tk671/code/p/tk671_$N.py --rows /data/p42-tk671/prod/lvl-$W-$S/rows.parquet --win $W --side $S --out $O/$W-$S.parquet > $O/$W-$S.log 2>&1
  echo "$N $W $S rc=$? sec=$(( $(date +%s) - T ))" | tee -a $O/done.log
done; done
