//! Тесты TK-697 (банк № 9, «тейк у следующего уровня»): ключи `tn` (доля позиции, %) и `tno` (отступ, NATR) — первый тейк у следующего уровня за пробиваемым.
//! Определение — в докстроке `ExitCfg::tn_pct`; здесь: поиск следующего уровня без заглядывания (`next_px`), план тейков (`next_take_plan`), ход сделки по потоку,
//! откаты на обычную лестницу, выключенный ключ = прежний путь, разбор и отпечаток, сквозной путь «фаза 1 → тейк сделки» (лонг и шорт).

use super::*;
use crate::breakout::levels::next_px;
use crate::breakout::params::{parse_variant, ExitCfg};
use crate::breakout::sigcache::cfg_key_text;
use crate::breakout::tests::HB;
use crate::breakout::tick::next_take_plan;

fn lvl(born: u32, end: u32, segs: &[(u32, f64)]) -> Level {
    Level {
        born,
        end,
        segs: segs.iter().map(|&(m, p)| (m, px(p))).collect(),
        first_bar: 0,
        mem: Vec::new(),
        src: 0,
        why: 0,
    }
}

#[test]
fn next_px_is_the_nearest_alive_level_strictly_beyond_in_the_trade_direction() {
    let set = vec![
        lvl(0, u32::MAX, &[(0, 100.0)]), // сам пробиваемый
        lvl(0, u32::MAX, &[(0, 103.0)]),
        lvl(0, u32::MAX, &[(0, 101.0)]), // ближайший за ним
        lvl(0, u32::MAX, &[(0, 100.0)]), // тот же по цене — не «строго за»
        lvl(0, u32::MAX, &[(0, 99.0)]),  // ниже — не по ходу лонга
    ];
    assert_eq!(next_px(&set, 1, px(100.0), 500), px(101.0));
    // шорт: длинная рама — цены с минусом, «за пробиваемым» — вниз: ближайший 99,0
    assert_eq!(next_px(&set, -1, -px(100.0), 500), -px(99.0));
    // за последним уровнем и в пустом наборе — 0
    assert_eq!(next_px(&set, 1, px(103.0), 500), 0);
    assert_eq!(next_px(&set, -1, -px(99.0), 500), 0);
    assert_eq!(next_px(&[], 1, px(100.0), 500), 0);
}

#[test]
fn next_px_ignores_unborn_and_dead_levels_and_uses_the_price_version_of_the_minute() {
    let set = vec![
        lvl(0, u32::MAX, &[(0, 100.0)]),
        lvl(600, u32::MAX, &[(600, 101.0)]), // рождается в 600
        lvl(0, 700, &[(0, 102.0)]),          // с 700 мёртв
        lvl(0, u32::MAX, &[(0, 105.0), (800, 101.5)]), // версия цены меняется в 800
    ];
    assert_eq!(
        next_px(&set, 1, px(100.0), 599),
        px(102.0),
        "101 ещё не родился"
    );
    assert_eq!(
        next_px(&set, 1, px(100.0), 600),
        px(101.0),
        "born ≤ t включительно"
    );
    assert_eq!(next_px(&set, 1, px(101.0), 699), px(102.0));
    assert_eq!(
        next_px(&set, 1, px(101.0), 700),
        px(105.0),
        "end ≤ t — уровень мёртв"
    );
    assert_eq!(next_px(&set, 1, px(101.0), 799), px(105.0));
    assert_eq!(
        next_px(&set, 1, px(101.0), 800),
        px(101.5),
        "версия цены минуты 800"
    );
}

