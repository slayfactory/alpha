//! Олвейс-он: ядро над сценарным `Feed`, ротация, глубокий поток.

use super::*;

// -----------------------------------------------------------------
// Таск 25: олвейс-он — ядро над сценарным `Feed` (A5: вызывающий не
// различает источник), без сети.
// -----------------------------------------------------------------

pub(super) enum Step {
    Ev(Event),
    Probe(Box<dyn FnMut()>),
}

/// Сценарный `Feed`: события по списку; `Probe` выполняется между
/// событиями — так тест смотрит на диск **во время** прогона, а не
/// после финального сброса, и отличает «кадр лёг по тику» от «кадр лёг
/// при закрытии».
pub(super) struct ScriptedFeed(pub(super) VecDeque<Step>);

impl Feed for ScriptedFeed {
    fn next_event(&mut self) -> Option<Event> {
        loop {
            match self.0.pop_front()? {
                Step::Ev(e) => return Some(e),
                Step::Probe(mut f) => f(),
            }
        }
    }
}

pub(super) fn book_event(
    symbol: u16,
    local_ts_ns: i64,
    cts_ms: i64,
    is_snapshot: bool,
    u: u64,
) -> Event {
    Event::Market {
        symbol,
        local_ts_ns,
        parse_latency_ns: None,
        payload: crate::bybit::ws::Event::Book(crate::book::Update {
            is_snapshot,
            depth: crate::bybit::conn::ORDERBOOK_DEPTH,
            u,
            seq: u,
            cts_ms,
            bids: vec![(100 * TEST_TICK_E9, 5 * TEST_STEP_E9)],
            asks: vec![(110 * TEST_TICK_E9, 7 * TEST_STEP_E9)],
        }),
    }
}

/// То же, но с явной глубиной потока: нужен тестам T45, где событие обязано
/// уехать в файл глубокого потока (`orderbook.200`).
pub(super) fn book_event_at_depth(
    symbol: u16,
    local_ts_ns: i64,
    cts_ms: i64,
    is_snapshot: bool,
    u: u64,
    depth: u32,
) -> Event {
    match book_event(symbol, local_ts_ns, cts_ms, is_snapshot, u) {
        Event::Market {
            payload: crate::bybit::ws::Event::Book(mut up),
            ..
        } => {
            up.depth = depth;
            Event::Market {
                symbol,
                local_ts_ns,
                parse_latency_ns: None,
                payload: crate::bybit::ws::Event::Book(up),
            }
        }
        other => panic!("book_event обязан вернуть книжное событие: {other:?}"),
    }
}

/// Снапшот глубокого потока в двести уровней на сторону — ровно то, что
/// присылает `orderbook.200` (документация Bybit v5), то есть 400 записей
/// одним сообщением: самое крупное сообщение сессии. Цены кратны шагу теста
/// и не пересекаются: биды 101..300 тиков, аски 1001..1200. Возвращается
/// **полезная нагрузка** события потока (`bybit::ws::Event`), как её получает
/// `write_market_event`.
pub(super) fn deep_snapshot_payload(cts_ms: i64, u: u64) -> crate::bybit::ws::Event {
    const LEVELS: i64 = 200;
    let bids: Vec<(i64, i64)> = (1..=LEVELS)
        .map(|i| ((100 + i) * TEST_TICK_E9, 5 * TEST_STEP_E9))
        .collect();
    let asks: Vec<(i64, i64)> = (1..=LEVELS)
        .map(|i| ((1000 + i) * TEST_TICK_E9, 7 * TEST_STEP_E9))
        .collect();
    crate::bybit::ws::Event::Book(crate::book::Update {
        is_snapshot: true,
        depth: crate::bybit::conn::ORDERBOOK_DEEP_DEPTH,
        u,
        seq: u,
        cts_ms,
        bids,
        asks,
    })
}

/// Дельта глубокого потока по одному уровню (только бид) — ровно одна запись
/// в кадр: так батч можно довести до самого порога `FRAME_TARGET_RECORDS`.
pub(super) fn deep_single_level_delta_payload(cts_ms: i64, u: u64) -> crate::bybit::ws::Event {
    crate::bybit::ws::Event::Book(crate::book::Update {
        is_snapshot: false,
        depth: crate::bybit::conn::ORDERBOOK_DEEP_DEPTH,
        u,
        seq: u,
        cts_ms,
        bids: vec![(101 * TEST_TICK_E9, 5 * TEST_STEP_E9)],
        asks: vec![],
    })
}

