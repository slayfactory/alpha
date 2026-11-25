#!/bin/bash
# TK-706 (пучок v2: лента кусками по 256 сделок, каждый член проходит кусок своим циклом `walk_step`): окно HOLD (holdrun2.sh b6 bash b6win.sh): опыт на H1, мерный ws-measure6, два прогона (JSON bench/b6-H1-{1,2}.json, поле "bundle").
cd /root/perf706
R=/root/perf706/bench/results18.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
for i in 1 2; do
  rm -f bench/b6-H1-$i.json
  P42_B3=1 P42_B3_OUT=/root/perf706/bench/b6-H1-$i.json python3 bench.py --bin /root/perf706/bins/ws-measure6 --sym-every 6 --cap-gb 6 --tag ws6-H1-$i --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-ws6-H >> $R 2>> bench/q18.err
done
echo done-b6win >> $R
