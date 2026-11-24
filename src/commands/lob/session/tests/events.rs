//! События сессии: закрытие сокета, тишина, переподключения, отказ подписки.

use super::*;

/// Таск 28. Разрыв мультиплексированного сокета доходит до каждого его
/// инструмента: строк `gaps.csv` — по инструменту, а `reconnects` — один
/// на сокет (копии приходят `DisconnectedSameSocket`). Кадр с
/// неразрешённым топиком считается отдельно (`unrouted`) и строки
/// `gaps.csv` не получает — у неё колонка `symbol` обязательна, а чей
/// это кадр, неизвестно.
#[test]
fn socket_close_gives_a_row_per_instrument_one_reconnect_and_unrouted_is_counted_apart() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let gap = |symbol, kind: FeedGapKind| Event::Gap {
        symbol,
        local_ts_ns: NOON_NS,
        kind,
        // Разрыв сокета и неразрешённый кадр потоку не принадлежат: сокет
        // роняет оба потока сразу (T45).
        depth: None,
        silence_ns: None,
        first_of_episode: false,
        detail: if kind == FeedGapKind::Unrouted {
            crate::feed::GapDetail::Unrouted
        } else {
            crate::feed::GapDetail::Disconnected
        },
    };
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(gap(0, FeedGapKind::Disconnected)),
        Step::Ev(gap(0, FeedGapKind::DisconnectedSameSocket)),
        Step::Ev(gap(0, FeedGapKind::Unrouted)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 20_000_000_000,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();
    assert_eq!(
        (summary.reconnects, summary.gaps, summary.unrouted),
        (1, 2, 1),
        "один разрыв сокета, две строки gaps.csv, один неразрешённый кадр"
    );
    let gaps = std::fs::read_to_string(gaps_csv_path(&root)).unwrap();
    assert_eq!(
        gaps.lines().count(),
        3,
        "заголовок и две строки разрыва, неразрешённого кадра там нет: {gaps}"
    );
}

/// MAJOR независимой проверки 7af2c0b: `bybit::conn` шлёт `MarketSilence` на
/// каждом тике эпизода, не только на первом — иначе `silence_max_ns` застыл
/// бы на значении первого срабатывания. Счётчик эпизодов растёт только на
/// `first_of_episode: true`, максимум — на каждом событии, включая эпизод,
/// не кончившийся к моменту, когда пришёл тик остановки сессии (реальное
/// поведение: `bybit::conn` продолжал бы слать обновления, здесь сценарий
/// просто кончается раньше). Строки `gaps.csv` тишина не даёт вовсе — как у
/// `unrouted` в тесте выше.
#[test]
fn market_silence_counts_episodes_once_and_tracks_the_true_max() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let silence = |local_ts_ns, silence_ns, first_of_episode| Event::Gap {
        symbol: 0,
        local_ts_ns,
        kind: FeedGapKind::MarketSilence,
        depth: None,
        silence_ns: Some(silence_ns),
        first_of_episode,
        detail: crate::feed::GapDetail::MarketSilence { silence_ns },
    };
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        // Эпизод 1: старт + два тихих обновления, максимум растёт до 90.
        Step::Ev(silence(NOON_NS, 40, true)),
        Step::Ev(silence(NOON_NS + 10, 60, false)),
        Step::Ev(silence(NOON_NS + 20, 90, false)),
        // Эпизод 2 (рынок вернулся между ними, здесь это не моделируется
        // отдельным Book/Trade — `bybit::conn` сам не пришлёт новый
        // `first_of_episode: true`, пока не увидит рынок): свой старт с
        // меньшей тишиной, чем максимум уже виденного — общий максимум
        // обязан остаться от эпизода 1, а не откатиться.
        Step::Ev(silence(NOON_NS + 30, 40, true)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 20_000_000_000,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();
    assert_eq!(
        (
            summary.silence_episodes,
            summary.silence_max_ns,
            summary.gaps
        ),
        (2, Some(90), 0),
        "два эпизода (по first_of_episode), максимум — фактическая длина, а не первое \
         срабатывание; тишина не даёт строк gaps.csv"
    );
}

