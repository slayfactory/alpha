//! Round trip, чтение суток с первого байта и усечённый хвост.

use super::*;

// -----------------------------------------------------------------
// Базовый round trip.
// -----------------------------------------------------------------

#[test]
fn header_round_trips() {
    let bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 10, 20, 1, 1)]]);
    let mut r = Reader::open(&bytes[..]).unwrap();
    assert_eq!(r.header(), header());
    assert!(r.read_frame().unwrap().is_some());
    assert!(r.read_frame().unwrap().is_none());
}

#[test]
fn records_round_trip_across_several_frames() {
    let frame0 = vec![
        rec(ev_snapshot_bid(), 1_000, 1_500, 100, 5),
        rec(ev_snapshot_bid(), 1_000, 1_600, 99, 3),
        rec(ev_delta_ask(), 1_000, 1_700, 101, 4),
    ];
    let frame1 = vec![
        rec(ev_delta_ask(), 5_000, 5_100, 105, 0),
        rec(ev_trade_buy(), 5_050, 5_200, 100, 2),
    ];
    let bytes = write_all(header(), &[frame0.clone(), frame1.clone()]);
    let (hdr, frames) = read_all(&bytes);
    assert_eq!(hdr, header());
    assert_eq!(frames, vec![frame0, frame1]);
}

/// Второй кадр не должен зависеть от состояния первого: дельта внутри
/// него считается заново от нуля. Если бы состояние переносилось между
/// кадрами, эта запись расшифровалась бы в другую цену.
#[test]
fn delta_state_resets_at_frame_boundary() {
    let frame0 = vec![rec(ev_snapshot_bid(), 0, 0, 1_000_000, 500)];
    let frame1 = vec![rec(ev_delta_ask(), 1, 1, 7, 2)];
    let bytes = write_all(header(), &[frame0, frame1]);
    let (_, frames) = read_all(&bytes);
    assert_eq!(frames[1][0].price_ticks, 7);
    assert_eq!(frames[1][0].qty_lots, 2);
}

/// Запись без событий — no-op: в потоке не появляется ни одного байта,
/// а не кадр с нулём записей.
#[test]
fn writing_an_empty_slice_writes_nothing() {
    let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    w.write_frame(&[]).unwrap();
    let bytes = w.into_inner();
    assert_eq!(bytes.len(), HEADER_LEN);
}

// -----------------------------------------------------------------
// Требование 1: round trip на 10^6 событий (done-condition шага 0.2).
// -----------------------------------------------------------------

#[test]
fn round_trips_one_million_events() {
    const TOTAL: usize = 1_000_000;
    const PER_FRAME: usize = 20_000;

    let mut w = Writer::create(Vec::new(), header(), 1).unwrap();
    let mut written = 0usize;
    let mut expected: Vec<Record> = Vec::with_capacity(TOTAL);
    while written < TOTAL {
        let n = PER_FRAME.min(TOTAL - written);
        let mut frame = Vec::with_capacity(n);
        for i in 0..n {
            let idx = (written + i) as i64;
            let ev = if idx % 2 == 0 {
                ev_delta_ask()
            } else {
                ev_trade_buy()
            };
            // Знакопеременный шаг: проверяет, что дельты уверенно берут
            // и рост, и падение цены/размера внутри одного кадра.
            let r = rec(
                ev,
                1_700_000_000_000_000_000 + idx * 1_000,
                1_700_000_000_000_500_000 + idx * 1_000,
                1_000_000 + if idx % 3 == 0 { idx } else { -idx },
                100 + (idx % 97),
            );
            frame.push(r);
            expected.push(r);
        }
        w.write_frame(&frame).unwrap();
        written += n;
    }
    let bytes = w.into_inner();

    let (hdr, frames) = read_all(&bytes);
    assert_eq!(hdr, header());
    let got: Vec<Record> = frames.into_iter().flatten().collect();
    assert_eq!(got.len(), TOTAL);
    assert_eq!(got, expected);
}

// -----------------------------------------------------------------
// Требование архитектуры (A2 через план): один и тот же бинлог,
// прочитанный дважды, даёт побайтово одинаковый вывод. Тест ловит
// недетерминизм, прокрашивающийся в тракт декодирования (порядок обхода
// хеш-таблиц, время, RNG): читает ОДНИ И ТЕ ЖЕ байты двумя независимыми
// Reader и сравнивает покадрово, а не только итогом.
// -----------------------------------------------------------------

#[test]
fn same_bytes_twice_give_byte_identical_frames() {
    let day = write_all(
        header(),
        &[
            vec![
                rec(ev_snapshot_bid(), 0, 1, 10, 10),
                rec(ev_snapshot_bid(), 0, 1, 9, 3),
            ],
            vec![
                rec(ev_delta_ask(), 1_000, 1_001, 11, 9),
                rec(ev_trade_buy(), 1_000, 1_001, 11, 1),
                rec(ev_delta_ask(), 1_000, 1_001, 21, 19),
            ],
        ],
    );
    let (_, first) = read_all(&day);
    let (_, second) = read_all(&day);
    assert_eq!(first.len(), second.len(), "число кадров обязано совпасть");
    for (a, b) in first.iter().zip(second.iter()) {
        assert_eq!(a, b, "кадры обязаны совпасть побайтово-полностью");
    }
}

