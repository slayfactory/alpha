//! Драйвер одного сигнала: шаг круга и круг группы поверх движка.

use super::*;

/// То же, что `drive_signal`, но для К вариантов одной группы форм (Э-08, «один проход на
/// вход»): вход общий, разворачивается один раз (по `variant_plans[0]` — входные поля у всех
/// вариантов группы равны по построению, проверка вызывающего, `drive_bounce_windowed_group`), а
/// выход у каждого варианта свой (`run_round_group`). `sig.plan` не читается — сигнал несёт
/// только `t0_ns`/`sigma`/`qty`, план входа берётся из `variant_plans[0]` напрямую.
///
/// `variant_carries` — сироты, унаследованные каждым вариантом от **прошлого** сигнала: группа
/// безопасна только когда они у всех пусты (иначе общий вход не может ответить, чья это сирота, —
/// проверка вызывающего, здесь только `debug_assert`). Номера заявок — свой независимый счётчик
/// (`GROUP_ID_BASE`), а не `variant_next_ids`/`cfg.first_order_id` сольного пути: движок сигнала
/// свежий (`drive_bounce_windowed`), поэтому единственное, что должно быть монотонно, — сироты одного и
/// того же варианта между сигналами, а не пересечение чисел с сольным путём (см. `GROUP_ID_BASE`).
#[allow(clippy::too_many_arguments)]
pub(super) fn drive_signal_group<B, MD>(
    bot: &mut B,
    asset_no: usize,
    sig: &BounceSignal,
    variant_plans: &[TradePlan],
    cfg: &DriveConfig,
    next_group_id: &mut u64,
    variant_carries: &mut [OrphanCarry],
    data_end_ns: Option<i64>,
    walls: Option<&[WallEvent]>,
) -> Result<Vec<SignalStep>, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let n = variant_plans.len();
    debug_assert_eq!(variant_carries.len(), n);
    debug_assert!(
        variant_carries.iter().all(OrphanCarry::is_empty),
        "группа форм (Э-08) требует пустых сирот у всех вариантов — проверка вызывающего"
    );
    let now = bot.current_timestamp();
    if sig.t0_ns > now {
        if bot.elapse(sig.t0_ns - now)? == ElapseResult::EndOfData {
            return Ok(vec![SignalStep::EndOfData; n]);
        }
        bot.clear_last_trades(Some(asset_no));
    }
    let qty = sig.qty.unwrap_or(cfg.order_qty);
    debug_assert!(qty > 0.0, "размер круга обязан быть положительным: {qty}");
    let entry_id_base = *next_group_id;
    let mut entry_state =
        StrategyState::with_plan(asset_no, sig.sigma, qty, entry_id_base, variant_plans[0]);
    entry_state.enable_tape_from(cfg.tape_log_secs);
    entry_state.enable_tape_ws(&cfg.tape_log_ws);
    let (entry_id, side) = match on_event(bot, &mut entry_state)? {
        Action::EntrySubmitted { order_id, side, .. } => (order_id, side),
        _ => {
            *next_group_id = entry_id_base.saturating_add(ID_STRIDE);
            let idle_ns = bot.current_timestamp();
            return Ok(vec![SignalStep::NotSubmitted { idle_ns }; n]);
        }
    };
    // Замер механизма отказа (таск 38), как у сольного `drive_signal` — по представителю группы:
    // входные поля (в т.ч. `entry_px`/лестница) у всех вариантов равны по построению.
    let mut crossed = false;
    let mut spread = None;
    if let Some(entry_px) = entry_market_px(variant_plans[0]) {
        let d = bot.depth(asset_no);
        let (bid, ask) = (d.best_bid(), d.best_ask());
        if bid.is_finite() && ask.is_finite() && bid > 0.0 && ask > 0.0 {
            crossed = match side {
                HbtSide::Buy => ask <= entry_px,
                _ => bid >= entry_px,
            };
            spread = Some(ask - bid);
        }
    }
    // Диапазон номеров на клон — не пересекается ни со входом (лестница ≤ `MAX_ENTRY_LEGS` < `VARIANT_ID_SPAN`),
    // ни с соседями: `(k+1) × VARIANT_ID_SPAN` от базы входа этого круга (TK-347).
    let variant_ids: Vec<u64> = (0..n as u64)
        .map(|k| group_variant_id(entry_id_base, k))
        .collect();
    *next_group_id = group_flatten_id(entry_id_base, n as u64, n as u64);
    let results = run_round_group(
        bot,
        asset_no,
        &mut entry_state,
        entry_id,
        legs_of(variant_plans[0]),
        side,
        variant_plans,
        &variant_ids,
        data_end_ns.filter(|_| cfg.hold_skip),
        walls,
    )?;
    let mut steps = Vec::with_capacity(n);
    for (i, r) in results.into_iter().enumerate() {
        variant_carries[i] = r.carry_out;
        if matches!(r.outcome, RoundOutcome::EndOfData) {
            steps.push(SignalStep::Submitted {
                crossed,
                spread,
                outcome: r.outcome,
                residual: None,
                idle_ns: r.idle_ns,
                exit_cancel_timeouts: r.exit_cancel_timeouts,
                orphan_fills: r.orphan_fills,
                gate_measure: r.gate_measure,
            });
            continue;
        }
        // Страховка остатка (2026-09-18), как у сольного `drive_signal` — по разу на вариант,
        // своим номером заявки (та же группа номеров, что и у выхода варианта — за пределами
        // диапазонов остальных вариантов и входа: `group_flatten_id`).
        let mut residual = None;
        let mut left = signed_residual(side, r.position);
        let mut flatten_id = group_flatten_id(entry_id_base, n as u64, i as u64);
        // `idle_ns` — своя метка варианта (`r.idle_ns`, момент его собственного `Idle` в общем
        // движке группы), а не после общего цикла; страховка остатка, если была, её двигает
        // дальше — как у сольного `drive_signal`.
        let mut idle_ns = r.idle_ns;
        if left != 0.0 {
            let mut ended = false;
            while left != 0.0 {
                let id = flatten_id;
                flatten_id = flatten_id.saturating_add(1);
                match flatten_residual(bot, asset_no, id, left)? {
                    FlattenOutcome::Flat => break,
                    FlattenOutcome::Retry { left: rest } => left = rest,
                    FlattenOutcome::EndOfData => {
                        ended = true;
                        break;
                    }
                }
            }
            residual = Some(ended);
            idle_ns = bot.current_timestamp();
        }
        steps.push(SignalStep::Submitted {
            crossed,
            spread,
            outcome: r.outcome,
            residual,
            idle_ns,
            exit_cancel_timeouts: r.exit_cancel_timeouts,
            orphan_fills: r.orphan_fills,
            gate_measure: r.gate_measure,
        });
    }
    bot.clear_inactive_orders(Some(asset_no));
    Ok(steps)
}

