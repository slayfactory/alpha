//! Издержки и проскальзывание по Decision 15 (план, §5.3).
//!
//! Вход — мейкер у своей стороны спреда без улучшения (тип ордера —
//! Decision 19); выход — тейкер с издержкой в один тик против позиции плюс
//! половина наблюдённого спреда в момент выхода. Круг комиссий —
//! по ногам: мейкер 0.014 % / тейкер 0.035 % минус 10 % возврата (В-63, условия счёта
//! владельца 2026-09-18; прежнее H4 — публичный non-VIP 0.02 % / 0.055 %).
//!
//! Формула на наблюдение: `net = m − (7.5 + slippage)`, где `m` — подписанный
//! markout по Decision 14 (потребитель: `markout::markout_bps`), а slippage
//! считается на каждом наблюдении отдельно из его же спреда выхода и его же
//! базовой середины. Валового порога-константы нет (H6): тик на тонком
//! инструменте честно весит больше, потому что знаменатель — цена.
//!
//! Целые до последнего, как в книге (A7): тики — `i64`, середина держится
//! удвоенной суммой `bid + ask` (ровно держит половину тика без деления),
//! вещественное число появляется один раз — при делении в формуле bps.
//!
//! Вывод slippage: цена издержки = 1 тик + spread_exit / 2 тика, то есть
//! `(spread + 2) / 2` тиков; середина базы = `mid2x / 2` тиков, где
//! `mid2x = bid_base + ask_base`. Масштаб тика сокращается:
//! `slippage_bps = (spread + 2) / mid2x * 10^4`. Числитель — целый (`i128`),
//! деление одно.
//!
//! Знаменатель — базовая середина, тот же, что у `m`: net вычитается
//! из markout без смены базы. Спред — наблюдённый в момент выхода
//! (`ask_exit − bid_exit` в тиках, потребитель: `book`).
//!
//! Вердикт «эдж после издержек положителен» больше не выносится здесь
//! (таск 17 снял `VerdictCell`/`G3Verdict`/`select_verdict_cell`/`decide_g3*`
//! — построены под отменённую ячеечную модель C1/C2 §5.2, `interfaces.md`
//! «Что построено под отменённый дизайн»; ни один вызывающий не читал их —
//! живой вердикт на очереди даёт `backtest::G4Verdict`/`decide_g4`).
//! Зелёный порог H6 (`GREEN_NET_BPS`, средний net ≥ 3 bps) остаётся здесь
//! просто числом: `shortlist` сравнивает с ним готовый `net_fill`.
//!
//! Модуль не хранит состояния и не выделяет память.

use crate::lob::cells::joint_product_interval;
use crate::stats::count_f64_u64;
use std::collections::BTreeMap;

/// Комиссия мейкера, bps: 0.014 % — условия счёта владельца (скриншот
/// account.tiger.com, 2026-09-18, В-63). Прежнее H4 — публичный non-VIP 0.02 %.
pub const MAKER_FEE_BPS: f64 = 1.4;

/// Комиссия тейкера, bps: 0.035 % (там же). Прежнее H4 — 0.055 %.
pub const TAKER_FEE_BPS: f64 = 3.5;

/// Возврат комиссий владельцу: 10 % от уплаченного (там же, «вы получаете 10 %
/// вознаграждения от торговых комиссий»). Применяется к каждой ноге.
pub const FEE_REBATE_SHARE: f64 = 0.10;

/// Комиссия спот-мейкера, bps: 0.0625 % (В-88, владелец 02.11: «комиссия везде как на байбите»).
pub const SPOT_MAKER_FEE_BPS: f64 = 6.25;

/// Комиссия спот-тейкера, bps: 0.075 % (там же).
pub const SPOT_TAKER_FEE_BPS: f64 = 7.5;

/// Эффективная комиссия спот-ноги после возврата 10 %, bps: мейкер 5.625, тейкер 6.75.
pub fn spot_leg_fee_bps(taker: bool) -> f64 {
    (if taker {
        SPOT_TAKER_FEE_BPS
    } else {
        SPOT_MAKER_FEE_BPS
    }) * (1.0 - FEE_REBATE_SHARE)
}

/// Эффективная комиссия одной ноги после возврата, bps: мейкер 1.26, тейкер 3.15.
pub fn leg_fee_bps(taker: bool) -> f64 {
    (if taker { TAKER_FEE_BPS } else { MAKER_FEE_BPS }) * (1.0 - FEE_REBATE_SHARE)
}

/// Круг мейкер-тейкер после возврата, bps (Decision 19 → В-63): одно число
/// для markout-метрик, у которых ног нет (G0, G3, дашборд, отбор, пол тейка);
/// круг **сделки** считается по ногам — `backtest::roundtrip_net_bps`.
pub const ROUNDTRIP_FEES_BPS: f64 = (MAKER_FEE_BPS + TAKER_FEE_BPS) * (1.0 - FEE_REBATE_SHARE);

