/// Заголовок — такие же данные с диска, как и поле длины кадра, и потолок,
/// выведенный только из него, испорченным заголовком обходится. Проверяется
/// то, что второй потолок существует и связывает: `u32::MAX` записей дали бы
/// тридцать с лишним гигабайт, а обязаны упереться в границу из Decision 23.
#[test]
fn a_corrupt_header_cannot_raise_the_ceiling_past_the_day_budget() {
    let honest = super::max_frame_payload_bytes(1_000);
    assert_eq!(
        honest,
        1_000 * super::MIN_RECORD_LEN + super::FRAME_EPOCH_LEN,
        "на честном значении потолок обязан считаться ровно по записям"
    );

    let corrupt = super::max_frame_payload_bytes(u32::MAX);
    assert_eq!(
        corrupt,
        super::HARD_PAYLOAD_CEILING,
        "испорченный заголовок обязан упираться в границу, а не в своё произведение"
    );
    assert!(
        (u32::MAX as usize) * super::MIN_RECORD_LEN > corrupt,
        "иначе тест не проверяет ничего: произведение обязано быть больше границы"
    );
}

use super::*;
use crate::alloc_count;

mod ceilings;
mod codec;
mod degenerate;
mod round_trip;
mod schedule;
use ceilings::*;

const TICK_E9: i64 = 100_000; // 0.0001, как в тестах book.rs
const STEP_E9: i64 = 1_000_000; // 0.001

/// Потолок заголовка по умолчанию для тестов, которым сам потолок не
/// важен — round trip, границы суток, усечение и т.п. Не значение,
/// которое использует рекордер (то назначает вызывающий при создании
/// файла, не этот модуль), а запас с большим отступом над самым
/// крупным кадром, который где-либо в этом наборе тестов пишется
/// одним вызовом `write_frame` через общий `header()` — крупнейший тут
/// `PER_FRAME = 20_000` в `round_trips_one_million_events` и в тестах
/// аллокаций ниже. Тесты про сам потолок (`..._exceeds_the_header_ceiling`,
/// `..._at_exactly_the_maximum`, `writer_refuses_frame_exceeding_...`)
/// собирают свой `Header` с маленьким явным значением, а не берут этот.
const DEFAULT_TEST_MAX_RECORDS_PER_FRAME: u32 = 1_000_000;

fn header() -> Header {
    Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
    }
}

/// Флаг снапшота бид-уровня, взятый у крейта, а не выдуманный: доказывает
/// «поля — надмножество `Event`» на конкретном значении, которое реально
/// использует экспортёр (шаг 6.1), а не произвольное число теста.
fn ev_snapshot_bid() -> u64 {
    hftbacktest::types::LOCAL_BID_DEPTH_SNAPSHOT_EVENT
}
fn ev_delta_ask() -> u64 {
    hftbacktest::types::LOCAL_ASK_DEPTH_EVENT
}
fn ev_trade_buy() -> u64 {
    hftbacktest::types::LOCAL_BUY_TRADE_EVENT
}

fn rec(ev: u64, exch_ts_ns: i64, local_ts_ns: i64, price_ticks: i64, qty_lots: i64) -> Record {
    Record {
        ev,
        exch_ts_ns,
        local_ts_ns,
        price_ticks,
        qty_lots,
        block: false,
        rpi: false,
    }
}

fn write_all(hdr: Header, frames: &[Vec<Record>]) -> Vec<u8> {
    let mut w = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    for frame in frames {
        w.write_frame(frame).unwrap();
    }
    w.into_inner()
}

fn read_all(bytes: &[u8]) -> (Header, Vec<Vec<Record>>) {
    let mut r = Reader::open(bytes).unwrap();
    let hdr = r.header();
    let mut frames = Vec::new();
    while let Some(f) = r.read_frame().unwrap() {
        frames.push(f);
    }
    (hdr, frames)
}
