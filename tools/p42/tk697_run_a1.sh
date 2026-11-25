#!/usr/bin/env bash
# П-42 TK-697, шаг A1: построение семейства m697 на 6 файлах строк (окно × сторона) → /data/p42-tk697/fam/m697/<окно>-<side>.parquet, затем ворота `tk697_gate.py`
# (wa-long и wc-short — полная выборка, остальные четыре — малая). Запуск: systemd-run --unit=tk697-a1 --collect -p Nice=15 -p CPUWeight=20 -p MemoryMax=8G -p CPUQuota=200% bash tk697_run_a1.sh [процессов]
set -uo pipefail
PR=${1:-2}
PY=/data/tk592/venv/bin/python; C=/data/p42-tk697/code/p; F=/data/p42-tk697/fam/m697; R=/data/p42-tk697/res; T=/data/p42-tk697/tmp
mkdir -p $F $R $T
for W in wa wb wc; do for S in long short; do
  ROWS=/data/p42-tk671/prod/lvl-$W-$S/rows.parquet
  T0=$(date +%s)
  PYTHONPATH=$C $PY $C/tk697_cols.py --rows $ROWS --win $W --side $S --out $F/$W-$S.parquet --procs $PR > $F/$W-$S.log 2>&1
  echo "build $W $S rc=$? sec=$(( $(date +%s) - T0 )) load=$(cut -d' ' -f1 /proc/loadavg)" | tee -a $F/run.log
done; done
for W in wa wb wc; do for S in long short; do
  ROWS=/data/p42-tk671/prod/lvl-$W-$S/rows.parquet
  K="--k-brute 300 --k-causal 100 --k-file 6"
  { [ "$W-$S" = wa-long ] || [ "$W-$S" = wc-short ]; } && K=""
  T0=$(date +%s)
  PYTHONPATH=$C $PY $C/tk697_gate.py --rows $ROWS --win $W --side $S --out $R/gate-m697-$W-$S.json --procs $PR --tmp $T $K > $F/$W-$S.gate.log 2>&1
  echo "gate $W $S rc=$? sec=$(( $(date +%s) - T0 ))" | tee -a $F/run.log
done; done
echo A1-DONE >> $F/run.log