/// Зелёный порог H6, bps: средний net ≥ 3 сверх полных издержек.
/// Только читается; вердикт G3 его не использует.
pub const GREEN_NET_BPS: f64 = 3.0;

// ---------------------------------------------------------------------------
// `net_fill` (таск 03, истории 19–22, §6): «вот это и есть окупается».
// ---------------------------------------------------------------------------

/// Одно наблюдение для `net_fill`: `net` после издержек (см. `net_bps`),
/// признак исполнения входа за 2 с (R06) и сутки как целый кластер для
/// совместного бутстрапа — тот же смысл кластера, что в
/// `cells::joint_product_interval` (сравнение и группировка по целому, не
/// по `f64`, A1).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FillObservation {
    /// Сутки UTC как целый кластер.
    pub day_cluster: i64,
    /// `net` этого наблюдения в bps (после издержек).
    pub net_bps: f64,
    /// Исполнился ли вход за 2 с (Decision, R06). `false` — не исполнился
    /// или позиция уже была открыта; какая из двух причин — не поле этой
    /// структуры (см. CONCERNS: колонки причины пропуска — гейт Е/задача 09).
    pub filled: bool,
}

/// Итог совместного интервала `net_fill` (R08/R50): точка (тождественно
/// прямой формуле `Σ netᵢ·fillᵢ / N` на тех же данных — алгебраическое
/// тождество, не оценка:
/// `Σᵢ netᵢ·fillᵢ = Σ_{fillᵢ=1} netᵢ`, значит `mean(net|fill=1)·fill_rate
/// = (Σ_{fill=1}netᵢ/n_filled)·(n_filled/N) = Σ netᵢ·fillᵢ/N`) и нижняя
/// граница — то, что сравнивается с нулём («окупается», бриф).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NetFillInterval {
    /// Исполнившихся входов, суммарно по суткам.
    pub n_filled: u64,
    /// Всего входов (знаменатель `N`), суммарно по суткам.
    pub n_total: u64,
    /// Точка: `net_fill`, bps.
    pub point_bps: f64,
    /// Нижняя граница интервала, bps.
    pub lower_bps: f64,
    /// Число реплик.
    pub replications: u32,
    /// Seed реплик.
    pub seed: u64,
}

/// Нижняя граница интервала `net_fill` совместным ресэмплингом пары
/// `(net, fill)`: один вес Уэбба на обе серии в одной реплике, произведение
/// считается на реплику (R08/R50) через `cells::joint_product_interval`,
/// а не второй бутстрап. Наблюдения группируются по `day_cluster` в
/// `BTreeMap` (по целому, не по `f64`):
/// порядок обхода детерминирован и не зависит от порядка входного среза
/// (ARCHITECTURE.md A2). Разложение на два ряда — среднее `net` там, где
/// `fill = true` (числитель `Σnetᵢ`, знаменатель `n_filled`), и доля `fill`
/// (числитель `n_filled` тех же суток, знаменатель `n_total`) — таково, что
/// их произведение на наблюдённых данных равно точке `Σ netᵢ·fillᵢ / N` (см.
/// `NetFillInterval`), а на репликах — совместному ресэмплингу с общим
/// весом на сутки, как того требует критерий.
///
/// `alpha` — односторонний уровень нижнего перцентиля, обязательный
/// параметр без умолчания (§9 плана: изобретённое число запрещено; спека
/// не называет число для этого гейта, в отличие от `stats::GATE_ALPHA` —
/// той альфы гейта G2). `None` — срез пуст, хотя бы один `net_bps` не
/// конечен, или исполнений нет вовсе (числитель `mean(net|filled)` не из
/// чего считать).
#[allow(clippy::cast_precision_loss)]
pub fn net_fill_interval(
    observations: &[FillObservation],
    alpha: f64,
    replications: u32,
    seed: u64,
) -> Option<NetFillInterval> {
    if observations.is_empty() || observations.iter().any(|o| !o.net_bps.is_finite()) {
        return None;
    }
    let mut by_day: BTreeMap<i64, (f64, u64, u64)> = BTreeMap::new();
    for o in observations {
        let entry = by_day.entry(o.day_cluster).or_insert((0.0, 0, 0));
        entry.2 = entry.2.saturating_add(1);
        if o.filled {
            entry.0 += o.net_bps;
            entry.1 = entry.1.saturating_add(1);
        }
    }
    let day_sums: Vec<(f64, u64, f64, u64)> = by_day
        .values()
        .map(|&(sum_net_filled, n_filled, n_total)| {
            (sum_net_filled, n_filled, count_f64_u64(n_filled), n_total)
        })
        .collect();
    let jp = joint_product_interval(&day_sums, alpha, replications, seed)?;
    Some(NetFillInterval {
        n_filled: jp.n_a,
        n_total: jp.n_b,
        point_bps: jp.point,
        lower_bps: jp.lower,
        replications: jp.replications,
        seed: jp.seed,
    })
}

#[cfg(test)]
mod tests;