pub(super) fn frames_on_disk(path: &Path) -> Vec<Vec<Record>> {
    let data = std::fs::read(path).unwrap();
    let mut reader = crate::binlog::Reader::open(&data[..]).unwrap();
    let mut out = Vec::new();
    while let Some(frame) = reader.read_frame().unwrap() {
        out.push(frame);
    }
    out
}

pub(super) fn always_on_ctx(root: &Path, started_ns: i64) -> SessionCtx {
    SessionCtx::open(
        root,
        &[test_member("SYM")],
        SessionPlan::AlwaysOn,
        started_ns,
    )
    .unwrap()
}

/// 2026-09-12T12:00:00Z в наносекундах — сутки `TEST_DAY`.
pub(super) const NOON_NS: i64 = 1_789_214_400 * 1_000_000_000;

/// Критерий «тихий инструмент даёт кадр на диске не позже окна» и
/// «сброс — по таймеру рантайма»: снапшот, одна дельта, потом только тик
/// — проба между тиком и концом прогона обязана увидеть дельту кадром
/// на диске (2 кадра: снапшот, дельта), хотя финального сброса ещё не
/// было. До таска 25 дельта ждала часового таймера или конца сессии.
#[test]
fn silent_instrument_frame_reaches_disk_on_the_tick_not_at_shutdown() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let path = crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 1);
    let seen = std::sync::Arc::new(std::sync::Mutex::new(0usize));
    let seen_probe = seen.clone();
    let probe_path = path.clone();
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
        Step::Ev(book_event(
            0,
            NOON_NS + 1,
            NOON_NS / 1_000_000 + 1,
            false,
            2,
        )),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + window_ns,
        }),
        Step::Probe(Box::new(move || {
            *seen_probe.lock().unwrap() = frames_on_disk(&probe_path).len();
        })),
    ]));
    run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();
    assert_eq!(
        *seen.lock().unwrap(),
        2,
        "после тика на диске обязаны лежать оба кадра — снапшот и дельта"
    );
}

/// Критерий «ноль событий → session.json на диске не позже окна» плюс
/// остановка сигналом-заменителем: `None` от `Feed` (то, во что
/// `StopHandle::stop` превращает Ctrl+C) — финальный `session.json` с
/// `closed = true`, `always_on = true`; до него — периодический с
/// `closed = false`, уже читаемый.
#[test]
fn zero_events_still_writes_session_json_and_stop_closes_it() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let mid = std::sync::Arc::new(std::sync::Mutex::new(None::<SessionSummary>));
    let mid_probe = mid.clone();
    let probe_root = root.clone();
    let hour_ns = HOURLY_REFRESH_SECS as i64 * 1_000_000_000;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + hour_ns,
        }),
        Step::Probe(Box::new(move || {
            let text = std::fs::read_to_string(probe_root.join("session.json")).unwrap();
            *mid_probe.lock().unwrap() = Some(serde_json::from_str(&text).unwrap());
        })),
    ]));
    // Финальная запись — дело шва, не теста: `None` от `Feed` обязан
    // закрыть `session.json` сам.
    let final_summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();
    let mid = mid
        .lock()
        .unwrap()
        .take()
        .expect("часовой тик обязан переписать session.json");
    assert!(
        mid.always_on && !mid.closed,
        "периодическая запись — не финальная"
    );
    assert!(
        final_summary.closed,
        "после None от Feed — финальная запись"
    );
    assert_eq!(final_summary.records_total, 0);
    let on_disk: SessionSummary =
        serde_json::from_str(&std::fs::read_to_string(root.join("session.json")).unwrap()).unwrap();
    assert!(on_disk.closed && on_disk.always_on);
}

