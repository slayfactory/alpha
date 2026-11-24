//! План на действующей сетке биржи (TK-037 п.3, В-172) и прямой повтор.

use super::*;

// -----------------------------------------------------------------------
// TK-037 п.3 (В-172): план на действующей сетке биржи (`bounce_plan_sched`).
// -----------------------------------------------------------------------

pub(super) fn at_step(ts_ns: i64, tick_e9: i64, step_e9: i64) -> StepAt {
    StepAt {
        ts_ns,
        tick_e9,
        step_e9,
    }
}

/// Цена лежит на сетке шага `step` (допуск на двоичную запись).
pub(super) fn on_step(px: f64, step: f64) -> bool {
    let n = px / step;
    (n - n.round()).abs() < 1e-6
}

/// Смена шага цены на PAXG-подобной монете: сетка данных 0.001, до `CHANGE_NS` действовал шаг 0.01.
pub(super) const CHANGE_NS: i64 = 5_000_000_000;

pub(super) fn paxg_schedule() -> StepSchedule {
    StepSchedule::from_header(
        1_000_000,
        1_000_000,
        &[
            at_step(0, 10_000_000, 1_000_000),
            at_step(CHANGE_NS, 1_000_000, 1_000_000),
        ],
    )
    .unwrap()
}

/// Касание, начавшееся в `start_ms`, на цене `price_tick` (тики сетки данных 0.001).
pub(super) fn paxg_touch(
    start_ms: i64,
    price_tick: i64,
    frontrun_tick: Option<i64>,
) -> TouchRecord {
    TouchRecord {
        start_ms,
        end_ms: start_ms + 1_000,
        ..bounce_touch(price_tick, frontrun_tick)
    }
}

pub(super) fn paxg_shape() -> PlanShape {
    PlanShape {
        lot: 0.001,
        ..plain_shape()
    }
}

pub(super) fn sched_plan(
    touch: &TouchRecord,
    schedule: &StepSchedule,
    form: BounceForm,
    shape: PlanShape,
) -> Option<(i8, TradePlan)> {
    bounce_plan_sched(touch, 1_000_000, 1_000_000, schedule, form, None, shape)
}

pub(super) fn plan_tick_px(plan: &TradePlan) -> f64 {
    match plan {
        TradePlan::Bounce { tick_px, .. } => *tick_px,
        TradePlan::SpreadHold => panic!("отскок обязан быть Bounce"),
    }
}

/// До смены шага цены ордера встают только на старую (грубую) цену, `tick_px` — старый шаг.
#[test]
fn sched_before_the_price_step_change_keeps_every_price_on_the_old_step() {
    let schedule = paxg_schedule();
    for frontrun in [None, Some(3_000_010), Some(3_000_030)] {
        let touch = paxg_touch(1_000, 3_000_000, frontrun);
        let (_, plan) = sched_plan(&touch, &schedule, base("pct2", "1to1"), paxg_shape())
            .expect("план строится");
        let (entry, stop, take) = plan_prices(&plan);
        let TradePlan::Bounce { level_px, .. } = plan else {
            panic!("отскок обязан быть Bounce");
        };
        for px in [entry, stop, take, level_px] {
            assert!(on_step(px, 0.01), "{px} не на шаге 0.01 ({frontrun:?})");
        }
        assert!(close(plan_tick_px(&plan), 0.01));
        assert!(close(level_px, 3_000.0));
        // Без фронтрана вход — 1 действующий тик (0.01), не мелкий 0.001.
        if frontrun.is_none() {
            assert!(close(entry - level_px, 0.01), "{entry}");
        }
        // Прежний `bounce_plan` на сетке данных поставил бы вход на мелкую цену 0.001.
        if frontrun.is_none() {
            let (_, old) = bounce_plan(&touch, 0.001, base("pct2", "1to1"), None, paxg_shape())
                .expect("план строится");
            assert!(!on_step(plan_prices(&old).0, 0.01));
        }
    }
    // Уровень вне старой сетки — плана нет (вызывающий считает пропуск).
    let off_grid = paxg_touch(1_000, 3_000_005, None);
    assert!(sched_plan(&off_grid, &schedule, base("pct2", "1to1"), paxg_shape()).is_none());
}

