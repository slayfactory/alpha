#!/usr/bin/env bash
# TK-695 (В-181): докачка января–мая 2026 на srv по месяцу назад, как июнь TK-686: план → закачка → дерево → `convert` → `store-fill` → один проход проверки →
# zip на Storage Box + манифест → пустышки (`srv-zip-stub.py`). Движок и P&L не запускаются (отложенная выборка В-170). Каждый шаг — отдельный юнит
# `systemd-run` (Nice=19, ionice idle, MemoryMax=4G, CPUQuota ≤ 200 %); шаг с готовой меткой `done.<шаг>` пропускается (повтор после сбоя — тот же запуск).
# Запуск: systemd-run --unit=tk695-chain -p Nice=19 -p CPUQuota=20% bash /data/p42-tk695/code/tk695_chain.sh   (MONTHS="may apr" — часть месяцев)
# События: /data/p42-tk695/ev/<N> — «READY <месяц> <json>» | «FAIL <что>» | «ALLDONE»; журнал — chain.log. Свободно < 60 ГиБ — стоп (FAIL).
set -uo pipefail
ROOT=/data/p42-tk695
CODE=$ROOT/code
BIN=/data/p42-tk616/alpha-mk.396989e9
FILES=/root/tk686/out/files.tsv
STORE=/data/p42-tk599/tick-store
BOXDIR=offload-srv-2026-11-23
MIN_FREE_GIB=60
DL_THREADS=${DL_THREADS:-16}
read -ra ORDER <<< "${MONTHS:-may apr mar feb jan}"
declare -A D0=([may]=2026-04-11 [apr]=2026-03-12 [mar]=2026-02-09 [feb]=2026-01-12 [jan]=2025-12-12)
declare -A S0=([may]=2026-05-01 [apr]=2026-04-01 [mar]=2026-03-01 [feb]=2026-02-01 [jan]=2026-01-01)
declare -A D1=([may]=2026-05-31 [apr]=2026-04-30 [mar]=2026-03-31 [feb]=2026-02-28 [jan]=2026-01-31)
declare -A OLD=([may]=/data/p42-tk686/wj [apr]=$ROOT/may/tree [mar]=$ROOT/apr/tree [feb]=$ROOT/mar/tree [jan]=$ROOT/feb/tree)
mkdir -p $ROOT/ev

log() { echo "$(date -u +%T) $*" >> $ROOT/chain.log; }
free_gib() { df -k --output=avail / | tail -1 | awk '{printf "%.1f", $1/1048576}'; }
ev() { ( flock 9; n=$(( $(cat $ROOT/ev.n 2>/dev/null || echo 0) + 1 )); echo $n > $ROOT/ev.n; echo "$*" > $ROOT/ev/.$n; mv $ROOT/ev/.$n $ROOT/ev/$n ) 9>$ROOT/ev.lock; }
fail() { log "FAIL $*"; touch $ROOT/FAIL; ev "FAIL $* (free $(free_gib) GiB)"; }
guard() {
  local f; f=$(free_gib)
  if awk "BEGIN{exit !($f < $MIN_FREE_GIB)}"; then fail "free disk $f GiB < $MIN_FREE_GIB"; return 1; fi
}

# step <месяц> <имя> <CPUQuota %> <команда…>
step() {
  local m=$1 n=$2 cpu=$3; shift 3
  local W=$ROOT/$m
  [ -e $W/done.$n ] && return 0
  [ -e $ROOT/FAIL ] && return 1
  guard || return 1
  log "$m $n start"
  systemd-run --unit=tk695-$n-$m --wait --pipe --collect -q -p Nice=19 -p IOSchedulingClass=idle -p MemoryMax=4G -p CPUQuota=${cpu}% "$@" >> $W/$n.log 2>&1
  local rc=$?
  echo $rc > $W/$n.rc
  if [ $rc -ne 0 ]; then fail "$m $n rc=$rc"; return 1; fi
  touch $W/done.$n
  log "$m $n ok"
}

prepare() {  # план, закачка, дерево
  local m=$1 W=$ROOT/$m
  mkdir -p $W
  [ -e $W/df-before ] || free_gib > $W/df-before
  step $m plan 100 python3 $CODE/tk686_probe.py plan /root/tk686/out --d0 ${D0[$m]} --d1 ${D1[$m]} --score0 ${S0[$m]} --tree ${OLD[$m]} --plan $W/plan --exclude $ROOT/tradfi.txt || return 1
  step $m dl 100 bash -c "python3 $CODE/tk621_dl.py $W/plan $W/dl $DL_THREADS && if [ -s $W/dl/log/miss.tsv ]; then mv $W/dl/log/miss.tsv $W/dl/log/miss.1.tsv; python3 $CODE/tk621_dl.py $W/plan $W/dl $DL_THREADS; fi; [ ! -s $W/dl/log/miss.tsv ]" || return 1
  step $m tree 100 python3 $CODE/tk686_june.py tree --files $FILES --universe $W/plan/universe.json --d0 ${D0[$m]} --d1 ${D1[$m]} --dl $W/dl --old ${OLD[$m]} --out $W/tree || return 1
}

build() {  # минутки → .b42, склад тиков, проверка одним проходом
  local m=$1 W=$ROOT/$m
  step $m convert 200 env P42_T0=${D0[$m]} $BIN breakout convert --src $W/tree/klines --out $W/cache --threads 2 || return 1
  step $m sf 200 env P42_T0=${D0[$m]} $BIN breakout store-fill --trades $W/tree/aggtrades --store $STORE --threads 2 --min-free-gb 45 --report $W/storefill || return 1
  step $m check 100 python3 $CODE/tk686_june.py check --files $FILES --universe $W/plan/universe.json --d0 ${D0[$m]} --d1 ${D1[$m]} --out $W/tree --store $STORE --report $W/check.json || return 1
}

offload() {  # zip на бокс → манифест → пустышки
  local m=$1 W=$ROOT/$m
  [ -e $W/done.ready ] && return 0
  echo '[{"src": "'$W'/dl", "dest": "'$BOXDIR'/p42-tk695-'$m'"}]' > $W/cspec.json
  step $m cp 100 python3 $CODE/srv-offload-paths.py $W/cspec.json --manifest $W/cp.manifest.jsonl || return 1
  step $m man 100 python3 $CODE/srv-zip-stub.py manifest $W/dl/aggtrades=$BOXDIR/p42-tk695-$m/aggtrades --store $STORE --out $W/zipstub.manifest.jsonl || return 1
  step $m mcp 100 rsync -e "ssh -o BatchMode=yes" $W/zipstub.manifest.jsonl box:$BOXDIR/p42-tk695-$m/ || return 1
  step $m stub 100 python3 $CODE/srv-zip-stub.py stub $W/zipstub.manifest.jsonl --store $STORE --go || return 1
  free_gib > $W/df-after
  python3 $CODE/tk695_report.py $m > $W/report.json
  touch $W/done.ready
  ev "READY $m $(cat $W/report.json)"
}

OFF=""
for m in "${ORDER[@]}"; do
  prepare $m || exit 1
  if [ -n "$OFF" ]; then wait $OFF || exit 1; OFF=""; fi
  build $m || exit 1
  offload $m &
  OFF=$!
done
if [ -n "$OFF" ]; then wait $OFF || exit 1; fi
ev "ALLDONE"
log "ALLDONE"
