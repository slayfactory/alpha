#!/bin/bash
# TK-706: цепочка PGO: ждёт `done-gen` → обучение в окне HOLD → сборка use (без HOLD) → проверка в окне HOLD. Журнал — bins/pgo5chain.log.
L=/root/perf706/bins/pgo5chain.log
until grep -q done-gen /root/perf706/bins/pgo5.log 2>/dev/null; do sleep 20; done
grep -q "== p5-gen rc=0" /root/perf706/bins/pgo5.log || { echo "gen failed" >> $L; exit 1; }
echo "gen ok $(date -u +%T)" >> $L
bash /root/perf706/holdrun.sh pgo5-train bash /root/perf706/pgo5train.sh
echo "train done $(date -u +%T)" >> $L
J=4 bash /root/perf706/pgo5.sh use
grep -q "== m3 rc=0" /root/perf706/bins/pgo5.log || { echo "use failed" >> $L; exit 1; }
echo "use ok $(date -u +%T)" >> $L
bash /root/perf706/holdrun.sh pgo5-verify bash /root/perf706/pgo5verify.sh
echo "verify done $(date -u +%T)" >> $L