/// После смены — новый шаг: вход в 1 тик = 0.001, `tick_px` = 0.001, план равен прежнему `bounce_plan`.
#[test]
fn sched_after_the_price_step_change_uses_the_new_step() {
    let schedule = paxg_schedule();
    let form = base("pct2", "1to1");
    let touch = paxg_touch(6_000, 3_000_005, None);
    let sched = sched_plan(&touch, &schedule, form, paxg_shape());
    let (_, plan) = sched.expect("план строится");
    let (entry, _, _) = plan_prices(&plan);
    let TradePlan::Bounce { level_px, .. } = plan else {
        panic!("отскок обязан быть Bounce");
    };
    assert!(close(plan_tick_px(&plan), 0.001));
    assert!(close(entry - level_px, 0.001), "{entry} против {level_px}");
    assert_eq!(
        sched,
        bounce_plan(&touch, 0.001, form, None, paxg_shape()),
        "один шаг — прежний план байт в байт"
    );
    // Ровно на метке смены (`ts <= t`) уже действует новый шаг.
    let on_edge = paxg_touch(5_000, 3_000_005, None);
    assert!(sched_plan(&on_edge, &schedule, form, paxg_shape()).is_some());
}

/// Смена лота (LAB-подобная монета): до смены `lot_qty` — старый лот, `level_qty` прежний; после — как раньше.
#[test]
fn sched_before_the_lot_change_uses_the_old_lot_and_keeps_level_qty() {
    // Сетка данных 0.01 / 0.1; до смены лот 1.0, после — 0.1; шаг цены не менялся.
    let schedule = StepSchedule::from_header(
        10_000_000,
        100_000_000,
        &[
            at_step(0, 10_000_000, 1_000_000_000),
            at_step(CHANGE_NS, 10_000_000, 100_000_000),
        ],
    )
    .unwrap();
    let form = base("pct2", "1to1");
    let shape = PlanShape {
        lot: 0.1,
        ..plain_shape()
    };
    let lot_fields = |plan: &TradePlan| match plan {
        TradePlan::Bounce {
            level_qty,
            lot_qty,
            tick_px,
            ..
        } => (*level_qty, *lot_qty, *tick_px),
        TradePlan::SpreadHold => panic!("отскок обязан быть Bounce"),
    };
    let sched = |t: &TouchRecord| {
        bounce_plan_sched(t, 10_000_000, 100_000_000, &schedule, form, None, shape)
    };

    let mut before = bounce_touch(1_000, Some(1_005));
    before.size_at_touch = 25;
    let (_, old) = bounce_plan(&before, 0.01, form, None, shape).unwrap();
    let (_, plan) = sched(&before).expect("план строится");
    let (level_qty, lot_qty, tick_px) = lot_fields(&plan);
    assert_eq!(level_qty, lot_fields(&old).0, "level_qty не меняется");
    assert_eq!(level_qty, 25.0 * 0.1);
    assert_eq!(lot_qty, 1.0, "lot_qty — действующий лот");
    assert!(close(tick_px, 0.01));
    assert_eq!(
        plan_prices(&plan),
        plan_prices(&old),
        "цены те же: шаг цены не менялся"
    );

    let mut after = bounce_touch(1_000, Some(1_005));
    after.start_ms = 6_000;
    after.end_ms = 7_000;
    after.size_at_touch = 25;
    assert_eq!(sched(&after), bounce_plan(&after, 0.01, form, None, shape));
}

/// Сутки без смены шага (пустое расписание или запись равна сетке): план — прежний `bounce_plan`, `==`.
#[test]
fn sched_without_a_change_equals_the_plain_bounce_plan() {
    let empty = StepSchedule::from_header(10_000_000, 100_000_000, &[]).unwrap();
    let flat = StepSchedule::from_header(
        10_000_000,
        100_000_000,
        &[
            at_step(0, 10_000_000, 100_000_000),
            at_step(CHANGE_NS, 10_000_000, 100_000_000),
        ],
    )
    .unwrap();
    let shape = PlanShape {
        lot: 0.1,
        ..plain_shape()
    };
    let mut ask = bounce_touch(1_000, Some(995));
    ask.side = Side::Ask;
    for touch in [
        bounce_touch(1_000, None),
        bounce_touch(1_000, Some(1_005)),
        ask,
    ] {
        for form in [
            base("pct2", "1to1"),
            base("stack2", "1to1"),
            base("before", "1to1"),
        ] {
            let plain = bounce_plan(&touch, 0.01, form, Some(20.0), shape);
            for schedule in [&empty, &flat] {
                let sched = bounce_plan_sched(
                    &touch,
                    10_000_000,
                    100_000_000,
                    schedule,
                    form,
                    Some(20.0),
                    shape,
                );
                assert_eq!(sched, plain, "{touch:?}");
            }
        }
    }
}

