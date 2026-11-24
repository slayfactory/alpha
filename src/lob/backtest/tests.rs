//! # Эталон и «байт в байт» (С-07, ревью 10.10)
//!
//! **Эталонный путь «клетка × сутки»** — `bounce-grid --cells` на старом бинарнике (или на этом при умолчаниях):
//! все пути ускорения ниже выключены (с TK-174 флаги b14flag — код, не env; выключенный путь живёт только в тестах), `--hold-step poll` (опрос 10 мс), `--busy-skip` как в клетке, непрерывный
//! драйвер `drive_bounce`. Любой флаг обязан давать побайтно тот же выход клетки. Порядок проверки флага: юнит-тест
//! на синтетике (если есть) → гейт на calc одним `--cells` на сутки, `diff -r` пустой (`tools/compute/*gate*.sh`).
//!
//! | флаг / режим | юнит-тест «байт в байт» | гейт на calc |
//! |---|---|---|
//! | `--hold-step skip` | `hold_skip_matches_polling_byte_for_byte` | — |
//! | `FORCE_ON` (тест) | `fast_hold_handoff_equals_continuous`, `group_fast_hold_matches_group_without_it` | — |
//! | `ALPHA_ADMIT_SOA=1` | `bounce_grid::tests` («`admits` по строкам `AdmitRow` …») | — |
//! | skip_same, skip_nosignal, holds_memo, admit_cache, sig_cache (всегда вкл, TK-174) | **нет юнит-теста — дыра**, закрывается гейтом | гейт d15+d01 головы без env против эталона b14flag |
//!
//! Новый флаг без строки в этой таблице не принимается.

use super::*;
use crate::lob::strategy::EntryLadder;

mod ladder;
mod windowed;
use windowed::*;
mod driver;
mod group;
mod queue_models;

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

/// Предрегистрация буквально: вход мейкером живёт 2 с, выход ровно
/// на t₀ + 10 с. Числа — константы, а не параметры вызова.
#[test]
fn decision20_timing_is_frozen_in_constants() {
    assert_eq!(ENTRY_TTL_NS, 2_000_000_000);
    assert_eq!(HOLD_NS, 10_000_000_000);
    const {
        assert!(HOLD_NS > ENTRY_TTL_NS, "выход обязан быть позже снятия");
    }
}

/// Выход — тейкер пересекающим лимитом: лонг по биду, шорт по аску.
#[test]
fn exit_takes_on_own_side_book() {
    assert_eq!(exit_price(HbtSide::Buy, 100.0, 101.0), Some(100.0));
    assert_eq!(exit_price(HbtSide::Sell, 100.0, 101.0), Some(101.0));
    assert_eq!(exit_price(HbtSide::Buy, 100.0, f64::NAN), None);
}

/// `Fill` синтетики: полное исполнение (F3), поэтому `entry_vwap == entry_px`
/// и `fill_frac == 1.0` — прежние числа формулы не меняются.
fn fill_at(dir: i8, entry_px: f64, exit_px: f64) -> Fill {
    Fill {
        dir,
        entry_px,
        exit_px,
        qty: 1.0,
        entry_taker: false,
        exit_taker: true,
        entry_vwap: entry_px,
        fill_frac: 1.0,
        legs_filled: 1,
        legs_rejected: 0,
        fill_by_cross: false,
        tape_press: 0.0,
        tape_press_ws: [0.0; crate::lob::backtest::TAPE_WS_MAX],
        cxl_press: 0.0,
        chase_wait_ns: -1,
    }
}

/// Формула круга буквально: направленный возврат минус комиссии по ногам
/// (В-63: вход мейкер 1.26 + выход тейкер 3.15 = 4.41 bps).
/// Лонг 100 → 101: +100 bps валом, 92.5 чистыми.
#[test]
fn roundtrip_formula_is_exact_on_synthetic_fill() {
    let long = fill_at(1, 100.0, 101.0);
    assert!(close(roundtrip_net_bps(&long).unwrap(), 95.59));
    let short = fill_at(-1, 101.0, 100.0);
    // Зеркало в уровнях не симметрично в bps: база другая (101, не 100).
    assert!(close(
        roundtrip_net_bps(&short).unwrap(),
        10_000.0 / 101.0 - 4.41
    ));
    let loss = fill_at(1, 100.0, 99.0);
    assert!(close(roundtrip_net_bps(&loss).unwrap(), -104.41));
    assert_eq!(roundtrip_net_bps(&fill_at(1, 0.0, 101.0)), None);
    assert_eq!(roundtrip_net_bps(&fill_at(0, 100.0, 101.0)), None);
    assert_eq!(roundtrip_net_bps(&fill_at(1, f64::NAN, 101.0)), None);
    // F3: доходность считается по `entry_vwap`, а не по `entry_px` — на
    // частичном входе по другой средней цене число меняется.
    let partial = Fill {
        entry_vwap: 99.0,
        ..fill_at(1, 100.0, 101.0)
    };
    assert!(close(
        roundtrip_net_bps(&partial).unwrap(),
        (101.0 - 99.0) / 99.0 * 10_000.0 - 4.41
    ));
}

