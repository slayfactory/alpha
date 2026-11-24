//! Форма входа «лестница»: ноги на целых тиках, предел ёмкости.

use super::*;

// -----------------------------------------------------------------------
// F6 (план 2026-09-20, §3): форма входа — `single@fr` или лестница
// `ladder<N>x<from>..<to>[w<k>]`, ноги — целыми тиками, совпавшие тики
// складываются, нижняя нога может весить к стене.
// -----------------------------------------------------------------------

/// Разбор имени формы входа: каноническое имя (как у `StopForm`/`TakeForm`),
/// границы полосы и веса, ёмкость ног. Имя — единственный источник чисел
/// `N`/`from`/`to`/`w` (предрегистрация), умолчаний нет.
/// В-131: σ-лестница `ladder<N>x<a>..<b>s[w<k>]` — разбор, каноническое имя, отказы; прежняя лестница в bps
/// разбирается как раньше.
#[test]
fn entry_form_parses_the_sigma_ladder() {
    let f = EntryForm::parse("ladder3x0.25..1.5sw2").unwrap();
    assert_eq!(
        f,
        EntryForm::LadderSigma {
            legs: 3,
            from_sigma: 0.25,
            to_sigma: 1.5,
            wall_weight: 2,
        }
    );
    assert_eq!(f.label(), "ladder3x0.25..1.5sw2");
    assert_eq!(
        EntryForm::parse("ladder4x0.5..2s").unwrap().label(),
        "ladder4x0.5..2s"
    );
    assert!(matches!(
        EntryForm::parse("ladder3x2..20w2").unwrap(),
        EntryForm::Ladder { .. }
    ));
    // В-131 «Окончательно»: у σ-лестницы ближняя нога на `a = 0` (Г-85б `ladder3x0..<k>sw2`).
    assert_eq!(
        EntryForm::parse("ladder3x0..0.0409sw2").unwrap(),
        EntryForm::LadderSigma {
            legs: 3,
            from_sigma: 0.0,
            to_sigma: 0.0409,
            wall_weight: 2,
        }
    );
    for bad in [
        "ladder3x0.25..1.5sw1",
        "ladder3x1.5..0.25s",
        "ladder3x0..1",
        "ladder3x-0.1..1s",
        "ladder3x0.25s..1.5",
        "ladder3x0.25..1.5ss",
        "ladder3x0.250..1.5s",
    ] {
        assert!(EntryForm::parse(bad).is_err(), "{bad}");
    }
}

/// В-131: нераздельные ноги — на монете с крупным шагом, где `ladder_legs` слил бы ноги в одну цену,
/// каждая следующая нога на тик дальше, первая не ближе `P ± 1`; при широкой полосе — те же цены, что у
/// `ladder_legs`; для аска — зеркально.
#[test]
fn distinct_ladder_legs_never_share_a_tick() {
    // Цена 100 тиков: 1 тик = 100 bps; полоса 1..30 bps — у `ladder_legs` все три ноги на P + 1.
    let (merged, _) = super::super::forms::ladder_legs(100, 1, 3, 1.0, 30.0, 2);
    assert_eq!(merged.n, 1);
    let (legs, avg) = super::super::forms::ladder_legs_distinct(100, 1, 3, 1.0, 30.0, 2);
    assert_eq!(legs.n, 3);
    assert_eq!(&legs.ticks[..3], &[101, 102, 103]);
    assert!((legs.frac[..3].iter().sum::<f64>() - 1.0).abs() < 1e-12);
    assert!((legs.frac[0] - 0.5).abs() < 1e-12, "вес к стене 2 из 4");
    assert_eq!(avg, 102);
    let (ask, _) = super::super::forms::ladder_legs_distinct(100, -1, 3, 1.0, 30.0, 1);
    assert_eq!(&ask.ticks[..3], &[99, 98, 97]);
    // Мелкий шаг (цена 100 000 тиков): ноги и так разные — те же цены, что у прежней лестницы.
    let (a, _) = super::super::forms::ladder_legs(100_000, 1, 3, 2.0, 20.0, 1);
    let (b, _) = super::super::forms::ladder_legs_distinct(100_000, 1, 3, 2.0, 20.0, 1);
    assert_eq!(a, b);
}

