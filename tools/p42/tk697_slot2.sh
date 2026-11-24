#!/bin/bash
# П-42 TK-697: второй слот движка на границе партий tk674c — клетки двух наборов подряд под одним замком: t697 (603 клетки, запись PS-1000 / L-389) и t697b (40 клеток, PS-1001 / L-393), бинарь c06f6674
# (ворота бинаря №9 / №1 пройдены в первом слоте: gate697d.json). Под замком — только движок (ftab → validate → cells), после снятия — tables → ccheck → map юнитом с квотой (как tk674c-tab-rNN).
# Замок /data/p42-tk674c/slot.lock берётся `mkdir` с опросом 2 с, снимается `rmdir`; слот ≤ 22 мин.
# Запуск (юнит): systemd-run --unit=tk697-slot2 --collect -p Nice=5 -p CPUWeight=800 -p MemoryMax=40G bash tk697_slot2.sh L-389 L-393
set -uo pipefail
E1=${1:?запись t697}; E2=${2:?запись t697b}
L=/data/p42-tk674c/slot.lock; LOG=/data/p42-tk697g/log/slot2.log; BUDGET=1320; CH=/data/p42-tk697x/code; CHB=/data/p42-tk697b/code
echo "== $(date -u +%FT%TZ) polling $L, entries $E1 $E2" >> $LOG
until mkdir $L 2>/dev/null; do sleep 2; done
TL=$(date +%s); HELD=1
release() { if [ "$HELD" = 1 ]; then rmdir $L 2>/dev/null; HELD=0; echo "== $(date -u +%FT%TZ) SLOT-RELEASED held=$(( $(date +%s) - TL ))s" >> $LOG; fi; }
trap release EXIT
echo "== $(date -u +%FT%TZ) SLOT-TAKEN" >> $LOG
left() { echo $(( BUDGET - ($(date +%s) - TL) )); }
cd $CH;  timeout $(( $(left) - 200 )) bash tk697_chain.sh $E1 engine;  R1=$?
echo "== $(date -u +%FT%TZ) t697 engine rc=$R1 left=$(left)s" >> $LOG
cd $CHB; timeout $(( $(left) - 20 )) bash tk697b_chain.sh $E2 engine; R2=$?
echo "== $(date -u +%FT%TZ) t697b engine rc=$R2 left=$(left)s" >> $LOG
release
if [ $R1 -eq 0 ] || [ $R2 -eq 0 ]; then
  systemd-run --unit=tk697-post --collect -p Nice=19 -p CPUQuota=200% -p MemoryMax=12G bash -c "[ $R1 -ne 0 ] || (cd $CH && bash tk697_chain.sh $E1 post); [ $R2 -ne 0 ] || (cd $CHB && bash tk697b_chain.sh $E2 post); echo POST-DONE \$(date -u +%FT%TZ) >> $LOG" >> $LOG 2>&1
fi
echo "SLOT-DONE r1=$R1 r2=$R2" >> $LOG
