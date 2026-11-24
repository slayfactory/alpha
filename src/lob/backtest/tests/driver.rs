//! Сценарии драйвера `drive_bounce` на синтетическом фиде: круг мейкером,
//! пропуск по таймауту и по занятости, общий буфер; сторона по σ и счётчик
//! пропусков.

use super::queue_models::f3_plan;
use super::*;

/// Сторона по σ (Decision 14): бид — шорт, аск — лонг; σ не из пары не
/// торгуется.
#[test]
fn side_follows_sigma() {
    assert_eq!(entry_side(SIGMA_SHORT), Some(HbtSide::Sell));
    assert_eq!(entry_side(SIGMA_LONG), Some(HbtSide::Buy));
    assert_eq!(entry_side(0), None);
}

/// Обязательная колонка пропусков: причины считаются раздельно.
#[test]
fn miss_ledger_counts_reasons_separately() {
    let mut ledger = MissLedger::default();
    ledger.record(MissReason::EntryTimeout);
    ledger.record(MissReason::EntryTimeout);
    ledger.record(MissReason::PositionBusy);
    assert_eq!(ledger.timeout, 2);
    assert_eq!(ledger.busy, 1);
    assert_eq!(ledger.total(), 3);
    assert_eq!(MissLedger::default().total(), 0);
}

fn long_signal(t0_ns: i64) -> BounceSignal {
    BounceSignal {
        touch_index: 0,
        t0_ns,
        sigma: SIGMA_LONG,
        plan: f3_plan(100.0, 101.0, 1, 1.0),
        profile: 0,
        qty: None,
    }
}

/// Бид 100 / аск 101; две продажи по 100 съедают очередь впереди (5) и
/// исполняют наш вход; затем бид уходит на 101 — тейк; хвост — чтобы ответ
/// на выход успел дойти.
fn round_trip_feed() -> [Event; 7] {
    [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        trade_at(S + S / 2, true, 100.0, 3.0),
        trade_at(S + 4 * S / 5, true, 100.0, 3.0),
        // Книга обязана держать спред: залоченная (бид ≥ аск) глубина
        // крейта прячет пересечённую сторону, и выхода не будет.
        depth_at(10 * S + 9 * S / 10, true, 101.0, 5.0),
        depth_at(10 * S + 9 * S / 10, false, 102.0, 5.0),
        depth_at(30 * S, false, 103.0, 5.0),
    ]
}

/// Лонг по σ=+1: вход мейкером на бид 100, сделки съедают очередь за 2 с,
/// книга уходит вверх с живым спредом, выход по тейку 101. Итог: один круг,
/// пропусков нет, позиция плоская, чистый результат — по формуле ног.
#[test]
fn driver_closes_a_maker_round_trip_on_synthetic_feed() {
    let feed = round_trip_feed();
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let run = drive_bounce(&mut hbt, 0, &[long_signal(S)], &drive_cfg()).unwrap();
    assert!(!run.incomplete, "фид длиннее выхода с запасом");
    assert_eq!(run.signals, 1);
    assert_eq!(run.fills.len(), 1, "вход исполнился за 2 с");
    assert_eq!(run.misses, MissLedger::default());
    let fill = run.fills[0];
    assert_eq!(fill.dir, 1);
    assert!(close(fill.entry_px, 100.0), "{}", fill.entry_px);
    assert!(close(fill.exit_px, 101.0), "{}", fill.exit_px);
    // В-63: вход отстоял в книге — мейкер по флагу крейта; комиссии по ногам.
    assert!(!fill.entry_taker, "вход лимитом обязан быть мейкерским");
    assert!(close(
        roundtrip_net_bps(&fill).unwrap(),
        100.0 - leg_fee_bps(fill.entry_taker) - leg_fee_bps(fill.exit_taker)
    ));
    assert_eq!(hbt.position(0), 0.0, "позиция плоская после выхода");
}

/// `with_backtest_over` (сетка форм без копии событий) даёт тот же круг, что
/// копирующий `build_backtest`, и несколько потоков могут гнать свои
/// `Backtest` над одним `&[Event]` одновременно — буфер только читается.
#[test]
fn shared_buffer_backtest_matches_copying_one_and_survives_threads() {
    let feed = round_trip_feed();
    let signals = [long_signal(S)];
    let mut copied = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let want = drive_bounce(&mut copied, 0, &signals, &drive_cfg()).unwrap();
    assert_eq!(want.fills.len(), 1, "эталон — один круг");
    let got = with_backtest_over(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
        |bt| drive_bounce(bt, 0, &signals, &drive_cfg()).unwrap(),
    );
    assert_eq!(got.fills, want.fills, "общий буфер — те же сделки");
    assert_eq!(got.misses, want.misses);

    let feed_ref: &[Event] = &feed;
    let per_thread: Vec<_> = std::thread::scope(|scope| {
        let handles: Vec<_> = (0..4)
            .map(|_| {
                scope.spawn(move || {
                    with_backtest_over(
                        feed_ref,
                        1.0,
                        0.1,
                        ExecLatency::uniform(1_000_000),
                        QueueModelKind::RiskAdverse,
                        |bt| {
                            drive_bounce(bt, 0, &[long_signal(S)], &drive_cfg())
                                .unwrap()
                                .fills
                        },
                    )
                })
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });
    for fills in per_thread {
        assert_eq!(
            fills, want.fills,
            "потоки над одним буфером не мешают друг другу"
        );
    }
    assert_eq!(feed[2].px, 100.0, "буфер после прогонов не тронут");
}

/// Без сделок очередь не двигается: вход снимается по сроку жизни и
/// считается пропуском именно по таймауту, а не по занятости.
#[test]
fn driver_counts_an_unfilled_entry_as_timeout_miss() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(40 * S, false, 102.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let run = drive_bounce(&mut hbt, 0, &[long_signal(S)], &drive_cfg()).unwrap();
    assert!(!run.incomplete);
    assert_eq!(run.fills.len(), 0);
    assert_eq!(run.misses.timeout, 1);
    assert_eq!(run.misses.busy, 0);
    assert_eq!(hbt.position(0), 0.0);
}

/// Сигнал при открытой позиции — пропуск по занятости, а не второй вход:
/// одна позиция за раз, в очередь не ставится.
#[test]
fn driver_counts_a_signal_inside_position_as_busy_miss() {
    let feed = round_trip_feed();
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let run = drive_bounce(
        &mut hbt,
        0,
        &[long_signal(S), long_signal(5 * S)],
        &drive_cfg(),
    )
    .unwrap();
    assert!(!run.incomplete);
    assert_eq!(run.fills.len(), 1, "второй вход не ставился");
    assert_eq!(run.misses.timeout, 0);
    assert_eq!(run.misses.busy, 1);
    assert_eq!(hbt.position(0), 0.0);
}
