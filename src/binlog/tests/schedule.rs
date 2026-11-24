//! Версия 4: расписание шагов внутри суток (TK-037).

use super::*;

// -----------------------------------------------------------------
// Версия 4: расписание шагов внутри суток (TK-037).
// -----------------------------------------------------------------

/// Расписание фикстуры: на начало суток шаги крупнее заголовка, с метки 3000 —
/// ровно шаги заголовка (мельчайшие).
pub(super) fn schedule() -> Vec<StepAt> {
    vec![
        StepAt {
            ts_ns: 0,
            tick_e9: 10 * TICK_E9,
            step_e9: 5 * STEP_E9,
        },
        StepAt {
            ts_ns: 3_000,
            tick_e9: TICK_E9,
            step_e9: STEP_E9,
        },
    ]
}

pub(super) fn write_all_v4(hdr: Header, sched: &[StepAt], frames: &[Vec<Record>]) -> Vec<u8> {
    let mut w = Writer::create_with_schedule(Vec::new(), hdr, sched, 1).unwrap();
    for frame in frames {
        w.write_frame(frame).unwrap();
    }
    w.into_inner()
}

/// Заголовок v4, собранный независимо от кода писателя: раскладка из доки
/// модуля (`magic|4|tick|step|max|n u16|n×(ts,tick,step)`).
pub(super) fn raw_v4_header(hdr: Header, sched: &[StepAt]) -> Vec<u8> {
    let mut b = Vec::new();
    b.extend_from_slice(&MAGIC);
    b.push(4);
    b.extend_from_slice(&hdr.tick_e9.to_le_bytes());
    b.extend_from_slice(&hdr.step_e9.to_le_bytes());
    b.extend_from_slice(&hdr.max_records_per_frame.to_le_bytes());
    b.extend_from_slice(&(sched.len() as u16).to_le_bytes());
    for s in sched {
        b.extend_from_slice(&s.ts_ns.to_le_bytes());
        b.extend_from_slice(&s.tick_e9.to_le_bytes());
        b.extend_from_slice(&s.step_e9.to_le_bytes());
    }
    b
}

pub(super) fn v4_frames() -> Vec<Vec<Record>> {
    vec![
        vec![
            rec(ev_snapshot_bid(), 10, 20, 100, 5),
            rec(ev_snapshot_bid(), 10, 20, 99, 3),
        ],
        vec![rec(ev_delta_ask(), 5_000, 5_100, 105, 0)],
    ]
}

/// Файлы v2/v3 расписания не несут: `step_schedule()` пуст, шаг один — из
/// `header()`.
#[test]
fn v3_and_v2_files_have_an_empty_step_schedule() {
    let bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 10, 20, 1, 1)]]);
    let r = Reader::open(&bytes[..]).unwrap();
    assert_eq!(r.version(), VERSION);
    assert!(r.step_schedule().is_empty());

    let payload = v2_payload_with_dead_fields(&[rec(ev_trade_buy(), 30, 40, 101, 2)], &[(0, 0, 0)]);
    let v2 = file_with_version(VERSION_V2, header(), std::slice::from_ref(&payload));
    let r = Reader::open(&v2[..]).unwrap();
    assert_eq!(r.version(), VERSION_V2);
    assert!(r.step_schedule().is_empty());
}

/// Обычный конструктор по-прежнему пишет v3 побайтово как до v4: заголовок —
/// литерал, не результат кода писателя.
#[test]
fn v3_writer_header_bytes_are_unchanged() {
    let want: [u8; 25] = [
        b'A', b'B', b'L', b'G', 3, // magic, версия
        0xA0, 0x86, 0x01, 0, 0, 0, 0, 0, // tick_e9 = 100_000
        0x40, 0x42, 0x0F, 0, 0, 0, 0, 0, // step_e9 = 1_000_000
        0x40, 0x42, 0x0F, 0, // max_records_per_frame = 1_000_000
    ];
    let bytes = write_all(header(), &[]);
    assert_eq!(bytes, want);
    assert_eq!(
        write_all(header(), &[vec![rec(ev_snapshot_bid(), 10, 20, 1, 1)]])[..25],
        want
    );
}

/// Круговой обход v4: заголовок (шаги — мельчайшие), расписание и кадры.
#[test]
fn v4_round_trips_header_schedule_and_frames() {
    let sched = schedule();
    let frames = v4_frames();
    let bytes = write_all_v4(header(), &sched, &frames);
    assert_eq!(
        bytes[..raw_v4_header(header(), &sched).len()],
        raw_v4_header(header(), &sched)[..],
        "раскладка заголовка v4 обязана совпасть с описанной в доке модуля"
    );

    let mut r = Reader::open(&bytes[..]).unwrap();
    assert_eq!(r.version(), VERSION_V4);
    assert_eq!(r.header(), header());
    assert_eq!(r.step_schedule(), &sched[..]);
    let mut got = Vec::new();
    while let Some(f) = r.read_frame().unwrap() {
        got.push(f);
    }
    assert_eq!(got, frames);
    assert!(r.read_frame().unwrap().is_none());
}