/// В-131: план σ-лестницы — ноги σ·(a…b) bps от стены, нераздельные; `a = 0` — ближняя на `P + 1`;
/// нет σ — нет плана. Г-85б `ladder3x0..0.0409sw2` при σ = 110 bps: дальняя — 4,5 bps.
#[test]
fn sigma_ladder_plan_takes_legs_from_entry_sigma() {
    let touch = bounce_touch(100_000, None);
    let shape = |sigma: Option<f64>| PlanShape {
        entry_form: EntryForm::parse("ladder3x0..0.0409sw2").unwrap(),
        entry_sigma_bps: sigma,
        ..plain_shape()
    };
    let bf = base("pct2", "1to1");
    assert!(
        bounce_plan(&touch, 0.01, bf, None, shape(None)).is_none(),
        "нет σ — нет плана"
    );
    let (_, plan) = bounce_plan(&touch, 0.01, bf, None, shape(Some(110.0))).unwrap();
    let TradePlan::Bounce { ladder, .. } = plan else {
        panic!("отскок обязан быть Bounce");
    };
    // 100 000 тиков: 1 тик = 0,1 bps; средняя 0,0409/2·110 = 2,2495 bps → 23 тика, дальняя 4,499 → 45.
    assert_eq!(ladder.n, 3);
    assert_eq!(&ladder.ticks[..3], &[100_001, 100_023, 100_045]);
    assert!((ladder.frac[0] - 0.5).abs() < 1e-12, "вес к стене 2 из 4");
    // Крупный шаг (цена 100 тиков, 1 тик = 100 bps): ноги не сливаются — P+1, P+2, P+3.
    let coarse = bounce_touch(100, None);
    let (_, plan) = bounce_plan(&coarse, 0.01, bf, None, shape(Some(110.0))).unwrap();
    let TradePlan::Bounce { ladder, .. } = plan else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(&ladder.ticks[..3], &[101, 102, 103]);
}

#[test]
fn entry_form_parses_the_ladder_and_the_single_frontrun() {
    assert_eq!(
        EntryForm::parse("single@fr").unwrap(),
        EntryForm::SingleFrontrun
    );
    assert_eq!(EntryForm::SingleFrontrun.label(), "single@fr");
    assert_eq!(
        EntryForm::parse("ladder3x2..10").unwrap(),
        EntryForm::Ladder {
            legs: 3,
            from_bps: 2.0,
            to_bps: 10.0,
            wall_weight: 1,
        }
    );
    assert_eq!(
        EntryForm::parse("ladder3x2..10").unwrap().label(),
        "ladder3x2..10"
    );
    let weighted = EntryForm::parse("ladder4x0.5..2w2").unwrap();
    assert_eq!(
        weighted,
        EntryForm::Ladder {
            legs: 4,
            from_bps: 0.5,
            to_bps: 2.0,
            wall_weight: 2,
        }
    );
    assert_eq!(weighted.label(), "ladder4x0.5..2w2");
    // Отказы: чужое имя, неканоническая запись, границы, ёмкость.
    assert!(EntryForm::parse("fr").is_err());
    assert!(
        EntryForm::parse("ladder3x2..10w1").is_err(),
        "вес 1 не пишется"
    );
    assert!(
        EntryForm::parse("ladder03x2..10").is_err(),
        "нули в числе ног"
    );
    assert!(
        EntryForm::parse("ladder3x2.0..10").is_err(),
        "неканоническая запись bps"
    );
    assert!(
        EntryForm::parse("ladder1x2..10").is_err(),
        "одна нога — не лестница"
    );
    assert!(EntryForm::parse("ladder3x0..10").is_err(), "from = 0");
    assert!(EntryForm::parse("ladder3x10..2").is_err(), "from ≥ to");
    assert!(
        EntryForm::parse("ladder9x2..10").is_err(),
        "ног больше ёмкости"
    );
    assert!(EntryForm::parse("ladder3x2..10w0").is_err(), "вес 0");
}

