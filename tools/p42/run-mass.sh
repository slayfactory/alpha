#!/bin/bash
# usage: run-mass.sh <run-id> <args after run --run-id R ...>
cd /data/p42-tk700/code && export PYTHONUTF8=1 TK674_REBASE=1
# движок TK-706 PGO m3 (LTO + abort + память + PGO, ворота: замороженный список 603 клетки без расхождений, критик PASS; до него memo 357a66c1, ворота 549 клеток побайтно равны, критик PASS, 25.11): подмена исполняемого файла, md5 сверяется в tk700_mass.py
export TK700_ENGINE=/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45 TK700_ENGINE_MD5=06367f45fad0d7c5c1031d82dfa03615
export TK700_HOLD_FILE=/data/p42-tk700/HOLD-a3      # новая партия ждёт, пока файл есть (окна TK-706)
R=$1; shift
/data/tk592/venv/bin/python tk700_mass.py run --run-id $R "$@"
