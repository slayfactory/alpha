//! Сбои диска и границы кадра: потеря границы, половинная запись, полный диск.

use super::*;

/// Файл, у которого отказывают **оба** шага: запись кадра (половина байт,
/// потом ошибка) и откат к границе кадра. Ровно это делает отвал тома или
/// диск, исчезнувший из-под записи: `FrameSink::boundary_lost` взводится, и
/// часть обязана быть переоткрыта следующей (`-p2`).
pub(super) struct LostBoundaryFile {
    pub(super) inner: File,
    pub(super) writes: u32,
    pub(super) fail_on: u32,
}

impl std::io::Write for LostBoundaryFile {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.writes += 1;
        if self.writes == self.fail_on {
            self.inner.write_all(&buf[..buf.len() / 2])?;
            return Err(std::io::Error::other("диск: нет места"));
        }
        self.inner.write(buf)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.inner.flush()
    }
}

impl SinkFile for LostBoundaryFile {
    fn truncate_to(&mut self, _len: u64) -> std::io::Result<()> {
        Err(std::io::Error::other(
            "диск: откат к границе кадра не удался",
        ))
    }
}

/// T45: потеря границы кадра — **по потокам**. Упавшая запись кадра глубокого
/// потока (и неудавшийся откат к границе) даёт `frames_failed` своему потоку,
/// строку `gaps.csv` и переоткрытие **его** части (`-p2` в `deep/`) с
/// синтетическим снапшотом его книги; часть быстрого потока при этом не
/// трогается, и в `binlog_files` (карта основных файлов) ротация глубокого не
/// попадает.
#[test]
fn deep_frame_boundary_loss_reopens_only_the_deep_part() {
    use crate::bybit::conn::ORDERBOOK_DEEP_DEPTH;
    use hftbacktest::types::{LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT};
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let sink_dir = tempfile::tempdir().unwrap();
    // Заголовок копится в буфере приёмника до первого сброса и уходит одним
    // `write_all` со снапшотом (№1); дельта по тику — №2: она падает на
    // половине, а откат к границе не удаётся, то есть граница потеряна.
    let (writer, part) = claim_part_with(
        sink_dir.path(),
        "SYM",
        TEST_DAY,
        1,
        TEST_TICK_E9,
        TEST_STEP_E9,
        |file| {
            FrameSink::over(Box::new(LostBoundaryFile {
                inner: file,
                writes: 0,
                fail_on: 2,
            }))
        },
    )
    .unwrap();
    ctx.states[0].streams[DEEP_STREAM].writer = writer;
    ctx.states[0].streams[DEEP_STREAM].part = part;

    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event_at_depth(
            0,
            NOON_NS,
            1_000,
            true,
            1,
            ORDERBOOK_DEEP_DEPTH,
        )),
        Step::Ev(book_event_at_depth(
            0,
            NOON_NS,
            1_001,
            false,
            2,
            ORDERBOOK_DEEP_DEPTH,
        )),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 20_000_000_000,
        }),
        Step::Ev(book_event_at_depth(
            0,
            NOON_NS,
            1_002,
            false,
            3,
            ORDERBOOK_DEEP_DEPTH,
        )),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 40_000_000_000,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    // Переоткрылась **только** часть глубокого потока и только она — в `deep/`.
    let deep_p2 = crate::commands::record::day_file_path(&root.join(DEEP_DIR), "SYM", TEST_DAY, 2);
    assert!(
        deep_p2.is_file(),
        "потерянная граница глубокого потока обязана дать его часть -p2"
    );
    assert!(
        !crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 2).exists(),
        "часть быстрого потока не теряла границу — -p2 у неё быть не должно"
    );
    assert_eq!(ctx.states[0].streams[DEEP_STREAM].part, 2);
    assert_eq!(ctx.states[0].streams[FAST_STREAM].part, 1);
    assert_eq!(
        summary.binlog_files.len(),
        1,
        "binlog_files — карта основных файлов: ротации глубокого в ней нет"
    );
    assert_eq!(summary.records_total, 6);

    // Переоткрытая часть начинается синтетическим снапшотом книги своего
    // потока, дальше идёт дельта, пришедшая после потери.
    let frames = frames_on_disk(&deep_p2);
    assert_eq!(
        frames.len(),
        2,
        "снапшот ротации и следующая дельта: {frames:?}"
    );
    assert_eq!(frames[0].len(), 2);
    assert_eq!(frames[0][0].ev, LOCAL_BID_DEPTH_SNAPSHOT_EVENT);
    assert_eq!(frames[0][0].price_ticks, 100);
    assert_eq!(frames[0][1].ev, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT);
    assert_eq!(frames[0][1].price_ticks, 110);
    assert_eq!(
        frames[1][0].exch_ts_ns,
        1_002 * 1_000_000,
        "дельта после потери границы легла в новую часть"
    );

    // Счётчики — по потокам: потерянный кадр принадлежит глубокому.
    let by_depth = |depth: u32| {
        summary
            .streams
            .iter()
            .find(|s| s.depth == depth)
            .unwrap_or_else(|| panic!("в session.json нет потока .{depth}"))
    };
    assert_eq!(by_depth(ORDERBOOK_DEEP_DEPTH).frames_failed, 1);
    assert_eq!(
        by_depth(crate::bybit::conn::ORDERBOOK_DEPTH).frames_failed,
        0
    );
    assert_eq!(summary.frames_failed, 1);
    let gaps = std::fs::read_to_string(gaps_csv_path(&root)).unwrap();
    assert_eq!(gaps.matches("write_failed").count(), 1, "{gaps}");
    assert!(
        gaps.contains("поток .200"),
        "строка разрыва обязана назвать поток: {gaps}"
    );
}

