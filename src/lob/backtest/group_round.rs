//! Круг группы форм с общим входом (Э-08): `run_round_group` и итог ноги входа.

use super::*;

// ---------------------------------------------------------------------------
// Э-08 (T-38, «один проход на вход»): группа форм с общим входом, свой выход
// на клон — вход разворачивается движком один раз, а не по разу на форму.
// ---------------------------------------------------------------------------

/// Итог ноги входа общего для группы круга — те же поля и та же формула, что
/// у хвоста сольного `run_round` (R7/В-78, комментарии там же), вынесены сюда,
/// чтобы посчитать один раз на К вариантов, а не по разу на каждый.
/// `None` — ни одна нога не исполнилась (Inconsistent для всех вариантов).
///
/// Раздел Э-08 вызывается `bounce-grid --exit-group on` через `precompute_exit_group` (групповые круги
/// кладутся в память кругов формы, дальше обычный драйвер берёт их оттуда).
pub(super) struct EntryTally {
    pub(super) entry_px: f64,
    pub(super) entry_vwap: f64,
    pub(super) entry_qty: f64,
    pub(super) fill_frac: f64,
    pub(super) entry_legs: usize,
    pub(super) entry_taker: bool,
}

pub(super) fn entry_tally<B, MD>(
    bot: &B,
    asset_no: usize,
    entry_id: u64,
    legs: u8,
    saved: &[(u64, Order)],
) -> Option<EntryTally>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut entry_px_sum = 0.0;
    let mut entry_notional = 0.0;
    let mut entry_qty = 0.0;
    let mut entry_legs = 0usize;
    let mut entry_taker = false;
    let mut ordered = 0.0;
    for i in 0..u64::from(legs.max(1)) {
        let Some(o) = order_of(bot, asset_no, saved, entry_id.saturating_add(i)) else {
            continue;
        };
        if !matches!(o.status, Status::Rejected | Status::Expired) {
            ordered += o.qty;
        }
        let exec = executed_qty(o);
        if exec > 0.0 {
            entry_qty += exec;
            let notional = executed_notional(o);
            entry_px_sum += notional / exec;
            entry_notional += notional;
            entry_legs += 1;
            entry_taker |= !o.maker;
        }
    }
    if entry_legs == 0 {
        return None;
    }
    Some(EntryTally {
        entry_px: entry_px_sum / crate::stats::count_f64(entry_legs),
        entry_vwap: if entry_qty > 0.0 {
            entry_notional / entry_qty
        } else {
            0.0
        },
        entry_qty,
        fill_frac: if ordered > 0.0 {
            entry_qty / ordered
        } else {
            0.0
        },
        entry_legs,
        entry_taker,
    })
}

/// Хвост одного варианта группы: своя нога выхода (`exits`), общая нога входа
/// (`entry`) — та же сборка `Fill`, что у сольного `run_round` (только выход
/// у каждого варианта свой).
#[allow(clippy::too_many_arguments)]
pub(super) fn build_group_outcome<B, MD>(
    bot: &B,
    asset_no: usize,
    entry_id: u64,
    legs: u8,
    side: HbtSide,
    fill_by_cross: bool,
    entry: &EntryTally,
    exits: &[(u64, ExitReason, bool)],
    saved: &[(u64, Order)],
) -> RoundOutcome
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let Some(&(_, reason, _)) = exits.last() else {
        return RoundOutcome::Inconsistent;
    };
    let mut exit_qty = 0.0;
    let mut exit_notional = 0.0;
    let mut exit_ts = i64::MIN;
    let mut exit_taker = false;
    for (exit_id, _, _) in exits {
        if let Some(o) = bot
            .orders(asset_no)
            .get(exit_id)
            .filter(|o| executed_qty(o) > 0.0)
        {
            let exec = executed_qty(o);
            exit_qty += exec;
            exit_notional += executed_notional(o);
            exit_ts = exit_ts.max(o.exch_timestamp);
            exit_taker |= !o.maker;
        }
    }
    let exit_ok = exit_qty > 0.0 && exit_qty + lot_half(bot.depth(asset_no)) >= entry.entry_qty;
    if !exit_ok {
        return RoundOutcome::Inconsistent;
    }
    let dir = if side == HbtSide::Buy { 1 } else { -1 };
    RoundOutcome::Filled {
        fill: Fill {
            dir,
            entry_px: entry.entry_px,
            exit_px: exit_notional / exit_qty,
            qty: entry.entry_qty,
            entry_taker: entry.entry_taker,
            exit_taker,
            entry_vwap: entry.entry_vwap,
            fill_frac: entry.fill_frac,
            legs_filled: u8::try_from(entry.entry_legs).unwrap_or(u8::MAX),
            legs_rejected: rejected_legs_with(bot, asset_no, entry_id, legs, saved),
            fill_by_cross,
            tape_press: 0.0,
            tape_press_ws: [0.0; TAPE_WS_MAX],
            cxl_press: 0.0,
            chase_wait_ns: -1,
        },
        exit_ts,
        reason,
        partial: exits.iter().any(|(_, _, partial)| *partial),
    }
}