/// Шаг драйвера на одном сигнале — всё, что требует движка: часы к `t0`,
/// проверка позиции, вход, круг, страховка остатка. Общий для сплошного
/// прогона (`drive_bounce`) и прогона по сетапам (`drive_bounce_windowed`):
/// различие только в том, откуда берётся движок, — иначе это была бы вторая
/// стратегия.
///
/// `large_enum_variant`: `Submitted` несёт `RoundOutcome` с `Fill` (TK-272), `EndOfData`/`NotSubmitted` малы;
/// значение на стеке драйвера, один шаг на сигнал.
#[allow(clippy::large_enum_variant)]
#[derive(Debug, Clone, PartialEq)]
pub(super) enum SignalStep {
    /// Часы не дошли до `t0`: запись кончилась.
    EndOfData,
    /// Книга не была готова в момент касания — попытки не было.
    NotSubmitted { idle_ns: i64 },
    Submitted {
        crossed: bool,
        spread: Option<f64>,
        outcome: RoundOutcome,
        /// `Some(ended)` — остаток позиции закрывали по рынку; `ended` —
        /// запись кончилась в процессе.
        residual: Option<bool>,
        /// Часы стороны в момент, когда стратегия снова `Idle` (граница
        /// шага опроса после выхода или подтверждённой отмены): до него
        /// форма **занята** для любого сигнала.
        idle_ns: i64,
        /// F8b (В5/Р2): сколько раз круг пережил потолок ожидания
        /// подтверждения отмены лимитки выхода (`CANCEL_WAIT_NS`).
        exit_cancel_timeouts: u64,
        /// F8c (К1): исполнения заявок-сирот (после потолка) за круг.
        orphan_fills: u64,
        /// Г-81: мера ворот на `sent + W` круга (проба порогов `entry_gate=cxl<W>qprobe`); `None` — проверки не было.
        gate_measure: Option<f64>,
    },
}