#[test]
fn tn_keys_parse_validate_default_off_and_keep_the_params_string() {
    let (_, p0) = parse_variant("x").expect("разбор");
    assert_eq!((p0.exit.tn_pct, p0.exit.tno_m), (0, 0));
    // выключено — `Debug` ExitCfg и всех Params как до ключа (meta.json байт в байт)
    assert_eq!(
        format!("{:?}", p0.exit),
        "ExitCfg { natr_p: 30, n_tp: 8, t_low_m: 1000, t_high_m: 5000, q_pct: 50, be: true, a_m: 2000, r_m: 2000, s_pm: 5000, slip_bps: 1, \
         best_order: false, lat_ms: 0, lat_exit_ms: 0, tp_gamma_m: 1000, tp_kappa_m: 0, natr_src: 0, ent_w_ms: 0, ent_x_bp: 0, ls_m: 0, \
         tx_min: 0, tx_x_pm: 0, rt_f_pm: 0, rt_s: 0, be_mode: 0, e2_pm: 0, sn_m: 0, sg: 0, sb_m: 0, sr_m: 0, sd_m: 0, tj_m: 0, tl: 0 }"
    );
    assert!(!format!("{p0:?}").contains("tn_pct") && !format!("{p0:?}").contains("tno_m"));
    assert_eq!(parse_variant("x tn=0 tno=0").expect("разбор").1, p0);
    // сетка перебора: доля 25 / 50 / 100 × отступ 0 / 0,1 / 0,25 NATR
    for tn in [25, 50, 100] {
        for (tno, m) in [("0", 0), ("0.1", 100), ("0.25", 250)] {
            let (_, p) = parse_variant(&format!("x tn={tn} tno={tno}")).expect("разбор");
            assert_eq!((p.exit.tn_pct, p.exit.tno_m), (tn, m), "tn={tn} tno={tno}");
            assert!(
                format!("{:?}", p.exit).ends_with(&format!("tl: 0, tn_pct: {tn}, tno_m: {m} }}"))
            );
            assert_eq!(p.sig, p0.sig, "ключи выхода сигнал не меняют");
        }
    }
    // любая доля 1…100; отступ по умолчанию 0
    for tn in [1, 33, 99] {
        let (_, p) = parse_variant(&format!("x tn={tn}")).expect("разбор");
        assert_eq!((p.exit.tn_pct, p.exit.tno_m), (tn, 0));
    }
    // отступ без доли — опечатка, не «выкл.»; доля вне 1…100; отрицательный отступ
    for bad in ["tno=0.1", "tn=-1", "tn=101", "tn=50 tno=-0.1"] {
        assert!(parse_variant(&format!("x {bad}")).is_err(), "{bad}");
    }
    // минутный движок отвергает
    let (_, p) = parse_variant("x tn=50").expect("разбор");
    assert!(crate::breakout::cli::reject_tick_only(&p, "x").is_err());
    assert!(crate::breakout::cli::reject_tick_only(&p0, "x").is_ok());
}

#[test]
fn tn_keys_are_exit_only_so_the_signal_job_and_the_cache_key_text_do_not_change() {
    let (_, a) = parse_variant("x").expect("разбор");
    let (_, b) = parse_variant("x tn=50 tno=0.1").expect("разбор");
    assert_eq!(
        a.sig, b.sig,
        "одно задание сигналов у вариантов с ключом и без"
    );
    assert_eq!(cfg_key_text(&a.sig), cfg_key_text(&b.sig));
    assert_ne!(a.exit, b.exit);
}

/// Лестница на 4 ступени при NATR 0,5 и входе 100,0: 100,5 / 101,0 / 101,5 / 102,0 (1 / 2 / 3 / 4 NATR); q = 50 %.
fn tn_ex4(tn: i64) -> (ExitCfg, i64, Vec<i64>) {
    let mut ex = ex_test();
    ex.n_tp = 4;
    ex.t_high_m = 4000;
    ex.tn_pct = tn;
    let ladder = (0..4).map(|i| px(100.5 + 0.5 * f64::from(i))).collect();
    (ex, px(100.0), ladder)
}

