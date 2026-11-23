#!/bin/bash
# П-42 TK-674 (PS-988): остаток уровня 3 партиями с чистыми границами (`tk674_level.py split 3 <cap>` → res/level674<SUF2>3rNN-<сторона>.json; у партии одна сторона).
# Партия rNN: ftab → [замок слота] validate → cells → [замок снят] → tables + ccheck фоном (пока идёт следующая партия). Слот движка — `mkdir <ROOT>/slot.lock`, как у TK-690 / TK-691 / TK-683:
# замок держат только validate + cells партии; между партиями слот свободен (окно ≈ ftab следующей партии). Файл <ROOT>/HOLD — пауза перед следующей партией (снять — продолжит).
# Запуск юнитом (окружение — как в run-step4.sh, без TK674_SEQ):
#   systemd-run --unit=tk674c-rest --collect -p Nice=19 -p MemoryMax=40G -p OOMScoreAdjust=1000 -p WorkingDirectory=<код> bash <обёртка с окружением> tk674_batches.sh L-<запись таблиц> r01 r02 …
# Партии идут в данном порядке; готовая партия (запись в БД) повторно не считается (клетки в БД, метки done/). Журнал — log/chain<суффикс>r.log; маркеры «== <время> <партия> <стадия>».
set -uo pipefail
ENTRY=$1; shift
PY=/opt/venv/bin/python
R=${TK674_ROOT:-/data/p42-tk674}
export TK674_LEVEL=${TK674_LEVEL:-3}
SUF2=$("$PY" -c "import tk674_axes as ax; print(ax.SUF2)")
LOG=$R/log/chain${SUF2}x${TK674_LEVEL}r.log
HELD=0
release() { if [ "$HELD" = 1 ]; then rmdir "$R/slot.lock" 2>/dev/null; HELD=0; fi; }
trap release EXIT
say() { echo "== $(date -u +%FT%TZ) $*" >> "$LOG"; }
stage() { say "$TAG $*"; "$PY" tk674_run.py "$@" >> "$LOG" 2>&1; }
PIDS=()
reap() {        # фоновые tables + ccheck готовых партий: упавшие — стоп (клетки на диске остаются, `tables` повторяется отдельно)
  local keep=() p
  for p in ${PIDS[@]+"${PIDS[@]}"}; do
    if kill -0 "$p" 2>/dev/null; then keep+=("$p"); else wait "$p" || { say "tables / ccheck упали (pid $p) — стоп"; exit 4; }; fi
  done
  PIDS=(${keep[@]+"${keep[@]}"})
}
for TAG in "$@"; do
  reap
  SIDE=""
  for s in L S; do [ -e "$R/res/level674${SUF2}${TK674_LEVEL}${TAG}-$s.json" ] && SIDE=$SIDE$s; done
  if [ ${#SIDE} -ne 1 ]; then say "$TAG: нет файла уровня или больше одной стороны ($SIDE) — стоп"; exit 2; fi
  while [ -e "$R/HOLD" ]; do sleep 30; done
  export TK674_BATCH=$TAG TK674_SIDES=$SIDE
  stage ftab
  say "$TAG слот: ждём замок"
  until mkdir "$R/slot.lock" 2>/dev/null; do sleep 20; done
  HELD=1
  stage validate
  say "$TAG cells $SIDE"
  "$PY" tk674_run.py cells >> "$LOG.$TAG" 2>&1 || { say "$TAG cells $SIDE упали (rc=$?) — стоп"; exit 3; }
  release
  say "$TAG cells $SIDE готово; слот свободен"
  ( stage tables --entry "$ENTRY" && stage ccheck && say "$TAG tables + ccheck готово" ) &
  PIDS+=($!)
done
RC=0
for p in "${PIDS[@]}"; do wait "$p" || RC=1; done
say "партии $* — tables + ccheck rc=$RC"
exit $RC