/// Файл под приёмником, роняющий N-й `write_all` посреди кадра
/// (половина байт на диск, потом ошибка) — то, что делает диск при
/// нехватке места или отвале тома.
pub(super) struct HalfFailingFile {
    pub(super) inner: File,
    pub(super) writes: u32,
    pub(super) fail_on: u32,
}

impl std::io::Write for HalfFailingFile {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.writes += 1;
        if self.writes == self.fail_on {
            self.inner.write_all(&buf[..buf.len() / 2])?;
            return Err(std::io::Error::other("диск: нет места"));
        }
        self.inner.write(buf)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.inner.flush()
    }
}

impl SinkFile for HalfFailingFile {
    fn truncate_to(&mut self, len: u64) -> std::io::Result<()> {
        self.inner.truncate_to(len)
    }
}

/// Ревью таска 25: упавшая запись кадра не оставляет обрывка на диске.
/// Снапшот (кадр 1), дельта → тик (кадр 2 падает на половине), дельта →
/// тик (кадр 3): `frames_failed = 1`, ровно одна строка `write_failed`
/// в `gaps.csv`, а файл читается `Reader` целиком до конца — два
/// кадра, снапшот и третий, без `ShortRead`.
#[test]
fn failed_frame_write_truncates_to_frame_boundary_and_the_part_stays_readable() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let sink_dir = tempfile::tempdir().unwrap();
    // Заголовок копится в буфере приёмника до первого сброса и уходит
    // одним `write_all` со снапшотом (№1); дельты — №2 (падает), №3.
    let (writer, part) = claim_part_with(
        sink_dir.path(),
        "SYM",
        TEST_DAY,
        1,
        TEST_TICK_E9,
        TEST_STEP_E9,
        |file| {
            FrameSink::over(Box::new(HalfFailingFile {
                inner: file,
                writes: 0,
                fail_on: 2,
            }))
        },
    )
    .unwrap();
    ctx.states[0].streams[FAST_STREAM].writer = writer;
    ctx.states[0].streams[FAST_STREAM].part = part;
    let ms = NOON_NS / 1_000_000;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, ms, true, 1)),
        Step::Ev(book_event(0, NOON_NS + 1, ms + 1, false, 2)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 10_000_000_000,
        }),
        Step::Ev(book_event(0, NOON_NS + 2, ms + 2, false, 3)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 20_000_000_000,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();
    assert_eq!(summary.frames_failed, 1);
    let gaps = std::fs::read_to_string(gaps_csv_path(&root)).unwrap();
    assert_eq!(
        gaps.matches("write_failed").count(),
        1,
        "одна строка write_failed: {gaps}"
    );
    let path = crate::commands::record::day_file_path(sink_dir.path(), "SYM", TEST_DAY, 1);
    let frames = frames_on_disk(&path);
    assert_eq!(
        frames.len(),
        2,
        "снапшот и третий кадр, обрывка второго нет"
    );
    assert_eq!(frames[1][0].exch_ts_ns, (ms + 2) * 1_000_000);
    assert_eq!(
        std::fs::metadata(&path).unwrap().len(),
        summary.bytes_written,
        "файл кончается ровно на границе последнего целого кадра"
    );
}

/// Файл, у которого окно записей `[fail_from, fail_until]` падает на
/// половине кадра («диск полон»), а до и после окна запись идёт нормально.
/// Откат к границе кадра работает (`truncate_to` настоящий) — ровно тот
/// сценарий, что даёт освободившееся место без перезапуска (A8.4).
pub(super) struct FullThenFreeFile {
    pub(super) inner: File,
    pub(super) writes: u32,
    pub(super) fail_from: u32,
    pub(super) fail_until: u32,
}

