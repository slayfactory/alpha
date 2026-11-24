//! Пул на ходу: снятие и замена инструментов (A8.1).

use super::*;

/// Критерий A8.1: строка исчезла из файла — символ снят с записи. Файлы
/// обоих потоков к этому моменту сброшены и закрыты (кадр до снятия на
/// диске), `session.json.pool_removals` показывает момент, `instruments`
/// помнит историю, а события снятого индекса (успевшие лечь в канал) и его
/// разрывы пропускаются: ни байта в закрытый файл, ни строки в `gaps.csv`.
#[test]
fn a_removed_row_stops_the_symbol_flushes_its_files_and_is_written_down() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = two_symbol_ctx(&root);
    let dead_path = crate::commands::record::day_file_path(&root, "DEAD", TEST_DAY, 1);
    let dead_probe = dead_path.clone();
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let r1 = root.clone();
    let r2 = root.clone();
    let probe_root = root.clone();
    let (mut feed, _added, removed) = growing_feed(
        2,
        vec![
            // Снапшот снятого символа — кадр, который обязан лечь на диск
            // до снятия.
            Step::Ev(book_event(1, NOON_NS, NOON_NS / 1_000_000, true, 1)),
            Step::Probe(Box::new(move || {
                rewrite_pool(&r1, &["SYM,0.001,1,0.001"]);
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + window_ns,
            }),
            Step::Probe(Box::new(move || {
                let on_disk = frames_on_disk(&dead_probe);
                assert_eq!(
                    on_disk.len(),
                    1,
                    "кадр до снятия обязан быть на диске (снятие сбрасывает батчи): {on_disk:?}"
                );
            })),
            // Событие рынка снятого индекса и разрыв того же индекса —
            // доезжают после снятия (сокет остановлен, канал уже не пуст).
            Step::Ev(book_event(
                1,
                NOON_NS + window_ns + 1,
                NOON_NS / 1_000_000 + 1,
                false,
                2,
            )),
            Step::Ev(Event::Gap {
                symbol: 1,
                local_ts_ns: NOON_NS + window_ns + 2,
                kind: crate::feed::GapKind::Disconnected,
                depth: None,
                silence_ns: None,
                first_of_episode: false,
                detail: crate::feed::GapDetail::Disconnected,
            }),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + 2 * window_ns,
            }),
            Step::Probe(Box::new(move || {
                let on_disk = frames_on_disk(&dead_path);
                assert_eq!(
                    on_disk.len(),
                    1,
                    "после снятия в закрытый файл не должно попасть ничего: {on_disk:?}"
                );
                let rows = crate::commands::record::read_gap_rows(&probe_root.join("gaps.csv"))
                    .unwrap_or_default();
                assert!(
                    rows.is_empty(),
                    "разрыв снятого символа — следствие нашей остановки сокета, а не шов записи: \
                     {rows:?}"
                );
            })),
            // Возврат того же имени в пул — новое состояние и **новый**
            // индекс (индексы не переиспользуются).
            Step::Probe(Box::new(move || {
                rewrite_pool(
                    &r2,
                    &["SYM,0.001,1,0.001", "DEAD,0.5,1,0.001", "NEW,0.5,1,0.001"],
                );
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + 3 * window_ns,
            }),
        ],
    );
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert!(!ctx.states[1].active, "символ 1 снят");
    assert!(ctx.states[0].active, "символ 0 не тронут");
    assert_eq!(
        *removed.lock().unwrap(),
        vec![1u16],
        "в источник ушло снятие ровно одного индекса, один раз"
    );
    assert_eq!(
        summary.gaps, 0,
        "разрыв снятого символа в счётчик потерь не идёт"
    );
    assert_eq!(
        summary
            .pool_removals
            .iter()
            .map(|r| r.symbol.as_str())
            .collect::<Vec<_>>(),
        vec!["DEAD"],
        "снятие записано в session.json.pool_removals"
    );
    assert_eq!(
        summary.instruments,
        vec!["SYM".to_string(), "DEAD".to_string(), "NEW".to_string()],
        "история остаётся: снятый в списке, вернувшееся имя — без дубликата"
    );
    assert!(
        summary.bytes_written > 0,
        "байты снятого символа из сводки не пропадают: {summary:?}"
    );
    assert_eq!(
        ctx.states.len(),
        4,
        "снятый индекс не переиспользован: вернувшееся имя и новое получили свои состояния"
    );
}

