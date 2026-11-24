//! Путь записи: пачки в кадры, глубокий поток, сэмпл ресурсов, аллокации разбора.

use super::*;

/// ТУПИК 1 (handoff-04-1), closed: `write_market_event` used to build a
/// fresh `Vec<Record>` every call, which allocates on its first `push` —
/// one allocation per event on the hot path. Drives `state.scratch`
/// through `ReplayFeed` over 10⁶ synthetic events and sums
/// `alloc_count::measure` **around each `write_market_event` call
/// alone** (after a warmup that lets `Writer`'s internal buffers and
/// `state.scratch` reach steady-state capacity), not around
/// `feed.next_event()` — `ReplayFeed`'s own `FileReplayer`
/// (`bybit::verify.rs`, outside this task's zone) rebuilds its
/// `Update.bids`/`.asks` with `std::mem::take` on every flush, which
/// resets those `Vec`s to zero capacity and reallocates them on the next
/// push; that is a real, separate allocation source this test does not
/// claim to have fixed. What this test proves is exactly what ТУПИК 1
/// named: the function under test, not its synthetic data source.
#[test]
fn million_events_through_replay_feed_allocate_nothing_after_warmup() {
    const WARMUP_EVENTS: usize = 2_000;
    const MEASURED_EVENTS: usize = 1_000_000;
    // +1 snapshot event per 2 records (bid delta + trade); generous
    // margin so the feed never runs dry before `MEASURED_EVENTS` is hit.
    let pairs = (WARMUP_EVENTS + MEASURED_EVENTS) / 2 + 10;
    let bytes = build_synthetic_binlog(pairs);
    let mut feed = ReplayFeed::open(0, &bytes[..]).unwrap();

    let dir = tempfile::tempdir().unwrap();
    let mut state = fresh_state(dir.path());

    let mut next_market_event = || loop {
        match feed
            .next_event()
            .expect("синтетический ReplayFeed обязан покрыть весь замер")
        {
            Event::Market {
                local_ts_ns,
                payload,
                ..
            } => return (local_ts_ns, payload),
            Event::Gap { .. } | Event::Tick { .. } => continue,
        }
    };

    for _ in 0..WARMUP_EVENTS {
        let (local_ts_ns, payload) = next_market_event();
        write_market_event(&mut state, local_ts_ns, payload).unwrap();
    }

    let mut measured_allocations = 0u64;
    for _ in 0..MEASURED_EVENTS {
        let (local_ts_ns, payload) = next_market_event();
        let (_, counts) =
            crate::alloc_count::measure(|| write_market_event(&mut state, local_ts_ns, payload));
        measured_allocations += counts.allocations;
    }
    assert_eq!(
        measured_allocations, 0,
        "write_market_event обязана не аллоцировать после прогрева на 10^6 событий"
    );
}

