#!/bin/bash
# TK-706: гейт байт в байт бинаря с памятью исходов хода (профиль p42 + память) на тех же клетках c539, что у `gate706b.sh`; сверка КАЖДОГО попадания памяти
# (`P42_MEMO_VERIFY=1`: любое расхождение — panic, rc −6 → GuardError). Запуск на srv юнитом: gatem.sh <бинарь> <тег>; результат res/gate683<тег>.json, журнал log/gate<тег>.log.
BIN=${1:?бинарь}; TAG=${2:?тег}
cd /data/p42-tk683/code
export TK674_ROOT=/data/p42-tk683 TK674_BIN=$BIN TK674_UNI=c539 TAG=$TAG P42_MEMO_VERIFY=1
/opt/venv/bin/python /root/perf706/tk683_gate_706.py --code /data/p42-tk674c/code --no-gbase --tag $TAG --threads ${THREADS:-4} --cap-gb 2 --sig-cache /data/p42-tk683/sig-cache-b --cells-root /data/p42-tk674c /data/p42-tk680n-m --full-root /data/p42-tk680n-g /data/p42-tk680n-h /data/p42-tk680n-c > /data/p42-tk683/log/gate$TAG.log 2>&1
echo rc=$? >> /data/p42-tk683/log/gate$TAG.log
