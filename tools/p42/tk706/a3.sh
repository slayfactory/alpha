#!/bin/bash
# TK-706 A3 (замечание критика): ПОЛНЫЙ тяжёлый боевой вызов — 539 монет, --threads 7, --cap-gb 24, P42_WIDE=200, юнит MemoryMax=40G без CPUQuota.
# Ждёт, пока кончатся tk674c-quadsS3, tk674c-tab-* и свои tk706-* (кроме себя), берёт /data/p42-tk700/HOLD-a3 на время (создаёт TK-700), по выходу снимает его (и при ошибке).
# Список бинарей — /root/perf706/a3.bins (строки <тег>:<путь>), читается ПОСЛЕ ожидания.
cd /root/perf706
R=/root/perf706/bench/results11.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
trap 'rm -f /data/p42-tk700/HOLD-a3; echo "a3-end $(date -u +%T)" >> $R' EXIT
busy(){ systemctl is-active --quiet tk674c-quadsS3 || [ -n "$(systemctl list-units "tk674c-tab*" --state=active --no-legend)" ] || [ -n "$(systemctl list-units "tk706-*" --state=active --no-legend | grep -v tk706-a3)" ]; }
while busy; do sleep 30; done
echo "a3-start $(date -u +%T)" >> $R
for pair in $(cat /root/perf706/a3.bins); do
  T=${pair%%:*}; BIN=${pair#*:}
  rm -rf /root/perf706/sc11-$T
  python3 bench.py --bin $BIN --cap-gb 24 --wide 200 --threads 7 --tag a3-$T-miss --variants $HV --every 1 --sig /root/perf706/sc11-$T >> $R 2>> bench/q11.err
  for i in 1 2; do
    python3 bench.py --bin $BIN --cap-gb 24 --wide 200 --threads 7 --tag a3-$T-hit-$i --variants $HV --every 1 --sig /root/perf706/sc11-$T >> $R 2>> bench/q11.err
  done
done
echo done-a3 >> $R