pub(super) fn drive_signal<B, MD>(
    bot: &mut B,
    asset_no: usize,
    sig: &BounceSignal,
    cfg: &DriveConfig,
    next_id: &mut u64,
    carry: &mut OrphanCarry,
    data_end_ns: Option<i64>,
) -> Result<SignalStep, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let now = bot.current_timestamp();
    if sig.t0_ns > now {
        if bot.elapse(sig.t0_ns - now)? == ElapseResult::EndOfData {
            return Ok(SignalStep::EndOfData);
        }
        // Сделки до постановки входа к пути исполнения не относятся (F3):
        // буфер чистится, детектор `run_round` видит только шаг исполнения.
        bot.clear_last_trades(Some(asset_no));
    }
    // Проверки «позиция уже открыта» по `Bot::position` здесь нет: с F4
    // (В-78) вход исполняется частично, а `Bot::position` крейта ведёт
    // позицию только по `Filled` (находка F3) — после первого же частичного
    // входа она врёт со знаком, и такая проверка объявила бы «занятыми» все
    // оставшиеся сигналы суток. Занятость ведёт `idle_ns` — момент, когда
    // стратегия вернулась в `Idle` со **своей** позицией, равной нулю
    // (`drive_bounce_with`), и в обоих драйверах он один и тот же (гейт
    // «те же круги» на `lob bounce-grid --driver full|setups`).

    let qty = sig.qty.unwrap_or(cfg.order_qty);
    debug_assert!(qty > 0.0, "размер круга обязан быть положительным: {qty}");
    let mut state = StrategyState::with_plan(asset_no, sig.sigma, qty, *next_id, sig.plan);
    state.enable_tape_from(cfg.tape_log_secs);
    state.enable_tape_ws(&cfg.tape_log_ws);
    *next_id = next_id.saturating_add(ID_STRIDE);
    // F8c: сироты прошлого круга живут дальше в новом состоянии — уборка
    // (повтор снятия, учёт исполнения) идёт с первого события этого круга.
    state.inherit_orphans(*carry);

    // Г-88: `reclaim` не ставит вход сразу — круг идёт в `ReclaimWait` (заявка уйдёт позже, с тем же номером).
    let mut reclaim_wait = false;
    let (entry_id, side) = match on_event(bot, &mut state)? {
        Action::EntrySubmitted { order_id, side, .. } => (order_id, side),
        Action::Idle
            if state.reclaim_wait_entry_id().is_some() && entry_side(sig.sigma).is_some() =>
        {
            reclaim_wait = true;
            (
                state.reclaim_wait_entry_id().unwrap_or_default(),
                entry_side(sig.sigma).unwrap_or(HbtSide::Buy),
            )
        }
        _ => {
            *carry = state.take_orphans();
            return Ok(SignalStep::NotSubmitted {
                idle_ns: bot.current_timestamp(),
            });
        }
    };
    // Замер механизма отказа (таск 38): в момент отправки входа смотрим,
    // пересекает ли его цена спред и каков спред. Пост-онли такую заявку
    // отвергает, обычный лимит — исполняет как тейкер.
    let mut crossed = false;
    let mut spread = None;
    if let (false, Some(entry_px)) = (reclaim_wait, entry_market_px(sig.plan)) {
        let d = bot.depth(asset_no);
        let (bid, ask) = (d.best_bid(), d.best_ask());
        if bid.is_finite() && ask.is_finite() && bid > 0.0 && ask > 0.0 {
            crossed = match side {
                HbtSide::Buy => ask <= entry_px,
                _ => bid >= entry_px,
            };
            spread = Some(ask - bid);
        }
    }

    // Э-04б: пропуск пустых шагов — только с флагом и известным концом данных круга.
    let skip_cap = data_end_ns.filter(|_| cfg.hold_skip);
    let (outcome, residual_left) = run_round(
        bot,
        asset_no,
        &mut state,
        entry_id,
        legs_of(sig.plan),
        side,
        skip_cap,
    )?;
    if matches!(outcome, RoundOutcome::EndOfData) {
        *carry = state.take_orphans();
        return Ok(SignalStep::Submitted {
            crossed,
            spread,
            outcome,
            residual: None,
            idle_ns: bot.current_timestamp(),
            exit_cancel_timeouts: state.exit_cancel_timeouts(),
            orphan_fills: state.orphan_fills(),
            gate_measure: state.gate_measure(),
        });
    }
    // Страховка (2026-09-18): круг кончился, а позиция осталась (например,
    // `Inconsistent`) — закрыть по рынку и посчитать, иначе все дальнейшие
    // сигналы молча «заняты» и остаток суток не считается вовсе. Размер
    // остатка — **своя** позиция стратегии (F4/В-78): `Bot::position` крейта
    // частичного исполнения не видит (находка F3), и по ней страховка
    // закрывала бы мнимый остаток, помечая честный круг `incomplete`.
    let mut residual = None;
    let mut left = signed_residual(side, residual_left);
    if left != 0.0 {
        let mut ended = false;
        while left != 0.0 {
            let id = *next_id;
            *next_id = next_id.saturating_add(1);
            match flatten_residual(bot, asset_no, id, left)? {
                FlattenOutcome::Flat => break,
                FlattenOutcome::Retry { left: rest } => left = rest,
                FlattenOutcome::EndOfData => {
                    ended = true;
                    break;
                }
            }
        }
        residual = Some(ended);
        if ended {
            *carry = state.take_orphans();
            return Ok(SignalStep::Submitted {
                crossed,
                spread,
                outcome,
                residual,
                idle_ns: bot.current_timestamp(),
                exit_cancel_timeouts: state.exit_cancel_timeouts(),
                orphan_fills: state.orphan_fills(),
                gate_measure: state.gate_measure(),
            });
        }
    }
    bot.clear_inactive_orders(Some(asset_no));
    *carry = state.take_orphans();
    Ok(SignalStep::Submitted {
        crossed,
        spread,
        outcome,
        residual,
        idle_ns: bot.current_timestamp(),
        exit_cancel_timeouts: state.exit_cancel_timeouts(),
        orphan_fills: state.orphan_fills(),
        gate_measure: state.gate_measure(),
    })
}
