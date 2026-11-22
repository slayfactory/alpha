#!/usr/bin/env bash
# Обновить страницу П-42 (srv:8090; вкладки «Цели TK-598», «Эквити», «Признаки»):
#   bash tools/p42/ml_page_update.sh
# Заливает данные и исходники генератора на srv и пересобирает /data/pages/ml/index.html (tk593_page.py; JSON проверяется по схемам до записи страницы).
# «Цели TK-598» — tools/p42/ml_progress.json → targets (схема ml_progress.schema.json). «Признаки» (TK-606) — tools/p42/ml_features.json (схема ml_features.schema.json).
# «Эквити» (TK-684) — tools/p42/tk684_variants.json (вариант = одна запись + каталоги его сделок на srv); tk684_equity.py читает готовые сделки,
#   сверяет каждую линию с летописью (/data/chron/chron.sqlite) и принятыми числами — расхождение останавливает выкладку.
set -euo pipefail
cd "$(dirname "$0")"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
HOST=root@5.231.25.224
python -c "import json,sys; json.load(open('ml_progress.json', encoding='utf-8'))" || { echo "ml_progress.json — не JSON"; exit 2; }
python -c "import json,sys; json.load(open('ml_features.json', encoding='utf-8'))" || { echo "ml_features.json — не JSON"; exit 2; }
tar -c ml_progress.schema.json ml_features.schema.json tk593_page.py tk593_template.html | ssh "${KEY[@]}" "$HOST" 'tar -x -C /data/pages/_src'
tar -c tk684_equity.py tk684_metrics.py tk684_variants.json | ssh "${KEY[@]}" "$HOST" 'mkdir -p /data/p42-tk624/code && tar -x -C /data/p42-tk624/code'
scp "${KEY[@]}" -q ml_progress.json "$HOST:/data/pages/ml/data/progress.json"
# TK-684: не затирать опись признаков, выложенную из другой ветки и более новую (22.11: выкладка из ветки без TK-681 откатила вкладку «Признаки») — сравниваем наибольший № признака
FT_N='import json,sys; print(max(x["n"] for s in json.load(open(sys.argv[1], encoding="utf-8"))["sets"] for g in s["groups"] for x in g["features"]))'
LIVE_N=$(ssh "${KEY[@]}" "$HOST" "/opt/venv/bin/python -c '$FT_N' /data/pages/ml/data/features.json" 2>/dev/null || echo 0)
LOCAL_N=$(python -c "$FT_N" ml_features.json)
if [ "${LIVE_N:-0}" -gt "$LOCAL_N" ]; then echo "ml_features.json: на srv опись новее (№ до $LIVE_N, здесь до $LOCAL_N) — не заливаю, вкладка «Признаки» остаётся как есть"
else scp "${KEY[@]}" -q ml_features.json "$HOST:/data/pages/ml/data/features.json"; fi
ssh "${KEY[@]}" "$HOST" bash -s <<'REMOTE'
set -e
/opt/venv/bin/python /data/p42-tk624/code/tk684_equity.py /data/p42-tk639/code /data/p42-tk624/code/tk684_variants.json /data/chron/chron.sqlite /data/pages/ml/data/variants.json /data/pages/ml/data/variants_check.json   # TK-684: варианты для выбора (только чтение, сверка с летописью)
/opt/venv/bin/python /data/pages/_src/tk593_page.py /data/pages/_src/tk593_template.html /data/pages/ml
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8090/
REMOTE
