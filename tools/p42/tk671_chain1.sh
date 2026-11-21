#!/usr/bin/env bash
# П-42 §13.71 (TK-671), цепочка 1 (srv): lvl (эпизоды + добавка строк базы + ftab) → lab (метки движком) по всем (окно, сторона). Ждёт готовых дампов sig и двойников twin.
# Использование: tk671_chain1.sh <параллельных прогонов lab>   (каждый прогон — 1 поток движка)
set -uo pipefail
PAR=${1:-5}; R=/data/p42-tk671/prod; C=/data/p42-tk671/code/p; PY=/data/tk592/venv/bin/python
SRCS="p30n1 p60n1 p30n2 lxP lxD p30n3 p240n1 p60n2 p60n3 p240n2 lxW p240n3"
for W in wa wb wc; do for S in long short; do
  until [ -e $R/sig-$W-$S/done.log ] && grep -q "rc=0" $R/sig-$W-$S/done.log && [ -e /data/p42-tk671/gate-pf/$W/done.log ]; do sleep 10; done
  T0=$(date +%s)
  PYTHONUTF8=1 $PY $C/tk671_lvl.py build --side $S --win $W --dumps $R/sig-$W-$S/dumps --twin /data/p42-tk671/gate-pf/$W/out --out $R/lvl-$W-$S > $R/lvl-$W-$S.log 2>&1
  echo "lvl $W $S rc=$? sec=$(( $(date +%s) - T0 ))" | tee -a $R/chain1.log
done; done
for W in wa wb wc; do for S in long short; do
  T0=$(date +%s)
  printf '%s\n' $SRCS | xargs -P $PAR -I{} bash $C/tk671_lab.sh $W $S $R/lvl-$W-$S $R/lab-$W-$S 1 {}
  echo "lab $W $S sec=$(( $(date +%s) - T0 ))" | tee -a $R/chain1.log
done; done
echo "chain1 done" | tee -a $R/chain1.log