/// Ротация по суткам UTC: событие биржи следующих суток открывает новую
/// часть (`claim_part_with`, часть 1 новых суток), первым кадром —
/// синтетический снапшот книги (обе стороны), затем сама дельта;
/// `binlog_files` получает вторую часть с `started_utc` новых суток;
/// `session_binlog_for` читает обе части по порядку.
#[test]
fn always_on_rotates_at_utc_midnight_with_a_synthetic_snapshot_first() {
    use hftbacktest::types::{LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT};
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let next_day_ns = NOON_NS + 12 * 3600 * 1_000_000_000 + 1_000_000;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
        Step::Ev(book_event(
            0,
            NOON_NS + 1,
            NOON_NS / 1_000_000 + 1,
            false,
            2,
        )),
        Step::Ev(book_event(
            0,
            next_day_ns,
            next_day_ns / 1_000_000,
            false,
            3,
        )),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    let paths = super::super::super::session_binlog_for(&root, "SYM").unwrap();
    assert_eq!(paths.len(), 2, "две части: сутки D и D+1");
    assert_eq!(
        paths[1],
        crate::commands::record::day_file_path(&root, "SYM", "2026-09-13", 1)
    );
    let frames = frames_on_disk(&paths[1]);
    assert!(frames.len() >= 2, "снапшот и дельта: {}", frames.len());
    let first = &frames[0];
    assert_eq!(
        first.len(),
        2,
        "синтетический снапшот — по записи на уровень"
    );
    assert_eq!(first[0].ev, LOCAL_BID_DEPTH_SNAPSHOT_EVENT);
    assert_eq!(first[0].price_ticks, 100);
    assert_eq!(first[1].ev, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT);
    assert_eq!(first[1].price_ticks, 110);
    assert_eq!(summary.binlog_files.len(), 2);
    assert!(summary.binlog_files[1]
        .started_utc
        .starts_with("2026-09-13"));
    assert_eq!(summary.binlog_files[1].part, 1);
    let parts = super::super::super::session_parts_for(&root, "SYM").unwrap();
    assert_eq!(parts[1].day_utc, "2026-09-13");
    assert_eq!(parts[1].start_hour_utc, 0);
}

/// Ротация только вперёд: после ухода в сутки D+1 опоздавшее событие
/// суток D (сделка с `T` раньше `cts` принятой дельты) пишется в
/// текущую часть D+1 — новой части `-p2` суток D не появляется,
/// `binlog_files` не растёт, запись лежит в файле D+1 со своей меткой.
#[test]
fn late_event_of_the_previous_day_stays_in_the_current_part() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let next_day_ns = NOON_NS + 12 * 3600 * 1_000_000_000 + 1_000_000;
    let late_ms = NOON_NS / 1_000_000 + 2;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
        Step::Ev(book_event(
            0,
            next_day_ns,
            next_day_ns / 1_000_000,
            false,
            2,
        )),
        Step::Ev(book_event(0, next_day_ns + 1, late_ms, false, 3)),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();
    assert_eq!(summary.binlog_files.len(), 2, "D и D+1, без -p2 для D");
    let paths = super::super::super::session_binlog_for(&root, "SYM").unwrap();
    assert_eq!(paths.len(), 2);
    let frames = frames_on_disk(&paths[1]);
    let last = frames.last().unwrap();
    assert_eq!(
        last.last().unwrap().exch_ts_ns,
        late_ms * 1_000_000,
        "опоздавшее событие — в части D+1"
    );
    assert!(
        !crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 2).exists(),
        "части -p2 прежних суток быть не должно"
    );
}

