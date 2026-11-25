#!/bin/bash
# TK-706 (замечание критика c): глибц-аллокатор — пороги mmap/trim/top_pad через окружение (без пересборки), v1-lto на H1, повторы вперемешку
cd /root/perf706
R=/root/perf706/bench/results10.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
run(){ tag=$1; python3 bench.py --bin /root/perf706/bins/v1-lto --sym-every 6 --cap-gb 6 --tag $tag --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-v1-H >> $R 2>> bench/q10.err; }
for i in 1 2 3; do
  run v1-plain-$i
  MALLOC_MMAP_THRESHOLD_=33554432 MALLOC_TRIM_THRESHOLD_=8589934592 MALLOC_TOP_PAD_=268435456 run v1-mallocenv-$i
done
echo done-malloc >> $R
