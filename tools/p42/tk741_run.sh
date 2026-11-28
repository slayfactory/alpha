#!/usr/bin/env bash
# TK-741 (В-192): цепочка движка на srv — V0 (tt=20) и V0nt (tt=0) на 10 окнах, одна клетка шорта; код и движок — как в TK-736 (`tk736_run.sh`).
#   systemd-run --unit tk741-run -p MemoryMax=40G -p CPUQuota=490% -p Nice=10 -p WorkingDirectory=/data/p42-tk741/code bash /data/p42-tk741/code/tk741_run.sh v0 nt
# Перед запуском: `systemctl list-units 'tk739*'` пуст (иначе ждать, не останавливать). Повтор безопасен: готовые партии tk700_mass.py пропускает.
set -uo pipefail
C=/data/p42-tk741/code; LOG=/data/p42-tk741/log; KEEP=/data/p42-tk741/trades
PY=/data/tk592/venv/bin/python; DB=/data/p42-tk741/mass741.sqlite
export PYTHONUTF8=1 TK674_REBASE=1 TK741_ROOT=/data/p42-tk741 TK736_ROOT=/data/p42-tk736
export P42_SIGSX=0:400   # дамп сигналов с ценой уровня (только запись файла; сделки не меняет) — для кластеров и вкладки «Сделки»
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
  $PY tk741_cells.py build "$ST" >> "$LOG/cells-$ST.log" 2>&1 || { say "СТОП: набор $ST"; exit 2; }
  mass "tk741-$ST-h1"  --cellset "tk741-$ST" --wins m01,m02,m03,m04,m05,m06 --sides short || exit 3
  mass "tk741-$ST-sel" --cellset "tk741-$ST" --dry --wins wa,wb,wc --sides short || exit 3
  TK700_WINSET=n1 mass "tk741-$ST-n1" --cellset "tk741-$ST" --wins n1 --sides short || exit 3
  say "этап $ST готов"
done
touch "/data/p42-tk741/ENGINE-DONE-$(echo "$*" | tr ' ' '-')"