#[test]
fn next_take_plan_replaces_step_one_drops_steps_below_the_take_and_renormalises_the_rest() {
    let (mut ex, e, ladder) = tn_ex4(50);
    // следующий уровень 101,2: ступень 1 (100,5) заменена, ступень 2 (101,0) ниже тейка — снята
    let pl = next_take_plan(&ex, px(101.2), e, 0, &ladder, NOTIONAL_U).expect("план");
    assert_eq!(pl.tps, vec![px(101.2), px(101.5), px(102.0)]);
    assert_eq!(pl.steps, vec![0, 2, 3]);
    assert_eq!(
        pl.lots,
        vec![NOTIONAL_U / 2, NOTIONAL_U / 8, NOTIONAL_U / 8]
    );
    // 25 %: остаток 3/4 позиции, в тейки q = 50 % остатка — 3/8, поровну на две ступени
    ex.tn_pct = 25;
    let pl = next_take_plan(&ex, px(101.2), e, 0, &ladder, NOTIONAL_U).expect("план");
    assert_eq!(
        pl.lots,
        vec![NOTIONAL_U / 4, NOTIONAL_U * 3 / 16, NOTIONAL_U * 3 / 16]
    );
    // 100 %: единственный тейк на всю позицию
    ex.tn_pct = 100;
    let pl = next_take_plan(&ex, px(101.2), e, 0, &ladder, NOTIONAL_U).expect("план");
    assert_eq!(
        (pl.tps, pl.lots, pl.steps),
        (vec![px(101.2)], vec![NOTIONAL_U], vec![0])
    );
    // отступ 0,05 внутрь (0,1 NATR): тейк 101,15; ступени 101,5 / 102,0 те же
    ex.tn_pct = 50;
    let pl = next_take_plan(&ex, px(101.2), e, px(0.05), &ladder, NOTIONAL_U).expect("план");
    assert_eq!(pl.tps, vec![px(101.15), px(101.5), px(102.0)]);
    // лот размером в полставки (второй лот e2): те же доли от него
    let pl = next_take_plan(&ex, px(101.2), e, 0, &ladder, NOTIONAL_U / 2).expect("план");
    assert_eq!(
        pl.lots,
        vec![NOTIONAL_U / 4, NOTIONAL_U / 16, NOTIONAL_U / 16]
    );
}

#[test]
fn next_take_plan_keeps_every_step_above_a_near_take_and_gives_the_division_rest_to_the_last() {
    let (ex, e, ladder) = tn_ex4(50);
    // тейк 100,8 ниже ступени 2 (101,0): заменена одна ступень 1, остальные три остаются
    let pl = next_take_plan(&ex, px(100.8), e, 0, &ladder, NOTIONAL_U).expect("план");
    assert_eq!(pl.tps, vec![px(100.8), px(101.0), px(101.5), px(102.0)]);
    assert_eq!(pl.steps, vec![0, 1, 2, 3]);
    // в тейки q = 50 % остатка = 125 000 000 на три ступени: 41 666 666 ×2 и остаток 41 666 668 — последней
    assert_eq!(
        pl.lots,
        vec![250_000_000, 41_666_666, 41_666_666, 41_666_668]
    );
    // перекос объёма κ = 1: веса 1000·(n−1) + κ·i — по исходным номерам ступеней 2 и 3 (5000 и 6000)
    let (mut ex, e, ladder) = tn_ex4(50);
    ex.tp_kappa_m = 1000;
    let pl = next_take_plan(&ex, px(101.2), e, 0, &ladder, NOTIONAL_U).expect("план");
    let l2 = 125_000_000 * 5000 / 11_000;
    assert_eq!(pl.lots, vec![250_000_000, l2, 125_000_000 - l2]);
    // q = 100 %: лестница закрывает остаток целиком — сумма лотов ровно вся позиция
    let (mut ex, e, ladder) = tn_ex4(25);
    ex.q_pct = 100;
    let pl = next_take_plan(&ex, px(100.8), e, 0, &ladder, NOTIONAL_U).expect("план");
    assert_eq!(pl.lots[0], NOTIONAL_U / 4);
    assert_eq!(pl.lots.iter().sum::<i64>(), NOTIONAL_U);
    // q = 0 («лестница выключена»): ступени безлотные, остаток ждёт обычного выхода
    ex.q_pct = 0;
    let pl = next_take_plan(&ex, px(100.8), e, 0, &ladder, NOTIONAL_U).expect("план");
    assert_eq!(pl.lots, vec![NOTIONAL_U / 4, 0, 0, 0]);
    // тейк ровно на внешней ступени (расстояние не больше потолка): ступеней выше нет — остаток ждёт обычного выхода
    let (ex, e, ladder) = tn_ex4(50);
    let pl = next_take_plan(&ex, px(102.0), e, 0, &ladder, NOTIONAL_U).expect("план");
    assert_eq!((pl.tps, pl.lots), (vec![px(102.0)], vec![NOTIONAL_U / 2]));
}

