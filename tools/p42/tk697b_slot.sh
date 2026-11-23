#!/bin/bash
# П-42 TK-697 (вторая поправка, набор t697b): слот движка на границе партий tk674c — только цепочка единичек `btc_mv15` / `mk_btc_s60` (40 клеток, бинарь c06f6674, без ворот нового бинаря). Замок и бюджет — как tk697_slot.sh:
# /data/p42-tk674c/slot.lock берётся `mkdir` с опросом 2 с, снимается `rmdir` в trap; слот ≤ 22 мин.
# Запуск (юнит): systemd-run --unit=tk697b-slot --collect -p Nice=5 -p CPUWeight=800 -p MemoryMax=40G bash tk697b_slot.sh L-<запись поправки>
set -uo pipefail
ENTRY=${1:?запись поправки L-N}
L=/data/p42-tk674c/slot.lock; G=/data/p42-tk697b; LOG=$G/log/slot.log; REF=/data/p42-tk688/bin/alpha-tk688-plain.c06f6674; BUDGET=1320
mkdir -p $G/log $G/res
echo "== $(date -u +%FT%TZ) polling $L, entry $ENTRY" >> $LOG
until mkdir $L 2>/dev/null; do sleep 2; done
TL=$(date +%s)
trap 'rmdir $L 2>/dev/null; echo "== $(date -u +%FT%TZ) SLOT-RELEASED held=$(( $(date +%s) - TL ))s" >> $LOG' EXIT
echo "== $(date -u +%FT%TZ) SLOT-TAKEN" >> $LOG
cd $G/code
env -u TK674_BIN -u TK674_ROOT TK674_BIN=$REF TK674_GBASE=/data/p42-tk688/res/gate688.json timeout $(( BUDGET - 20 )) bash tk697b_chain.sh $ENTRY; RC=$?
echo "== $(date -u +%FT%TZ) chain rc=$RC left=$(( BUDGET - ($(date +%s) - TL) ))s" >> $LOG
echo "SLOT-DONE" >> $LOG
