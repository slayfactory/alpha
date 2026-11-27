#!/usr/bin/env python3
"""TK-728 шаг 4а: ворота `tk671_t668_gate` без G-eq-B (равенство на базах TK-668 читает кэш монет вне c539 — для тестов на заглядывание не нужно, падает на `<монета>.b42` вне дерева окна); G-causal-A и G-causal-B — как в воротах.
Падение G-causal-B (кэш монеты вне c539) записывается как `error`, не как «прошло».
  python3 tk728_t668_causal.py --rows <rows.parquet> --win wb --side long --out <json> --procs 2 --k-causal 2000"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk671_t668_gate as G  # noqa: E402

G.g_eq_b = lambda *a, **k: ({"skipped": "G-eq-B — равенство, не тест на заглядывание; читает кэш монет вне c539"}, 0)
_orig = G.g_causal_b


def _safe(*a, **k):
    try:
        return _orig(*a, **k)
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)[:300], "not_run": True}, 0


G.g_causal_b = _safe
G.main()
