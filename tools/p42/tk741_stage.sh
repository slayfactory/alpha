#!/usr/bin/env bash
# TK-741: пробная страница :8096 (юнит tk741-stage) — та же сборка, что боевая :8090, но свой каталог /data/p42-tk741/stage/site; боевую страницу и описи не трогает.
#   bash tools/p42/tk741_stage.sh        # заливает код, собирает данные «Сделки» и «Эквити» (вариант san), собирает index.html, поднимает сервер на 8096
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../_ssh.sh"
cd "$(dirname "$0")"
KEY=("${SSH_ID[@]}" -o ConnectTimeout=15)
HOST=root@5.231.25.224
S=/data/p42-tk741/stage
tar -c ml_features.schema.json tk593_page.py tk593_template.html tk684_fan.js tk741_trades.js ml_page_server.py | ssh "${KEY[@]}" "$HOST" "mkdir -p $S/src && tar -x -C $S/src"
tar -c tk684_equity.py tk684_metrics.py tk684_variants.json | ssh "${KEY[@]}" "$HOST" "mkdir -p $S/code && tar -x -C $S/code"
tar -c tk741_pagedata.py tk741_cluster.py tk741_lib.py tk741_trades.js tk736_an.py tk736_lib.py tk726_seam.py | ssh "${KEY[@]}" "$HOST" 'mkdir -p /data/p42-tk741/code && tar -x -C /data/p42-tk741/code'
ssh "${KEY[@]}" "$HOST" "bash -s" <<'REMOTE'
set -e
S=/data/p42-tk741/stage
mkdir -p $S/site/data
cp /data/pages/ml/data/features.json $S/site/data/features.json
ln -sfn /data/pages/ml/data/fan $S/site/data/fan
PY="systemd-run --quiet --wait --pipe -p Nice=19 -p MemoryMax=8G -p CPUQuota=200% -E PYTHONDONTWRITEBYTECODE=1 /opt/venv/bin/python"
$PY /data/p42-tk741/code/tk741_pagedata.py equity
$PY $S/code/tk684_equity.py /data/p42-tk696/engine $S/code/tk684_variants.json /data/chron/chron.sqlite $S/site/data/variants.json $S/site/data/variants_check.json
$PY /data/p42-tk741/code/tk741_pagedata.py trades $S/site
/opt/venv/bin/python $S/src/tk593_page.py $S/src/tk593_template.html $S/site
if ! systemctl is-active -q tk741-stage; then
  systemctl reset-failed tk741-stage 2>/dev/null || true
  systemd-run --quiet --unit=tk741-stage -p Restart=always /usr/bin/python3 $S/src/ml_page_server.py 8096 $S/site
fi
sleep 1; curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8096/
REMOTE
