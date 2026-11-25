#!/bin/bash
# TK-706: цепочка опыта пучка на srv (юнит tk706-b4chain): ждёт сборку w1 (m2 + `Walk`), собирает мерный ws-measure4 (src-ws4: ws3-счётчики + `sim_bundle` + опыт), ставит окно HOLD b4 (b4win.sh). Журнал — bins/b4chain.log.
L=/root/perf706/bins/b4chain.log
until grep -q done-build-w1 /root/perf706/bins/build-w1.log 2>/dev/null; do sleep 15; done
grep -q "== w1 rc=0" /root/perf706/bins/build-w1.log || { echo "w1 build failed" >> $L; exit 1; }
echo "w1 ok $(date -u +%T)" >> $L
rm -f /root/perf706/bins/build-ws-measure4.log
SRC=/root/perf706/src-ws4 J=4 bash /root/perf706/buildm.sh ws-measure4
grep -q "== ws-measure4 rc=0" /root/perf706/bins/build-ws-measure4.log || { echo "ws-measure4 build failed" >> $L; exit 1; }
echo "ws-measure4 ok $(date -u +%T)" >> $L
[ -d /root/perf706/sc4-ws4-H ] || cp -a /root/perf706/sc4-m2-H /root/perf706/sc4-ws4-H
bash /root/perf706/holdrun2.sh b4 bash /root/perf706/b4win.sh
echo "window b4 done $(date -u +%T)" >> $L