/// Округление недостающих тиков — в сторону уровня / от рынка, вход не ближе одного действующего тика.
#[test]
fn sched_rounds_frontrun_and_stack_toward_the_level_and_away_from_the_market() {
    let schedule = paxg_schedule();
    let entry_of = |touch: &TouchRecord| {
        let (_, plan) = sched_plan(touch, &schedule, base("pct2", "1to1"), paxg_shape())
            .expect("план строится");
        plan_prices(&plan).0
    };
    // Бид: фронтран 3000.013 → вниз к 3000.010; 3000.004 → вниз к самому уровню → не ближе 1 тика (3000.01).
    assert!(close(
        entry_of(&paxg_touch(1_000, 3_000_000, Some(3_000_013))),
        3_000.01
    ));
    assert!(close(
        entry_of(&paxg_touch(1_000, 3_000_000, Some(3_000_004))),
        3_000.01
    ));
    // Аск: фронтран 2999.987 → вверх (к уровню) до 2999.990.
    let mut ask = paxg_touch(1_000, 3_000_000, Some(2_999_987));
    ask.side = Side::Ask;
    assert!(close(entry_of(&ask), 2_999.99));

    // Стоп «за второй плотностью»: плотность бида на 2999.987 → вниз до 2999.980, стоп на тик глубже — 2999.97.
    let stop_of = |touch: &TouchRecord| {
        let (_, plan) = sched_plan(touch, &schedule, base("stack2", "1to1"), paxg_shape())
            .expect("план строится");
        plan_prices(&plan).1
    };
    let mut bid = paxg_touch(1_000, 3_000_000, Some(3_000_020));
    bid.stack_next_tick = Some(2_999_987);
    assert!(close(stop_of(&bid), 2_999.97), "{}", stop_of(&bid));
    // Аск: плотность на 3000.013 → вверх до 3000.020, стоп — 3000.03.
    ask.stack_next_tick = Some(3_000_013);
    assert!(close(stop_of(&ask), 3_000.03), "{}", stop_of(&ask));
}

/// Подход: тот же план, что у вида подхода как касания (`touch_view_of_approach`), время — взвод.
#[test]
fn approach_plan_sched_is_the_sched_plan_of_the_touch_view() {
    let schedule = paxg_schedule();
    let approach = crate::lob::levels::ApproachRecord {
        side: Side::Bid,
        price_tick: 3_000_000,
        approach_index: 0,
        arm_ms: 1_000,
        arm_dist_bps: 10,
        level_birth_ms: 0,
        size_at_arm: 100,
        best_own_tick: 2_999_000,
        best_opp_tick: 3_010_000,
        flow_1h_lots: 0,
        strength_e2: [-1, -1, -1],
        depth_behind_lots: 0,
        stack_levels_at_arm: 0,
        stack_span_bps_at_arm: 0,
        repeat_count_at_arm: 0,
        frontrun_lots_at_arm: 0,
        p08: Some(Default::default()),
        r1: None,
        touch_start_ms: None,
        disarm_ms: 2_000,
        disarm_reason: crate::lob::levels::ApproachEnd::PriceLeft,
    };
    let form = base("pct2", "1to1");
    let by_approach = approach_plan_sched(
        &approach,
        1_000_000,
        1_000_000,
        &schedule,
        form,
        None,
        paxg_shape(),
    );
    let by_view = sched_plan(
        &touch_view_of_approach(&approach),
        &schedule,
        form,
        paxg_shape(),
    );
    assert!(by_approach.is_some());
    assert_eq!(by_approach, by_view);
    let (_, plan) = by_approach.unwrap();
    assert!(
        close(plan_tick_px(&plan), 0.01),
        "взвод до смены — старый шаг"
    );
}

pub(super) fn write_sched_binlog(
    path: &std::path::Path,
    tick_e9: i64,
    sched: &[crate::binlog::StepAt],
) {
    let header = crate::binlog::Header {
        tick_e9,
        step_e9: 1_000_000,
        max_records_per_frame: 8,
    };
    let file = std::fs::File::create(path).unwrap();
    let w = if sched.is_empty() {
        crate::binlog::Writer::create(file, header, 1).unwrap()
    } else {
        crate::binlog::Writer::create_with_schedule(file, header, sched, 1).unwrap()
    };
    drop(w.into_inner());
}

