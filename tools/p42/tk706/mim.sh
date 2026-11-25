#!/bin/bash
# TK-706 (c): v3-mimalloc против v1-lto на H1 (попадание), 3 + 3 прогона вперемешку, у каждого `perf stat` (циклы, инструкции, страничные ошибки). Запуск на srv.
cd /root/perf706
R=/root/perf706/bench/results12.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
run(){ t=$1; b=$2; c=$3; python3 bench.py --bin /root/perf706/bins/$b --sym-every 6 --cap-gb 6 --tag mim-$t --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-$c-H --stat /root/perf706/bench/mim-$t.csv >> $R 2>> bench/q12.err; }
for i in 1 2 3; do
  run v1-$i v1-lto v1
  run v3-$i v3-mimalloc v3
done
echo done-mim >> $R
