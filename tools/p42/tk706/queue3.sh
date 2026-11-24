#!/bin/bash
# usage: queue3.sh <bin> <tagprefix> [full]
cd /root/perf706
BIN=$1; T=$2; MODE=${3:-lite}
B=/root/perf706/bench.py
LV=/data/p42-tk674c/cells/wc-S/pab9981688e.variants.txt
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
R=/root/perf706/bench/results3.jsonl
SC=/root/perf706/sc3-$T
run(){ python3 $B --bin $BIN --sym-every 6 --cap-gb 6 "$@" >> $R 2>> /root/perf706/bench/q3.err; }
run --tag $T-L-miss --variants $LV --every 64 --threads 7 --sig $SC-L
run --tag $T-L-hit-a --variants $LV --every 64 --threads 7 --sig $SC-L
run --tag $T-L-hit-b --variants $LV --every 64 --threads 7 --sig $SC-L
run --tag $T-H-miss --variants $HV --every 34 --threads 7 --sig $SC-H
run --tag $T-H-hit-a --variants $HV --every 34 --threads 7 --sig $SC-H
run --tag $T-H-hit-b --variants $HV --every 34 --threads 7 --sig $SC-H
if [ "$MODE" = full ]; then
run --tag $T-H-hit-t1 --variants $HV --every 34 --threads 1 --sig $SC-H
run --tag $T-H-hit-t4 --variants $HV --every 34 --threads 4 --sig $SC-H
run --tag $T-H-hit-cap2 --variants $HV --every 34 --threads 7 --cap-gb 2 --sig $SC-H
run --tag $T-H-hit-wide0 --variants $HV --every 34 --threads 7 --wide 0 --sig $SC-H
run --tag $T-L-hit-t1 --variants $LV --every 64 --threads 1 --sig $SC-L
fi
echo "done $T" >> $R
