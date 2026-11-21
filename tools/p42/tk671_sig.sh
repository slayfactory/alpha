#!/usr/bin/env bash
# П-42 §13.71 (TK-671), шаг sig (srv): дампы сигналов движка без фильтров по 12 источникам уровней, одна сторона (P42_SIDES), окно wa|wb|wc; кэш сигналов `--sig-cache` общий с шагом lab.
# Использование: tk671_sig.sh <окно> <side long|short> <каталог выхода> <потоки>
# Дамп с первых торговых суток сетки (14) до последних зачётных суток окна (`hi`): сигналы до `lo` нужны только для границ эпизодов (tk671_lvl.py оставляет строки в `lo…hi`).
set -uo pipefail
WIN=$1; SIDE=$2; OUT=$3; THR=$4
BIN=${TK671_BIN:-/data/p42-tk671/bin/alpha-lx-sides}
W=/data/p42-tk621/$WIN
COINS=$(cat /data/p42-tk621/universe/symbols-all.txt)
STORE="--tick-store /data/p42-tk599/tick-store --tick-store-cap-gb 3 --tick-store-min-free-gb 5"
case $WIN in wa) T0=2026-06-17; HI=69;; wb) T0=2026-07-25; HI=69;; wc) T0=2026-09-01; HI=73;; *) echo "окно?"; exit 2;; esac
LW=$(python3 -c "import datetime as d;print((7-d.date.fromisoformat('$T0').weekday())%7)")
BASE="lat=4 latx=4 natr_min=0 t1h=0 ew=5 ex=0"
if [ "$SIDE" = long ]; then EX="rf=0.2 rs=20"; else EX="tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05"; fi
mkdir -p "$OUT"
{
  for tf in 30 60 240; do for nb in 1 2 3; do echo "p${tf}n${nb} $BASE $EX tf=$tf n_bounce=$nb"; done; done
  echo "lxD $BASE $EX lx=1 tf=60"
  echo "lxW $BASE $EX lx=2 tf=60 lw=$LW"
  echo "lxP $BASE $EX lx=3 tf=60"
} > "$OUT/variants.txt"
S=$(date +%s)
P42_SIDES=$SIDE P42_T0=$T0 P42_SIGSX_ONLY=1 P42_SIGSX=14:$HI $BIN breakout tick --cache $W/cache --trades $W/aggtrades --variants "$OUT/variants.txt" --symbols "$COINS" --out "$OUT/dumps" \
  --threads "$THR" --cap-gb 4 --sig-cache "/data/p42-tk671/sigcache/$WIN" $STORE > "$OUT/sig.log" 2>&1
echo "sig $WIN $SIDE rc=$? sec=$(( $(date +%s) - S ))" | tee -a "$OUT/done.log"
