#!/usr/bin/env bash
# TK-720 (В-185 «по вчера», слова владельца 25.11: «ну по вчера скачай допрогони»): повторяемый форвард ненастроенной базы B шорта (9 членов TK-718) с 25.11 по вчера.
# Шаги (каждый — одна команда, повтор безопасен; D — последние ЗАВЕРШЁННЫЕ сутки UTC, по умолчанию вчера):
#   data [D]  — листинг S3 → вселенная crypto → докачка в общий `$R/dl` (скачанное по .sha256 пропускается) → REST-добор минуток → `$P/READY`
#   run  [D]  — дерево окна n2 (T0 25.10 … D) → `breakout convert` → движок на 9 членах → ряды и сделки в БД → отчёт (python3 tk720_fwd.py)
# Запуск на srv юнитом (один движок за раз, Nice ≥ 10):
#   systemd-run --unit=tk720-data --collect -p Nice=19 -p IOSchedulingClass=idle -p MemoryMax=24G -p StandardOutput=append:/data/p42-tk720/log/data.log -p StandardError=append:/data/p42-tk720/log/data.log bash /data/p42-tk720/code/tools/p42/tk720_fwd.sh data
#   systemd-run --unit=tk720-run  --collect -p Nice=10 -p CPUQuota=540% -p MemoryMax=40G -p StandardOutput=append:/data/p42-tk720/log/run.log -p StandardError=append:/data/p42-tk720/log/run.log bash /data/p42-tk720/code/tools/p42/tk720_fwd.sh run
# Ожидание — `bash tools/srv-wait.sh unit:tk720-run 60` (фоном). Только crypto (В-175); TradFi не скачивается.
set -uo pipefail
MODE=${1:?data|run}
C=${C:-/data/p42-tk720/code}
CODE=$C/tools/p42
R=${R:-/data/p42-tk616/fwd}                                          # общий каталог докачки TK-710 (`dl`, `rest`)
W=${W:-/data/p42-tk720}
PY=${PY:-/data/tk592/venv/bin/python}
BASE=${BASE:-/data/p42-tk621/universe}
FREEZE_SHA256=${FREEZE_SHA256:-46a261896e5b4ba47bdd8d2832485b31e189eb7f41f53d6deea3de05caeffa4b}   # sha256 файла заморозки (PS-1042, L-462); другой файл — отказ
D=${2:-$(date -u -d yesterday +%F)}
[ "$(date -u +%F)" \> "$D" ] || { echo "СТОП: сутки $D ещё не закончились (UTC $(date -u +%F))" >&2; exit 2; }
P=$W/prep-$D
mkdir -p "$P/list" "$R/dl" "$R/rest" "$W/log"
say() { echo "$(date -u +%FT%TZ) $MODE $D $*" | tee -a "$P/prep.log"; }
fail() { say "FAIL $*"; echo 1 > "$P/READY"; exit 1; }
case $MODE in
  data)
    rm -f "$P/READY"
    PREVU=$(ls -d "$R"/thru-*/universe 2>/dev/null | LC_ALL=C sort | tail -1)    # классы прироста — по предыдущему чтению (как tk710_thru.sh)
    say "старт, предыдущая вселенная: ${PREVU:-нет}"
    python3 "$CODE/tk621_list.py" "$P/list" 2026-07-01 "$D" >> "$P/prep.log" 2>&1 || fail list
    PREVDAY=$(date -u -d "$D - 1 day" +%F)
    ND=$(awk -F'\t' -v d="$D" '$1=="aggTrades" && $3==d' "$P/list/files.tsv" | wc -l)
    NP=$(awk -F'\t' -v d="$PREVDAY" '$1=="aggTrades" && $3==d' "$P/list/files.tsv" | wc -l)
    say "опубликовано aggTrades: $D — $ND файлов, $PREVDAY — $NP"
    [ "$NP" -gt 0 ] && [ $((ND * 100)) -ge $((NP * 99)) ] || fail "сутки $D опубликованы не полностью ($ND из $NP) — повтор позже"
    python3 "$CODE/tk626_prep.py" universe --list-dir "$P/list" --base-dir "$BASE" --d "$D" --out "$P/universe" --only crypto ${PREVU:+--prev "$PREVU"} >> "$P/prep.log" 2>&1 || fail universe
    say "докачка (общий каталог $R/dl, возобновляемо)"
    rm -f "$R/dl/log/miss.tsv"
    python3 "$CODE/tk621_dl.py" "$P/list" "$R/dl" 16 >> "$P/prep.log" 2>&1 || fail download
    [ ! -s "$R/dl/log/miss.tsv" ] || say "НЕ СКАЧАНО (строк $(wc -l < "$R/dl/log/miss.tsv")): см. $R/dl/log/miss.tsv"
    say "REST-добор провалов минуток"
    python3 "$CODE/tk621_restfill.py" "$P/list" "$R/rest" >> "$P/prep.log" 2>&1 || fail restfill
    echo 0 > "$P/READY"
    say "данные готовы (READY 0)" ;;
  run)
    [ "$(cat "$P/READY" 2>/dev/null)" = 0 ] || { echo "СТОП: нет $P/READY = 0 — сначала data $D" >&2; exit 2; }
    cd "$CODE" || exit 1
    PYTHONUTF8=1 TK674_REBASE=1 $PY tk720_fwd.py run --through "$D" --freeze-sha256 "$FREEZE_SHA256" --dl "$R/dl" --rest "$R/rest" ;;
  *) echo "режим: data | run" >&2; exit 2 ;;
esac
