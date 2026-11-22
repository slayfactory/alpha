#!/bin/bash
# П-42 TK-680: дельты единичек по очереди, один движок — история пробоев (§13.73.8), сетки шире (§13.73.9), оси контекста (§13.73.13). Каждая — только при флаге GO
# (в нём L-запись отбора). Запуск юнитом с именем tk680-chain (его ждут очереди TK-674 / TK-681), после юнитов TK-674 / TK-679:
#   systemd-run --unit=tk680-chain --collect -p Nice=19 -p MemoryMax=40G -p OOMScoreAdjust=1000 -p WorkingDirectory=/data/p42-tk680/code bash -c "<ожидание>; bash tk680_delta.sh"
for x in h:hist g:grid c:ctx; do
  D=/data/p42-tk680${x%%:*}; S=${x##*:}
  if [ -f $D/GO ]; then
    (cd $D/code && TK680_SET=$S TK680_ROOT=$D bash tk680_chain.sh "$(cat $D/GO)") || echo "== $(date -u +%FT%TZ) $S: сбой" >> $D/log/chain.log
  fi
done
