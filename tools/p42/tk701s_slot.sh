#!/bin/bash
# П-42 TK-701 (набор t701): слот движка на границе партий tk674c — цепочка единичек `tfv60` / `pre_n5` / `oi_c1` (бинарь c06f6674, без ворот нового бинаря); под замком только PHASE=engine (ftab, validate, cells), post — после снятия замка.
# Замок и бюджет — как tk697b_slot.sh:
# /data/p42-tk674c/slot.lock берётся `mkdir` с опросом 2 с, снимается `rmdir` в trap; слот ≤ BUDGET с (по умолчанию 600, согласовано с thr_n8653xpt8j).
# Запуск (юнит): systemd-run --unit=tk701s-slot --collect -p Nice=5 -p CPUWeight=800 -p MemoryMax=40G bash tk701s_slot.sh L-<запись поправки>
set -uo pipefail
ENTRY=${1:?запись поправки L-N}
L=/data/p42-tk674c/slot.lock; G=/data/p42-tk701s; LOG=$G/log/slot.log; REF=/data/p42-tk688/bin/alpha-tk688-plain.c06f6674; BUDGET=${BUDGET:-600}
mkdir -p $G/log $G/res
echo "== $(date -u +%FT%TZ) polling $L, entry $ENTRY" >> $LOG
until mkdir $L 2>/dev/null; do sleep 2; done
TL=$(date +%s)
trap 'rmdir $L 2>/dev/null; echo "== $(date -u +%FT%TZ) SLOT-RELEASED held=$(( $(date +%s) - TL ))s" >> $LOG' EXIT
echo "== $(date -u +%FT%TZ) SLOT-TAKEN" >> $LOG
cd $G/code
env -u TK674_BIN -u TK674_ROOT TK674_BIN=$REF TK674_GBASE=/data/p42-tk680n/res/gbase688.json timeout $(( BUDGET - 20 )) bash tk701s_chain.sh $ENTRY engine; RC=$?
echo "== $(date -u +%FT%TZ) chain rc=$RC left=$(( BUDGET - ($(date +%s) - TL) ))s" >> $LOG
echo "SLOT-DONE" >> $LOG
rmdir $L 2>/dev/null; trap - EXIT; echo "== $(date -u +%FT%TZ) SLOT-RELEASED held=$(( $(date +%s) - TL ))s" >> $LOG
[ $RC -eq 0 ] && systemd-run --wait --collect --unit=tk701s-post -p Nice=10 -p CPUQuota=100% -p MemoryMax=20G -p WorkingDirectory=$G/code -E TK674_BIN=$REF -E TK674_GBASE=/data/p42-tk680n/res/gbase688.json bash tk701s_chain.sh $ENTRY post
