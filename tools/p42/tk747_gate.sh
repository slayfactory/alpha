#!/usr/bin/env bash
# TK-747: гейт «ключи lm / lz / fw / fz выключены — сделки побайтно прежние». База шорта V0nt (TK-741, этап `nt`) на 10 окнах движком этой ветки (с `--keep-orders`, как TK-743)
# против файлов прогона TK-743 (бинарь /data/p42-tk743/bin/alpha-tk743): trades, orders, setup — `cmp`, все окна. Только srv, один движок за раз:
#   systemd-run --unit tk747-gate -p MemoryMax=40G -p CPUQuota=490% -p Nice=10 -p WorkingDirectory=/data/p42-tk747/code bash /data/p42-tk747/code/tk747_gate.sh <бинарь> <md5> [окна]
# Код — копия `tools/p42` в /data/p42-tk747/code (LF); результат — /data/p42-tk747/trades/tk747-nt-{h1,sel,n1}/<окно>/; итог — строка «гейт: …» в /data/p42-tk747/log/gate.log.
# [окна] — по умолчанию все десять (m01…m06, wa, wb, wc, n1); «m02» — только окно m02.
set -uo pipefail
BIN=${1:?бинарь}; BIN_MD5=${2:?md5}; WINS=${3:-all}
C=/data/p42-tk747/code; LOG=/data/p42-tk747/log; KEEP=/data/p42-tk747/trades; REF=/data/p42-tk743/trades
PY=/data/tk592/venv/bin/python; DB=/data/p42-tk747/mass741.sqlite
export PYTHONUTF8=1 TK674_REBASE=1 TK741_ROOT=/data/p42-tk747 TK736_ROOT=/data/p42-tk736
export P42_SIGSX=0:400 TK743_ORDERS=1
export TK700_ENGINE="$BIN" TK700_ENGINE_MD5="$BIN_MD5"
THR=${THR:-7}; CAP=${CAP:-12}
cd "$C" || exit 1
mkdir -p "$LOG" "$KEEP"
T0=$(date +%s)
say() { echo "$(date -u +%FT%TZ) [$(( $(date +%s) - T0 )) s] $*" | tee -a "$LOG/gate.log"; }
mass() {   # run-id, остальное — аргументы tk700_mass.py run
  local R=$1; shift
  $PY tk700_mass.py run --db "$DB" --run-id "$R" --class A --vmax 800 --sigmax 26 --threads "$THR" --cap-gb "$CAP" --keep-trades "$KEEP" "$@" >> "$LOG/$R.log" 2>&1
  local rc=$?; say "$R rc=$rc: $(tail -1 "$LOG/$R.log" | cut -c1-200)"; return $rc
}
say "старт гейта: V0nt, окна $WINS, движок $BIN md5 $BIN_MD5"
$PY tk741_cells.py build nt >> "$LOG/cells-nt.log" 2>&1 || { say "СТОП: набор nt"; exit 2; }
if [ "$WINS" = all ]; then
  mass "tk747-nt-h1"  --cellset "tk741-nt" --wins m01,m02,m03,m04,m05,m06 --sides short || exit 3
  mass "tk747-nt-sel" --cellset "tk741-nt" --dry --wins wa,wb,wc --sides short || exit 3
  TK700_WINSET=n1 mass "tk747-nt-n1" --cellset "tk741-nt" --wins n1 --sides short || exit 3
else
  mass "tk747-nt-h1" --cellset "tk741-nt" --wins "$WINS" --sides short || exit 3
fi
n=0; bad=0
for R in h1 sel n1; do
  for W in "$KEEP"/tk747-nt-$R/*/; do
    [ -d "$W" ] || continue
    w=$(basename "$W")
    for f in "$W"*.csv; do
      b=$(basename "$f"); g="$REF/tk743-nt-$R/$w/$b"
      case "$b" in *-trades.csv|*-orders.csv|*-setup.csv) ;; *) continue ;; esac
      if cmp -s "$f" "$g"; then n=$((n+1)); else bad=$((bad+1)); say "РАЗЛИЧИЕ: $f против $g"; fi
    done
  done
done
say "гейт: файлов равны $n, различий $bad (trades + orders + setup; эталон — прогон TK-743, /data/p42-tk743/bin/alpha-tk743)"
[ "$bad" -eq 0 ] && [ "$n" -gt 0 ]