/// RTT 6.4 → задержка: вся измеренная RTT на входном плече, ответ — ноль.
/// Момент подтверждения совпадает с измеренным, очередь — консервативно.
#[test]
fn rtt_maps_wholly_onto_entry_leg() {
    assert_eq!(latency_from_rtt(1_500_000), (1_500_000, 0));
    assert_eq!(latency_from_rtt(0), (0, 0));
    assert_eq!(latency_from_rtt(-5), (0, 0));
}

/// В-68: задержка по типу запроса — снятие, рыночный, лимитка — так, как крейт
/// сам помечает запросы (`req == Canceled`, `order_type == Market`).
#[test]
fn measured_latency_picks_place_cancel_taker_by_request_kind() {
    let lat = ExecLatency {
        place_ns: 4_200_000,
        cancel_ns: 3_980_000,
        taker_ns: 5_650_000,
    };
    let mut model = MeasuredLatency(lat);
    let mut order = Order::new(
        1,
        100,
        1.0,
        1.0,
        HbtSide::Buy,
        OrdType::Limit,
        TimeInForce::GTC,
    );
    order.req = Status::New;
    assert_eq!(model.entry(0, &order), 4_200_000);
    assert_eq!(model.response(0, &order), 0);
    order.req = Status::Canceled;
    assert_eq!(model.entry(0, &order), 3_980_000);
    let mut market = Order::new(
        2,
        100,
        1.0,
        1.0,
        HbtSide::Buy,
        OrdType::Market,
        TimeInForce::IOC,
    );
    market.req = Status::New;
    assert_eq!(model.entry(0, &market), 5_650_000);
    // Одно число — прежняя форма В-37: всем одинаково.
    let mut uni = MeasuredLatency(ExecLatency::uniform(20_000_000));
    assert_eq!(uni.entry(0, &order), 20_000_000);
    assert_eq!(uni.entry(0, &market), 20_000_000);
}

#[test]
fn exec_latency_parses_single_number_and_triple_and_prints_back() {
    let uni: ExecLatency = "20000000".parse().unwrap();
    assert_eq!(uni, ExecLatency::uniform(20_000_000));
    assert!(uni.is_uniform());
    assert_eq!(uni.to_string(), "20000000");
    assert_eq!(uni.provenance(), "assumed(В-37)");
    let tri: ExecLatency = "taker=5650000, place=4200000,cancel=3980000"
        .parse()
        .unwrap();
    assert_eq!(
        tri,
        ExecLatency {
            place_ns: 4_200_000,
            cancel_ns: 3_980_000,
            taker_ns: 5_650_000
        }
    );
    assert_eq!(
        tri.to_string(),
        "place=4200000,cancel=3980000,taker=5650000"
    );
    assert_eq!(tri.provenance(), "measured(lob latency, В-68)");
    assert_eq!(tri.to_string().parse::<ExecLatency>().unwrap(), tri);
    assert!(
        "place=1,cancel=2".parse::<ExecLatency>().is_err(),
        "без taker — отказ"
    );
    assert!("place=1,cancel=2,taker=x".parse::<ExecLatency>().is_err());
    assert!("foo=1,cancel=2,taker=3".parse::<ExecLatency>().is_err());
}

// -----------------------------------------------------------------------
// Очередь RiskAdverseQueueModel на фикстуре крейта: позиция двигается
// только сделками на той же цене (самое консервативное допущение).
// -----------------------------------------------------------------------