/// Ревью 24.09, блок B: быстрые правки пула подряд — снятие, возврат и снова
/// снятие того же имени (так шли правки живого пула 24.09 11:28–11:37 UTC).
/// Каждое снятие закрывает **свой** индекс (сначала исходный, потом выданный
/// при возврате), возврат получает новую часть, дублей индексов и повисших
/// активных состояний нет, `pool_removals` помнит оба снятия.
#[test]
fn remove_readd_remove_in_quick_succession_closes_each_index_once() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = two_symbol_ctx(&root);
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let (r1, r2, r3) = (root.clone(), root.clone(), root.clone());
    let (mut feed, added, removed) = growing_feed(
        2,
        vec![
            Step::Probe(Box::new(move || rewrite_pool(&r1, &["SYM,0.001,1,0.001"]))),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + window_ns,
            }),
            Step::Probe(Box::new(move || {
                rewrite_pool(&r2, &["SYM,0.001,1,0.001", "DEAD,0.5,1,0.001"]);
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + 2 * window_ns,
            }),
            Step::Probe(Box::new(move || rewrite_pool(&r3, &["SYM,0.001,1,0.001"]))),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + 3 * window_ns,
            }),
        ],
    );
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert_eq!(
        *removed.lock().unwrap(),
        vec![1u16, 2u16],
        "снят исходный индекс, затем индекс, выданный при возврате — каждый один раз"
    );
    assert_eq!(
        *added.lock().unwrap(),
        vec!["DEAD".to_string()],
        "возврат — одно добавление"
    );
    assert_eq!(
        ctx.states.len(),
        3,
        "индексы не переиспользуются: 0, 1 (снят), 2 (возврат, снят)"
    );
    assert!(ctx.states[0].active, "SYM не тронут");
    assert!(
        !ctx.states[1].active && !ctx.states[2].active,
        "оба состояния DEAD неактивны — повисшего активного нет"
    );
    assert_eq!(
        summary
            .pool_removals
            .iter()
            .map(|r| r.symbol.as_str())
            .collect::<Vec<_>>(),
        vec!["DEAD", "DEAD"],
        "оба снятия записаны"
    );
    assert_eq!(
        summary.instruments,
        vec!["SYM".to_string(), "DEAD".to_string()],
        "история без дублей имени"
    );
    assert!(
        crate::commands::record::day_file_path(&root, "DEAD", TEST_DAY, 2).exists(),
        "возврат писал в новую часть суток (-p2)"
    );
}

/// Замена монеты — это снятие и добавление **за одну перечитку**: старый
/// индекс уходит в `remove`, новый приходит из `add` со следующим номером.
#[test]
fn swapping_a_coin_is_a_remove_and_an_add_in_one_reread() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = two_symbol_ctx(&root);
    let r1 = root.clone();
    let (mut feed, added, removed) = growing_feed(
        2,
        vec![
            Step::Probe(Box::new(move || {
                rewrite_pool(&r1, &["SYM,0.001,1,0.001", "FRESH,0.5,1,0.001"]);
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000,
            }),
        ],
    );
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert_eq!(*removed.lock().unwrap(), vec![1u16], "снят один индекс");
    assert_eq!(
        *added.lock().unwrap(),
        vec!["FRESH".to_string()],
        "добавлен ровно один новый символ"
    );
    assert_eq!(
        summary
            .pool_removals
            .iter()
            .map(|r| r.symbol.as_str())
            .collect::<Vec<_>>(),
        vec!["DEAD"]
    );
    assert_eq!(
        summary.instruments,
        vec!["SYM".to_string(), "DEAD".to_string(), "FRESH".to_string()]
    );
    assert!(
        crate::commands::record::day_file_path(&root, "FRESH", TEST_DAY, 1).exists(),
        "новому символу открыта своя часть"
    );
}

