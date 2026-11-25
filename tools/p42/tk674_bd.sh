#!/bin/bash
# П-42 TK-674 (PS-996): пересчёт bd-клеток партий шага 4 на xb-таблицах. На партию: build-batch (контроли а, а', б, г: tk674_xb.py) → lists (K3 — 30 клеток без bd; bd-клетки партии) →
# K3 на xb-таблице (точное равенство с БД, любое расхождение — стоп) → bd-клетки (метка окна TK690-xb) → tables + ccheck. Корень счёта — /data/p42-tk674c-x690b (общий с PS-995),
# таблица партии — ссылка ftab6743<партия> на /data/p42-tk674c-xb/ftab6743<партия>xb; прежние строки и m не трогаются (другая метка окна).
# Слот движка — mkdir $R/slot.lock (как tk674_batches.sh; держат только cells). Файл $R/HOLD — пауза перед партией. Провал любого шага — маркер $R/BD-STOPPED и код ≠ 0.
# Запуск юнитом (окружение — env-rest-l.sh, бинарь l; TK674_ENVFILE — тот же файл):
#   TK674_ENVFILE=/data/p42-tk674c/env-rest-l.sh systemd-run --unit=tk674c-bd --collect -p Nice=19 -p MemoryMax=40G -p OOMScoreAdjust=1000 -p WorkingDirectory=<код> bash <обёртка> tk674_bd.sh L-<запись> base r02 r03 …
# `base` — таблица основы шага 4 (ftab6743). TK674_BD_DRY=1 — без движка: печатает команды стадий cells.
set -uo pipefail
ENTRY=$1; shift
PY=/opt/venv/bin/python
R=/data/p42-tk674c; R5=/data/p42-tk674c-x690b; XB=/data/p42-tk674c-xb
LOG=$R/log/bd674.log
MINFREE=${TK674_MIN_FREE_GB:-12}
HELD=0
say() { echo "== $(date -u +%FT%TZ) $*" >> "$LOG"; }
free_gb() { df -BG --output=avail "$R" | tail -1 | tr -dc '0-9'; }
guard() { local f; f=$(free_gb); [ "$f" -ge "$MINFREE" ] || { say "$TAG ДИСК: свободно $f ГБ < $MINFREE — стоп"; exit 6; }; }
slot_get() { until mkdir "$R/slot.lock" 2>/dev/null; do sleep 5; done; HELD=1; say "$TAG слот взят"; }
slot_put() { if [ "$HELD" = 1 ]; then rmdir "$R/slot.lock" 2>/dev/null; HELD=0; say "$TAG слот отдан"; fi; }
finish() { local rc=$?; slot_put; if [ "$rc" -ne 0 ]; then say "!!! bd-пересчёт ОСТАНОВЛЕН rc=$rc на партии ${TAG:-?} — журнал $LOG.${TAG:-?}"; echo "$(date -u +%FT%TZ) rc=$rc партия ${TAG:-?}" > "$R/BD-STOPPED"; fi; }
trap finish EXIT
rm -f "$R/BD-STOPPED"
stage() { say "$TAG $*"; "$PY" tk674_run.py "$@" >> "$LOG.$TAG" 2>&1 || { say "$TAG $* упал (rc=$?) — стоп"; exit 5; }; }
cells_stage() {
  if [ -n "${TK674_BD_DRY:-}" ]; then say "$TAG DRY: cells WIN=$TK674_WIN ONLY=$TK674_ONLY ($(python3 -c "import json;print(len(json.load(open('$TK674_ONLY'))))") cid)"; return 0; fi
  guard; slot_get; stage cells; slot_put
}
for TAG in "$@"; do
  T=$TAG; [ "$TAG" = base ] && T=""
  [ -e "$R5/done/bd-$TAG" ] && { say "$TAG уже пересчитана (done/bd-$TAG) — пропуск"; continue; }
  while [ -e "$R/HOLD" ]; do sleep 30; done
  mkdir -p "$R5/res" "$R5/cells" "$R5/done" "$R5/log" "$R5/tmp"
  say "$TAG build-batch (контроли а, а', б, г)"
  ( source "${TK674_ENVFILE:?}"; cd "$PWD"; TK674XB_ROOT=$XB PYTHONUTF8=1 "$PY" tk674_xb.py build-batch --tag "$T" ) >> "$LOG.$TAG" 2>&1 || { say "$TAG build-batch: контроль не пройден — стоп"; exit 6; }
  PYTHONUTF8=1 "$PY" tk674_xb.py lists --tag "$T" --out-dir "$R5/tmp" >> "$LOG.$TAG" 2>&1 || { say "$TAG lists — стоп"; exit 6; }
  for f in "level674bs3$T-S.json" "invalid674bs3$T-S.json" "ftabcols6743$T-S.json" eqbins674.json; do [ -e "$R/res/$f" ] && cp -n "$R/res/$f" "$R5/res/"; done
  cp -n "$R"/res/thr674* "$R5/res/" 2>/dev/null
  ln -sfn "$XB/ftab6743${T}xb" "$R5/ftab6743$T"
  source "$TK674_ENVFILE"
  export PYTHONUTF8=1 TK674_ROOT=$R5 TK674_LEVEL=3 TK674_BATCH=$T TK674_SIDES=S
  # K3: 30 клеток без bd на xb-таблице — точное равенство с БД
  export TK674_WIN=TK690-K3 TK674_ONLY=$R5/tmp/k3-$T.json
  cells_stage
  if [ -z "${TK674_BD_DRY:-}" ]; then
    stage tables --entry "$ENTRY"
    TK674_CMP_PLAN=$R5/res/level674bs3$T-S.json env -u TK674_WIN -u TK674_ONLY "$PY" tk674_recount.py cmp main TK690-K3 "$R5/tmp/k3-$T.json" --stop >> "$LOG.$TAG" 2>&1 || { say "$TAG K3: РАСХОЖДЕНИЕ — стоп"; exit 7; }
    say "$TAG K3 PASS"
  fi
  # bd-клетки партии (метка TK690-xb)
  export TK674_WIN=TK690-xb TK674_ONLY=$R5/tmp/bd-$T.json
  if [ "$("$PY" -c "import json;print(len(json.load(open('$TK674_ONLY'))))")" = 0 ]; then say "$TAG bd-клеток нет — пропуск"; touch "$R5/done/bd-$TAG"; continue; fi
  cells_stage
  if [ -z "${TK674_BD_DRY:-}" ]; then
    stage tables --entry "$ENTRY"
    stage ccheck
    touch "$R5/done/bd-$TAG"
  fi
  say "$TAG готово"
done
say "bd-пересчёт $* — готово"