/// Таск 24, критерий «батчинг записи»: пишет 2500 событий книги через
/// настоящий файловый `Writer` (`claim_symbol_binlog`, тот же путь, что
/// `run_session`), читает файл обратно `binlog::Reader` и проверяет два
/// свойства разом — (1) кадров на диске на порядки меньше, чем событий
/// (батчинг реально произошёл, а не осталась запись на событие под
/// другим именем переменной), (2) все записи читаются обратно в том же
/// порядке и с тем же содержимым, что построено напрямую из событий —
/// границы кадра не меняют то, что видит читатель (`bybit::verify::
/// FileReplayer` группирует по `(вид, exch_ts_ns)`, не по кадру, тот же
/// принцип проверяется здесь на уровне сырых `Record`).
#[test]
fn write_market_event_batches_many_messages_into_few_frames_and_round_trips() {
    const N: i64 = 2_500;
    let dir = tempfile::tempdir().unwrap();
    let (writer, part) = claim_symbol_binlog(
        dir.path(),
        "SOLUSDT",
        "2026-09-12",
        TEST_TICK_E9,
        TEST_STEP_E9,
    )
    .unwrap();
    assert_eq!(part, 1);
    let mut state = state_with_writer(dir.path(), "SOLUSDT", writer, part);

    let mut expected_ticks = Vec::with_capacity(N as usize);
    for i in 0..N {
        // Первое обновление — снапшот: файл, начатый с дельт, нечитаем
        // (`has_snapshot`, тот же контракт, что `Recorder::NoSnapshot`).
        let update = crate::book::Update {
            is_snapshot: i == 0,
            depth: 50,
            u: i as u64 + 1,
            seq: i as u64 + 1,
            cts_ms: i,
            bids: vec![(TEST_TICK_E9 * (100 + i), TEST_STEP_E9)],
            asks: vec![],
        };
        expected_ticks.push(100 + i);
        write_market_event(&mut state, i * 1_000, crate::bybit::ws::Event::Book(update)).unwrap();
    }
    flush_symbol_batch(&mut state.streams[FAST_STREAM]).unwrap();
    state.streams[FAST_STREAM].writer.flush().unwrap();
    assert_eq!(state.streams[FAST_STREAM].records_written, N as u64);

    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-12", 1);
    let bytes = std::fs::read(&path).unwrap();
    let mut reader = crate::binlog::Reader::open(&bytes[..]).unwrap();
    let mut frames = 0u64;
    let mut got_ticks = Vec::with_capacity(N as usize);
    while let Some(frame) = reader.read_frame().unwrap() {
        frames += 1;
        for rec in frame {
            got_ticks.push(rec.price_ticks);
        }
    }
    assert_eq!(
        got_ticks, expected_ticks,
        "порядок и содержимое обязаны совпасть"
    );
    assert!(
        frames <= 5,
        "{N} событий по одному кадру на событие дали бы {N} кадров; батчинг обязан \
         уместить их в единицы кадров (порог {}), получено {frames}",
        crate::commands::record::FRAME_TARGET_RECORDS
    );
    assert!(
        frames >= 2,
        "2500 записей при пороге {} обязаны дать хотя бы два кадра (не один гигантский), \
         получено {frames}",
        crate::commands::record::FRAME_TARGET_RECORDS
    );
}

