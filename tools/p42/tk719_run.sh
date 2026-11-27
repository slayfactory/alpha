#!/usr/bin/env bash
# TK-719 (PS-1040 + PS-1041) на srv: движок на 9 шорт-членах базы B (набор tk719b) — SEL (wa, wb, wc) и N1 со сохранением сделок; заморозка размера записана ДО запуска.
# Запуск: systemd-run --unit tk719-engine -p MemoryMax=40G -p CPUQuota=540% -p Nice=10 -p WorkingDirectory=/data/p42-tk718/code bash /data/p42-tk719/code/tk719_run.sh
# Повтор безопасен: готовые партии tk700_mass.py пропускает. rc проверяется по БД (`tk700_mass run` возвращает 0 и при rc ≠ 0 партии): mass_batch.rc, mass_skip, mass_cw.errs, mass_run.state.
set -uo pipefail
C=/data/p42-tk718/code
LOG=/data/p42-tk719/log
KEEP=/data/p42-tk719/trades
PY=/data/tk592/venv/bin/python
DB=/data/p42-tk718/mass718.sqlite
export PYTHONUTF8=1 TK674_REBASE=1
export TK700_ENGINE=/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45 TK700_ENGINE_MD5=06367f45fad0d7c5c1031d82dfa03615
THR=${THR:-7}; CAP=${CAP:-12}
cd "$C" || exit 1
mkdir -p "$LOG" "$KEEP"
T0=$(date +%s)
say() { echo "$(date -u +%FT%TZ) [$(( $(date +%s) - T0 )) s] $*" | tee -a "$LOG/run.log"; }
[ -f /data/p42-tk719/freeze/tk719-frozen.json ] || { say "СТОП: нет файла заморозки"; exit 2; }
$PY - <<PYEOF || exit 2
import sqlite3
c = sqlite3.connect("$DB")
c.execute("INSERT OR IGNORE INTO cell SELECT 'tk719b', cid, side, ckey, line, sig, filt, feats, ref_n, ref_usd, ref_daily, db_id, bin FROM cell WHERE cellset='tk718core' AND side='short' AND cid BETWEEN 7180015 AND 7180023")
c.commit()
n = c.execute("SELECT count(*) FROM cell WHERE cellset='tk719b'").fetchone()[0]
print("tk719b клеток", n)
raise SystemExit(0 if n == 9 else 1)
PYEOF
mass() {   # run-id, класс, остальные аргументы tk700_mass.py run
  local R=$1 K=$2; shift 2
  $PY tk700_mass.py run --db "$DB" --run-id "$R-$K" --class "$K" --vmax 800 --sigmax 26 --threads "$THR" --cap-gb "$CAP" "$@" >> "$LOG/$R-$K.log" 2>&1
  local rc=$?
  say "$R-$K rc=$rc: $(tail -1 "$LOG/$R-$K.log" | cut -c1-200)"
  return $rc
}
say "старт; движок $TK700_ENGINE"
mass tk719s-sel A --cellset tk719b --dry --wins wa,wb,wc --sides short --keep-trades "$KEEP" || exit 4
TK700_WINSET=n1 mass tk719n-n1 A --cellset tk719b --wins n1 --sides short --keep-trades "$KEEP" || exit 4
$PY - <<PYEOF || exit 5
import sqlite3
c = sqlite3.connect("$DB")
bad = 0
for r in ("tk719s-sel-A", "tk719n-n1-A"):
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
say "готово: сделки SEL / N1 в $KEEP"
touch /data/p42-tk719/ENGINE-DONE
