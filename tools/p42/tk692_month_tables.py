"""TK-692: таблицы раздела месяца для findings из вывода `tk692_walk.py report` + `cmp` и лога цепочки.

usage: tk692_month_tables.py <m04> <Апрель> <январь–апрель> <report+cmp txt> <chain log> <out md>
report+cmp: ssh srv, `tk692_walk.py report --rid A-m0N ...; echo =====CMP; tk692_walk.py cmp --rids A-m0N,H-m0N,B-m0N`; chain log: /data/p42-tk692/log/chain-m0N.log.
В выходном md маркер @@NEXT@@ — заменить подписью «Следующий выбор для …:» руками (проза, заголовок, ворота — вручную).
"""
import re, sys
M, NAME, RNG, REPF, CHAINF, OUTF = sys.argv[1:7]   # m04, Апрель, январь–апрель, report txt, chain log, out md
rep = open(REPF, encoding='utf-8').read().split('\n')
log = open(CHAINF, encoding='utf-8').read().split('\n')
def block(start_pat):
    i = next(k for k, l in enumerate(rep) if l.startswith(start_pat))
    rows = []; j = i + 1
    while j < len(rep) and rep[j].startswith('|'):
        rows.append(rep[j]); j += 1
    return rows
out = []
for side in ("long", "short"):
    out.append(f"#### A-{M} {side} (E = 1; ряд вне выборки, ставка 500 $, маржа 250 $)\n")
    out += block(f"### A-{M} {side}"); out.append("")
nxt = [l for l in rep if l.startswith('Следующий выбор (month')]
if len(nxt) >= 2:   # у последнего месяца (июнь) следующего выбора нет
    out.append("@@NEXT@@ лонг — " + nxt[0].split(': ',1)[1] + "; шорт — " + nxt[1].split(': ',1)[1] + ".\n")
rr = {'long': [], 'short': []}; cur = None
for l in log:
    if 'replay long' in l and l.startswith('['): cur = 'long'
    if 'replay short' in l and l.startswith('['): cur = 'short'
    m = re.match(r'\| (day3|month|week) \| E=1 \| (\S+) \| R:R ([\d.]+) \| стоп ([\d.]+) \$ \(([\d.]+) % маржи\) \| утечка (\S+) \|', l)
    if m and cur: rr[cur].append(m.groups())
out.append(f"#### R:R, убыток по цене стопа, доля утечки — E = 1, {RNG}\n")
out.append("| сторона | шаг | ряд | R:R | убыток по стопу $ (% маржи 250 $) | доля утечки |\n|---|---|---|---|---|---|")
for side in ('short', 'long'):
    for st, ser, r, stop, pct, leak in sorted(rr[side], key=lambda x: (x[0], x[1])):
        out.append(f"| {side} | {st} | {ser} | {r} | {stop} ({pct} %) | {leak} |")
out.append("")
out.append(f"#### База против отобранных — {NAME.lower()} и накопительно (по пулам A / H / B; «best» — найдено на этих данных)\n")
for side in ("long", "short"):
    out.append(f"{side}:\n")
    out.append("| окно | пул | ряд | шаг | cid | суток | сд. | Σ $ | Шарп | просадка $ (% маржи) | примечание |\n|---|---|---|---|---|---|---|---|---|---|---|")
    i = next(k for k, l in enumerate(rep) if l.startswith(f"### {side}: база"))
    j = i + 1
    while j < len(rep) and (rep[j].startswith('|') or rep[j].strip() == ''):
        if rep[j].startswith(f'| {M}') or rep[j].startswith('| cum'): out.append(rep[j])
        j += 1
    out.append("")
open(OUTF, 'w', encoding='utf-8').write("\n".join(out))
