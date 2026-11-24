//! F3 (план 2026-09-20): модель очереди по объёму, частичное исполнение, пропуск удержания.

use super::*;

// -----------------------------------------------------------------------
// F3 (план 2026-09-20): модель очереди по объёму, частичное исполнение и
// три пути исполнения крейта.
// -----------------------------------------------------------------------

/// План F3: вход лестницей по `entry_px` (`legs` ног с шагом `step`, каждая
/// следующая дальше от плотности), тейк `take_px`, стоп далеко, дедлайн 60 с,
/// срок жизни входа 20 с.
pub(super) fn f3_plan(entry_px: f64, take_px: f64, legs: u8, step: f64) -> TradePlan {
    TradePlan::Bounce {
        entry_px,
        stop_px: 90.0,
        take_px,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: legs,
        grid_step_px: step,
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
        lot_qty: 0.1,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    }
}

#[test]
fn queue_model_kind_parses_and_labels() {
    assert_eq!(
        QueueModelKind::parse("risk-adverse"),
        Ok(QueueModelKind::RiskAdverse)
    );
    assert_eq!(
        QueueModelKind::parse(" risk-adverse "),
        Ok(QueueModelKind::RiskAdverse)
    );
    assert_eq!(
        QueueModelKind::parse("prob:3.0"),
        Ok(QueueModelKind::Prob { n: 3.0 })
    );
    assert_eq!(
        QueueModelKind::label(QueueModelKind::RiskAdverse),
        "risk-adverse"
    );
    assert_eq!(
        QueueModelKind::label(QueueModelKind::Prob { n: 1.5 }),
        "prob:1.5"
    );
    for bad in [
        "",
        "prob",
        "prob:",
        "prob:abc",
        "prob:nan",
        "prob:inf",
        "prob:0",
        "prob:-1",
        "risk-averse",
    ] {
        assert!(
            QueueModelKind::parse(bad).is_err(),
            "{bad:?} обязан быть отказом"
        );
    }
}

/// Путь (1), модель по объёму: заявка исполняется **частично**. Лестница из
/// двух ног по 1 лоту: продажа 0.1 в дальнюю ногу (99, очередь впереди пуста)
/// даёт 0.1 исполнения, а нога 100 исполняется целиком приоритетом цены —
/// позиция возникает, круг закрывается. `qty` несёт реальное исполнение
/// (1.1 из 2.0), `fill_frac` < 1, `entry_vwap` — средневзвешенную цену.
/// Средняя (99.9) сдвигает тейк формы (100) на тик вверх — 101, и бид доходит
/// до него (F4: стоп и тейк считаются от средней цены исполненного, В-78).
#[test]
fn partial_fill_records_real_qty_and_fill_frac() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        // Продажа 0.1 ровно по 99: впереди в дальней ноге пусто → 0.1
        // исполнения; ближняя нога 100 исполняется целиком (сделка ниже неё).
        trade_at(2 * S, true, 99.0, 0.1),
        // Вход живёт до срока (20 с) — нога 99 стоит недоисполненной, — и
        // круг уходит в `Holding` только после снятия входа. Средняя (99.9 → тик
        // 100) сдвигает тейк формы на тик вверх: 101, и бид доходит до него.
        depth_at(32 * S, true, 101.0, 5.0),
        depth_at(32 * S, false, 105.0, 5.0),
        // Хвост, чтобы ответ на выход успел дойти.
        depth_at(40 * S, false, 105.0, 5.0),
    ];
    let cfg = DriveConfig {
        tape_log_secs: 0,
        tape_log_ws: crate::lob::backtest::NO_TAPE_WS,
        order_qty: 2.0,
        first_order_id: 1,
        queue_model: QueueModelKind::Prob { n: 3.0 },
        busy_skip: true,
        hold_skip: false,
    };
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        cfg.queue_model,
    );
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            touch_index: 0,
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan: f3_plan(99.0, 100.0, 2, 1.0),
            profile: 0,
            qty: None,
        }],
        &cfg,
    )
    .unwrap();

    assert!(!run.incomplete, "круг обязан закрыться: {:?}", run.misses);
    assert_eq!(run.fills.len(), 1, "ровно один круг");
    let fill = run.fills[0];
    assert!(
        close(fill.qty, 1.1),
        "исполнено 1.1 из 2.0 (0.1 + 1.0): {}",
        fill.qty
    );
    assert!(
        close(fill.fill_frac, 1.1 / 2.0),
        "доля исполнения круга: {}",
        fill.fill_frac
    );
    assert_eq!(fill.legs_filled, 2, "обе ноги дали исполнение");
    assert!(
        close(fill.entry_vwap, 109.9 / 1.1),
        "средневзвешенная цена входа 0.1×99 + 1.0×100: {}",
        fill.entry_vwap
    );
    assert!(
        close(fill.entry_px, 99.5),
        "простая средняя по ногам остаётся прежней: {}",
        fill.entry_px
    );
    assert!(
        !fill.fill_by_cross,
        "исполнение пришло сделкой на нашей стороне цены — путь (1)/(2), не крест"
    );
    // `Bot::position` крейта здесь **не ноль** — и это ожидаемо: `Local`
    // обновляет позицию только на `Filled`, а частично исполненная нога 99
    // (0.1, снята недоисполненной) в неё не попала (находка F3). Круг вышел на
    // свою позицию 1.1, локальная позиция ушла в −0.1 — тот самый мнимый
    // остаток, из-за которого страховка остатка в драйвере считает теперь
    // **свою** позицию стратегии (F4, В-78).
    assert!(
        close(hbt.position(0), -0.1),
        "локальная позиция крейта не видит частичного исполнения: {}",
        hbt.position(0)
    );
}

