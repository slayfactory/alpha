#!/bin/bash
# usage: statq.sh <tag>:<bin> ...  (H1 hit with perf stat; needs sc4-<tag>-H populated)
cd /root/perf706
for pair in "$@"; do T=${pair%%:*}; BIN=${pair#*:}
  python3 bench.py --bin $BIN --sym-every 6 --cap-gb 6 --tag stat-$T-H1 --variants /data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt --every 1 --threads 7 --sig /root/perf706/sc4-$T-H --stat /root/perf706/stat-$T-H1.csv >> bench/results6.jsonl 2>> bench/q6.err
done
echo done-statq >> bench/results6.jsonl
