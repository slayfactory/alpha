#!/bin/bash
# compare.sh <каталог базы> <каталог нового>: rc и forms.csv / rounds.csv / manifest.txt по каждой строке конфигов; упавшие в обоих —
# по последней строке stderr. Печатает счёт и расхождения; код возврата 1 при любом расхождении.
A=$1; B=$2; n=0; ok=0; same=0; bad=""
for d in "$A"/c*; do
  c=$(basename "$d"); n=$((n+1)); ra=$(cat "$A/$c/rc.txt"); rb=$(cat "$B/$c/rc.txt" 2>/dev/null)
  if [ "$ra" = rc=0 ] && [ "$rb" = rc=0 ]; then
    ok=$((ok+1)); s=1
    for f in forms.csv rounds.csv manifest.txt; do cmp -s "$A/$c/out/$f" "$B/$c/out/$f" || s=0; done
    [ $s = 1 ] && same=$((same+1)) || bad="$bad $c"
  elif [ "$ra" != "$rb" ] || [ "$(tail -1 "$A/$c/stderr.txt")" != "$(tail -1 "$B/$c/stderr.txt")" ]; then bad="$bad $c(rc/err)"; fi
done
echo "configs=$n ran_ok_both=$ok identical=$same differ:[$bad ]"
[ -z "$bad" ]
