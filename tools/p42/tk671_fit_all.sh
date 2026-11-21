#!/usr/bin/env bash
# П-42 §13.71 (TK-671): обучение по протоколу PS-948 §5 на обеих сторонах: nstar (июль) → months → plc. Ждёт ASM-DONE и glab pass. Юнит tk671-fit (systemd-run, Nice 10), потоки — первый аргумент (по умолчанию 4).
# Журнал /data/p42-tk671/fit/fit-all.log; конец — строка FIT-DONE. Печатает только служебные числа (RMSE по N, число строк), не P&L.
set -uo pipefail
TH=${1:-4}; C=/data/p42-tk671/code/p; PY=/data/tk592/venv/bin/python; D=/data/p42-tk671/data; O=/data/p42-tk671/fit; L=$O/fit-all.log
export PYTHONUTF8=1
mkdir -p $O
until grep -q ASM-DONE /data/p42-tk671/fam/asm.log 2>/dev/null; do sleep 30; done
for S in long short; do python3 -c "import json,sys;sys.exit(0 if json.load(open('$D/$S.glab.json'))['pass'] else 1)" || { echo "G-lab $S красный — стоп" >> $L; exit 3; }; done
for S in long short; do
  T0=$(date +%s); $PY $C/tk671_fit.py --side $S --data $D/$S.parquet --out $O/$S --threads $TH --step nstar >> $L 2>&1; echo "nstar $S rc=$? sec=$(( $(date +%s) - T0 ))" >> $L
done
echo NSTAR-DONE >> $L
for S in long short; do
  T0=$(date +%s); $PY $C/tk671_fit.py --side $S --data $D/$S.parquet --out $O/$S --threads $TH --step months >> $L 2>&1; echo "months $S rc=$? sec=$(( $(date +%s) - T0 ))" >> $L
  T0=$(date +%s); $PY $C/tk671_fit.py --side $S --data $D/$S.parquet --out $O/$S --threads $TH --step plc >> $L 2>&1; echo "plc $S rc=$? sec=$(( $(date +%s) - T0 ))" >> $L
done
echo FIT-DONE >> $L
