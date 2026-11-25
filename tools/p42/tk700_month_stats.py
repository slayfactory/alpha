#!/usr/bin/env python3
"""TK-700: сводка месяца H1 по run-id `h1-<mon>-*` из mass.sqlite — клеток, ошибки охраны, пропуски пар, партии, минуты движка (стена / ЦП).
  python3 tk700_month_stats.py jan"""
import sqlite3
import sys

DB = "/data/p42-tk700/mass/mass.sqlite"


def main(mon):
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    for rid, state in c.execute("SELECT run_id, state FROM mass_run WHERE run_id LIKE ? ORDER BY run_id", ("h1-%s-%%" % mon,)):
        cw = c.execute("SELECT count(*), coalesce(sum(errs!=''),0), coalesce(sum(n>0),0), coalesce(sum(note LIKE '%низкое покрытие%'),0) FROM mass_cw WHERE run_id=?", (rid,)).fetchone()
        sk = c.execute("SELECT count(*) FROM mass_skip WHERE run_id=?", (rid,)).fetchone()[0]
        b = c.execute("SELECT count(*), coalesce(sum(rc!=0),0), round(coalesce(sum(engine_s),0)/60,1), round(coalesce(sum(cpu_s),0)/60,1), min(utc), max(utc) FROM mass_batch WHERE run_id=?", (rid,)).fetchone()
        print("%s state=%s клеток×окон %d, ошибок %d, со сделками %d, «низкое покрытие» %d, пропусков %d; партий %d (rc≠0: %d), движок %.1f мин стена / %.1f мин ЦП, %s … %s" % (
            (rid, state) + tuple(cw) + (sk,) + tuple(b)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
