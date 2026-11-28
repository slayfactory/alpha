#!/usr/bin/env bash
# TK-739 (П-42 шаг 8, §13.81.4): цепочка движка на srv — один движок за раз. Запуск (этапы — аргументы):
#   systemd-run --unit tk739-cost -p MemoryMax=40G -p Nice=10 -p WorkingDirectory=/data/p42-tk739/code bash /data/p42-tk739/code/tk739_run.sh cost
#   … design / ctl / box / desc — только после заморозки (S, кандидат) и G-pow8 (не O6)
# Повтор безопасен: готовые партии tk700_mass.py пропускает. Этап → окна — `tk739_cells.py list`. Бинарь — TK-738 plain (md5 в карточке TK-738; amend L-544).
set -uo pipefail
C=/data/p42-tk739/code; LOG=/data/p42-tk739/log; KEEP=/data/p42-tk739/trades
PY=/data/tk592/venv/bin/python; DB=/data/p42-tk739/mass739.sqlite; FREEZE=/data/p42-tk739/tk739-frozen.json
export PYTHONUTF8=1 TK674_REBASE=1 TK739_ROOT=/data/p42-tk739
export TK700_ENGINE=/data/p42-tk738/bin/alpha-tk738-plain.66eab54e TK700_ENGINE_MD5=66eab54ed5b28d6ce5bfdb0f21296171
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
say "старт: $*; движок $TK700_ENGINE"
for ST in "$@"; do
  $PY tk739_cells.py build "$ST" --freeze "$FREEZE" >> "$LOG/cells-$ST.log" 2>&1 || { say "СТОП: набор $ST"; exit 2; }
  tail -1 "$LOG/cells-$ST.log" | tee -a "$LOG/run.log"
  WINS=$($PY tk739_cells.py list | awk -v s="$ST" '$1==s {print $3}')
  H=$(echo "$WINS" | tr ',' '\n' | grep -E '^m0' | paste -sd,); S=$(echo "$WINS" | tr ',' '\n' | grep -E '^w[abc]$' | paste -sd,); N=$(echo "$WINS" | tr ',' '\n' | grep -E '^n1$' | paste -sd,)
  [ -n "$H" ] && { mass "tk739-$ST-h1" --cellset "tk739-$ST" --wins "$H" --sides short || exit 3; }
  [ -n "$S" ] && { mass "tk739-$ST-sel" --cellset "tk739-$ST" --dry --wins "$S" --sides short || exit 3; }
  [ -n "$N" ] && { TK700_WINSET=n1 mass "tk739-$ST-n1" --cellset "tk739-$ST" --wins "$N" --sides short || exit 3; }
  say "этап $ST готов"
done
touch "/data/p42-tk739/ENGINE-DONE-$(echo "$*" | tr ' ' '-')"
