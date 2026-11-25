#!/usr/bin/env bash
# TK-700 (srv): дерево окна ворот прогрева `wa20` (T0 = 11.06, прогрев 20 суток вместо 14) и дамп сигналов `tk700_sig.sh wa20 all` (P42_SIGSX_ONLY=1: P&L не считается).
# Сделки 11–16.06 — из `wj` (TK-686), с 17.06 — из `wa`; минутки — те же деревья; кэш `.b42` — `breakout convert` (P42_T0 = 2026-06-11, как tk625_prep.sh).
# Использование: tk700_wa20.sh [потоки]    Выход: /data/p42-tk621/wa20 (дерево), /data/p42-tk700/gate/wa20/dumps (дампы), READY в дереве.
set -uo pipefail
TH=${1:-2}
HERE=$(cd "$(dirname "$0")" && pwd)
WA=/data/p42-tk621/wa; WJ=/data/p42-tk686/wj; W=/data/p42-tk621/wa20; OUT=/data/p42-tk700/gate/wa20
BIN=${TK671_BIN:-/data/p42-tk671/bin/alpha-lx-sides}
T0=2026-06-11
[ "$(df -BG --output=avail /data | tail -1 | tr -dc 0-9)" -ge 20 ] || { echo "FAIL места < 20 ГБ"; exit 3; }
if [ ! -e "$W/READY" ]; then
  rm -rf "$W"; mkdir -p "$W"
  python3 - "$W" "$WA" "$WJ" <<'PY' || exit 1
import datetime, os, sys
W, wa, wj = sys.argv[1:4]
a, b = datetime.date(2026, 6, 11), datetime.date(2026, 6, 16)
jd = [(a + datetime.timedelta(i)).isoformat() for i in range((b - a).days + 1)]
n = [0, 0, 0]
for s in sorted(os.listdir(os.path.join(wa, "aggtrades"))):
    for k in ("klines", "aggtrades"):
        os.makedirs(os.path.join(W, k, s), exist_ok=True)
    for src, days in ((wj, jd), (wa, None)):
        for k, pat in (("klines", "%s.csv"), ("aggtrades", "%s-aggTrades-%%s.zip" % s)):
            if days is None:
                d0 = os.path.join(src, k, s)
                names = sorted(os.listdir(d0)) if os.path.isdir(d0) else []
            else:
                names = [pat % d for d in days]
            for nm in names:
                p = os.path.join(src, k, s, nm)
                if os.path.exists(p) and os.path.getsize(p) > 0 and not os.path.exists(os.path.join(W, k, s, nm)):
                    os.symlink(os.path.realpath(p), os.path.join(W, k, s, nm)); n[0 if k == "klines" else 1] += 1
print("дерево wa20: минуток+funding %d, сделок %d" % (n[0], n[1]))
PY
  mkdir -p "$W/cache"
  P42_T0=$T0 "$BIN" breakout convert --src "$W/klines" --out "$W/cache" --threads "$TH" > "$W/convert.log" 2>&1 || { echo "FAIL convert"; exit 1; }
  echo 0 > "$W/READY"
fi
bash "$HERE/tk700_sig.sh" wa20 all "$OUT" "$TH"; rc=$?
echo "wa20 sig rc=$rc $(date +%T)"
exit $rc
