#!/usr/bin/env bash
# П-42 §13.71 (TK-671): ждёт конец Rust-признаков (fam/rust/ALLDONE) и Python-семейств (PYFAM-DONE в fam/pyfam.log), затем assemble 6 файлов → набор данных стороны с метками (tk671_data build) → G-lab (tk671_data glab).
# Использование: tk671_asm.sh   (юнит tk671-asm; журнал — /data/p42-tk671/fam/asm.log; конец — строка ASM-DONE). Значения меток не печатает (только числа ворот).
set -uo pipefail
C=/data/p42-tk671/code/p; PY=/data/tk592/venv/bin/python; F=/data/p42-tk671/fam; L=$F/asm.log
export PYTHONUTF8=1
until [ -e $F/rust/ALLDONE ] && grep -q PYFAM-DONE $F/pyfam.log; do sleep 30; done
for W in wa wb wc; do for S in long short; do
  T0=$(date +%s)
  $PY $C/tk671_assemble.py --win $W --side $S >> $L 2>&1; echo "asm $W $S rc=$? sec=$(( $(date +%s) - T0 ))" >> $L
done; done
for S in long short; do
  T0=$(date +%s)
  $PY $C/tk671_data.py build --side $S >> $L 2>&1; echo "data $S rc=$? sec=$(( $(date +%s) - T0 ))" >> $L
  $PY $C/tk671_data.py glab --side $S >> $L 2>&1; echo "glab $S rc=$?" >> $L
done
echo ASM-DONE >> $L
