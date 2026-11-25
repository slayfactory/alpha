#!/bin/bash
# TK-706: цепочка после сборки бинаря памяти <имя>: ждёт `done-build-<имя>`, ставит бинарь в /data/p42-tk683/bin/alpha-tk706memo-plain.<md5>, H1 (queue4.sh, хеш дерева = 92e20ecd72a6bef1),
# гейт (gatem.sh, VERIFY=1), при PASS — создаёт /root/perf706/a3m.go (старт A3m). Журнал — /root/perf706/bins/chain-<имя>.log.
N=${1:?имя}; L=/root/perf706/bins/chain-$N.log
until grep -q "done-build-$N" /root/perf706/bins/build-$N.log 2>/dev/null; do sleep 20; done
grep -q "== $N rc=0" /root/perf706/bins/build-$N.log || { echo "build failed" >> $L; exit 1; }
M=$(md5sum /root/perf706/bins/$N | cut -c1-8)
B=/data/p42-tk683/bin/alpha-tk706memo-plain.$M
install -m 755 /root/perf706/bins/$N $B; echo "bin $B $(md5sum $B | cut -d' ' -f1)" >> $L
bash /root/perf706/queue4.sh /root/perf706/bins/$N $N 1
grep "\"$N-H1" /root/perf706/bench/results4.jsonl | python3 -c "import sys,json
for l in sys.stdin:
    d=json.loads(l); print(d['tag'],d['tree'],d['nfiles'],d['memo'])" >> $L
bash /root/perf706/gatem.sh $B gm$N
tail -3 /data/p42-tk683/log/gategm$N.log >> $L
if grep -q "^rc=0" /data/p42-tk683/log/gategm$N.log && grep -q "различий 0" /data/p42-tk683/log/gategm$N.log; then touch /root/perf706/a3m.go; echo "GATE PASS -> a3m.go" >> $L; else echo "GATE FAIL" >> $L; fi
