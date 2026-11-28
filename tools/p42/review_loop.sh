#!/usr/bin/env bash
# TK-745: цикл «правим код → смотрим сделки» на :8090 «Сделки», набор «30 дней» (по умолчанию — вкладка по умолчанию).
#   bash tools/p42/review_loop.sh [--bin <бинарь на srv>] [--keys "<k=v …>"] [--tag <метка>] [--lo ISO --hi ISO] [--app]
# Один круг: выкладывает код на srv → движок на окнах wc + n1 (юнит rv-run, `--keep-trades --keep-orders`) → строки сделок, заявки и уровни сетапа → data/trades страницы :8090.
# Печатает число сделок, Σ $ и время шагов; цель — ≤ 10 мин на круг (движок ≈ 20 с, упаковка ≈ 1 мин, остальное — выкладка).
#   --bin   бинарь движка на srv (по умолчанию /data/p42-tk743/bin/alpha-tk743; свой — `CHECK_HOST=srv BIN_OUT=… bash tools/vps-check.sh <дерево> binrel`)
#   --keys  ключи клетки поверх V0nt (`fh=0` — V1, TK-744); без ключей — ровно V0nt
#   --tag   метка прогона (каталог /data/p42-review/<метка>, по умолчанию d30)
#   --lo / --hi  сутки входа ISO (по умолчанию 2026-10-26 … 2026-11-24 — последние 30 суток подготовленных окон wc / n1)
#   --app   пересобрать и выложить приложение «Сделки» (София, ≈ 25 с) — после правок tools/p42/trades-app
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../_ssh.sh"
cd "$(dirname "$0")"
BIN=/data/p42-tk743/bin/alpha-tk743; KEYS=""; TAG=d30; LO=2026-10-26; HI=2026-11-24; APP=0
while [ $# -gt 0 ]; do
  case $1 in
    --bin) BIN=$2; shift 2;; --keys) KEYS=$2; shift 2;; --tag) TAG=$2; shift 2;; --lo) LO=$2; shift 2;; --hi) HI=$2; shift 2;; --app) APP=1; shift;;
    *) echo "неизвестный ключ $1"; sed -n 2,13p "$0"; exit 2;;
  esac
done
case $TAG in *[!A-Za-z0-9_-]*|"") echo "метка — латиница, цифры, _ и -"; exit 2;; esac
KEY=("${SSH_ID[@]}" -o ConnectTimeout=15 -o BatchMode=yes)
HOST=root@5.231.25.224
T0=$(date +%s)
lap() { echo "[$(( $(date +%s) - T0 )) с] $*"; }
if [ "$APP" = 1 ]; then
  bash trades-app/build.sh > /tmp/review-app-build.log 2>&1 || { tail -20 /tmp/review-app-build.log; exit 3; }
  tar -c -C trades-app/dist . | ssh "${KEY[@]}" "$HOST" "mkdir -p /data/pages/ml/trades-app && rm -f /data/pages/ml/trades-app/* && tar -x -C /data/pages/ml/trades-app"
  lap "приложение собрано и выложено"
fi
# код на srv (LF; каталоги и архивы не берём)
tar -cz *.py *.sh *.json *.js 2>/dev/null | ssh "${KEY[@]}" "$HOST" 'mkdir -p /data/p42-review/code && tar -xz -C /data/p42-review/code && cd /data/p42-review/code && sed -i "s/\r$//" *.py *.sh'
lap "код выложен"
ssh "${KEY[@]}" "$HOST" "systemd-run --quiet --wait --pipe --unit rv-run -p MemoryMax=40G -p CPUQuota=490% -p Nice=10 -E THR=${THR:-7} bash /data/p42-review/code/review_srv.sh '$TAG' '$BIN' '$KEYS' '$LO' '$HI'" 2>&1 | tee /tmp/review-loop.log
rc=${PIPESTATUS[0]}
lap "готово (rc=$rc): http://5.231.25.224:8090/#trades — вкладка «30 дней»"
exit "$rc"
