#!/usr/bin/env python3
"""TK-709: таблица итогов 9 клеток из БД (таблицы `cells`, `tests`) в Markdown для записи летописи. Запуск (srv): python3 tk709_report.py <БД> <run_id>
В таблице по периодам А / Б: сделок, Σ $ (ставка $500), Шарп, просадка $ (% маржи 250 $), перехай (самый длинный, сутки), p Холма, доля двух лучших суток; затем справочные R1/R3/R4."""
import sqlite3
import sys

XS = (0.05, 0.10, 0.20)
HS = (5, 15, 60)


def main():
    db, run = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    cells = {}
    for v, x, h, p, m, val in c.execute("select variant, x, h, period, metric, value from cells where run_id=?", (run,)):
        cells[(v, x, h, p, m)] = val
    tests = {(x, h, p): r for x, h, p, *r in c.execute("select x, h, period, mean_daily, ci_lo, ci_hi, p_two, p_holm, days_with, coin2_share, day2_share from tests where run_id=?", (run,))}

    def g(v, x, h, p, m, f="%.0f"):
        s = cells.get((v, x, h, p, m))
        try:
            return f % float(s)
        except (TypeError, ValueError):
            return "—"

    print("| X | h | период | сделок | Σ $ | Шарп | просадка $ (% маржи) | перехай, сут | p Холма | 2 лучших суток, доля Σ |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for x in XS:
        for h in HS:
            for p in ("A", "B"):
                t = tests[(x, h, p)]
                print(f"| {int(x * 100)} % | {h} | {p} | {g('main', x, h, p, 'n')} | {g('main', x, h, p, 'sum_usd')} | {g('main', x, h, p, 'sharpe', '%.2f')} | "
                      f"{g('main', x, h, p, 'maxdd_usd')} ({g('main', x, h, p, 'maxdd_pct_margin')} %) | {g('main', x, h, p, 'ph_longest')} | {t[4]:.3f} | "
                      f"{('%.2f' % t[7]) if t[7] is not None else '— (Σ ≤ 0)'} |")
    print()
    print("Справочно, Σ $ по периодам А / Б (не испытания): R1 (первая сделка) · R3 (минутная) · R4_1s · R4_5s; безубыточная добавка ко входу, бп (main).")
    print("| X | h | R1 А/Б | R3 А/Б | R4_1s А/Б | R4_5s А/Б | безубыток, бп А/Б | макс. одновременных, Б (пик $) |")
    print("|---|---|---|---|---|---|---|---|")
    for x in XS:
        for h in HS:
            row = [f"{int(x * 100)} %", str(h)]
            for v in ("R1", "R3", "R4_1s", "R4_5s"):
                row.append(f"{g(v, x, h, 'A', 'sum_usd')} / {g(v, x, h, 'B', 'sum_usd')}")
            row.append(f"{g('main', x, h, 'A', 'breakeven_extra_bps')} / {g('main', x, h, 'B', 'breakeven_extra_bps')}")
            row.append(f"{g('main', x, h, 'B', 'max_concurrent')} ({g('main', x, h, 'B', 'peak_usd')})")
            print("| " + " | ".join(row) + " |")


if __name__ == "__main__":
    main()