#[test]
fn next_take_plan_falls_back_to_the_ladder_when_there_is_no_usable_level() {
    let (ex, e, ladder) = tn_ex4(50);
    let plan = |nxt: i64, off: i64| next_take_plan(&ex, nxt, e, off, &ladder, NOTIONAL_U);
    assert!(plan(0, 0).is_none(), "следующего уровня нет");
    assert!(
        plan(e, 0).is_none(),
        "тейк на цене входа — нулевое расстояние"
    );
    assert!(
        plan(px(99.9), 0).is_none(),
        "уровень не по ту сторону от входа"
    );
    assert!(
        plan(px(100.04), px(0.05)).is_none(),
        "после отступа тейк ≤ цены входа"
    );
    assert!(
        plan(px(100.06), px(0.05)).is_some(),
        "после отступа тейк 100,01 — выше входа"
    );
    assert!(
        plan(px(102.0), 0).is_some(),
        "ровно на внешней ступени — допустимо"
    );
    assert!(
        plan(px(102.0) + 1, 0).is_none(),
        "дальше внешней ступени (t_high) — обычная лестница"
    );
    assert!(
        plan(px(102.3), px(0.1)).is_none(),
        "тейк 102,2 дальше потолка 102,0"
    );
    assert!(
        plan(px(102.05), px(0.1)).is_some(),
        "тейк 101,95 внутри потолка"
    );
    let mut off = ex.clone();
    off.tn_pct = 0;
    assert!(
        next_take_plan(&off, px(101.2), e, 0, &ladder, NOTIONAL_U).is_none(),
        "tn = 0 — выкл."
    );
}

/// Лот входит по 100,0 в 1000 мс (NATR 0,5: 1 NATR = 0,5); `nxt` — следующий уровень (настоящая цена в раскладке лонга; 0 — нет), `ticks` — сделки в раскладке лонга;
/// для шорта все цены зеркалятся (200 − цена), рама лонга / шорта — как в движке.
fn run_tn(ex: &ExitCfg, side: Side, nxt: f64, ticks: &[(i64, f64, bool)]) -> TickOut {
    let s = side.s();
    let m = |p: f64| if s > 0 { p } else { 200.0 - p };
    let lc = LevelCtx {
        lp: s * px(m(100.2)),
        far: s * px(m(100.0)),
        rng: None,
        vol: -1,
        volz: -1,
        nxt: if nxt > 0.0 { s * px(m(nxt)) } else { 0 },
    };
    let t: Vec<(i64, f64, bool)> = ticks.iter().map(|&(ms, p, bm)| (ms, m(p), bm)).collect();
    sim_fill_lc(
        &mut stream(t),
        1000,
        s * px(100.0),
        side,
        NATR,
        ex,
        Some(lc),
        NOTIONAL_U,
    )
}

/// Цена в раскладке лонга `x` → настоящая цена стороны (×1e8).
fn px_m(side: Side, x: f64) -> i64 {
    px(if side == Side::Long { x } else { 200.0 - x })
}

