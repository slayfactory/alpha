#!/usr/bin/env python3
"""TK-698, пункт 5в: сводная таблица «на чём зарабатывает шорт / на чём теряет лонг» — только выборка чисел из уже записанных прогонов (новых расчётов нет): 5а (`tk698-i5a-v1`, анатомия линий), пункт 2 (`tk698-i2-v1`, рынок перед входом), 5б (`tk698-i5b-v2`, признаки × стороны).
Пары: A — зеркальная конструкция `n_bounce1+s4` на обеих сторонах; B — якоря (лонг `s3`, шорт `s4`, L-334). Таблица А — по видам показателей; таблица Б — столбцы TK-671 с расхождением сторон выше линии шума (10 столбцов f12 — «до исправления ТФ», TK-699, — не берутся).
Описательно, найдено на этих данных. Запуск на srv: python3 tk698_item5c.py <корень /data/p42-tk698> <run_key>"""
import json
import sys

import tk698_db as D

R5A, R2, R5B = "tk698-i5a-v1", "tk698-i2-v1", "tk698-i5b-v2"
BAD = {"age_h", "approach_dur_min", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h", "depth_min_bp", "depth_mean_bp", "since_touch_prior_h", "touch_prior_none"}
PAIRS = {"A": ("L_nb1s4", "S_nb1s4", "n_bounce1+s4 (обе стороны)"), "B": ("L_anchor", "S_anchor", "якоря: лонг s3 / шорт s4")}
TYPE = {"clu_id": "монета: метка кластера", "clu_n": "монета: размер кластера", "bd_mode": "артефакт сбора: режим метки суток снимка стакана", "f_age_h": "время: часы от выплаты фандинга",
        "sg1": "рынок: σ BTC", "sg5": "рынок: σ BTC", "sg15": "рынок: σ BTC", "mk_rv5_btc_1440": "рынок: волатильность BTC", "mk_btc_s1440": "рынок: ход BTC за сутки", "mk_eth_s1440": "рынок: ход ETH за сутки",
        "bk_n": "монета: число прошлых эпизодов", "bk_k": "монета: из них с меткой 1", "t_wday": "время: день недели", "t_hour": "время: час", "bt_24h": "монета: бета к BTC 24 ч", "bt_7d": "монета: бета к BTC 7 сут",
        "bz5": "рынок: шок BTC в σ", "bz15": "рынок: шок BTC в σ", "peerall15": "рынок: число сигналов по рынку за 15 мин", "bd_depth_turn": "стакан / оборот", "dist_bp": "цена к уровню", "cc_4h": "монета: корреляция с кластером 4 ч",
        "conf02": "фандинг: флаг"}


def kindtype(col):
    if col in TYPE:
        return TYPE[col]
    return "время: килзона (0/1)" if col.startswith("kz") else "—"


def f(x, nd=0):
    return "—" if x is None else ("%.*f" % (nd, x))


def main(root, run_key):
    db = D.connect()
    D.new_run(db, run_key, "item5c", "tools/p42/tk698_item5c.py", "сводка 5в из прогонов 5а / 2 / 5б: на чём зарабатывает шорт, на чём теряет лонг; описательно, найдено на этих данных")
    line = {r[0]: r for r in db.execute("select line, n, usd, per_trade, sharpe, dd, dd_pct, gap_longest, gap_mean, gap_tail, rr, extra from res_line where run_key = ?", (R5A,))}
    split = {}
    for ln, dim, bn, n, usd, sh, ex in db.execute("select line, dim, bin, n, usd, share, extra from res_split where run_key = ? and item = 'item5a'", (R5A,)):
        split[(ln, dim, bn)] = (n, usd, sh, json.loads(ex) if ex else {})
    mk = {}
    for ln, dim, bn, n, usd in db.execute("select line, dim, bin, n, usd from res_split where run_key = ? and item = 'item2'", (R2,)):
        mk[(ln, dim, bn)] = (n, usd)
    rows = []                                   # (пара, раздел, показатель, шорт, лонг)

    def put(pair, sect, name, s, l):
        rows.append((pair, sect, name, s, l))

    for pk, (lid, sid, title) in PAIRS.items():
        L, S = line[lid], line[sid]
        le, se = json.loads(L[11]), json.loads(S[11])
        put(pk, "итог", "сделок", str(S[1]), str(L[1]))
        put(pk, "итог", "Σ $ (500 $), $ на сделку", "%s / %s" % (f(S[2]), f(S[3], 2)), "%s / %s" % (f(L[2]), f(L[3], 2)))
        put(pk, "итог", "Шарп; просадка $ (% маржи); перехай (самый долгий / средний / хвост, сут.)", "%s; %s (%s %%); %d / %s / %d" % (f(S[4], 2), f(S[5]), f(S[6]), S[7], f(S[8], 1), S[9]), "%s; %s (%s %%); %d / %s / %d" % (f(L[4], 2), f(L[5]), f(L[6]), L[7], f(L[8], 1), L[9]))
        put(pk, "итог", "R:R", f(S[10], 2), f(L[10], 2))
        put(pk, "исход", "доля выигрышей; ср. выигрыш; ср. проигрыш ($)", "%s; %s; %s" % (f(se["win_share"], 2), f(se["mean_win"], 2), f(se["mean_loss"], 2)), "%s; %s; %s" % (f(le["win_share"], 2), f(le["mean_win"], 2), f(le["mean_loss"], 2)))
        put(pk, "исход", "в сделке, мин (среднее)", f(se["hold_mean"], 1), f(le["hold_mean"], 1))
        for k in ("trail", "rt", "be", "stop"):
            a, b = split.get((sid, "kind", k)), split.get((lid, "kind", k))
            put(pk, "выходы", "вид «%s»: сделок / доля / Σ $ / в сделке мин" % k, "—" if a is None else "%d / %s / %s / %s" % (a[0], f(a[2], 2), f(a[1]), f(a[3].get("hold_mean"), 0)), "—" if b is None else "%d / %s / %s / %s" % (b[0], f(b[2], 2), f(b[1]), f(b[3].get("hold_mean"), 0)))
        for k in ("0", "1-3", "4-7", "8+"):
            a, b = split.get((sid, "takes", k)), split.get((lid, "takes", k))
            put(pk, "ступени", "takes %s: сделок / Σ $" % k, "%d / %s" % (a[0], f(a[1])), "%d / %s" % (b[0], f(b[1])))
        pa, pb = split.get((sid, "path_all", "all")), split.get((lid, "path_all", "all"))
        if pa and pb:
            put(pk, "путь", "MFE / MAE по ленте, NATR (все сделки)", "%s / %s" % (f(pa[3]["mfe_natr"], 2), f(pa[3]["mae_natr"], 2)), "%s / %s" % (f(pb[3]["mfe_natr"], 2), f(pb[3]["mae_natr"], 2)))
            for nm in ("win", "loss"):
                a, b = split[(sid, "wl", nm)], split[(lid, "wl", nm)]
                put(pk, "путь", "%s: сделок / Σ $ / MFE NATR / MAE NATR / в сделке мин" % ("выигрыши" if nm == "win" else "проигрыши"),
                    "%d / %s / %s / %s / %s" % (a[0], f(a[1]), f(a[3].get("mfe_natr"), 2), f(a[3].get("mae_natr"), 2), f(a[3].get("hold_mean"), 1)), "%d / %s / %s / %s / %s" % (b[0], f(b[1]), f(b[3].get("mfe_natr"), 2), f(b[3].get("mae_natr"), 2), f(b[3].get("hold_mean"), 1)))
        for mn in ("jul", "aug", "sep", "oct", "nov"):
            a, b = split[(sid, "month", mn)], split[(lid, "month", mn)]
            put(pk, "месяцы", "%s: Σ $ (сделок)" % mn, "%s (%d)" % (f(a[1]), a[0]), "%s (%d)" % (f(b[1]), b[0]))
        for dim, lab in (("lead_mk_btc_s15", "BTC за 15 мин до сигнала"), ("lead_mk_btc_s60", "BTC за 60 мин до сигнала"), ("lead_mk_eth_s60", "ETH за 60 мин до сигнала")):
            for t, tn in (("t1", "против сделки"), ("t2", "середина"), ("t3", "по ходу сделки")):
                a, b = mk.get((sid, dim, t)), mk.get((lid, dim, t))
                if a and b:
                    put(pk, "рынок", "%s, терциль %s: Σ $ (сделок)" % (lab, tn), "%s (%d)" % (f(a[1]), a[0]), "%s (%d)" % (f(b[1]), b[0]))
    # таблица Б: признаки
    feats = []
    for col, fam, d, nz, ex in db.execute("select col, family, div, noise_hi, extra from res_div where run_key = ? and div is not null order by div desc", (R5B,)):
        if col in BAD or nz is None or d <= nz:
            continue
        e = json.loads(ex)
        feats.append((col, kindtype(col), d, nz, d / nz, e["bin_max"], e["short_at_max"], e["long_at_max"], e["bin_min"], e["short_at_min"], e["long_at_min"], len(e["edges"]) + 1))
    n_cols = db.execute("select count(*) from res_div where run_key = ?", (R5B,)).fetchone()[0]
    n_nonf12 = db.execute("select count(*) from res_div where run_key = ? and col not in (%s)" % ",".join("?" * len(BAD)), (R5B,) + tuple(BAD)).fetchone()[0]
    n_read = db.execute("select count(*) from res_div where run_key = ? and div is not null and col not in (%s)" % ",".join("?" * len(BAD)), (R5B,) + tuple(BAD)).fetchone()[0]
    md = ["| пара | раздел | показатель | шорт | лонг |", "|---|---|---|---|---|"] + ["| %s | %s | %s | %s | %s |" % r for r in rows]
    md2 = ["| столбец | что это | расхождение, $/строку | линия шума 95 % | отношение | корзина max (шорт / лонг) | корзина min (шорт / лонг) | корзин |", "|---|---|---|---|---|---|---|---|"]
    md2 += ["| %s | %s | %.3f | %.3f | %.2f | б%d: %.2f / %.2f | б%d: %.2f / %.2f | %d |" % r for r in feats]
    D.put(db, "res_text", run_key, [("table_5c_a", "\n".join(md)), ("table_5c_b", "\n".join(md2)),
                                     ("meta", json.dumps({"pairs": PAIRS, "columns_total": n_cols, "columns_f12_excluded": n_cols - n_nonf12, "columns_non_f12": n_nonf12, "columns_readable": n_read, "above_noise95": len(feats), "expected_by_chance_95_readable": round(0.05 * n_read, 1), "expected_by_chance_95_non_f12": round(0.05 * n_nonf12, 1), "sources": [R5A, R2, R5B]}, ensure_ascii=False))], ["key", "value"])
    print("\n".join(md))
    print()
    print("\n".join(md2[: 2 + 20]))
    print("выше линии шума: %d из %d читаемых (%d без f12; по случаю ≈ %.1f)" % (len(feats), n_read, n_nonf12, 0.05 * n_read))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