/// Переподключение и ресинк — числом в `session.json` и строкой в
/// `gaps.csv` каждый: без них сутки записи нечем оценить. T45: разрывы
/// считаются и **раздельно по потокам** — разрыв `.200` не разрыв `.50`.
#[test]
fn reconnects_and_resyncs_are_counted_and_logged() {
    use crate::bybit::conn::{ORDERBOOK_DEEP_DEPTH, ORDERBOOK_DEPTH};
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let gap = |kind, depth, detail: crate::feed::GapDetail| Event::Gap {
        symbol: 0,
        local_ts_ns: NOON_NS,
        kind,
        depth,
        silence_ns: None,
        first_of_episode: false,
        detail,
    };
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(gap(
            FeedGapKind::Disconnected,
            None,
            crate::feed::GapDetail::Disconnected,
        )),
        Step::Ev(gap(
            FeedGapKind::SequenceGap,
            Some(ORDERBOOK_DEPTH),
            crate::feed::GapDetail::SequenceGap {
                depth: ORDERBOOK_DEPTH,
                expected: 1,
                got: 2,
            },
        )),
        Step::Ev(gap(
            FeedGapKind::BookInvariant,
            Some(ORDERBOOK_DEEP_DEPTH),
            crate::feed::GapDetail::BookInvariant {
                depth: ORDERBOOK_DEEP_DEPTH,
                err: crate::book::ApplyError::SequenceGap {
                    expected: 1,
                    got: 2,
                },
            },
        )),
        Step::Ev(gap(
            FeedGapKind::ParseFailed,
            None,
            crate::feed::GapDetail::ParseFailed(crate::bybit::ws::ParseError::NotJson),
        )),
        Step::Ev(gap(
            FeedGapKind::ConnectFailed,
            None,
            crate::feed::GapDetail::ConnectFailed {
                attempt: 1,
                http_status: Some(403),
                err: "Forbidden".to_string(),
            },
        )),
    ]));
    run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();
    let summary = ctx.write_session_json(true, &SystemClock).unwrap();
    assert_eq!(summary.reconnects, 1);
    assert_eq!(summary.resyncs, 2);
    assert_eq!(summary.gaps, 5);
    // K1: отказ `connect()` — свой счётчик, а не «молчаливый ретрай»:
    // сокет не открылся, `reconnects` этого не видит.
    assert_eq!(summary.connect_failed, 1); // Раздельные счётчики потоков: по одному ресинку на каждый поток.
    let by_depth = |depth: u32| {
        summary
            .streams
            .iter()
            .find(|s| s.depth == depth)
            .unwrap_or_else(|| panic!("в session.json нет потока .{depth}"))
    };
    assert_eq!(by_depth(ORDERBOOK_DEPTH).resyncs, 1);
    assert_eq!(by_depth(ORDERBOOK_DEEP_DEPTH).resyncs, 1);
    let rows = crate::commands::record::read_gap_rows(&gaps_csv_path(&root)).unwrap();
    assert_eq!(rows.len(), 5, "строка на каждый разрыв");
    assert_eq!(rows[0].kind, GapKind::SequenceGap);
    assert_eq!(rows[1].kind, GapKind::SequenceGap);
    assert_eq!(rows[2].kind, GapKind::BookInvariant);
    assert_eq!(rows[3].kind, GapKind::ParseError);
    assert_eq!(
        rows[4].kind,
        GapKind::ConnectFailed,
        "отказ рукопожатия обязан быть строкой, а не тишиной"
    );
    assert!(rows[4].detail.contains("403"), "{}", rows[4].detail);
    // Строка разрыва несёт поток: по ней видно, чей `u` разошёлся.
    assert!(
        rows[1].detail.contains(&format!(".{ORDERBOOK_DEPTH}")),
        "деталь строки обязана назвать поток: {}",
        rows[1].detail
    );
}

/// A8.3 (2026-09-18): отказ подписки биржи — свой счётчик
/// (`session.json.subscribe_failed`) и своя строка `gaps.csv` класса
/// `subscribe_failed`, а книга инструмента перестаёт быть доверенной
/// (снапшота по нему не будет). До правки ответ `{"success":false}` читался
/// как служебное сообщение: инструмент молча стоял без данных, и в журнале не
/// было ни строки.
#[test]
fn a_refused_subscription_is_counted_and_written_to_the_journal() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
        Step::Ev(Event::Gap {
            symbol: 0,
            local_ts_ns: NOON_NS + 1,
            kind: FeedGapKind::SubscribeFailed,
            depth: None,
            silence_ns: None,
            first_of_episode: false,
            detail: crate::feed::GapDetail::SubscribeFailed {
                topic: Some("orderbook.50.SYM".to_string()),
                ret_msg: "error:handler not found".to_string(),
            },
        }),
    ]));
    run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();
    let summary = ctx.write_session_json(true, &SystemClock).unwrap();

    assert_eq!(summary.subscribe_failed, 1, "счётчик отказа подписки");
    assert_eq!(summary.connect_failed, 0, "это не отказ рукопожатия");
    assert_eq!(summary.gaps, 1);
    assert!(
        !ctx.states[0].streams[FAST_STREAM].synced,
        "книга инструмента без снапшота недоверена"
    );
    let rows = crate::commands::record::read_gap_rows(&gaps_csv_path(&root)).unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].kind, GapKind::SubscribeFailed);
    assert_eq!(rows[0].symbol, "SYM", "строка называет инструмент");
    assert!(
        rows[0].detail.contains("handler not found"),
        "деталь несёт текст биржи: {}",
        rows[0].detail
    );
}

/// `--always-on` взаимоисключающий с `--minutes`/`--pilot-minutes` на
/// самом парсере; `resolve_duration` даёт `AlwaysOn` без дедлайна.
#[test]
fn always_on_conflicts_with_timed_flags_and_resolves_without_deadline() {
    #[derive(Debug, clap::Parser)]
    struct TestCli {
        #[command(subcommand)]
        cmd: super::super::super::LobCommand,
    }
    use clap::Parser as _;
    for other in [["--minutes", "10"], ["--pilot-minutes", "30"]] {
        let err = TestCli::try_parse_from([
            "t",
            "session",
            "--pool-instruments",
            "instruments.csv",
            "--root",
            "root",
            "--always-on",
            other[0],
            other[1],
        ])
        .expect_err("--always-on с таймированным флагом обязан конфликтовать");
        assert_eq!(
            err.kind(),
            clap::error::ErrorKind::ArgumentConflict,
            "{err}"
        );
    }
    let args = SessionArgs {
        pool_instruments: Some(PathBuf::from("does/not/exist/instruments.csv")),
        all_instruments: false,
        root: PathBuf::from("does/not/exist/root"),
        minutes: None,
        pilot_minutes: None,
        always_on: true,
        base_url: BYBIT_MAINNET_URL.to_string(),
        ntp_addr: "pool.ntp.org:123".to_string(),
    };
    assert_eq!(resolve_duration(&args).unwrap(), SessionPlan::AlwaysOn);
}