fn legs_of(o: &TickOut) -> Vec<(i64, i64)> {
    o.legs.iter().map(|l| (l.px, l.u)).collect()
}

const TN_TICKS: [(i64, f64, bool); 4] = [
    (1100, 100.6, false), // ниже тейка у уровня при любом отступе (100,8 / 100,75 / 100,675); у обычной лестницы — ступень 100,5
    (1200, 100.9, false), // выше тейка у уровня; ниже ступени 101,0
    (1300, 101.1, false), // ступень 101,0
    (1400, 99.9, true),   // безубыток после первого тейка → выход
];

#[test]
fn tn_closes_its_share_at_next_level_minus_offset_long_and_short() {
    for side in [Side::Long, Side::Short] {
        for (tn, first, second) in [
            (25, NOTIONAL_U / 4, 187_500_000),
            (50, NOTIONAL_U / 2, NOTIONAL_U / 4),
        ] {
            for (tno_m, off) in [(0, 0), (100, 5_000_000), (250, 12_500_000)] {
                let mut ex = ex_test();
                ex.tn_pct = tn;
                ex.tno_m = tno_m;
                let out = run_tn(&ex, side, 100.8, &TN_TICKS);
                // тейк у уровня: у лонга ниже уровня на отступ, у шорта — выше (зеркально)
                let tp = if side == Side::Long {
                    px(100.8) - off
                } else {
                    px(99.2) + off
                };
                let tag = format!("{side:?} tn={tn} tno_m={tno_m}");
                assert_eq!(
                    legs_of(&out),
                    vec![
                        (tp, first),
                        (px_m(side, 101.0), second),
                        (px_m(side, 99.9), NOTIONAL_U - first - second)
                    ],
                    "{tag}"
                );
                assert_eq!((out.takes, out.kind), (2, ExitKind::BreakEven), "{tag}");
                assert!(
                    out.legs[0].maker && out.legs[1].maker && !out.legs[2].maker,
                    "{tag}"
                );
                assert_eq!(out.legs[0].ms, 1200, "{tag}");
            }
        }
        // 100 %: вся позиция закрыта тейком у уровня, выход «тейки» на его сделке
        for (tno_m, off) in [(0, 0), (100, 5_000_000), (250, 12_500_000)] {
            let mut ex = ex_test();
            ex.tn_pct = 100;
            ex.tno_m = tno_m;
            let out = run_tn(&ex, side, 100.8, &TN_TICKS);
            let tp = if side == Side::Long {
                px(100.8) - off
            } else {
                px(99.2) + off
            };
            assert_eq!(
                legs_of(&out),
                vec![(tp, NOTIONAL_U)],
                "{side:?} tno_m={tno_m}"
            );
            assert_eq!(
                (out.takes, out.kind, out.exit_ms),
                (1, ExitKind::Takes, 1200)
            );
        }
    }
}

#[test]
fn tn_take_between_ladder_steps_fills_in_price_order_and_skips_the_steps_below_it() {
    for side in [Side::Long, Side::Short] {
        let mut ex = ex_test();
        ex.n_tp = 4;
        ex.t_high_m = 4000; // ступени 100,5 / 101,0 / 101,5 / 102,0
        ex.tn_pct = 50;
        let ticks = [
            (1100, 101.3, false), // тейк у уровня 101,2; ступень 101,5 ещё нет
            (1200, 101.6, false), // 101,5
            (1300, 102.1, false), // 102,0
            (1400, 99.9, true),
        ];
        let out = run_tn(&ex, side, 101.2, &ticks);
        assert_eq!(
            legs_of(&out),
            vec![
                (px_m(side, 101.2), NOTIONAL_U / 2),
                (px_m(side, 101.5), NOTIONAL_U / 8),
                (px_m(side, 102.0), NOTIONAL_U / 8),
                (px_m(side, 99.9), NOTIONAL_U / 4),
            ],
            "{side:?}: ступень 101,0 снята, остаток — обычный выход"
        );
        assert_eq!(out.takes, 3);
    }
}

