#!/bin/bash
# Гейт «байт в байт» (TK-702): один прогон `lob bounce-grid` на строку configs.txt, фикстура — srv /data/tk702/gate-root
# (BTCUSDT-2026-09-09.binlog + instruments.csv + session.json). CPUQuota=25 % (движок TK-674 на srv важнее).
#   gate-all.sh <бинарь> <каталог результата> [файл конфигов]
# Бинари: `vps-check.sh <дерево> bin` на Софии (plain + r2), формы R2 — только в r2-бинаре.
# Сравнение двух каталогов — compare.sh. stdout/stderr содержат тайминги, их не сравнивают.
BIN=$1; OUT=$2; CFG=${3:-$(dirname "$0")/configs.txt}
rm -rf "$OUT"; mkdir -p "$OUT"
i=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  i=$((i+1)); n=$(printf 'c%02d' $i); d="$OUT/$n"; mkdir -p "$d"
  echo "$line" > "$d/args.txt"
  systemd-run --scope -q --unit="tk702-gate-$$-$n" -p CPUQuota=25% -p MemoryMax=8G nice -n 19 \
    "$BIN" lob bounce-grid --root /data/tk702/gate-root --median-rtt-ns 1000000 --p95-rtt-ns 2000000 \
    --h3-mode floor --allow-unverified --threads 1 --order-qty-from-pool --out-dir "$d/out" $line > "$d/stdout.txt" 2> "$d/stderr.txt"
  echo "rc=$?" > "$d/rc.txt"
done < "$CFG"
echo done > "$OUT/DONE"