impl std::io::Write for FullThenFreeFile {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.writes += 1;
        if (self.fail_from..=self.fail_until).contains(&self.writes) {
            self.inner.write_all(&buf[..buf.len() / 2])?;
            return Err(std::io::Error::new(
                std::io::ErrorKind::StorageFull,
                "диск: нет места",
            ));
        }
        self.inner.write(buf)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.inner.flush()
    }
}

impl SinkFile for FullThenFreeFile {
    fn truncate_to(&mut self, len: u64) -> std::io::Result<()> {
        self.inner.truncate_to(len)
    }
}

/// A8.4 (2026-09-18): диск полон → снова свободен. Пока места нет, кадры не
/// ложатся (`frames_failed` растёт, строка `write_failed` на каждый), но
/// запись **не останавливается**: счётчик замирает, как только место
/// вернулось, та же часть продолжается (`-p2` нет — граница кадра не
/// потеряна), а файл читается целиком: снапшот и кадры после освобождения
/// места, без обрывков и `ShortRead`.
#[test]
fn a_full_disk_needs_no_restart_when_space_comes_back() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let sink_dir = tempfile::tempdir().unwrap();
    // Запись №1 — заголовок со снапшотом; №2–№4 — «диск полон»; с №5 место
    // есть.
    let (writer, part) = claim_part_with(
        sink_dir.path(),
        "SYM",
        TEST_DAY,
        1,
        TEST_TICK_E9,
        TEST_STEP_E9,
        |file| {
            FrameSink::over(Box::new(FullThenFreeFile {
                inner: file,
                writes: 0,
                fail_from: 2,
                fail_until: 4,
            }))
        },
    )
    .unwrap();
    ctx.states[0].streams[FAST_STREAM].writer = writer;
    ctx.states[0].streams[FAST_STREAM].part = part;

    let ms = NOON_NS / 1_000_000;
    let tick = |k: i64| Event::Tick {
        local_ts_ns: NOON_NS + k * 10_000_000_000,
    };
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, ms, true, 1)),
        // Три кадра подряд не ложатся: диск полон.
        Step::Ev(book_event(0, NOON_NS + 1, ms + 1, false, 2)),
        Step::Ev(tick(1)),
        Step::Ev(book_event(0, NOON_NS + 2, ms + 2, false, 3)),
        Step::Ev(tick(2)),
        Step::Ev(book_event(0, NOON_NS + 3, ms + 3, false, 4)),
        Step::Ev(tick(3)),
        // Место вернулось — те же писатели продолжают, без перезапуска.
        Step::Ev(book_event(0, NOON_NS + 4, ms + 4, false, 5)),
        Step::Ev(tick(4)),
        Step::Ev(book_event(0, NOON_NS + 5, ms + 5, false, 6)),
        Step::Ev(tick(5)),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx, &SystemClock).unwrap();

    assert_eq!(
        summary.frames_failed, 3,
        "три кадра потеряны за время, пока места не было"
    );
    let fast = summary
        .streams
        .iter()
        .find(|s| s.depth == crate::bybit::conn::ORDERBOOK_DEPTH)
        .unwrap();
    assert_eq!(fast.frames_failed, 3);
    let gaps = std::fs::read_to_string(gaps_csv_path(&root)).unwrap();
    assert_eq!(
        gaps.matches("write_failed").count(),
        3,
        "строка на каждую потерянную запись: {gaps}"
    );
    // Перезапуска нет: та же часть, `-p2` не появился.
    assert_eq!(ctx.states[0].streams[FAST_STREAM].part, 1);
    assert!(
        !crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 2).exists(),
        "граница кадра не терялась — новая часть не нужна"
    );
    // Файл читается целиком: снапшот и два кадра после освобождения места.
    let path = crate::commands::record::day_file_path(sink_dir.path(), "SYM", TEST_DAY, 1);
    let frames = frames_on_disk(&path);
    assert_eq!(
        frames.len(),
        3,
        "снапшот + два кадра после возврата места, обрывков нет"
    );
    assert_eq!(frames[2][0].exch_ts_ns, (ms + 5) * 1_000_000);
    assert_eq!(
        summary.records_total,
        frames.iter().map(Vec::len).sum::<usize>() as u64,
        "записей в сводке ровно столько, сколько легло в файл: потерянные не считаются записанными"
    );
    assert_eq!(
        std::fs::metadata(&path).unwrap().len(),
        summary.bytes_written,
        "байты сводки — ровно файл на диске"
    );
}