/// T45: сброс доверия и ротация суток — **по потокам**. Разрыв `u` глубокого
/// потока оставляет быстрый поток доверенным (`synced`), поэтому его переход
/// через полночь по-прежнему открывает новую часть синтетическим снапшотом
/// своей книги; глубокий поток суток не менял — своей части D+1 у него не
/// появляется, а `-p2` не появляется ни у одного из потоков.
#[test]
fn gap_in_deep_stream_leaves_fast_synced_for_its_own_rotation() {
    use crate::bybit::conn::{ORDERBOOK_DEEP_DEPTH, ORDERBOOK_DEPTH};
    use hftbacktest::types::{LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT};
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let next_day_ns = NOON_NS + 12 * 3600 * 1_000_000_000 + 1_000_000;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event_at_depth(
            0,
            NOON_NS,
            NOON_NS / 1_000_000,
            true,
            1,
            ORDERBOOK_DEPTH,
        )),
        Step::Ev(book_event_at_depth(
            0,
            NOON_NS,
            NOON_NS / 1_000_000,
            true,
            1,
            ORDERBOOK_DEEP_DEPTH,
        )),
        // Разрыв `u` есть только у глубокого потока.
        Step::Ev(Event::Gap {
            symbol: 0,
            local_ts_ns: NOON_NS,
            kind: FeedGapKind::SequenceGap,
            depth: Some(ORDERBOOK_DEEP_DEPTH),
            silence_ns: None,
            first_of_episode: false,
            detail: crate::feed::GapDetail::SequenceGap {
                depth: ORDERBOOK_DEEP_DEPTH,
                expected: 1,
                got: 2,
            },
        }),
        // Событие следующих суток приходит только быстрым потоком.
        Step::Ev(book_event_at_depth(
            0,
            next_day_ns,
            next_day_ns / 1_000_000,
            false,
            2,
            ORDERBOOK_DEPTH,
        )),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    // Доверие сброшено по потоку, а не по инструменту.
    assert!(
        ctx.states[0].streams[FAST_STREAM].synced,
        "разрыв .200 не имеет права сбросить доверие быстрого потока"
    );
    assert!(
        !ctx.states[0].streams[DEEP_STREAM].synced,
        "у глубокого потока после разрыва доверия нет"
    );
    // Ротировался только быстрый: часть D+1 есть у него, у глубокого — нет.
    let fast_d1 = crate::commands::record::day_file_path(&root, "SYM", "2026-09-13", 1);
    let deep_d1 =
        crate::commands::record::day_file_path(&root.join(DEEP_DIR), "SYM", "2026-09-13", 1);
    assert!(
        fast_d1.is_file(),
        "новая часть быстрого потока обязана появиться"
    );
    assert!(
        !deep_d1.exists(),
        "глубокий поток суток не менял — части D+1 у него быть не должно"
    );
    assert!(
        crate::commands::record::day_file_path(&root.join(DEEP_DIR), "SYM", TEST_DAY, 1).is_file()
    );
    assert!(!crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 2).exists());
    assert!(
        !crate::commands::record::day_file_path(&root.join(DEEP_DIR), "SYM", TEST_DAY, 2).exists()
    );
    assert_eq!(ctx.states[0].streams[FAST_STREAM].part, 1);
    assert_eq!(ctx.states[0].streams[DEEP_STREAM].part, 1);
    assert_eq!(
        summary.binlog_files.len(),
        2,
        "D и D+1 — только быстрый поток"
    );

    // Новая часть быстрого потока начинается синтетическим снапшотом своей
    // книги (фикстура `book_event`: бид 100, аск 110 тиков) — то есть доверие
    // быстрого потока действительно уцелело.
    let frames = frames_on_disk(&fast_d1);
    let first = &frames[0];
    assert_eq!(first.len(), 2, "по записи на уровень");
    assert_eq!(first[0].ev, LOCAL_BID_DEPTH_SNAPSHOT_EVENT);
    assert_eq!(first[0].price_ticks, 100);
    assert_eq!(first[1].ev, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT);
    assert_eq!(first[1].price_ticks, 110);
}

