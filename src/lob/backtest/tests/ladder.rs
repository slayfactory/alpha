//! Сквозной прогон: лестница входа, ранний выход, выход двумя ногами.

use super::*;

/// Лестница входа (решение владельца 2026-09-13, переписано F4/В-78):
/// исполнившаяся нога остальные **не снимает** — вход копит позицию, пока жив
/// (до `entry_ttl_ns`), и цена входа берётся с исполненных, а не с первой.
/// Здесь дальняя нога (104) исполняется на 2 с, а на 10 с — вторая продажа по
/// 101, которая добирает остальные (приоритет цены по 102..104, очередь по
/// 101): вход набран целиком (`legs_filled = 4`), и только тогда круг уходит
/// в `Holding`. Прежний код снял бы ноги 101..103 при первом исполнении, и
/// набралось бы 0.25 вместо 1.0 — это и есть проверка накопления.
#[test]
fn ladder_entry_accumulates_legs_until_the_entry_is_over() {
    // Стена на бид 100, аск 105: покупки по 101..104 стоят в стороне от
    // рынка. Продажа-агрессор ровно по 104 исполняет **дальнюю** ногу;
    // следующая, по 101, закрывает всю лестницу. Средняя исполненного —
    // 102.5, поэтому стоп (99) и тейк (103) формы сдвинуты на неё: тейк
    // 106, и бид доходит до 106. Лот 0.25 (у биржи модели и у плана): ноги
    // лестницы — целые шаги лота (R3), и 1.0 на четыре ноги — по шагу на ногу;
    // при лоте 1.0 весь вход ушёл бы одной ногой.
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        trade_at(2 * S, true, 104.0, 5.0),
        trade_at(10 * S, true, 101.0, 5.0),
        depth_at(30 * S, true, 106.0, 5.0),
        depth_at(30 * S, false, 107.0, 5.0),
        // Хвост: ответу на выход нужно событие после срабатывания тейка.
        depth_at(32 * S, false, 107.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.25,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        // Стоп далеко (90): средняя исполненного (102.5) сдвигает и стоп —
        // форма «1:1» поставила бы его выше текущего бида (100) и выбила бы
        // круг сразу, а тест про накопление позиции, не про геометрию стопа.
        stop_px: 90.0,
        take_px: 103.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 4,
        grid_step_px: 1.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 0.25,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    };
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            touch_index: 0,
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan,
            profile: 0,
            qty: None,
        }],
        &drive_cfg(),
    )
    .unwrap();

    assert!(
        !run.incomplete,
        "круг обязан закрыться: fills={} misses={:?} pos={} orders={:?}",
        run.fills.len(),
        run.misses,
        hbt.position(0),
        hbt.orders(0)
            .values()
            .map(|o| (o.order_id, o.status))
            .collect::<Vec<_>>()
    );
    assert_eq!(run.fills.len(), 1, "ровно один круг");
    let fill = run.fills[0];
    // В-78: позиция — сумма исполненных ног (0.25 × 4), а не одна нога, и
    // средняя цена — средневзвешенная по ним (102.5). Прежний код снял бы
    // ноги 101..103 при первом исполнении, и здесь стояло бы 0.25.
    assert_eq!(fill.legs_filled, 4, "накоплены все четыре ноги лестницы");
    assert!(
        close(fill.qty, 1.0),
        "позиция круга — весь вход: {}",
        fill.qty
    );
    assert!(
        close(fill.entry_vwap, 102.5),
        "средняя исполненного входа (101+102+103+104)/4: {}",
        fill.entry_vwap
    );
    assert!(
        close(fill.entry_px, 102.5),
        "простая средняя по исполненным ногам: {}",
        fill.entry_px
    );
    assert_eq!(
        run.fill_reason,
        vec![crate::lob::strategy::ExitReason::Take],
        "выход — тейк, сдвинутый на среднюю (103 + 2.5 → 106)"
    );
    assert!(
        close(fill.exit_px, 106.0),
        "выход по тейку от средней цены: {}",
        fill.exit_px
    );
    assert_eq!(run.fill_signal, vec![0]);
    assert_eq!(hbt.position(0), 0.0, "позиция плоская после выхода");
    let live = hbt
        .orders(0)
        .values()
        .filter(|o| o.status == Status::New || o.status == Status::PartiallyFilled)
        .count();
    assert_eq!(live, 0, "к концу круга живых ног нет");
}

