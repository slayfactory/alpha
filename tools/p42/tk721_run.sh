#!/usr/bin/env bash
# TK-721 (PS-1043 + PS-1044) на srv: движок на 9 шорт-членах B со `slip` 2 и 3 (набор tk721s) — m02…m06, wa wb wc, n1 — и g0 (набор tk721g, строки без изменений) на n1; сделки сохраняются.
# Запуск (один движок за раз): systemd-run --unit tk721-engine --collect -p MemoryMax=40G -p CPUQuota=540% -p Nice=10 -p WorkingDirectory=/data/p42-tk718/code bash /data/p42-tk721/code/tools/p42/tk721_run.sh
# Повтор безопасен: готовые партии tk700_mass.py пропускает. rc проверяется по БД (`tk700_mass run` возвращает 0 и при rc ≠ 0 партии): mass_batch.rc, mass_skip, mass_cw.errs, mass_run.state.
set -uo pipefail
C=/data/p42-tk718/code
CODE=/data/p42-tk721/code/tools/p42
LOG=/data/p42-tk721/log
KEEP=/data/p42-tk721/trades
PY=/data/tk592/venv/bin/python
DB=/data/p42-tk721/mass721.sqlite
export PYTHONUTF8=1 TK674_REBASE=1
export TK700_ENGINE=/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45 TK700_ENGINE_MD5=06367f45fad0d7c5c1031d82dfa03615
THR=${THR:-7}; CAP=${CAP:-12}
cd "$C" || exit 1
mkdir -p "$LOG" "$KEEP"
T0=$(date +%s)
say() { echo "$(date -u +%FT%TZ) [$(( $(date +%s) - T0 )) s] $*" | tee -a "$LOG/run.log"; }
mass() {   # run-id, класс, остальные аргументы tk700_mass.py run
  local R=$1 K=$2; shift 2
  $PY tk700_mass.py run --db "$DB" --run-id "$R-$K" --class "$K" --vmax 800 --sigmax 26 --threads "$THR" --cap-gb "$CAP" "$@" >> "$LOG/$R-$K.log" 2>&1
  local rc=$?
  say "$R-$K rc=$rc: $(tail -1 "$LOG/$R-$K.log" | cut -c1-200)"
  return $rc
}
say "старт; движок $TK700_ENGINE"
$PY "$CODE/tk721_cells.py" build | tee -a "$LOG/run.log" || exit 2
TK700_WINSET=n1 mass tk721g-n1 A --cellset tk721g --wins n1 --sides short --keep-trades "$KEEP" || exit 4
TK700_WINSET=n1 mass tk721s-n1 A --cellset tk721s --wins n1 --sides short --keep-trades "$KEEP" || exit 4
mass tk721s-sel A --cellset tk721s --dry --wins wa,wb,wc --sides short --keep-trades "$KEEP" || exit 4
mass tk721s-h1 A --cellset tk721s --wins m02,m03,m04,m05,m06 --sides short --keep-trades "$KEEP" || exit 4
$PY - <<PYEOF || exit 5
import sqlite3
c = sqlite3.connect("$DB")
bad = 0
for r in ("tk721g-n1-A", "tk721s-n1-A", "tk721s-sel-A", "tk721s-h1-A"):
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
say "готово: сделки стресса в $KEEP"
touch /data/p42-tk721/ENGINE-DONE
