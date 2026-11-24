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
import tk700_warm20 as wm  # noqa: E402
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


class Holdout(unittest.TestCase):
    """Фильтры и ftab раннера: те же правила, что `tk674_run._pass` (пустое значение проходит, «определён и не проходит» — исключён)."""

    def setUp(self):
        import numpy as np
        self.np = np
        nan = float("nan")
        self.tab = {"sym": np.array(["A", "B", "C", "D", "E"], dtype=object), "ms": np.array([1, 2, 3, 4, 5], dtype=np.int64),
                    "tpm60": np.array([0.1, 0.5, nan, 0.9, 0.3]), "n24": np.array([1e5, 5e5, 2e5, nan, 3e5]), "m24": np.array([0.0, 0.0, 1.0, 0.0, 0.0]),
                    "cons60": np.array([0.0, 0.0, 1.0, nan, 0.0]), "mv5": np.array([2.0, 0.5, 3.0, 1.0, nan])}

    def test_le_ge_nan_passes(self):
        import tk700_holdout as h
        self.assertEqual(h.passes(self.tab, [("tpm60", "<=", 0.5)]).tolist(), [True, True, True, False, True])       # NaN (C) проходит
        self.assertEqual(h.passes(self.tab, [("tpm60", ">=", 0.5)]).tolist(), [False, True, True, True, False])

    def test_n24_uses_m24(self):
        import tk700_holdout as h
        # C: m24 = 1 (значение неопределено) — проходит при любом пороге; D: n24 пусто — проходит; A: 1e5 < 2.5e5 — исключён
        self.assertEqual(h.passes(self.tab, [("n24", ">=", 2.5e5)]).tolist(), [False, True, True, True, True])

    def test_sharp_legacy(self):
        import tk700_holdout as h
        self.assertEqual(h.passes(self.tab, [("sharp", "==", 1.0)]).tolist(), [True, False, False, True, True])       # D: cons60 пусто, E: mv5 пусто → проходят

    def test_conjunction_and_missing_column(self):
        import tk700_holdout as h
        self.assertEqual(h.passes(self.tab, [("tpm60", "<=", 0.5), ("n24", ">=", 2.5e5)]).tolist(), [False, True, True, False, True])
        with self.assertRaises(SystemExit):
            h.passes(self.tab, [("nope", "<=", 1.0)])

    def test_filter_cols_deterministic(self):
        import tk700_holdout as h
        c1 = dict(filter=[["b", ">=", 1.0]], id="x")
        c2 = dict(filter=[["a", "<=", 2.0], ["b", ">=", 1.0]], id="y")
        self.assertEqual(h.filter_cols([c1, c2]), h.filter_cols([c2, c1]))
        self.assertEqual(sorted(h.filter_cols([c1, c2]).values()), ["f000", "f001"])

    def test_split_ckey(self):
        import tk700_freeze as fz
        line, parts = fz.split_ckey("a=1 b=2 F=n24>=250000&tpm60<=0.5")
        self.assertEqual(line, "a=1 b=2")
        self.assertEqual(sorted(parts), [["n24", ">=", 250000.0], ["tpm60", "<=", 0.5]])
        self.assertEqual(fz.split_ckey("a=1")[1], [])


