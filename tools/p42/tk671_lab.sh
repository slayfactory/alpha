#!/usr/bin/env bash
# П-42 §13.71 (TK-671), шаг lab (srv): метки строк — сделки движка по `ftab`: каждая строка торгуется независимо (reentry=2, busy=0, без портфеля), выходы базы стороны.
# Использование: tk671_lab.sh <окно wa|wb|wc> <side long|short> <каталог lvl (ftab-<источник>.csv)> <каталог выхода> <потоки> <источник> [<источник>…]
# Один прогон движка на источник (свой `--ftab`), `--sig-cache` — общий для окна; память прогона ограничена `--cap-gb`.
set -uo pipefail
WIN=$1; SIDE=$2; LVL=$3; OUT=$4; THR=$5; shift 5
BIN=${TK671_BIN:-/data/p42-tk668/bin/alpha-lx}
W=/data/p42-tk621/$WIN
COINS=$(cat /data/p42-tk621/universe/symbols-all.txt)
STORE="--tick-store /data/p42-tk599/tick-store --tick-store-cap-gb 3 --tick-store-min-free-gb 5"
case $WIN in wa) T0=2026-06-17;; wb) T0=2026-07-25;; wc) T0=2026-09-01;; *) echo "окно?"; exit 2;; esac
LW=$(python3 -c "import datetime as d;print((7-d.date.fromisoformat('$T0').weekday())%7)")
BASE="lat=4 latx=4 natr_min=0 t1h=0 ew=5 ex=0"
if [ "$SIDE" = long ]; then EX="rf=0.2 rs=20"; else EX="tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05"; fi
SIG="p=100000 c=0 m=1000000 s_glob=1000000 ww=0 busy=0 reentry=2 fa=ROW fo=lo fv=0.5"
mkdir -p "$OUT"
for SRC in "$@"; do
  case $SRC in
    p*n*) TF=${SRC#p}; TF=${TF%%n*}; NB=${SRC##*n}; SK="tf=$TF n_bounce=$NB";;
    lxD) SK="lx=1 tf=60";; lxW) SK="lx=2 tf=60 lw=$LW";; lxP) SK="lx=3 tf=60";;
    *) echo "источник?"; exit 2;;
  esac
  echo "lab_${SRC} $BASE $EX $SK $SIG" > "$OUT/variants-$SRC.txt"
  S=$(date +%s)
  P42_T0=$T0 $BIN breakout tick --cache $W/cache --trades $W/aggtrades --variants "$OUT/variants-$SRC.txt" --symbols "$COINS" --out "$OUT/$SRC" \
    --threads "$THR" --cap-gb 4 --ftab "$LVL/ftab-$SRC.csv" --sig-cache "$OUT/sigcache" $STORE > "$OUT/$SRC.log" 2>&1
  echo "$SRC rc=$? sec=$(( $(date +%s) - S ))" | tee -a "$OUT/done.log"
done
