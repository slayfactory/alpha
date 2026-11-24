//! TK-676 (§13.74): стоп от волатильности — ряды `vol_for` (NATR 5, реализованная 5/15/30/60), формулы `stop_pm`, ключи, выход по расстоянию на входе.

use super::data::{natr_series, Cols, Sym, N_MIN, SCALE, VOL_PERIODS};
use super::params::{parse_variant, ExitCfg, Params};
use super::tests::{flat, px, set, sym};
use super::tick::{sim_fill_lc, LevelCtx, TickOut};
use super::trade::{ExitKind, NOTIONAL_U};
use super::volstop::stop_pm;
use super::Side;

/// Детерминированное блуждание с дырами и нулевыми приращениями.
fn walk(seed: u64, price: i64) -> Cols {
    let mut x = seed;
    let mut next = move |n: u64| {
        x = x
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        (x >> 11) % n
    };
    let mut c = Cols::new();
    let mut p = price;
    for m in 0..N_MIN {
        if next(40) == 0 {
            continue; // дыра
        }
        let o = p;
        p = (p + (next(2001) as i64 - 1000) * (price / 100_000).max(1)).max(price / 2);
        c.o[m] = o;
        c.h[m] = o.max(p) + next(300) as i64;
        c.l[m] = (o.min(p) - next(300) as i64).max(1);
        c.c[m] = p;
    }
    c
}

/// Эталон реализованной волатильности: прямой цикл по окну, целые.
fn rv_ref(c: &Cols, p: usize, t: usize) -> i32 {
    if t < p || c.o[t] == 0 {
        return -1;
    }
    let (mut s, mut k) = (0u128, 0usize);
    for j in t + 1 - p..=t {
        if c.o[j] != 0 && c.o[j - 1] != 0 {
            let r = i128::from(c.c[j] - c.c[j - 1]) * i128::from(SCALE) / i128::from(c.c[j - 1]);
            s += (r * r) as u128;
            k += 1;
        }
    }
    if k * 2 < p {
        return -1;
    }
    s.isqrt().min(i32::MAX as u128) as i32
}

#[test]
fn vol_series_match_references_and_codes() {
    let c = walk(7, 300_000_000);
    let s = Sym::from_cols("T", c.clone(), Vec::new());
    for (i, &p) in VOL_PERIODS.iter().enumerate() {
        let rv = s.vol_for(5 + i as u8).expect("код 5–8");
        for t in (0..N_MIN).step_by(11) {
            assert_eq!(rv[t], rv_ref(&c, p, t), "rv {p} t {t}");
        }
        let nat = s.vol_for(1 + i as u8).expect("код 1–4");
        assert_eq!(nat, natr_series(&c, p).as_slice(), "natr {p}");
    }
    assert!(s.vol_for(0).is_none() && s.vol_for(9).is_none());
    assert!(
        s.vol_for(5).expect("rv5").iter().any(|&v| v > 0),
        "ряд не пуст"
    );
}

#[test]
fn realized_volatility_of_one_jump_is_exact_and_leaves_the_window() {
    let mut c = flat(3000, 100.0);
    let m = 2000;
    for t in m..3000 {
        set(&mut c, t, 101.0, 101.0, 101.0, 101.0); // одна минута +1 %, дальше цена стоит
    }
    let s = sym(c);
    let rv5 = s.vol_for(5).expect("rv5");
    assert_eq!(rv5[m - 1], 0);
    assert_eq!(rv5[m], 1_000_000, "1 % = 1e6 (доля ×1e8)");
    assert_eq!(rv5[m + 4], 1_000_000, "скачок ещё в окне 5 минут");
    assert_eq!(rv5[m + 5], 0, "и вышел из окна");
    let rv60 = s.vol_for(8).expect("rv60");
    assert_eq!(rv60[m + 59], 1_000_000);
    assert_eq!(rv60[m + 60], 0);
}

fn vex(line: &str) -> ExitCfg {
    parse_variant(&format!("x {line}")).expect("разбор").1.exit
}

#[test]
fn stop_pm_forms_clamp_and_regime() {
    let v = 500_000; // 0,5 % цены
                     // линейная: 3·0,5 % = 1,5 %
    assert_eq!(stop_pm(&vex("vf=1 vs=3 va=3"), v, -1), Some(1500));
    // зажим: низ vm = 0,5 % и верх s = 5 %
    assert_eq!(stop_pm(&vex("vf=1 vs=3 va=3"), 0, -1), Some(500));
    assert_eq!(stop_pm(&vex("vf=1 vs=3 va=3"), 50_000_000, -1), Some(5000));
    assert_eq!(stop_pm(&vex("vf=1 vs=3 va=3 s=3 vm=1"), v, -1), Some(1500));
    assert_eq!(
        stop_pm(&vex("vf=1 vs=3 va=3 s=1 vm=2"), v, -1),
        Some(1000),
        "верх главнее низа"
    );
    // аффинная: 0,5 + 2·0,5 = 1,5 %
    assert_eq!(stop_pm(&vex("vf=2 vs=3 va=2 vb=0.5"), v, -1), Some(1500));
    // степень: 3·0,25^0,5 = 1,5 %
    assert_eq!(
        stop_pm(&vex("vf=3 vs=3 va=3 vp=0.5"), 250_000, -1),
        Some(1500)
    );
    // логарифм: 2·ln(1 + 0,5/0,5) = 1,3863 %
    assert_eq!(stop_pm(&vex("vf=4 vs=3 va=2 v0=0.5"), v, -1), Some(1386));
    // режим: V = 0,5 %, V_z = 1 % → V·(V_z/V) = 1 % → 3·1 = 3 %
    assert_eq!(
        stop_pm(&vex("vf=1 vs=3 va=3 vz=1 vr=1"), v, 1_000_000),
        Some(3000)
    );
    assert_eq!(
        stop_pm(&vex("vf=1 vs=3 va=3 vz=1 vr=0.5"), v, 2_000_000),
        Some(3000),
        "V·√4 = 1 %"
    );
    // нет значения источника / режима / формы — стопа нет
    assert_eq!(stop_pm(&vex("vf=1 vs=3 va=3"), -1, -1), None);
    assert_eq!(stop_pm(&vex("vf=1 vs=3 va=3 vz=1 vr=1"), v, -1), None);
    assert_eq!(stop_pm(&Params::base().exit, v, v), None);
}