#[test]
fn read_day_schedule_none_for_v3_some_for_v4_and_fails_closed() {
    let dir = tempfile::tempdir().unwrap();
    let at = |ts, tick| crate::binlog::StepAt {
        ts_ns: ts,
        tick_e9: tick,
        step_e9: 1_000_000,
    };
    let v3 = dir.path().join("a.binlog");
    let v4 = dir.path().join("b.binlog");
    write_sched_binlog(&v3, 1_000_000, &[]);
    write_sched_binlog(&v4, 1_000_000, &[at(0, 10_000_000), at(5, 1_000_000)]);
    assert!(
        read_day_schedule(std::slice::from_ref(&v3), 1_000_000, 1_000_000)
            .unwrap()
            .is_none()
    );
    let sc = read_day_schedule(std::slice::from_ref(&v4), 1_000_000, 1_000_000)
        .unwrap()
        .unwrap();
    assert_eq!(sc.at(4), (10_000_000, 1_000_000));
    assert_eq!(sc.at(5), (1_000_000, 1_000_000));
    // две части в сутках с v4 и сетка символа не равна заголовку — отказ
    assert!(read_day_schedule(&[v3, v4.clone()], 1_000_000, 1_000_000).is_err());
    assert!(read_day_schedule(&[v4], 2_000_000, 1_000_000).is_err());
}

/// Прямой путь `ReplayFeed::drive_raw` даёт те же компактные события в том же
/// порядке, что `translate_feed_until` через `next_event`: снапшоты, дельты, сделки, группы через
/// границу кадра, потолок времени.
#[test]
fn direct_replay_path_matches_feed_translation() {
    use crate::binlog::{Header, Record, Writer};
    use crate::feed::replay::ReplayFeed;
    use hftbacktest::types::{LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT};

    let header = Header {
        tick_e9: 10_000_000,
        step_e9: 1_000_000,
        max_records_per_frame: 100,
    };
    let mut seed = 0x1234_5678_9abc_def0_u64;
    let mut rnd = move |m: u64| {
        seed = seed
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (seed >> 33) % m
    };
    let (mut exch, mut local) = (1_000_000_000_i64, 1_000_000_500_i64);
    let mut recs = Vec::new();
    for _ in 0..3000 {
        local += 1 + rnd(5) as i64;
        if rnd(3) == 0 {
            exch += 1_000_000 * (1 + rnd(3) as i64);
        }
        let k = rnd(20);
        let ev = match k {
            0 => LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
            1 => LOCAL_ASK_DEPTH_SNAPSHOT_EVENT,
            2 | 3 => LOCAL_BUY_TRADE_EVENT,
            4 => LOCAL_SELL_TRADE_EVENT,
            5..=12 => LOCAL_BID_DEPTH_EVENT,
            _ => LOCAL_ASK_DEPTH_EVENT,
        };
        recs.push(Record {
            ev,
            exch_ts_ns: exch,
            local_ts_ns: local,
            price_ticks: 100 + rnd(12) as i64,
            qty_lots: if rnd(4) == 0 { 0 } else { 1 + rnd(50) as i64 },
            block: rnd(9) == 0,
            rpi: rnd(11) == 0,
        });
    }
    let mut buf = Vec::new();
    let mut w = Writer::create(&mut buf, header, 0).unwrap();
    let mut i = 0;
    while i < recs.len() {
        let n = (1 + rnd(7) as usize).min(recs.len() - i);
        w.write_frame(&recs[i..i + n]).unwrap();
        i += n;
    }
    w.flush().unwrap();
    drop(w);

    for until in [None, Some(local / 2), Some(1_000_000_600), Some(i64::MAX)] {
        let mut old = Vec::new();
        let mut f = ReplayFeed::open(0, &buf[..]).unwrap();
        let hit_old =
            super::super::feed::translate_feed_until(&mut f, until, &mut |ev| old.push(ev));
        let mut new = Vec::new();
        let mut f = ReplayFeed::open(0, &buf[..]).unwrap();
        let hit_new = super::super::feed::replay_compact_into_until(&mut f, until, &mut new);
        assert!(!matches!(until, None | Some(i64::MAX)) || !old.is_empty());
        assert_eq!(hit_old, hit_new, "until {until:?}");
        assert_eq!(old, new, "until {until:?}");
    }
}
