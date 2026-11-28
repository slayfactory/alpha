#!/usr/bin/env bash
# TK-743: гейт «сделки байт в байт» — сделки прогона с журналом заявок против прогона TK-741 (V0nt), все окна. Только srv.
#   bash tk743_gate.sh [корень A=/data/p42-tk741/trades] [корень B=/data/p42-tk743/trades]
A=${1:-/data/p42-tk741/trades}; B=${2:-/data/p42-tk743/trades}
n=0; bad=0
for R in h1 sel n1; do
  for W in "$B"/tk743-nt-$R/*/; do
    w=$(basename "$W"); [ "$w" = t0.json ] && continue
    for f in "$W"*-trades.csv; do
      g="$A/tk741-nt-$R/$w/$(basename "$f")"
      if cmp -s "$f" "$g"; then n=$((n+1)); else bad=$((bad+1)); echo "РАЗЛИЧИЕ: $f против $g"; fi
    done
  done
done
echo "гейт: файлов сделок равны $n, различий $bad"
[ "$bad" -eq 0 ] && [ "$n" -gt 0 ]