#[test]
fn tn_falls_back_to_the_ordinary_ladder_and_off_is_the_old_path() {
    for side in [Side::Long, Side::Short] {
        let base = ex_test(); // tn = 0
        let want = format!("{:?}", run_tn(&base, side, 0.0, &TN_TICKS));
        // обычная лестница: 100,5 и 101,0 по 1/4, остаток — безубыток
        assert_eq!(
            legs_of(&run_tn(&base, side, 0.0, &TN_TICKS)),
            vec![
                (px_m(side, 100.5), NOTIONAL_U / 4),
                (px_m(side, 101.0), NOTIONAL_U / 4),
                (px_m(side, 99.9), NOTIONAL_U / 2)
            ]
        );
        // tn = 0: следующий уровень в сигнале ничего не меняет
        for nxt in [100.8, 100.3, 101.0, 101.5] {
            let got = format!("{:?}", run_tn(&base, side, nxt, &TN_TICKS));
            assert_eq!(got, want, "{side:?} tn=0 nxt={nxt}");
        }
        // tn > 0, но тейк не годится — исход тот же, что у обычной лестницы
        let mut ex = ex_test();
        ex.tn_pct = 50;
        for (nxt, tno_m) in [
            (0.0, 0),      // следующего уровня нет
            (100.0, 0),    // тейк на цене входа
            (99.5, 0),     // не по ту сторону
            (100.04, 100), // после отступа 0,05 тейк 99,99 ≤ входа
            (101.3, 0),    // дальше внешней ступени 101,0
            (101.3, 100),  // 101,25 — всё ещё дальше
        ] {
            ex.tno_m = tno_m;
            let got = format!("{:?}", run_tn(&ex, side, nxt, &TN_TICKS));
            assert_eq!(got, want, "{side:?} nxt={nxt} tno_m={tno_m}");
        }
        // тейк ровно на внешней ступени 101,0 допустим: лот 50 % по 101,0, остаток — выход
        ex.tno_m = 0;
        let out = run_tn(&ex, side, 101.0, &TN_TICKS);
        assert_eq!(
            legs_of(&out),
            vec![
                (px_m(side, 101.0), NOTIONAL_U / 2),
                (px_m(side, 99.9), NOTIONAL_U / 2)
            ],
            "{side:?}"
        );
    }
}

#[test]
fn tn_with_percent_ladder_caps_at_the_percent_outer_step() {
    for side in [Side::Long, Side::Short] {
        let mut base = ex_test();
        base.tpp = true;
        base.t_low_m = 500; // 0,5 % и 1 % цены входа: тейки 100,5 / 101,0
        base.t_high_m = 1000;
        let want = format!("{:?}", run_tn(&base, side, 0.0, &TN_TICKS));
        let mut ex = base.clone();
        ex.tn_pct = 50;
        let out = run_tn(&ex, side, 100.8, &TN_TICKS);
        assert_eq!(
            legs_of(&out),
            vec![
                (px_m(side, 100.8), NOTIONAL_U / 2),
                (px_m(side, 101.0), NOTIONAL_U / 4),
                (px_m(side, 99.9), NOTIONAL_U / 4)
            ],
            "{side:?}"
        );
        // уровень 101,2 дальше внешней ступени в процентах (101,0) — обычная лестница
        let got = format!("{:?}", run_tn(&ex, side, 101.2, &TN_TICKS));
        assert_eq!(got, want, "{side:?}");
    }
}

