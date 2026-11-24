//! Пул на ходу: добавление строк `instruments.csv` (таск 34).

use super::*;

// -----------------------------------------------------------------------
// Таск 34: пул на ходу — `<root>/instruments.csv` живой файл.
// -----------------------------------------------------------------------

/// Сценарный `Feed` без изменения состава: то же, что реплей, — источник
/// статичен, `add`/`remove` отвечают `StaticSource`. Тесты выше про пул не
/// знают, им это и нужно.
impl DynamicPool for ScriptedFeed {
    fn add(
        &mut self,
        _members: Vec<PoolMember>,
    ) -> Result<Vec<u16>, crate::feed::live::LayoutError> {
        Err(crate::feed::live::LayoutError::StaticSource)
    }

    fn remove(&mut self, _symbols: &[u16]) -> Result<Vec<u16>, crate::feed::live::LayoutError> {
        Err(crate::feed::live::LayoutError::StaticSource)
    }
}

/// Сценарный `Feed`, который умеет расти и снимать: `add` продолжает
/// нумерацию с `pool_len` (как `LiveFeed`) и запоминает, что добавляли,
/// `remove` запоминает снятые индексы — тесты проверяют, что в источник
/// партия/снятие ушли ровно один раз и с ожидаемыми индексами.
pub(super) struct GrowingFeed {
    pub(super) steps: ScriptedFeed,
    pub(super) pool_len: usize,
    pub(super) added: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
    pub(super) removed: std::sync::Arc<std::sync::Mutex<Vec<u16>>>,
}

impl Feed for GrowingFeed {
    fn next_event(&mut self) -> Option<Event> {
        self.steps.next_event()
    }
}

impl DynamicPool for GrowingFeed {
    fn add(
        &mut self,
        members: Vec<PoolMember>,
    ) -> Result<Vec<u16>, crate::feed::live::LayoutError> {
        let mut out = Vec::with_capacity(members.len());
        for m in members {
            out.push(u16::try_from(self.pool_len).unwrap());
            self.pool_len += 1;
            self.added.lock().unwrap().push(m.symbol);
        }
        Ok(out)
    }

    /// Индексы не переиспользуются: `pool_len` снятие не уменьшает — того же
    /// правила держится `LiveFeed`. Соседей сценарный источник не делит: шва
    /// нет (пустой список), — шов задаёт `NeighbourSeamFeed`.
    fn remove(&mut self, symbols: &[u16]) -> Result<Vec<u16>, crate::feed::live::LayoutError> {
        self.removed.lock().unwrap().extend_from_slice(symbols);
        Ok(Vec::new())
    }
}

/// Источник, который при снятии сообщает соседей (A8.1b): в живом пуле
/// инструменты делят сокет, и его пересборка — шов покрытия у оставшихся;
/// сценарный `Feed` ничего не делит, поэтому шов задаётся тестом явно.
pub(super) struct NeighbourSeamFeed {
    pub(super) inner: GrowingFeed,
    pub(super) seams: Vec<u16>,
}

impl Feed for NeighbourSeamFeed {
    fn next_event(&mut self) -> Option<Event> {
        self.inner.next_event()
    }
}

impl DynamicPool for NeighbourSeamFeed {
    fn add(
        &mut self,
        members: Vec<PoolMember>,
    ) -> Result<Vec<u16>, crate::feed::live::LayoutError> {
        crate::feed::DynamicPool::add(&mut self.inner, members)
    }

    fn remove(&mut self, symbols: &[u16]) -> Result<Vec<u16>, crate::feed::live::LayoutError> {
        crate::feed::DynamicPool::remove(&mut self.inner, symbols)?;
        Ok(self.seams.clone())
    }
}

/// Источник, у которого первые `failures_left` партий `add` отказывают:
/// проверка повтора на следующем тике **без** нового `mtime` (A8.1b).
pub(super) struct FlakyAddFeed {
    pub(super) inner: GrowingFeed,
    pub(super) failures_left: usize,
}

impl Feed for FlakyAddFeed {
    fn next_event(&mut self) -> Option<Event> {
        self.inner.next_event()
    }
}

impl DynamicPool for FlakyAddFeed {
    fn add(
        &mut self,
        members: Vec<PoolMember>,
    ) -> Result<Vec<u16>, crate::feed::live::LayoutError> {
        if self.failures_left > 0 {
            self.failures_left -= 1;
            return Err(crate::feed::live::LayoutError::StaticSource);
        }
        crate::feed::DynamicPool::add(&mut self.inner, members)
    }

