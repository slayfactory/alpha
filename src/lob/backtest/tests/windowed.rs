//! Прогон по окнам и сплошной драйвер: эквивалентность, снимки глубины.

use super::*;

/// Синтетические сутки гейта «окна = сплошной» и план круга — общие для тестов драйвера по окнам.
pub(super) fn windowed_fixture() -> (Vec<Event>, TradePlan) {
    let feed = vec![
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        // Агрессор-продавец по 101 закрывает все четыре ноги входа.
        trade_at(2 * S, true, 101.0, 5.0),
        depth_at(3 * S, true, 103.0, 5.0),
        trade_at(4 * S, false, 103.0, 5.0),
        depth_at(30 * S, true, 103.0, 5.0),
        depth_at(30 * S, false, 104.0, 5.0),
        // Второй круг: книга вернулась, тот же вход и тот же тейк.
        depth_at(60 * S, true, 100.0, 5.0),
        depth_at(60 * S, false, 105.0, 5.0),
        trade_at(62 * S, true, 101.0, 5.0),
        depth_at(63 * S, true, 103.0, 5.0),
        trade_at(64 * S, false, 103.0, 5.0),
        depth_at(90 * S, true, 103.0, 5.0),
        depth_at(90 * S, false, 104.0, 5.0),
        depth_at(200 * S, false, 104.0, 5.0),
    ];
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 99.0,
        take_px: 103.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 4,
        grid_step_px: 0.0,
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
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    };
    (feed, plan)
}

/// Прогон по сетапам (`drive_bounce_windowed`) даёт тот же `BounceRun`, что
/// сплошной `drive_bounce`: круги, занятые сигналы, время выхода — всё поле в
/// поле. Второй сигнал приходит внутри первого круга — «занято» у обоих.
/// Лестница F4 стоит **без шага** (`grid_step_px = 0`): ноги по одной цене
/// набираются целиком первым же агрессором, средняя равна плановой, и числа
/// стопа/тейка остаются прежними — тест про драйвер, не про геометрию.
#[test]
fn windowed_driver_matches_the_continuous_one_on_a_synthetic_day() {
    let (feed, plan) = windowed_fixture();
    let signal = |t0_ns: i64| BounceSignal {
        touch_index: 0,
        t0_ns,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
        qty: None,
    };
    let signals = [signal(S), signal(3 * S), signal(61 * S)];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let full = drive_bounce(&mut hbt, 0, &signals, &drive_cfg()).unwrap();
    let windows = SignalWindows::build(&feed, &[S, 3 * S, 61 * S], 1.0, 1.0);
    assert_eq!(windows.len(), 3);
    let win = drive_bounce_windowed(
        &feed,
        &windows,
        &signals,
        &drive_cfg(),
        ExecLatency::uniform(1_000_000),
        None,
    )
    .unwrap();
    assert_eq!(win, full, "окна обязаны дать тот же прогон, что сплошной");
    assert_eq!(full.fills.len(), 2, "{full:?}");
    assert_eq!(full.misses.busy, 1, "второй сигнал пришёл внутри круга");
    assert_eq!(full.fill_exit_ns.len(), 2);
    assert!(full.fill_exit_ns[0] < full.fill_exit_ns[1]);

    // G10: память кругов. «Набор A» (все три сигнала) заполняет память, «набор B» (без первого
    // сигнала — у него другая занятость и другая нумерация заявок) берёт круги из неё; итог B
    // обязан совпасть с его же прогоном без памяти, а память — сработать.
    let mut memo = RoundMemo::default();
    let a = drive_bounce_windowed(
        &feed,
        &windows,
        &signals,
        &drive_cfg(),
        ExecLatency::uniform(1_000_000),
        Some(&mut memo),
    )
    .unwrap();
    assert_eq!(a, win, "с пустой памятью прогон тот же");
    let subset = [signal(3 * S), signal(61 * S)];
    let plain_b = drive_bounce_windowed(
        &feed,
        &windows,
        &subset,
        &drive_cfg(),
        ExecLatency::uniform(1_000_000),
        None,
    )
    .unwrap();
    let memo_b = drive_bounce_windowed(
        &feed,
        &windows,
        &subset,
        &drive_cfg(),
        ExecLatency::uniform(1_000_000),
        Some(&mut memo),
    )
    .unwrap();
    assert_eq!(
        memo_b, plain_b,
        "круги из памяти обязаны дать тот же прогон"
    );
    let (hits, _) = memo.stats();
    assert!(
        hits >= 1,
        "второй набор обязан взять хотя бы круг 61 с из памяти: {hits}"
    );
}

