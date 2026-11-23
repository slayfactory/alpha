#!/usr/bin/env bash
# TK-693: пересчёт влияния осей одной командой. Копирует код на srv (/data/p42-tk689/code) и запускает юнит tk689-update
# (≤ 2 ядра, MemoryMax 8G, nice 10): считаются только уровни, чьи клетки в БД или код изменились с последнего прогона; результат — новый прогон в БД
# (infl_*, прежние остаются). Ждать: bash tools/srv-wait.sh unit:tk689-update 240 (фоном). Смотреть: python tools/p42/tk689_db.py runs | top | rollup.
#   bash tools/p42/tk689_update.sh [--levels 1,2,3,4]        # без аргументов — auto (только изменившиеся уровни)
set -euo pipefail
cd "$(dirname "$0")/../.."
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15 -o BatchMode=yes)
HOST=root@5.231.25.224
SHA=$(git rev-parse --short HEAD)
ARGS="${*:-}"
tar -c --transform 's,.*/,,' tools/p42/tk689_influence.py tools/p42/tk689_db.py tools/p42/tk689_report.py tools/p42/tk674_axes.py tools/p42/ml_features.json tools/chron.py |
  ssh "${KEY[@]}" "$HOST" "set -e; mkdir -p /data/p42-tk689/code && tar -x -C /data/p42-tk689/code
systemctl reset-failed tk689-update 2>/dev/null || true
cd /data/p42-tk689/code
systemd-run --unit=tk689-update -p CPUQuota=200% -p MemoryMax=8G -p Nice=10 -E OMP_NUM_THREADS=1 -E PYTHONUTF8=1 \
  /usr/bin/python3 tk689_influence.py compute --db /data/chron/chron.sqlite --code /data/p42-tk674c/code --git-sha $SHA $ARGS"
echo "ждать: bash tools/srv-wait.sh unit:tk689-update 240"
