//! Аргументы `lob session` и имена файлов суток (`claim_symbol_binlog`).

use super::*;

pub(super) fn args_with_minutes(minutes: u64) -> SessionArgs {
    SessionArgs {
        pool_instruments: Some(PathBuf::from("does/not/exist/instruments.csv")),
        all_instruments: false,
        root: PathBuf::from("does/not/exist/root"),
        minutes: Some(minutes),
        pilot_minutes: None,
        always_on: false,
        base_url: BYBIT_MAINNET_URL.to_string(),
        ntp_addr: "pool.ntp.org:123".to_string(),
    }
}

pub(super) fn args_with_pilot_minutes(pilot_minutes: u32) -> SessionArgs {
    SessionArgs {
        pool_instruments: Some(PathBuf::from("does/not/exist/instruments.csv")),
        all_instruments: false,
        root: PathBuf::from("does/not/exist/root"),
        minutes: None,
        pilot_minutes: Some(pilot_minutes),
        always_on: false,
        base_url: BYBIT_MAINNET_URL.to_string(),
        ntp_addr: "pool.ntp.org:123".to_string(),
    }
}

/// История 7 / R38 дословно: «от пяти до пятнадцати минут». Значение вне
/// `MIN_MINUTES..=MAX_MINUTES` обязано провалиться на самой первой строке
/// `run_session` — до `create_dir_all`/`load_pool` — иначе сообщение об
/// ошибке пришло бы от отсутствующего `instruments.csv`, а не от границы
/// `--minutes`, и звало бы сеть/диск раньше проверки диапазона.
#[test]
fn run_session_rejects_minutes_outside_five_to_fifteen_before_touching_disk() {
    for bad in [0, MIN_MINUTES - 1, MAX_MINUTES + 1, 60] {
        let err = run_session(&args_with_minutes(bad)).expect_err("вне 5..=15 обязана быть ошибка");
        let msg = err.to_string();
        assert!(
            msg.contains("--minutes") && msg.contains(&bad.to_string()),
            "сообщение обязано называть флаг и полученное значение {bad}: {msg}"
        );
    }
}

/// Граничные значения (5 и 15) обязаны пройти проверку диапазона и
/// провалиться дальше, на отсутствующем `instruments.csv` — не на
/// сообщении про `--minutes`. Так тест ловит и «отклоняет legit
/// значения», и «ошибка не о границе» одним прогоном.
#[test]
fn run_session_accepts_boundary_minutes_and_proceeds_past_validation() {
    for ok in [MIN_MINUTES, MAX_MINUTES] {
        let err = run_session(&args_with_minutes(ok)).expect_err("несуществующий пул — ошибка");
        let msg = err.to_string();
        assert!(
            !msg.contains("--minutes"),
            "{ok} — валидная граница, ошибка обязана быть не про --minutes: {msg}"
        );
    }
}

/// Таск 22, критерий 1: `--pilot-minutes` вне
/// `MIN_PILOT_MINUTES..=MAX_PILOT_MINUTES` — ошибка до сети/диска, тем
/// же приёмом, что `--minutes` уже проверяется.
#[test]
fn run_session_rejects_pilot_minutes_outside_range_before_touching_disk() {
    for bad in [0, MIN_PILOT_MINUTES - 1, MAX_PILOT_MINUTES + 1, 1000] {
        let err = run_session(&args_with_pilot_minutes(bad))
            .expect_err("вне диапазона обязана быть ошибка");
        let msg = err.to_string();
        assert!(
            msg.contains("--pilot-minutes") && msg.contains(&bad.to_string()),
            "сообщение обязано называть флаг и полученное значение {bad}: {msg}"
        );
    }
}

