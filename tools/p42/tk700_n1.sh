#!/usr/bin/env bash
# TK-700 (srv, приказ координатора 25.11, дельта TK-692): окно n1 = 14.11–24.11 (T0 25.10), только A short — сокращённая цепочка признаков → движок PGO m3 (`run-mass.sh`) → mass_day (run-id h2n-A-short, сутки 317…327 от D0 = 01.01.2026).
# Шаги (юнитом, Nice ≥ 10, MemoryMax; один движок СЧЁТА (P&L) за раз — `mass` ждёт, пока идёт чтение TK-710 `tk710-thru-*`; лёгкая цепочка `chain` без P&L идёт рядом, приказ координатора 25.11 «parallel»):
#   prep   — вселенная n1 (c539 ∩ список TK-710), дерево из старых деревьев TK-621 и общего fwd/dl, `breakout convert`, ссылки /data/p42-tk621/n1/{cache,aggtrades,klines}
#   chain  — дампы сигналов (tk700_sig.sh all) → строки базы short (tk700_lvl.sh) → Rust-признаки (tk700_feat.sh: f12 tcount tsize tox fast all) → семейство coin + сборка (tk700_asm.sh n1 short coin)
#   mass   — plan, затем движок двумя частями (--part 0/2, 1/2; по 3 потока) и закрывающий проход (state=done)
# Использование: tk700_n1.sh prep | chain | mass
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
R=/data/p42-tk700/n1
LOG=/data/p42-tk700/log
CODE=/data/p42-tk700/code
PY=${TK700_PY:-/data/tk592/venv/bin/python}
BIN=${TK700_CONVERT_BIN:-/data/p42-tk616/alpha-mk.396989e9}            # тот же бинарь, что делал кэши деревьев TK-710 (`breakout convert`)
RUNID=${RUNID:-h2n-A-short}
export TK671_ROOT=/data/p42-tk700/p671 TK700_WINSET=n1 PYTHONUTF8=1
mkdir -p "$LOG"
say() { echo "$(date -u +%FT%TZ) n1 $*" | tee -a "$LOG/n1.log"; }
busy() { for u in "$@"; do systemctl is-active -q "$u" && return 0; done; return 1; }
wait_tk710() { while busy tk710-thru-1 tk710-thru-2 tk710-thru-3 tk710-thru-4; do sleep 30; done; }
case ${1:?prep|chain|mass} in
  prep)
    say "prep: старт"
    python3 "$HERE/tk700_win.py" universe --apply n1 || exit 1
    $PY "$HERE/tk700_n1tree.py" tree && $PY "$HERE/tk700_n1tree.py" check || { say "prep: дерево — сбой"; exit 1; }
    eval "$(python3 "$HERE/tk700_win.py" env n1)"
    P42_T0=$T0 "$BIN" breakout convert --src "$R/tree/klines" --out "$R/cache" --threads 4 > "$R/convert.log" 2>&1 || { say "prep: convert — сбой, $(tail -1 "$R/convert.log")"; exit 1; }
    python3 "$HERE/tk700_win.py" trees --apply n1 || exit 1
    say "prep: готово (T0 $T0, SIGSX $SIGSX, контрактов $(tr ',' '\n' < "$SYMS" | grep -c .))" ;;
  chain)
    [ "${N1_WAIT_TK710:-0}" = 1 ] && { say "chain: ждём чтение TK-710 (tk710-thru-*)"; wait_tk710; }
    say "chain: старт"
    S0=$(date +%s)
    bash "$HERE/tk700_sig.sh" n1 all /data/p42-tk700/prod/all-n1 3 || exit 1
    grep -q "^all n1 rc=0" /data/p42-tk700/prod/all-n1/done.log || { say "chain: дампы сигналов — сбой"; exit 1; }
    bash "$HERE/tk700_lvl.sh" n1 short || exit 1
    bash "$HERE/tk700_feat.sh" n1 short 2 "f12 tcount tsize tox fast all" || exit 1
    bash "$HERE/tk700_asm.sh" n1 short coin || exit 1
    [ -s "$TK671_ROOT/fam/coin/n1-short.parquet" ] || { say "chain: нет fam/coin/n1-short.parquet (семейство coin — сбой)"; exit 1; }
    [ -s "$TK671_ROOT/asm/n1-short.parquet" ] || { say "chain: нет asm/n1-short.parquet"; exit 1; }
    say "chain: готово за $(( $(date +%s) - S0 )) с" ;;
  mass)
    RUN="$CODE/run-mass.sh"
    ARGS="--cellset all1 --wins n1 --sides short --class A --vmax 800 --sigmax 26 --cap-gb 6"
    cd "$CODE" || exit 1
    say "mass: ждём чтение TK-710 (tk710-thru-*)"
    wait_tk710
    $PY tk700_mass.py plan --cellset all1 --wins n1 --sides short --class A > "$LOG/$RUNID.plan" 2>&1
    say "mass: plan — $(tail -3 "$LOG/$RUNID.plan" | tr '\n' ' ' | cut -c1-300)"
    systemctl set-property --runtime tk700.slice CPUQuota=${QUOTA:-700%} MemoryMax=40G
    for k in 0 1; do
      systemd-run --unit=tk700-n1-A-short-p$k --slice=tk700.slice -p LimitCORE=0 -p CPUWeight=800 -p Nice=10 -p MemoryMax=20G -E TK700_WINSET=n1 /bin/bash -c \
        "$RUN $RUNID $ARGS --threads 3 --part $k/2 >> $LOG/$RUNID-p$k.log 2>&1" > /dev/null
    done
    sleep 30
    while busy tk700-n1-A-short-p0 tk700-n1-A-short-p1; do sleep 30; done
    $RUN $RUNID $ARGS --threads 4 >> "$LOG/$RUNID-close.log" 2>&1
    say "mass: готово ($RUNID), $(sqlite3 /data/p42-tk700/mass/mass.sqlite "select state from mass_run where run_id='$RUNID'" 2>/dev/null)" ;;
  *) echo "режим: prep | chain | mass" >&2; exit 2 ;;
esac
