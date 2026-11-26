#!/usr/bin/env bash
# TK-718 (PS-1037 + PS-1038 п. 4) на srv: один движок за раз, ворота первой партией с автостопом → ядро (сделки сохраняются) → соседи FJ / SEL → сбор рядов.
# Запуск: systemd-run --unit tk718-engine -p MemoryMax=40G -p CPUQuota=540% -p Nice=10 -p WorkingDirectory=/data/p42-tk718/code bash /data/p42-tk718/code/tk718_run.sh
# Повтор безопасен: готовые партии (run-id, окно, сторона, ключ партии) tk700_mass.py пропускает; ворота — при каждом запуске.
set -uo pipefail
C=/data/p42-tk718/code
LOG=/data/p42-tk718/log
KEEP=/data/p42-tk718/trades
PY=/data/tk592/venv/bin/python
DB=/data/p42-tk718/mass718.sqlite
export PYTHONUTF8=1 TK674_REBASE=1
# движок TK-706 PGO m3 (как `run-mass.sh` TK-700): подмена исполняемого файла, md5 сверяется в tk700_mass.py
export TK700_ENGINE=/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45 TK700_ENGINE_MD5=06367f45fad0d7c5c1031d82dfa03615
THR=${THR:-7}; CAP=${CAP:-12}
cd "$C" || exit 1
mkdir -p "$LOG" "$KEEP"
T0=$(date +%s)
say() { echo "$(date -u +%FT%TZ) [$(( $(date +%s) - T0 )) s] $*" | tee -a "$LOG/run.log"; }
mass() {   # run-id, класс клеток (A | B: как у пула — `h1-*-A` / `h1-*-B`), остальные аргументы tk700_mass.py run
  local R=$1 K=$2; shift 2
  $PY tk700_mass.py run --db "$DB" --run-id "$R-$K" --class "$K" --vmax 800 --sigmax 26 --threads "$THR" --cap-gb "$CAP" "$@" >> "$LOG/$R-$K.log" 2>&1
  local rc=$?
  say "$R-$K rc=$rc: $(tail -1 "$LOG/$R-$K.log" | cut -c1-200)"
  return $rc
}
say "старт; движок $TK700_ENGINE"
for K in A B; do
  mass tk718g $K --cellset tk718gate --wins m02,m03,m04,m05,m06 --sides long,short || { say "СТОП: ворота — движок"; exit 3; }
done
$PY tk718_cells.py gate --run-id tk718g-A,tk718g-B | tee -a "$LOG/gate.log" || { say "СТОП: ворота S2 провалены (автостоп)"; exit 3; }
say "ворота пройдены"
for K in A B; do
  mass tk718c-h1 $K --cellset tk718core --wins m01,m02,m03,m04,m05,m06 --sides long,short --keep-trades "$KEEP" || exit 4
  TK700_WINSET=n1 mass tk718c-n1 $K --cellset tk718core --wins n1 --sides short || exit 4
  mass tk718f-h1 $K --cellset tk718nbF --wins m02,m03,m04,m05,m06 --sides long,short || exit 4
  mass tk718s-sel $K --cellset tk718nbS --dry --wins wa,wb,wc --sides long,short || exit 4
done
for R in tk718c-h1 tk718c-n1 tk718f-h1 tk718s-sel; do
  for K in A B; do
    $PY tk700_mass.py timing --db "$DB" --run-id "$R-$K" > "$LOG/$R-$K.timing" 2>&1 || true
  done
done
$PY tk718_cells.py collect >> "$LOG/collect.log" 2>&1 || exit 5
say "готово: ядро, соседи FJ / SEL, ряды собраны"
touch /data/p42-tk718/ENGINE-DONE