/// T45: ротация глубокого потока идёт **своей** частью и своим доверием.
/// Событие следующих суток в глубоком потоке открывает его часть D+1, но
/// синтетического снапшота в ней не будет: доверие сброшено разрывом `u`, и
/// файл обязан ждать снапшота биржи — иначе сутки начинались бы с дельт и
/// были бы нечитаемы целиком (`record::RecordError::NoSnapshot`). Быстрый
/// поток суток не менял: его единственная часть — D.
#[test]
fn deep_stream_rotates_on_its_own_part_and_waits_for_the_exchange_snapshot() {
    use crate::bybit::conn::{ORDERBOOK_DEEP_DEPTH, ORDERBOOK_DEPTH};
    use hftbacktest::types::{LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT};
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let next_day_ns = NOON_NS + 12 * 3600 * 1_000_000_000 + 1_000_000;
    let next_ms = next_day_ns / 1_000_000;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event_at_depth(
            0,
            NOON_NS,
            NOON_NS / 1_000_000,
            true,
            1,
            ORDERBOOK_DEPTH,
        )),
        Step::Ev(book_event_at_depth(
            0,
            NOON_NS,
            NOON_NS / 1_000_000,
            true,
            1,
            ORDERBOOK_DEEP_DEPTH,
        )),
        Step::Ev(Event::Gap {
            symbol: 0,
            local_ts_ns: NOON_NS,
            kind: FeedGapKind::SequenceGap,
            depth: Some(ORDERBOOK_DEEP_DEPTH),
            silence_ns: None,
            first_of_episode: false,
            detail: crate::feed::GapDetail::SequenceGap {
                depth: ORDERBOOK_DEEP_DEPTH,
                expected: 1,
                got: 2,
            },
        }),
        // Дельта глубокого потока в сутках D+1 — она же и ротирует его часть.
        Step::Ev(book_event_at_depth(
            0,
            next_day_ns,
            next_ms,
            false,
            2,
            ORDERBOOK_DEEP_DEPTH,
        )),
        Step::Ev(Event::Tick {
            local_ts_ns: next_day_ns + 1,
        }),
        // Снапшот биржи открывает новую часть.
        Step::Ev(book_event_at_depth(
            0,
            next_day_ns + 2,
            next_ms + 1,
            true,
            3,
            ORDERBOOK_DEEP_DEPTH,
        )),
        Step::Ev(Event::Tick {
            local_ts_ns: next_day_ns + 3,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    let deep_d = crate::commands::record::day_file_path(&root.join(DEEP_DIR), "SYM", TEST_DAY, 1);
    let deep_d1 =
        crate::commands::record::day_file_path(&root.join(DEEP_DIR), "SYM", "2026-09-13", 1);
    assert!(deep_d.is_file(), "часть суток D остаётся на диске");
    assert!(
        deep_d1.is_file(),
        "глубокий поток ротировался в свою часть D+1"
    );
    assert_eq!(ctx.states[0].streams[DEEP_STREAM].part, 1);
    assert_eq!(
        ctx.states[0].streams[DEEP_STREAM].day_index,
        crate::commands::record::day_index_of_day_str("2026-09-13").unwrap()
    );
    // Быстрый поток суток не менял, `-p2` нигде не появилось.
    assert_eq!(
        ctx.states[0].streams[FAST_STREAM].day_index,
        crate::commands::record::day_index_of_day_str(TEST_DAY).unwrap()
    );
    assert!(!crate::commands::record::day_file_path(&root, "SYM", "2026-09-13", 1).exists());
    assert!(
        !crate::commands::record::day_file_path(&root.join(DEEP_DIR), "SYM", TEST_DAY, 2).exists()
    );
    assert_eq!(
        summary.binlog_files.len(),
        1,
        "в binlog_files — только быстрая часть D"
    );

    // В части D+1 первым (и единственным) кадром — снапшот **биржи** с его
    // `cts` = next_ms + 1, а не снапшот ротации с `cts` триггера = next_ms:
    // дельта до снапшота в файл не попала вовсе.
    let frames = frames_on_disk(&deep_d1);
    assert_eq!(
        frames.len(),
        1,
        "один кадр — снапшот биржи; дельта ждала его и не писалась"
    );
    let first = &frames[0];
    assert_eq!(first.len(), 2);
    assert_eq!(first[0].ev, LOCAL_BID_DEPTH_SNAPSHOT_EVENT);
    assert_eq!(first[0].exch_ts_ns, (next_ms + 1) * 1_000_000);
    assert_eq!(first[1].ev, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT);
    assert!(
        !first.iter().any(|r| r.exch_ts_ns == next_ms * 1_000_000),
        "снапшот ротации не писался: дельта-триггер в файл не легла"
    );
    // Записи глубокого потока: снапшот D (2) плюс снапшот D+1 (2); дельта
    // D+1 потеряна по контракту «файл, начатый с дельт, нечитаем».
    let deep_counters = summary
        .streams
        .iter()
        .find(|s| s.depth == ORDERBOOK_DEEP_DEPTH)
        .expect("счётчики глубокого потока");
    assert_eq!(deep_counters.records, 4);
    assert_eq!(deep_counters.resyncs, 1);
    assert_eq!(deep_counters.frames_failed, 0);
}
