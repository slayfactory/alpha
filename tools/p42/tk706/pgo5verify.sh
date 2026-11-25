#!/bin/bash
# TK-706: проверка PGO-бинаря `m3` внутри окна HOLD (holdrun.sh pgo5-verify bash pgo5verify.sh): H1 (хеш дерева = 92e20ecd72a6bef1), A3 (m2 против m3: полный тяжёлый вызов, промах + попадание),
# контроль на другом окне wb (hold-out; профиль снят на H1), затем гейт 549 клеток (P42_MEMO_VERIFY=1, 8 потоков). Результаты — bench/results13.jsonl, res/gate683gmm3.json.
cd /root/perf706
R=/root/perf706/bench/results13.jsonl
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
M=$(md5sum /root/perf706/bins/m3 | cut -c1-8)
B=/data/p42-tk683/bin/alpha-tk706pgo-plain.$M
install -m 755 /root/perf706/bins/m3 $B; echo "m3 $B $(md5sum $B | cut -d' ' -f1)" >> $R
rm -rf sc4-m3-H
bash queue4.sh /root/perf706/bins/m3 m3 1 >> /dev/null 2>&1
H=$(grep '"m3-H1-hit-1"' bench/results4.jsonl | tail -1 | python3 -c "import sys,json;print(json.loads(sys.stdin.read())['tree'])")
echo "m3 H1 tree $H" >> $R
[ "$H" = 92e20ecd72a6bef1 ] || { echo "m3 H1 TREE MISMATCH" >> $R; exit 1; }
for T in m2 m3; do
  rm -rf sc13-$T
  python3 bench.py --bin /root/perf706/bins/$T --cap-gb 24 --wide 200 --threads 7 --tag a3p-$T-miss --variants $HV --every 1 --sig /root/perf706/sc13-$T >> $R 2>> bench/q13.err
  python3 bench.py --bin /root/perf706/bins/$T --cap-gb 24 --wide 200 --threads 7 --tag a3p-$T-hit --variants $HV --every 1 --sig /root/perf706/sc13-$T >> $R 2>> bench/q13.err
done
for T in m2 m3; do
  for t in miss hit; do
    python3 bench.py --window wb --bin /root/perf706/bins/$T --sym-every 6 --cap-gb 6 --tag hold-$T-wb-$t --variants $HV --every 1 --threads 7 --sig /root/perf706/sc13-$T-wb >> $R 2>> bench/q13.err
  done
done
THREADS=8 bash /root/perf706/gatem.sh $B gm3
tail -3 /data/p42-tk683/log/gategm3.log >> $R
echo done-verify >> $R
