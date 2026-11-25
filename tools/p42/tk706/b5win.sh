#!/bin/bash
# TK-706 (Walk v2: скаляры отдельно от кучи — w1 стоил +10 % инструкций; опыт пучка на нём): окно HOLD (holdrun2.sh b5 bash b5win.sh): (1) H1: m2 против w2, разогрев + 3 + 3 вперемешку, `perf stat`, хеш дерева = 92e20ecd72a6bef1;
# (2) опыт пучка: ws-measure5 (P42_B3=1, JSON `bench/b5-H1.json`, поле "bundle"); (3) гейт w2 по замороженному списку (VERIFY=1, 8 потоков). Результаты — bench/results17.jsonl, res/gate683fw2.json.
cd /root/perf706
R=/root/perf706/bench/results17.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
[ -d sc4-w2-H ] || cp -a sc4-m2-H sc4-w2-H
run(){ python3 bench.py --bin /root/perf706/bins/$2 --sym-every 6 --cap-gb 6 --tag $1 --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-$3-H --stat /root/perf706/bench/$1.csv >> $R 2>> bench/q17.err; }
run m2-warm5 m2 m2
for i in 1 2 3; do
  run m2b-hit-$i m2 m2
  run w2-hit-$i w2 w2
done
rm -f bench/b5-H1.json
P42_B3=1 P42_B3_OUT=/root/perf706/bench/b5-H1.json python3 bench.py --bin /root/perf706/bins/ws-measure5 --sym-every 6 --cap-gb 6 --tag ws5-H1 --variants $HV --every 1 --threads 7 --sig /root/perf706/sc4-ws5-H >> $R 2>> bench/q17.err
M=$(md5sum /root/perf706/bins/w2 | cut -c1-8)
B=/data/p42-tk683/bin/alpha-tk706walk2-plain.$M
install -m 755 /root/perf706/bins/w2 $B; echo "w2 $B $(md5sum $B | cut -d' ' -f1)" >> $R
THREADS=8 bash /root/perf706/gatef.sh list $B fw2 /data/p42-tk683/gate-frozen/g1.json
tail -3 /data/p42-tk683/log/gatefw2.log >> $R
echo done-b5win >> $R
