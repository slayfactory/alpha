"""TK-675: проверки tools/srv-offload-paths.py (запуск: python tools/tests/test_srv_offload_paths.py). Только временные каталоги; ssh и rsync не вызываются."""
import importlib.util
import json
import os
import types
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


class Patch:
    """Подмена атрибутов модуля на время теста."""

    def __init__(self, **kw):
        self.kw = kw

    def __enter__(self):
        self.old = {k: getattr(sop, k) for k in self.kw}
        for k, v in self.kw.items():
            setattr(sop, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(sop, k, v)


def run_move(src, late_write=None, **unit):
    """move_one с --delete без сети: rsync и ssh подменены, бокс «равен» локальной сводке до копии; late_write — файл, записанный во время повторного rsync -n."""
    def fake_run(cmd, **kw):
        if late_write and "-aHn" in cmd:
            Path(late_write).write_bytes(b"late")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    excl = unit.get("exclude", [])
    box = {k: v for k, v in sop.scan(src, excl)[0].items() if k != "max_mtime_ns"}
    man = src + ".manifest.jsonl"
    a = types.SimpleNamespace(delete=True, sample=0, bwlimit=1)
    with Patch(busy=lambda *x: [], ssh=lambda *x, **k: types.SimpleNamespace(returncode=0, stdout=""), parse_ls=lambda *x: box,
               subprocess=types.SimpleNamespace(run=fake_run)):
        ok = sop.move_one(dict(unit, src=src, dest="box/x"), a, man)
    return ok, json.loads(Path(man).read_text(encoding="utf-8").splitlines()[-1])


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

    def test_delete_with_excludes_removes_dir_symlinks(self):
        """FIX 3: ссылка на каталог в переносимой части удаляется, `deleted` считает и ссылки."""
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "s")
            mk(src, {"a/x.bin": b"12345", "klines/BTCUSDT/k.csv": b"keep", "klines/ETHUSDT/k.csv": b"abc"})
            os.symlink(os.path.join(src, "a"), os.path.join(src, "dlnk"), target_is_directory=True)
            os.symlink(os.path.join(src, "klines", "ETHUSDT"), os.path.join(src, "klines", "dl2"), target_is_directory=True)
            ex = ["klines/BTCUSDT"]
            self.assertEqual(sop.scan(src, ex)[0]["symlinks"], 2)
            ok, rec = run_move(src, exclude=ex)
            self.assertTrue(ok)
            self.assertFalse(os.path.lexists(os.path.join(src, "dlnk")))
            self.assertFalse(os.path.lexists(os.path.join(src, "klines", "dl2")))
            self.assertTrue(os.path.exists(os.path.join(src, "klines/BTCUSDT/k.csv")))
            self.assertEqual(sop.scan(src, ex)[0]["symlinks"], 0)
            self.assertEqual((rec["deleted"], rec["left_after_delete"], rec["left_symlinks_after_delete"]), (True, 0, 0))

    def test_deleted_false_while_symlinks_remain(self):
        """FIX 3: если ссылки остались, в манифесте `deleted: false`."""
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "s")
            mk(src, {"a/x.bin": b"1", "keep/k": b"2"})
            os.symlink(os.path.join(src, "a"), os.path.join(src, "dlnk"), target_is_directory=True)

            def old_delete(s, ex):  # удаление, не снимающее ссылки на каталоги
                os.unlink(os.path.join(s, "a", "x.bin"))

            with Patch(delete_moved=old_delete):
                ok, rec = run_move(src, exclude=["keep"])
            self.assertEqual((rec["deleted"], rec["left_after_delete"], rec["left_symlinks_after_delete"]), (False, 0, 1))

    def test_rescan_before_delete_refuses_changed_path(self):
        """FIX 4: файл, записанный после послекопийной сводки, не удаляется — путь остаётся целым."""
        for ex in ([], ["keep"]):
            with tempfile.TemporaryDirectory() as d:
                src = os.path.join(d, "s")
                mk(src, {"a/x.bin": b"12345", "keep/k": b"2"})
                late = os.path.join(src, "a", "late.bin")
                ok, rec = run_move(src, late_write=late, exclude=ex)
                self.assertFalse(ok)
                self.assertTrue(os.path.exists(late))
                self.assertTrue(os.path.exists(os.path.join(src, "a", "x.bin")))
                self.assertEqual((rec["deleted"], rec["verified"]), (False, False))
                self.assertIn("менялся после сверки", rec["why_not"])
                self.assertNotIn("left_after_delete", rec)

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

    @unittest.skipIf(os.name == "nt", "readlink на Windows даёт \\?\-пути; проверка — на Linux (srv)")
    def test_preflight_walks_excluded_subtrees(self):
        """FIX 2: ссылка из исключённого (остающегося) поддерева в переносимую часть найдена; внутри переносимой части и внутрь исключения — не считаются."""
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "p")
            mk(src, {"jul/agg/f": b"1", "jul/klines/BTC/k": b"2", "jul/klines/deep/x/y": b"3", "aug/g": b"4"})
            unit = [{"src": src + "/", "exclude": ["jul/klines/"]}]
            self.assertEqual(sop.preflight(unit, roots=(d,)), 0)
            os.symlink(os.path.join(src, "aug", "g"), os.path.join(src, "jul", "agg", "in-moved"))  # обе стороны уходят
            os.symlink(os.path.join(src, "jul", "klines", "BTC", "k"), os.path.join(d, "to-kept"))  # цель остаётся
            self.assertEqual(sop.preflight(unit, roots=(d,)), 0)
            os.symlink(os.path.join(src, "jul", "agg", "f"), os.path.join(src, "jul", "klines", "deep", "x", "lnk"))
            self.assertEqual(sop.preflight(unit, roots=(d,)), 1)
            os.unlink(os.path.join(src, "jul", "klines", "deep", "x", "lnk"))
            os.symlink(os.path.join(src, "aug"), os.path.join(src, "jul", "klines", "dlnk"), target_is_directory=True)
            self.assertEqual(sop.preflight(unit, roots=(d,)), 1)

    @unittest.skipIf(os.name == "nt", "readlink на Windows даёт \\?\-пути; проверка — на Linux (srv)")
    def test_delete_gated_on_preflight(self):
        """FIX 1: --delete без --preflight отказывает до первого пути, если в переносимое ведут ссылки; без ссылок — идёт."""
        with tempfile.TemporaryDirectory() as d:
            mk(d, {"move/f": b"1", "keep/g": b"2"})
            sp = os.path.join(d, "spec.json")
            Path(sp).write_text(json.dumps([{"src": os.path.join(d, "move"), "dest": "box/move"}]))
            calls = []
            argv = [sp, "--manifest", os.path.join(d, "m.jsonl")]
            with Patch(ROOTS=(d,), move_one=lambda u, a, m: calls.append(u["src"]) or True):
                self.assertEqual(sop.main(argv + ["--delete"]), 0)
                self.assertEqual(len(calls), 1)
                os.symlink(os.path.join(d, "move", "f"), os.path.join(d, "keep", "lnk"))
                self.assertEqual(sop.main(argv + ["--delete"]), 1)
                self.assertEqual(len(calls), 1)
                self.assertTrue(os.path.exists(os.path.join(d, "move", "f")))
                self.assertEqual(sop.main(argv), 0)  # без --delete копия и сверка разрешены
                self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
