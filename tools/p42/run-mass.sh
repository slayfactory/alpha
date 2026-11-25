#!/bin/bash
# usage: run-mass.sh <run-id> <args after run --run-id R ...>
cd /data/p42-tk700/code && export PYTHONUTF8=1 TK674_REBASE=1
# движок TK-706 memo (LTO + abort + память, ворота 549 клеток побайтно равны, критик PASS, 25.11): подмена исполняемого файла, md5 сверяется в tk700_mass.py
export TK700_ENGINE=/data/p42-tk683/bin/alpha-tk706memo-plain.357a66c1 TK700_ENGINE_MD5=357a66c14605c3a228d18defbe35bb22
export TK700_HOLD_FILE=/data/p42-tk700/HOLD-a3      # новая партия ждёт, пока файл есть (окна TK-706)
R=$1; shift
/data/tk592/venv/bin/python tk700_mass.py run --run-id $R "$@"
