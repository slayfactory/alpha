#!/bin/bash
# TK-706 A3 (версия b, 25.11 03:50 GMT+4, приказ координатора: «speedup first»): ПОЛНЫЙ тяжёлый боевой вызов — 539 монет, --threads 7, --cap-gb 24, P42_WIDE=200, юнит MemoryMax=40G без CPUQuota.
# Отличия от a3.sh: ждёт только tk674c-quadsS3 и tk674c-tab-* (свои tk706-* заморожены `systemctl freeze`, не считаются); HOLD-a3 НЕ снимает (снимает исполнитель после гейта и PASS критика);
# порядок: промахи обоих бинарей, затем по одному попаданию (по a3.bins: <тег>:<путь>); второй повтор попадания — отдельно (a3c.sh), когда хост свободен.
cd /root/perf706
R=/root/perf706/bench/results11.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
trap 'echo "a3-end $(date -u +%T)" >> $R' EXIT
busy(){ systemctl is-active --quiet tk674c-quadsS3 || [ -n "$(systemctl list-units "tk674c-tab*" --state=active --no-legend)" ]; }
while busy; do sleep 5; done
echo "a3-start $(date -u +%T) load $(cut -d' ' -f1 /proc/loadavg)" >> $R
run(){ T=$1; BIN=$2; K=$3; python3 bench.py --bin $BIN --cap-gb 24 --wide 200 --threads 7 --tag a3-$T-$K --variants $HV --every 1 --sig /root/perf706/sc11-$T >> $R 2>> bench/q11.err; }
for pair in $(cat /root/perf706/a3.bins); do
  T=${pair%%:*}; BIN=${pair#*:}
  rm -rf /root/perf706/sc11-$T
  run $T $BIN miss
done
for pair in $(cat /root/perf706/a3.bins); do
  T=${pair%%:*}; BIN=${pair#*:}
  run $T $BIN hit-1
done
echo done-a3 >> $R