#[test]
fn tn_keeps_the_step_ladder_recalculation_aligned_with_the_taken_steps() {
    // tj = 0,5: после каждого исполненного тейка оставшиеся ступени сдвигаются на δ·j NATR; тейк у уровня — первый исполненный (j = 1)
    for side in [Side::Long, Side::Short] {
        let mut ex = ex_test();
        ex.n_tp = 4;
        ex.t_high_m = 4000; // ступени 1 / 2 / 3 / 4 NATR
        ex.tn_pct = 50;
        ex.tj_m = 500;
        let ticks = [
            (1100, 101.3, false), // тейк у уровня 101,2 (j = 1); ступени 101,5 / 102,0 → 3·1,5 = 4,5 NATR = 102,25 и 4·1,5 = 6 NATR = 103,0
            (1200, 102.1, false), // выше прежних 101,5 / 102,0, но ниже сдвинутой 102,25 — ничего не исполняется
            (1300, 102.3, false), // сдвинутая ступень 102,25
            (1400, 99.9, true),
        ];
        let out = run_tn(&ex, side, 101.2, &ticks);
        assert_eq!(
            legs_of(&out),
            vec![
                (px_m(side, 101.2), NOTIONAL_U / 2),
                (px_m(side, 102.25), NOTIONAL_U / 8),
                (px_m(side, 99.9), NOTIONAL_U * 3 / 8),
            ],
            "{side:?}"
        );
    }
}

// ───────────────────── фаза 1: следующий уровень в сигнале и сквозной путь до тейка ─────────────────────

/// Минута рождения уровня 2 в мире `tn_world`.
const TN_BORN2: usize = (HB + 14) * 60;

/// Мир TK-697: уровни стороны — 100,025 (рождён в `BORN`) и 102,025 (пивоты 102,00 и 102,05 на барах HB+9 и HB+12; закрытия баров 98,0 — ниже первого уровня,
/// поэтому он жив), второй рождён с минуты `TN_BORN2`. `sig` — минуты сигнала (зелёная минутка в зоне входа под первым уровнем), `vol` — минуты с размахом 1,0
/// ниже уровня (NATR выхода > 0); `flip` — зеркало для шорта.
fn tn_world(sig: &[usize], vol: &[usize], flip: bool) -> Cols {
    let mut c = level_world();
    let hs = [99.0, 102.0, 99.0, 98.0, 102.05, 99.0];
    for (i, h) in hs.iter().enumerate() {
        let b = (HB + 8 + i) * 60;
        for m in b..b + 60 {
            set(&mut c, m, 98.0, 98.0, 98.0, 98.0);
        }
        set(&mut c, b, 98.0, *h, 97.9, 98.0);
    }
    for &m in vol {
        set(&mut c, m, 99.0, 99.9, 98.9, 99.0);
    }
    for &m in sig {
        set(&mut c, m, 99.85, 99.95, 99.84, 99.90);
    }
    if flip {
        mirror(&c, 200.0)
    } else {
        c
    }
}

#[test]
fn signal_carries_the_next_level_known_at_the_signal_minute() {
    for (flip, side) in [(false, Side::Long), (true, Side::Short)] {
        let t_a = (HB + 8) * 60 + 30; // до рождения уровня 2
        let (t_lo, t_hi) = (TN_BORN2 - 1, TN_BORN2); // последняя минута до рождения и первая после
        let day = t_hi / 1440;
        assert!(t_a / 1440 == day && t_lo / 1440 == day);
        let mut p = lv_params();
        p.sig.ban = false; // пики баров 9 и 12 — прокол уровня 1 с возвратом: запрет ложного пробоя здесь не проверяется
        let c = tn_world(&[t_a, t_lo, t_hi], &[], flip);
        // набор стороны — два уровня с рождениями BORN и TN_BORN2
        let lv = build_levels(&sym(c.clone()), &p.sig.lv, side);
        let mut born: Vec<usize> = lv.iter().map(|l| l.born as usize).collect();
        born.sort_unstable();
        assert_eq!(born, vec![BORN, TN_BORN2], "{side:?}");
        let f = |m: usize, ms: u32, price: f64| {
            tk(m, ms, if flip { 200.0 - price } else { price }, false)
        };
        let mut ticks = Vec::new();
        for t in [t_a, t_lo, t_hi] {
            ticks.push(f(t, 0, 99.6));
            ticks.push(f(t, 10_000, 99.8)); // сигнал: в зоне, выше открытия минуты
        }
        let (mut sg, _) = ap_signals(c, &p, side, vec![(day, ticks)]);
        sg.sort_by_key(|s| s.t);
        assert_eq!(sig_minutes(&sg), vec![t_a, t_lo, t_hi], "{side:?}");
        let l2 = if flip {
            px(200.0) - px(102.025)
        } else {
            px(102.025)
        };
        assert_eq!(
            sg.iter().map(|s| s.nxt).collect::<Vec<_>>(),
            vec![0, 0, side.s() * l2],
            "{side:?}: уровень 2 виден только сигналу с минуты его рождения, раньше — нет следующего"
        );
    }
}