/// Круг группы форм (Э-08): общий вход разворачивается один раз (`entry_state`, уже в
/// `EntryPending` — вызывающий отправил его как в сольном `drive_signal`), а после форка
/// (переход в `Holding`) каждый вариант ведёт свой выход своими заявками (`variant_next_ids` —
/// непересекающиеся диапазоны номеров) в том же движке до своего `Idle`. Точно: наши заявки не
/// двигают глубину модели очереди (`queue_model.trade` не декрементирует объём между заявками,
/// `partialfillexchange.rs` крейта) — К вариантов на одном движке эквивалентны К сольным прогонам
/// с тем же входом (тикет Э-08, обоснование там же).
///
/// Без пропуска пустых шагов удержания (Э-04б): группа всегда шагает прежним шагом
/// `ON_EVENT_POLL_STEP_NS`. Совместный пропуск потребовал бы порога — минимума по всем вариантам —
/// и «есть открытые заявки» отдельно на клон (`has_open_orders` сейчас смотрит на все заявки
/// актива разом, не различая, чьи они), что в бюджет этой задачи не вошло: `--exit-group on` не
/// снижает эффект `--hold-step skip` там, где применимо (гейт — `group_matches_solo_with_hold_skip`
/// в тестах), но и не пользуется им внутри группы.
///
/// Возвращает исход и позицию плана на конце круга по каждому варианту, в порядке `variant_plans`.
#[allow(clippy::too_many_arguments)]
pub(super) fn run_round_group<B, MD>(
    bot: &mut B,
    asset_no: usize,
    entry_state: &mut StrategyState,
    entry_id: u64,
    legs: u8,
    side: HbtSide,
    variant_plans: &[TradePlan],
    variant_next_ids: &[u64],
    skip_cap: Option<i64>,
    walls: Option<&[WallEvent]>,
) -> Result<Vec<GroupRoundResult>, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    debug_assert_eq!(variant_plans.len(), variant_next_ids.len());
    // Круг кончился без форка (конец записи/таймаут входа) — исход, позиция, счётчики F8b/F8c и
    // сироты общего входа одни на всю группу: форк ещё не случился, у вариантов своей истории нет.
    let broadcast = |entry_state: &mut StrategyState,
                     outcome: RoundOutcome,
                     n: usize,
                     idle_ns: i64|
     -> Vec<GroupRoundResult> {
        let tail = (
            entry_state.position(),
            entry_state.exit_cancel_timeouts(),
            entry_state.orphan_fills(),
            entry_state.take_orphans(),
            entry_state.gate_measure(),
        );
        std::iter::repeat_with(move || GroupRoundResult {
            outcome: outcome.clone(),
            position: tail.0,
            exit_cancel_timeouts: tail.1,
            orphan_fills: tail.2,
            gate_measure: tail.4,
            idle_ns,
            carry_out: tail.3,
        })
        .take(n)
        .collect()
    };
    // --- общий вход: тот же цикл, что у сольного `run_round`, до `Holding` либо до конца круга
    // без входа (таймаут/конец записи) — тогда исход один на всю группу. ---
    let mut timed_out = false;
    let mut cancel_reason = EntryCancelReason::NotPlaced;
    let mut entry_seen: u64 = 0;
    let mut entry_pending: u64 = 0;
    let mut fill_by_cross = false;
    let mut entry_stable = false;
    loop {
        let entry_wake = match skip_cap {
            Some(_) if entry_stable && entry_pending == 0 && entry_state.is_entry_pending() => {
                entry_state.step_wakeup_ns(bot.current_timestamp())
            }
            _ => None,
        };
        let entry_stepped = match (entry_wake, skip_cap) {
            (Some(th), Some(cap)) => {
                let resp = !entry_state.is_holding() || has_open_orders(bot, asset_no);
                hold_step(bot, th, cap, resp)?
            }
            _ => bot.elapse(ON_EVENT_POLL_STEP_NS)?,
        };
        if entry_stepped == ElapseResult::EndOfData {
            let now = bot.current_timestamp();
            return Ok(broadcast(
                entry_state,
                RoundOutcome::EndOfData,
                variant_plans.len(),
                now,
            ));
        }
        if entry_pending != 0 {
            fill_by_cross |= pending_is_cross(bot, asset_no, entry_id, side, entry_pending, legs);
            entry_pending = 0;
        }
        for i in 0..u32::from(legs.max(1)) {
            if i >= u64::BITS {
                break;
            }
            let bit = 1u64 << i;
            if entry_seen & bit != 0 {
                continue;
            }
            let Some(o) = bot
                .orders(asset_no)
                .get(&entry_id.saturating_add(u64::from(i)))
            else {
                continue;
            };
            if executed_qty(o) <= 0.0 {
                continue;
            }
            entry_seen |= bit;
            let tick = bot.depth(asset_no).tick_size();
            if !trade_could_fill(bot.last_trades(asset_no), side, o.price_tick, tick) {
                entry_pending |= bit;
            }
        }
        if entry_pending == 0 {
            entry_state.observe_cancels(
                bot.depth(asset_no),
                bot.last_trades(asset_no),
                bot.current_timestamp(),
            );
            entry_state.observe_wall_trades(bot.last_trades(asset_no));
            bot.clear_last_trades(Some(asset_no));
        }
        let entry_mark = entry_state.phase_mark();
        let entry_action = on_event(bot, entry_state)?;
        entry_stable =
            entry_state.phase_mark() == entry_mark && matches!(entry_action, Action::Idle);
        match entry_action {
            Action::EntryTimedOut { reason, .. } => {
                timed_out = true;
                cancel_reason = reason;
            }
            Action::Idle | Action::EntrySubmitted { .. } => {}
            Action::ExitSubmitted { .. } => {
                // Общий вход `on_event` не выходит — план начал бы держать позицию только у
                // одиночного `Bounce`/`SpreadHold` (`decide_exit` читает `Phase::Holding`, форк
                // сюда не доходит): если это случилось, дальше по группе идти нельзя.
                unreachable!("общий вход круга группы не выходит — форк раньше, в Holding")
            }
        }
        if entry_state.is_idle() {
            debug_assert!(timed_out, "вход решился Idle не через таймаут");
            let entry_status = bot.orders(asset_no).get(&entry_id).map(|o| o.status);
            let outcome = RoundOutcome::TimedOut {
                entry_status,
                legs_rejected: rejected_legs(bot, asset_no, entry_id, legs),
                reason: cancel_reason,
            };
            let now = bot.current_timestamp();
            return Ok(broadcast(entry_state, outcome, variant_plans.len(), now));
        }
        if entry_state.is_holding() {
            break;
        }
    }
    // --- форк: К клонов состояния входа, каждому — свой план (выход) и свой диапазон заявок. ---
    // Г-65 (e65): время исполнения входа — для выбора живых стен журнала; буфер один на форк.
    let fill_ms = bot.current_timestamp() / 1_000_000;
    let mut wall_live: Vec<i64> = Vec::new();
    let mut states: Vec<StrategyState> = variant_plans
        .iter()
        .zip(variant_next_ids)
        .map(|(&plan, &id)| {
            let mut s = entry_state.clone();
            match walls {
                Some(w) => s.set_plan_wall2(plan, w, fill_ms, side == HbtSide::Buy, &mut wall_live),
                None => s.set_plan(plan),
            }
            s.set_next_order_id(id);
            s
        })
        .collect();
    let n = states.len();
    let mut exits: Vec<Vec<(u64, ExitReason, bool)>> = vec![Vec::new(); n];
    let mut outcome: Vec<Option<RoundOutcome>> = vec![None; n];
    // Своя метка часов на клон — в момент, когда **этот** вариант вернулся в `Idle`, а не после
    // общего цикла (движок группы шагает, пока не решатся все К вариантов; вариант, решившийся
    // раньше соседей, иначе унаследовал бы их более позднюю метку — `SignalStep::idle_ns` тогда
    // разошёлся бы с сольным кругом того же варианта, находка теста при разработке Э-08).
    let mut idle_ns: Vec<i64> = vec![0; n];
    // Э-04б в группе (условие Судьи 1 к Э-08): пропуск пустых шагов — только если у **всех** живых
    // вариантов решение удержания уже принято на этой точке (`decided`), заявок в движке нет, а порог —
    // минимум по вариантам. После форка первое решение ещё не считано (как у сольного круга).
    let mut decided: Vec<bool> = vec![false; n];
    let mut stable: Vec<bool> = vec![false; n];
    let mut sigs: Vec<SigMemo> = vec![SigMemo::default(); n];
    // TK-048 (быстрый путь): один заход группового быстрого пути сразу после форка; ноги входа
    // сохраняются перед заменой движка (как `saved` сольного `run_round`).
    let mut fast_tried = !fast_hold::fast_hold_on();
    let mut post_step = false;
    let mut saved: Vec<(u64, Order)> = Vec::new();
    loop {
        if outcome.iter().all(Option::is_some) {
            break;
        }
        if !fast_tried
            && entry_pending == 0
            && skip_cap.is_some()
            && states
                .iter()
                .all(|s| s.is_holding() && s.hold_wakeup_ns(bot.current_timestamp()).is_some())
            && !has_open_orders(bot, asset_no)
        {
            fast_tried = true;
            let legs_saved: Vec<(u64, Order)> = (0..u64::from(legs.max(1)))
                .filter_map(|i| {
                    let id = entry_id.saturating_add(i);
                    bot.orders(asset_no).get(&id).map(|o| (id, o.clone()))
                })
                .collect();
            if let Some(cap) = skip_cap {
                if let Some(ps) = fast_hold::try_fast_hold_group(
                    bot,
                    asset_no,
                    &mut states,
                    cap,
                    &mut decided,
                    &mut stable,
                    &mut sigs,
                ) {
                    post_step = ps;
                    saved = legs_saved;
                }
            }
        }
        let skip_step = std::mem::take(&mut post_step);
        let wakeup = match skip_cap {
            Some(_) if !has_open_orders(bot, asset_no) => {
                let now = bot.current_timestamp();
                let mut th: Option<i64> = None;
                let mut all = true;
                for i in 0..n {
                    if outcome[i].is_some() {
                        continue;
                    }
                    match (decided[i], states[i].hold_wakeup_ns(now)) {
                        (true, Some(t)) => th = Some(th.map_or(t, |x: i64| x.min(t))),
                        _ => {
                            all = false;
                            break;
                        }
                    }
                }
                th.filter(|_| all)
            }
            _ => None,
        };
        let stepped = match (wakeup, skip_cap) {
            _ if skip_step => ElapseResult::Ok,
            (Some(th), Some(cap)) => {
                let resp = has_open_orders(bot, asset_no)
                    || states
                        .iter()
                        .zip(&outcome)
                        .any(|(s, o)| o.is_none() && !s.is_holding());
                hold_step(bot, th, cap, resp)?
            }
            _ => bot.elapse(ON_EVENT_POLL_STEP_NS)?,
        };
        if stepped == ElapseResult::EndOfData {
            let now = bot.current_timestamp();
            for (o, t) in outcome.iter_mut().zip(idle_ns.iter_mut()) {
                if o.is_none() {
                    *o = Some(RoundOutcome::EndOfData);
                    *t = now;
                }
            }
            break;
        }
        // Путь исполнения входа (F3) — как у сольного `run_round` на каждом шаге и после форка:
        // отложенный вердикт ноги и ноги лестницы, исполнившиеся уже в удержании.
        if entry_pending != 0 {
            fill_by_cross |= pending_is_cross(bot, asset_no, entry_id, side, entry_pending, legs);
            entry_pending = 0;
        }
        for i in 0..u32::from(legs.max(1)) {
            if i >= u64::BITS {
                break;
            }
            let bit = 1u64 << i;
            if entry_seen & bit != 0 {
                continue;
            }
            let Some(o) = order_of(bot, asset_no, &saved, entry_id.saturating_add(u64::from(i)))
            else {
                continue;
            };
            if executed_qty(o) <= 0.0 {
                continue;
            }
            entry_seen |= bit;
            let tick = bot.depth(asset_no).tick_size();
            if !trade_could_fill(bot.last_trades(asset_no), side, o.price_tick, tick) {
                entry_pending |= bit;
            }
        }
        // F7 (Б-75): все живые варианты видят сделки этого шага **до** общей очистки буфера —
        // как у сольного `run_round`, только К раз вместо одного (и так же откладывается, пока
        // вердикт ноги входа ждёт шага).
        if entry_pending == 0 {
            for (i, s) in states.iter_mut().enumerate() {
                if outcome[i].is_none() {
                    s.observe_cancels(
                        bot.depth(asset_no),
                        bot.last_trades(asset_no),
                        bot.current_timestamp(),
                    );
                    s.observe_wall_trades(bot.last_trades(asset_no));
                }
            }
            bot.clear_last_trades(Some(asset_no));
        }
        for i in 0..n {
            if outcome[i].is_some() {
                continue;
            }
            let held_before = states[i].hold_wakeup_ns(bot.current_timestamp()).is_some();
            let mark_before = states[i].phase_mark();
            sigs[i].stat_step(false, || {
                states[i].hold_input_sig(bot.depth(asset_no), bot.current_timestamp())
            });
            let skip = sigs[i]
                .skip(states[i].hold_input_sig(bot.depth(asset_no), bot.current_timestamp()));
            let act = if skip {
                Action::Idle
            } else {
                on_event(bot, &mut states[i])?
            };
            if !matches!(act, Action::Idle) {
                sigs[i].reset();
            }
            stable[i] = states[i].phase_mark() == mark_before && matches!(act, Action::Idle);
            match act {
                Action::ExitSubmitted {
                    order_id,
                    reason,
                    partial,
                    ..
                } => exits[i].push((order_id, reason, partial)),
                Action::Idle | Action::EntrySubmitted { .. } | Action::EntryTimedOut { .. } => {}
            }
            decided[i] = held_before && states[i].hold_wakeup_ns(bot.current_timestamp()).is_some();
            if states[i].is_idle() {
                idle_ns[i] = bot.current_timestamp();
                // Сольный круг на своём конце добирает отложенный вердикт ноги входа.
                let fbc = fill_by_cross
                    || (entry_pending != 0
                        && pending_is_cross(bot, asset_no, entry_id, side, entry_pending, legs));
                let mut out = match entry_tally(bot, asset_no, entry_id, legs, &saved) {
                    Some(entry) => build_group_outcome(
                        bot, asset_no, entry_id, legs, side, fbc, &entry, &exits[i], &saved,
                    ),
                    None => RoundOutcome::Inconsistent,
                };
                if let RoundOutcome::Filled { fill, .. } = &mut out {
                    fill.tape_press = states[i].tape_at_fill();
                    fill.tape_press_ws = states[i].tape_at_fill_ws();
                    fill.cxl_press = states[i].cxl_at_fill();
                    fill.chase_wait_ns = states[i].chase_wait_ns();
                }
                outcome[i] = Some(out);
            }
        }
    }
    Ok(outcome
        .into_iter()
        .zip(states)
        .zip(idle_ns)
        .map(|((o, mut s), t)| GroupRoundResult {
            outcome: o.unwrap_or(RoundOutcome::Inconsistent),
            position: s.position(),
            exit_cancel_timeouts: s.exit_cancel_timeouts(),
            orphan_fills: s.orphan_fills(),
            gate_measure: s.gate_measure(),
            carry_out: s.take_orphans(),
            idle_ns: t,
        })
        .collect())
}

