#!/usr/bin/env bash
# TK-728 шаг 4а (srv): питон-семейства — существующие ворота G-causal на K случайных строках (мусор вместо данных позже момента сигнала), по очереди; результат каждого — json в <каталог>.
#   tk728_causal.sh <каталог> <K> [окно] [сторона]       (окно / сторона — строки `prod/lvl-<окно>-<сторона>/rows.parquet`; по умолчанию wb long)
set -uo pipefail
R="${1:?каталог}"; K="${2:?K}"; W="${3:-wb}"; S="${4:-long}"
H=/data/p42-tk728/code; PY=/data/tk592/venv/bin/python
ROWS=${ROWS:-/data/p42-tk671/prod/lvl-$W-$S/rows.parquet}
KB=$(( K < 20 ? K : 20 )); KF=$(( K < 20 ? K : 2 ))
mkdir -p "$R/tmp"; cd "$H"; export PYTHONPATH="$H"
run() { n="$1"; shift; t=$(date +%s); "$@" > "$R/$n.log" 2>&1; echo "$n rc=$? $(( $(date +%s) - t )) с" | tee -a "$R/chain.log"; }
run hist  $PY tk671_gate_hist.py --prod /data/p42-tk671/prod --out "$R/hist.json" --brute $KB --causal $K
run mkt   $PY tk671_gate_mkt.py --rows "$ROWS" --win $W --side $S --out "$R/mkt.json" --procs 2 --k-brute $KB --k-causal $K --k-file $KF --tmp "$R/tmp"
run t668  $PY tk671_t668_gate.py --rows "$ROWS" --win $W --side $S --out "$R/t668.json" --procs 2 --k-causal $K
run m697  $PY tk697_gate.py --rows "$ROWS" --win $W --side $S --out "$R/m697.json" --procs 2 --k-brute $KB --k-causal $K --k-file $KF --tmp "$R/tmp"
run h697  $PY tk697_hl_gate.py --rows "$ROWS" --daily /data/p42-tk697/daily/daily.parquet --win $W --side $S --out "$R/h697.json" --k-brute $KB --k-causal $K --k-eq 100 --tmp "$R/tmp"
run l697  $PY tk697_lv_gate.py --rows "$ROWS" --win $W --side $S --out "$R/l697.json" --k-brute $KB --k-causal $K --k-tf 2 --tmp "$R/tmp"
run fund  $PY tk728_gate_wrap.py tk671_gate_fund "$R/fund" causal --n $K --procs 2
run bd    $PY tk728_gate_wrap.py tk671_gate_bd "$R/bd" causal --n $K --procs 2
echo "chain done" >> "$R/chain.log"