/// Граничные значения пилота (16 и 360) обязаны пройти проверку
/// диапазона и провалиться дальше, на отсутствующем `instruments.csv`.
#[test]
fn run_session_accepts_boundary_pilot_minutes_and_proceeds_past_validation() {
    for ok in [MIN_PILOT_MINUTES, MAX_PILOT_MINUTES] {
        let err =
            run_session(&args_with_pilot_minutes(ok)).expect_err("несуществующий пул — ошибка");
        let msg = err.to_string();
        assert!(
            !msg.contains("--pilot-minutes"),
            "{ok} — валидная граница, ошибка обязана быть не про --pilot-minutes: {msg}"
        );
    }
}

/// `resolve_duration`: ни `--minutes`, ни `--pilot-minutes` — вторая
/// половина условия «ровно один обязателен», которую `clap
/// conflicts_with` не выражает (он только запрещает оба разом).
#[test]
fn resolve_duration_requires_exactly_one_of_minutes_or_pilot_minutes() {
    let args = SessionArgs {
        pool_instruments: Some(PathBuf::from("does/not/exist/instruments.csv")),
        all_instruments: false,
        root: PathBuf::from("does/not/exist/root"),
        minutes: None,
        pilot_minutes: None,
        always_on: false,
        base_url: BYBIT_MAINNET_URL.to_string(),
        ntp_addr: "pool.ntp.org:123".to_string(),
    };
    let err = resolve_duration(&args).expect_err("ни один флаг не задан — обязана быть ошибка");
    let msg = err.to_string();
    assert!(
        msg.contains("--minutes") && msg.contains("--pilot-minutes"),
        "сообщение обязано назвать оба флага: {msg}"
    );
}

/// `--minutes`/`--pilot-minutes` — `clap conflicts_with`: заданы оба
/// разом на самом парсере, не на уже собранной структуре
/// (`resolve_duration` эту комбинацию в принципе не видит).
#[test]
fn cli_rejects_minutes_and_pilot_minutes_given_together() {
    #[derive(Debug, clap::Parser)]
    struct TestCli {
        #[command(subcommand)]
        cmd: super::super::super::LobCommand,
    }
    use clap::Parser as _;
    let err = TestCli::try_parse_from([
        "t",
        "session",
        "--pool-instruments",
        "instruments.csv",
        "--root",
        "root",
        "--minutes",
        "10",
        "--pilot-minutes",
        "30",
    ])
    .expect_err("--minutes и --pilot-minutes разом обязаны конфликтовать");
    assert_eq!(
        err.kind(),
        clap::error::ErrorKind::ArgumentConflict,
        "{err}"
    );
}

/// `session.json.debug` — чистая функция от длительности (таск 17,
/// пункт 5б): `true`, пока сессия короче часа (`CLAUDE.md`: тестовый
/// прогон — фаза отладки, результат не данные), `false` начиная ровно с
/// часа. `MAX_MINUTES = 15` не даёт этой ветке случиться через сегодняшний
/// CLI — тест бьёт по самой функции, не по `run_session`.
#[test]
fn is_debug_session_true_under_an_hour_false_at_and_past_it() {
    assert!(is_debug_session(0));
    assert!(is_debug_session(3_599));
    assert!(!is_debug_session(3_600));
    assert!(!is_debug_session(3_601));
}

// -----------------------------------------------------------------
// Таск 19: `lob session` пишет `<SYMBOL>-<день>.binlog`, не
// `<SYMBOL>.binlog` — то имя, которое читают `verify`/`levels`/
// `markout` (слепая приёмка G4).
// -----------------------------------------------------------------

/// `claim_symbol_binlog` первой сессии суток обязана давать часть 1 —
/// ровно то же имя, что и прежняя жёсткая `session_binlog_path`
/// (`day_file_path(.., 1)`), до таска 22 единственный случай.
#[test]
fn claim_symbol_binlog_gives_part_one_on_a_fresh_day() {
    let dir = tempfile::tempdir().unwrap();
    let (_writer, part) = claim_symbol_binlog(
        dir.path(),
        "SOLUSDT",
        "2025-09-13",
        TEST_TICK_E9,
        TEST_STEP_E9,
    )
    .unwrap();
    assert_eq!(part, 1);
    assert!(
        crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2025-09-13", 1).is_file()
    );
}