/// T45: глубокий поток пишется **своим** файлом в `deep/` — тем же форматом
/// v3, первым кадром синтетический снапшот своей книги, дальше дельты
/// `.200`. Основной файл при этом остаётся ровно таким, как был (только
/// события `.50` и лента), а `session.json` несёт раздельные счётчики:
/// записи, байты и разрывы по потокам.
#[test]
fn deep_stream_writes_its_own_file_with_snapshot_first_frame() {
    use hftbacktest::types::{
        LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT,
        LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
    };
    const DEEP: u32 = crate::bybit::conn::ORDERBOOK_DEEP_DEPTH;
    const FAST: u32 = crate::bybit::conn::ORDERBOOK_DEPTH;

    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        // Снапшот .200 открывает глубокий файл, снапшот .50 — основной:
        // каждый поток флашит свой первый кадр сразу.
        Step::Ev(book_event_at_depth(0, NOON_NS, 1_000, true, 1, DEEP)),
        Step::Ev(book_event_at_depth(0, NOON_NS, 1_000, true, 10, FAST)),
        // По дельте в каждый поток — они уйдут вторыми кадрами по тику.
        Step::Ev(book_event_at_depth(0, NOON_NS, 1_001, false, 2, DEEP)),
        Step::Ev(book_event_at_depth(0, NOON_NS, 1_001, false, 11, FAST)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 20_000_000_000,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    let deep = crate::commands::record::day_file_path(&root.join(DEEP_DIR), "SYM", TEST_DAY, 1);
    assert!(
        deep.is_file(),
        "глубокий файл обязан лежать в `{DEEP_DIR}/`: {}",
        deep.display()
    );
    let deep_frames = frames_on_disk(&deep);
    assert_eq!(
        deep_frames.len(),
        2,
        "снапшот — своим кадром сразу, дельта — вторым кадром по тику: {deep_frames:?}"
    );
    assert_eq!(
        deep_frames[0].iter().map(|r| r.ev).collect::<Vec<_>>(),
        vec![
            LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
            LOCAL_ASK_DEPTH_SNAPSHOT_EVENT
        ],
        "первый кадр глубокого файла — синтетический снапшот его книги"
    );
    assert_eq!(
        deep_frames[1].iter().map(|r| r.ev).collect::<Vec<_>>(),
        vec![LOCAL_BID_DEPTH_EVENT, LOCAL_ASK_DEPTH_EVENT],
        "дальше идут дельты потока, не снапшот: {deep_frames:?}"
    );

    // Основной файл: те же два кадра, но только события быстрого потока —
    // ни одного уровня .200 в него не попало.
    let fast = crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 1);
    let fast_frames = frames_on_disk(&fast);
    assert_eq!(
        fast_frames.len(),
        2,
        "основной файл пишется ровно как до T45: снапшот, потом дельта"
    );
    assert_eq!(
        fast_frames[0].iter().map(|r| r.ev).collect::<Vec<_>>(),
        vec![
            LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
            LOCAL_ASK_DEPTH_SNAPSHOT_EVENT
        ]
    );
    assert_eq!(
        fast_frames[1].iter().map(|r| r.ev).collect::<Vec<_>>(),
        vec![LOCAL_BID_DEPTH_EVENT, LOCAL_ASK_DEPTH_EVENT]
    );
    // `cts` различает потоки в фикстуре: у быстрого события свои метки.
    assert_eq!(
        fast_frames[1][0].exch_ts_ns,
        1_001 * 1_000_000,
        "в основной файл легла именно дельта .50"
    );

    let by_depth = |depth: u32| {
        summary
            .streams
            .iter()
            .find(|s| s.depth == depth)
            .unwrap_or_else(|| panic!("в session.json нет потока .{depth}"))
    };
    assert_eq!(summary.streams.len(), 2, "по счётчику на поток");
    let deep_counters = by_depth(DEEP);
    let fast_counters = by_depth(FAST);
    assert_eq!(
        (deep_counters.records, fast_counters.records),
        (4, 4),
        "по два сообщения на поток, по два уровня в каждом"
    );
    assert!(
        deep_counters.bytes > 0,
        "глубокий файл занял место на диске"
    );
    assert_eq!(deep_counters.frames_failed, 0);
    assert_eq!(fast_counters.frames_failed, 0);
    // Основной файл — это ровно сегодняшнее поведение: его размер и число
    // записей совпадают с прежними счётчиками сводки, а не с суммой двух
    // потоков.
    assert_eq!(
        std::fs::metadata(&fast).unwrap().len(),
        fast_counters.bytes,
        "bytes быстрого потока — это весь основной файл, заголовок включительно"
    );
    assert_eq!(
        std::fs::metadata(&deep).unwrap().len(),
        deep_counters.bytes,
        "bytes глубокого потока — это весь его файл"
    );
    assert_eq!(
        summary.records_total,
        deep_counters.records + fast_counters.records
    );
    assert_eq!(
        summary.bytes_written,
        deep_counters.bytes + fast_counters.bytes,
        "сумма байт по потокам — это и есть общий счётчик сводки"
    );

    // Резолвер корня сессии глубокого файла не видит (T45): команды
    // `verify`/`levels`/`markout`/`touches`/`dashboard` читают только
    // основной.
    assert_eq!(
        super::super::super::session_binlog_for(&root, "SYM").unwrap(),
        vec![fast],
        "резолвер обязан вернуть только основной файл, хотя глубокий лежит рядом"
    );
}