/// Путь (3): лучший аск опустился до нашей цены **без сделки** — крейт
/// исполняет весь остаток (`on_best_ask_update`), и круг помечен
/// `fill_by_cross`. Сделки в буфере нет, поэтому детектор видит именно крест.
#[test]
fn fill_by_cross_is_flagged_when_no_trade_could_fill() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        // Аск 105 снят, аск 102 встал прямо на нашу цену — сделок нет.
        depth_at(2 * S, false, 105.0, 0.0),
        depth_at(2 * S, false, 102.0, 5.0),
        depth_at(30 * S, true, 100.0, 5.0),
        depth_at(30 * S, false, 102.0, 5.0),
    ];
    let cfg = DriveConfig {
        tape_log_secs: 0,
        tape_log_ws: crate::lob::backtest::NO_TAPE_WS,
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::Prob { n: 3.0 },
        busy_skip: true,
        hold_skip: false,
    };
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        cfg.queue_model,
    );
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            touch_index: 0,
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan: f3_plan(102.0, 100.0, 1, 0.0),
            profile: 0,
            qty: None,
        }],
        &cfg,
    )
    .unwrap();

    assert!(!run.incomplete, "{:?}", run.misses);
    assert_eq!(run.fills.len(), 1);
    let fill = run.fills[0];
    assert!(
        fill.fill_by_cross,
        "исполнение обновлением лучшей цены — путь (3)"
    );
    assert!(
        close(fill.fill_frac, 1.0),
        "крейт отдаёт весь остаток: {}",
        fill.fill_frac
    );
    assert!(!fill.entry_taker, "исполнение лимитной заявки — мейкерское");
    assert_eq!(fill.legs_filled, 1);
}

