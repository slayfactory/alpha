#!/usr/bin/env bash
# TK-726, шаг 1 (PS-1054 + PS-1060 п. 6, 15): один юнит, один движок за раз: набор → wa / wb / wc → n1 → ряды → ВОРОТА 1 (байт в байт; провал — стоп до 1б) → двойники twA, twB.
# Запуск: systemd-run --unit tk726-step1 -p MemoryMax=40G -p CPUQuota=540% -p Nice=10 -p WorkingDirectory=/data/p42-tk726/code bash /data/p42-tk726/code/tk726_step1.sh
# Повтор безопасен: готовые партии tk700_mass.py пропускает; дерево двойника A строится один раз (повтор — только `twin-run`).
set -uo pipefail
C=/data/p42-tk726/code
LOG=/data/p42-tk726/log
KEEP=/data/p42-tk726/trades
PY=/data/tk592/venv/bin/python
DB=/data/p42-tk726/mass726.sqlite
export PYTHONUTF8=1 TK674_REBASE=1
export TK700_ENGINE=/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45 TK700_ENGINE_MD5=06367f45fad0d7c5c1031d82dfa03615
THR=${THR:-7}; CAP=${CAP:-12}
cd "$C" || exit 1
mkdir -p "$LOG" "$KEEP"
T0=$(date +%s)
say() { echo "$(date -u +%FT%TZ) [$(( $(date +%s) - T0 )) s] $*" | tee -a "$LOG/run.log"; }
mass() {   # run-id, остальные аргументы tk700_mass.py run
  local R=$1; shift
  $PY tk700_mass.py run --db "$DB" --run-id "$R" --cellset tk726s --class A --vmax 800 --sigmax 26 --threads "$THR" --cap-gb "$CAP" --keep-trades "$KEEP" "$@" >> "$LOG/$R.log" 2>&1
  local rc=$?
  say "$R rc=$rc: $(tail -1 "$LOG/$R.log" | cut -c1-200)"
  return $rc
}
say "старт; движок $TK700_ENGINE"
$PY tk726_run.py cells >> "$LOG/cells.log" 2>&1 || { say "СТОП: набор клеток"; exit 2; }
mass tk726-sel-A --dry --wins wa,wb,wc --sides short || exit 3
TK700_WINSET=n1 mass tk726-n1-A --wins n1 --sides short || exit 3
$PY tk726_run.py collect >> "$LOG/collect.log" 2>&1 || { say "СТОП: сбор рядов"; exit 4; }
$PY tk726_run.py gate | tee -a "$LOG/gate.log"
[ "${PIPESTATUS[0]}" = 0 ] || { say "СТОП: ВОРОТА 1 провалены (ряды ≠ летопись) — разбор до 1б"; touch /data/p42-tk726/GATE-FAIL; exit 5; }
say "ВОРОТА 1 пройдены"
if [ ! -e /data/p42-tk726/twA/cache ]; then
  $PY tk726_run.py twin-prep >> "$LOG/twin-prep.log" 2>&1 || { say "СТОП: дерево двойника A / кэш wc"; exit 6; }
fi
$PY tk726_run.py twin-run --threads "$THR" --cap-gb "$CAP" >> "$LOG/twin-run.log" 2>&1 || { say "СТОП: движок на двойниках"; exit 7; }
say "готово: wa/wb/wc, n1, twA, twB; сделки — $KEEP"
touch /data/p42-tk726/ENGINE-DONE
