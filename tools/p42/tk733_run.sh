#!/usr/bin/env bash
# TK-733: прогрев против шага 1 — цепочка prep → run → cmp по списку «окно:режим»; один движок за раз.
# Запуск: systemd-run --unit tk733-run -p MemoryMax=40G -p CPUQuota=500% -p Nice=10 bash /data/p42-tk733/code/tk733_run.sh wa:ctrl wa:warm n1:warm m02:warm m03:warm m01:cold
set -uo pipefail
C=/data/p42-tk733/code; LOG=/data/p42-tk733/log; PY=/data/tk592/venv/bin/python
export PYTHONUTF8=1
mkdir -p "$LOG"
cd "$C" || exit 1
for wm in "$@"; do
  w=${wm%%:*}; m=${wm##*:}
  T=$(date +%s)
  for step in prep run cmp; do
    [ "$step" = prep ] && [ -d "/data/p42-tk733/trees/$w-$m/klines" ] && continue
    [ "$step" = run ] && [ -f "/data/p42-tk733/trades/$w-$m/t0.json" ] && continue
    $PY tk733_warm.py $step $w $m >> "$LOG/$w-$m.log" 2>&1 || { echo "$(date -u +%FT%TZ) $w-$m $step FAIL" >> "$LOG/run.log"; continue 2; }
  done
  echo "$(date -u +%FT%TZ) $w-$m готово за $(( $(date +%s) - T )) с" >> "$LOG/run.log"
done
echo "$(date -u +%FT%TZ) ВСЁ" >> "$LOG/run.log"
touch /data/p42-tk733/DONE
