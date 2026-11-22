#!/usr/bin/env python3
"""Репетиция стадий half1 / half2 (TK-685, §13.76) на синтетике: половина 2 читается один раз, ошибка после замка стоила бы единственного чтения.
Запуск на srv: `cd /data/p42-tk685/code && /data/tk592/venv/bin/python -m unittest test_tk685_stages` (нужны pandas, pyarrow, catboost)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk685_lib as lib  # noqa: E402
import tk685_run as R  # noqa: E402
import tk685_stages as S  # noqa: E402


def synth(out, name, n=700, seed=1):
    rng = np.random.default_rng(seed)
    day = np.sort(rng.integers(0, 136, n))
    half = lib.halves(day)
    f0 = rng.normal(size=n)                       # признак с сигналом: большие значения — убытки
    p_loss = 1 / (1 + np.exp(-(1.2 * f0 - 0.2)))
    u = rng.random(n)
    zero = u < 0.12
    loss = (~zero) & (rng.random(n) < p_loss)
    group = np.where(zero, lib.ZERO, np.where(loss, lib.LOSS, lib.PROFIT)).astype(int)
    feats = {"f0": f0}
    for k in range(1, 40):
        feats["f%d" % k] = rng.normal(size=n)
    feats["const"] = np.full(n, 3.0)
    feats["mostly_nan"] = np.where(rng.random(n) < 0.7, np.nan, rng.normal(size=n))
    feats["dup0"] = f0.copy()                     # копия f0: пригоден только первый
    os.makedirs(os.path.join(out, "trades"), exist_ok=True)
    os.makedirs(os.path.join(out, "feat"), exist_ok=True)
    pd.DataFrame({"row": np.arange(n), "half": half, "group": group, "day_in": day, "sym": rng.choice(list("ABCDEFGH"), n), "net": rng.normal(size=n)}).to_csv(
        os.path.join(out, "trades", name + ".csv"), index=False)
    fr = pd.DataFrame(feats)
    fr.insert(0, "dup", rng.random(n) < 0.05)
    fr.insert(0, "row", np.arange(n))
    fr.to_parquet(os.path.join(out, "feat", name + ".parquet"), index=False)


class Stages(unittest.TestCase):
    def test_half1_half2_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = (R.OUT, R.SETS, R.B, S.N_NULL_TREES, S.N_NULL_CB, dict(S.CB_PARAMS))
            try:
                R.OUT, R.SETS, R.B, S.N_NULL_TREES, S.N_NULL_CB = tmp, ("L_A",), 400, 8, 2
                S.CB_PARAMS.update(iterations=30, depth=3)
                synth(tmp, "L_A")
                S.stage_half1()
                h1 = json.load(open(os.path.join(tmp, "h1", "L_A.json"), encoding="utf-8"))
                self.assertEqual(h1["m_f"], 40)                       # f0..f39; dup0 — копия f0, const и mostly_nan непригодны
                self.assertTrue(os.path.exists(os.path.join(tmp, "h1", "DONE")))
                c1 = h1["contrasts"]["C1"]
                self.assertTrue(c1["testable"])
                self.assertEqual(c1["candidates"][0]["name"], "f0")
                self.assertGreater(c1["candidates"][0]["auc1"], 0.5)
                self.assertIn("tree", h1)
                S.stage_half2()
                h2 = json.load(open(os.path.join(tmp, "h2", "L_A.json"), encoding="utf-8"))
                cand = {r["name"]: r for r in h2["contrasts"]["C1"]["candidates"]}
                self.assertTrue(cand["f0"]["confirmed"])
                self.assertIn("auc2_months", cand["f0"])
                self.assertTrue(h2["tree"]["testable"])
                self.assertIn("auc2", h2["catboost"])
                with self.assertRaises(FileExistsError):
                    S.stage_half2()                                   # замок: второй раз нельзя
            finally:
                R.OUT, R.SETS, R.B, S.N_NULL_TREES, S.N_NULL_CB = old[:5]
                S.CB_PARAMS.clear()
                S.CB_PARAMS.update(old[5])


if __name__ == "__main__":
    unittest.main()
