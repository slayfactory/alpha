"""TK-681: сверка ключей и покрытие таблицы FAST (`tk681_run.cov_one`)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tk681_run as r  # noqa: E402


def put(d, key, fast):
    open(os.path.join(d, "eps-key.csv"), "w", encoding="utf-8", newline="").write(key)
    open(os.path.join(d, "fast.csv"), "w", encoding="utf-8", newline="").write(fast)


def test_cov_counts_non_empty(tmp_path):
    put(tmp_path, "row_id,sym,sig_ms,level_px_e8\n7,A,10,1\n8,A,20,1\n9,B,5,1\n", "sym,sig_ms,x,y\nA,10,1.5,\nA,20,,\nB,5,2,3\n")
    n, share = r.cov_one(str(tmp_path))
    assert n == 3 and share == {"x": 2 / 3, "y": 1 / 3}


@pytest.mark.parametrize("fast", ["sym,sig_ms,x\nA,10,1\nA,21,1\n", "sym,sig_ms,x\nA,10,1\n", "sym,sig_ms,x\nA,10,1\nA,20,1\nA,30,1\n", "sig_ms,sym,x\n10,A,1\n20,A,1\n"])
def test_cov_rejects_key_mismatch(tmp_path, fast):
    put(tmp_path, "row_id,sym,sig_ms,level_px_e8\n7,A,10,1\n8,A,20,1\n", fast)
    with pytest.raises(ValueError):
        r.cov_one(str(tmp_path))
