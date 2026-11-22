#!/bin/bash
# П-42 TK-680: единички (уровень 1 карты TK-674) на srv — select → ftab → validate → cells → tables → ccheck → map. Свой каталог, общая БД клеток летописи. Запуск юнитом
# (строго после юнитов TK-674 / TK-679: два движка вместе не помещаются в память):
#   systemd-run --unit=tk680-chain --collect -p Nice=19 -p MemoryMax=40G -p OOMScoreAdjust=1000 -p WorkingDirectory=/data/p42-tk680/code bash tk680_chain.sh L-<запись результата>
set -euo pipefail
ENTRY=$1
PY=/opt/venv/bin/python
export TK674_ROOT=/data/p42-tk680 TK674_REBASE=1 TK674_LEVEL=1 TK674_AUTHOR=TK-680
export TK674_BIN=/data/p42-tk674/bin/alpha-tk674 TK674_GBASE=/data/p42-tk674/res/gbase674.json
mkdir -p $TK674_ROOT/res $TK674_ROOT/log
cp -n /data/p42-tk674/res/eqbins674.json $TK674_ROOT/res/ 2>/dev/null || true
LOG=$TK674_ROOT/log/chain.log
echo "== $(date -u +%FT%TZ) select" >> "$LOG"; "$PY" tk680_singles.py select >> "$LOG" 2>&1
stage() { echo "== $(date -u +%FT%TZ) $*" >> "$LOG"; "$PY" tk674_run.py "$@" >> "$LOG" 2>&1; }
stage ftab
stage validate
stage cells                                             # стороны по очереди, один движок
stage tables --entry "$ENTRY"
stage ccheck
echo "== $(date -u +%FT%TZ) map" >> "$LOG"; "$PY" tk680_singles.py map >> "$LOG" 2>&1
echo "== $(date -u +%FT%TZ) chain готово" >> "$LOG"
