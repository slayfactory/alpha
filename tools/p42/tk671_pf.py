#!/usr/bin/env python3
"""П-42 §13.71 (TK-671): реплика правил занятости портфеля движка (`run_tick_xb`, блок «освобождение позиций» + проверки перед входом) на размеченных сделках.
Исход сделки не зависит от портфеля (движок считает его после проверок, и отклонённый сигнал состояния не меняет), поэтому выбранные строки можно прогнать через те же правила без повторного счёта сделок.

  replay(rows, cfg) -> маска принятых строк. rows (np-массивы): ms (sig_ms), dist, sym (номер монеты в списке вселенной), key (id уровня), entry/exit_ms, stop (bool: ExitKind::HardStop).
Порядок строк — как `sort_tsigs` движка: (sig_ms, dist, sym, level). Правила (по умолчанию — база движка `PortCfg::default`): одна позиция на монету (busy), пауза монеты после выхода `cool_min`, `m_stops` стопов подряд по монете —
пауза монеты `mm_min`, `s_glob` стопов за `w_min` — общая пауза `ww_min`, не больше `p` открытых, уровень — одна сделка (`reentry = 0`; `reentry = 1` — расходуется только стопом; `reentry = 2` — не расходуется).
Ворота G-pf: реплика на двойниках баз (те же фильтры, каждая строка торгуется независимо, `reentry = 2`) даёт те же сделки, что база движка (`tk671_gate_pf.py`)."""
from __future__ import annotations

import bisect

import numpy as np

MIN_MS = 60_000
BASE_CFG = dict(p=3, cool_min=5, m_stops=3, mm_min=60, s_glob=4, w_min=10, ww_min=60, reentry=0, busy=True)


def order(ms, dist, sym, key):
    """Индексы строк в порядке движка (sig_ms, dist, sym, level)."""
    return np.lexsort((np.asarray(key), np.asarray(sym), np.asarray(dist), np.asarray(ms)))


def replay(ms, dist, sym, key, exit_ms, stop, cfg=None, uid=None):
    """Возвращает булев массив «строка принята» в исходном порядке входных массивов. `key` — идентификатор уровня для правила «один уровень — одна сделка»; `uid` не нужен."""
    c = dict(BASE_CFG)
    c.update(cfg or {})
    n = len(ms)
    acc = np.zeros(n, dtype=bool)
    ms, exit_ms, stop = np.asarray(ms), np.asarray(exit_ms), np.asarray(stop, dtype=bool)
    sym, key = np.asarray(sym), np.asarray(key)
    cool_until, cpause_until, streak, busy = {}, {}, {}, {}
    gstops: list[int] = []
    gpause = 0
    consumed: set = set()
    open_: list[tuple[int, int, int]] = []          # (exit_ms, номер строки, монета), по возрастанию выхода (устойчиво)
    for i in order(ms, np.asarray(dist), sym, key):
        t = int(ms[i])
        while open_ and open_[0][0] <= t:
            ex, j, s = open_.pop(0)
            busy[s] = False
            cool_until[s] = ex + c["cool_min"] * MIN_MS
            if stop[j]:
                if c["reentry"] == 1:
                    consumed.add((int(sym[j]), key[j]))
                streak[s] = streak.get(s, 0) + 1
                if streak[s] >= c["m_stops"]:
                    cpause_until[s] = ex + c["mm_min"] * MIN_MS
                    streak[s] = 0
                gstops.append(ex)
                while gstops and ex >= gstops[0] + c["w_min"] * MIN_MS:
                    gstops.pop(0)
                if len(gstops) >= c["s_glob"]:
                    if c["ww_min"] > 0:
                        gpause = ex + c["ww_min"] * MIN_MS
                    gstops.clear()
            else:
                streak[s] = 0
        s = int(sym[i])
        if (s, key[i]) in consumed:
            continue
        if c["busy"] and busy.get(s, False):
            continue
        if t < cool_until.get(s, 0) or t < cpause_until.get(s, 0):
            continue
        if t < gpause:
            continue
        if len(open_) >= c["p"]:
            continue
        if c["reentry"] == 0:
            consumed.add((s, key[i]))
        busy[s] = True
        ex = int(exit_ms[i])
        pos = bisect.bisect_right([o[0] for o in open_], ex)      # sort_by_key устойчив: равный выход — после прежних
        open_.insert(pos, (ex, i, s))
        acc[i] = True
    return acc
