#!/bin/bash
# TK-706 (G1): гейт байт в байт по ЗАМОРОЖЕННОМУ списку клеток (см. docstring tk683_gate_706f.py); запуск на srv.
#   gatef.sh freeze <файл.json>              — собрать список: клетки гейта m2 (res/gate683gmm2.json) + свежие клетки tk674c (последние 36 ч), копия посчитанных клеток в <файл>.ref/ (без счёта);
#   gatef.sh list <бинарь> <тег> <файл.json> — сверить бинарь по списку (профиль p42 + память, VERIFY=1); итог res/gate683<тег>.json, журнал log/gate<тег>.log.
# Окно HOLD для `list`: holdrun2.sh gate bash gatef.sh list …  (THREADS=8 — потоков движка).
MODE=${1:?freeze|list}
cd /data/p42-tk683/code
export TK674_ROOT=/data/p42-tk683 TK674_UNI=c539
COMMON="--code /data/p42-tk674c/code --no-gbase --cap-gb 2 --sig-cache /data/p42-tk683/sig-cache-b --cells-root /data/p42-tk674c /data/p42-tk680n-m --full-root /data/p42-tk680n-g /data/p42-tk680n-h /data/p42-tk680n-c"
if [ "$MODE" = freeze ]; then
  LST=${2:?файл списка}
  export TK674_BIN=/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45
  /opt/venv/bin/python /root/perf706/tk683_gate_706f.py $COMMON --freeze $LST --from-gate /data/p42-tk683/res/gate683gmm2.json --fresh-root /data/p42-tk674c --fresh-hours ${FRESH_HOURS:-36} --fresh-batches ${FRESH_BATCHES:-6} --fresh-per-batch ${FRESH_PER:-3}
else
  BIN=${2:?бинарь}; TAG=${3:?тег}; LST=${4:?файл списка}
  export TK674_BIN=$BIN TAG=$TAG P42_MEMO_VERIFY=1
  /opt/venv/bin/python /root/perf706/tk683_gate_706f.py $COMMON --tag $TAG --threads ${THREADS:-4} --list $LST > /data/p42-tk683/log/gate$TAG.log 2>&1
  echo rc=$? >> /data/p42-tk683/log/gate$TAG.log
fi
