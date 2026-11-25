#!/bin/bash
# TK-706: гейт байт в байт LTO-бинаря с panic=abort (v1-lto = будущий профиль p42: fat LTO + cgu 1 + abort; LTO без abort не быстрее базы) на тех же 600 клетках c539, что у бинаря l. Запуск на srv; результат res/gate683lto2.json.
cd /data/p42-tk683/code
export TK674_ROOT=/data/p42-tk683 TK674_BIN=/data/p42-tk683/bin/alpha-tk706lto-plain.8759375e TK674_UNI=c539 TAG=lto2
/opt/venv/bin/python tk683_gate.py --code /data/p42-tk674c/code --no-gbase --tag lto2 --threads 2 --cap-gb 2 --sig-cache /data/p42-tk683/sig-cache-b --cells-root /data/p42-tk674c /data/p42-tk680n-m --full-root /data/p42-tk680n-g /data/p42-tk680n-h /data/p42-tk680n-c > /data/p42-tk683/log/gatelto2.log 2>&1
echo rc=$? >> /data/p42-tk683/log/gatelto2.log
