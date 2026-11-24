#!/bin/bash
# usage: queue4.sh <bin> <tagprefix> [reps]   H1 = all 1705 heavy variants x 90 coins (every 6th symbol)
cd /root/perf706
BIN=$1; T=$2; N=${3:-2}
B=/root/perf706/bench.py
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
R=/root/perf706/bench/results4.jsonl
SC=/root/perf706/sc4-$T
run(){ python3 $B --bin $BIN --sym-every 6 --cap-gb 6 "$@" >> $R 2>> /root/perf706/bench/q4.err; }
run --tag $T-H1-miss --variants $HV --every 1 --threads 7 --sig $SC-H
for i in $(seq 1 $N); do run --tag $T-H1-hit-$i --variants $HV --every 1 --threads 7 --sig $SC-H; done
echo "done $T" >> $R
