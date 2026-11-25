#!/bin/bash
# TK-706: цепочка опыта пучка на srv (юнит tk706-b5chain): ждёт сборку w2 (m2 + `Walk`), собирает мерный ws-measure5 (src-ws5: ws3-счётчики + `sim_bundle` + опыт), ставит окно HOLD b5 (b5win.sh). Журнал — bins/b5chain.log.
L=/root/perf706/bins/b5chain.log
until grep -q done-build-w2 /root/perf706/bins/build-w2.log 2>/dev/null; do sleep 15; done
grep -q "== w2 rc=0" /root/perf706/bins/build-w2.log || { echo "w2 build failed" >> $L; exit 1; }
echo "w2 ok $(date -u +%T)" >> $L
rm -f /root/perf706/bins/build-ws-measure5.log
SRC=/root/perf706/src-ws5 J=4 bash /root/perf706/buildm.sh ws-measure5
grep -q "== ws-measure5 rc=0" /root/perf706/bins/build-ws-measure5.log || { echo "ws-measure5 build failed" >> $L; exit 1; }
echo "ws-measure5 ok $(date -u +%T)" >> $L
[ -d /root/perf706/sc4-ws5-H ] || cp -a /root/perf706/sc4-m2-H /root/perf706/sc4-ws5-H
bash /root/perf706/holdrun2.sh b5 bash /root/perf706/b5win.sh
echo "window b5 done $(date -u +%T)" >> $L
