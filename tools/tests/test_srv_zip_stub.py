"""TK-694: проверки tools/srv-zip-stub.py (запуск: python tools/tests/test_srv_zip_stub.py). Только временные каталоги; «бокс» — локальный каталог.
Пустышки (0 блоков) проверяются только на Linux."""
import argparse
import hashlib
import importlib.util
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("szs", Path(__file__).resolve().parent.parent / "srv-zip-stub.py")
szs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(szs)

LINUX = sys.platform.startswith("linux")


class LocalBox:
    def __init__(self, root):
        self.root = root

    def sha256(self, paths):
        res = {}
        for p in paths:
            f = os.path.join(self.root, p)
            if os.path.exists(f):
                res[p] = hashlib.sha256(open(f, "rb").read()).hexdigest()
        return res

    def fetch(self, path, dst):
        shutil.copyfile(os.path.join(self.root, path), dst)


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.loc, self.boxd, self.store = (os.path.join(self.d, x) for x in ("loc", "box", "store"))
        self.box = LocalBox(self.boxd)
        self.mt = 1_794_995_293_276_192_300
        for coin, day, data in (("AAAUSDT", "2026-07-01", b"x" * 5000), ("AAAUSDT", "2026-07-02", b"y" * 7000), ("BBBUSDT", "2026-07-01", b"z" * 3000)):
            name = "%s-aggTrades-%s.zip" % (coin, day)
            p = os.path.join(self.loc, coin, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(data)
            os.utime(p, ns=(self.mt, self.mt))
            bp = os.path.join(self.boxd, "arch", coin, name)
            os.makedirs(os.path.dirname(bp), exist_ok=True)
            shutil.copyfile(p, bp)
            self.tk(coin, day, len(data), self.mt)

    def tk(self, coin, day, size, mt_ns):
        p = os.path.join(self.store, coin, "%s-%s.tk" % (coin, day))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(szs.MAGIC + struct.pack("<I", 2) + struct.pack("<QQ", size, mt_ns // 10 ** 9) + struct.pack("<I", mt_ns % 10 ** 9) + b"\0" * 64)

    def ns(self, **kw):
        base = dict(roots=["%s=arch" % self.loc], store=self.store, out=os.path.join(self.d, "m.jsonl"))
        base.update(kw)
        return argparse.Namespace(**base)

    def test_manifest_verified(self):
        self.assertEqual(szs.cmd_manifest(self.ns(), self.box), 0)
        recs = szs.read_manifest(os.path.join(self.d, "m.jsonl"))
        self.assertEqual(len(recs), 3)
        self.assertTrue(all(r["verified"] for r in recs))
        r = [r for r in recs if r["path"].endswith("BBBUSDT-aggTrades-2026-07-01.zip")][0]
        self.assertEqual((r["bytes"], r["mtime_ns"], r["sha256"]), (3000, self.mt, hashlib.sha256(b"z" * 3000).hexdigest()))

    def test_box_mismatch_and_missing_tk_not_verified(self):
        open(os.path.join(self.boxd, "arch", "AAAUSDT", "AAAUSDT-aggTrades-2026-07-01.zip"), "wb").write(b"broken")
        os.remove(os.path.join(self.store, "BBBUSDT", "BBBUSDT-2026-07-01.tk"))
        self.assertEqual(szs.cmd_manifest(self.ns(), self.box), 1)
        recs = {os.path.basename(r["path"]): r for r in szs.read_manifest(os.path.join(self.d, "m.jsonl"))}
        self.assertFalse(recs["AAAUSDT-aggTrades-2026-07-01.zip"]["verified"])
        self.assertFalse(recs["BBBUSDT-aggTrades-2026-07-01.zip"]["verified"])
        self.assertTrue(recs["AAAUSDT-aggTrades-2026-07-02.zip"]["verified"])

    def test_published_checksum_mismatch(self):
        open(os.path.join(self.loc, "AAAUSDT", "AAAUSDT-aggTrades-2026-07-01.zip.CHECKSUM"), "w").write("0" * 64 + "  AAAUSDT-aggTrades-2026-07-01.zip\n")
        szs.cmd_manifest(self.ns(), self.box)
        recs = {os.path.basename(r["path"]): r for r in szs.read_manifest(os.path.join(self.d, "m.jsonl"))}
        self.assertFalse(recs["AAAUSDT-aggTrades-2026-07-01.zip"]["verified"])

    def test_stub_and_restore(self):
        szs.cmd_manifest(self.ns(), self.box)
        m = os.path.join(self.d, "m.jsonl")
        orig = {os.path.join(self.loc, c, "%s-aggTrades-%s.zip" % (c, dd)): hashlib.sha256(open(os.path.join(self.loc, c, "%s-aggTrades-%s.zip" % (c, dd)), "rb").read()).hexdigest()
                for c, dd in (("AAAUSDT", "2026-07-01"), ("AAAUSDT", "2026-07-02"), ("BBBUSDT", "2026-07-01"))}
        szs.cmd_stub(argparse.Namespace(manifest=m, store=self.store, go=False))
        self.assertTrue(all(os.path.getsize(p) > 0 and open(p, "rb").read(1) != b"\0" for p in orig))   # без --go ничего не тронуто
        szs.cmd_stub(argparse.Namespace(manifest=m, store=self.store, go=True))
        for p in orig:
            st = os.lstat(p)
            self.assertEqual(st.st_mtime_ns, self.mt)
            self.assertGreater(st.st_size, 0)
            if LINUX:
                self.assertTrue(szs.is_stub(st))
        # метаданные пустышки равны заголовку .tk: движок видит тот же SrcMeta
        self.assertTrue(szs.tk_ok(self.store, "AAAUSDT", "2026-07-01", os.lstat(next(iter(orig)))))
        if LINUX:
            szs.cmd_restore(argparse.Namespace(manifest=m, only=None), self.box)
            for p, h in orig.items():
                self.assertEqual(hashlib.sha256(open(p, "rb").read()).hexdigest(), h)
                self.assertEqual(os.lstat(p).st_mtime_ns, self.mt)
                self.assertFalse(szs.is_stub(os.lstat(p)))

    def test_changed_file_not_stubbed(self):
        szs.cmd_manifest(self.ns(), self.box)
        p = os.path.join(self.loc, "AAAUSDT", "AAAUSDT-aggTrades-2026-07-02.zip")
        os.utime(p, ns=(self.mt + 1000, self.mt + 1000))
        szs.cmd_stub(argparse.Namespace(manifest=os.path.join(self.d, "m.jsonl"), store=self.store, go=True))
        self.assertEqual(open(p, "rb").read(), b"y" * 7000)

    def test_stub_refused_without_tk(self):
        szs.cmd_manifest(self.ns(), self.box)
        os.remove(os.path.join(self.store, "AAAUSDT", "AAAUSDT-2026-07-02.tk"))
        szs.cmd_stub(argparse.Namespace(manifest=os.path.join(self.d, "m.jsonl"), store=self.store, go=True))
        self.assertEqual(open(os.path.join(self.loc, "AAAUSDT", "AAAUSDT-aggTrades-2026-07-02.zip"), "rb").read(), b"y" * 7000)

    def test_resume_skips_verified(self):
        szs.cmd_manifest(self.ns(), self.box)
        szs.cmd_manifest(self.ns(), self.box)
        self.assertEqual(len(szs.read_manifest(os.path.join(self.d, "m.jsonl"))), 3)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