/// T45: снапшот глубокого потока — 200 уровней на сторону, то есть 400
/// записей одним сообщением: самое крупное сообщение сессии, и под него
/// поднят бюджет кадра (`SESSION_MAX_FRAME_RECORDS`). Тест ставит батч на
/// порог (999 записей), кладёт сверху этот снапшот и проверяет разом три
/// вещи: кадр уходит **одним** куском (читается обратно 1399 записей), резерв
/// батча покрывает порог плюс снапшот **до** боя, и на этот вызов не нужно ни
/// одной аллокации — ошибка константы дала бы рост батча прямо в горячем пути.
///
/// Прогрев `Writer` здесь — **напрямую** (`write_frame` по готовому срезу),
/// мимо `write_market_event`: прогрев тем же путём растушевал бы и сам резерв,
/// который тест проверяет (первый большой кадр вырастил бы батч, и второй
/// прошёл бы по уже выросшей ёмкости). Поэтому `batch` за всю свою жизнь
/// видит единственное большое сообщение — измеряемое.
#[test]
fn deep_two_hundred_level_snapshot_at_batch_threshold_allocates_nothing() {
    use hftbacktest::types::{LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT};
    // Порог плюс снапшот `.200` обеих сторон — ожидание, собранное из порога
    // батча и глубины топика, а не из константы под тестом.
    const BIG_FRAME: usize = 200 * 2;
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut state = fresh_state(&root);
    let cts = 1_000i64;

    // Прогрев буферов `Writer` (тело кадра, сжатие, контекст zstd): кадр из
    // столько же записей, сколько будет у измеряемого, и с самой дорогой
    // разметкой — у каждой записи своя метка времени, то есть своя группа
    // (`encode_frame_payload_v3`), значит закодированное тело не меньше
    // измеряемого.
    let warm_len = FRAME_TARGET_RECORDS - 1 + BIG_FRAME;
    let warm: Vec<Record> = (0..warm_len)
        .map(|i| {
            rec(
                LOCAL_BID_DEPTH_EVENT,
                1_000_000 + i as i64,
                100 + i as i64,
                1,
            )
        })
        .collect();
    state.streams[DEEP_STREAM]
        .writer
        .write_frame(&warm)
        .unwrap();
    state.streams[DEEP_STREAM].writer.flush().unwrap();
    drop(warm);

    // Резерв батча обязан покрывать порог и снапшот целиком: растущий `Vec`
    // в горячем пути — ровно то, что запрещает гейт GC.
    assert!(
        state.streams[DEEP_STREAM].batch.capacity() >= warm_len,
        "резерв батча {} меньше порога со снапшотом .200 ({warm_len}): первое же \
         крупное сообщение на границе порога перевыделит его в бою",
        state.streams[DEEP_STREAM].batch.capacity()
    );

    // Снапшот биржи открывает файл потока (как в `lob session`).
    let mut u = 1u64;
    write_market_event(&mut state, cts, deep_snapshot_payload(cts, u)).unwrap();
    u += 1;
    // Дельты по одной записи доводят батч ровно до порога минус запись.
    while state.streams[DEEP_STREAM].batch.len() + 1 < FRAME_TARGET_RECORDS {
        write_market_event(&mut state, cts, deep_single_level_delta_payload(cts, u)).unwrap();
        u += 1;
    }
    assert_eq!(
        state.streams[DEEP_STREAM].batch.len(),
        FRAME_TARGET_RECORDS - 1,
        "батч обязан стоять ровно у порога {FRAME_TARGET_RECORDS}"
    );
    let snapshot = deep_snapshot_payload(cts, u);
    let (result, counts) =
        crate::alloc_count::measure(|| write_market_event(&mut state, cts, snapshot));
    result.unwrap();
    assert_eq!(
        counts.allocations,
        0,
        "снапшот .200 в {BIG_FRAME} записей поверх порога не имеет права перевыделять батч \
         или буфер кадра (потолок — {} записей)",
        super::super::sink::SESSION_MAX_FRAME_RECORDS
    );

    // Кадры на диске: прогревочный кадр, снапшот биржи (400 записей) и кадр
    // «999 дельт + снапшот» (1399 записей) — одним куском, без разрыва.
    let deep = crate::commands::record::day_file_path(&root.join(DEEP_DIR), "SYM", TEST_DAY, 1);
    let frames = frames_on_disk(&deep);
    assert_eq!(
        frames.len(),
        3,
        "прогрев, снапшот биржи и большой кадр: {frames:?}"
    );
    let big = &frames[2];
    assert_eq!(
        big.len(),
        warm_len,
        "сообщение снапшота не имеет права разъехаться по кадрам"
    );
    let snapshot_records = big
        .iter()
        .filter(|r| {
            r.ev == LOCAL_BID_DEPTH_SNAPSHOT_EVENT || r.ev == LOCAL_ASK_DEPTH_SNAPSHOT_EVENT
        })
        .count();
    assert_eq!(
        snapshot_records, BIG_FRAME,
        "в кадре обязаны быть все 200 уровней обеих сторон: биды 101..300, аски 1001..1200"
    );
    let deepest_bid = big
        .iter()
        .filter(|r| r.ev == LOCAL_BID_DEPTH_SNAPSHOT_EVENT)
        .map(|r| r.price_ticks)
        .min()
        .unwrap();
    assert_eq!(deepest_bid, 101, "самый дальний бид снапшота не потерян");
    assert_eq!(
        state.streams[DEEP_STREAM].records_written as usize,
        BIG_FRAME + warm_len,
        "счётчик потока — снапшот биржи и большой кадр (прогрев писался напрямую, мимо батча)"
    );
}

