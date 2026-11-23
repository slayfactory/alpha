#!/usr/bin/env bash
# TK-695: копия `dl` месяца на Storage Box (rsync -aH, до 40 МБ/с); целостность — следующим шагом: манифест (sha256 локально = `sha256sum` на боксе = опубликованный Binance).
#   tk695_cp.sh <каталог месяца> <каталог на боксе> [<источник, по умолчанию <каталог месяца>/dl>]
W=$1
D=$2
SRC=${3:-$W/dl}
ssh -o BatchMode=yes box "mkdir -p $D" || exit 1
rsync -aH --partial --bwlimit=40000 -e "ssh -o BatchMode=yes" $SRC/ box:$D/
