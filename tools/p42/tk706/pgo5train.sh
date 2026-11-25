#!/bin/bash
# TK-706: обучение PGO на бинаре `p5-gen` (код m2): H1 (промах + попадание, 7 потоков) + малые фикстуры (queue3 lite). Запускать внутри окна HOLD: holdrun.sh pgo5-train bash pgo5train.sh.
cd /root/perf706
rm -rf /root/perf706/pgo5; mkdir -p /root/perf706/pgo5
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
R=/root/perf706/bench/results13.jsonl
export LLVM_PROFILE_FILE=/root/perf706/pgo5/%p-%m.profraw
rm -rf sc4-p5gen-H sc3-p5gen-H sc3-p5gen-L
for t in miss hit; do
  python3 bench.py --bin /root/perf706/bins/p5-gen --sym-every 6 --cap-gb 6 --tag p5gen-H1-$t --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-p5gen-H >> $R 2>> bench/q13.err
done
./queue3.sh /root/perf706/bins/p5-gen p5gen lite
unset LLVM_PROFILE_FILE
echo "train-done $(ls /root/perf706/pgo5/*.profraw | wc -l) profraw" >> $R