/// T45: каталог `deep/` для резолвера бинлогов **невидим**. Он и есть
/// причина, по которой глубокий файл лежит в подкаталоге, а не рядом: все
/// существующие команды (`verify`, `levels`, `markout`, `touches`,
/// `dashboard`, `binlog-stats`) ищут бинлоги корня сессии через
/// `session_binlog_for`, и найдись там файл `.200` — они читали бы два
/// потока как один.
#[test]
fn deep_subdirectory_is_invisible_to_the_root_binlog_resolver() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let fast = crate::commands::record::day_file_path(root, "SYM", TEST_DAY, 1);
    std::fs::write(&fast, b"main").unwrap();
    let deep_dir = root.join(DEEP_DIR);
    std::fs::create_dir_all(&deep_dir).unwrap();
    let deep = crate::commands::record::day_file_path(&deep_dir, "SYM", TEST_DAY, 1);
    std::fs::write(&deep, b"deep").unwrap();

    assert_eq!(
        super::super::super::session_binlog_for(root, "SYM").unwrap(),
        vec![fast.clone()],
        "резолвер обязан вернуть только основной файл, хотя глубокий лежит рядом"
    );
    // Подкаталог — не имя суток и не имя бинлога: разбор имени файла его не
    // признаёт файлом записи вовсе.
    assert_eq!(
        crate::commands::lob::parts::day_of_binlog_name(DEEP_DIR),
        None
    );
    assert_ne!(deep, fast);
}

/// `LatencyHistogram`: перцентиль на известной синтетике — 100 значений
/// `1..=100` (микросекунды в наносекундах), p99 обязан попасть в бин,
/// чья нижняя граница не выше истинного значения (99 000 нс) и не ниже
/// его больше, чем на ширину бина (`RESOLUTION_PCT`).
/// Таск 25: ряд `samples` олвейс-он не растёт без потолка — раз в
/// `RESOURCE_SAMPLE_SECS` (гейт GC `PLAN.md` 6.1: «RSS раз в 30 с»)
/// первый час, дальше раз в `HOURLY_REFRESH_SECS` (существующий часовой
/// период, с которым и переписывается `session.json`). Ожидаемые числа —
/// из таблицы 6.1 и doc `HOURLY_REFRESH_SECS`, не из кода под тестом.
#[test]
fn resource_sample_period_is_30s_in_the_first_hour_then_hourly() {
    assert_eq!(resource_sample_period(0), Duration::from_secs(30));
    assert_eq!(resource_sample_period(3_599), Duration::from_secs(30));
    assert_eq!(resource_sample_period(3_600), Duration::from_secs(3_600));
    assert_eq!(resource_sample_period(86_400), Duration::from_secs(3_600));
}

#[test]
fn latency_histogram_p99_is_within_bin_resolution_of_the_true_value() {
    let mut h = LatencyHistogram::new();
    assert_eq!(h.percentile(99), None, "пустая гистограмма — не число");
    for v in 1..=100i64 {
        h.record(v * 1_000);
    }
    assert_eq!(h.len(), 100);
    let p99 = h
        .percentile(99)
        .expect("100 значений — перцентиль обязан быть числом");
    let true_value = 99_000i64;
    assert!(
        p99 <= true_value,
        "перцентиль — нижняя граница бина, обязан быть не выше истинного значения: \
         {p99} > {true_value}"
    );
    let tolerance = (true_value as f64 * LatencyHistogram::RESOLUTION_PCT / 100.0) as i64 + 1;
    assert!(
        true_value - p99 <= tolerance,
        "отклонение {} превышает разрешение бина {tolerance}",
        true_value - p99
    );
}