/// Ноги лестницы ложатся на **целые тики** (`bps_to_ticks_ceil` — расстояние
/// не меньше заданного), средняя цена плана — по долям, а цена уровня остаётся
/// стеной. Бид 100.00, шаг 0.01: 2/6/10 bps — это 2/6/10 тиков.
#[test]
fn ladder_legs_land_on_whole_ticks_and_the_plan_average_is_weighted() {
    let touch = bounce_touch(10_000, None);
    let build = |spec: &str| {
        let (_, plan) = bounce_plan(
            &touch,
            0.01,
            base("pct2", "1to1"),
            None,
            PlanShape {
                entry_form: EntryForm::parse(spec).unwrap(),
                ..plain_shape()
            },
        )
        .expect("план строится");
        plan
    };
    let TradePlan::Bounce {
        ladder,
        entry_px,
        level_px,
        ..
    } = build("ladder3x2..10")
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(ladder.n, 3);
    assert_eq!(&ladder.ticks[..3], &[10_002, 10_006, 10_010]);
    for i in 0..3 {
        assert!(
            (ladder.frac[i] - 1.0 / 3.0).abs() < 1e-12,
            "равные доли: {}",
            ladder.frac[i]
        );
    }
    // Средняя — ближайший тик к взвешенной сумме: 10 006 → 100.06.
    assert!((entry_px - 100.06).abs() < 1e-9, "{entry_px}");
    assert!((level_px - 100.0).abs() < 1e-9, "стена — цена уровня");

    // Вес к стене: нижняя нога ×2 — доли 2/4, 1/4, 1/4, средняя (2×10002 +
    // 10006 + 10010)/4 = 10005 → 100.05.
    let TradePlan::Bounce {
        ladder, entry_px, ..
    } = build("ladder3x2..10w2")
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(ladder.n, 3);
    assert!((ladder.frac[0] - 0.5).abs() < 1e-12);
    assert!((ladder.frac[1] - 0.25).abs() < 1e-12 && (ladder.frac[2] - 0.25).abs() < 1e-12);
    assert!((entry_px - 100.05).abs() < 1e-9, "{entry_px}");
}

/// Совпавшие тики складываются в одну ногу с суммарной долей: у дешёвой монеты
/// (10.00, шаг 0.01) 2/6/10 bps — это 0.2/0.6/1.0 тика, все три округляются
/// вверх до первого тика (не меньше заданного), и биржа получила бы три заявки
/// по одной цене — а это одна нога и вся сумма долей.
#[test]
fn ladder_legs_on_the_same_tick_merge_into_one_with_the_summed_share() {
    let touch = bounce_touch(1_000, None);
    let (_, plan) = bounce_plan(
        &touch,
        0.01,
        base("pct2", "1to1"),
        None,
        PlanShape {
            entry_form: EntryForm::parse("ladder3x2..10").unwrap(),
            ..plain_shape()
        },
    )
    .expect("план строится");
    let TradePlan::Bounce { ladder, .. } = plan else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(ladder.n, 1, "совпавшие тики — одна нога");
    assert_eq!(ladder.ticks[0], 1_001);
    assert!((ladder.frac[0] - 1.0).abs() < 1e-12, "{}", ladder.frac[0]);
}

/// F8b (С11 аудита 21.09): граница ёмкости ног. `ladder8x…` — ровно
/// `MAX_ENTRY_LEGS` ног: `ladder_legs` заполняет массив до последнего места
/// (его `push` за ёмкостью — отказ, а не усечение), доли нормированы в
/// единицу, тики идут от стены к рынку, а средняя цена плана — взвешенная.
#[test]
fn ladder_at_capacity_fills_every_leg_and_keeps_the_share_sum() {
    let touch = bounce_touch(10_000, None);
    let (_, plan) = bounce_plan(
        &touch,
        0.01,
        base("pct2", "1to1"),
        None,
        PlanShape {
            entry_form: EntryForm::parse("ladder8x1..8").unwrap(),
            ..plain_shape()
        },
    )
    .expect("план строится");
    let TradePlan::Bounce {
        ladder,
        entry_px,
        tick_px,
        ..
    } = plan
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(
        ladder.n as usize,
        crate::lob::strategy::MAX_ENTRY_LEGS,
        "восемь ног — вся ёмкость"
    );
    // Первая нога — тик над стеной (1 bps при цене 100.00).
    assert_eq!(ladder.ticks[0], 10_001, "нога у стены");
    for i in 1..ladder.n as usize {
        assert!(
            ladder.ticks[i] > ladder.ticks[i - 1],
            "ноги идут от стены к рынку: {:?}",
            &ladder.ticks[..ladder.n as usize]
        );
    }
    let sum: f64 = ladder.frac[..ladder.n as usize].iter().sum();
    assert!((sum - 1.0).abs() < 1e-12, "доли нормированы: {sum}");
    let weighted = ladder.weighted_avg_tick() * tick_px;
    // Цена плана — ближайший **тик** к взвешенной сумме (цены живут на сетке
    // тиков), поэтому сравнивается округление, а не сама дробная сумма.
    let nearest = (weighted / tick_px).round() * tick_px;
    assert!(
        (entry_px - nearest).abs() < 1e-9,
        "средняя плана — взвешенная по долям: {entry_px} против {weighted}"
    );
}
