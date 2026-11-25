#!/bin/bash
# TK-706: короткое окно HOLD (приказ владельца 25.11: тяжёлые замеры — только на границе партии): holdrun.sh <метка> <команда…> — ставит HOLD-файлы TK-700 / TK-674 (пауза ПЕРЕД следующей
# партией, идущие не трогает), ждёт, пока погаснет последний чужой `breakout tick` (вне юнитов tk706-*), выполняет команду, снимает HOLD сразу при выходе; времена — в /root/perf706/hold-log.txt.
L=/root/perf706/hold-log.txt; H1=/data/p42-tk700/HOLD-a3; H2=/data/p42-tk674c/HOLD
busy(){ for p in $(pgrep -f "breakout tick"); do grep -q "tk706-" /proc/$p/cgroup 2>/dev/null || return 0; done; return 1; }
LAB=${1:?метка}; shift
exec 9>/root/perf706/hold.lock   # окна не пересекаются: второй ждёт первого (TK-706, 25.11); пока ждёт — держит HOLD, чтобы между окнами партия не стартовала
until flock -n 9; do touch $H1 $H2; sleep 1; done
touch $H1 $H2; trap 'rm -f $H1 $H2; echo "$LAB lifted $(date -u +%T) rc=$RC" >> $L' EXIT
echo "$LAB set $(date -u +%T)" >> $L
while busy; do touch $H1 $H2; sleep 5; done
echo "$LAB start $(date -u +%T) load $(cut -d' ' -f1 /proc/loadavg)" >> $L
"$@"; RC=$?
