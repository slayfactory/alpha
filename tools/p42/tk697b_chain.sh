#!/bin/bash
# П-42 TK-697 (вторая поправка, набор t697b): цепочка единичек `btc_mv15` / `mk_btc_s60` после отбора (select уже выполнен и записан в запись поправки: level674b1-*.json, thr680q.json) — ftab → validate → cells → tables → ccheck → map.
# Как tk697_chain.sh (набор t697), но TK680_SET=t697b и корень /data/p42-tk697b. Один движок: запускать только внутри слота (замок /data/p42-tk674c/slot.lock держит tk697b_slot.sh).
#   TK674_BIN=/data/p42-tk688/bin/alpha-tk688-plain.c06f6674 TK674_GBASE=/data/p42-tk680n/res/gbase688.json bash tk697b_chain.sh L-<запись поправки>
set -euo pipefail
ENTRY=$1; PHASE=${2:-all}     # engine = ftab, validate, cells (под замком слота); post = tables, ccheck, map (после снятия замка, юнит с квотой); all = всё
PY=/opt/venv/bin/python
export TK674_ROOT=/data/p42-tk697b TK674_UNI=c539 TK674_REBASE=1 TK674_LEVEL=1 TK680_SET=t697b TK674_AUTHOR=TK-697 PYTHONUTF8=1
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
