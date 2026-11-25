#!/bin/bash
# TK-706: постоянная пауза TK-700 / TK-674 на время работ по ускорению (приказ владельца 25.11: «сделай еще ускорение», приоритет выше счёта). Подновляет HOLD-файлы каждые 5 с, пока не появится
# /root/perf706/HOLD-RELEASE; снимает файлы при выходе; ждёт погасания чужих `breakout tick` — по `busy` (вне юнитов tk706-*), момент готовности пишет в /root/perf706/hold-log.txt.
L=/root/perf706/hold-log.txt; H1=/data/p42-tk700/HOLD-a3; H2=/data/p42-tk674c/HOLD
busy(){ for p in $(pgrep -f "breakout tick"); do grep -q "tk706-" /proc/$p/cgroup 2>/dev/null || return 0; done; return 1; }
rm -f /root/perf706/HOLD-RELEASE; trap 'rm -f $H1 $H2; echo "hold lifted $(date -u +%T)" >> $L' EXIT
touch $H1 $H2; echo "hold set $(date -u +%T)" >> $L
was=1
while [ ! -e /root/perf706/HOLD-RELEASE ]; do
  touch $H1 $H2
  if busy; then was=1; elif [ $was = 1 ]; then was=0; echo "host idle $(date -u +%T) load $(cut -d' ' -f1 /proc/loadavg)" >> $L; fi
  sleep 5
done
