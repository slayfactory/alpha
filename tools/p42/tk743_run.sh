#!/usr/bin/env bash
# TK-743: база шорта V0nt (TK-741, этап `nt`) на 10 окнах с журналом заявок и уровней сетапа (`--keep-orders`) — на srv, один юнит:
#   systemd-run --unit tk743-run -p MemoryMax=40G -p CPUQuota=490% -p Nice=10 -p WorkingDirectory=/data/p42-tk743/code bash /data/p42-tk743/code/tk743_run.sh <бинарь> <md5>
# Те же окна, клетка, движок-вход и каталог кода, что у `tk741_run.sh` (код — копия `tools/p42`); бинарь — сборка этой ветки (`--keep-orders`). Сделки обязаны совпасть
# с /data/p42-tk741/trades побайтно (`cmp`, `tk743_gate.sh`). Результат: /data/p42-tk743/trades/<прогон>/<окно>/<cid>-{trades,orders,setup,sigsx}.csv.
set -uo pipefail
BIN=${1:?бинарь}; BIN_MD5=${2:?md5}
C=/data/p42-tk743/code; LOG=/data/p42-tk743/log; KEEP=/data/p42-tk743/trades
PY=/data/tk592/venv/bin/python; DB=/data/p42-tk743/mass741.sqlite
export PYTHONUTF8=1 TK674_REBASE=1 TK741_ROOT=/data/p42-tk743 TK736_ROOT=/data/p42-tk736
export P42_SIGSX=0:400   # как в TK-741 (только запись файла; сделки не меняет)
export TK743_ORDERS=1
export TK700_ENGINE="$BIN" TK700_ENGINE_MD5="$BIN_MD5"
THR=${THR:-7}; CAP=${CAP:-12}
cd "$C" || exit 1
mkdir -p "$LOG" "$KEEP"
T0=$(date +%s)
say() { echo "$(date -u +%FT%TZ) [$(( $(date +%s) - T0 )) s] $*" | tee -a "$LOG/run.log"; }
mass() {   # run-id, остальное — аргументы tk700_mass.py run
  local R=$1; shift
  $PY tk700_mass.py run --db "$DB" --run-id "$R" --class A --vmax 800 --sigmax 26 --threads "$THR" --cap-gb "$CAP" --keep-trades "$KEEP" "$@" >> "$LOG/$R.log" 2>&1
  local rc=$?; say "$R rc=$rc: $(tail -1 "$LOG/$R.log" | cut -c1-200)"; return $rc
}
say "старт: V0nt, движок $BIN md5 $BIN_MD5"
ST=nt
$PY tk741_cells.py build "$ST" >> "$LOG/cells-$ST.log" 2>&1 || { say "СТОП: набор $ST"; exit 2; }
mass "tk743-$ST-h1"  --cellset "tk741-$ST" --wins m01,m02,m03,m04,m05,m06 --sides short || exit 3
mass "tk743-$ST-sel" --cellset "tk741-$ST" --dry --wins wa,wb,wc --sides short || exit 3
TK700_WINSET=n1 mass "tk743-$ST-n1" --cellset "tk741-$ST" --wins n1 --sides short || exit 3
say "этап $ST готов"
touch /data/p42-tk743/ENGINE-DONE
