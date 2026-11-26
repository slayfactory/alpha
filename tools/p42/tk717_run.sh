#!/usr/bin/env bash
# TK-717 стадия A (PS-1035 + поправка PS-1036) на srv: упаковка пула → T1 → T2 (ворота против L-436) → T3 → вердикт → отчёт.
# Запуск: systemd-run --unit tk717-stagea -p MemoryMax=14G -p CPUQuota=100% -p Nice=10 bash /data/p42-tk717/code/tk717_run.sh
set -euo pipefail
C=/data/p42-tk717/code
DB=/data/p42-tk700/mass/mass.sqlite
O=/data/p42-tk717/tk717.sqlite
M="jan feb mar apr may jun"
LONG=""; SHORT=""
for m in $M; do LONG="$LONG,h1-$m-A"; SHORT="$SHORT,h1-$m-A-short"; done
LONG=${LONG#,}; SHORT=${SHORT#,}
T0=$(date +%s)
step() { echo "[$(( $(date +%s) - T0 )) s] $*"; }
for side in short long; do
  runs=$SHORT; [ "$side" = long ] && runs=$LONG
  step "pack $side"
  python3 $C/tk717_stagea.py pack --db $DB --side $side --runs "$runs" --out /data/p42-tk717/pool-$side
  step "t1 $side"
  python3 $C/tk717_stagea.py t1 --pool /data/p42-tk717/pool-$side --side $side --out $O
  step "t2 $side"
  python3 $C/tk717_stagea.py t2 --pool /data/p42-tk717/pool-$side --side $side --out $O --tk692 /data/p42-tk692/tk692.sqlite --extra /data/p42-tk692/replay.sqlite
  if [ "$side" = short ]; then
    step "t3 short"
    python3 $C/tk717_stagea.py t3 --pool /data/p42-tk717/pool-short --side short --out $O --extra /data/p42-tk692/replay.sqlite
  fi
done
step "verdict"
python3 $C/tk717_stagea.py verdict --out $O
python3 $C/tk717_stagea.py report --out $O > /data/p42-tk717/report-SA.md
step "done"
