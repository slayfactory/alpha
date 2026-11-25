#!/bin/bash
# TK-706 (a′): v1-unwind (LTO без panic=abort) против v1-lto (с abort) на H1 (попадание), 3 + 3 прогона вперемешку под `perf stat`; первый прогон v1-unwind — промах (заполняет кэш сигналов). Ждёт конца tk706-build5. Запуск на srv.
cd /root/perf706
R=/root/perf706/bench/results14.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
while ! grep -q done-build5 /root/perf706/bins/build5.log; do sleep 20; done
grep -q "== v1-unwind rc=0" /root/perf706/bins/build5.log || { echo "v1-unwind build failed" >> $R; exit 1; }
run(){ t=$1; b=$2; c=$3; python3 bench.py --bin /root/perf706/bins/$b --sym-every 6 --cap-gb 6 --tag unw-$t --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-$c-H --stat /root/perf706/bench/unw-$t.csv >> $R 2>> bench/q14.err; }
rm -rf /root/perf706/sc4-v1u-H
run v1u-miss v1-unwind v1u
for i in 1 2 3; do
  run v1-$i v1-lto v1
  run v1u-$i v1-unwind v1u
done
echo done-unwind >> $R