/// T-31: без пропуска «занято» сигнал внутри круга получает свой круг, а след шагов позволяет вне
/// движка повторить правило занятости: «занят при `t0 < idle_ns` прошлого принятого» над следом `off`
/// даёт ровно круги прогона `on` — те же исполнения и выходы.
#[test]
fn busy_skip_off_runs_every_signal_and_its_trace_replays_the_busy_run() {
    let (feed, plan) = windowed_fixture();
    let signal = |t0_ns: i64| BounceSignal {
        touch_index: 0,
        t0_ns,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
        qty: None,
    };
    let signals = [signal(S), signal(3 * S), signal(61 * S)];
    let windows = SignalWindows::build(&feed, &[S, 3 * S, 61 * S], 1.0, 1.0);
    let lat = ExecLatency::uniform(1_000_000);
    let on = drive_bounce_windowed(&feed, &windows, &signals, &drive_cfg(), lat, None).unwrap();
    assert!(on.trace.is_empty(), "при `on` след не пишется");
    let off_cfg = DriveConfig {
        busy_skip: false,
        hold_skip: false,
        ..drive_cfg()
    };
    let off = drive_bounce_windowed(&feed, &windows, &signals, &off_cfg, lat, None).unwrap();
    assert_eq!(off.misses.busy, 0, "{off:?}");
    assert!(off.busy_signal.is_empty());
    assert_eq!(off.trace.len(), 3, "по шагу на сигнал: {:?}", off.trace);
    assert_eq!(
        off.trace.iter().map(|t| t.signal).collect::<Vec<_>>(),
        [0, 1, 2]
    );
    assert_eq!(on.busy_signal, [1], "при `on` второй сигнал — «занято»");
    assert!(
        off.submitted_signal.contains(&1),
        "при `off` второй сигнал — свой круг (вход отправлен): {off:?}"
    );

    // правило движка над следом: занят при t0 < idle прошлого принятого; обрыв — конец суток
    let mut idle = i64::MIN;
    let mut kept: Vec<usize> = Vec::new();
    for t in &off.trace {
        if signals[t.signal].t0_ns < idle {
            continue;
        }
        kept.push(t.signal);
        if matches!(t.step, TraceStep::EndOfData | TraceStep::NoWindow) || t.residual == Some(true)
        {
            break;
        }
        idle = t.idle_ns;
    }
    let replay: Vec<(usize, Fill, i64)> = off
        .fill_signal
        .iter()
        .enumerate()
        .filter(|(_, s)| kept.contains(s))
        .map(|(i, &s)| (s, off.fills[i], off.fill_exit_ns[i]))
        .collect();
    let base: Vec<(usize, Fill, i64)> = on
        .fill_signal
        .iter()
        .enumerate()
        .map(|(i, &s)| (s, on.fills[i], on.fill_exit_ns[i]))
        .collect();
    assert_eq!(replay, base, "след `off` обязан воспроизвести круги `on`");

    // Условие Судьи 5: фильтр, отбросивший первый сигнал, освобождает монету — второй (при `on` «занято»)
    // получает свой круг; ровно то же даёт движок, которому подали сигналы уже без первого.
    let mut idle = i64::MIN;
    let mut kept_f: Vec<usize> = Vec::new();
    for t in off.trace.iter().filter(|t| t.signal != 0) {
        if signals[t.signal].t0_ns < idle {
            continue;
        }
        kept_f.push(t.signal);
        idle = t.idle_ns;
    }
    assert!(
        kept_f.contains(&1),
        "без первого сигнала второй принят: {kept_f:?}"
    );
    let engine =
        drive_bounce_windowed(&feed, &windows, &signals[1..], &drive_cfg(), lat, None).unwrap();
    assert_eq!(
        engine.submitted_signal.len(),
        kept_f.len(),
        "движок без первого сигнала отправил столько же входов"
    );
    let engine_exits: Vec<i64> = engine.fill_exit_ns.clone();
    let replay_exits: Vec<i64> = off
        .fill_signal
        .iter()
        .enumerate()
        .filter(|(_, s)| kept_f.contains(s))
        .map(|(i, _)| off.fill_exit_ns[i])
        .collect();
    assert_eq!(
        replay_exits, engine_exits,
        "те же круги, что у движка с фильтром"
    );
}

