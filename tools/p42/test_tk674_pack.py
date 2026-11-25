#!/usr/bin/env python3
"""Тесты `tk674_pack` (упаковка каталогов клеток партий, извлечение по требованию, проверка перед удалением). Нужны `tar` и `zstd` (srv)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
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
        os.makedirs(os.path.join(self.root, "done"))
        for h in ("f111", "f222"):
            open(os.path.join(self.root, "done", "wa-S-" + h), "w").write("1" + chr(10))                                  # партия закончена
            os.makedirs(os.path.join(self.ws, h), exist_ok=True)
            open(os.path.join(self.ws, h, "summary.csv"), "w").write("a,b" + chr(10) + "1,2" + chr(10))                  # файл рядом с каталогами клеток (сводка движка)
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

    def test_retry_after_failed_check_deletes(self):
        old = pk.verify_sample
        pk.verify_sample = lambda *a, **k: ["x"]
        try:
            with self.assertRaises(SystemExit):
                pk.cmd_pack("wa-S", True, None, 1)
        finally:
            pk.verify_sample = old
        pk.cmd_pack("wa-S", True, None, 1)                                                          # архивы полны, каталоги целы — повтор проверяет и удаляет
        self.assertEqual(os.listdir(self.ws), [])

    def test_unfinished_batch_not_packed(self):
        os.remove(os.path.join(self.root, "done", "wa-S-f222"))                                       # идёт партия f222
        os.makedirs(os.path.join(self.ws, "fchk", "c1"))                                              # служебный каталог без метки
        self.assertEqual(sorted(pk.cmd_pack("wa-S", True, None, 1)), ["f222", "fchk"])
        self.assertEqual(sorted(os.listdir(self.ws)), ["f222", "fchk"])                               # f111 упакована и удалена, остальное цело
        self.assertEqual(os.listdir(os.path.join(self.root, "cells-arch", "wa-S")), ["f111.tar.zst"])

    def test_drop_keeps_dir_not_in_archive(self):
        pk.cmd_pack("wa-S", False, "f111", 1)
        open(os.path.join(self.ws, "f111", "s4+x3.ge_q23", "new.csv"), "w").write("x")                # файл, которого нет в архиве
        pk.cmd_drop("wa-S")
        self.assertTrue(os.path.isdir(os.path.join(self.ws, "f111")))                                 # не удалён
        os.remove(os.path.join(self.ws, "f111", "s4+x3.ge_q23", "new.csv"))
        pk.cmd_drop("wa-S")
        self.assertFalse(os.path.isdir(os.path.join(self.ws, "f111")))

    def test_ensure_cell_threads_share_cell(self):
        pk.cmd_pack("wa-S", True, None, 1)
        d = os.path.join(self.ws, "f111", "s4+x5.ge_q25")
        errs = []

        def go():
            try:
                pk.ensure_cell(d)
            except Exception as e:                                                                     # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=go) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(sorted(os.listdir(d)), sorted(FILES))
        self.assertFalse(any(x.startswith(".x") for x in os.listdir(self.ws)))

    def test_missing_cell_raises(self):
        with self.assertRaises(FileNotFoundError):
            pk.ensure_cell(os.path.join(self.ws, "f999", "nope"))

    def test_entries_count_matches_tar(self):
        pk.cmd_pack("wa-S", False, "f111", 1)
        self.assertEqual(pk.entries(os.path.join(self.ws, "f111")), 2 + 30 * (1 + len(FILES)))


if __name__ == "__main__":
    unittest.main()