#[test]
fn next_level_flows_from_the_signal_into_the_take_of_the_simulated_trade() {
    for (flip, side) in [(false, Side::Long), (true, Side::Short)] {
        let t = TN_BORN2;
        let day = t / 1440;
        let vol: Vec<usize> = (t - 35..t).collect();
        let s = Sym::from_cols("TEST", tn_world(&[t], &vol, flip), Vec::new());
        let m = |p: f64| if flip { 200.0 - p } else { p };
        let ticks = vec![
            tk(t, 0, m(99.6), false),
            tk(t, 10_000, m(99.8), false), // сигнал
            tk(t, 20_000, m(99.9), flip), // вход: агрессор стороны (покупатель — лонг, продавец — шорт)
            tk(t, 30_000, m(102.2), flip), // за следующим уровнем 102,025 → тейк
        ];
        let mut mem = HashMap::new();
        mem.insert(("TEST".to_string(), day), Arc::new(ticks));
        let src = MemSrc(mem);
        let mut p = lv_params();
        p.sig.ban = false; // как в `signal_carries_the_next_level_known_at_the_signal_minute`
        p.exit.tn_pct = 100;
        let jobs = vec![(p.sig.clone(), side)];
        let mut outs = vec![Vec::new()];
        let mut ctrs = vec![TickCtr::default()];
        signals_for_coin(&s, 0, &jobs, &src, &mut outs, &mut ctrs);
        assert_eq!(sig_minutes(&outs[0]), vec![t], "{side:?}");
        let syms = [s];
        // ключ выключен — обычная лестница: первый тейк не у уровня 102,025
        let off = run_tick(&syms, &outs[0], side, &Params::base().exit, &p.port, &src);
        assert_eq!(off.trades.len(), 1, "{side:?}: отказы {:?}", off.rej);
        let l2 = if flip {
            px(200.0) - px(102.025)
        } else {
            px(102.025)
        };
        assert!(off.trades[0].legs.iter().all(|l| l.px != l2), "{side:?}");
        for tno_m in [0i64, 100, 250] {
            p.exit.tno_m = tno_m;
            let r = run_tick(&syms, &outs[0], side, &p.exit, &p.port, &src);
            assert_eq!(r.trades.len(), 1, "{side:?}: отказы {:?}", r.rej);
            let tr = &r.trades[0];
            // отступ — доля NATR от цены входа лота (формула ступеней лестницы)
            let off_px =
                (i128::from(tr.entry_px.abs()) * i128::from(tr.natr_e8) * i128::from(tno_m)
                    / (i128::from(SCALE) * 1000)) as i64;
            let want = if flip { l2 + off_px } else { l2 - off_px };
            assert_eq!(
                (tr.kind, tr.takes, tr.legs.len()),
                (ExitKind::Takes, 1, 1),
                "{side:?} tno_m={tno_m}"
            );
            assert_eq!(
                (tr.legs[0].px, tr.legs[0].u, tr.legs[0].maker),
                (want, NOTIONAL_U, true),
                "{side:?} tno_m={tno_m}"
            );
        }
    }
}
