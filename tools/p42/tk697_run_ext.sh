#!/usr/bin/env bash
# П-42 TK-697 (C, критик 23.11 FIX 2): строки сигналов базы без строки таблицы TK-671 — самопроверка (--which all, wb-long) и затем --which missing по 6 файлам.
# Журнал — /data/p42-tk697/fam/ext/run.log, конец — EXT-DONE. Запуск: systemd-run --unit=tk697-ext --collect -p Nice=17 -p CPUWeight=20 -p MemoryMax=8G -p CPUQuota=200% bash tk697_run_ext.sh
set -uo pipefail
PY=/data/tk592/venv/bin/python; C=/data/p42-tk697x/code; O=/data/p42-tk697/fam/ext; LOG=$O/run.log
mkdir -p $O
T0=$(date +%s)
PYTHONUTF8=1 PYTHONPATH=$C $PY $C/tk697_ext.py --win wb --side long --which all --out $O/chk-wb-long.parquet --procs 2 > $O/chk-wb-long.log 2>&1
echo "ext check wb long rc=$? sec=$(( $(date +%s) - T0 ))" | tee -a $LOG
for W in wa wb wc; do for S in long short; do
  T0=$(date +%s)
  PYTHONUTF8=1 PYTHONPATH=$C $PY $C/tk697_ext.py --win $W --side $S --which missing --out $O/$W-$S.parquet --procs 2 > $O/$W-$S.log 2>&1
  echo "ext missing $W $S rc=$? sec=$(( $(date +%s) - T0 ))" | tee -a $LOG
done; done
echo EXT-DONE | tee -a $LOG