/// Таск 24, критерий «аллокаций на сообщение после прогрева на пути
/// разбор → книга → запись — измерено `alloc_count`-гейтом»: 10⁶
/// сообщений в формате площадки (дельты 0–5 уровней на сторону, лента
/// 1–10 сделок каждое десятое сообщение, снапшот 50+50 раз в 100 000)
/// через `ws::parse_message_into` (буфер вызывающего, как в
/// `bybit::conn`), `Book::apply` (книга `conn.rs`) и `write_market_event`
/// — те же три шага, что проходит живой кадр до диска. Текст сообщения
/// строится **до** замера (в бою его выделяет `tungstenite` —
/// «транспорт ≤ 1 на кадр, принято», `PLAN.md` 6.1), замер — только три
/// шага. Ожидаемые числа названы заранее, не подсмотрены: книжное
/// сообщение — по одному `Vec<(i64, i64)>` на **непустую** сторону
/// (`book::Update` владеет `bids`/`asks`, doc модуля `ws.rs`), лента —
/// ноль, снапшот — восемь (две стороны × (первая ёмкость + три роста
/// 8→16→32→64, `ws::LEVELS_INITIAL_CAPACITY`)). Средние по видам
/// печатаются в stderr — их кладёт в таблицу
/// `docs/findings/collector-2026-09-12.md`.
#[test]
fn parse_book_write_path_allocations_per_message_after_warmup() {
    use crate::book::Book;
    use crate::bybit::ws::{parse_message_into, Event as WsEvent};

    const WARMUP: usize = 2_000;
    const MEASURED: usize = 1_000_000;
    const SNAPSHOT_EVERY: usize = 100_000;
    // Тик 0.01 — как у цен фикстуры с двумя знаками: 50 уровней через
    // 0.01 = 50 тиков, внутри кольца книги (`book::CAPACITY` = 128).
    const TICK_E9: i64 = 10_000_000;
    const STEP_E9: i64 = 1_000_000;
    const SNAPSHOT: usize = 0;
    const DELTA: usize = 1;
    const TRADES: usize = 2;

    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
            let mut z = self.0;
            z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
            z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
            z ^ (z >> 31)
        }
        fn below(&mut self, n: u64) -> u64 {
            self.next() % n
        }
    }
    fn levels(rng: &mut Rng, start_cents: u64, n: usize, ascending: bool) -> String {
        let mut out = String::from("[");
        for i in 0..n {
            if i > 0 {
                out.push(',');
            }
            let cents = if ascending {
                start_cents + i as u64
            } else {
                start_cents - i as u64
            };
            let milli = 1 + rng.below(50_000);
            out.push_str(&format!(
                "[\"{}.{:02}\",\"{}.{:03}\"]",
                cents / 100,
                cents % 100,
                milli / 1000,
                milli % 1000
            ));
        }
        out.push(']');
        out
    }
    fn book_msg(kind: &str, bids: &str, asks: &str, u: u64) -> String {
        format!(
            "{{\"topic\":\"orderbook.50.SOLUSDT\",\"type\":\"{kind}\",\"ts\":{},\
             \"data\":{{\"s\":\"SOLUSDT\",\"b\":{bids},\"a\":{asks},\"u\":{u},\
             \"seq\":{}}},\"cts\":{}}}",
            1_757_600_000_000u64 + u,
            u * 7,
            1_757_599_999_999u64 + u
        )
    }
    fn trades_msg(rng: &mut Rng, u: u64) -> String {
        let n = 1 + rng.below(10) as usize;
        let mut items = String::new();
        for i in 0..n {
            if i > 0 {
                items.push(',');
            }
            let side = if rng.below(2) == 0 { "Buy" } else { "Sell" };
            let milli = 1 + rng.below(50_000);
            let cents = 15_000 + rng.below(10);
            let id = rng.next();
            items.push_str(&format!(
                "{{\"T\":{},\"s\":\"SOLUSDT\",\"S\":\"{side}\",\"v\":\"{}.{:03}\",\
                 \"p\":\"{}.{:02}\",\"L\":\"PlusTick\",\"i\":\"{id:016x}\",\"BT\":false}}",
                1_757_600_000_000u64 + u,
                milli / 1000,
                milli % 1000,
                cents / 100,
                cents % 100
            ));
        }
        format!(
            "{{\"topic\":\"publicTrade.SOLUSDT\",\"type\":\"snapshot\",\"ts\":{},\
             \"data\":[{items}]}}",
            1_757_600_000_000u64 + u
        )
    }

    let dir = tempfile::tempdir().unwrap();
    let mut state = fresh_state(dir.path());
    let mut book = Book::new(TICK_E9, STEP_E9);
    let mut events: Vec<WsEvent> = Vec::new();
    let mut rng = Rng(0x5EED_2026_0912);
    let mut u: u64 = 0;

    let mut allocs_by_kind = [0u64; 3];
    let mut max_by_kind = [0u64; 3];
    let mut n_by_kind = [0u64; 3];
    for i in 0..(WARMUP + MEASURED) {
        let (kind, raw, expected_book_allocs) = if i % SNAPSHOT_EVERY == 0 {
            u += 1;
            let bids = levels(&mut rng, 15_000, 50, false);
            let asks = levels(&mut rng, 15_001, 50, true);
            (SNAPSHOT, book_msg("snapshot", &bids, &asks, u), Some(8))
        } else if i % 10 == 9 {
            (TRADES, trades_msg(&mut rng, u), None)
        } else {
            u += 1;
            let nb = rng.below(6) as usize;
            let na = rng.below(6) as usize;
            let bid_start = 15_000 - rng.below(20);
            let ask_start = 15_001 + rng.below(20);
            let bids = levels(&mut rng, bid_start, nb, false);
            let asks = levels(&mut rng, ask_start, na, true);
            let non_empty_sides = u64::from(nb > 0) + u64::from(na > 0);
            (
                DELTA,
                book_msg("delta", &bids, &asks, u),
                Some(non_empty_sides),
            )
        };
        let (_, counts) = crate::alloc_count::measure(|| {
            parse_message_into(&raw, &mut events).expect("фикстура обязана разбираться");
            for ev in events.drain(..) {
                if let WsEvent::Book(update) = &ev {
                    book.apply(update)
                        .expect("фикстура — валидная последовательность u");
                }
                write_market_event(&mut state, 1, ev).unwrap();
            }
        });
        if i < WARMUP {
            continue;
        }
        let allocs = counts.allocations;
        n_by_kind[kind] += 1;
        allocs_by_kind[kind] += allocs;
        max_by_kind[kind] = max_by_kind[kind].max(allocs);
        if let Some(expected) = expected_book_allocs {
            assert_eq!(
                allocs, expected,
                "сообщение #{i}: книжное сообщение обязано аллоцировать ровно по одному \
                 Vec на непустую сторону (снапшот — 8), получено {allocs}: {raw}"
            );
        }
    }
    assert_eq!(
        max_by_kind[TRADES], 0,
        "лента сделок после прогрева не аллоцирует — сделки идут прямо в буфер вызывающего"
    );
    for (name, kind) in [("snapshot", SNAPSHOT), ("delta", DELTA), ("trades", TRADES)] {
        eprintln!(
            "alloc gate: {name}: n={} allocs_per_msg_avg={:.3} max={}",
            n_by_kind[kind],
            allocs_by_kind[kind] as f64 / n_by_kind[kind].max(1) as f64,
            max_by_kind[kind]
        );
    }
    flush_symbol_batch(&mut state.streams[FAST_STREAM]).unwrap();
    state.streams[FAST_STREAM].writer.flush().unwrap();
    assert!(state.streams[FAST_STREAM].records_written > 0);
}
