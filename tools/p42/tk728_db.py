#!/usr/bin/env python3
"""TK-728 (В-179): результаты шага 4 этапа A — в БД на srv (`/data/p42-tk728/res728.sqlite`), ключ прогона `run_key`; md-отчёт — только итоги.
  t728_run(run_key, created, item, note)
  t728_feat(run_key, n, name, set_id, in_sample, cnt, cov, window_min, cluster, rep_n, rep_name, tie, is_rep, status, why)   — признаки, кластеры 4б и итоговый статус
  t728_cluster(run_key, cluster, size, rep_name, tie, mean_abs_rho, members)                                                — кластеры
  t728_pair(run_key, a, b, rho, n_pair)                                                                                     — корреляции Спирмена пар
  t728_causal(run_key, family, method, n_rows, n_values, n_diff, note)                                                      — тест на заглядывание (4а)
  t728_label(run_key, scope, v_third, n, y_share, y_lo, y_hi, yn_share, yn_lo, yn_hi, agree, kappa, extra)                  — метки y и y_n по третям V (4в)
  t728_mem(run_key, scope, key, value, lo, hi, n, extra)                                                                    — память исходов (4г)
  t728_text(run_key, key, value)                                                                                            — прочее
"""
import json
import os
import sqlite3
import time

PATH = os.environ.get("TK728_DB", "/data/p42-tk728/res728.sqlite")
DDL = [
    "create table if not exists t728_run(run_key text primary key, created text, item text, note text)",
    "create table if not exists t728_feat(run_key text, n integer, name text, set_id text, in_sample integer, cnt integer, cov real, window_min real, cluster integer, rep_n integer, rep_name text, tie text, is_rep integer, status text, why text, primary key(run_key, n))",
    "create table if not exists t728_cluster(run_key text, cluster integer, size integer, rep_name text, tie text, mean_abs_rho real, members text, primary key(run_key, cluster))",
    "create table if not exists t728_pair(run_key text, a text, b text, rho real, n_pair integer)",
    "create index if not exists ix728_pair on t728_pair(run_key, a)",
    "create table if not exists t728_causal(run_key text, family text, method text, n_rows integer, n_values integer, n_diff integer, note text)",
    "create table if not exists t728_label(run_key text, scope text, v_third text, n integer, y_share real, y_lo real, y_hi real, yn_share real, yn_lo real, yn_hi real, agree real, kappa real, extra text)",
    "create table if not exists t728_mem(run_key text, scope text, key text, value real, lo real, hi real, n integer, extra text)",
    "create table if not exists t728_text(run_key text, key text, value text, primary key(run_key, key))",
]
TABLES = ("t728_feat", "t728_cluster", "t728_pair", "t728_causal", "t728_label", "t728_mem", "t728_text")


def connect(path=None):
    db = sqlite3.connect(path or PATH)
    for q in DDL:
        db.execute(q)
    return db


def new_part(db, run_key, tables, item, note=""):
    """Стереть прежние строки перечисленных таблиц этого прогона и (пере)записать запись прогона."""
    for t in tables:
        db.execute("delete from %s where run_key = ?" % t, (run_key,))
    db.execute("delete from t728_run where run_key = ?", (run_key,))
    db.execute("insert into t728_run values (?,?,?,?)", (run_key, time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), item, note))


def put(db, table, run_key, rows):
    """rows — список кортежей без run_key."""
    if not rows:
        return
    q = "insert into %s values (%s)" % (table, ",".join("?" * (len(rows[0]) + 1)))
    db.executemany(q, [(run_key,) + tuple(r) for r in rows])


def text(db, run_key, key, value):
    db.execute("insert or replace into t728_text values (?,?,?)", (run_key, key, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)))