/// Очередь встаёт за видимым объёмом, сделка на чужой цене её не двигает,
/// сделка на своей — двигает, исполнение наступает при съеденной очереди.
#[test]
fn risk_adverse_queue_moves_only_on_same_price_trades() {
    use hftbacktest::backtest::models::QueueModel;
    use hftbacktest::depth::L2MarketDepth;
    use hftbacktest::types::{OrdType, TimeInForce};

    let mut depth = FastMarketDepth::new(1.0, 1.0);
    depth.update_bid_depth(100.0, 5.0, 0);
    let qm = RiskAdverseQueueModel::new();
    let mut order = hftbacktest::types::Order::new(
        7,
        100,
        1.0,
        1.0,
        HbtSide::Buy,
        OrdType::Limit,
        TimeInForce::GTX,
    );
    qm.new_order(&mut order, &depth);
    // Чужая цена: глубина сменилась — очередь лишь подрезается минимумом,
    // фронт тот же.
    qm.depth(&mut order, 9.0, 7.0, &depth);
    assert_eq!(qm.is_filled(&mut order, &depth), 0.0);
    // Своя цена, мало: фронт 5 − 3 = 2 — исполнения нет.
    qm.trade(&mut order, 3.0, &depth);
    assert_eq!(qm.is_filled(&mut order, &depth), 0.0);
    // Своя цена, добивка: фронт 2 − 3 = −1 — исполнен весь лот.
    qm.trade(&mut order, 3.0, &depth);
    assert_eq!(qm.is_filled(&mut order, &depth), 1.0);
}

// -----------------------------------------------------------------------
// Сквозной прогон драйвера на синтетике крейта: фид собирается руками,
// неделя не нужна. Мотор — `strategy::on_event`, не копия экономики.
// -----------------------------------------------------------------------

use hftbacktest::types::{
    EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT, EXCH_EVENT,
    EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BUY_TRADE_EVENT,
    LOCAL_EVENT, LOCAL_SELL_TRADE_EVENT,
};

/// Флаг глубины, видимый обеим сторонам бэктеста: локальной (по local_ts)
/// и биржевой (по exch_ts). Экспорт пишет только LOCAL-флаги, здесь оба —
/// иначе одна из сторон фид не увидит.
fn depth_ev(bid: bool) -> u64 {
    if bid {
        LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT
    } else {
        LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT
    }
}

fn trade_ev(sell: bool) -> u64 {
    if sell {
        LOCAL_SELL_TRADE_EVENT | EXCH_SELL_TRADE_EVENT
    } else {
        LOCAL_BUY_TRADE_EVENT | EXCH_BUY_TRADE_EVENT
    }
}

fn depth_at(exch_ts: i64, bid: bool, px: f64, qty: f64) -> Event {
    Event {
        ev: depth_ev(bid),
        exch_ts,
        local_ts: exch_ts + 500,
        px,
        qty,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }
}

fn trade_at(exch_ts: i64, sell: bool, px: f64, qty: f64) -> Event {
    Event {
        ev: trade_ev(sell) | EXCH_EVENT | LOCAL_EVENT,
        exch_ts,
        local_ts: exch_ts + 500,
        px,
        qty,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }
}

/// Конфиг прогона по умолчанию для тестов: прежний движок (`risk-adverse`),
/// размер 1 лот. Частичное исполнение тесты включают своим `DriveConfig` с
/// `QueueModelKind::Prob`.
fn drive_cfg() -> DriveConfig {
    DriveConfig {
        tape_log_secs: 0,
        tape_log_ws: crate::lob::backtest::NO_TAPE_WS,
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::RiskAdverse,
        busy_skip: true,
        hold_skip: false,
    }
}

/// Секунда в наносекундах для читаемости сценариев.
const S: i64 = 1_000_000_000;

/// Граница ядра как у издержек и экспорта: чистому ядру стратегии нечего
/// делать в транспорте, стеночных часах и площадке. Имена заметаются
/// склейкой, чтобы сам тест запрет не триггерил.
#[test]
fn module_stays_detached_from_transport_and_clocks() {
    const SRCS: [&str; 11] = [
        include_str!("../backtest.rs"),
        include_str!("model.rs"),
        include_str!("config.rs"),
        include_str!("round.rs"),
        include_str!("group_round.rs"),
        include_str!("signal.rs"),
        include_str!("diag.rs"),
        include_str!("memo.rs"),
        include_str!("drive.rs"),
        include_str!("windowed.rs"),
        include_str!("build.rs"),
    ];
    let banned = [
        concat!("tok", "io"),
        concat!("Inst", "ant"),
        concat!("System", "Time"),
        concat!("std::", "time"),
        concat!("byb", "it::"),
        concat!("reqw", "est"),
        concat!("tungst", "enite"),
        concat!("elapse", "_bt"),
        concat!("crate::", "feed"),
    ];
    for b in banned {
        for src in SRCS {
            assert!(!src.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}