/// Недописанный файл (нет перевода строки в конце) — не состав записи:
/// снятие не применяется, состав прежний, повтор на следующем изменении.
#[test]
fn an_unterminated_pool_file_does_not_remove_anything() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = two_symbol_ctx(&root);
    let r1 = root.clone();
    let (mut feed, added, removed) = growing_feed(
        2,
        vec![
            Step::Probe(Box::new(move || {
                std::thread::sleep(Duration::from_millis(20));
                // Оборванная перезапись: строка DEAD не закрыта переводом
                // строки — файл от целого не отличается ничем, кроме этого.
                std::fs::write(
                    r1.join("instruments.csv"),
                    format!("{POOL_CSV_HEADER}SYM,0.001,1,0.001\nDEAD,0.5,1,0.0"),
                )
                .unwrap();
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000,
            }),
        ],
    );
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert!(
        ctx.states[0].active && ctx.states[1].active,
        "состав прежний"
    );
    assert!(removed.lock().unwrap().is_empty(), "снятия не было");
    assert!(added.lock().unwrap().is_empty(), "добавления не было");
    assert_eq!(
        summary.instruments,
        vec!["SYM".to_string(), "DEAD".to_string()]
    );
}

/// Полная замена пула — ни одного общего имени — тоже набор снятий плюс
/// добавлений за одну перечитку: запись не остаётся без инструментов, а
/// недописанный файл ловится правилом «файл обязан кончаться переводом
/// строки» (отдельный тест).
#[test]
fn a_disjoint_full_replacement_removes_the_old_pool_and_adds_the_new_one() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = two_symbol_ctx(&root);
    let r1 = root.clone();
    let (mut feed, added, removed) = growing_feed(
        2,
        vec![
            Step::Probe(Box::new(move || {
                rewrite_pool(&r1, &["OTHER,0.5,1,0.001"]);
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000,
            }),
        ],
    );
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert_eq!(
        *removed.lock().unwrap(),
        vec![0u16, 1u16],
        "оба прежних символа сняты"
    );
    assert_eq!(*added.lock().unwrap(), vec!["OTHER".to_string()]);
    assert!(!ctx.states[0].active && !ctx.states[1].active);
    assert!(
        ctx.states[2].active,
        "новый символ пишется (индекс продолжает нумерацию)"
    );
    assert_eq!(
        summary.instruments,
        vec!["SYM".to_string(), "DEAD".to_string(), "OTHER".to_string()],
        "история снятых остаётся в записи"
    );
}

/// Повторная перечитка того же состава (файл переписан без изменений) —
/// ни снятий, ни добавлений, `mtime` остаётся пусковым событием, а не
/// приказом менять запись.
#[test]
fn rewriting_the_same_pool_changes_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = two_symbol_ctx(&root);
    let r1 = root.clone();
    let (mut feed, added, removed) = growing_feed(
        2,
        vec![
            Step::Probe(Box::new(move || {
                rewrite_pool(&r1, &["SYM,0.001,1,0.001", "DEAD,0.5,1,0.001"]);
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000,
            }),
        ],
    );
    run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert!(ctx.states[0].active && ctx.states[1].active);
    assert!(removed.lock().unwrap().is_empty());
    assert!(added.lock().unwrap().is_empty());
    assert_eq!(ctx.states.len(), 2, "новых состояний не появилось");
}

