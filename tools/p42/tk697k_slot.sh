#!/bin/bash
# П-42 TK-697: слот движка на границе партий tk674c — клетки набора t697k (28 клеток, поправка PS-1002), бинарь 906c0999. Под замком — только `cells` (select, G-base, ftab, validate уже выполнены юнитом tk697k-pre);
# после снятия замка — `tables → ccheck → map` юнитом с квотой 25 % ЦП. Замок /data/p42-tk674c/slot.lock берётся `mkdir` с опросом 2 с, снимается `rmdir`; слот ≤ 10 мин (BUDGET); недосчитанные клетки
# досчитывает повторный запуск (пакеты клеток с отметкой «готово», `cells` их пропускает).
# Запуск (юнит): systemd-run --unit=tk697k-slot --collect -p Nice=5 -p CPUWeight=800 -p MemoryMax=40G -p WorkingDirectory=/data/p42-tk697k/code bash tk697k_slot.sh L-<запись PS-1002>
set -uo pipefail
ENTRY=${1:?запись поправки}
L=/data/p42-tk674c/slot.lock; LOG=/data/p42-tk697k/log/slot.log; BUDGET=${BUDGET:-600}; CH=/data/p42-tk697k/code
echo "== $(date -u +%FT%TZ) polling $L, entry $ENTRY" >> $LOG
until mkdir $L 2>/dev/null; do sleep 2; done
TL=$(date +%s); HELD=1
release() { if [ "$HELD" = 1 ]; then rmdir $L 2>/dev/null; HELD=0; echo "== $(date -u +%FT%TZ) SLOT-RELEASED held=$(( $(date +%s) - TL ))s" >> $LOG; fi; }
trap release EXIT
echo "== $(date -u +%FT%TZ) SLOT-TAKEN" >> $LOG
cd $CH; timeout $BUDGET bash tk697k_chain.sh $ENTRY engine; R=$?
echo "== $(date -u +%FT%TZ) cells rc=$R (124 = бюджет $BUDGET с)" >> $LOG
release
if [ $R -eq 0 ]; then
  systemd-run --unit=tk697k-post --collect -p Nice=19 -p CPUQuota=25% -p MemoryMax=12G -p WorkingDirectory=$CH bash -c "bash tk697k_chain.sh $ENTRY post; echo POST-DONE \$(date -u +%FT%TZ) >> $LOG" >> $LOG 2>&1
fi
echo "SLOT-DONE rc=$R" >> $LOG
