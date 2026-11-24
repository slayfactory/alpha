//! Разбор имён форм: стоп, тейк, дедлайн, ранний выход.

use super::*;

/// Имена форм разбираются и печатаются каноническими; чужие и неканонические — отказ.
#[test]
fn form_names_round_trip_and_reject_strangers() {
    for name in [
        "before", "at", "behind", "midfr", "stack2", "pct0.5", "pct2", "s1", "s1.5",
    ] {
        assert_eq!(StopForm::parse(name).unwrap().label(), name);
    }
    for name in ["1to1", "t1", "t0.5", "tr0.5x0.3", "tr1x0.5"] {
        assert_eq!(TakeForm::parse(name).unwrap().label(), name);
    }
    for bad in ["s1.0", "pct01", "pct0", "pct100", "s-1", "x", "sigma1"] {
        assert!(StopForm::parse(bad).is_err(), "{bad}");
    }
    for bad in ["t1.0", "1:1", "take", "tr0.5", "tr0x0.3", "tr0.5x-1"] {
        assert!(TakeForm::parse(bad).is_err(), "{bad}");
    }
    // Трейл кладёт активацию и откат в план в bps (0.5 % → 50 bps, 0.3 % → 30).
    let touch = bounce_touch(1_000, Some(1_005));
    let (_, plan) =
        bounce_plan(&touch, 0.01, base("at", "tr0.5x0.3"), None, plain_shape()).unwrap();
    let TradePlan::Bounce {
        trail_bps,
        trail_activate_bps,
        ..
    } = plan
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert!((trail_activate_bps - 50.0).abs() < 1e-9 && (trail_bps - 30.0).abs() < 1e-9);
    assert!(
        BounceForm::parse("s1", "t1", None).is_err(),
        "σ-тейк без пола — отказ"
    );
    assert!(BounceForm::parse("before", "1to1", None).is_ok());
}

/// Числа владельца проверяются: отрицательный множитель и нулевой пол — отказ.
#[test]
fn sigma_geometry_rejects_negative_multipliers_and_a_zero_floor() {
    assert!(geometry(1.0, 1.0, 1.0).validate().is_ok());
    assert!(
        geometry(0.0, 0.0, 0.5).validate().is_ok(),
        "нулевые множители — полы работают"
    );
    assert!(geometry(-1.0, 1.0, 1.0).validate().is_err());
    assert!(geometry(1.0, f64::NAN, 1.0).validate().is_err());
    assert!(geometry(1.0, 1.0, 0.0).validate().is_err());
}

/// B3/B4: сетки В-58 зафиксированы **до** данных — значение дедлайна или
/// досрочного выхода вне них это отказ, а не параметр. Проверяется функцией,
/// а не живым прогоном: на боевом корне та же ошибка стоит минут счёта до
/// диагностики (находка R5 аудита 2026-09-17 — отказа не покрывал тест).
#[test]
fn deadline_and_early_exit_values_outside_the_preregistered_grid_are_refused() {
    // Дедлайн: сетка {60, 600, 3600, 7200} с.
    assert_eq!(deadline_ns_from_secs(60).unwrap(), 60 * 1_000_000_000);
    assert_eq!(deadline_ns_from_secs(7_200).unwrap(), 7_200 * 1_000_000_000);
    assert_eq!(
        deadline_ns_from_secs(21_600).unwrap(),
        21_600 * 1_000_000_000
    );
    assert_eq!(
        deadline_ns_from_secs(28_800).unwrap(),
        28_800 * 1_000_000_000
    );
    // F2 П-18 оси 4 (TK-289): точки между прежними допустимы, прежние значения не тронуты.
    for secs in [4_281, 5_091, 6_054, 8_562, 10_182, 12_109] {
        assert_eq!(deadline_ns_from_secs(secs).unwrap(), secs * 1_000_000_000);
    }
    let err = deadline_ns_from_secs(120).unwrap_err().to_string();
    assert!(
        err.contains("не из предрегистрированной сетки В-58") && err.contains("120"),
        "отказ обязан называть значение и сетку: {err}"
    );
    // Досрочный выход: выключен либо {1, 2, 3} с — четвёртый вариант той же оси.
    assert_eq!(
        early_exit_ns_from_ms(None).unwrap(),
        0,
        "без флага досрочный выход выключен, а не «ноль секунд»"
    );
    for x in EARLY_EXITS_S {
        assert_eq!(
            early_exit_ns_from_ms(Some(x * 1_000)).unwrap(),
            x * 1_000_000_000
        );
    }
    assert_eq!(
        early_exit_ns_from_ms(Some(EARLY_EXIT_HALF_MS)).unwrap(),
        500_000_000
    );
    for x in EARLY_EXITS_HALF_S {
        assert_eq!(
            early_exit_ns_from_ms(Some(x * 1_000 + 500)).unwrap(),
            (x * 1_000 + 500) * 1_000_000
        );
    }
    for bad in [0, 4_000, 7_000, 250, 1_500, 38_500] {
        let err = early_exit_ns_from_ms(Some(bad)).unwrap_err().to_string();
        assert!(
            err.contains("не из предрегистрированного набора В-58"),
            "{bad}: {err}"
        );
    }
}