/// Невалидное расписание писатель отвергает до первого байта.
#[test]
fn writer_rejects_an_invalid_schedule() {
    let at = |ts_ns, tick_e9, step_e9| StepAt {
        ts_ns,
        tick_e9,
        step_e9,
    };
    let cases: Vec<(Vec<StepAt>, usize)> = vec![
        (vec![], 0),
        (vec![at(0, TICK_E9, STEP_E9)], 0),
        // метки не по возрастанию и равные
        (vec![at(5, TICK_E9, STEP_E9), at(4, TICK_E9, STEP_E9)], 1),
        (vec![at(5, TICK_E9, STEP_E9), at(5, TICK_E9, STEP_E9)], 1),
        // тик и шаг не кратны шагам заголовка
        (
            vec![at(0, TICK_E9, STEP_E9), at(1, TICK_E9 + 1, STEP_E9)],
            1,
        ),
        (
            vec![at(0, TICK_E9, STEP_E9), at(1, TICK_E9, STEP_E9 + 1)],
            1,
        ),
        // мельче шага заголовка — тоже не кратен
        (
            vec![at(0, TICK_E9 / 2, STEP_E9), at(1, TICK_E9, STEP_E9)],
            0,
        ),
        // не положительны
        (vec![at(0, 0, STEP_E9), at(1, TICK_E9, STEP_E9)], 0),
        (vec![at(0, TICK_E9, STEP_E9), at(1, TICK_E9, -STEP_E9)], 1),
    ];
    for (sched, index) in cases {
        let err = Writer::create_with_schedule(Vec::new(), header(), &sched, 1).unwrap_err();
        assert!(
            matches!(err, BinlogError::InvalidSchedule { index: i, .. } if i == index),
            "расписание {sched:?}: ожидалась InvalidSchedule на записи {index}, получено {err:?}"
        );
    }
    let err = Writer::create_with_schedule(
        Vec::new(),
        Header {
            tick_e9: 0,
            ..header()
        },
        &schedule(),
        1,
    )
    .unwrap_err();
    assert!(matches!(err, BinlogError::InvalidHeader { .. }));
}

/// То же расписание на чтении: сфабрикованный или испорченный заголовок v4
/// даёт ошибку, а не молчаливо принятые шаги.
#[test]
fn reader_rejects_an_invalid_schedule() {
    let mut bad = schedule();
    bad[1].ts_ns = bad[0].ts_ns;
    let bytes = raw_v4_header(header(), &bad);
    assert!(matches!(
        Reader::open(&bytes[..]).unwrap_err(),
        BinlogError::InvalidSchedule { index: 1, .. }
    ));

    let mut bad = schedule();
    bad[0].tick_e9 += 1;
    let bytes = raw_v4_header(header(), &bad);
    assert!(matches!(
        Reader::open(&bytes[..]).unwrap_err(),
        BinlogError::InvalidSchedule { index: 0, .. }
    ));

    // n = 0 и n = 1: расписание v4 обязано нести смену шага.
    for n in 0..2 {
        let bytes = raw_v4_header(header(), &schedule()[..n]);
        assert!(matches!(
            Reader::open(&bytes[..]).unwrap_err(),
            BinlogError::InvalidSchedule { index: 0, .. }
        ));
    }
}

/// Усечённый заголовок v4 на любой длине — `TruncatedHeader`, не паника и не
/// принятое неполное расписание.
#[test]
fn truncated_v4_header_is_an_error_at_every_cut() {
    let sched = schedule();
    let full = write_all_v4(header(), &sched, &v4_frames());
    let header_len = raw_v4_header(header(), &sched).len();
    for cut in 0..header_len {
        let err = Reader::open(&full[..cut]).unwrap_err();
        assert!(
            matches!(err, BinlogError::TruncatedHeader { .. }),
            "обрезка до {cut} байт: ожидался TruncatedHeader, получено {err:?}"
        );
    }
    // Смещения: нет счётчика, обрыв внутри записи.
    assert_eq!(
        Reader::open(&full[..HEADER_LEN]).unwrap_err(),
        BinlogError::TruncatedHeader {
            got: HEADER_LEN,
            want: HEADER_LEN + SCHEDULE_COUNT_LEN,
        }
    );
    assert_eq!(
        Reader::open(&full[..HEADER_LEN + SCHEDULE_COUNT_LEN + 10]).unwrap_err(),
        BinlogError::TruncatedHeader {
            got: HEADER_LEN + SCHEDULE_COUNT_LEN + 10,
            want: header_len,
        }
    );
    // Счётчик объявляет больше, чем на диске: усечение, не чтение за границей.
    let mut huge = raw_v4_header(header(), &sched);
    huge[HEADER_LEN..HEADER_LEN + 2].copy_from_slice(&u16::MAX.to_le_bytes());
    assert!(matches!(
        Reader::open(&huge[..]).unwrap_err(),
        BinlogError::TruncatedHeader { .. }
    ));
}

#[test]
fn non_positive_tick_or_step_is_rejected_on_write_and_read() {
    assert!(Writer::create(
        Vec::new(),
        Header {
            tick_e9: 0,
            step_e9: 1,
            max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
        },
        zstd::DEFAULT_COMPRESSION_LEVEL
    )
    .is_err());

    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
    // Затираем tick_e9 заголовка нулём напрямую в байтах.
    bytes[5..13].copy_from_slice(&0i64.to_le_bytes());
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert!(matches!(err, BinlogError::InvalidHeader { .. }));
}

/// `max_records_per_frame = 0` — тот же класс ошибки, что нулевой/
/// отрицательный `tick_e9`/`step_e9` выше: заголовок, а не программный
/// аргумент, поэтому и здесь `Result`, а не паника (`validate_header`
/// общая на запись и чтение).
#[test]
fn zero_max_records_per_frame_is_rejected_on_write_and_read() {
    assert!(Writer::create(
        Vec::new(),
        Header {
            tick_e9: TICK_E9,
            step_e9: STEP_E9,
            max_records_per_frame: 0,
        },
        zstd::DEFAULT_COMPRESSION_LEVEL
    )
    .is_err());

    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
    // Затираем max_records_per_frame заголовка нулём напрямую в байтах
    // (смещение 21..25, см. `Writer::create`).
    bytes[21..25].copy_from_slice(&0u32.to_le_bytes());
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert!(matches!(err, BinlogError::InvalidHeader { .. }));
}
