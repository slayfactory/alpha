#!/usr/bin/env python3
"""Тесты импорта готовых клеток h775 в БД летописи (TK-674): разбор `params` движка, строка ↔ params, ключ клетки по смыслу, смысл фильтров. Без srv и без движка."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk674_axes as ax  # noqa: E402
import tk674_import as im  # noqa: E402

BASE = {"L": "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20",
        "S": "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05"}
# `meta.json` / params: база шорта (turbo, без поля tl), клетка ek = 5 шорта (бинарь TK-660), фильтр N24_q40 лонга (turbo)
P_TURBO_S = 'Params { sig: SigCfg { lv: LevelCfg { tf_min: 60, k: 2, d_pm: 500, n: 2, tau_pm: 200, l_days: 7, pk: true }, natr_p: 30, natr_min_pm: 400, t1h_u: 50000000000, t24_u: 1200000000000, track_pm: 2000, e_pm: 150, color: true, f_pm: 100, r_min: 15, ban: true, ap: 0, pb: true, fr_h_min: 240, fr_k: 120, fr_share: 50, fr_b_pm: 500 }, exit: ExitCfg { natr_p: 30, n_tp: 8, t_low_m: 1000, t_high_m: 5000, q_pct: 50, be: true, a_m: 2000, r_m: 2000, s_pm: 5000, slip_bps: 1, best_order: false, lat_ms: 4, lat_exit_ms: 4, tp_gamma_m: 1000, tp_kappa_m: 0, natr_src: 0, ent_w_ms: 5, ent_x_bp: 0, ls_m: 0, tx_min: 20, tx_x_pm: 100, rt_f_pm: 200, rt_s: 2, be_mode: 1, e2_pm: 50, sn_m: 0, sg: 0, sb_m: 0, sr_m: 0, sd_m: 0, tj_m: 0 }, port: PortCfg { cool_min: 5, m_stops: 3, mm_min: 60, s_glob: 4, w_min: 10, ww_min: 60, p: 3, reentry: 0, busy: true }, filt: FilterCfg { name: "", hi: None, v: None } }'
P_EK5_S = 'Params { sig: SigCfg { lv: LevelCfg { tf_min: 60, k: 2, d_pm: 500, n: 2, tau_pm: 200, l_days: 7, pk: true }, natr_p: 30, natr_min_pm: 400, t1h_u: 50000000000, t24_u: 1200000000000, track_pm: 2000, e_pm: 150, color: true, f_pm: 100, r_min: 15, ban: true, ap: 0, pb: true, fr_h_min: 240, fr_k: 120, fr_share: 50, fr_b_pm: 500 }, exit: ExitCfg { natr_p: 30, n_tp: 8, t_low_m: 1000, t_high_m: 5000, q_pct: 50, be: true, a_m: 2000, r_m: 2000, s_pm: 5000, slip_bps: 1, best_order: false, lat_ms: 4, lat_exit_ms: 4, tp_gamma_m: 1000, tp_kappa_m: 0, natr_src: 0, ent_w_ms: 5, ent_x_bp: 0, ls_m: 0, tx_min: 20, tx_x_pm: 100, rt_f_pm: 200, rt_s: 2, be_mode: 1, e2_pm: 50, sn_m: 0, sg: 0, sb_m: 0, sr_m: 0, sd_m: 0, tj_m: 0, tl: 0 }, port: PortCfg { cool_min: 5, m_stops: 3, mm_min: 60, s_glob: 4, w_min: 10, ww_min: 60, p: 3, reentry: 0, busy: true }, filt: FilterCfg { name: "", hi: None, v: None }, ek: 5 }'
P_N24_L = 'Params { sig: SigCfg { lv: LevelCfg { tf_min: 60, k: 2, d_pm: 500, n: 2, tau_pm: 200, l_days: 7, pk: true }, natr_p: 30, natr_min_pm: 400, t1h_u: 50000000000, t24_u: 1200000000000, track_pm: 2000, e_pm: 300, color: true, f_pm: 100, r_min: 15, ban: true, ap: 0, pb: true, fr_h_min: 240, fr_k: 120, fr_share: 50, fr_b_pm: 500 }, exit: ExitCfg { natr_p: 30, n_tp: 8, t_low_m: 1000, t_high_m: 5000, q_pct: 50, be: true, a_m: 2000, r_m: 2000, s_pm: 5000, slip_bps: 1, best_order: false, lat_ms: 4, lat_exit_ms: 4, tp_gamma_m: 1000, tp_kappa_m: 0, natr_src: 0, ent_w_ms: 5, ent_x_bp: 0, ls_m: 0, tx_min: 0, tx_x_pm: 0, rt_f_pm: 200, rt_s: 20, be_mode: 0, e2_pm: 0, sn_m: 0, sg: 0, sb_m: 0, sr_m: 0, sd_m: 0, tj_m: 0 }, port: PortCfg { cool_min: 5, m_stops: 3, mm_min: 60, s_glob: 4, w_min: 10, ww_min: 60, p: 3, reentry: 0, busy: true }, filt: FilterCfg { name: "N24_q40", hi: Some(false), v: Some(0.5) } }'


class Params(unittest.TestCase):
    def test_flat(self):
        f = im.flat_params(P_TURBO_S)
        self.assertEqual(f["sig.lv.tf_min"], 60)
        self.assertEqual(f["sig.t24_u"], 1200000000000)
        self.assertEqual(f["exit.s_pm"], 5000)
        self.assertEqual(f["exit.rt_s"], 2)
        self.assertIs(f["sig.lv.pk"], True)
        self.assertEqual((f["filt.name"], f["filt.hi"], f["filt.v"]), ("", None, None))
        self.assertNotIn("exit.tl", f)                       # turbo до TK-603 поле tl не печатает
        self.assertEqual(im.flat_params(P_EK5_S)["ek"], 5)
        self.assertEqual(im.flat_params(P_EK5_S)["exit.tl"], 0)
        g = im.flat_params(P_N24_L)
        self.assertEqual((g["filt.name"], g["filt.hi"], g["filt.v"]), ("N24_q40", ("some", False), ("some", 0.5)))

    def test_check_ok(self):
        self.assertEqual(im.check_params(BASE["S"] + " t24=12", P_TURBO_S), [])
        self.assertEqual(im.check_params(BASE["S"] + " t24=12 ek=5", P_EK5_S), [])
        self.assertEqual(im.check_params(BASE["L"] + " t24=12 fa=N24_q40 fo=lo fv=0.5", P_N24_L), [])

    def test_check_mismatch(self):
        self.assertTrue(im.check_params(BASE["S"] + " t24=12 s=3", P_TURBO_S))                          # значение ключа
        self.assertTrue(im.check_params(BASE["S"] + " t24=12 ek=10", P_EK5_S))
        self.assertTrue(im.check_params(BASE["S"], P_TURBO_S))                                          # без t24=12: пул по умолчанию 0
        self.assertTrue(im.check_params(BASE["S"] + " t24=12 tu=60", P_EK5_S))                          # поле, которого нет в строке базы
        self.assertTrue(im.check_params(BASE["L"] + " t24=12 fa=N24_q40 fo=lo fv=0.6", P_N24_L))        # порог фильтра
        self.assertTrue(im.check_params(BASE["L"] + " t24=12 fa=N24_q20 fo=lo fv=0.5", P_N24_L))        # имя столбца
        self.assertTrue(im.check_params(BASE["L"] + " t24=12", P_N24_L))                                # фильтр есть в params, нет в строке
        self.assertTrue(im.check_params(BASE["S"] + " t24=12 zz=1", P_TURBO_S))                         # неизвестный ключ

    def test_round_half_away(self):
        self.assertEqual(im.enc("pm", -0.75), -750)
        self.assertEqual(im.enc("pm", 0.025), 25)
        self.assertEqual(im.enc("big", 12.0), 1200000000000)
        self.assertEqual(im.enc("h", 4.0), 240)
        self.assertIs(im.enc("b", 1.0), True)


class Keys(unittest.TestCase):
    def test_base_key_is_plan_key(self):
        for s in "LS":
            self.assertEqual(im.ckey_of(BASE[s] + " t24=12", []), ax.canon(BASE[s] + " t24=12"))

    def test_same_cell_two_lines(self):
        # пул TK-638: `t24 t1h` в строке вместо u.extra — тот же ключ, что у базы
        self.assertEqual(im.ckey_of(BASE["S"] + " t24=12 t1h=0.5", []), im.ckey_of(BASE["S"] + " t24=12", []))
        self.assertNotEqual(im.ckey_of(BASE["S"] + " t24=25 t1h=0.5", []), im.ckey_of(BASE["S"] + " t24=12", []))

    def test_lw_only_for_weeks(self):
        k1 = im.ckey_of(BASE["L"] + " t24=12 lx=1 tf=60 lw=5", [])
        self.assertEqual(k1, im.ckey_of(BASE["L"] + " t24=12 lx=1", []))
        self.assertNotIn("lw=", k1)
        self.assertIn("lw=5", im.ckey_of(BASE["L"] + " t24=12 lx=2 tf=60 lw=5", []))

    def test_filter_by_meaning(self):
        a = im.ckey_of(BASE["S"] + " t24=12 fa=TPM60_le_q60 fo=lo fv=0.5", [("tpm60", "<=", 2.4)])
        b = im.ckey_of(BASE["S"] + " t24=12 fa=f003 fo=lo fv=0.5", [("tpm60", "<=", 2.4)])
        self.assertEqual(a, b)                                # имя столбца таблицы не входит в ключ
        self.assertTrue(a.endswith("F=tpm60<=2.4"))
        self.assertNotEqual(a, im.ckey_of(BASE["S"] + " t24=12", []))

    def test_label_and_patch(self):
        base = ax.canon(BASE["S"] + " t24=12")
        ck = im.ckey_of(BASE["S"] + " t24=12 s=3 e=0.3", [])
        self.assertEqual(im.diff_patch(ck, base), {"s": "3", "e": "0.3"})         # e=0.3 = умолчание движка: из ключа выпал, в заплатке есть
        self.assertEqual(im.cell_label(base, base), "base")
        self.assertEqual(im.cell_label(im.ckey_of(BASE["S"] + " t24=12 s=3", [("n24", ">=", 85000)]), base), "s3+n24>=85000")


class Semantics(unittest.TestCase):
    def test_tk654_filters(self):
        f = im.tk654_filt
        self.assertEqual(f(dict(cid="TPM60_le_q60", kind="lad", col="tpm60", dir="le", X=2.4)), [("tpm60", "<=", 2.4)])
        self.assertEqual(f(dict(cid="ST_ge_q20", kind="lad", col="spread_tau", dir="ge", X=0.1)), [("spread_tau", ">=", 0.1)])
        self.assertEqual(f(dict(cid="K20_t035", kind="bk", K=20, tau=0.035)), [("bk20", ">=", 0.035)])
        self.assertEqual(f(dict(cid="SHARP", kind="named", X=1.2)), [("sharp", "==", 1.2)])
        self.assertEqual(f(dict(cid="CLEAN", kind="named")), [("pierce_ep", "==", 0)])

    def test_tk668_filters(self):
        thr = {"X24": {"0.75": 0.5}}
        f = im.tk668_filt
        self.assertEqual(f(dict(fam="E5", col="R", op="lt", x=1.0, q=None), thr), [("R", ">=", 1.0)])          # исключали R < 1 → оставляем R ≥ 1
        self.assertEqual(f(dict(fam="EP", col="X24", op="gt", x=None, q=0.75), thr), [("X24", "<=", 0.5)])
        self.assertEqual(f(dict(fam="B", col="bz5", op="abs", x=2.0, q=None), thr), [("abs_bz5", "<", 2.0)])
        self.assertEqual(f(dict(fam="B", col="bz5", op="neg", x=2.0, q=None), thr), [("bz5", ">", -2.0)])
        self.assertEqual(f(dict(fam="B", col="bz5", op="pos", x=2.0, q=None), thr), [("bz5", "<", 2.0)])


class Dedupe(unittest.TestCase):
    def _o(self, task, cid, bin_, trades, sig):
        return dict(task=task, cid=cid, row=dict(side="long", ckey="k", bin=bin_, trades=trades, usd=1.0, extra=dict(trades_sig=sig)))

    def test_groups(self):
        rows = [self._o("TK-638", "D12_H0.5", "a" * 32, 428, "s"), self._o("TK-643", "base", "a" * 32, 428, "s"), self._o("TK-652", "base", "b" * 32, 428, "s"),
                self._o("TK-650", "base", "a" * 32, 428, "x")]
        uniq, same_bin, cross = im.dedupe(rows)
        self.assertEqual(len(uniq), 2)                                             # (long, k, a…) и (long, k, b…)
        self.assertEqual([d["sig_equal"] for d in same_bin], [True, False])        # TK-650 расходится — должно попасть в отчёт
        self.assertEqual(len(cross), 1)


if __name__ == "__main__":
    unittest.main()