/// B4: `--early-exit-secs` доезжает до плана вместе с ценой уровня и шагом
/// цены — без них стратегия не может решить «уровень ещё держит»: тика она не
/// знает, а цена уровня не выводится из входа (вход бывает от фронтранера).
#[test]
fn bounce_plan_carries_the_early_exit_the_level_and_the_tick() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005));
    let shape = PlanShape {
        early_exit_ns: 2 * 1_000_000_000,
        ..plain_shape()
    };
    let (_, plan) = bounce_plan(&touch, tick, geometry(1.0, 1.0, 1.0), Some(10.0), shape).unwrap();
    let TradePlan::Bounce {
        early_exit_ns,
        level_px,
        tick_px,
        ..
    } = plan
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(early_exit_ns, 2_000_000_000, "X из флага — в план");
    assert!(
        (level_px - 10.0).abs() < 1e-9,
        "уровень касания P = 10.00, а не цена входа: {level_px}"
    );
    assert!((tick_px - 0.01).abs() < 1e-12, "шаг цены: {tick_px}");
}

/// E7: формы `half1to1` и `eat<h>x<a>` — имя ↔ форма, границы порогов, и как
/// они ложатся в план (доля тейка, пороги съедания, размер плотности в
/// единицах крейта = лоты × шаг лота).
#[test]
fn e7_take_forms_parse_and_fill_the_plan() {
    assert_eq!(TakeForm::parse("half1to1").unwrap(), TakeForm::HalfOneToOne);
    assert_eq!(TakeForm::HalfOneToOne.label(), "half1to1");
    let e = TakeForm::parse("eat50x80").unwrap();
    assert_eq!(
        e,
        TakeForm::Eaten {
            half_pct: 50.0,
            all_pct: 80.0
        }
    );
    assert_eq!(e.label(), "eat50x80");
    assert!(
        TakeForm::parse("eat80x50").is_err(),
        "половина обязана быть меньше «всё»"
    );
    assert!(TakeForm::parse("eat50x120").is_err(), "не больше 100 %");
    assert!(TakeForm::parse("eat50").is_err());

    let touch = bounce_touch(1_000, Some(1_005));
    let shape = PlanShape {
        lot: 0.1,
        ..plain_shape()
    };
    let (_, plan) = bounce_plan(&touch, 0.01, base("pct1", "eat50x80"), None, shape).unwrap();
    let TradePlan::Bounce {
        take_frac,
        eaten_half_pct,
        eaten_all_pct,
        eaten_half_frac,
        level_qty,
        lot_qty,
        ..
    } = plan
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert!((take_frac - 1.0).abs() < 1e-12);
    assert!((eaten_half_pct - 50.0).abs() < 1e-12 && (eaten_all_pct - 80.0).abs() < 1e-12);
    assert!((eaten_half_frac - 0.5).abs() < 1e-12);
    // `size_at_touch = 10` лотов × шаг 0.1 = 1.0 в единицах крейта.
    assert!((level_qty - 1.0).abs() < 1e-12, "{level_qty}");
    assert!((lot_qty - 0.1).abs() < 1e-12);

    let (_, plan) = bounce_plan(&touch, 0.01, base("pct1", "half1to1"), None, shape).unwrap();
    let TradePlan::Bounce {
        take_frac,
        eaten_half_pct,
        take_px,
        entry_px,
        stop_px,
        ..
    } = plan
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert!((take_frac - 0.5).abs() < 1e-12 && eaten_half_pct == 0.0);
    // Тейк — те же 1:1 от входа, что у `1to1`.
    assert!((take_px - (entry_px + (entry_px - stop_px))).abs() < 1e-9);
}

/// S8: тейк в процентах от входа `tk<x>` — имя ↔ форма, границы (0, 100),
/// тейк ложится в план на `ceil(x % от входа)` тиков в сторону отскока
/// независимо от стопа (у `pct2-tk0.5` тейк в 4 раза ближе стопа).
#[test]
fn pct_take_form_parses_and_sets_the_take_independent_of_the_stop() {
    assert_eq!(TakeForm::parse("tk0.5").unwrap(), TakeForm::Pct(0.5));
    assert_eq!(TakeForm::Pct(1.0).label(), "tk1");
    assert_eq!(TakeForm::parse("tk1").unwrap(), TakeForm::Pct(1.0));
    assert!(TakeForm::parse("tk0").is_err());
    assert!(TakeForm::parse("tk100").is_err());
    assert!(TakeForm::parse("tk1.0").is_err(), "имя не каноническое");
    // Бид 10 000 без фронтрана: вход P + 1 = 10 001; стоп 2 % — 201 тик вниз.
    let touch = bounce_touch(10_000, None);
    let plan = |take: &str| {
        let (_, plan) = bounce_plan(&touch, 0.01, base("pct2", take), None, plain_shape())
            .expect("план строится");
        plan_prices(&plan)
    };
    let (e1, s1, t1) = plan("1to1");
    let (e2, s2, t2) = plan("tk0.5");
    assert!(
        (e1 - e2).abs() < 1e-9 && (s1 - s2).abs() < 1e-9,
        "вход и стоп общие"
    );
    // 1:1 — столько же тиков вверх, сколько стоп вниз; tk0.5 — ceil(0.5 % × 10 001) = 51 тик.
    assert!((t1 - (e1 + (e1 - s1))).abs() < 1e-9, "1to1: {t1}");
    assert!((t2 - (e2 + 51.0 * 0.01)).abs() < 1e-9, "tk0.5: {t2}");
    assert!(t2 < t1, "тейк в % ближе, чем 1:1 при стопе 2 %");
}