/// Путь (2): сделка **ниже** нашей цены (в стену) исполняет весь остаток по
/// приоритету цены; сделка в буфере есть, поэтому путь (3) не отмечается —
/// это и отличает счётчик `n_fill_by_cross` от «исполнений без сделки на
/// нашей цене».
#[test]
fn trade_below_our_price_fills_by_priority_and_is_not_a_cross() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        // Продажа-агрессор ниже нашего лимита 102 — заявка исполняется целиком
        // приоритетом цены.
        trade_at(2 * S, true, 101.0, 7.0),
        depth_at(30 * S, true, 100.0, 5.0),
        depth_at(30 * S, false, 105.0, 5.0),
    ];
    let cfg = DriveConfig {
        tape_log_secs: 0,
        tape_log_ws: crate::lob::backtest::NO_TAPE_WS,
        order_qty: 3.0,
        first_order_id: 1,
        queue_model: QueueModelKind::Prob { n: 3.0 },
        busy_skip: true,
        hold_skip: false,
    };
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        cfg.queue_model,
    );
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            touch_index: 0,
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan: f3_plan(102.0, 100.0, 1, 0.0),
            profile: 0,
            qty: None,
        }],
        &cfg,
    )
    .unwrap();

    assert!(!run.incomplete, "{:?}", run.misses);
    assert_eq!(run.fills.len(), 1);
    let fill = run.fills[0];
    assert!(
        close(fill.fill_frac, 1.0),
        "приоритет цены — весь остаток: {}",
        fill.fill_frac
    );
    assert!(
        !fill.fill_by_cross,
        "сделка в буфере была — это путь (2), не (3)"
    );
}

