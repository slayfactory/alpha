#!/usr/bin/env bash
# TK-700 (srv): класс B — таблицы TK-654 на всех кандидатах дампа базы (дыра G-key). Использование: tk700_gkey.sh gate <wa|wb|wc …> | h1 <m01 … m06>
#   gate — дамп базы (tk700_sig.sh <окно> base) → tk700_gkey.py build --no-flow → gate (сверка с прежними таблицами TK-634 / TK-638 по кандидатам вселенной дампа)
#   h1   — то же для окон H1 (TK700_WINSET=h1) и table (<root>/<окно>/tab-{long,short}.parquet); дампы base уже есть → пропуск
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PY=/data/tk592/venv/bin/python
ROOT=/data/p42-tk700/gkey
export PYTHONUTF8=1
MODE=${1:?gate|h1}; shift
for W in "$@"; do
  [ "$MODE" = h1 ] && export TK700_WINSET=h1 || unset TK700_WINSET
  [ -s "$ROOT/$W/dumps/L_base/long-sigsx.csv" ] || bash "$HERE/tk700_sig.sh" "$W" base "$ROOT/$W" 2 || { echo "base $W: сбой"; exit 1; }
  $PY "$HERE/tk700_gkey.py" build --win "$W" --procs 2 --no-flow || { echo "build $W: сбой"; exit 1; }
  if [ "$MODE" = h1 ]; then $PY "$HERE/tk700_gkey.py" table --win "$W" || exit 1; else $PY "$HERE/tk700_gkey.py" gate --win "$W" || echo "gate $W: расхождения"; fi
done
echo "gkey $MODE done: $*"
