#!/usr/bin/env bash
# П-42, TK-681 (srv, юнит tk681-chain): ворота G-base новым бинарём, затем таблица `FAST` (50 столбцов) на всех строках окон wa / wb / wc × long / short.
# Использование: tk681_chain.sh <потоки>   (каталог — /data/p42-tk681; строки — /data/p42-tk671/prod/lvl-<окно>-<сторона>/rows.parquet, как у tox в tk671_feat.sh)
# Красные ворота останавливают цепочку. Выход: fast/<окно>-<сторона>/{eps-key.csv,fast.csv}, время шагов — fast/steps.log, покрытие — res/fastcov681.json.
set -uo pipefail
TH=${1:-4}
R=/data/p42-tk681
PY=/data/tk592/venv/bin/python
FEAT=$R/bin/alpha-feat-tk681
STORE=/data/p42-tk599/tick-store
cd "$R" || exit 2
mkdir -p log res fast
python3 code/tk681_run.py gates > log/gates.log 2>&1 || { echo "gates rc=$?" >> fast/steps.log; exit 1; }
echo "gates rc=0" >> fast/steps.log
for WIN in wa wb wc; do
  case $WIN in wa) T0=2026-06-17;; wb) T0=2026-07-25;; wc) T0=2026-09-01;; esac
  W=/data/p42-tk621/$WIN
  for SIDE in long short; do
    O=$R/fast/$WIN-$SIDE
    [ -e "$O/DONE" ] && continue
    mkdir -p "$O"
    $PY code/tk671_prep.py --rows /data/p42-tk671/prod/lvl-$WIN-$SIDE/rows.parquet --out "$O" > "$O/prep.log" 2>&1 || { echo "prep $WIN $SIDE rc=$?" >> fast/steps.log; exit 1; }
    rm -f "$O/rows-slice.parquet" "$O/sigsx.csv"
    awk -v s=$SIDE 'NR==1{print $0",side";next}{print $0","s}' "$O/eps-ts.csv" > "$O/eps-tox.csv"
    S=$(date +%s)
    $FEAT fast --eps "$O/eps-tox.csv" --out "$O/fast.csv" --base-date $T0 --trades $W/aggtrades --store $STORE --threads "$TH" > "$O/fast.log" 2>&1; rc=$?
    echo "fast $WIN $SIDE rc=$rc sec=$(( $(date +%s) - S )) threads=$TH rows=$(( $(wc -l < "$O/eps-ts.csv") - 1 )) load=$(cut -d' ' -f1 /proc/loadavg)" >> fast/steps.log
    [ $rc -eq 0 ] || exit 1
    rm -f "$O/eps-tox.csv" "$O/eps-ts.csv"
    touch "$O/DONE"
  done
done
python3 code/tk681_run.py cov "$R/fast" > log/cov.log 2>&1 || { echo "cov rc=$?" >> fast/steps.log; exit 1; }
echo "chain done" >> fast/steps.log
