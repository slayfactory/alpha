"""TK-700: реестр окон отложенной выборки, охрана P&L движка, замок одного чтения. Синтетика, без движка и без P&L.
Запуск: `PYTHONUTF8=1 python3 -m unittest tools/p42/tests/test_tk700.py` (на srv — `/opt/venv/bin/python -m unittest`).
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk700_lock as lk  # noqa: E402
import tk700_win as w  # noqa: E402

SHA = "ab" * 32
H1_CMD = "/data/p42-tk700/bin/alpha-lx breakout tick --tree /data/p42-tk621/m03 --out /data/p42-tk700/out"


class Windows(unittest.TestCase):
    def test_geometry(self):
        self.assertTrue(w.check_spec())
        self.assertEqual([w.sigsx_arg(n) for n in w.NAMES], ["14:50", "14:47", "14:50", "14:49", "14:50", "14:49"])

    def test_universe_inside_c539(self):
        with tempfile.TemporaryDirectory() as d:
            sp = w.spec("m02")
            month = os.path.join(d, "u.json")
            with open(month, "w") as f:
                json.dump(["AUSDT", "BUSDT", "XAUUSDT"], f)
            old = w._SPEC
            try:
                w._SPEC = tuple(s[:6] + (month,) if s[0] == "m02" else s for s in old)
                self.assertEqual(w.universe("m02", ["AUSDT", "ZUSDT", "BUSDT"]), ["AUSDT", "BUSDT"])      # вне c539 — ни одного контракта
            finally:
                w._SPEC = old
        self.assertEqual(sp["t0"], "2026-01-12")


class Fence(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.root = self.d

    def token(self, state):
        p, nonce = lk.claim(self.root, SHA)
        if state != "claimed":
            lk.set_state(self.root, SHA, nonce, state)
        return {w.TOKEN_ENV: p, w.NONCE_ENV: nonce}

    def test_refuses_pnl_run_without_token(self):
        with self.assertRaises(w.FenceError):
            w.check_cmd(H1_CMD.split(), {})
        with self.assertRaises(w.FenceError):
            w.check_cmd(["bash", "-c", "cd /x && " + H1_CMD], {})
        with self.assertRaises(w.FenceError):
            w.check_cmd(H1_CMD, {})

    def test_allows_sigsx_only_and_other_trees(self):
        w.check_cmd(H1_CMD.split(), {"P42_SIGSX_ONLY": "1"})
        w.check_cmd(["env", "P42_SIGSX_ONLY=1"] + H1_CMD.split(), {})
        w.check_cmd("/data/p42-tk674c/bin/alpha-lx breakout tick --tree /data/p42-tk621/wa".split(), {})      # Июль–ноябрь не охраняется
        w.check_cmd(["python3", "tk604_stats.py", "/data/p42-tk700/x"], {})                                   # не команда движка

    def test_token_only_in_started_state(self):
        env = self.token("claimed")
        with self.assertRaises(w.FenceError):
            w.check_cmd(H1_CMD.split(), env)
        _, nonce = env[w.TOKEN_ENV], env[w.NONCE_ENV]
        lk.set_state(self.root, SHA, nonce, "started")
        w.check_cmd(H1_CMD.split(), env)                                                                      # жетон действует
        with self.assertRaises(w.FenceError):
            w.check_cmd(H1_CMD.split(), {w.TOKEN_ENV: env[w.TOKEN_ENV], w.NONCE_ENV: "0" * 32})              # чужой nonce
        lk.set_state(self.root, SHA, nonce, "done")
        with self.assertRaises(w.FenceError):
            w.check_cmd(H1_CMD.split(), env)                                                                  # после чтения жетон мёртв

    def test_popen_is_fenced(self):
        orig = subprocess.Popen.__init__
        saved = w._installed
        try:
            w._installed = False
            w.install_fence()
            old = os.environ.pop("P42_SIGSX_ONLY", None)
            with self.assertRaises(w.FenceError):
                subprocess.Popen(H1_CMD.split())
            if old is not None:
                os.environ["P42_SIGSX_ONLY"] = old
        finally:
            subprocess.Popen.__init__ = orig
            w._installed = saved


class Lock(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_second_claim_refused(self):
        lk.claim(self.root, SHA)
        with self.assertRaises(lk.LockError):
            lk.claim(self.root, SHA)

    def test_crash_after_mkdir_stays_refused(self):
        os.makedirs(lk.lock_dir(self.root, SHA))                      # процесс оборвался сразу после `mkdir`, lock.json нет
        with self.assertRaises(lk.LockError) as cm:
            lk.claim(self.root, SHA)
        self.assertIn("оборван после mkdir", str(cm.exception))

    def test_crash_in_started_state_stays_refused(self):
        _, nonce = lk.claim(self.root, SHA)
        lk.set_state(self.root, SHA, nonce, "started")                # процесс умер, не дойдя до done
        with self.assertRaises(lk.LockError):
            lk.claim(self.root, SHA)
        self.assertEqual(lk.read_lock(self.root, SHA)["state"], "started")

    def test_race_only_one_wins(self):
        wins, errs = [], []
        bar = threading.Barrier(8)

        def go():
            bar.wait()
            try:
                wins.append(lk.claim(self.root, SHA))
            except lk.LockError as e:
                errs.append(e)
        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual((len(wins), len(errs)), (1, 7))

    def test_state_machine(self):
        _, nonce = lk.claim(self.root, SHA)
        with self.assertRaises(lk.LockError):
            lk.set_state(self.root, SHA, "0" * 32, "started")         # чужой nonce
        lk.set_state(self.root, SHA, nonce, "started")
        with self.assertRaises(lk.LockError):
            lk.set_state(self.root, SHA, nonce, "claimed")            # назад нельзя
        lk.set_state(self.root, SHA, nonce, "done", {"out": "x"})
        with self.assertRaises(lk.LockError):
            lk.set_state(self.root, SHA, nonce, "started")            # из done выхода нет
        self.assertEqual(lk.read_lock(self.root, SHA)["out"], "x")

    def test_bad_sha(self):
        with self.assertRaises(lk.LockError):
            lk.claim(self.root, "zz")

    def test_reopen_needs_chron_and_leaves_trace(self):
        lk.claim(self.root, SHA)
        with self.assertRaises(lk.LockError):
            lk.reopen(self.root, SHA, "")
        with self.assertRaises(lk.LockError):
            lk.reopen(self.root, SHA, "x y")
        dst = lk.reopen(self.root, SHA, "chr-123", "решение координатора")
        self.assertTrue(os.path.exists(os.path.join(dst, "reopen.json")))
        self.assertTrue(os.path.exists(os.path.join(dst, "lock.json")))                                       # след прежнего чтения остаётся
        lk.claim(self.root, SHA)                                                                              # теперь можно
        dst2 = lk.reopen(self.root, SHA, "chr-123")
        self.assertNotEqual(dst, dst2)


if __name__ == "__main__":
    unittest.main()