/// Снимок книги воспроизводит `HashMapMarketDepth` крейта поле в поле,
/// включая перекрещённые «спрятанные» уровни и границы поиска лучшей цены.
#[test]
fn depth_snapshot_rebuilds_the_crate_book_field_by_field() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(S, true, 99.0, 2.0),
        depth_at(S, false, 103.0, 1.0),
        // Бид залезает на аск: крейт прячет аск 101, лучший аск уходит на 103.
        depth_at(2 * S, true, 101.0, 4.0),
        depth_at(3 * S, true, 100.0, 0.0),
    ];
    let windows = SignalWindows::build(&feed, &[10 * S], 1.0, 1.0);
    let w = windows.window_at(10 * S).unwrap();
    assert_eq!(w.start, feed.len(), "все строки до t0 — в снимке");
    let mut want = FastMarketDepth::new(1.0, 1.0);
    for ev in &feed {
        if ev.is(LOCAL_BID_DEPTH_EVENT) {
            want.update_bid_depth(ev.px, ev.qty, ev.local_ts);
        } else {
            want.update_ask_depth(ev.px, ev.qty, ev.local_ts);
        }
    }
    let got = w.depth.build(1.0, 1.0);
    assert_eq!(got.bid_depth, want.bid_depth);
    assert_eq!(got.ask_depth, want.ask_depth);
    assert_eq!(got.best_bid_tick, want.best_bid_tick);
    assert_eq!(got.best_ask_tick, want.best_ask_tick);
    assert_eq!(got.low_bid_tick, want.low_bid_tick);
    assert_eq!(got.high_ask_tick, want.high_ask_tick);
    assert_eq!(got.best_ask(), 103.0, "спрятанный аск 101 не всплыл");
    assert!(windows.window_at(5 * S).is_none());
    // Строка с биржевой меткой за t0 в снимок не входит, даже если локальная
    // метка уже прошла: биржевая сторона доберёт её из среза сама.
    let late = [
        depth_at(0, true, 100.0, 5.0),
        Event {
            exch_ts: 5 * S,
            local_ts: S,
            ..depth_at(0, true, 101.0, 1.0)
        },
        depth_at(2 * S, true, 102.0, 1.0),
    ];
    let w = SignalWindows::build(&late, &[3 * S], 1.0, 1.0);
    let w = w.window_at(3 * S).unwrap();
    assert_eq!(w.start, 1, "срез начинается со строки с exch_ts > t0");
    assert_eq!(w.depth.bids, vec![(100, 5.0)]);
}

/// Микрозамер фиксированной цены окна (сборка `Backtest` + две книги из
/// снимка + якорь), без событий в срезе. Не гейт — число для решения, что
/// оптимизировать; гонять `cargo test --release -- --ignored bench_window`.
#[test]
#[ignore]
fn bench_window_fixed_cost() {
    for levels in [50usize, 200, 1000] {
        let mut feed = Vec::new();
        for i in 0..levels {
            feed.push(depth_at(0, true, 1000.0 - i as f64, 1.0));
            feed.push(depth_at(0, false, 1001.0 + i as f64, 1.0));
        }
        feed.push(depth_at(10 * S, true, 1000.0, 2.0));
        let windows = SignalWindows::build(&feed, &[5 * S], 1.0, 1.0);
        let w = windows.window_at(5 * S).unwrap();
        let n = 20_000;
        let t = std::time::Instant::now();
        let mut acc = 0i64;
        for _ in 0..n {
            acc += with_backtest_over_window(
                &w.depth,
                5 * S,
                &feed[w.start..],
                1.0,
                1.0,
                ExecLatency::uniform(1_000_000),
                QueueModelKind::RiskAdverse,
                |bt| {
                    bt.elapse(0).unwrap();
                    bt.current_timestamp()
                },
            );
        }
        let per = t.elapsed().as_secs_f64() * 1e6 / n as f64;
        eprintln!("bench_window: levels={levels} per_window={per:.1}us (acc={acc})");
    }
}