    fn remove(&mut self, symbols: &[u16]) -> Result<Vec<u16>, crate::feed::live::LayoutError> {
        crate::feed::DynamicPool::remove(&mut self.inner, symbols)
    }
}

pub(super) const POOL_CSV_HEADER: &str = "symbol,tick_size,min_order_qty,qty_step\n";

/// Дописывает строку в `<root>/instruments.csv` (создаёт с заголовком,
/// если файла нет). Короткая пауза перед записью — чтобы `mtime` заведомо
/// отличался от предыдущей записи на любой файловой системе.
pub(super) fn append_pool_row(root: &Path, row: &str) {
    std::thread::sleep(Duration::from_millis(20));
    let path = root.join("instruments.csv");
    let mut text = std::fs::read_to_string(&path).unwrap_or_else(|_| POOL_CSV_HEADER.to_string());
    text.push_str(row);
    text.push('\n');
    std::fs::write(&path, text).unwrap();
}

pub(super) fn session_json_on_disk(root: &Path) -> SessionSummary {
    serde_json::from_str(&std::fs::read_to_string(root.join("session.json")).unwrap()).unwrap()
}

/// Критерии таска 34: между двумя тиками в `instruments.csv` дописана
/// строка → появился `<SYM>-<день>.binlog` с заголовком из строки (tick
/// 0.5, step 0.001 — не тестовые `TEST_TICK_E9`/`TEST_STEP_E9`, чтобы
/// видеть, что шаги пришли из файла), `session.json.instruments` и
/// `binlog_files` выросли на один; событие нового символа с индексом 1
/// легло в **его** файл, а не в файл первого (индекс = позиция в `states`);
/// повторная запись той же строки ничего не дублирует; битая строка —
/// только stderr, состав прежний; в источник партия ушла один раз.
#[test]
fn a_row_appended_to_instruments_csv_between_ticks_joins_the_recording_once() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    // Файл с уже пишущимся символом лежит до старта — как после `cp
    // instruments.csv <root>/` из `CLAUDE.md`; его повтор ничего не даёт.
    append_pool_row(&root, "SYM,0.001,1,0.001");
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let added = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let after_first = std::sync::Arc::new(std::sync::Mutex::new(None::<SessionSummary>));
    let after_first_probe = after_first.clone();
    let after_first_root = root.clone();
    let (r1, r2, r3) = (root.clone(), root.clone(), root.clone());
    let new_path = crate::commands::record::day_file_path(&root, "NEWUSDT", TEST_DAY, 1);
    let sym_path = crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 1);
    let new_probe = new_path.clone();
    let mut feed = GrowingFeed {
        pool_len: 1,
        added: added.clone(),
        removed: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
        steps: ScriptedFeed(VecDeque::from(vec![
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + window_ns,
            }),
            Step::Probe(Box::new(move || {
                assert!(
                    !new_probe.exists(),
                    "до строки в instruments.csv файла нового символа быть не должно"
                );
                append_pool_row(&r1, "NEWUSDT,0.5,1,0.001");
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + 2 * window_ns,
            }),
            Step::Probe(Box::new(move || {
                *after_first_probe.lock().unwrap() = Some(session_json_on_disk(&after_first_root));
                // Та же строка ещё раз — дубликата быть не должно.
                append_pool_row(&r2, "NEWUSDT,0.5,1,0.001");
            })),
            // Снапшот нового символа под индексом 1 — обязан лечь в его файл.
            Step::Ev(book_event(
                1,
                NOON_NS + 2 * window_ns + 1,
                NOON_NS / 1_000_000,
                true,
                1,
            )),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + 3 * window_ns,
            }),
            Step::Probe(Box::new(move || {
                // Битая строка: tick_size не число — только stderr.
                append_pool_row(&r3, "BADUSDT,not-a-number,1,0.001");
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + 4 * window_ns,
            }),
        ])),
    };
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    let after_first = after_first
        .lock()
        .unwrap()
        .take()
        .expect("после тика с новой строкой session.json обязан быть переписан");
    assert_eq!(
        after_first.instruments,
        vec!["SYM".to_string(), "NEWUSDT".to_string()],
        "новый символ — в session.json.instruments сразу после тика"
    );
    assert_eq!(
        after_first
            .binlog_files
            .iter()
            .map(|p| (p.symbol.as_str(), p.part))
            .collect::<Vec<_>>(),
        vec![("SYM", 1), ("NEWUSDT", 1)],
        "binlog_files вырос на часть нового символа"
    );

    let header = crate::binlog::Reader::open(&std::fs::read(&new_path).unwrap()[..])
        .unwrap()
        .header();
    assert_eq!(
        (header.tick_e9, header.step_e9),
        (500_000_000, 1_000_000),
        "tick/step заголовка — из строки instruments.csv, не из первого символа"
    );
    assert_eq!(
        frames_on_disk(&new_path).len(),
        1,
        "снапшот с индексом 1 обязан лечь в файл нового символа"
    );
    // У первого символа событий не было: файл либо ещё без заголовка
    // (буфер писателя не сброшен — кадров не было), либо без кадров.
    let sym_bytes = std::fs::read(&sym_path).unwrap();
    assert!(
        sym_bytes.is_empty() || frames_on_disk(&sym_path).is_empty(),
        "в файл первого символа ничего чужого не попало"
    );

    assert_eq!(
        *added.lock().unwrap(),
        vec!["NEWUSDT".to_string()],
        "в источник партия уходит ровно один раз: повтор строки и битая строка не добавляют"
    );
    assert_eq!(
        summary.instruments,
        vec!["SYM".to_string(), "NEWUSDT".to_string()],
        "битая строка не меняет состав, дубликата нет"
    );
    assert_eq!(summary.binlog_files.len(), 2);
    assert!(
        !crate::commands::record::day_file_path(&root, "BADUSDT", TEST_DAY, 1).exists(),
        "битой строке файла не открывали"
    );
}

