#!/bin/bash
# TK-674: тройки (остаток шорта) и затем пары оси split — строго по очереди (22.11: два движка по 26 проходов сигналов вместе не помещаются в память)
cd /data/p42-tk674/code
TK674_REBASE=1 TK674_LEVEL=3 bash tk674_chain.sh L-301
TK674_REBASE=1 TK674_SPLIT=1 TK674_BIN=/data/p42-tk677/bin/alpha-tk677.93cb51a9 TK674_GBASE=/data/p42-tk677/res/gbase674.json bash tk674_chain.sh L-299
