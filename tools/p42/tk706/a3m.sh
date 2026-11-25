#!/bin/bash
# TK-706 A3 для бинаря с памятью исходов (`m1`) против `v1-lto` в одном окне: ПОЛНЫЙ тяжёлый боевой вызов (539 монет, --threads 7, --cap-gb 24, P42_WIDE=200, юнит MemoryMax=45G).
# Ждёт tk674c-quadsS3 и tk674c-tab-*; HOLD не ставит (чужих юнитов не останавливает) — нагрузку смотреть по строке `a3m-start … load` и ЦП-секундам, не по стене. Бинари — `a3m.bins` (<тег>:<путь>); промахи, затем по одному попаданию.

cd /root/perf706
R=/root/perf706/bench/results12.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
trap 'echo "a3m-end $(date -u +%T)" >> $R' EXIT
busy(){ systemctl is-active --quiet tk674c-quadsS3 || [ -n "$(systemctl list-units "tk674c-tab*" --state=active --no-legend)" ]; }
while busy; do sleep 5; done
echo "a3m-start $(date -u +%T) load $(cut -d' ' -f1 /proc/loadavg)" >> $R
run(){ T=$1; BIN=$2; K=$3; python3 bench.py --bin $BIN --cap-gb 24 --wide 200 --threads 7 --tag a3m-$T-$K --variants $HV --every 1 --sig /root/perf706/sc12-$T >> $R 2>> bench/q12.err; }
for pair in $(cat /root/perf706/a3m.bins); do
  T=${pair%%:*}; BIN=${pair#*:}
  rm -rf /root/perf706/sc12-$T
  run $T $BIN miss
done
for pair in $(cat /root/perf706/a3m.bins); do
  T=${pair%%:*}; BIN=${pair#*:}
  run $T $BIN hit-1
done
echo done-a3m >> $R
