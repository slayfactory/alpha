#!/usr/bin/env python3
# TK-706 (пучок, E2 критика 25.11): ожидаемый выигрыш пучка по замеру `ws-measure3` (bench/b3-H1.json) и ЦП `m2`.
# usage: b3model.py <b3.json> <ЦП m2 на H1, с (user+sys)> [доля обвязки f, если задана извне; иначе верхняя оценка из замера]
import json, sys
j = json.load(open(sys.argv[1]))
cpu_m2 = float(sys.argv[2])
t1, t2 = j['ticks_dem1'], j['lot2']['ticks']
a, b, m, w = j['ticks_dem1_groupmax'], j['ticks_bundle_wrap'], j['ticks_bundle_member'], j['ticks_unacc_est']
ns_tick = j['timed_full_cpu_s'] / max(j['timed_ticks'], 1)  # с на тик, ЦП потока, сутки в памяти
f = float(sys.argv[3]) if len(sys.argv) > 3 else j['timed_iter_cpu_s'] / max(j['timed_full_cpu_s'], 1e-9)
walk_cpu = ns_tick * (t1 + t2)
g_eff = t1 / max(a, 1)
g_eff_b = m / max(b, 1)
dem1 = j['dem1_keys']
unacc = j['unaccepted_in_dem_groups']
bundle_l1 = ns_tick * (f * b + (1 - f) * m)
save_l1 = ns_tick * t1 - bundle_l1
pre = j['prepass_cpu_s']
net = save_l1 - pre
# формула критика (ручной вид): ход_m2 · доля_обвязки · (1 − 1/g_eff) − предпроход − (непринятых / запрошенных) · ход_m2(лот 1) · цена_непринятого / цена_запрошенного
walk_l1 = ns_tick * t1
ratio = (w / max(unacc, 1)) / (t1 / max(dem1, 1))
crit = walk_l1 * f * (1 - 1 / g_eff) - pre - (unacc / max(dem1, 1)) * walk_l1 * ratio
print(f"ЦП m2 {cpu_m2:.2f} с; ход m2 (лот 1 + лот 2) {walk_cpu:.2f} с = {100*walk_cpu/cpu_m2:.1f} % ЦП; на тик {ns_tick*1e9:.1f} нс; доля общей части f = {f:.3f}")
print(f"тики: лот 1 {t1:,}, лот 2 {t2:,}; пучок: обвязка {b:,} (g_eff по тикам спроса {g_eff:.2f}; с тратой {g_eff_b:.2f}), члены {m:,}, трата {w:,}")
print(f"ключи: кандидатов {j['cand_keys']:,}, запрошено {dem1:,} (не из кандидатов {j['dem1_not_in_cand']}), непринятых в группах со спросом {unacc:,} (цена непринятого / запрошенного {ratio:.2f})")
print(f"выигрыш лот 1 {save_l1:.2f} с − предпроход {pre:.2f} с = {net:.2f} с = {100*net/cpu_m2:.1f} % ЦП m2   [формула критика: {crit:.2f} с = {100*crit/cpu_m2:.1f} %]")
print("РЕШЕНИЕ:", "брать (≥ 5 %)" if 100 * net / cpu_m2 >= 5 else "отбросить (< 5 %)")
