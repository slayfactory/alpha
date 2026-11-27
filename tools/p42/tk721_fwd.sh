#!/usr/bin/env bash
# TK-721 (PS-1043 §7, В-185 «по вчера»): повторяемый форвард ненастроенной базы B лонга (9 членов TK-718) с 25.11 по вчера. Шорт-форвард TK-720 не трогается.
# Шаги (каждый — одна команда, повтор безопасен; D — последние ЗАВЕРШЁННЫЕ сутки UTC, по умолчанию вчера):
#   data [D]  — данные «по вчера»: те же, что у TK-720 (`tk720_fwd.sh data`, общий `dl`, `$W720/prep-$D/READY`); готовое не качается заново
#   mass      — движок tk700_mass на 9 лонг-членах (набор tk721l): SEL (wa wb wc, --dry; n1 нет — сборки n1-long в TK-700 нет, PS-1045) + проверка rc по БД; сделки → /data/p42-tk721/trades (для ворот L1 / L2)
#   run  [D]  — дерево n2 (T0 25.10 … D) → convert → движок лонга (+ шорт для ворот L3) → ряды и сделки в БД (tk721l_*) → отчёт (python3 tk721_fwd.py)
# Запуск на srv юнитом (один движок за раз, Nice ≥ 10):
#   systemd-run --unit=tk721-mass --collect -p Nice=10 -p CPUQuota=540% -p MemoryMax=40G -p WorkingDirectory=/data/p42-tk718/code bash /data/p42-tk721/code/tools/p42/tk721_fwd.sh mass
#   systemd-run --unit=tk721-run  --collect -p Nice=10 -p CPUQuota=540% -p MemoryMax=40G -p StandardOutput=append:/data/p42-tk721/log/fwd.log -p StandardError=append:/data/p42-tk721/log/fwd.log bash /data/p42-tk721/code/tools/p42/tk721_fwd.sh run
# Ожидание — `bash tools/srv-wait.sh unit:tk721-run 60` (фоном). Только crypto (В-175).
set -uo pipefail
MODE=${1:?data|mass|run}
C=${C:-/data/p42-tk721/code}
CODE=$C/tools/p42
W=${W:-/data/p42-tk721}
W720=${W720:-/data/p42-tk720}
PY=${PY:-/data/tk592/venv/bin/python}
D=${2:-$(date -u -d yesterday +%F)}
mkdir -p "$W/log"
case $MODE in
  data)
    C=/data/p42-tk720/code bash /data/p42-tk720/code/tools/p42/tk720_fwd.sh data "$D" ;;
  mass)
    cd /data/p42-tk718/code || exit 1
    LOG=$W/log; KEEP=$W/trades; DB=$W/mass721.sqlite
    export PYTHONUTF8=1 TK674_REBASE=1
    export TK700_ENGINE=/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45 TK700_ENGINE_MD5=06367f45fad0d7c5c1031d82dfa03615
    say() { echo "$(date -u +%FT%TZ) mass $*" | tee -a "$LOG/fwd.log"; }
    massrun() {   # run-id, остальные аргументы tk700_mass.py run
      local R=$1; shift
      $PY tk700_mass.py run --db "$DB" --run-id "$R" --class A --vmax 800 --sigmax 26 --threads 7 --cap-gb 12 --cellset tk721l --sides long --keep-trades "$KEEP" "$@" >> "$LOG/$R.log" 2>&1
      local rc=$?; say "$R rc=$rc: $(tail -1 "$LOG/$R.log" | cut -c1-200)"; return $rc
    }
    $PY "$CODE/tk721_cells.py" build-long | tee -a "$LOG/fwd.log" || exit 2
    massrun tk721l-sel-A --dry --wins wa,wb,wc || exit 4
    $PY - <<PYEOF || exit 5
import sqlite3
c = sqlite3.connect("$DB")
bad = 0
for r in ("tk721l-sel-A",):
    st = c.execute("SELECT state FROM mass_run WHERE run_id=?", (r,)).fetchone()
    nb = c.execute("SELECT count(*) FROM mass_batch WHERE run_id=? AND rc<>0", (r,)).fetchone()[0]
    ok = c.execute("SELECT count(*) FROM mass_batch WHERE run_id=? AND rc=0", (r,)).fetchone()[0]
    sk = c.execute("SELECT count(*) FROM mass_skip WHERE run_id=?", (r,)).fetchone()[0]
    er = c.execute("SELECT count(*) FROM mass_cw WHERE run_id=? AND errs<>''", (r,)).fetchone()[0]
    ncw = c.execute("SELECT count(*) FROM mass_cw WHERE run_id=?", (r,)).fetchone()[0]
    print(r, "state", st, "партий rc=0:", ok, "rc≠0:", nb, "mass_skip:", sk, "errs:", er, "клетка×окно:", ncw)
    if not (st and st[0] == "done" and nb == 0 and ok > 0 and sk == 0 and er == 0 and ncw > 0):
        bad += 1
raise SystemExit(1 if bad else 0)
PYEOF
    say "готово: сделки лонга в $KEEP/tk721l-sel-A"
    touch "$W/MASS-DONE" ;;
  run)
    [ "$(date -u +%F)" \> "$D" ] || { echo "СТОП: сутки $D ещё не закончились (UTC $(date -u +%F))" >&2; exit 2; }
    [ "$(cat "$W720/prep-$D/READY" 2>/dev/null)" = 0 ] || { echo "СТОП: нет $W720/prep-$D/READY = 0 — сначала data $D" >&2; exit 2; }
    : "${FREEZE_SHA256:?FREEZE_SHA256 — sha256 файла заморозки tk721-long-frozen.json}"
    cd "$CODE" || exit 1
    PYTHONUTF8=1 TK674_REBASE=1 $PY tk721_fwd.py run --through "$D" --freeze-sha256 "$FREEZE_SHA256" --dl /data/p42-tk616/fwd/dl --rest /data/p42-tk616/fwd/rest ;;
  *) echo "режим: data | mass | run" >&2; exit 2 ;;
esac
