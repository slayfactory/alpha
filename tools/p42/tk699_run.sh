#!/usr/bin/env bash
# TK-699 (srv): ворота и дельта-пересчёт f12 с размером свечи уровня. Запускать юнитом (systemd-run, ≤ 2 ядра, MemoryMax 8G, Nice 15).
#   tk699_run.sh gate <win> <side> [coin-mod coin-rem]   — срез монет: старый и новый бинарь на всех строках среза (байт в байт вне 10 столбцов и на tf = 60),
#                                                          Python-эталон с tf_min на выборке (30 / 60 / 240); итог — <RUN>/gate-<win>-<side>/gate-{bin,py}.json
#   tk699_run.sh prod <win> <side>                       — f12 новым бинарём на строках tf ≠ 60 окна и стороны → <RUN>/prod-<win>-<side>/f12.csv; asm / data не трогает
# Каталоги: /data/p42-tk699/{bin,code,run}; входы — как tk671_feat.sh (кэш минуток и сутки сделок окна, склад тиков только чтение).
set -uo pipefail
MODE=$1; WIN=$2; SIDE=$3; CM=${4:-30}; CR=${5:-7}
PY=/opt/venv/bin/python
R=/data/p42-tk699; CODE=$R/code
OLD=/data/p42-tk610/bin/alpha-feat-f12d-65519d8ff44e5be924f662609f9d54f7
NEW=$R/bin/alpha-feat-tf
STORE=/data/p42-tk599/tick-store
case $WIN in wa) T0=2026-06-17;; wb) T0=2026-07-25;; wc) T0=2026-09-01;; *) echo "окно?"; exit 2;; esac
W=/data/p42-tk621/$WIN
f12() { # <бинарь> <каталог> <выход>
  "$1" f12 --sigsx "$2/sigsx.csv" --side "$SIDE" --out "$3" --cache $W/cache --trades $W/aggtrades --base-date $T0 --days 80 --threads 2 --store $STORE \
    > "$3.log" 2>&1
}
case $MODE in
  gate)
    D=$R/run/gate-$WIN-$SIDE; mkdir -p "$D"
    $PY $CODE/tk699_f12tf.py prep --win $WIN --side $SIDE --out "$D" --which all --coin-mod "$CM" --coin-rem "$CR" || exit 1
    S=$(date +%s); f12 $OLD "$D" "$D/old.csv"; echo "old rc=$? sec=$(( $(date +%s) - S ))"
    S=$(date +%s); f12 $NEW "$D" "$D/new.csv"; echo "new rc=$? sec=$(( $(date +%s) - S ))"
    $PY $CODE/tk699_f12tf.py gate-bin --dir "$D" --old "$D/old.csv" --new "$D/new.csv" > "$D/gate-bin.json"; echo "gate-bin rc=$?"
    SIGFEAT_DAYS=80 $PY $CODE/tk699_f12tf.py gate-py --win $WIN --side $SIDE --dir "$D" --new "$D/new.csv" --sample 1500 > "$D/gate-py.json"; echo "gate-py rc=$?"
    echo "gate $WIN $SIDE done" ;;
  prod)
    D=$R/run/prod-$WIN-$SIDE; mkdir -p "$D"
    $PY $CODE/tk699_f12tf.py prep --win $WIN --side $SIDE --out "$D" --which tf || exit 1
    S=$(date +%s); f12 $NEW "$D" "$D/f12.csv"; echo "f12 rc=$? sec=$(( $(date +%s) - S ))"
    echo "prod $WIN $SIDE done" ;;
  *) echo "gate|prod"; exit 2 ;;
esac
