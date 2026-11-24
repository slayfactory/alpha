#!/usr/bin/env bash
# П-42 TK-697: повтор ворот семейства l697 (G-brute, G-causal, G-tf — критик 23.11) по 6 файлам; ждёт конца tk697-a23. Журнал — /data/p42-tk697/fam/lgate.log, конец — LGATE-DONE.
# Запуск: systemd-run --unit=tk697-lgate --collect -p Nice=17 -p CPUWeight=20 -p MemoryMax=8G -p CPUQuota=200% bash tk697_run_lgate.sh
set -uo pipefail
PY=/data/tk592/venv/bin/python; C=/data/p42-tk697/code/p; R=/data/p42-tk697/res; T=/data/p42-tk697/tmp; LOG=/data/p42-tk697/fam/lgate.log
while systemctl is-active -q tk697-a23; do sleep 20; done
for W in wa wb wc; do for S in long short; do
  K="--k-brute 300 --k-causal 60 --k-tf 20"
  { [ "$W-$S" = wa-long ] || [ "$W-$S" = wc-short ]; } && K="--k-tf 20"
  T0=$(date +%s)
  PYTHONPATH=$C $PY $C/tk697_lv_gate.py --rows /data/p42-tk671/prod/lvl-$W-$S/rows.parquet --win $W --side $S --out $R/gate-l697-$W-$S.json --tmp $T $K > /data/p42-tk697/fam/l697/$W-$S.gate.log 2>&1
  echo "l697 gate $W $S rc=$? sec=$(( $(date +%s) - T0 ))" | tee -a $LOG
done; done
echo LGATE-DONE | tee -a $LOG
