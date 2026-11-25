#!/bin/bash
# П-42 TK-674: один пакет уровня карты на srv — [ftab] → validate → cells → tables → ccheck → map. Запуск юнитом:
#   systemd-run --unit=tk674b-chain --collect -p Nice=19 -p MemoryMax=40G -p LimitCORE=0 -p OOMScoreAdjust=1000 -p WorkingDirectory=/data/p42-tk674/code -E TK674_REBASE=1 [-E TK674_LEVEL=3] bash tk674_chain.sh L-<запись результата>
# Каждая стадия — отдельный процесс; клетки, найденные в БД летописи, не пересчитываются. Журнал — log/chain<суффикс>.log.
# Пары с активными осями единичек TK-680 (§13.73.7, PS-960): -E TK674_REBASE=1 -E TK674_X680=1 [-E TK674_ROOT=<свой каталог>] [-E TK674_X680_ROOTS=<корни>] — стадии plan → ftab → validate →
# plan (заново, без отвергнутых движком; отбор пар заморожен) → cells → tables → ccheck → map; журнал log/chainbx.log; нужны res/thr674.json, res/eqbins674.json, res/gbase674.json в каталоге.
set -euo pipefail
ENTRY=$1
PY=/opt/venv/bin/python
LOG=${TK674_ROOT:-/data/p42-tk674}/log/chain${TK674_REBASE:+b}${TK674_SPLIT:+s}${TK674_X680:+x}${TK674_LEVEL:-}.log
stage() { echo "== $(date -u +%FT%TZ) $*" >> "$LOG"; "$PY" tk674_run.py "$@" >> "$LOG" 2>&1; }
# новый бинарь (ось split): выборочная проверка до счёта — только если md5 бинаря ещё не в eqbins674.json (c539: бинарь прошёл cgate, L-334)
if [ -n "${TK674_BIN:-}" ] && ! grep -q "$(md5sum "$TK674_BIN" | cut -c1-32)" "${TK674_ROOT:-/data/p42-tk674}/res/eqbins674.json" 2>/dev/null; then stage spot; fi
if [ "${TK674_LEVEL:-2}" -gt 2 ]; then stage ftab; fi
if [ -n "${TK674_X680:-}" ] && [ "${TK674_LEVEL:-2}" -le 2 ]; then stage plan; stage ftab; fi     # пары с осями единичек: отбор пар (без движка) и таблица фильтров (тройки / четвёрки — `ftab` выше)
stage validate
if [ -n "${TK674_X680:-}" ] && [ "${TK674_LEVEL:-2}" -le 2 ]; then stage plan; fi                                # заново: клетки, отвергнутые движком, уходят из плана (отбор пар заморожен)
if [ "${TK674_LEVEL:-2}" -gt 2 ]; then                  # уровни: стороны двумя процессами (движок по 2 потока)
  echo "== $(date -u +%FT%TZ) cells L | S" >> "$LOG"
  if [ -n "${TK674_SEQ:-}" ]; then                      # стороны по очереди: один движок за раз (22.11: L | S вместе — 40 ГБ, OOM; 26 проходов сигналов ≈ 30 ГБ)
    TK674_SIDES=L "$PY" tk674_run.py cells >> "$LOG.L" 2>&1
    TK674_SIDES=S "$PY" tk674_run.py cells >> "$LOG.S" 2>&1
  else
    TK674_SIDES=L "$PY" tk674_run.py cells >> "$LOG.L" 2>&1 & pl=$!
    TK674_SIDES=S "$PY" tk674_run.py cells >> "$LOG.S" 2>&1 & ps=$!
    wait $pl; wait $ps
  fi
else
  stage cells
fi
stage tables --entry "$ENTRY"
stage ccheck
if [ "${TK674_LEVEL:-2}" -gt 2 ]; then "$PY" tk674_level.py map "${TK674_LEVEL}" >> "$LOG" 2>&1; else stage map; fi
echo "== $(date -u +%FT%TZ) chain готово" >> "$LOG"