/// Итог одного варианта группового круга (Э-08): то же, что сольный `drive_signal` берёт из
/// `RoundOutcome` и `StrategyState` напрямую, — позиция для страховки остатка, счётчики F8b/F8c и
/// сироты, унаследуемые следующим сигналом этого же варианта.
pub(super) struct GroupRoundResult {
    pub(super) outcome: RoundOutcome,
    /// Своя позиция плана на конце круга (F4, В-78) — по ней страховка остатка сигнала.
    pub(super) position: f64,
    pub(super) exit_cancel_timeouts: u64,
    pub(super) orphan_fills: u64,
    pub(super) gate_measure: Option<f64>,
    pub(super) carry_out: OrphanCarry,
    /// Часы стороны в момент, когда **этот** вариант вернулся в `Idle` (`SignalStep::idle_ns` у
    /// сольного круга) — своя метка на клон, взятая **в момент форка/исполнения именно его**, а
    /// не после общего цикла: движок группы шагает, пока не решатся все К вариантов, и вариант,
    /// решившийся раньше соседей, не должен получить их более позднюю метку (иначе `idle_ns`,
    /// а с ним и `signals.csv`, разошлись бы с сольным кругом того же варианта — находка теста
    /// `group_round_matches_three_solo_runs_with_divergent_exits`, разбор Э-08).
    pub(super) idle_ns: i64,
}

