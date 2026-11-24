use super::feed::events_from_feed;
use super::plan::{
    approach_plan_sched, bounce_plan_sched, EARLY_EXITS_HALF_S, EARLY_EXITS_S, EARLY_EXIT_HALF_MS,
};
use super::*;
use crate::binlog::StepAt;
use crate::book::{Side, Update};
use crate::bybit::ws::Event as WsEvent;
use crate::feed::{Event as FeedEvent, Feed};
use crate::lob::backtest::SIGMA_SHORT;
use crate::lob::levels::TouchRecord;
use crate::lob::step_schedule::StepSchedule;
use crate::lob::strategy::TradePlan;
use hftbacktest::types::{
    LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BUY_TRADE_EVENT, LOCAL_SELL_TRADE_EVENT,
};

mod forms;
mod ladder;
mod plan;
mod sched;

struct VecFeed(std::vec::IntoIter<FeedEvent>);
impl Feed for VecFeed {
    fn next_event(&mut self) -> Option<FeedEvent> {
        self.0.next()
    }
}

fn book_ev(local_ts_ns: i64, up: Update) -> FeedEvent {
    FeedEvent::Market {
        symbol: 0,
        local_ts_ns,
        parse_latency_ns: None,
        payload: WsEvent::Book(up),
    }
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

/// Снапшот, потерявший уровень против предыдущего снапшота, обязан
/// явно его обнулить — иначе `HashMapMarketDepth` крейта унаследует
/// цену, которой в книге уже нет (`book::Book::apply` делает то же самое
/// перед применением снапшота, это тот же случай на другом типе).
#[test]
fn snapshot_clears_stale_levels_and_deltas_upsert() {
    let snap1 = Update {
        is_snapshot: true,
        depth: 50,
        u: 1,
        seq: 1,
        cts_ms: 1000,
        bids: vec![
            (100_000_000_000, 5_000_000_000),
            (99_000_000_000, 2_000_000_000),
        ],
        asks: vec![(101_000_000_000, 3_000_000_000)],
    };
    let snap2 = Update {
        is_snapshot: true,
        depth: 50,
        u: 2,
        seq: 2,
        cts_ms: 2000,
        bids: vec![(100_000_000_000, 4_000_000_000)],
        asks: vec![(101_000_000_000, 3_000_000_000)],
    };
    let events = vec![book_ev(1_500, snap1), book_ev(2_500, snap2)];
    let mut feed = VecFeed(events.into_iter());
    let out = events_from_feed(&mut feed);

    assert_eq!(
        out.iter().filter(|e| e.exch_ts == 1_000_000_000).count(),
        3,
        "первый снапшот — только апсерты, стейла ещё нет"
    );
    let cleared = out
        .iter()
        .find(|e| e.exch_ts == 2_000_000_000 && close(e.px, 99.0));
    assert!(
        cleared.is_some(),
        "пропавший из второго снапшота уровень (99.0) обязан обнулиться"
    );
    assert_eq!(cleared.unwrap().qty, 0.0);
    let kept = out
        .iter()
        .rfind(|e| e.exch_ts == 2_000_000_000 && close(e.px, 100.0))
        .unwrap();
    assert_eq!(kept.qty, 4.0, "выживший уровень апсерчен новым размером");
}

/// Дельта с нулевым размером снимает уровень так же, как отсутствие
/// его в снапшоте — оба пути дают одно и то же событие цепочки крейта.
#[test]
fn zero_qty_delta_removes_a_level() {
    let snap = Update {
        is_snapshot: true,
        depth: 50,
        u: 1,
        seq: 1,
        cts_ms: 1000,
        bids: vec![(100_000_000_000, 5_000_000_000)],
        asks: vec![],
    };
    let delta = Update {
        is_snapshot: false,
        depth: 50,
        u: 2,
        seq: 2,
        cts_ms: 1500,
        bids: vec![(100_000_000_000, 0)],
        asks: vec![],
    };
    let events = vec![book_ev(1_000, snap), book_ev(1_200, delta)];
    let mut feed = VecFeed(events.into_iter());
    let out = events_from_feed(&mut feed);
    assert_eq!(out.len(), 2);
    assert_eq!(out[1].qty, 0.0);
    assert!(close(out[1].px, 100.0));
}

/// Форма сделки «как в T38»: без пост-онли, трейла и лестницы — тесты B2
/// проверяют геометрию входа/стопа/тейка, а не оси T38.
fn plain_shape() -> PlanShape {
    PlanShape {
        lot: 1.0,
        level_lot: None,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_ticks: 0,
        // Дедлайн по умолчанию — 60 с, как `--deadline-secs` без флага (B3).
        deadline_ns: 60 * 1_000_000_000,
        // Досрочный выход выключен: тесты B2/B3 проверяют геометрию плана.
        early_exit_ns: 0,
        // F5 (В-74): условия «стена снята»/«цена ушла» проверяются своими
        // тестами, здесь — прежний режим входа.
        entry_ttl: EntryTtl::Touch,
        h3_usd: None,
        band_exit_bps: 0.0,
        // F6 (В-73): прежняя форма входа — нога у фронтранера (гейт).
        entry_form: EntryForm::SingleFrontrun,
        // F7/F8: форма выхода — не используется в одиночном backtest.
        exit_form: crate::commands::lob::bounce_grid::ExitForm::None,
        // В-131: σ на взводе — только σ-лестнице, у прежних форм не читается.
        entry_sigma_bps: None,
    }
}

/// Касание для проверки плана B2: бид, цена уровня `P`, при желании — цена
/// фронтрана перед ним. Остальные поля записаны так, чтобы тест читался:
/// касание длиной 1 с, размер 10 лотов.
fn bounce_touch(price_tick: i64, frontrun_tick: Option<i64>) -> TouchRecord {
    TouchRecord {
        side: Side::Bid,
        price_tick,
        touch_index: 0,
        start_ms: 1_000,
        end_ms: 2_000,
        duration_ms: 1_000,
        level_birth_ms: 0,
        size_at_touch: 10,
        size_max_before: 10,
        traded_during: 0,
        frontrun_lots: if frontrun_tick.is_some() { 5 } else { 0 },
        frontrun_tick,
        swept_lots: 0,
        round_zeros: 2,
        ended_by_death: false,
        stack_levels: 1,
        stack_next_tick: None,
        traded_first_s: [0; 3],
        flow_1h_lots: 0,
        strength_e2: [-1, -1, -1],
        strength_held_e2: [-1, -1, -1, -1],
        repeat_count: 0,
        depth_behind_lots: 0,
    }
}

/// Разбирает план сделки-отскока в тройку (вход, стоп, тейк) — иначе тест
/// утонул бы в сопоставлении с образцом.
fn plan_prices(plan: &TradePlan) -> (f64, f64, f64) {
    match plan {
        TradePlan::Bounce {
            entry_px,
            stop_px,
            take_px,
            ..
        } => (*entry_px, *stop_px, *take_px),
        TradePlan::SpreadHold => panic!("отскок обязан быть Bounce"),
    }
}

fn geometry(stop_mult: f64, take_mult: f64, take_floor_fees: f64) -> BounceForm {
    BounceForm {
        stop: StopForm::Sigma(stop_mult),
        take: TakeForm::Sigma(take_mult),
        take_floor_fees: Some(take_floor_fees),
    }
}

fn base(stop: &str, take: &str) -> BounceForm {
    BounceForm::parse(stop, take, None).unwrap()
}
