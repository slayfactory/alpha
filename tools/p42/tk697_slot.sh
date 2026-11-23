#!/bin/bash
# П-42 TK-697: один слот движка на границе партий tk674c (договорённость с thr_n8653xpt8j, 24.11): ворота «байт в байт» нового бинаря (№ 9 `tn`, № 1 `lx = 4`; ключи выключены = вывод бинаря l c06f6674) +
# дымовой прогон новых ключей + цепочка единичек t697 (603 клетки, бинарь c06f6674). Замок /data/p42-tk674c/slot.lock берётся `mkdir` с опросом 2 с, снимается `rmdir` в trap; слот ≤ 22 мин.
# Запуск (юнит, только когда бинарь и ворота готовы): systemd-run --unit=tk697-slot --collect -p Nice=5 -p CPUWeight=800 -p MemoryMax=40G bash tk697_slot.sh <новый бинарь> [L-<запись PS-1000>]
set -uo pipefail
BIN=${1:?новый бинарь}; ENTRY=${2:-}
L=/data/p42-tk674c/slot.lock; G=/data/p42-tk697g; LOG=$G/log/slot.log; REF=/data/p42-tk688/bin/alpha-tk688-plain.c06f6674; BUDGET=1320
PY=/opt/venv/bin/python
mkdir -p $G/log $G/res $G/gate
echo "== $(date -u +%FT%TZ) polling $L, bin $BIN" >> $LOG
until mkdir $L 2>/dev/null; do sleep 2; done
TL=$(date +%s)
trap 'rmdir $L 2>/dev/null; echo "== $(date -u +%FT%TZ) SLOT-RELEASED held=$(( $(date +%s) - TL ))s" >> $LOG' EXIT
echo "== $(date -u +%FT%TZ) SLOT-TAKEN" >> $LOG
left() { echo $(( BUDGET - ($(date +%s) - TL) )); }
export PYTHONUTF8=1 TK674_UNI=c539 TK674_ROOT=$G TK674_BIN=$BIN TK674_CAP_GB=2
cd /data/p42-tk697g/code
timeout $(( $(left) - 420 )) $PY tk697_gate_d.py --ref $REF >> $G/log/gate.log 2>&1; RG=$?
echo "== $(date -u +%FT%TZ) gate rc=$RG left=$(left)s" >> $LOG
if [ $RG -ne 0 ]; then echo "== STOP: ворота не пройдены (rc=$RG), цепочка не запускается" >> $LOG; exit $RG; fi
timeout $(( $(left) - 300 )) $PY tk697_smoke_d.py >> $G/log/smoke.log 2>&1; RS=$?
echo "== $(date -u +%FT%TZ) smoke rc=$RS left=$(left)s" >> $LOG
if [ -n "$ENTRY" ]; then
  cd /data/p42-tk697x/code
  env -u TK674_BIN -u TK674_ROOT -u TK674_CAP_GB TK674_BIN=$REF TK674_GBASE=/data/p42-tk688/res/gate688.json timeout $(( $(left) - 20 )) bash tk697_chain.sh $ENTRY; RC=$?
  echo "== $(date -u +%FT%TZ) chain rc=$RC left=$(left)s" >> $LOG
fi
echo "SLOT-DONE" >> $LOG
