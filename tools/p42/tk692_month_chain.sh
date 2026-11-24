#!/bin/bash
# TK-692: вся цепочка нового месяца на srv одним юнитом (по приходу прогонов TK-700): walk пулов A (лонг) / H / B → база s4 → повтор лонга + rr → [ждать маркер шорта A] → walk шорта A → повтор шорта + rr.
# usage: tk692_month_chain.sh <M: m02…> <JA: run-id месяцев A лонг, через запятую> <JAS: A шорт> <JH: H> <JB: B лонг> <JBS: B шорт> <маркер готовности A шорта> [FROM шаг 1–4]
# Движок повтора — отдельным юнитом на CPUQuota=25 % (п. PS-1007 §9); walk — на самом юните. Запуск: systemd-run --unit tk692-chain-<M> -p CPUQuota=100% -p CPUWeight=100 -p MemoryMax=8G -p Nice=10 bash tk692_month_chain.sh …
set -euo pipefail
export PYTHONUTF8=1
M=$1; JA=$2; JAS=$3; JH=$4; JB=$5; JBS=$6; MARK=$7; FROM=${8:-1}       # FROM: 1 walk, 2 база s4, 3 повтор лонга, 4 шорт A (после маркера)
C=/data/p42-tk692/code
MASS=/data/p42-tk700/mass/mass.sqlite; OUT=/data/p42-tk692/tk692.sqlite; RP=/data/p42-tk692/replay.sqlite
W="python3 $C/tk692_walk.py run --db $MASS --out $OUT --extra $RP"
eng() { systemd-run --wait --collect --pipe --unit "tk692-eng-$M-$1" -p CPUQuota=25% -p CPUWeight=20 -p MemoryMax=14G -p Nice=15 -p Environment=PYTHONUTF8=1 /data/tk592/venv/bin/python "$C/tk692_replay.py" "${@:2}"; }
if [ "$FROM" -le 1 ]; then
echo "[$(date +%T)] walk A long"; $W --pool A --cellset all1 --sides long --runs-long "$JA"
echo "[$(date +%T)] walk H";      $W --pool H --cellset all2 --runs-long "$JH" --runs-short "$JH"
echo "[$(date +%T)] walk B";      $W --pool B --cellset all2 --runs-long "$JB" --runs-short "$JBS"
fi
if [ "$FROM" -le 2 ]; then echo "[$(date +%T)] base s4"; eng base-s4 base --side short --label anchor --runs "$JAS"; fi
if [ "$FROM" -le 3 ]; then
echo "[$(date +%T)] replay long"; eng rp-long replay --rid "A-$M" --side long --runs "$JA"
python3 "$C/tk692_replay.py" rr --rid "A-$M" --side long --runs "$JA"
fi
echo "[$(date +%T)] wait marker $MARK"; until [ -f "$MARK" ]; do sleep 60; done
echo "[$(date +%T)] walk A short"; $W --pool A --cellset all1 --sides short --runs-short "$JAS"
echo "[$(date +%T)] replay short"; eng rp-short replay --rid "A-$M" --side short --runs "$JAS"
python3 "$C/tk692_replay.py" rr --rid "A-$M" --side short --runs "$JAS"
echo "[$(date +%T)] chain $M done"
