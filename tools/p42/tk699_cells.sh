#!/bin/bash
# TK-699 шаг 3: пересчёт клеток c539 на исправленных столбцах f12 — движок в слоте TK-674 (mkdir /data/p42-tk674c/slot.lock), собственный корень /data/p42-tk699/r674.
# Вход — tools/p42/tk699_plan.py plan (res/level674bs3t699-<сторона>.json, ftab6743t699/, res/recount-lists-<сторона>.json). Прежние корни, строки БД (метка 01.07–13.11, TK690-*) и
# res/thr674*.json не трогаются: новые строки — метка окна TK699-K3 (контроли) и TK699-f12 (пересчёт).
#   bash tk699_cells.sh <S|L> <k3|changed> <L-запись>      (запускать юнитом, окружение — env-rest-l.sh; код — /data/p42-tk674c/code-bd)
# Фаза k3: клетки без изменения единички на новой таблице → tables → сравнение с БД (trades, usd, sharpe, daily — точно); расхождение — стоп, код 7.
# Фаза changed: клетки с изменившейся единичкой → tables → ccheck. Слот — как у tk674_bd.sh; файл $R/HOLD — пауза; провал — маркер $R9/STOPPED и код ≠ 0.
set -uo pipefail
SIDE=$1; PHASE=$2; ENTRY=$3
PY=/opt/venv/bin/python
R=/data/p42-tk674c; R9=/data/p42-tk699/r674
LOG=$R9/log/cells699.log
MINFREE=${TK699_MIN_FREE_GB:-12}
HELD=0
mkdir -p "$R9/log" "$R9/res" "$R9/cells" "$R9/done" "$R9/tmp"
say() { echo "== $(date -u +%FT%TZ) $*" >> "$LOG"; }
free_gb() { df -BG --output=avail "$R" | tail -1 | tr -dc '0-9'; }
slot_get() { until mkdir "$R/slot.lock" 2>/dev/null; do sleep 5; done; HELD=1; say "$SIDE $PHASE слот взят"; }
slot_put() { if [ "$HELD" = 1 ]; then rmdir "$R/slot.lock" 2>/dev/null; HELD=0; say "$SIDE $PHASE слот отдан"; fi; }
finish() { local rc=$?; slot_put; if [ "$rc" -ne 0 ]; then say "!!! остановлен rc=$rc ($SIDE $PHASE)"; echo "$(date -u +%FT%TZ) rc=$rc $SIDE $PHASE" > "$R9/STOPPED"; fi; }
trap finish EXIT
rm -f "$R9/STOPPED"
stage() { say "$SIDE $PHASE $*"; "$PY" tk674_run.py "$@" >> "$LOG.$SIDE$PHASE" 2>&1 || { say "$SIDE $PHASE $* упал (rc=$?) — стоп"; exit 5; }; }
source "${TK674_ENVFILE:?}"
export TK674_ROOT=$R9 TK674_LEVEL=3 TK674_BATCH=t699 TK674_SIDES=$SIDE TK674_AUTHOR=TK-699 PYTHONUTF8=1 TK674_CANARY=0 TK674_THREADS=${TK699_THREADS:-6}
cp -n "$R"/res/thr674* "$R9/res/" 2>/dev/null; cp -n "$R/res/eqbins674.json" "$R9/res/" 2>/dev/null
SN=short; [ "$SIDE" = L ] && SN=long
case "$PHASE" in
  k3) export TK674_WIN=TK699-K3 ;;
  changed) export TK674_WIN=TK699-f12 ;;
  *) echo "фаза k3|changed"; exit 2 ;;
esac
"$PY" - <<EOF
import json
d = json.load(open("$R9/res/recount-lists-$SIDE.json", encoding="utf-8"))
json.dump(d["k3" if "$PHASE" == "k3" else "changed"], open("$R9/tmp/only-$PHASE-$SIDE.json", "w"))
EOF
export TK674_ONLY=$R9/tmp/only-$PHASE-$SIDE.json
while [ -e "$R/HOLD" ]; do sleep 30; done
f=$(free_gb); [ "$f" -ge "$MINFREE" ] || { say "ДИСК: свободно $f ГБ < $MINFREE — стоп"; exit 6; }
slot_get; stage cells; slot_put
stage tables --entry "$ENTRY"
if [ "$PHASE" = k3 ]; then
  "$PY" /data/p42-tk699/code/tk699_plan.py cmp --root "$R9" --side $SN --win-b TK699-K3 --list k3 >> "$LOG.$SIDE$PHASE" 2>&1 || { say "$SIDE K3: РАСХОЖДЕНИЕ — стоп"; exit 7; }
  say "$SIDE K3 PASS"
else
  stage ccheck
  touch "$R9/done/changed-$SIDE"
fi
say "$SIDE $PHASE готово"