/// R7 (ревью 23.09): рыночная заявка, прошедшая два уровня книги, стоит
/// средневзвешенную цену своих исполнений, а не цену последнего уровня на
/// весь объём. Биржа частичного исполнения (`prob`) бьёт заявку по уровням
/// (`fill` на каждый), и `exec_price()` крейта остаётся ценой последнего
/// (102) — до исправления драйвер умножал её на весь исполненный объём.
/// Итог ведёт вендорная правка `Order::exec_notional`, читает
/// `executed_notional`.
#[test]
fn market_sweep_over_two_levels_costs_the_weighted_average_price() {
    use hftbacktest::types::{OrdType, TimeInForce};
    let feed = [
        depth_at(0, true, 99.0, 5.0),
        depth_at(0, false, 101.0, 1.0),
        depth_at(0, false, 102.0, 5.0),
        // Хвост, чтобы ответ биржи успел дойти до локальной стороны.
        depth_at(10 * S, true, 99.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::Prob { n: 3.0 },
    );
    hbt.elapse(S).unwrap();
    hbt.submit_buy_order(0, 7, 102.0, 2.0, TimeInForce::GTC, OrdType::Market, false)
        .unwrap();
    hbt.elapse(S).unwrap();
    let o = hbt.orders(0).get(&7).expect("заявка в учёте").clone();
    assert!(close(executed_qty(&o), 2.0), "исполнено целиком: {o:?}");
    assert!(
        close(o.exec_price(), 102.0),
        "крейт хранит цену последнего уровня: {}",
        o.exec_price()
    );
    assert!(
        close(executed_notional(&o), 101.0 + 102.0),
        "стоимость — 1.0×101 + 1.0×102: {}",
        executed_notional(&o)
    );
    assert!(close(executed_notional(&o) / executed_qty(&o), 101.5));
}

/// R7: заявка без накопленного итога (отклик площадки без поля — живой
/// коннектор, который его не ведёт) оценивается прежним `exec_price ×
/// исполненное`, а не нулём: нулевая цена входа сломала бы стоп и тейк.
#[test]
fn executed_notional_falls_back_to_last_price_when_the_venue_kept_no_total() {
    use hftbacktest::types::{OrdType, Side, TimeInForce};
    let mut o = Order::new(
        1,
        100,
        1.0,
        2.0,
        Side::Buy,
        OrdType::Limit,
        TimeInForce::GTC,
    );
    assert_eq!(executed_notional(&o), 0.0, "не исполнено — ноль");
    o.leaves_qty = 0.5;
    o.exec_price_tick = 100;
    assert!(close(executed_notional(&o), 150.0), "1.5 × 100 без итога");
    o.exec_notional = 149.0;
    assert!(
        close(executed_notional(&o), 149.0),
        "итог площадки — главнее"
    );
}

/// B4 (ревью 23.09): остаток шорта гасится **покупкой** — знак остатка для
/// `flatten_residual` берётся от стороны входа, а не от `position()` без знака.
#[test]
fn a_short_residual_is_flattened_by_buying() {
    assert!(
        close(signed_residual(HbtSide::Buy, 0.5), 0.5),
        "лонг — продажей"
    );
    assert!(
        close(signed_residual(HbtSide::Sell, 0.5), -0.5),
        "шорт — покупкой"
    );
    assert_eq!(signed_residual(HbtSide::Sell, 0.0), 0.0);
}

/// Строка потока в компактном виде — только если она им представима побитно (иначе тест врёт о фикстуре).
pub(super) fn compact_of(e: &Event) -> CompactEvent {
    try_compact_of(e).unwrap_or_else(|| panic!("фикстура не представима компактно: {e:?}"))
}

pub(super) fn try_compact_of(e: &Event) -> Option<CompactEvent> {
    let kind = if e.is(LOCAL_BID_DEPTH_EVENT) {
        EventKind::BidDepth
    } else if e.is(LOCAL_ASK_DEPTH_EVENT) {
        EventKind::AskDepth
    } else if e.is(BUY_EVENT) {
        EventKind::BuyTrade
    } else {
        EventKind::SellTrade
    };
    if e.exch_ts % 1_000_000 != 0 {
        return None;
    }
    let c = CompactEvent::new(
        kind,
        e.exch_ts / 1_000_000,
        e.local_ts,
        (e.px * 1e9).round() as i64,
        (e.qty * 1e9).round() as i64,
    );
    let back = c.expand();
    (back.ev == e.ev
        && back.exch_ts == e.exch_ts
        && back.local_ts == e.local_ts
        && back.px.to_bits() == e.px.to_bits()
        && back.qty.to_bits() == e.qty.to_bits())
    .then_some(c)
}

/// Р6: сутки компактными событиями — тот же прогон по окнам (и с памятью кругов), что над 64-байтными
/// строками и сплошной; при искусственно малом горизонте развёртки, когда пересчитывается почти каждый
/// круг, — тоже тот же (условие Судьи 2).
#[test]
fn compact_rows_drive_the_same_rounds_even_with_a_tiny_horizon() {
    let (feed, plan) = windowed_fixture();
    let compact: Vec<CompactEvent> = feed.iter().map(compact_of).collect();
    let signal = |t0_ns: i64| BounceSignal {
        touch_index: 0,
        t0_ns,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
        qty: None,
    };
    let signals = [signal(S), signal(3 * S), signal(61 * S)];
    let lat = ExecLatency::uniform(1_000_000);
    let mut hbt = build_backtest(&feed, 1.0, 1.0, lat, QueueModelKind::RiskAdverse);
    let full = drive_bounce(&mut hbt, 0, &signals, &drive_cfg()).unwrap();
    let t0s = [S, 3 * S, 61 * S];
    let windows = SignalWindows::build(&feed, &t0s, 1.0, 1.0);
    let windows_c = SignalWindows::build(&compact, &t0s, 1.0, 1.0);
    assert_eq!(windows_c.len(), windows.len());
    let wide =
        drive_bounce_windowed(&compact, &windows_c, &signals, &drive_cfg(), lat, None).unwrap();
    assert_eq!(
        wide, full,
        "компактные строки — тот же прогон, что сплошной"
    );
    let before = HORIZON_RETRIES.load(std::sync::atomic::Ordering::Relaxed);
    HORIZON_SPAN_OVERRIDE_NS.with(|c| c.set(Some(1)));
    let tiny = drive_bounce_windowed(&compact, &windows_c, &signals, &drive_cfg(), lat, None);
    let mut memo = RoundMemo::default();
    let tiny_memo = drive_bounce_windowed(
        &compact,
        &windows_c,
        &signals,
        &drive_cfg(),
        lat,
        Some(&mut memo),
    );
    HORIZON_SPAN_OVERRIDE_NS.with(|c| c.set(None));
    assert_eq!(tiny.unwrap(), full, "малый горизонт — те же круги");
    assert_eq!(
        tiny_memo.unwrap(),
        full,
        "малый горизонт с памятью кругов — те же круги"
    );
    assert!(
        HORIZON_RETRIES.load(std::sync::atomic::Ordering::Relaxed) > before,
        "малый горизонт обязан вызвать пересчёты"
    );
}

/// Э-04б (T-38): пропуск пустых шагов удержания (`DriveConfig::hold_skip`) даёт тот же `BounceRun`, что
/// пошаговый опрос 10 мс: выход по дедлайну без событий, досрочный выход («прилипание»), трейл на
/// редких событиях, событие ровно на точке сетки, конец данных посреди удержания; с пропуском «занято»
/// и без. События — не на точках сетки (+3 мс, +7,5 мс), чтобы проверить выравнивание.
#[test]
fn hold_skip_matches_polling_byte_for_byte() {
    let ms = 1_000_000;
    let (_, base) = windowed_fixture();
    let with = |deadline_ns: i64, early_exit_ns: i64, trail: (f64, f64)| match base {
        TradePlan::Bounce { .. } => {
            let mut p = base;
            if let TradePlan::Bounce {
                deadline_ns: d,
                early_exit_ns: e,
                trail_bps,
                trail_activate_bps,
                grid_legs,
                ..
            } = &mut p
            {
                *d = deadline_ns;
                *e = early_exit_ns;
                *trail_bps = trail.0;
                *trail_activate_bps = trail.1;
                *grid_legs = 1;
            }
            p
        }
        TradePlan::SpreadHold => unreachable!(),
    };
    // Вход по 101 агрессором на 2 с; дальше книга редкая.
    let head = |tail: &[Event]| {
        let mut f = vec![
            depth_at(0, true, 100.0, 5.0),
            depth_at(0, false, 105.0, 5.0),
            trade_at(2 * S + 3 * ms, true, 101.0, 5.0),
        ];
        f.extend_from_slice(tail);
        f
    };
    let sparse = head(&[
        depth_at(9 * S + 7 * ms + ms / 2, false, 104.0, 5.0),
        // Ровно на точке сетки круга (старт окна — 1 с, шаг 10 мс; `local_ts` = exch + 500 нс).
        depth_at(20 * S - 500, true, 100.0, 6.0),
        depth_at(47 * S + 3 * ms, false, 103.0, 5.0),
        depth_at(300 * S, false, 104.0, 5.0),
    ]);
    let rising = head(&[
        depth_at(5 * S + 3 * ms, true, 103.0, 5.0),
        depth_at(5 * S + 3 * ms, false, 104.0, 5.0),
        depth_at(31 * S + 7 * ms, true, 102.0, 5.0),
        depth_at(400 * S, false, 104.0, 5.0),
    ]);
    // Данные кончаются посреди удержания (дедлайн 60 с, последняя строка — 40 с).
    let short = head(&[depth_at(40 * S + 3 * ms, false, 104.0, 5.0)]);
    // Стену (бид 100) снимают частями без сделок: `gone50` и съедание по книге (`eat50x80`).
    let thinning = head(&[
        depth_at(9 * S + 7 * ms + ms / 2, true, 100.0, 4.0),
        depth_at(13 * S + 3 * ms, false, 104.0, 5.0),
        depth_at(21 * S + 3 * ms + 700, true, 100.0, 2.0),
        depth_at(33 * S + 1, true, 100.0, 0.5),
        depth_at(300 * S, false, 104.0, 5.0),
    ]);
    let gone_at_fill = vec![
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        trade_at(2 * S + 3 * ms, true, 101.0, 5.0),
        depth_at(2 * S + 3 * ms, true, 100.0, 1.0),
        depth_at(300 * S, false, 104.0, 5.0),
    ];
    let gone = |mut p: TradePlan, pct: f64| {
        if let TradePlan::Bounce {
            exit_gone_pct,
            level_qty,
            ..
        } = &mut p
        {
            *exit_gone_pct = pct;
            *level_qty = 5.0;
        }
        p
    };
    let cases = [
        (
            "дедлайн без событий",
            sparse.clone(),
            with(60 * S, 0, (0.0, 0.0)),
        ),
        (
            "прилипание",
            sparse.clone(),
            with(60 * S, 5 * S, (0.0, 0.0)),
        ),
        ("трейл", rising, with(120 * S, 0, (50.0, 100.0))),
        ("конец данных", short, with(60 * S, 0, (0.0, 0.0))),
        (
            "стену сняли",
            thinning.clone(),
            gone(with(120 * S, 0, (0.0, 0.0)), 50.0),
        ),
        // Стену снимают тем же шагом, что исполняется вход: первое решение удержания — на следующем шаге,
        // без событий (гейт 27.09, 03.08 DOGEUSDT `gone50`).
        (
            "стена снята на входе",
            gone_at_fill,
            gone(with(120 * S, 0, (0.0, 0.0)), 50.0),
        ),
    ];
    let lat = ExecLatency::uniform(1_000_000);
    let mut fast_used = 0u64;
    for (name, feed, plan) in cases {
        let signal = |t0_ns: i64| BounceSignal {
            touch_index: 0,
            t0_ns,
            sigma: SIGMA_LONG,
            plan,
            profile: 0,
            qty: None,
        };
        let signals = [signal(S), signal(3 * S)];
        let windows = SignalWindows::build(&feed, &[S, 3 * S], 1.0, 1.0);
        for busy_skip in [true, false] {
            let poll = DriveConfig {
                busy_skip,
                ..drive_cfg()
            };
            let skip = DriveConfig {
                hold_skip: true,
                ..poll
            };
            let a = drive_bounce_windowed(&feed, &windows, &signals, &poll, lat, None).unwrap();
            let before = HOLD_SKIPS.load(std::sync::atomic::Ordering::Relaxed);
            let b = drive_bounce_windowed(&feed, &windows, &signals, &skip, lat, None).unwrap();
            // «Стена снята на входе» выходит первым же решением удержания — пропускать нечего.
            assert!(
                name == "стена снята на входе"
                    || HOLD_SKIPS.load(std::sync::atomic::Ordering::Relaxed) > before,
                "{name}: пропуск обязан сработать"
            );
            assert_eq!(a, b, "{name}, busy_skip {busy_skip}");
            // TK-049: быстрый путь удержания (ALPHA_FAST_HOLD) — тот же `BounceRun`, что и без него.
            let used = fast_hold::FAST_ROUNDS.load(std::sync::atomic::Ordering::Relaxed)
                + fast_hold::FAST_FALLBACKS.load(std::sync::atomic::Ordering::Relaxed);
            fast_hold::FORCE_ON.with(|f| f.set(true));
            let c = drive_bounce_windowed(&feed, &windows, &signals, &skip, lat, None);
            fast_hold::FORCE_ON.with(|f| f.set(false));
            assert_eq!(a, c.unwrap(), "{name}, busy_skip {busy_skip}: быстрый путь");
            fast_used += fast_hold::FAST_ROUNDS.load(std::sync::atomic::Ordering::Relaxed)
                + fast_hold::FAST_FALLBACKS.load(std::sync::atomic::Ordering::Relaxed)
                - used;
            if name != "конец данных" {
                assert!(
                    !a.fills.is_empty(),
                    "{name}: круг обязан исполниться — {a:?}"
                );
            }
        }
    }
    assert!(fast_used > 0, "быстрый путь обязан хотя бы раз сработать");
}
