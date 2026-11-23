#!/bin/bash
# П-42 TK-697 C: цепочка единичек набора t697 после отбора (select уже выполнен и записан в PS-1000 / L-389: level674b1-*.json, thr680p.json) — ftab → validate → cells → tables → ccheck → map.
# Как tk680_chain.sh без стадии select (повтор отбора съедает слот). Один движок: запускать только внутри слота (замок /data/p42-tk674c/slot.lock держит tk697_slot.sh).
#   TK674_BIN=/data/p42-tk688/bin/alpha-tk688-plain.c06f6674 TK674_GBASE=/data/p42-tk680n/res/gbase688.json bash tk697_chain.sh L-<запись PS-1000>
set -euo pipefail
ENTRY=$1; PHASE=${2:-all}     # engine = ftab, validate, cells (под замком слота); post = tables, ccheck, map (после снятия замка, юнит с квотой); all = всё
PY=/opt/venv/bin/python
export TK674_ROOT=/data/p42-tk697x TK674_UNI=c539 TK674_REBASE=1 TK674_LEVEL=1 TK680_SET=t697 TK674_AUTHOR=TK-697 PYTHONUTF8=1
export TK674_BIN=${TK674_BIN:-/data/p42-tk688/bin/alpha-tk688-plain.c06f6674} TK674_GBASE=${TK674_GBASE:-/data/p42-tk680n/res/gbase688.json}
mkdir -p $TK674_ROOT/res $TK674_ROOT/log
LOG=$TK674_ROOT/log/chain.log
stage() { echo "== $(date -u +%FT%TZ) $*" >> "$LOG"; "$PY" tk674_run.py "$@" >> "$LOG" 2>&1; }
if [ "$PHASE" = all ] || [ "$PHASE" = engine ]; then
  stage ftab
  stage validate
  stage cells                                             # стороны по очереди, один движок
fi
if [ "$PHASE" = all ] || [ "$PHASE" = post ]; then
  stage tables --entry "$ENTRY"
  stage ccheck
  echo "== $(date -u +%FT%TZ) map" >> "$LOG"; "$PY" tk680_singles.py map >> "$LOG" 2>&1
  echo "== $(date -u +%FT%TZ) chain готово" >> "$LOG"
fi