/// Снятие без добавления: `session.json` обязан показать его **сразу** (а не
/// на следующей часовой перезаписи), а кадр, лежавший в батче, — лечь на диск
/// закрытием части: `close_symbol` сбрасывает потоки сам, не полагаясь на
/// сброс тика.
#[test]
fn a_removal_without_additions_writes_the_frame_and_the_summary_right_away() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = two_symbol_ctx(&root);
    let dead_path = crate::commands::record::day_file_path(&root, "DEAD", TEST_DAY, 1);
    let (mut feed, added, removed) = growing_feed(2, Vec::new());
    let depth = crate::bybit::conn::ORDERBOOK_DEPTH;
    let cts_ms = NOON_NS / 1_000_000;
    let snapshot = crate::bybit::ws::Event::Book(crate::book::Update {
        is_snapshot: true,
        depth,
        u: 1,
        seq: 1,
        cts_ms,
        bids: vec![(100 * TEST_TICK_E9, 5 * TEST_STEP_E9)],
        asks: vec![(110 * TEST_TICK_E9, 7 * TEST_STEP_E9)],
    });
    let delta = crate::bybit::ws::Event::Book(crate::book::Update {
        is_snapshot: false,
        depth,
        u: 2,
        seq: 2,
        cts_ms: cts_ms + 1,
        bids: vec![(101 * TEST_TICK_E9, 5 * TEST_STEP_E9)],
        asks: vec![],
    });
    write_market_event(&mut ctx.states[1], NOON_NS, snapshot).unwrap();
    write_market_event(&mut ctx.states[1], NOON_NS + 1, delta).unwrap();
    assert_eq!(
        frames_on_disk(&dead_path).len(),
        1,
        "снапшот лёг кадром сразу, дельта осталась в батче"
    );

    rewrite_pool(&root, &["SYM,0.001,1,0.001"]);
    ctx.check_pool_file(&mut feed, NOON_NS + 2, &SystemClock);

    assert_eq!(
        frames_on_disk(&dead_path).len(),
        2,
        "батч снятого символа сброшен закрытием части, а не потерян"
    );
    assert!(!ctx.states[1].active);
    assert_eq!(*removed.lock().unwrap(), vec![1u16]);
    assert!(added.lock().unwrap().is_empty());
    let on_disk = session_json_on_disk(&root);
    assert_eq!(
        on_disk
            .pool_removals
            .iter()
            .map(|r| r.symbol.as_str())
            .collect::<Vec<_>>(),
        vec!["DEAD"],
        "снятие без добавления всё равно переписывает session.json"
    );
    assert_eq!(
        on_disk.instruments,
        vec!["SYM".to_string(), "DEAD".to_string()],
        "история символа остаётся в записи"
    );
}

/// A8.5 (2026-09-18): после отказов соединения запись возобновляется штатно.
/// `ConnectFailed` сбрасывает доверие книги (дельты в неё не пишутся), а первый
/// снапшот восстановленного сокета делает её доверенной снова — кадр уходит на
/// диск, сессия не перезапускается.
#[test]
fn a_snapshot_after_connect_failures_makes_the_book_trusted_again() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let fast_path = crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 1);
    let fast_probe = fast_path.clone();
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(Event::Gap {
            symbol: 0,
            local_ts_ns: NOON_NS,
            kind: FeedGapKind::ConnectFailed,
            depth: None,
            silence_ns: None,
            first_of_episode: false,
            detail: crate::feed::GapDetail::ConnectFailed {
                attempt: 1,
                http_status: Some(429),
                err: "Too Many Requests".to_string(),
            },
        }),
        Step::Probe(Box::new(move || {
            assert!(
                std::fs::metadata(&fast_probe).map_or(true, |m| m.len() == 0),
                "до снапшота файл части пуст"
            );
        })),
        Step::Ev(book_event(0, NOON_NS + 1, NOON_NS / 1_000_000 + 1, true, 1)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 20_000_000_000,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert_eq!(summary.connect_failed, 1, "отказ соединения посчитан");
    assert!(
        ctx.states[0].streams[FAST_STREAM].synced,
        "снапшот восстановленного сокета снова доверяет книгу"
    );
    assert!(ctx.states[0].streams[FAST_STREAM].has_snapshot);
    assert_eq!(
        frames_on_disk(&fast_path).len(),
        1,
        "кадр снапшота после восстановления лёг на диск"
    );
}

/// A8.6 (2026-09-18): скачок локальных часов (NTP-шаг) не ломает запись.
/// Метка `local_ts_ns` идёт назад, но кадры пишутся тем же путём (локальная
/// метка — только поле записи, ротация и разрывы смотрят время биржи
/// `cts`), `gaps.csv` не заводится, а `duration_s` в `session.json` не может
/// стать отрицательной (`(now − started).max(0)`). Анализ от локальной метки
/// не зависит вовсе: середина строится по `exch_ts_ns`.
#[test]
fn a_backward_local_clock_step_does_not_break_the_recording() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let fast_path = crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 1);
    // Метка второго события — на 5 с **раньше** первого: ровно шаг назад.
    let step_back = NOON_NS - 5_000_000_000;
    assert!(step_back < NOON_NS);
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
        Step::Ev(book_event(0, step_back, NOON_NS / 1_000_000 + 1, false, 2)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 20_000_000_000,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert_eq!(
        frames_on_disk(&fast_path).len(),
        2,
        "снапшот и дельта после шага часов — оба кадра на диске"
    );
    assert_eq!(summary.gaps, 0, "шаг часов — не потеря данных");
    assert_eq!(
        crate::commands::record::read_gap_rows(&gaps_csv_path(&root))
            .unwrap()
            .len(),
        0
    );
    let on_disk = session_json_on_disk(&root);
    assert!(
        on_disk.duration_s < u64::MAX / 2,
        "длительность считается от старта сессии, а не от метки события"
    );
    assert_eq!(summary.records_total, 4, "обе записи посчитаны записанными");
}

