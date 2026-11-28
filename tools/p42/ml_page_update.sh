#!/usr/bin/env bash
# Обновить страницу П-42 (srv:8090; вкладки «Эквити», «Признаки»):
#   bash tools/p42/ml_page_update.sh
# Заливает данные и исходники генератора на srv и пересобирает /data/pages/ml/index.html (tk593_page.py; JSON проверяется по схемам до записи страницы).
# «Признаки» (TK-606) — tools/p42/ml_features.json (схема ml_features.schema.json).
# «Эквити» (TK-684) — tools/p42/tk684_variants.json (вариант = одна запись + каталоги его сделок на srv); tk684_equity.py читает готовые сделки,
#   сверяет каждую линию с летописью (/data/chron/chron.sqlite) и принятыми числами — расхождение останавливает выкладку.
# «Сделки» (TK-741, В-192) — вкладка по сделкам базы шорта без выхода по времени и кластерам сетапа: данные data/trades/ строит tk741_pagedata.py из /data/p42-tk741 (БД, res/*.json, кэш минуток окон); вариант «база, без выхода по времени» в «Эквити» — каталоги /data/p42-tk741/page/{wa,wb,wc} (tk741_pagedata.py equity).
# Вид «Веер» — data/fan/ (tk684_fan.py: все клетки перебора c539 из летописи, ≈ 8 мин, юнит Nice 19, пик 3,4 ГБ); строится, если каталога ещё нет, пересчитать после новых клеток (четвёрки шорта, пересчёт bd): REBUILD_FAN=1 bash tools/p42/ml_page_update.sh
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../_ssh.sh"
cd "$(dirname "$0")"
KEY=("${SSH_ID[@]}" -o ConnectTimeout=15)
HOST=root@5.231.25.224
python -c "import json,sys; json.load(open('ml_features.json', encoding='utf-8'))" || { echo "ml_features.json — не JSON"; exit 2; }
tar -c ml_features.schema.json tk593_page.py tk593_template.html tk684_fan.js tk741_trades.js ml_page_server.py | ssh "${KEY[@]}" "$HOST" 'tar -x -C /data/pages/_src'
tar -c tk684_equity.py tk684_metrics.py tk684_variants.json tk684_fan.py | ssh "${KEY[@]}" "$HOST" 'mkdir -p /data/p42-tk624/code && tar -x -C /data/p42-tk624/code'
tar -c tk741_pagedata.py tk741_cluster.py tk741_lib.py tk741_trades.js tk736_an.py tk736_lib.py tk726_seam.py | ssh "${KEY[@]}" "$HOST" 'mkdir -p /data/p42-tk741/code && tar -x -C /data/p42-tk741/code'
# TK-684 / TK-680: не затирать опись признаков, выложенную из другой ветки и более новую (22.11: выкладка из ветки без TK-681 откатила вкладку «Признаки»;
# охрана по наибольшему № не ловила ветку с пропуском № 252–307) — заливаем, только если все имена живого файла есть в локальном
FT_NAMES='import json,sys; print(" ".join(x["name"] for s in json.load(open(sys.argv[1], encoding="utf-8"))["sets"] for g in s["groups"] for x in g["features"]))'
LIVE_NAMES=$(ssh "${KEY[@]}" "$HOST" "/opt/venv/bin/python -c '$FT_NAMES' /data/pages/ml/data/features.json" 2>/dev/null || echo "")
LOCAL_NAMES=$(python -c "$FT_NAMES" ml_features.json)
MISSING=$(python -c "import sys; print(' '.join(sorted(set(sys.argv[1].split()) - set(sys.argv[2].split()))))" "$LIVE_NAMES" "$LOCAL_NAMES")
if [ -n "$MISSING" ]; then echo "ml_features.json: на srv есть признаки, которых нет здесь ($(echo $MISSING | wc -w): $(echo $MISSING | cut -c1-80)…) — не заливаю, вкладка «Признаки» остаётся как есть"
else scp "${KEY[@]}" -q ml_features.json "$HOST:/data/pages/ml/data/features.json"; fi
ssh "${KEY[@]}" "$HOST" "REBUILD_FAN=${REBUILD_FAN:-0} bash -s" <<'REMOTE'
set -e
# TK-696: код чтения сделок — снимок кода TK-674 (вселенная c539 есть только там), берётся один раз; файлы TK-674 не трогаются (без .pyc)
[ -s /data/p42-tk696/engine/tk625_run.py ] || { mkdir -p /data/p42-tk696/engine /data/p42-tk696/cells && cp /data/p42-tk674c/code/*.py /data/p42-tk696/engine/; }
# TK-741: каталоги сделок варианта «база, без выхода по времени» и данные вкладки «Сделки» (из прогона и БД TK-741, движок не запускается)
systemd-run --quiet --wait --pipe -p Nice=19 -p MemoryMax=8G -p CPUQuota=200% -E PYTHONDONTWRITEBYTECODE=1 /opt/venv/bin/python /data/p42-tk741/code/tk741_pagedata.py equity
systemd-run --quiet --wait --pipe -p Nice=19 -p MemoryMax=6G -p CPUQuota=200% -E PYTHONDONTWRITEBYTECODE=1 /opt/venv/bin/python /data/p42-tk624/code/tk684_equity.py /data/p42-tk696/engine /data/p42-tk624/code/tk684_variants.json /data/chron/chron.sqlite /data/pages/ml/data/variants.json /data/pages/ml/data/variants_check.json   # TK-684: варианты для выбора (только чтение, сверка с летописью)
if [ "$REBUILD_FAN" = 1 ] || [ ! -s /data/pages/ml/data/fan/index.json ]; then
  rm -rf /data/pages/ml/data/fan.new && systemd-run --quiet --wait --pipe -p Nice=19 -p MemoryMax=6G -p CPUQuota=200% /opt/venv/bin/python /data/p42-tk624/code/tk684_fan.py /data/chron/chron.sqlite /data/pages/ml/data/fan.new | tail -3
  test -s /data/pages/ml/data/fan.new/index.json && rm -rf /data/pages/ml/data/fan.old && { [ ! -d /data/pages/ml/data/fan ] || mv /data/pages/ml/data/fan /data/pages/ml/data/fan.old; } && mv /data/pages/ml/data/fan.new /data/pages/ml/data/fan && rm -rf /data/pages/ml/data/fan.old
fi
systemd-run --quiet --wait --pipe -p Nice=19 -p MemoryMax=8G -p CPUQuota=200% -E PYTHONDONTWRITEBYTECODE=1 /opt/venv/bin/python /data/p42-tk741/code/tk741_pagedata.py trades /data/pages/ml   # TK-741: data/trades/ (сделки, кластеры, минутки ±2 ч)
# TK-696: сервер страницы — ml_page_server.py (Cache-Control: страница и описи no-cache, пакеты веера с ?v= — на год); перезапуск, если файл изменился или юнит не работает
M=$(md5sum /data/pages/_src/ml_page_server.py | cut -d' ' -f1)
if [ "$(cat /data/pages/_src/.server.md5 2>/dev/null)" != "$M" ] || ! systemctl is-active -q ml-page-8090; then
  systemctl stop ml-page-8090 2>/dev/null || true; systemctl reset-failed ml-page-8090 2>/dev/null || true
  systemd-run --quiet --unit=ml-page-8090 -p Restart=always /usr/bin/python3 /data/pages/_src/ml_page_server.py 8090 /data/pages/ml && echo "$M" > /data/pages/_src/.server.md5
fi
/opt/venv/bin/python /data/pages/_src/tk593_page.py /data/pages/_src/tk593_template.html /data/pages/ml
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8090/
REMOTE
