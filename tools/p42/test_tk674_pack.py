#!/usr/bin/env python3
"""Тесты `tk674_pack` (упаковка каталогов клеток партий, извлечение по требованию, проверка перед удалением). Нужны `tar` и `zstd` (srv)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk674_pack as pk  # noqa: E402

FILES = ("short-trades.csv", "short-daily.csv", "short-lots.csv", "short-coinday.csv", "short-entryrej.csv")


@unittest.skipUnless(shutil.which("zstd") and shutil.which("tar"), "нужны tar и zstd")
class Pack(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        pk.ROOT = self.root
        self.ws = os.path.join(self.root, "cells", "wa-S")
        self.cells = {}
        for h in ("f111", "f222"):
            for i in range(30):
                cid = "s4+x%d.ge_q%d" % (i, 20 + i)
                d = os.path.join(self.ws, h, cid)
                os.makedirs(d)
                for f in FILES:
                    body = ("%s %s %d" % (h, f, i) + chr(10)) * (i + 1)
                    body = body.encode()
                    open(os.path.join(d, f), "wb").write(body)
                    self.cells[(h, cid, f)] = body

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_pack_delete_extract_roundtrip(self):
        pk.cmd_pack("wa-S", True, None, 1)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "cells-arch", "wa-S"))), ["f111.tar.zst", "f222.tar.zst"])
        self.assertEqual(os.listdir(self.ws), [])                                                   # каталоги удалены
        d = os.path.join(self.ws, "f222", "s4+x7.ge_q27")
        self.assertTrue(pk.cell_exists(d))
        self.assertFalse(os.path.isdir(d))
        self.assertEqual(pk.ensure_cell(d), d)
        for f in FILES:
            self.assertEqual(open(os.path.join(d, f), "rb").read(), self.cells[("f222", "s4+x7.ge_q27", f)])
        self.assertEqual(sorted(os.listdir(os.path.join(self.ws, "f222"))), ["s4+x7.ge_q27"])      # извлечена одна клетка
        self.assertFalse(any(x.startswith(".x") for x in os.listdir(self.ws)))                    # временный каталог убран

    def test_repack_skips_existing_archive(self):
        pk.cmd_pack("wa-S", True, None, 1)
        arch = os.path.join(self.root, "cells-arch", "wa-S", "f111.tar.zst")
        m0 = os.path.getmtime(arch)
        pk.ensure_cell(os.path.join(self.ws, "f111", "s4+x3.ge_q23"))                              # частичное извлечение
        pk.cmd_pack("wa-S", True, None, 1)                                                          # не перепаковывает неполный каталог поверх архива
        self.assertEqual(os.path.getmtime(arch), m0)
        pk.cmd_drop("wa-S")
        self.assertEqual(os.listdir(self.ws), [])

    def test_failed_check_keeps_dirs(self):
        old = pk.verify_sample
        pk.verify_sample = lambda *a, **k: ["x"]
        try:
            with self.assertRaises(SystemExit):
                pk.cmd_pack("wa-S", True, None, 1)
        finally:
            pk.verify_sample = old
        self.assertEqual(sorted(os.listdir(self.ws)), ["f111", "f222"])                              # проверка не прошла — каталоги на месте

    def test_missing_cell_raises(self):
        with self.assertRaises(FileNotFoundError):
            pk.ensure_cell(os.path.join(self.ws, "f999", "nope"))

    def test_entries_count_matches_tar(self):
        pk.cmd_pack("wa-S", False, "f111", 1)
        self.assertEqual(pk.entries(os.path.join(self.ws, "f111")), 1 + 30 * (1 + len(FILES)))


if __name__ == "__main__":
    unittest.main()