/// Отсчёт заявок общего круга группы (Э-08) — отдельное от `--first-order-id` (умолчание `1`)
/// пространство: сирота, унаследованная из группового круга, никогда не совпадёт номером с
/// заявкой сольного круга того же варианта (`drive_signal`), даже если сироту не заметить и
/// вариант потом поведут сольно (соседний сигнал без группы — не пустые сироты, T-38). Место —
/// `u64` половина, нет практического исчерпания на сутки сигналов.
pub(super) const GROUP_ID_BASE: u64 = 1 << 40;

/// Ширина диапазона номеров заявок на вариант группы (TK-347): выход берёт новый номер на каждую заявку
/// (перестановка по цене, дедлайн-погоня, добор, страховка остатка) — при прежних `ID_STRIDE` = 12
/// вариант с длинной погоней вылезал в диапазон соседа и падал «Order id exists» (drive.rs:263, MYXUSDT
/// 2026-02-13/02-01). Движок группы свежий на сигнал, поэтому номера нужны лишь уникальные внутри него.
pub(super) const VARIANT_ID_SPAN: u64 = 1 << 20;

/// Первый номер заявок выхода варианта `k` (вход — `[base, base + SPAN)`, варианты — следом).
pub(super) fn group_variant_id(entry_id_base: u64, k: u64) -> u64 {
    entry_id_base.saturating_add(VARIANT_ID_SPAN.saturating_mul(k.saturating_add(1)))
}

/// Первый номер страховки остатка варианта `i` из `n` — за диапазонами всех вариантов.
pub(super) fn group_flatten_id(entry_id_base: u64, n: u64, i: u64) -> u64 {
    group_variant_id(entry_id_base, n.saturating_add(i))
}
