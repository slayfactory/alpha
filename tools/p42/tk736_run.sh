#!/usr/bin/env bash
# TK-736 (П-42 шаг 7, §13.80.3): цепочка движка на srv — один движок за раз. Запуск (этапы — аргументы):
#   systemd-run --unit tk736-pre -p MemoryMax=40G -p CPUQuota=540% -p Nice=10 -p WorkingDirectory=/data/p42-tk736/code bash /data/p42-tk736/code/tk736_run.sh v0long gv ctl kinf
#   systemd-run --unit tk736-post ... bash /data/p42-tk736/code/tk736_run.sh v2fj v2sel v2pool        # только после заморозки k и G-pow (не O6)
# Повтор безопасен: готовые партии tk700_mass.py пропускает. Этап → окна — `tk736_cells.py list`.
set -uo pipefail
C=/data/p42-tk736/code; LOG=/data/p42-tk736/log; KEEP=/data/p42-tk736/trades
PY=/data/tk592/venv/bin/python; DB=/data/p42-tk736/mass736.sqlite; FREEZE=/data/p42-tk736/tk736-frozen.json
export PYTHONUTF8=1 TK674_REBASE=1 TK736_ROOT=/data/p42-tk736
export TK700_ENGINE=/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45 TK700_ENGINE_MD5=06367f45fad0d7c5c1031d82dfa03615
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
  $PY tk736_cells.py build "$ST" --freeze "$FREEZE" >> "$LOG/cells-$ST.log" 2>&1 || { say "СТОП: набор $ST"; exit 2; }
  WINS=$($PY tk736_cells.py list | awk -v s="$ST" '$1==s {print $0}' | sed -E 's/.*окон +[0-9]+ \(([^)]*)\).*/\1/')
  SIDES=$($PY tk736_cells.py list | awk -v s="$ST" '$1==s {print $0}' | sed -E 's/.*стороны ([a-z+]+),.*/\1/' | tr '+' ',')
  H=$(echo "$WINS" | tr ',' '\n' | grep -E '^m0' | paste -sd,); S=$(echo "$WINS" | tr ',' '\n' | grep -E '^w[abc]$' | paste -sd,); N=$(echo "$WINS" | tr ',' '\n' | grep -E '^n1$' | paste -sd,)
  [ -n "$H" ] && { mass "tk736-$ST-h1" --cellset "tk736-$ST" --wins "$H" --sides "$SIDES" || exit 3; }
  [ -n "$S" ] && { mass "tk736-$ST-sel" --cellset "tk736-$ST" --dry --wins "$S" --sides "$SIDES" || exit 3; }
  [ -n "$N" ] && { TK700_WINSET=n1 mass "tk736-$ST-n1" --cellset "tk736-$ST" --wins "$N" --sides "$SIDES" || exit 3; }
  say "этап $ST готов"
done
touch "/data/p42-tk736/ENGINE-DONE-$(echo "$*" | tr ' ' '-')"