/// A8.1b (правка ревью A8.1): соседи по снятому сокету переезжают в новое
/// соединение — у них между остановкой старого и снапшотом нового данных нет.
/// Шов обязан быть **видимым**: строка `gaps.csv` класса `sequence_gap` на
/// каждого соседа (в проде 99 монет на одном сокете) и сброс доверия книги —
/// до снапшота дельты нового сокета легли бы не на ту книгу. Раньше это было
/// тихо (`gaps = 0`), и проверить «потери видны» было нечем.
#[test]
fn a_rebuilt_socket_gives_the_neighbours_a_visible_seam() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = two_symbol_ctx(&root);
    let r1 = root.clone();
    let (added, removed) = (
        std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
        std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
    );
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let mut feed = NeighbourSeamFeed {
        seams: vec![0],
        inner: GrowingFeed {
            pool_len: 2,
            added: added.clone(),
            removed: removed.clone(),
            steps: ScriptedFeed(VecDeque::from(vec![
                Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
                Step::Probe(Box::new(move || {
                    rewrite_pool(&r1, &["SYM,0.001,1,0.001"]);
                })),
                Step::Ev(Event::Tick {
                    local_ts_ns: NOON_NS + window_ns,
                }),
            ])),
        },
    };
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert_eq!(*removed.lock().unwrap(), vec![1u16], "снят только DEAD");
    assert_eq!(summary.gaps, 1, "шов соседа посчитан");
    let rows = crate::commands::record::read_gap_rows(&gaps_csv_path(&root)).unwrap();
    assert_eq!(rows.len(), 1, "строка на соседа: {rows:?}");
    assert_eq!(rows[0].kind, GapKind::SequenceGap);
    assert_eq!(rows[0].symbol, "SYM", "строка называет соседа, не снятого");
    assert!(
        rows[0].detail.contains("пересборка сокета при снятии DEAD"),
        "деталь объясняет причину шва: {}",
        rows[0].detail
    );
    assert!(
        !ctx.states[0].streams[FAST_STREAM].synced && !ctx.states[0].streams[DEEP_STREAM].synced,
        "до снапшота нового соединения книга соседа недоверена"
    );
}

/// A8.1b (правка ревью A8.1): правка пула одноразовая — если добавление новой
/// партии отказало, состав приводится к файлу **на следующем тике**, а не ждёт
/// нового `mtime` (иначе потерянная монета вернулась бы только после
/// следующей правки пула). Снятия при этом уже применены и не повторяются.
#[test]
fn a_failed_add_is_retried_on_the_next_tick_without_a_new_mtime() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    // Файл уже содержит будущий символ: правка была одна, `mtime` после неё
    // не меняется.
    rewrite_pool(&root, &["SYM,0.001,1,0.001", "NEW,0.5,1,0.001"]);
    let added = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let removed = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let mut feed = FlakyAddFeed {
        failures_left: 1,
        inner: GrowingFeed {
            pool_len: 1,
            added: added.clone(),
            removed: removed.clone(),
            steps: ScriptedFeed(VecDeque::from(vec![
                Step::Ev(Event::Tick {
                    local_ts_ns: NOON_NS + window_ns,
                }),
                // Проба между тиками: первый отказ не добавил ничего.
                Step::Probe(Box::new(move || {})),
                Step::Ev(Event::Tick {
                    local_ts_ns: NOON_NS + 2 * window_ns,
                }),
            ])),
        },
    };
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert_eq!(
        *added.lock().unwrap(),
        vec!["NEW".to_string()],
        "партия ушла в источник на повторе, и ровно один раз"
    );
    assert!(
        removed.lock().unwrap().is_empty(),
        "снятий не было: обе строки файла — активный и новый символы"
    );
    assert_eq!(
        summary.instruments,
        vec!["SYM".to_string(), "NEW".to_string()],
        "новый символ в записи после повтора"
    );
    assert_eq!(ctx.states.len(), 2);
}
