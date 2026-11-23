#!/usr/bin/env bash
# TK-700 (srv): Rust-признаки на строках БАЗЫ окна и стороны — f12 (бинарь TK-699 с tf_min) → all (flow + deriv) → tcount → tsize → tox; как tk671_feat.sh, но окно из tk700_win.py env и корень TK671_ROOT.
# Использование: tk700_feat.sh <окно m01…m06|wa|wb|wc> <side long|short> <потоки>
# Входы: $TK671_ROOT/prod/lvl-<окно>-<side>/rows.parquet (строки базы, tk700_prep.py base); выход: $TK671_ROOT/fam/rust/<окно>-<side>/{f12,all,tcount,tsize,tox,eps-key}.csv + steps.log.
# Данные deriv: метрики / премия / фандинг — TK700_DL (по умолчанию /data/p42-tk700/dl; для wa / wb / wc — прежние корни TK-600).
# Для окон отложенной выборки движок и охрана P&L не нужны: alpha-feat читает только сутки сделок, минутки и тики. Значения исходов не считаются.
set -uo pipefail
WIN=${1:?окно}; SIDE=${2:?сторона}; TH=${3:-2}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=${TK671_ROOT:-/data/p42-tk700/p671}; export TK671_ROOT=$ROOT
PY=${TK700_PY:-/data/tk592/venv/bin/python}
F12=${TK700_F12:-/data/p42-tk699/bin/alpha-feat-tf}
BT=/data/p42-tk639/bin/alpha-feat
TOX=/data/p42-tk671/bin/alpha-feat-tox-ed78ac4c408073f5f7343538c99b1cf3
STORE=/data/p42-tk599/tick-store
eval "$(python3 "$HERE/tk700_win.py" env "$WIN")"
W=$TREE
O=$ROOT/fam/rust/$WIN-$SIDE
case $WIN in
  wa|wb|wc) export TK594_METRICS=/data/p42-tk600/metrics-all TK594_PREMIUM=/data/p42-tk600/premium-all TK594_FUNDING_M=/data/p42-tk600/fund-all ;;
  *) DL=${TK700_DL:-/data/p42-tk700/dl}; export TK594_METRICS=$DL/metrics-all TK594_PREMIUM=$DL/premium-all TK594_FUNDING_M=$DL/fund-all ;;
esac
export TK594_TRADES=$W/aggtrades
mkdir -p "$O"; cd "$O" || exit 2
tm() { echo "$1 rc=$2 sec=$(( $(date +%s) - $3 )) threads=$TH rows=$(( $(wc -l < eps-ts.csv) - 1 )) load=$(cut -d' ' -f1 /proc/loadavg)" | tee -a steps.log; }
S=$(date +%s)
$PY "$HERE/tk700_prep.py" feat-in --rows "$ROOT/prod/lvl-$WIN-$SIDE/rows.parquet" --out "$O" > prep.log 2>&1; rc=$?
tm prep $rc $S
[ $rc -eq 0 ] || exit 1
awk -v s=$SIDE 'NR==1{print $0",side";next}{print $0","s}' eps-ts.csv > eps-tox.csv
S=$(date +%s)
$F12 f12 --sigsx sigsx.csv --side "$SIDE" --out f12.csv --cache $W/cache --trades $W/aggtrades --base-date $T0 --days 80 --threads "$TH" --store $STORE > f12.log 2>&1; rc=$?; tm f12 $rc $S
[ $rc -eq 0 ] || exit 1
$PY - <<'PY'
import pandas as pd
d = pd.read_csv("f12.csv", usecols=["sym", "sig_ms", "side", "turn24h"])
d["ep_id"] = ["e%d" % i for i in range(len(d))]
d[["ep_id", "sym", "side", "sig_ms", "turn24h"]].to_csv("eps-flow.csv", index=False)
PY
S=$(date +%s)
$BT all --eps eps-flow.csv --out all.csv --base-date $T0 --trades $W/aggtrades --store $STORE --threads "$TH" > all.log 2>&1; rc=$?; tm all_flow_deriv $rc $S
for m in tcount tsize; do
  S=$(date +%s)
  $BT $m --eps eps-ts.csv --out $m.csv --base-date $T0 --trades $W/aggtrades --store $STORE --threads "$TH" > $m.log 2>&1; rc=$?; tm $m $rc $S
done
S=$(date +%s)
$TOX tox --eps eps-tox.csv --out tox.csv --base-date $T0 --trades $W/aggtrades --store $STORE --threads "$TH" > tox.log 2>&1; rc=$?; tm tox $rc $S
echo "feat $WIN $SIDE done" >> steps.log
