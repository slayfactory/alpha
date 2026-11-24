//! `hftbacktest` со стратегией Decision 20, RTT из 6.4, очередь
//! `RiskAdverseQueueModel` (план, §6.3). С F3 плана 2026-09-20 модель очереди
//! и исполнения — параметр (`QueueModelKind`): прежняя пара
//! `RiskAdverseQueueModel` + `NoPartialFillExchange` или крейтовые
//! `ProbQueueModel<PowerProbQueueFunc(n)>` + `PartialFillExchange`; три пути
//! исполнения крейта — в doc-комментарии `build_backtest`.
//!
//! Стратегия предрегистрирована целиком (Decision 20): вход мейкером у своей
//! стороны спреда с временем жизни ордера 2 с, выход тейкером ровно на
//! горизонте без стопа и без цели, одна позиция за раз, модель очереди —
//! `RiskAdverseQueueModel`. Отчёт строится на **произвольное число
//! профилей** (таск 11, история 32–34 спеки): полей `c1`/`c2`/`verdict_cell`
//! отменённого дизайна здесь больше нет — вызывающий (`commands::lob::
//! backtest`) приносит уже сгруппированные по `profile_id` сигналы, а этот
//! модуль их только гоняет и агрегирует.
//!
//! # Одна стратегия на бэктест и живую торговлю (A6)
//!
//! `drive_profile` не реализует вход/выход второй раз — это была бы
//! Reinvention (`interfaces.md`, правило 5). Экономику решения (вход
//! мейкером у своей стороны спреда, время жизни входа, горизонт выхода)
//! делает **`crate::lob::strategy::on_event<MD, B: Bot<MD>>`** (таск 15) —
//! ровно тот же символ функции, что пойдёт в `LiveBot` фазы 2 без правок.
//! Этот файл только продвигает часы стороны и решает, когда вооружить новый
//! круг и как классифицировать его исход (заполнение / таймаут / занятость)
//! по данным, которые `Bot<MD>` и так публикует (`orders`, `position`).
//!
//! # RTT из 6.4
//!
//! Замер 6.4 даёт две задержки — медиану и p95 (D-RTT). Обе прогоняются
//! отдельно (`build_backtest` дважды, с разной `exec_rtt_ns`), и обе кривые
//! PnL печатаются (гейт G4-в, `PLAN.md`). Вся измеренная RTT кладётся на
//! входное плечо (`response = 0`) — консервативное допущение, см.
//! `latency_from_rtt`.
//!
//! # Done-condition 6.3 буквально
//!
//! Отчёт на профиль несёт: PnL-кривую на обеих RTT, число заполнений, число
//! пропущенных сигналов отдельно по причине (`MissLedger`), агрегаты
//! `fill`/`net_fill` через `costs::net_fill_interval` (alpha — из спеки, не
//! изобретать), и сравнение с оценкой из таблицы профилей (таск 10) — приносит вызывающий, этот модуль
//! `shortlist`/файловый ввод не трогает (граница модулей: чистое ядро не
//! тянет CLI и площадку — см. грепом-тест в конце файла, тот же приём, что
//! `levels.rs`/`strategy.rs`).
//!
//! Модуль не хранит состояния и не выделяет память вне отчёта и очереди
//! самого крейта.

use hftbacktest::backtest::assettype::LinearAsset;
use hftbacktest::backtest::data::{Data, DataPtr};
use hftbacktest::backtest::models::{
    CommonFees, LatencyModel, PowerProbQueueFunc, ProbQueueModel, RiskAdverseQueueModel,
    TradingValueFeeModel,
};
use hftbacktest::backtest::BacktestError;
use hftbacktest::backtest::{Backtest, DataSource, ExchangeKind, L2AssetBuilder};
use hftbacktest::depth::{L2MarketDepth, MarketDepth};
use hftbacktest::types::{
    Bot, ElapseResult, Event, OrdType, Order, Side as HbtSide, Status, TimeInForce, BUY_EVENT,
    EXCH_BID_DEPTH_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT, SELL_EVENT,
};

use crate::lob::costs::{leg_fee_bps, FillObservation, MAKER_FEE_BPS, TAKER_FEE_BPS};
use crate::lob::levels::WallEvent;
use crate::lob::retreat::{RetreatCfg, RetreatMemory};
use crate::lob::strategy::{
    on_event, Action, EntryCancelReason, ExitReason, OrphanCarry, StrategyState, TradePlan,
    MAX_ENTRY_LEGS,
};

mod compact;
pub mod fast_depth;
pub mod fast_hold;
mod levels;
use fast_depth::FastMarketDepth;
mod window_depth;
pub use compact::{CompactEvent, EventKind, EventRows};
pub(crate) use window_depth::round_half_away;
pub use window_depth::WindowDepth;
pub use window_depth::{QUANT_CALLS, QUANT_SLOW};

mod model;
pub use model::*;
mod config;
pub use config::*;
mod round;
pub(crate) use round::*;
mod group_round;
use group_round::*;
mod signal;
use signal::*;
mod diag;
pub use diag::*;
mod memo;
pub use memo::*;
mod drive;
pub use drive::*;
mod windowed;
pub use windowed::*;
mod build;
pub use build::*;

/// Шаг номеров заявок между сигналами: круг тратит до `MAX_ENTRY_LEGS` ног входа,
/// заявку выхода, тейкерское добивание и гашение сироты (F8c) — прежний запас
/// «+4» с лестницей формы F6 давал коллизию id ещё открытой заявки с входом
/// следующего сигнала (ревью 22.09, п. 5). Не экономическая величина.
const ID_STRIDE: u64 = MAX_ENTRY_LEGS as u64 + 4;

#[cfg(test)]
mod tests;
