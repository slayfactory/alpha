#!/bin/bash
# П-42 TK-697 (третья поправка, набор t697k): единички ключей движка № 9 `tn` / `tno` и № 1 `lx = 4` — select → gates → ftab → validate → cells → tables → ccheck → map на бинаре `alpha-tk697-plain.906c0999`.
# Фазы: pre = select, gates (G-base бинаря, шесть баз h775), ftab, validate — юнитом с квотой, до слота; engine = только cells (под замком слота /data/p42-tk674c/slot.lock; бюджет — снаружи);
# post = tables, ccheck, map (после снятия замка, юнит с квотой); all = всё подряд (только для проверки скрипта).
#   bash tk697k_chain.sh L-<запись PS-1002> pre|engine|post
set -euo pipefail
ENTRY=$1; PHASE=${2:-all}
PY=/opt/venv/bin/python
export TK674_ROOT=/data/p42-tk697k TK674_UNI=c539 TK674_REBASE=1 TK674_LEVEL=1 TK680_SET=t697k TK674_AUTHOR=TK-697 PYTHONUTF8=1
export TK674_BIN=${TK674_BIN:-/data/p42-tk697g/bin/alpha-tk697-plain.906c0999} TK674_GBASE=${TK674_GBASE:-/data/p42-tk697k/res/gbase674.json}
mkdir -p $TK674_ROOT/res $TK674_ROOT/log
# равные бинари: 9387e0a9 / c06f6674 (якоря L-334) + новый 906c0999 — после ворот gate697d (200 файлов, 34 клетки при выключенных ключах, байт в байт против c06f6674; /data/p42-tk697g/res/gate697d.json)
cp -n /data/p42-tk680n/res/eqbins674.json $TK674_ROOT/res/ 2>/dev/null || true
"$PY" - <<'EOF'
import hashlib, json, os
f = os.environ["TK674_ROOT"] + "/res/eqbins674.json"
g = json.load(open("/data/p42-tk697g/res/gate697d.json", encoding="utf-8"))
m = hashlib.md5(open(os.environ["TK674_BIN"], "rb").read()).hexdigest()
assert g["bad"] == [] and g["bin_md5"] == m, "ворота gate697d не пройдены или бинарь другой"
eq = json.load(open(f, encoding="utf-8"))
if m not in eq:
    json.dump(eq + [m], open(f, "w", encoding="utf-8"))
print("eqbins", [x[:8] for x in json.load(open(f, encoding="utf-8"))])
EOF
LOG=$TK674_ROOT/log/chain.log
stage() { echo "== $(date -u +%FT%TZ) $*" >> "$LOG"; "$PY" tk674_run.py "$@" >> "$LOG" 2>&1; }
if [ "$PHASE" = all ] || [ "$PHASE" = pre ]; then
  echo "== $(date -u +%FT%TZ) select" >> "$LOG"; "$PY" tk680_singles.py select >> "$LOG" 2>&1
  stage gates
  stage ftab
  stage validate
fi
if [ "$PHASE" = all ] || [ "$PHASE" = engine ]; then
  stage cells                                             # стороны по очереди, один движок
fi
if [ "$PHASE" = all ] || [ "$PHASE" = post ]; then
  stage tables --entry "$ENTRY"
  stage ccheck
  echo "== $(date -u +%FT%TZ) map" >> "$LOG"; "$PY" tk680_singles.py map >> "$LOG" 2>&1
  echo "== $(date -u +%FT%TZ) chain готово" >> "$LOG"
fi
