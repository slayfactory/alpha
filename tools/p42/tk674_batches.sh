#!/bin/bash
# П-42 TK-674 (PS-990): остаток уровня 3 партиями с чистыми границами (`tk674_level.py split 3 <cap>` → res/level674<SUF2>3rNN-<сторона>.json; у партии одна сторона).
# Партия rNN: ftab → [замок слота] validate → cells → [замок снят] → tables + ccheck отдельным юнитом `tk674c-tab-rNN` (своя группа: MemoryMax 12G, CPUQuota 200 %, Nice 19 — не делит 40G с движком; пока идёт следующая партия). Слот движка — `mkdir <ROOT>/slot.lock`, как у TK-690 / TK-691 / TK-683:
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
ENVF=${TK674_ENVFILE:?TK674_ENVFILE — файл окружения шага (source в юните таблиц)}
HELD=0
release() { if [ "$HELD" = 1 ]; then rmdir "$R/slot.lock" 2>/dev/null; HELD=0; fi; }
trap release EXIT
say() { echo "== $(date -u +%FT%TZ) $*" >> "$LOG"; }
stage() { say "$TAG $*"; "$PY" tk674_run.py "$@" >> "$LOG" 2>&1 || { say "$TAG $* упал (rc=$?) — стоп"; exit 5; }; }
TABS=()
reap() {        # юниты tables + ccheck готовых партий: упавший — стоп (клетки на диске остаются, `tables` повторяется отдельно); пик памяти и CPU юнита — в журнал
  local keep=() u
  for u in ${TABS[@]+"${TABS[@]}"}; do
    if systemctl is-active -q "$u" || [ "$(systemctl is-active "$u")" = activating ]; then keep+=("$u"); continue; fi
    [ "$(systemctl show "$u" -p Result --value)" = success ] || { say "$u упал — стоп"; exit 4; }
    systemctl reset-failed "$u" 2>/dev/null; systemctl stop "$u" 2>/dev/null
  done
  TABS=(${keep[@]+"${keep[@]}"})
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
  say "$TAG tables + ccheck: юнит tk674c-tab-$TAG"
  systemd-run --unit="tk674c-tab-$TAG" -p MemoryMax=12G -p CPUQuota=200% -p Nice=19 -p OOMScoreAdjust=1000 -p WorkingDirectory="$PWD" bash -c     "source $ENVF; export PYTHONUTF8=1 TK674_BATCH=$TAG TK674_SIDES=$SIDE; echo '== '\$(date -u +%FT%TZ)' $TAG tables' >> $LOG; $PY tk674_run.py tables --entry $ENTRY >> $LOG 2>&1 && echo '== '\$(date -u +%FT%TZ)' $TAG ccheck' >> $LOG && $PY tk674_run.py ccheck >> $LOG 2>&1 && echo '== '\$(date -u +%FT%TZ)' $TAG tables + ccheck готово' >> $LOG; C=/sys/fs/cgroup\$(cut -d: -f3 /proc/self/cgroup); echo '== '\$(date -u +%FT%TZ)' $TAG юнит таблиц: пик памяти '\$((\$(cat \$C/memory.peak)/1048576))' МБ, CPU '\$((\$(awk '/usage_usec/{print \$2}' \$C/cpu.stat)/1000000))' с' >> $LOG" > /dev/null
  TABS+=("tk674c-tab-$TAG")
done
while [ ${#TABS[@]} -gt 0 ]; do sleep 20; reap; done           # последние партии: ждём юниты таблиц
say "партии $* — tables + ccheck готово"
