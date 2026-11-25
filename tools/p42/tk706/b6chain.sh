#!/bin/bash
# TK-706: сборка ws-measure6 (src-ws6: ws3-счётчики + пучок кусками) и окно HOLD b6 (b6win.sh). Журнал — bins/b6chain.log.
L=/root/perf706/bins/b6chain.log
rm -f /root/perf706/bins/build-ws-measure6.log
SRC=/root/perf706/src-ws6 J=4 bash /root/perf706/buildm.sh ws-measure6
grep -q "== ws-measure6 rc=0" /root/perf706/bins/build-ws-measure6.log || { echo "ws-measure6 build failed" >> $L; exit 1; }
echo "ws-measure6 ok $(date -u +%T)" >> $L
[ -d /root/perf706/sc4-ws6-H ] || cp -a /root/perf706/sc4-m2-H /root/perf706/sc4-ws6-H
bash /root/perf706/holdrun2.sh b6 bash /root/perf706/b6win.sh
echo "window b6 done $(date -u +%T)" >> $L
