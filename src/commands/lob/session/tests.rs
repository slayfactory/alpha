use super::*;
// Элементы подмодулей, которые сам `session.rs` не импортирует — нужны только тестам
// (разрезка W6, ревью 23.09: `stream_dir`/`claim_symbol_binlog`/`open_symbol_state` живут
// в `session::parts`, `is_debug_session` — в `session::args`, `StreamState` — в `session::sink`).
use super::args::is_debug_session;
use super::parts::{claim_symbol_binlog, open_symbol_state, stream_dir};
use super::resources::resource_sample_period;
use super::sink::{FrameSink, SinkFile, StreamState};
use crate::binlog::Header;
use crate::binlog::Record;
use crate::binlog::Writer;
use crate::bybit::conn::{DEEP_STREAM, SUBSCRIBED_DEPTHS};
use crate::bybit::rest::BYBIT_MAINNET_URL;
use crate::commands::record::claim_part_with;
use crate::feed::replay::ReplayFeed;
use hftbacktest::types::{
    LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
    LOCAL_BUY_TRADE_EVENT,
};
use std::collections::VecDeque;
use std::fs::File;

mod always_on;
mod cli;
mod write_path;
use always_on::*;
mod events;
mod failures;
mod pool_add;
use pool_add::*;
mod pool_remove;

const TEST_TICK_E9: i64 = 1_000_000;
const TEST_STEP_E9: i64 = 1_000_000;

fn rec(ev: u64, exch_ts_ns: i64, price_ticks: i64, qty_lots: i64) -> Record {
    Record {
        ev,
        exch_ts_ns,
        local_ts_ns: exch_ts_ns,
        price_ticks,
        qty_lots,
        block: false,
        rpi: false,
    }
}

/// One resting snapshot (bid + ask, never touched again) followed by
/// `pairs` repetitions of (bid-depth delta, trade) — same shape as the
/// `record.rs::steady_events_allocate_nothing` precedent ("форма и
/// magnitudes не меняются, меняются только монотонные счётчики"), built
/// as a real binlog so the test drives `write_market_event` through
/// `ReplayFeed`, not by calling it directly — that is the shape ТУПИК 1
/// (handoff-04-1) named: "синтетический `ReplayFeed`", not a synthetic
/// `Event` fed straight to the function under test.
fn build_synthetic_binlog(pairs: usize) -> Vec<u8> {
    const PER_FRAME: usize = 20_000;
    // `max_records_per_frame` sizes the reader's decompression-bomb
    // ceiling as `declared_max * MIN_RECORD_LEN` (`binlog::mod::
    // max_frame_payload_bytes`) — a ceiling computed from records at
    // their *minimum* varint size, not their real one. Declaring it
    // equal to `PER_FRAME` made every chunk here reject once real
    // (non-zero-delta) encoding pushed a 20,000-record frame past that
    // tight ceiling (found by bisection: broke between 2,000 and 5,000
    // pairs). `binlog::tests::round_trips_one_million_events` sets this
    // field to 1,000,000 while writing the very same 20,000-record
    // chunks — the same headroom, reused here rather than reinvented.
    let header = Header {
        tick_e9: TEST_TICK_E9,
        step_e9: TEST_STEP_E9,
        max_records_per_frame: 1_000_000,
    };
    let mut w = Writer::create(Vec::new(), header, 0).unwrap();
    w.write_frame(&[
        rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 0, 100, 5),
        rec(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, 0, 110, 5),
    ])
    .unwrap();

    let mut buf: Vec<Record> = Vec::with_capacity(PER_FRAME);
    for k in 0..pairs {
        let ts = 1_000_000 + (2 * k as i64 + 1) * 1_000;
        buf.push(rec(LOCAL_BID_DEPTH_EVENT, ts, 100, 5));
        buf.push(rec(LOCAL_BUY_TRADE_EVENT, ts + 1_000, 100, 1));
        if buf.len() >= PER_FRAME {
            w.write_frame(&buf).unwrap();
            buf.clear();
        }
    }
    if !buf.is_empty() {
        w.write_frame(&buf).unwrap();
    }
    w.into_inner()
}

const TEST_DAY: &str = "2026-09-12";

fn test_member(symbol: &str) -> PoolMember {
    PoolMember {
        symbol: symbol.to_string(),
        tick_e9: TEST_TICK_E9,
        step_e9: TEST_STEP_E9,
    }
}

fn fresh_state(root: &Path) -> SymbolState {
    open_symbol_state(root, &test_member("SYM"), TEST_DAY).unwrap()
}

/// Состояние инструмента с уже открытыми файлами **обоих** потоков: заданные
/// `writer`/`part` — быстрый поток (как было до T45), глубокий открывается
/// рядом, в `deep/` того же корня. Тесты этих двух потоков не смешивают:
/// адресуются к `state.streams[FAST_STREAM]`.
fn state_with_writer(
    root: &Path,
    symbol: &str,
    writer: Writer<FrameSink>,
    part: u32,
) -> SymbolState {
    let day_index = crate::commands::record::day_index_of_day_str(TEST_DAY).unwrap();
    let deep_dir = stream_dir(root, DEEP_STREAM);
    std::fs::create_dir_all(&deep_dir).unwrap();
    let (deep_writer, deep_part) =
        claim_symbol_binlog(&deep_dir, symbol, TEST_DAY, TEST_TICK_E9, TEST_STEP_E9).unwrap();
    let member = test_member(symbol);
    SymbolState {
        streams: [
            StreamState::new(
                SUBSCRIBED_DEPTHS[FAST_STREAM],
                &member,
                writer,
                part,
                day_index,
            ),
            StreamState::new(
                SUBSCRIBED_DEPTHS[DEEP_STREAM],
                &member,
                deep_writer,
                deep_part,
                day_index,
            ),
        ],
        member,
        active: true,
    }
}
