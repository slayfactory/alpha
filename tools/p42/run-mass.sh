#!/bin/bash
# usage: run-mass.sh <run-id> <args after run --run-id R ...>
cd /data/p42-tk700/code && export PYTHONUTF8=1 TK674_REBASE=1
R=$1; shift
/data/tk592/venv/bin/python tk700_mass.py run --run-id $R "$@"