/// Сид-корпус фаззера (`fuzz/corpus/reader`) гоняется и в стабильном
/// наборе: ни один вход не вправе ронять читатель — ни паникой, ни
/// бесконечностью. Потолок кадров тот же, что в фазз-таргете.
#[test]
fn fuzz_seed_corpus_never_panics_nor_hangs() {
    let mut names: Vec<_> = std::fs::read_dir("fuzz/corpus/reader")
        .expect("сид-корпус обязан существовать")
        .map(|e| e.unwrap().path())
        .collect();
    names.sort();
    assert!(!names.is_empty(), "пустой корпус ничего не проверяет");
    for path in names {
        let data = std::fs::read(&path).unwrap();
        let Ok(mut reader) = Reader::open(&data[..]) else {
            continue;
        };
        let _ = reader.header();
        let mut frames = 0usize;
        while let Ok(Some(_)) = reader.read_frame() {
            frames += 1;
            assert!(frames < 4096, "вход {:?} не заканчивается", path);
        }
    }
}

// -----------------------------------------------------------------
// Требование 2: сутки читаются с первого байта без предыдущих суток.
// -----------------------------------------------------------------

/// Два «дня» с **разными** `tickSize` (биржа сменила шаг между ними,
/// как и предупреждает Decision 7) кодируются независимо. Открытие
/// второго не видит байт первого вообще — они лежат в отдельных `Vec`.
#[test]
fn each_day_file_decodes_standing_alone() {
    let day1_header = Header {
        tick_e9: 100_000,
        step_e9: 1_000_000,
        max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
    };
    let day2_header = Header {
        tick_e9: 50_000, // другой шаг цены — как после смены на бирже
        step_e9: 2_000_000,
        max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
    };
    let day1 = write_all(day1_header, &[vec![rec(ev_snapshot_bid(), 0, 1, 10, 10)]]);
    let day2 = write_all(day2_header, &[vec![rec(ev_snapshot_bid(), 0, 1, 20, 20)]]);

    // День 2 декодируется из собственного среза, день 1 в эту функцию
    // не передаётся вообще — не только логически, а буквально.
    let (hdr2, frames2) = read_all(&day2);
    assert_eq!(hdr2, day2_header);
    assert_eq!(frames2[0][0].price_ticks, 20);

    let (hdr1, frames1) = read_all(&day1);
    assert_eq!(hdr1, day1_header);
    assert_eq!(frames1[0][0].price_ticks, 10);
}

// -----------------------------------------------------------------
// Требование 3: усечённый последний кадр — короткое чтение.
// -----------------------------------------------------------------

#[test]
fn truncated_final_frame_is_a_short_read_not_corruption() {
    let good = vec![rec(ev_snapshot_bid(), 0, 1, 10, 10)];
    let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    w.write_frame(&good).unwrap();
    w.write_frame(&[rec(ev_delta_ask(), 5, 6, 11, 9)]).unwrap();
    let mut bytes = w.into_inner();

    // Обрезаем файл на середине второго кадра: первый читается штатно,
    // на втором должен быть именно `ShortRead`, не паника и не пустой
    // результат, выданный за нормальный конец файла.
    bytes.truncate(bytes.len() - 3);

    let mut r = Reader::open(&bytes[..]).unwrap();
    assert!(r.read_frame().unwrap().is_some(), "первый кадр цел");
    let err = r.read_frame().unwrap_err();
    assert!(
        matches!(err, BinlogError::ShortRead { .. }),
        "ожидался ShortRead, получено {err:?}"
    );
}

/// A4 (2026-09-17): тот же обрезанный хвост, но **мягким** чтением — это конец
/// прочитанного с флагом `truncated_tail`, а не отказ команды. Живой корень
/// читают, пока коллектор пишет (`COMMANDS.md`), а запись кладёт кадр не одним
/// `write`; строгий `read_frame` при этом обязан остаться строгим — мягкость
/// свойство вызывающего, а не чтения.
#[test]
fn soft_read_stops_at_a_truncated_tail_and_flags_it() {
    let good = vec![rec(ev_snapshot_bid(), 0, 1, 10, 10)];
    let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    w.write_frame(&good).unwrap();
    w.write_frame(&[rec(ev_delta_ask(), 5, 6, 11, 9)]).unwrap();
    let mut bytes = w.into_inner();
    bytes.truncate(bytes.len() - 3);

    let mut soft = Reader::open(&bytes[..]).unwrap();
    assert!(!soft.truncated_tail(), "до чтения флага нет");
    assert!(
        soft.read_frame_soft().unwrap().is_some(),
        "целый кадр читается"
    );
    assert!(!soft.truncated_tail(), "целый кадр флага не поднимает");
    assert!(
        soft.read_frame_soft().unwrap().is_none(),
        "обрезанный хвост — конец прочитанного, не ошибка"
    );
    assert!(soft.truncated_tail(), "и об этом сказано флагом");

    let mut strict = Reader::open(&bytes[..]).unwrap();
    assert!(strict.read_frame().unwrap().is_some());
    assert!(
        matches!(strict.read_frame(), Err(BinlogError::ShortRead { .. })),
        "`read_frame` остаётся строгим: мягкость выбирает вызывающий"
    );
}
