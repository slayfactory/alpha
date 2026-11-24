#!/bin/bash
# П-42 TK-697 (PS-1003, условия критика до вердикта; без P&L, вне замка слота, юнит с квотой 25 % ЦП):
#   (1) прогон `gates` h775 на c06f6674 (бинарь якорей) — тот же, что дал 24 расхождения у 906c0999: если и у c06f6674 расхождения — эталон 19.11 устарел, не бинарь;
#   (2) замер сдвига линии lx = 4 за 30 с (tk697_slope.py) на сделках окна wa.
#   systemd-run --unit=tk697k-cond --collect -p Nice=17 -p CPUWeight=20 -p CPUQuota=25% -p MemoryMax=14G -p WorkingDirectory=/data/p42-tk697k/code bash tk697k_cond.sh
set -uo pipefail
PY=/opt/venv/bin/python
LOG=/data/p42-tk697k/log/cond.log
export TK674_UNI=c539 TK674_REBASE=1 TK674_LEVEL=1 TK680_SET=t697k PYTHONUTF8=1
echo "== $(date -u +%FT%TZ) gates c06f6674 (h775, root /data/p42-tk697c)" >> $LOG
mkdir -p /data/p42-tk697c/res /data/p42-tk697c/log
TK674_ROOT=/data/p42-tk697c TK674_BIN=/data/p42-tk688/bin/alpha-tk688-plain.c06f6674 $PY tk674_run.py gates >> $LOG 2>&1; echo "== gates rc=$?" >> $LOG
echo "== $(date -u +%FT%TZ) slope lx=4 wa" >> $LOG
TK674_ROOT=/data/p42-tk697k TK674_BIN=/data/p42-tk697g/bin/alpha-tk697-plain.906c0999 $PY tk697_slope.py >> $LOG 2>&1; echo "== slope rc=$?" >> $LOG
echo "COND-DONE $(date -u +%FT%TZ)" >> $LOG