#[test]
fn vol_keys_parse_validate_and_default_params_string_unchanged() {
    let e = vex("vf=3 vs=5 va=2.5 vp=0.75 vm=1 vz=8 vr=0.5 s=3");
    assert_eq!(
        (e.vf, e.vs, e.va_m, e.vp_m, e.vm_pm, e.vz, e.vr_m, e.s_pm),
        (3, 5, 2500, 750, 1000, 8, 500, 3000)
    );
    let base = Params::base();
    let s0 = format!("{base:?}");
    assert!(
        !s0.contains("vf") && !s0.contains("vm_pm"),
        "ключ выключен — строка params прежняя"
    );
    let (_, p) = parse_variant("x vf=1 vs=3 va=3").expect("разбор");
    assert!(format!("{p:?}").contains("vf: 1"));
    assert_eq!(vex("s=5"), base.exit, "умолчания не трогаются");
    for bad in [
        "va=3",                     // без vf
        "vf=1 va=3",                // нет vs
        "vf=1 vs=9 va=3",           // vs вне 1…8
        "vf=1 vs=3",                // нет va
        "vf=5 vs=3 va=3",           // форма вне 1…4
        "vf=2 vs=3 va=2",           // аффинная без b
        "vf=3 vs=3 va=2",           // степень без p
        "vf=4 vs=3 va=2",           // логарифм без v0
        "vf=1 vs=3 va=3 vb=0.5",    // b не из своей формы
        "vf=1 vs=3 va=3 vz=2",      // vz без vr
        "vf=1 vs=3 va=3 vr=1",      // vr без vz
        "vf=1 vs=3 va=3 vz=3 vr=1", // vz = vs
        "vf=1 vs=3 va=3 sn=2",      // с NATR-стопом sn нельзя
        "vm=1",                     // зажим без формы
    ] {
        assert!(parse_variant(&format!("x {bad}")).is_err(), "{bad}");
    }
}

fn ex_vol(line: &str) -> ExitCfg {
    let mut e = vex(line);
    e.slip_bps = 0;
    e.n_tp = 2;
    e.t_low_m = 1000;
    e.t_high_m = 2000;
    e.q_pct = 50;
    e.be = true;
    e.a_m = 1_000_000; // трейл не взводится
    e
}

/// Выход лота по потоку: вход в 1000 мс по цене 100,0, источник волатильности `vol_e8` (доля цены ×1e8; −1 — нет значения).
fn run(ticks: Vec<(i64, f64, bool)>, ex: &ExitCfg, vol_e8: i64) -> TickOut {
    let lc = LevelCtx {
        lp: px(100.0),
        far: px(100.0),
        rng: None,
        vol: vol_e8,
        volz: -1,
        nxt: 0,
    };
    let mut it = ticks.into_iter();
    let mut next = move || it.next().map(|(ms, p, bm)| (ms, px(p), bm));
    sim_fill_lc(
        &mut next,
        1000,
        px(100.0),
        Side::Long,
        500_000,
        ex,
        Some(lc),
        NOTIONAL_U,
    )
}

#[test]
fn volatility_stop_exits_at_the_frozen_distance_and_falls_back_to_s_without_a_value() {
    // V = 0,5 %, 2·V = 1 %: стоп 99,0; падение до 98,9 — выход по стопу
    let ex = ex_vol("vf=1 vs=3 va=2");
    let ticks = vec![(1100, 99.5, true), (1200, 98.9, true), (1300, 98.0, true)];
    let out = run(ticks.clone(), &ex, 500_000);
    assert_eq!(out.kind, ExitKind::HardStop);
    assert_eq!(out.exit_ms, 1200, "сработал на первой сделке за 99,0");
    // тот же поток без значения источника: стоп только `s = 5 %` — позиция держится до конца потока
    let out = run(ticks.clone(), &ex, -1);
    assert_ne!(out.kind, ExitKind::HardStop);
    // шире зажима `s`: 20·V = 10 % → зажат в 5 % — стоп 95,0, на 98,0 не срабатывает
    let wide = ex_vol("vf=1 vs=3 va=20");
    assert_ne!(run(ticks, &wide, 500_000).kind, ExitKind::HardStop);
}

#[test]
fn volatility_stop_does_not_change_the_default_path() {
    // vf = 0: прежний выход — стоп 5 % от входа, источник волатильности не читается
    let ex = ex_vol("lat=4");
    let out = run(vec![(1100, 99.5, true), (1200, 94.0, true)], &ex, 500_000);
    assert_eq!(out.kind, ExitKind::HardStop);
    assert_eq!(out.exit_ms, 1200);
    let out = run(vec![(1100, 99.5, true), (1200, 96.0, true)], &ex, 500_000);
    assert_ne!(out.kind, ExitKind::HardStop, "96,0 выше 95,0: стопа нет");
}
