#!/usr/bin/env python3
"""TK-698 (В-179): результаты разбора — в БД на srv (`/data/p42-tk698/res698.sqlite`), ключ прогона `run_key`; md — только заглушка. Таблицы длинные, запрос — по item / line / dim.
  res_run(run_key, created, item, tool, note)
  res_line(run_key, line, cell, side, n, usd, per_trade, sharpe, dd, dd_pct, gap_longest, gap_mean, gap_tail, rr, extra)      — линии (В-169, п. 35)
  res_split(run_key, item, line, dim, market, bin, n_days, n, usd, per_trade, per_day, share, extra)                          — срезы по рынку / корзинам / видам выхода
  res_corr(run_key, line, market, kind, value, lo, hi, n)                                                                      — корреляции суточного P&L с рынком (+ границы перестановок)
  res_feat(run_key, col, side, bin, lo, hi, n, usd, per_trade, stop_share)  /  res_div(run_key, col, rank, div, noise_hi, ...)  — признаки по корзинам и расхождение сторон (пункт 5б)
  res_text(run_key, key, value)                                                                                                 — прочее (охраны, покрытие, примечания)
"""
import json
import os
import sqlite3
import time

PATH = os.environ.get("TK698_DB", "/data/p42-tk698/res698.sqlite")
DDL = [
    "create table if not exists res_run(run_key text primary key, created text, item text, tool text, note text)",
    "create table if not exists res_line(run_key text, line text, cell text, side text, n integer, usd real, per_trade real, sharpe real, dd real, dd_pct real, gap_longest integer, gap_mean real, gap_tail integer, rr real, extra text, primary key(run_key, line))",
    "create table if not exists res_split(run_key text, item text, line text, dim text, market text, bin text, n_days integer, n integer, usd real, per_trade real, per_day real, share real, extra text)",
    "create index if not exists ix_split on res_split(run_key, item, line, dim)",
    "create table if not exists res_corr(run_key text, line text, market text, kind text, value real, lo real, hi real, n integer)",
    "create table if not exists res_feat(run_key text, col text, side text, bin integer, lo real, hi real, n integer, usd real, per_trade real, stop_share real, extra text)",
    "create index if not exists ix_feat on res_feat(run_key, col, side)",
    "create table if not exists res_div(run_key text, col text, family text, rank integer, div real, noise_hi real, n_short integer, n_long integer, extra text)",
    "create table if not exists res_text(run_key text, key text, value text, primary key(run_key, key))",
]


def connect():
    db = sqlite3.connect(PATH)
    for q in DDL:
        db.execute(q)
    return db


def new_run(db, run_key, item, tool, note=""):
    db.execute("delete from res_run where run_key = ?", (run_key,))
    for t in ("res_line", "res_split", "res_corr", "res_feat", "res_div", "res_text"):
        db.execute("delete from %s where run_key = ?" % t, (run_key,))
    db.execute("insert into res_run values (?,?,?,?,?)", (run_key, time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), item, tool, note))
    db.commit()


def put(db, table, run_key, rows, cols):
    q = "insert into %s (run_key, %s) values (?%s)" % (table, ",".join(cols), ",?" * len(cols))
    db.executemany(q, [(run_key,) + tuple(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in r) for r in rows])
    db.commit()
