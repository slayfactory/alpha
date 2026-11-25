#!/bin/bash
# П-42 TK-674: один пакет уровня карты на srv — [ftab] → validate → cells → tables → ccheck → map. Запуск юнитом:
#   systemd-run --unit=tk674b-chain --collect -p Nice=19 -p MemoryMax=40G -p LimitCORE=0 -p OOMScoreAdjust=1000 -p WorkingDirectory=/data/p42-tk674/code -E TK674_REBASE=1 [-E TK674_LEVEL=3] bash tk674_chain.sh L-<запись результата>
# Каждая стадия — отдельный процесс; клетки, найденные в БД летописи, не пересчитываются. Журнал — log/chain<суффикс>.log.
set -euo pipefail
ENTRY=$1
PY=/opt/venv/bin/python
LOG=/data/p42-tk674/log/chain${TK674_REBASE:+b}${TK674_SPLIT:+s}${TK674_LEVEL:-}.log
stage() { echo "== $(date -u +%FT%TZ) $*" >> "$LOG"; "$PY" tk674_run.py "$@" >> "$LOG" 2>&1; }
if [ -n "${TK674_BIN:-}" ]; then stage spot; fi           # новый бинарь (ось split): выборочная проверка до счёта
if [ "${TK674_LEVEL:-2}" -gt 2 ]; then stage ftab; fi
stage validate
if [ "${TK674_LEVEL:-2}" -gt 2 ]; then                  # уровни: стороны двумя процессами (движок по 2 потока)
  echo "== $(date -u +%FT%TZ) cells L | S" >> "$LOG"
  TK674_SIDES=L "$PY" tk674_run.py cells >> "$LOG.L" 2>&1 & pl=$!
  TK674_SIDES=S "$PY" tk674_run.py cells >> "$LOG.S" 2>&1 & ps=$!
  wait $pl; wait $ps
else
  stage cells
fi
stage tables --entry "$ENTRY"
stage ccheck
if [ "${TK674_LEVEL:-2}" -gt 2 ]; then "$PY" tk674_level.py map "${TK674_LEVEL}" >> "$LOG" 2>&1; else stage map; fi
echo "== $(date -u +%FT%TZ) chain готово" >> "$LOG"
