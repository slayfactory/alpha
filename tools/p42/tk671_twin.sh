#!/usr/bin/env bash
# П-42 §13.71 (TK-671), ворота G-pf (srv): двойники баз `L_base` / `S_base` / `D_S` — те же фильтры и выходы, но каждая строка торгуется независимо (reentry=2, без занятости и пауз).
# Реплика (tk671_pf.py) на сделках двойника должна дать сделки базы движка. Использование: tk671_twin.sh <окно> <каталог выхода> <потоки>
set -uo pipefail
WIN=$1; OUT=$2; THR=$3
BIN=${TK671_BIN:-/data/p42-tk671/bin/alpha-lx-sides}
W=/data/p42-tk621/$WIN
COINS=$(cat /data/p42-tk621/universe/symbols-all.txt)
STORE="--tick-store /data/p42-tk599/tick-store --tick-store-cap-gb 3 --tick-store-min-free-gb 5"
case $WIN in wa) T0=2026-06-17;; wb) T0=2026-07-25;; wc) T0=2026-09-01;; *) echo "окно?"; exit 2;; esac
IND="p=100000 c=0 m=1000000 s_glob=1000000 ww=0 busy=0 reentry=2"
LB="lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20 t24=12"
SB="lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05 t24=12"
mkdir -p "$OUT"
printf 'L_ind %s %s\nS_ind %s %s\nD_ind %s lx=1 tf=60 %s\n' "$LB" "$IND" "$SB" "$IND" "$SB" "$IND" > "$OUT/variants.txt"
S=$(date +%s)
P42_T0=$T0 $BIN breakout tick --cache $W/cache --trades $W/aggtrades --variants "$OUT/variants.txt" --symbols "$COINS" --out "$OUT/out" --threads "$THR" --cap-gb 4 $STORE > "$OUT/twin.log" 2>&1
echo "twin $WIN rc=$? sec=$(( $(date +%s) - S ))" | tee -a "$OUT/done.log"