/// B4 (В-58 п. 5): досрочный выход «по прилипанию». Вход исполняется на 2 с,
/// уровень `P = 100` остаётся лучшим бидом, и через `X = 1 с` после входа
/// сделка выходит **по рынку с причиной `Early`** — не стопом, не тейком и не
/// дедлайном (все три здесь недостижимы: стоп 99, тейк 103, дедлайн 60 с).
#[test]
fn early_exit_leaves_a_level_that_sticks_for_x_seconds() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        // Продажа-агрессор ровно в наш лимит 101 — вход исполнен на 2 с.
        trade_at(2 * S, true, 101.0, 5.0),
        // Уровень держится: бид всё те же 100.
        depth_at(20 * S, true, 100.0, 5.0),
        depth_at(20 * S, false, 105.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 99.0,
        take_px: 103.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: S,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    };
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            touch_index: 0,
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan,
            profile: 0,
            qty: None,
        }],
        &drive_cfg(),
    )
    .unwrap();

    assert!(
        !run.incomplete,
        "круг обязан закрыться: fills={} misses={:?}",
        run.fills.len(),
        run.misses
    );
    assert_eq!(run.fills.len(), 1, "ровно один круг");
    assert_eq!(
        run.fill_reason,
        vec![crate::lob::strategy::ExitReason::Early],
        "причина выхода — прилипание"
    );
    assert_eq!(run.exits.early, 1);
    assert_eq!(run.exits.stop, 0, "стоп 99 не достигнут");
    assert_eq!(run.exits.take, 0, "тейк 103 не достигнут");
    assert_eq!(run.exits.deadline, 0, "дедлайн 60 с не наступил");
    // Выход по рынку: исполнение по лучшему биду 100, а не по тейку 103.
    assert!(
        close(run.fills[0].exit_px, 100.0),
        "выход по рынку на 100, а не по тейку: {}",
        run.fills[0].exit_px
    );
}

/// B4, отрицательный контроль: цена ушла с уровня вверх (`102` против уровня
/// `100` — больше половины тика), значит касание разрешилось, и «прилипания»
/// нет: `Early` не выдаётся, даже когда `X` истёк.
#[test]
fn early_exit_does_not_fire_once_the_price_left_the_level() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        trade_at(2 * S, true, 101.0, 5.0),
        // Цена ушла с уровня: лучший бид 102.
        depth_at(3 * S, true, 102.0, 5.0),
        depth_at(20 * S, true, 102.0, 5.0),
        depth_at(20 * S, false, 105.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 99.0,
        take_px: 103.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: S,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    };
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            touch_index: 0,
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan,
            profile: 0,
            qty: None,
        }],
        &drive_cfg(),
    )
    .unwrap();

    assert!(
        !run.fill_reason
            .iter()
            .any(|r| matches!(r, crate::lob::strategy::ExitReason::Early)),
        "прилипания нет: цена с уровня ушла — {run:?}"
    );
    assert_eq!(run.exits.early, 0);
}

/// E7: круг с двумя ногами выхода в драйвере — одна `Fill` на круг, цена
/// выхода средневзвешенная по ногам, в сводке `eaten = 1`, `partial = 1`.
#[test]
fn a_two_leg_exit_is_one_fill_with_a_weighted_exit_price() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, true, 99.0, 10.0),
        depth_at(0, false, 105.0, 5.0),
        // Вход 101 исполняет продажа-агрессор на 2 с.
        trade_at(2 * S, true, 101.0, 5.0),
        // Плотность 100 (5 → 2, 60 %) — половина по рынку в бид 100.
        depth_at(4 * S, true, 100.0, 2.0),
        // Плотность на 100 снята целиком (100 % ≥ 80), лучший бид — 99 (стоял
        // с начала): остаток по рынку в бид 99.
        depth_at(6 * S, true, 98.0, 10.0),
        depth_at(6 * S, true, 100.0, 0.0),
        depth_at(30 * S, true, 98.0, 10.0),
        depth_at(30 * S, false, 105.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 90.0,
        take_px: 110.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 50.0,
        eaten_all_pct: 80.0,
        eaten_half_frac: 0.5,
        level_qty: 5.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    };
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            touch_index: 0,
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan,
            profile: 0,
            qty: None,
        }],
        &DriveConfig {
            tape_log_secs: 0,
            tape_log_ws: crate::lob::backtest::NO_TAPE_WS,
            order_qty: 2.0,
            first_order_id: 1,
            queue_model: QueueModelKind::RiskAdverse,
            busy_skip: true,
            hold_skip: false,
        },
    )
    .unwrap();
    assert!(!run.incomplete, "{:?}", run.misses);
    assert_eq!(run.fills.len(), 1, "две ноги — один круг");
    let fill = run.fills[0];
    assert!(close(fill.entry_px, 101.0), "{}", fill.entry_px);
    // F3: полное исполнение одной ноги — `entry_vwap` равен прежней
    // `entry_px`, `qty` — плановому размеру, `fill_frac` — единице.
    assert!(close(fill.entry_vwap, 101.0), "{}", fill.entry_vwap);
    assert!(close(fill.qty, 2.0), "{}", fill.qty);
    assert!(close(fill.fill_frac, 1.0), "{}", fill.fill_frac);
    assert_eq!(fill.legs_filled, 1);
    assert!(!fill.fill_by_cross, "вход исполнен сделкой, а не крестом");
    // Половина по 100, половина по 99 → 99.5.
    assert!(
        close(fill.exit_px, 99.5),
        "средневзвешенная цена выхода: {}",
        fill.exit_px
    );
    assert!(fill.exit_taker);
    assert_eq!(run.exits.eaten, 1);
    assert_eq!(run.exits.partial, 1);
    assert_eq!(run.fill_reason, vec![ExitReason::Eaten]);
    assert_eq!(hbt.position(0), 0.0);
}