// -----------------------------------------------------------------
// Таск 22: несколько сессий в те же сутки — `claim_symbol_binlog`
// обязана отдать следующий свободный номер, не затирая прежний файл;
// `session_binlog_for` (`mod.rs`) обязана прочитать обе части подряд.
// -----------------------------------------------------------------

/// Критерий приёмки 2 дословно: «две сессии подряд в один каталог дают
/// два файла и обе читаются». Читает содержимое обоих файлов (не только
/// их наличие) через `session_binlog_for`, чтобы отличить «часть 2
/// существует» от «часть 2 несёт свои собственные, а не чужие данные».
#[test]
fn two_sessions_in_a_row_into_one_directory_produce_two_parts_both_readable() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let day = "2026-09-08";

    let (mut w1, part1) =
        claim_symbol_binlog(root, "SOLUSDT", day, TEST_TICK_E9, TEST_STEP_E9).unwrap();
    w1.write_frame(&[rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 0, 100, 1)])
        .unwrap();
    w1.flush().unwrap();

    let (mut w2, part2) =
        claim_symbol_binlog(root, "SOLUSDT", day, TEST_TICK_E9, TEST_STEP_E9).unwrap();
    w2.write_frame(&[rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 0, 200, 2)])
        .unwrap();
    w2.flush().unwrap();

    assert_eq!(part1, 1, "первая сессия суток — часть 1, не затёрта");
    assert_eq!(part2, 2, "вторая сессия суток — новая часть, не часть 1");

    let paths = super::super::super::session_binlog_for(root, "SOLUSDT").unwrap();
    assert_eq!(paths.len(), 2, "обе части присутствуют");
    assert_eq!(
        paths[0],
        crate::commands::record::day_file_path(root, "SOLUSDT", day, 1)
    );
    assert_eq!(
        paths[1],
        crate::commands::record::day_file_path(root, "SOLUSDT", day, 2)
    );

    // "обе читаются": часть 1 несёт price_ticks=100, часть 2 — 200, в
    // том порядке, в котором `session_binlog_for` их вернула.
    for (path, expected_tick) in [(&paths[0], 100i64), (&paths[1], 200i64)] {
        let data = std::fs::read(path).unwrap();
        let mut reader = crate::binlog::Reader::open(&data[..]).unwrap();
        let frame = reader
            .read_frame()
            .unwrap()
            .expect("часть обязана нести свой кадр");
        assert_eq!(frame[0].price_ticks, expected_tick);
    }
}

/// Слепая приёмка G4: каталог, размеченный так, как `run_session`
/// раскладывает файлы начиная с этого таска (`<SYMBOL>-<день>.binlog`,
/// день часть 1 — та же `day_file_path`, что доказал предыдущий тест),
/// читается `verify` без ручного переименования. Фикстура
/// собрана публичной `test_support::write_day` (тот же шов, что
/// `pilot.rs::process_instrument` уже использует для этой пары команд),
/// не вызовом `run_session` целиком — та сама открывает `LiveFeed`
/// (живой сокет), офлайн-тестам сеть недоступна (`CLAUDE.md`).
#[test]
fn directory_named_like_a_fresh_session_reads_through_verify_untouched() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let day = "2026-09-08";

    super::super::super::test_support::write_day(
        root,
        "SOLUSDT",
        day,
        &super::super::super::test_support::three_level_frames(),
    );
    std::fs::write(
        crate::commands::record::instruments_csv_path(root),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();

    let vs = crate::bybit::verify::run_verify(&crate::bybit::verify::VerifyArgs {
        symbol: "SOLUSDT".to_string(),
        root: root.to_path_buf(),
        keep_going: false,
        windows_out: None,
    })
    .expect("verify обязан найти суточный файл сессии без переименования");
    assert_eq!(vs.files, 1, "ровно один суточный файл сессии");
}
