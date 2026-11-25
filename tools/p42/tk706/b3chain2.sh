#!/bin/bash
# TK-706: второй заход b3: после последнего замера проверки PGO (идёт гейт, он ЦП-чувствителен слабо) — свежий патч ws3, проверка компиляции, сборка ws-measure3, окно HOLD b3.
# Журнал — bins/b3chain.log.
L=/root/perf706/bins/b3chain.log
until grep -q hold-m3-wb-hit /root/perf706/bench/results13.jsonl 2>/dev/null; do sleep 15; done
echo "verify measurements done, start $(date -u +%T)" >> $L
cp /root/perf706/src-memo/src/breakout/tick.rs /root/perf706/src-memo/src/breakout/tick_cli.rs /root/perf706/src-ws3/src/breakout/ && python3 /root/perf706/ws3_patch.py >> $L 2>&1 || { echo "patch failed" >> $L; exit 1; }
bash /root/perf706/chk.sh /root/perf706/src-ws3 > /root/perf706/bins/chk3.log 2>&1
grep -q "^error" /root/perf706/bins/chk3.log && { echo "check failed" >> $L; exit 1; }
rm -f /root/perf706/bins/build-ws-measure3.log
SRC=/root/perf706/src-ws3 J=4 bash /root/perf706/buildm.sh ws-measure3
grep -q "== ws-measure3 rc=0" /root/perf706/bins/build-ws-measure3.log || { echo "build failed" >> $L; exit 1; }
echo "build ok $(date -u +%T)" >> $L
rm -rf /root/perf706/sc4-ws3-H
bash /root/perf706/holdrun2.sh b3 bash /root/perf706/b3run.sh
echo "window2 done $(date -u +%T)" >> $L
