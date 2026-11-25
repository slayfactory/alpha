#!/bin/bash
# TK-706 (рефакторинг `Walk` + опыт пучка, H1–H2 критика): окно HOLD (holdrun2.sh b4 bash b4win.sh): (1) H1: m2 против w1 (m2 + `Walk::step`), разогрев + 3 + 3 вперемешку, `perf stat`, хеш дерева = 92e20ecd72a6bef1;
# (2) опыт пучка: ws-measure4 (P42_B3=1, JSON `bench/b4-H1.json`, поле "bundle"); (3) гейт w1 по замороженному списку (VERIFY=1, 8 потоков). Результаты — bench/results16.jsonl, res/gate683fw1.json.
cd /root/perf706
R=/root/perf706/bench/results16.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
[ -d sc4-w1-H ] || cp -a sc4-m2-H sc4-w1-H
run(){ python3 bench.py --bin /root/perf706/bins/$2 --sym-every 6 --cap-gb 6 --tag $1 --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-$3-H --stat /root/perf706/bench/$1.csv >> $R 2>> bench/q16.err; }
run m2-warm m2 m2
for i in 1 2 3; do
  run m2-hit-$i m2 m2
  run w1-hit-$i w1 w1
done
rm -f bench/b4-H1.json
P42_B3=1 P42_B3_OUT=/root/perf706/bench/b4-H1.json python3 bench.py --bin /root/perf706/bins/ws-measure4 --sym-every 6 --cap-gb 6 --tag ws4-H1 --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-ws4-H >> $R 2>> bench/q16.err
M=$(md5sum /root/perf706/bins/w1 | cut -c1-8)
B=/data/p42-tk683/bin/alpha-tk706walk-plain.$M
install -m 755 /root/perf706/bins/w1 $B; echo "w1 $B $(md5sum $B | cut -d' ' -f1)" >> $R
THREADS=8 bash /root/perf706/gatef.sh list $B fw1 /data/p42-tk683/gate-frozen/g1.json
tail -3 /data/p42-tk683/log/gatefw1.log >> $R
echo done-b4win >> $R
