#!/usr/bin/env bash
# П-42 TK-697, шаги A2 (семейство h697, № 8) и A3 (семейство l697, № 2 / № 7): ждёт конца юнита tk697-a1, затем строит дневную таблицу, 6 файлов `h697` и 6 файлов `l697` и гонит ворота
# (wa-long и wc-short — полная выборка, остальные четыре — малая). Журнал — /data/p42-tk697/fam/a23.log, конец — A23-DONE.
# Запуск: systemd-run --unit=tk697-a23 --collect -p Nice=17 -p CPUWeight=20 -p MemoryMax=8G -p CPUQuota=200% bash tk697_run_a23.sh [процессов]
set -uo pipefail
PR=${1:-2}
PY=/data/tk592/venv/bin/python; C=/data/p42-tk697/code/p; FH=/data/p42-tk697/fam/h697; FL=/data/p42-tk697/fam/l697; R=/data/p42-tk697/res; T=/data/p42-tk697/tmp
D=/data/p42-tk697/daily; LOG=/data/p42-tk697/fam/a23.log
mkdir -p $FH $FL $R $T
while systemctl is-active -q tk697-a1; do sleep 20; done
say() { echo "$*" | tee -a $LOG; }
T0=$(date +%s)
PYTHONPATH=$C $PY $C/tk697_hl.py build-daily --root $D --out $D/daily.parquet > $FH/build-daily.log 2>&1
say "build-daily rc=$? sec=$(( $(date +%s) - T0 ))"
for W in wa wb wc; do for S in long short; do
  ROWS=/data/p42-tk671/prod/lvl-$W-$S/rows.parquet
  T0=$(date +%s)
  PYTHONPATH=$C $PY $C/tk697_hl.py build --rows $ROWS --daily $D/daily.parquet --win $W --side $S --out $FH/$W-$S.parquet > $FH/$W-$S.log 2>&1
  say "h697 build $W $S rc=$? sec=$(( $(date +%s) - T0 ))"
  T0=$(date +%s)
  PYTHONPATH=$C $PY $C/tk697_lv.py --rows $ROWS --win $W --side $S --out $FL/$W-$S.parquet --procs $PR > $FL/$W-$S.log 2>&1
  say "l697 build $W $S rc=$? sec=$(( $(date +%s) - T0 ))"
done; done
for W in wa wb wc; do for S in long short; do
  ROWS=/data/p42-tk671/prod/lvl-$W-$S/rows.parquet
  KH="--k-brute 300 --k-causal 60 --k-eq 1000"; KL="--k-brute 300 --k-causal 60"
  { [ "$W-$S" = wa-long ] || [ "$W-$S" = wc-short ]; } && { KH=""; KL=""; }
  T0=$(date +%s)
  PYTHONPATH=$C $PY $C/tk697_hl_gate.py --rows $ROWS --daily $D/daily.parquet --win $W --side $S --out $R/gate-h697-$W-$S.json --tmp $T $KH > $FH/$W-$S.gate.log 2>&1
  say "h697 gate $W $S rc=$? sec=$(( $(date +%s) - T0 ))"
  T0=$(date +%s)
  PYTHONPATH=$C $PY $C/tk697_lv_gate.py --rows $ROWS --win $W --side $S --out $R/gate-l697-$W-$S.json --tmp $T $KL > $FL/$W-$S.gate.log 2>&1
  say "l697 gate $W $S rc=$? sec=$(( $(date +%s) - T0 ))"
done; done
say A23-DONE
