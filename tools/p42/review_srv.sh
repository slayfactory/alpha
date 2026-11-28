#!/usr/bin/env bash
# TK-745: сторона srv цикла `review_loop.sh` — прогон движка на «30 днях», строки сделок, упаковка заявок и уровней, выкладка «Сделок» на :8090. Запускать юнитом (review_loop.sh делает сам):
#   systemd-run --unit rv-run --wait --pipe -p MemoryMax=40G -p CPUQuota=490% -p Nice=10 bash /data/p42-review/code/review_srv.sh <метка> <бинарь> "<ключи k=v …>" <первые сутки ISO> <последние сутки ISO>
# Шаги: клетка V0nt (+ключи) → движок на окнах wc и n1 (`--keep-trades`, `TK743_ORDERS=1`: заявки и уровни сетапа) → строки сделок набора (review_rows.py) → data/trades страницы (tk741_pagedata.py, TK745_REVIEW).
# Один движок счёта за раз (AGENTS.md): если идёт юнит tk7*-run — отказ. Каждый круг начисто: каталог прогона /data/p42-review/<метка> пересоздаётся (готовые партии tk700_mass.py иначе пропускаются).
set -uo pipefail
TAG=${1:?метка}; BIN=${2:?бинарь}; KEYS=${3:-}; LO=${4:?первые сутки}; HI=${5:?последние сутки}
C=/data/p42-review/code; R=/data/p42-review/$TAG; PAGE=${PAGE:-/data/pages/ml}
PY=/data/tk592/venv/bin/python; PYV=/opt/venv/bin/python
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 TK674_REBASE=1 TK741_ROOT=/data/p42-tk741 TK736_ROOT=/data/p42-tk736
export P42_SIGSX=0:400 TK743_ORDERS=1
THR=${THR:-7}; CAP=${CAP:-12}
S0=$(date +%s); S=$S0
step() { local n=$(date +%s); echo "ШАГ $1: $(( n - S )) с"; S=$n; }
die() { echo "СТОП: $*"; exit "${2:-1}"; }
busy=$(systemctl list-units --no-legend --state=active 'tk7*-run*' 2>/dev/null | awk '{print $1}' | tr '\n' ' ')
[ -z "$busy" ] || die "идёт счёт другого движка ($busy) — один движок за раз; ждать, не останавливать" 4
[ -x "$BIN" ] || die "нет бинаря $BIN" 2
MD5=$(md5sum "$BIN" | cut -d' ' -f1)
export TK700_ENGINE="$BIN" TK700_ENGINE_MD5="$MD5"
cd "$C" || die "нет $C" 2
[ -f selection_bias.py ] || cp /data/p42-tk743/code/selection_bias.py . || die "нет selection_bias.py" 2      # нужен tk700_mass.py через lib.py; в tools/p42 его нет
rm -rf "$R" && mkdir -p "$R/log" "$R/trades" || die "каталог $R" 2
echo "прогон $TAG: движок $BIN md5 $MD5; ключи «$KEYS»; сутки $LO … $HI"
$PY review_cells.py build "$R" "$TAG" "$KEYS" > "$R/log/cells.log" 2>&1 || { cat "$R/log/cells.log"; die "клетка" 2; }
tail -1 "$R/log/cells.log" | cut -c1-300
step "клетка"
mass() {   # run-id, остальное — аргументы tk700_mass.py run
  local RID=$1; shift
  $PY tk700_mass.py run --db "$R/mass.sqlite" --run-id "$RID" --class A --vmax 800 --sigmax 26 --threads "$THR" --cap-gb "$CAP" --keep-trades "$R/trades" "$@" >> "$R/log/$RID.log" 2>&1
  local rc=$?; echo "$RID rc=$rc: $(tail -1 "$R/log/$RID.log" | cut -c1-200)"; return $rc
}
mass "$TAG-sel" --cellset "rv-$TAG" --dry --wins wc --sides short || die "движок wc" 3
TK700_WINSET=n1 mass "$TAG-n1" --cellset "rv-$TAG" --wins n1 --sides short || die "движок n1" 3
step "движок (wc, n1)"
$PYV review_rows.py "$R" "$TAG" "$LO" "$HI" "$KEYS" "$BIN" "$MD5" 2>&1 | tee "$R/log/rows.log" | tail -3
[ -s "$R/res/trades.json" ] || die "нет строк сделок (см. $R/log/rows.log)" 3
step "строки сделок"
mkdir -p "$PAGE/data"
TK745_REVIEW="$R" $PYV tk741_pagedata.py trades "$PAGE" 2>&1 | tail -3
step "упаковка и выкладка data/trades"
echo "ИТОГ $(cat "$R/summary.json") ; всего $(( $(date +%s) - S0 )) с"
