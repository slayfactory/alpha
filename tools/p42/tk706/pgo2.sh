#!/bin/bash
# TK-706: hold-out — тот же веер H1 на окне wb (PGO обучался на wc): base / v1-lto / v4-use, промах + повтор
cd /root/perf706
R9=/root/perf706/bench/results9.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
while ! grep -q done-pgo bench/results8.jsonl 2>/dev/null; do sleep 20; done
for pair in base:/data/p42-tk683/bin/alpha-tk683l-plain.562f6899 v1:/root/perf706/bins/v1-lto v4:/root/perf706/bins/v4-use; do
  T=${pair%%:*}; BIN=${pair#*:}
  for t in miss hit; do
    python3 bench.py --window wb --bin $BIN --sym-every 6 --cap-gb 6 --tag hold-$T-wb-$t --variants $HV --every 1 --threads 7 --sig /root/perf706/sc9-$T-wb >> $R9 2>> bench/q9.err
  done
done
echo done-hold >> $R9
