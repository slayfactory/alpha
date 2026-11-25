#!/usr/bin/env bash
# TK-710 (В-NNN; слова владельца 25.11: «ну по вчера скачай допрогони» … «форвард для завтра мы не делаем просто я вчера подгружаю»): повторяемое чтение замороженных клеток и линий «по вчера» —
# листинг → вселенная c539 (только crypto) → докачка в общий каталог `$R/dl` (скачанное по .sha256 и размеру пропускается) → REST-добор минуток → дерево + ворота + кэш → `tk616_forward.py thru`.
# Каждый прогон — новый id чтения `thru-<D>[-rN]`, прежние чтения и их каталоги остаются. Данные «завтра» не ждём: D — последние ЗАВЕРШЁННЫЕ и опубликованные сутки (по умолчанию вчера UTC).
# Запуск на srv под systemd-run (Nice ≥ 10, потолок памяти; движок сам берёт `--cap-gb` из P42_CAP_GB):
#   systemd-run --unit=tk710-thru --collect -p Nice=19 -p IOSchedulingClass=idle -p CPUQuota=200% -p MemoryMax=24G -p MemorySwapMax=0 \
#     -p StandardOutput=append:/data/p42-tk710/thru.log -p StandardError=append:/data/p42-tk710/thru.log bash /data/p42-tk710/code/tools/p42/tk710_thru.sh [D]
# Код — копия master (`git archive`), каталог `$C` (tools/p42 + docs/research/freeze). Только крипта: TradFi не скачивается и в дерево не входит (`universe.read = [crypto]`, В-175). Движок и P&L — только на шаге чтения.
set -uo pipefail
C=${C:-/data/p42-tk710/code}
CODE=$C/tools/p42
FZ=$C/docs/research/freeze
R=${R:-/data/p42-tk616/fwd}
PY=${PY:-/data/tk592/venv/bin/python}
BASE=${BASE:-/data/p42-tk621/universe}
BIN=${BIN:-/data/p42-tk616/alpha-mk.396989e9}                       # замороженный бинарь (md5 396989e9…)
THREADS=${THREADS:-2}
DL_THREADS=${DL_THREADS:-16}
export P42_CAP_GB=${P42_CAP_GB:-6}                                   # память движка: без него 24 ГБ → OOM (TK-708)
D=${1:-$(date -u -d yesterday +%F)}
[ "$(date -u +%F)" \> "$D" ] || { echo "СТОП: сутки $D ещё не закончились (UTC $(date -u +%F))" >&2; exit 2; }
RID=thru-$D
n=2
while [ -e "$R/READ-$RID.started" ] || [ -e "$R/$RID" ]; do RID=thru-$D-r$n; n=$((n + 1)); done
PREVU=$(ls -d "$R"/thru-*/universe 2>/dev/null | LC_ALL=C sort | tail -1)    # классы прироста — по предыдущему чтению (класс записан при первом появлении и не пересматривается)
P=$R/$RID
mkdir -p "$P/list" "$R/dl" "$R/rest"; : > "$P/log"
say() { echo "$(date -u +%FT%TZ) $RID $*" | tee -a "$P/log"; }
fail() { say "FAIL $*"; echo 1 > "$P/READY"; exit 1; }
say "старт: D=$D, предыдущее чтение: ${PREVU:-нет}"
WIN=$(PYTHONPATH=$CODE $PY - "$FZ/tk626-frozen.json" "$D" "$RID" <<'EOF'
import datetime, sys
import tk616_forward as fw
fz = fw.load_freeze(sys.argv[1])
print(",".join(fw.through_point(fz, sys.argv[2], sys.argv[3], datetime.date.today() + datetime.timedelta(days=1))["windows"]))
EOF
) || fail "окна чтения (охрана through_point)"
say "листинг S3 01.07…$D; окна $WIN"
python3 "$CODE/tk621_list.py" "$P/list" 2026-07-01 "$D" >> "$P/log" 2>&1 || fail list
# сутки D опубликованы полностью: число aggTrades-файлов суток D не меньше 99 % от суток D − 1 (иначе — задержка публикации, чтение «по вчера» ждёт)
PREVDAY=$(date -u -d "$D - 1 day" +%F)
ND=$(awk -F'\t' -v d="$D" '$1=="aggTrades" && $3==d' "$P/list/files.tsv" | wc -l)
NP=$(awk -F'\t' -v d="$PREVDAY" '$1=="aggTrades" && $3==d' "$P/list/files.tsv" | wc -l)
say "опубликовано aggTrades: $D — $ND файлов, $PREVDAY — $NP"
[ "$NP" -gt 0 ] && [ $((ND * 100)) -ge $((NP * 99)) ] || fail "сутки $D опубликованы не полностью ($ND из $NP) — прогон не начат, повтор позже"
python3 "$CODE/tk626_prep.py" universe --list-dir "$P/list" --base-dir "$BASE" --d "$D" --out "$P/universe" --only crypto ${PREVU:+--prev "$PREVU"} >> "$P/log" 2>&1 || fail universe
say "докачка (общий каталог $R/dl, возобновляемо)"
rm -f "$R/dl/log/miss.tsv"                                            # журнал неудач — только этого прогона (скачанное ранее пропускается по .sha256)
python3 "$CODE/tk621_dl.py" "$P/list" "$R/dl" "$DL_THREADS" >> "$P/log" 2>&1 || fail download
[ ! -s "$R/dl/log/miss.tsv" ] || say "НЕ СКАЧАНО (строк $(wc -l < "$R/dl/log/miss.tsv")): см. $R/dl/log/miss.tsv"
say "REST-добор провалов минуток"
python3 "$CODE/tk621_restfill.py" "$P/list" "$R/rest" >> "$P/log" 2>&1 || fail restfill
cp "$R/rest/log/restfill.tsv" "$P/restfill.tsv" 2>/dev/null || : > "$P/restfill.tsv"
say "деревья окон $WIN (crypto)"
python3 "$CODE/tk626_prep.py" tree --out "$P" --universe "$P/universe" --universe-name crypto --d "$D" --windows "$WIN" --allow-test --dl "$R/dl" --rest "$R/rest" --freeze "$FZ/tk626-frozen.json" >> "$P/log" 2>&1 || fail tree
python3 "$CODE/tk626_prep.py" check --out "$P" --universe "$P/universe" --universe-name crypto --d "$D" --windows "$WIN" --freeze "$FZ/tk626-frozen.json" >> "$P/log" 2>&1 || fail check
for w in ${WIN//,/ }; do
  T0=$($PY -c "import json; print([x for x in json.load(open('$FZ/tk626-frozen.json'))['windows'] if x['name']=='$w'][0]['t0'])")
  say "convert $w P42_T0=$T0"
  P42_T0=$T0 "$BIN" breakout convert --src "$P/$w/klines" --out "$P/$w/cache" --threads 4 >> "$P/log" 2>&1 || fail "convert $w"
done
echo 0 > "$P/READY"
say "данные готовы (READY 0): nofile $(cat "$P"/nofile-*.tsv | wc -l) файлов, restfill $(wc -l < "$P/restfill.tsv") строк"
sha() { PYTHONPATH=$CODE $PY -c "import sys, tk616_forward as fw; print(fw.sha256_text(sys.argv[1]))" "$1"; }
# commit-метки — коммиты заморозок (файлы не меняются); sha256 считается с самих файлов и сверяется охраной `check_freeze_committed` и звеньями
K=${K:-8af062fd056fa40e1f742a0aabd79a31416cd476}; K5=${K5:-1cd8e4f7fa35145aae629326bb941e211d7ee5bf}; K7=${K7:-332e93a8340875e1c27441e8073ba066145fbfcd}
cd "$C" || fail "нет $C"
say "чтение $RID"
TK616F_GO=1 $PY tools/p42/tk616_forward.py thru --through "$D" --read-id "$RID" --root "$R" \
  --bin "$BIN" --feat-bin /data/p42-tk638/bin/alpha-feat.69fcf7cd --ds-bin /data/p42-tk670/bin/alpha-lx.2815279d \
  --tk674-bin /data/p42-tk683/bin/alpha-tk706memo-plain.357a66c1 --f12-bin /data/p42-tk610/bin/alpha-feat-f12d-65519d8ff44e5be924f662609f9d54f7 \
  --flow-bin /data/p42-tk639/bin/alpha-feat.44ca3843 --tox-bin /data/p42-tk671/bin/alpha-feat-tox-ed78ac4c408073f5f7343538c99b1cf3 \
  --base-dir "$BASE" --tick-store /data/p42-tk599/tick-store --threads "$THREADS" \
  --freeze-commit "$K" --freeze-sha256 "$(sha "$FZ/tk626-frozen.json")" \
  --candidate-commit "$K" --candidate-sha256 "$(sha "$FZ/tk638-frozen.json")" --candidate2-commit "$K" --candidate2-sha256 "$(sha "$FZ/tk641-frozen.json")" \
  --candidate3-commit "$K" --candidate3-sha256 "$(sha "$FZ/tk670-frozen.json")" --candidate4-commit "$K" --candidate4-sha256 "$(sha "$FZ/tk691-frozen.json")" \
  --candidate5-commit "$K5" --candidate5-sha256 "$(sha "$FZ/tk708-frozen.json")" --tk674-commit "$K7" --tk674-sha256 "$(sha "$FZ/tk674-frozen.json")" >> "$P/log" 2>&1
rc=$?
say "чтение $RID завершено rc=$rc; итог — $R/point-$RID.json, отчёт — $R/report-$RID.md"
exit $rc