/// Микрозамер цены **круга** в окне: вход исполняется через 1 с, тейк через 3 с
/// — около 300 шагов опроса по 10 мс. Отсюда цена одного `elapse` крейта.
/// Гонять `cargo test --release -- --ignored bench_round`.
#[test]
#[ignore]
fn bench_round_cost_in_a_window() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        trade_at(2 * S, true, 104.0, 5.0),
        depth_at(3 * S, true, 103.0, 5.0),
        trade_at(4 * S, false, 103.0, 5.0),
        depth_at(30 * S, true, 103.0, 5.0),
        depth_at(30 * S, false, 104.0, 5.0),
        depth_at(200 * S, false, 104.0, 5.0),
    ];
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
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    };
    let signals = [BounceSignal {
        touch_index: 0,
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
        qty: None,
    }];
    let windows = SignalWindows::build(&feed, &[S], 1.0, 1.0);
    let n = 2_000;
    let t = std::time::Instant::now();
    let mut exit_ns = 0;
    for _ in 0..n {
        let run = drive_bounce_windowed(
            &feed,
            &windows,
            &signals,
            &drive_cfg(),
            ExecLatency::uniform(1_000_000),
            None,
        )
        .unwrap();
        exit_ns = run.fill_exit_ns.first().copied().unwrap_or(0);
    }
    let per = t.elapsed().as_secs_f64() * 1e6 / n as f64;
    let polls = (exit_ns - S) / ON_EVENT_POLL_STEP_NS;
    eprintln!(
        "bench_round: per_window={per:.1}us polls~{polls} per_poll~{:.2}us (exit at {:.3}s)",
        (per - 20.0) / polls.max(1) as f64,
        exit_ns as f64 / 1e9
    );
}

/// Микрозамер круга при плотной ленте (обновление далёкого уровня каждые 4 мс,
/// как ~250 событий/с у ZEC): вход лестницей исполняется через 1 с, тейк
/// через 3 с. Отсюда цена события внутри круга против цены шага опроса.
/// Гонять `cargo test --release -- --ignored bench_dense`.
#[test]
#[ignore]
fn bench_dense_round_in_a_window() {
    let mut feed = vec![
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
    ];
    let mut t = 4_000_000i64;
    let mut i = 0i64;
    while t < 30 * S {
        feed.push(depth_at(
            t,
            true,
            80.0 - (i % 20) as f64,
            1.0 + (i % 3) as f64,
        ));
        t += 4_000_000;
        i += 1;
    }
    feed.push(trade_at(2 * S, true, 104.0, 5.0));
    feed.push(depth_at(3 * S, true, 103.0, 5.0));
    feed.push(trade_at(4 * S, false, 103.0, 5.0));
    feed.push(depth_at(30 * S, false, 104.0, 5.0));
    feed.push(depth_at(200 * S, false, 104.0, 5.0));
    feed.sort_by_key(|e| e.local_ts);
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 99.0,
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
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    };
    let signals = [BounceSignal {
        touch_index: 0,
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
        qty: None,
    }];
    let windows = SignalWindows::build(&feed, &[S], 1.0, 1.0);
    let n = 2_000;
    let t = std::time::Instant::now();
    let mut exit_ns = 0;
    for _ in 0..n {
        let run = drive_bounce_windowed(
            &feed,
            &windows,
            &signals,
            &drive_cfg(),
            ExecLatency::uniform(1_000_000),
            None,
        )
        .unwrap();
        exit_ns = run.fill_exit_ns.first().copied().unwrap_or(0);
    }
    let per = t.elapsed().as_secs_f64() * 1e6 / n as f64;
    let polls = (exit_ns - S) / ON_EVENT_POLL_STEP_NS;
    let events = (exit_ns - S) / 4_000_000;
    eprintln!(
        "bench_dense: per_window={per:.1}us polls~{polls} events~{events} → {:.2}us per event+poll pair-ish (exit at {:.3}s)",
        (per - 20.0) / events.max(1) as f64,
        exit_ns as f64 / 1e9
    );
}
