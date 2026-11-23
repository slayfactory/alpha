#!/usr/bin/env bash
# TK-695 (дополнение, В-181; слова владельца 23.11: «и до вчера догрузить еще можно»): сутки 14.11 → последние опубликованные Binance, aggTrades + минутки, крипто-вселенная по правилу TK-621.
# Закачка — в общий каталог `/data/p42-tk616/fwd/dl` (его читает `tk626_prep.sh` точки вперёд: скачанное по `.sha256` и размеру пропускается — второй закачки нет);
# дерево `/data/p42-tk695/fwd/tree` — отдельное (не wa / wb / wc / wj). Шаги: листинг → план → закачка → дерево → store-fill → проверка одним проходом → копия на бокс → манифест.
# Без `convert` (кэш `.b42` — на окно, его строит `tk626_prep.sh` для w1) и без пустышек (читателю точки нужны настоящие zip, пока он не вызван с `--tick-store`).
# Запуск: systemd-run --unit=tk695-fwd -p Nice=19 -p CPUQuota=20% bash /data/p42-tk695/code/tk695_fwd.sh. Движок и P&L не запускаются.
set -uo pipefail
ROOT=/data/p42-tk695
CODE=$ROOT/code
BIN=/data/p42-tk616/alpha-mk.396989e9
STORE=/data/p42-tk599/tick-store
BOXDIR=offload-srv-2026-11-23
DL=/data/p42-tk616/fwd/dl
W=$ROOT/fwd
DL_THREADS=${DL_THREADS:-24}
D0=2026-11-14
MIN_FREE_GIB=60
mkdir -p $W/list $ROOT/ev
log() { echo "$(date -u +%T) fwd $*" >> $ROOT/chain.log; }
free_gib() { df -k --output=avail / | tail -1 | awk '{printf "%.1f", $1/1048576}'; }
ev() { ( flock 9; n=$(( $(cat $ROOT/ev.n 2>/dev/null || echo 0) + 1 )); echo $n > $ROOT/ev.n; echo "$*" > $ROOT/ev/.$n; mv $ROOT/ev/.$n $ROOT/ev/$n ) 9>$ROOT/ev.lock; }
fail() { log "FAIL $*"; touch $W/FAIL; ev "FAIL fwd $* (free $(free_gib) GiB)"; exit 1; }
step() {  # <имя> <CPUQuota %> <команда…>
  local n=$1 cpu=$2; shift 2
  [ -e $W/done.$n ] && return 0
  [ -e $W/FAIL ] && exit 1
  local f; f=$(free_gib)
  awk "BEGIN{exit !($f < $MIN_FREE_GIB)}" && fail "free disk $f GiB < $MIN_FREE_GIB"
  log "$n start"
  systemd-run --unit=tk695-$n-fwd --slice=tk695.slice --wait --pipe --collect -q -p Nice=19 -p IOSchedulingClass=idle -p MemoryMax=4G -p CPUQuota=${cpu}% "$@" >> $W/$n.log 2>&1
  local rc=$?
  echo $rc > $W/$n.rc
  [ $rc -ne 0 ] && fail "$n rc=$rc"
  touch $W/done.$n
  log "$n ok"
}
[ -e $W/df-before ] || free_gib > $W/df-before
D1=$(date -u +%F)
step list 100 python3 $CODE/tk621_list.py $W/list $D0 $D1
[ -e $W/done.plan ] || { python3 $CODE/tk695_fwd_plan.py plan $W/list $ROOT/tradfi.txt $W/plan > $W/plan.log 2>&1 || fail plan; touch $W/done.plan; }
LAST=$(python3 -c "import json;print(json.load(open('$W/plan/plan.json'))['last_day'])")
step dl 200 bash -c "python3 $CODE/tk621_dl.py $W/plan $DL $DL_THREADS && if [ -s $DL/log/miss.tsv ]; then mv $DL/log/miss.tsv $DL/log/miss.fwd.tsv; python3 $CODE/tk621_dl.py $W/plan $DL $DL_THREADS; fi; [ ! -s $DL/log/miss.tsv ]; rc=\$?; cp $DL/log/progress.json $W/dl.progress.json; exit \$rc"
step tree 100 python3 $CODE/tk686_june.py tree --files $W/plan/files-1m.tsv --universe $W/plan/universe.json --d0 $D0 --d1 $LAST --dl $DL --old $W/no-old --out $W/tree
step sf 200 env P42_T0=$D0 $BIN breakout store-fill --trades $W/tree/aggtrades --store $STORE --threads 2 --min-free-gb 45 --report $W/storefill
step check 100 python3 $CODE/tk686_june.py check --files $W/plan/files-1m.tsv --universe $W/plan/universe.json --d0 $D0 --d1 $LAST --out $W/tree --store $STORE --report $W/check.json
step cp 100 bash $CODE/tk695_cp.sh $W $BOXDIR/p42-tk695-fwd $DL
step man 100 python3 $CODE/srv-zip-stub.py manifest $DL/aggtrades=$BOXDIR/p42-tk695-fwd/aggtrades --store $STORE --out $W/zipstub.manifest.jsonl
step mcp 100 rsync -e "ssh -o BatchMode=yes" $W/zipstub.manifest.jsonl box:$BOXDIR/p42-tk695-fwd/
free_gib > $W/df-after
python3 $CODE/tk695_fwd_plan.py report $W > $W/report.json
touch $W/done.ready
ev "READY fwd $(cat $W/report.json)"
