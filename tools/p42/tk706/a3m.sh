#!/bin/bash
# TK-706 A3 для бинаря с памятью исходов (`m1`) против `v1-lto` в одном окне: ПОЛНЫЙ тяжёлый боевой вызов (539 монет, --threads 7, --cap-gb 24, P42_WIDE=200, юнит MemoryMax=45G).
# Ждёт `a3m.go`, затем ставит HOLD (TK-700 `HOLD-a3`, TK-674 `<ROOT>/HOLD`: пауза перед следующей партией, текущие не трогает), ждёт, пока не погаснет последний чужой `breakout tick`; HOLD снимает при выходе — нагрузку смотреть по строке `a3m-start … load` и ЦП-секундам, не по стене. Бинари — `a3m.bins` (<тег>:<путь>); промахи, затем по одному попаданию.

cd /root/perf706
R=/root/perf706/bench/results12.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
H1=/data/p42-tk700/HOLD-a3; H2=/data/p42-tk674c/HOLD   # паузы перед следующей партией TK-700 / TK-674 (приказ координатора 25.11); ставятся ТОЛЬКО после `a3m.go`, снимаются при выходе
# занято = жив чей-либо `breakout tick` вне юнитов tk706-* (юниты партий остаются active и в паузе HOLD, поэтому по юнитам не судим)
busy(){ for p in $(pgrep -f "breakout tick"); do grep -q "tk706-" /proc/$p/cgroup 2>/dev/null || return 0; done; return 1; }
while [ ! -e /root/perf706/a3m.go ]; do sleep 5; done   # бинарь собран, H1 и гейт пройдены (chainm.sh); до этого хост занят производством
trap 'rm -f $H1 $H2; echo "a3m-end $(date -u +%T)" >> $R' EXIT
echo "a3m-hold $(date -u +%T)" >> $R
while busy; do touch $H1 $H2; sleep 5; done
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
