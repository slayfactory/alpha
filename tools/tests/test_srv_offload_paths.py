"""TK-675: проверки tools/srv-offload-paths.py (запуск: python tools/tests/test_srv_offload_paths.py). Только временные каталоги; ssh и rsync не вызываются."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("sop", Path(__file__).resolve().parent.parent / "srv-offload-paths.py")
sop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sop)


def mk(root, files):
    for rel, data in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


class T(unittest.TestCase):
    def test_under(self):
        self.assertTrue(sop.under("klines/BTCUSDT", ["klines/BTCUSDT"]))
        self.assertTrue(sop.under("klines/BTCUSDT/a.csv", ["klines/BTCUSDT"]))
        self.assertFalse(sop.under("klines/BTCUSDT2/a.csv", ["klines/BTCUSDT"]))
        self.assertFalse(sop.under("", ["klines/BTCUSDT"]))

    def test_scan_excludes_and_delete(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, {"a/x.bin": b"12345", "klines/BTCUSDT/k.csv": b"keep", "klines/ETHUSDT/k.csv": b"abc", "top.txt": b"z"})
            os.symlink("top.txt", os.path.join(d, "lnk"))
            ex = ["klines/BTCUSDT"]
            s, files = sop.scan(d, ex)
            self.assertEqual((s["files"], s["symlinks"], s["bytes"]), (3, 1, 9))
            self.assertEqual(len(files), 3)
            sop.delete_moved(d, ex)
            self.assertTrue(os.path.exists(os.path.join(d, "klines/BTCUSDT/k.csv")))
            self.assertFalse(os.path.exists(os.path.join(d, "a")))
            self.assertFalse(os.path.exists(os.path.join(d, "klines/ETHUSDT")))
            self.assertFalse(os.path.lexists(os.path.join(d, "lnk")))
            s2, _ = sop.scan(d, ex)
            self.assertEqual(s2["files"], 0)

    def test_delete_whole(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "s")
            mk(src, {"a/b": b"1"})
            sop.delete_moved(src, [])
            self.assertFalse(os.path.exists(src))

    def test_parse_ls_skips_excluded_subtree(self):
        text = (
            "box/dest:\ntotal 8\n-rw-r--r-- 1 u u 10 Nov 14 16:19 a.csv\nlrwxrwxrwx 1 u u 3 Nov 14 16:19 l -> a\ndrwxr-xr-x 2 u u 2 Nov 14 16:19 binance\n\n"
            "box/dest/binance:\ntotal 8\n-rw-r--r-- 1 u u 7 Nov 14 16:19 e.csv\ndrwxr-xr-x 2 u u 2 Nov 14 16:19 BTCUSDT\n\n"
            "box/dest/binance/BTCUSDT:\ntotal 8\n-rw-r--r-- 1 u u 100 Nov 14 16:19 k.csv\n\n"
        )
        self.assertEqual(sop.parse_ls(text, "box/dest", ["binance/BTCUSDT"]), {"files": 2, "symlinks": 1, "bytes": 17})
        self.assertEqual(sop.parse_ls(text, "box/dest", []), {"files": 3, "symlinks": 1, "bytes": 117})

    @unittest.skipIf(os.name == "nt", "readlink на Windows даёт \\?\-пути; проверка — на Linux (srv)")
    def test_preflight_finds_outside_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, {"move/f": b"1", "keep/g": b"2"})
            os.symlink(os.path.join(d, "move", "f"), os.path.join(d, "keep", "lnk"))
            orig = sop.os.walk
            try:
                sop.os.walk = lambda root, *a, **k: orig(d) if root == "/data" else iter(())
                self.assertEqual(sop.preflight([{"src": os.path.join(d, "move")}]), 1)
                self.assertEqual(sop.preflight([{"src": os.path.join(d, "keep")}]), 0)
            finally:
                sop.os.walk = orig


if __name__ == "__main__":
    unittest.main()
