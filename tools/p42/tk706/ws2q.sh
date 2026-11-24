#!/bin/bash
# TK-706: ws-measure2 на малых фикстурах (G по классам вызовов): L1 (лёгкий), H-малая (средний). Запуск на srv.
cd /root/perf706
B=/root/perf706/bins/ws-measure2
LV=/data/p42-tk674c/cells/wc-S/pab9981688e.variants.txt
HV=/data/p42-tk674c/cells/wc-S/p5cd4933c8e.variants.txt
python3 bench.py --bin $B --sym-every 6 --cap-gb 6 --tag ws2-L --variants $LV --every 64 --threads 7 --sig /root/perf706/sc-ws2L >> bench/results13.jsonl 2>> bench/q13.err
python3 bench.py --bin $B --sym-every 6 --cap-gb 6 --tag ws2-Hs --variants $HV --every 34 --threads 7 --sig /root/perf706/sc-ws2Hs >> bench/results13.jsonl 2>> bench/q13.err
echo done-ws2q >> bench/results13.jsonl
