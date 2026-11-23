#!/usr/bin/env bash
# TK-700: копия tools/p42 на srv в /data/p42-tk700/code (плоско, как /data/p42-tk674c/code); код движка / признаков — бинарники TK-699 (/data/p42-tk699/bin/alpha-feat-tf).
# Использование (с ПК, из корня репозитория): bash tools/p42/tk700_deploy.sh
set -euo pipefail
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes -o ConnectTimeout=15)
tar -C tools/p42 --exclude=__pycache__ -cf - . | ssh "${KEY[@]}" root@5.231.25.224 "mkdir -p /data/p42-tk700/code && tar -C /data/p42-tk700/code -xf - && ls /data/p42-tk700/code | wc -l"
