#!/usr/bin/env bash
# TK-700 (srv): дампы сигналов движка для подготовки признаков — ВСЕГДА P42_SIGSX_ONLY=1 (портфель и P&L не считаются; на деревьях отложенной выборки иначе охрана `tk700_win` отказывает).
# Режимы (те же варианты, что tk671_sig.sh + tk671_twin.sh; окно берётся из `tk700_win.py env`):
#   sig-long | sig-short — 12 источников уровней (p30/60/240 × n_bounce 1–3, lxD / lxW / lxP) одной стороны, P42_LEVELS=1 (дампы + файлы уровней: один проход вместо sig + siglv)
#                          → <out>/dumps/<источник>/<side>-{sigsx,levels}.csv
#   all                  — 12 источников (оба направления, P42_LEVELS=1) + L_ind / S_ind / D_ind ОДНИМ вызовом движка: фаза 1 (чтение тиков) общая; сигналы источников от выходных ключей (rf / rs / tt …) не зависят — те же дампы, что у sig-long / sig-short / ind по отдельности
#                          (сверяется на wa с `prod/siglv-wa-*` TK-671 байт в байт) → <out>/dumps/<вариант>/<side>-{sigsx,levels}.csv
#   base                 — варианты L_base / S_base (дамп кандидатов, как tk625_run `cmd_eng`, но P42_SIGSX_ONLY=1) → <out>/dumps/{L_base,S_base}/<side>-sigsx.csv: строки таблиц TK-654 / TK-634 (класс B, дыра G-key; `tk700_gkey.sh`)
#   ind                  — варианты баз L_ind / S_ind / D_ind (фильтры двойников tk671_twin.sh) → <out>/dumps/<вариант>/<side>-sigsx.csv: ключи (sym, sig_ms) строк базы вместо сделок / entryrej двойников
# Использование: tk700_sig.sh <окно m01…m06|wa|wb|wc|wa20> <all|sig-long|sig-short|ind> <каталог выхода> [потоки]
# Переменные: TK671_BIN (бинарь), TK700_SIGSX (<от>:<до>, по умолчанию из окна), TK700_SIGCACHE, TK700_SYMS (файл контрактов, по умолчанию из окна)
set -uo pipefail
WIN=${1:?окно}; MODE=${2:?режим}; OUT=${3:?каталог выхода}; THR=${4:-3}
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=${TK671_BIN:-/data/p42-tk671/bin/alpha-lx-sides}
eval "$(python3 "$HERE/tk700_win.py" env "$WIN")"
SIGSX=${TK700_SIGSX:-$SIGSX}; SYMS=${TK700_SYMS:-$SYMS}
COINS=$(cat "$SYMS")
STORE="--tick-store /data/p42-tk599/tick-store --tick-store-cap-gb 3 --tick-store-min-free-gb 5"
SIGCACHE=${TK700_SIGCACHE:-/data/p42-tk700/sigcache/$WIN}
mkdir -p "$OUT" "$SIGCACHE"
BASE="lat=4 latx=4 natr_min=0 t1h=0 ew=5 ex=0"
unset P42_SIDES P42_LEVELS
case $MODE in
  sig-long|sig-short)
    SIDE=${MODE#sig-}
    if [ "$SIDE" = long ]; then EX="rf=0.2 rs=20"; else EX="tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05"; fi
    { for tf in 30 60 240; do for nb in 1 2 3; do echo "p${tf}n${nb} $BASE $EX tf=$tf n_bounce=$nb"; done; done
      echo "lxD $BASE $EX lx=1 tf=60"
      echo "lxW $BASE $EX lx=2 tf=60 lw=$LW"
      echo "lxP $BASE $EX lx=3 tf=60"; } > "$OUT/variants.txt"
    export P42_SIDES=$SIDE P42_LEVELS=1 ;;
  all)
    EX="rf=0.2 rs=20"        # ключи выхода в SIGSX_ONLY не участвуют; значения — как у лонга (сторона у источников — обе: P42_SIDES не задан)
    { for tf in 30 60 240; do for nb in 1 2 3; do echo "p${tf}n${nb} $BASE $EX tf=$tf n_bounce=$nb"; done; done
      echo "lxD $BASE $EX lx=1 tf=60"
      echo "lxW $BASE $EX lx=2 tf=60 lw=$LW"
      echo "lxP $BASE $EX lx=3 tf=60"; } > "$OUT/variants.txt"
    IND="p=100000 c=0 m=1000000 s_glob=1000000 ww=0 busy=0 reentry=2"
    LB="lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20 t24=12"
    SB="lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05 t24=12"
    printf 'L_ind %s %s
S_ind %s %s
D_ind %s lx=1 tf=60 %s
' "$LB" "$IND" "$SB" "$IND" "$SB" "$IND" >> "$OUT/variants.txt"
    export P42_LEVELS=1 ;;
  ind)
    IND="p=100000 c=0 m=1000000 s_glob=1000000 ww=0 busy=0 reentry=2"
    LB="lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20 t24=12"
    SB="lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05 t24=12"
    printf 'L_ind %s %s\nS_ind %s %s\nD_ind %s lx=1 tf=60 %s\n' "$LB" "$IND" "$SB" "$IND" "$SB" "$IND" > "$OUT/variants.txt" ;;
  base)
    LB="lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20 t24=12"
    SB="lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05 t24=12"
    printf 'L_base %s
S_base %s
' "$LB" "$SB" > "$OUT/variants.txt" ;;
  *) echo "режим: all | sig-long | sig-short | ind | base" >&2; exit 2 ;;
esac
S=$(date +%s)
P42_T0=$T0 P42_SIGSX_ONLY=1 P42_SIGSX=$SIGSX "$BIN" breakout tick --cache "$TREE/cache" --trades "$TREE/aggtrades" --variants "$OUT/variants.txt" --symbols "$COINS" --out "$OUT/dumps" \
  --threads "$THR" --cap-gb 4 --sig-cache "$SIGCACHE" $STORE > "$OUT/sig.log" 2>&1
echo "$MODE $WIN rc=$? sec=$(( $(date +%s) - S )) sigsx=$SIGSX" | tee -a "$OUT/done.log"
