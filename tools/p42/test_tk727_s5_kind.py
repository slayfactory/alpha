"""Тест TK-727 шага 5: потеря по видам выхода на известных числах (нужен numpy)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk727_s5_kind as K  # noqa: E402


def test_loss_by_kind_known():
    pnl = np.array([5.0, -2.0, -4.0, 3.0, -1.0, 0.0])
    kind = np.array(["trail", "stop", "stop", "rt", "rt", "stop"])
    r = K.loss_by_kind(pnl, kind)
    assert r["n"] == 6 and r["n_loss"] == 4 and abs(r["mean_loss"] - 7.0 / 4) < 1e-12
    s = r["kinds"]["stop"]
    assert s["n"] == 3 and s["n_loss"] == 3 and abs(s["mean_loss"] - 2.0) < 1e-12 and abs(s["share_of_loss_sum"] - 6.0 / 7.0) < 1e-12
    assert r["kinds"]["rt"]["n_loss"] == 1 and abs(r["kinds"]["rt"]["mean_loss"] - 1.0) < 1e-12
    assert r["kinds"]["trail"]["n_loss"] == 0 and np.isnan(r["kinds"]["trail"]["mean_loss"])