/// Источник, который не растёт (реплей, `StaticSource`): строка в файле
/// — stderr, файл части убран, состав прежний; цикл не падает.
#[test]
fn a_static_feed_refuses_the_batch_and_the_recording_stays_as_it_was() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let r1 = root.clone();
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Probe(Box::new(move || {
            append_pool_row(&r1, "NEWUSDT,0.5,1,0.001")
        })),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + window_ns,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();
    assert_eq!(summary.instruments, vec!["SYM".to_string()]);
    assert!(
        !crate::commands::record::day_file_path(&root, "NEWUSDT", TEST_DAY, 1).exists(),
        "источник отказал — файл части не должен остаться"
    );
}

/// Семь запретов после добавления: событие рынка нового символа идёт тем
/// же `write_market_event`, что и у стартовых, — ноль аллокаций после
/// прогрева (тот же гейт `alloc_count`, что у сессии; аллокации — только в
/// момент добавления).
#[test]
fn events_of_an_added_symbol_allocate_nothing_after_warmup() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let mut feed = GrowingFeed {
        pool_len: 1,
        added: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
        removed: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
        steps: ScriptedFeed(VecDeque::new()),
    };
    append_pool_row(&root, "NEWUSDT,0.001,1,0.001");
    ctx.check_pool_file(&mut feed, NOON_NS, &SystemClock);
    assert_eq!(ctx.states.len(), 2, "символ добавлен");
    let mut u = 1u64;
    let mut delta = || {
        u += 1;
        crate::bybit::ws::Event::Book(crate::book::Update {
            is_snapshot: false,
            depth: 50,
            u,
            seq: u,
            cts_ms: NOON_NS / 1_000_000,
            bids: vec![(100 * TEST_TICK_E9, 5 * TEST_STEP_E9)],
            asks: vec![],
        })
    };
    // Снапшот и прогрев — до замера.
    let snapshot = crate::bybit::ws::Event::Book(crate::book::Update {
        is_snapshot: true,
        depth: 50,
        u: 1,
        seq: 1,
        cts_ms: NOON_NS / 1_000_000,
        bids: vec![(100 * TEST_TICK_E9, 5 * TEST_STEP_E9)],
        asks: vec![(110 * TEST_TICK_E9, 7 * TEST_STEP_E9)],
    });
    write_market_event(&mut ctx.states[1], NOON_NS, snapshot).unwrap();
    for _ in 0..2 * FRAME_TARGET_RECORDS {
        let payload = delta();
        write_market_event(&mut ctx.states[1], NOON_NS, payload).unwrap();
    }
    let mut allocations = 0u64;
    for _ in 0..10_000 {
        let payload = delta();
        let (_, counts) = crate::alloc_count::measure(|| {
            write_market_event(&mut ctx.states[1], NOON_NS, payload)
        });
        allocations += counts.allocations;
    }
    assert_eq!(
        allocations, 0,
        "после добавления — ноль аллокаций на событие рынка"
    );
}