class Mass(unittest.TestCase):
    """Массовый прогон клеток (В-183): партии, кластерная выборка, замок по запуску."""

    def test_batches_limits(self):
        import tk700_mass as ms
        cells = [dict(cid=i, sig="s%02d" % (i // 3)) for i in range(30)]                  # 10 ключей по 3 клетки
        b = ms.make_batches(cells, vmax=7, sigmax=2)
        self.assertEqual([c["cid"] for x in b for c in x], list(range(30)))
        for x in b:
            self.assertLessEqual(len(x), 7)
            self.assertLessEqual(len({c["sig"] for c in x}), 2)
        self.assertEqual(ms.bkey_of(b[0]), ms.bkey_of(list(b[0])))
        self.assertNotEqual(ms.bkey_of(b[0]), ms.bkey_of(b[1]))

    def test_pick_sigs_whole_groups(self):
        import tk700_mass as ms
        cells = [dict(cid=i, sig="s%02d" % (i // 3)) for i in range(30)]
        p1, p2 = ms.pick_sigs(cells, 3, 5), ms.pick_sigs(cells, 3, 5)
        self.assertEqual(p1, p2)
        self.assertEqual(len(p1), 9)
        self.assertEqual(len({c["sig"] for c in p1}), 3)

    def test_run_lock_attempts(self):
        import tk700_mass as ms
        with tempfile.TemporaryDirectory() as d:
            m = {"lock": d}
            key, _p, nonce, att = ms.claim_run(m, "r1", {})
            self.assertEqual(att, 1)
            with self.assertRaises(SystemExit):                                           # та же попытка ещё идёт (pid жив)
                ms.claim_run(m, "r1", {})
            lk.set_state(d, key, nonce, "started")
            lk.set_state(d, key, nonce, "done")
            _k2, _p2, _n2, att2 = ms.claim_run(m, "r1", {})                                # повторный запуск того же run-id — новая попытка, отказа «одно чтение» нет
            self.assertEqual(att2, 2)
            self.assertEqual(ms.claim_run(m, "r2", {})[3], 1)


class Warm20(unittest.TestCase):
    """Ворота прогрева: строки эталона (T0 = 17.06) сдвигаются на 6 суток к координатам wa20 (T0 = 11.06)."""
    HEAD = "sym,level,sig_ms,dist_e8,level_px_e8,born,end,first_bar,natr_e8,members\n"

    def _write(self, d, name, rows):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(self.HEAD + "".join(rows))
        return p

    def test_shift_and_diff(self):
        sh_ms, sh_min = wm.T0_SHIFT_MS, wm.T0_SHIFT_MIN
        fb = sh_min // 60
        ref = ["AUSDT,3,1209600592,100,500,20100,20280,326,70,332:6099|326:6093\n",
               "BUSDT,1,1209601000,200,600,100,300,10,80,12:6000\n"]
        new = ["AUSDT,9,%d,100,500,%d,%d,%d,70,%d:6099|%d:6093\n" % (1209600592 + sh_ms, 20100 + sh_min, 20280 + sh_min, 326 + fb, 332 + fb, 326 + fb),
               "BUSDT,7,%d,200,600,%d,%d,%d,80,%d:6000\n" % (1209601000 + sh_ms, 100 + sh_min, 300 + sh_min, 10 + fb, 12 + fb)]
        with tempfile.TemporaryDirectory() as d:
            c = wm.compare(self._write(d, "n.csv", new), self._write(d, "r.csv", ref), 60)
            self.assertTrue(c["ok"], c)
            self.assertEqual((c["new_rows"], c["ref_rows"]), (2, 2))
            new[1] = new[1].replace(",200,600,", ",201,600,")                     # dist_e8 другой → расхождение в обе стороны
            c = wm.compare(self._write(d, "n.csv", new), self._write(d, "r.csv", ref), 60)
            self.assertFalse(c["ok"])
            self.assertEqual((c["only_new"], c["only_ref"]), (1, 1))

    def test_end_edge_and_base_keys(self):
        with tempfile.TemporaryDirectory() as d:
            sh_ms, sh_min = wm.T0_SHIFT_MS, wm.T0_SHIFT_MIN
            fb = sh_min // 60
            ref = ["AUSDT,3,1209600592,100,500,20100,20280,326,70,332:6099\n"]
            new = ["AUSDT,9,%d,100,500,%d,4294967295,%d,70,%d:6099\n" % (1209600592 + sh_ms, 20100 + sh_min, 326 + fb, 332 + fb)]      # другой `end` (край данных)
            n, r = self._write(d, "n.csv", new), self._write(d, "r.csv", ref)
            c = wm.compare(n, r, 60, None, {("AUSDT", str(1209600592 + sh_ms))})
            self.assertFalse(c["ok"])
            self.assertEqual((c["only_new_ex_end"], c["only_ref_ex_end"]), (0, 0))        # без `end` строки совпали
            self.assertEqual(c["diff_on_base"], 2)
            c = wm.compare(n, r, 60, None, set())
            self.assertEqual(c["diff_on_base"], 0)


    def test_before_day20_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            early = ["AUSDT,0,%d,1,2,3,4,5,6,7:8\n" % (19 * 86400 * 1000)]           # wa20 сутки 19 — в сравнение не входит
            c = wm.compare(self._write(d, "n.csv", early), self._write(d, "r.csv", []), 60)
            self.assertTrue(c["ok"])
            self.assertEqual(c["new_rows"], 0)


if __name__ == "__main__":
    unittest.main()
