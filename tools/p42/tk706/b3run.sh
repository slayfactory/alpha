#!/bin/bash
# TK-706 (пучок, E1–E2 критика 25.11): окно HOLD: (1) H1 с мерным бинарём ws-measure3 (P42_B3=1: кандидаты, спрос, группы, ход непринятых → bench/b3-H1.json);
# (2) профиль `perf record` бинаря m2 на H1 (доли обвязки и хода: perf-m2-H1.txt). Запуск: holdrun.sh b3 bash b3run.sh
cd /root/perf706
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
R=/root/perf706/bench/results15.jsonl
[ -d sc4-ws3-H ] || cp -a sc4-m2-H sc4-ws3-H
rm -f bench/b3-H1.json
P42_B3=1 P42_B3_OUT=/root/perf706/bench/b3-H1.json python3 bench.py --bin /root/perf706/bins/ws-measure3 --sym-every 6 --cap-gb 6 --tag ws3-H1 --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-ws3-H >> $R 2>> bench/q15.err
python3 bench.py --bin /root/perf706/bins/m2 --sym-every 6 --cap-gb 6 --tag perf-m2-H1 --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-m2-H --perf /root/perf706/perf-m2-H1.data >> $R 2>> bench/q15.err
perf report -i perf-m2-H1.data --no-children --sort sym --stdio 2>/dev/null | grep -v "^#" | grep -v "^$" | head -60 > perf-m2-H1.txt
echo "done b3run" >> $R