/// В-41: `<root>/stop` останавливает сессию на ближайшем тике штатно —
/// `closed = true`, события после него не читаются; старый `stop` на старте
/// удаляется и новую сессию не останавливает.
#[test]
fn a_stop_file_ends_the_session_on_the_next_tick_and_is_cleared_on_open() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    std::fs::write(root.join("stop"), "").unwrap();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    assert!(
        !root.join("stop").exists(),
        "старый stop обязан быть удалён на старте"
    );
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let stop_root = root.clone();
    let consumed = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let consumed_probe = consumed.clone();
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + window_ns,
        }),
        Step::Probe(Box::new(move || {
            std::fs::write(stop_root.join("stop"), "").unwrap();
        })),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 2 * window_ns,
        }),
        Step::Probe(Box::new(move || {
            consumed_probe.store(true, std::sync::atomic::Ordering::SeqCst);
        })),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 3 * window_ns,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();
    assert!(summary.closed, "stop обязан дать штатное закрытие");
    assert!(
        !consumed.load(std::sync::atomic::Ordering::SeqCst),
        "после stop цикл не должен читать следующие события"
    );
}

/// V6 (2026-09-17): журнал потерь сам может не писаться — полный диск гасит и
/// `gaps.csv`. Отказ обязан быть виден счётчиком, а не исчезнуть вместе с
/// файлом. Путь журнала указывает на **каталог**: открыть его на дозапись
/// нельзя ровно так же, как файл на полном диске (и, в отличие от
/// несуществующего каталога, `ensure_gaps_csv` его сам не создаст).
#[test]
fn failed_gap_rows_are_counted_not_swallowed() {
    let dir = tempfile::tempdir().unwrap();
    let mut ctx = always_on_ctx(dir.path(), NOON_NS);
    let as_dir = dir.path().join("gaps.csv-но-каталог");
    std::fs::create_dir_all(&as_dir).unwrap();
    ctx.gaps_path = as_dir;
    let row = |detail: &str| {
        ctx.log_gap(
            Some(0),
            GapKind::ConnectFailed,
            "2026-09-12T12:00:00Z".to_string(),
            detail.to_string(),
        )
    };
    row("первый отказ");
    row("второй отказ");

    let summary = ctx.write_session_json(true, &SystemClock).unwrap();
    assert_eq!(
        summary.gap_rows_failed, 2,
        "оба отказа журнала посчитаны (session.json пишется в свой каталог и не зависит от gaps.csv)"
    );
}

// -----------------------------------------------------------------------
// A8.1: снятие инструмента с записи на ходу — строка ушла из
// `<root>/instruments.csv`. Правило «целого файла»: перечитывается только
// разобранный файл, кончающийся переводом строки (пустой/оборванный —
// прежний состав), и одна перечитка даёт снятия, добавления или и то и
// другое сразу (замена монеты, полная замена пула).
// -----------------------------------------------------------------------

/// Переписывает `<root>/instruments.csv` целиком (закрывающий перевод строки
/// есть — файл «дописан», A8.1): так снимают строку, так меняют монету и так
/// получают «недописанный» файл, если строку не закрыть. Пауза перед записью
/// — чтобы `mtime` заведомо отличался от прежнего (как в `append_pool_row`).
pub(super) fn rewrite_pool(root: &Path, rows: &[&str]) {
    std::thread::sleep(Duration::from_millis(20));
    let mut text = POOL_CSV_HEADER.to_string();
    for row in rows {
        text.push_str(row);
        text.push('\n');
    }
    std::fs::write(root.join("instruments.csv"), text).unwrap();
}

/// Сессия с двумя активными инструментами (`SYM` — индекс 0, `DEAD` — 1),
/// оба записаны в `<root>/instruments.csv` на старте.
pub(super) fn two_symbol_ctx(root: &Path) -> SessionCtx {
    rewrite_pool(root, &["SYM,0.001,1,0.001", "DEAD,0.5,1,0.001"]);
    SessionCtx::open(
        root,
        &[test_member("SYM"), test_member("DEAD")],
        SessionPlan::AlwaysOn,
        NOON_NS,
    )
    .unwrap()
}

/// Журналы источника для тестов A8.1: что в него добавляли и какие индексы
/// снимали (снятие приходит индексами — символ по индексу знает состояние).
pub(super) type AddedLog = std::sync::Arc<std::sync::Mutex<Vec<String>>>;

pub(super) type RemovedLog = std::sync::Arc<std::sync::Mutex<Vec<u16>>>;

pub(super) fn growing_feed(
    pool_len: usize,
    steps: Vec<Step>,
) -> (GrowingFeed, AddedLog, RemovedLog) {
    let added = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let removed = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let feed = GrowingFeed {
        pool_len,
        added: added.clone(),
        removed: removed.clone(),
        steps: ScriptedFeed(VecDeque::from(steps)),
    };
    (feed, added, removed)
}
