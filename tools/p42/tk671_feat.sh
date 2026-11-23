#!/usr/bin/env bash
# П-42 §13.71 (TK-671), шаг feat (srv): Rust-признаки на ВСЕХ строках одного окна и стороны — f12 → (turn24h) → all = flow + deriv → tcount → tsize → tox; время каждого шага — в `<out>/steps.log`.
# Использование: tk671_feat.sh <окно wa|wb|wc> <side long|short> <потоки>   (выход — /data/p42-tk671/fam/rust/<окно>-<side>/, входы — tk671_prep из prod/lvl-<окно>-<side>/rows.parquet)
# Позиционная связь: строка i каждого выхода = строка i файла eps-key.csv (row_id,sym,sig_ms,level_px_e8) — сборщик проверяет (sym, sig_ms) по каждой строке.
set -uo pipefail
WIN=$1; SIDE=$2; TH=$3
PY=/data/tk592/venv/bin/python
CODE=/data/p42-tk671/code/p
F12=/data/p42-tk610/bin/alpha-feat-f12d-65519d8ff44e5be924f662609f9d54f7
BT=/data/p42-tk639/bin/alpha-feat
TOX=/data/p42-tk671/bin/alpha-feat-tox-ed78ac4c408073f5f7343538c99b1cf3
STORE=/data/p42-tk599/tick-store
case $WIN in wa) T0=2026-06-17;; wb) T0=2026-07-25;; wc) T0=2026-09-01;; *) echo "окно?"; exit 2;; esac
W=/data/p42-tk621/$WIN
O=${TK671_RUST_OUT:-/data/p42-tk671/fam/rust}/$WIN-$SIDE            # TK-690: TK671_RUST_OUT / TK671_ROWS — строки другого набора (потоки nf / ev / ea); по умолчанию — как было
mkdir -p "$O"; cd "$O" || exit 2
export TK594_TRADES=$W/aggtrades TK594_METRICS=/data/p42-tk600/metrics-all TK594_PREMIUM=/data/p42-tk600/premium-all TK594_FUNDING_M=/data/p42-tk600/fund-all
tm() { echo "$1 rc=$2 sec=$(( $(date +%s) - $3 )) threads=$TH rows=$(( $(wc -l < eps-ts.csv) - 1 )) load=$(cut -d' ' -f1 /proc/loadavg)" | tee -a steps.log; }
S=$(date +%s)
$PY $CODE/tk671_prep.py --rows "${TK671_ROWS:-/data/p42-tk671/prod/lvl-$WIN-$SIDE/rows.parquet}" --out "$O" > prep.log 2>&1; rc=$?; rm -f rows-slice.parquet
tm prep $rc $S
awk -v s=$SIDE 'NR==1{print $0",side";next}{print $0","s}' eps-ts.csv > eps-tox.csv
S=$(date +%s)
$F12 f12 --sigsx sigsx.csv --side "$SIDE" --out f12.csv --cache $W/cache --trades $W/aggtrades --base-date $T0 --days 80 --threads "$TH" --store $STORE > f12.log 2>&1; tm f12 $? $S
$PY - <<'PY'
import pandas as pd
d = pd.read_csv("f12.csv", usecols=["sym", "sig_ms", "side", "turn24h"])
d["ep_id"] = ["e%d" % i for i in range(len(d))]
d[["ep_id", "sym", "side", "sig_ms", "turn24h"]].to_csv("eps-flow.csv", index=False)
PY
S=$(date +%s)
$BT all --eps eps-flow.csv --out all.csv --base-date $T0 --trades $W/aggtrades --store $STORE --threads "$TH" > all.log 2>&1; tm all_flow_deriv $? $S
for m in tcount tsize; do
  S=$(date +%s)
  $BT $m --eps eps-ts.csv --out $m.csv --base-date $T0 --trades $W/aggtrades --store $STORE --threads "$TH" > $m.log 2>&1; tm $m $? $S
done
S=$(date +%s)
$TOX tox --eps eps-tox.csv --out tox.csv --base-date $T0 --trades $W/aggtrades --store $STORE --threads "$TH" > tox.log 2>&1; tm tox $? $S
echo "feat $WIN $SIDE done" >> steps.log
