#!/usr/bin/env bash
# TK-700 (srv): очередь месяцев H1 — на каждый месяц: цепочка A long → H → B long → B short (без охраны охвата) и A short двумя частями (--part 0/2, 1/2),
# затем закрывающий проход (досчитывает недостающие партии, ставит state=done) и сводка `tk700_month_stats.py` в /data/p42-tk700/log/month-<mon>.done.
# Использование (юнитом): systemd-run --unit=tk700-queue --slice=tk700.slice -p MemoryMax=2G -E WAIT_FIRST="tk700-feb-A-short-p0 …" bash /data/p42-tk700/code/tk700_queue.sh m03 m04 m05 m06
set -uo pipefail
CODE=/data/p42-tk700/code
LOG=/data/p42-tk700/log
PY=/data/tk592/venv/bin/python
declare -A MON=([m01]=jan [m02]=feb [m03]=mar [m04]=apr [m05]=may [m06]=jun)
RUN="$CODE/run-mass.sh"
ARGS="--vmax 800 --sigmax 26 --cap-gb 6"

busy() { for u in "$@"; do systemctl is-active -q "$u" && return 0; done; return 1; }

# Все юниты месяца — в слайсе tk700.slice: общий потолок ЦП 700 % (8 vCPU минус ≈ 1 под коллекторы, приказ координатора 24.11 «один движок счёта за раз») и память 40 ГБ.
systemctl set-property --runtime tk700.slice CPUQuota=${QUOTA:-700%} MemoryMax=40G

# WAIT_FIRST="юнит …" — подождать юниты предыдущего месяца, прежде чем начинать первый
[ -n "${WAIT_FIRST:-}" ] && while busy $WAIT_FIRST; do sleep 30; done
for M in "$@"; do
  N=${MON[$M]}
  echo "$(date -u +%FT%TZ) месяц $M ($N): старт"
  systemd-run --unit=tk700-$N-lite --slice=tk700.slice -p CPUWeight=400 -p Nice=5 -p MemoryMax=18G /bin/bash -c "
    $RUN h1-$N-A --cellset all1 --wins $M --sides long --class A --threads 3 $ARGS >> $LOG/h1-$N-A.log 2>&1
    $RUN h1-$N-H --cellset all2 --wins $M --class H --threads 3 $ARGS >> $LOG/h1-$N-H.log 2>&1
    $RUN h1-$N-B --cellset all2 --wins $M --class B --sides long --threads 3 $ARGS >> $LOG/h1-$N-B.log 2>&1
    $RUN h1-$N-B-short --cellset all2 --wins $M --class B --sides short --unguarded short --threads 3 $ARGS >> $LOG/h1-$N-B-short.log 2>&1" > /dev/null
  for k in 0 1; do
    systemd-run --unit=tk700-$N-A-short-p$k --slice=tk700.slice -p CPUWeight=800 -p Nice=5 -p MemoryMax=20G /bin/bash -c \
      "$RUN h1-$N-A-short --cellset all1 --wins $M --sides short --class A --threads 3 --part $k/2 $ARGS >> $LOG/h1-$N-A-short-p$k.log 2>&1" > /dev/null
  done
  sleep 30
  while busy tk700-$N-A-short-p0 tk700-$N-A-short-p1; do sleep 30; done
  # закрывающий проход: недостающие партии (сбой / OOM части) досчитываются, state=done
  $RUN h1-$N-A-short --cellset all1 --wins $M --sides short --class A --threads 4 $ARGS >> $LOG/h1-$N-A-short-close.log 2>&1
  while busy tk700-$N-lite; do sleep 30; done
  $PY $CODE/tk700_month_stats.py $N > $LOG/month-$N.done 2>&1
  echo "$(date -u +%FT%TZ) месяц $M ($N): готово"
done
