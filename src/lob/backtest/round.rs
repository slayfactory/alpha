//! Один круг сигнала: вход, ожидание, выход, гашение остатка (`run_round`).

use super::*;

/// Остаток позиции круга со знаком для `flatten_residual` (B4, ревью 23.09):
/// `StrategyState::position()` — объём без знака (`entry − exit ≥ 0`) у обеих
/// сторон, а `flatten_residual` читает знак как сторону позиции (`> 0` — лонг,
/// гасится продажей; `< 0` — шорт, гасится покупкой). Прежде остаток шорта
/// приходил положительным и «гасился» продажей — шорт удваивался вместо
/// закрытия.
pub(super) fn signed_residual(entry_side: HbtSide, position: f64) -> f64 {
    match entry_side {
        HbtSide::Sell => -position,
        _ => position,
    }
}

pub(super) enum FlattenOutcome {
    /// Остаток закрыт.
    Flat,
    /// Заявка стала терминальной, а остаток больше нуля (нет ликвидности на
    /// лучшей цене): вызывающий шлёт новую с новым `order_id`.
    Retry {
        left: f64,
    },
    EndOfData,
}

/// Половина шага лота — допуск «объём сошёлся»: дробный выход округляется
/// **вниз** до шага лота (E7), и требовать равенства сумм в `f64` значило бы
/// проверять арифметику, а не позицию.
pub(super) fn lot_half<MD: MarketDepth>(depth: &MD) -> f64 {
    let lot = depth.lot_size();
    if lot.is_finite() && lot > 0.0 {
        lot * 0.5
    } else {
        0.0
    }
}

/// Закрыть остаток позиции по рынку (IOC) и дождаться ответа биржи.
/// `left` — сколько остатка закрыть, **своей** позиции круга (F4/В-78):
/// `Bot::position` крейта частичного исполнения не видит (обновляется только
/// на `Filled`), и по нему остаток либо теряется, либо остаётся мнимым.
pub(super) fn flatten_residual<B, MD>(
    bot: &mut B,
    asset_no: usize,
    order_id: u64,
    left: f64,
) -> Result<FlattenOutcome, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let d = bot.depth(asset_no);
    let (bid, ask) = (d.best_bid(), d.best_ask());
    let ok_px = |px: f64| px.is_finite() && px > 0.0;
    if left > 0.0 {
        if !ok_px(bid) {
            return match bot.elapse(ON_EVENT_POLL_STEP_NS)? {
                ElapseResult::EndOfData => Ok(FlattenOutcome::EndOfData),
                _ => Ok(FlattenOutcome::Retry { left }),
            };
        }
        bot.submit_sell_order(
            asset_no,
            order_id,
            bid,
            left,
            TimeInForce::IOC,
            OrdType::Market,
            false,
        )?;
    } else {
        if !ok_px(ask) {
            return match bot.elapse(ON_EVENT_POLL_STEP_NS)? {
                ElapseResult::EndOfData => Ok(FlattenOutcome::EndOfData),
                _ => Ok(FlattenOutcome::Retry { left }),
            };
        }
        bot.submit_buy_order(
            asset_no,
            order_id,
            ask,
            -left,
            TimeInForce::IOC,
            OrdType::Market,
            false,
        )?;
    }
    let half_lot = lot_half(bot.depth(asset_no));
    loop {
        if bot.elapse(ON_EVENT_POLL_STEP_NS)? == ElapseResult::EndOfData {
            return Ok(FlattenOutcome::EndOfData);
        }
        // Готовность — по исполненному объёму заявки (`qty − leaves_qty`), а не
        // по `Bot::position`: частичное исполнение крейт в позиции не видит
        // (находка F3), и после частичного входа она врёт со знаком.
        let Some(o) = bot.orders(asset_no).get(&order_id) else {
            return Ok(FlattenOutcome::Flat);
        };
        let executed = executed_qty(o);
        let resolved = o.req == Status::None
            && !matches!(
                o.status,
                Status::None | Status::New | Status::PartiallyFilled
            );
        if !resolved {
            continue;
        }
        let rest = left.abs() - executed;
        if rest <= half_lot {
            return Ok(FlattenOutcome::Flat);
        }
        return Ok(FlattenOutcome::Retry {
            left: if left > 0.0 { rest } else { -rest },
        });
    }
}

/// Сколько ног входа ставит этот план: у Decision 20 — одна, у лестницы
/// В-44 — `grid_legs`, у лестницы формы F6 (`ladder.n > 0`) — её ноги
/// (совпавшие тики сложены, так что число ног — уже итоговое). Нужно драйверу,
/// чтобы найти исполненные ноги.
pub(super) fn legs_of(plan: TradePlan) -> u8 {
    match plan {
        TradePlan::SpreadHold => 1,
        TradePlan::Bounce {
            grid_legs, ladder, ..
        } => {
            if ladder.n > 0 {
                ladder.n
            } else {
                grid_legs.max(1)
            }
        }
    }
}

/// Самая близкая к рынку цена входа плана (F6, В-73): у лестницы формы — её
/// дальняя нога (`to` bps), у прежнего входа — `entry_px`. По ней драйвер
/// отвечает на вопрос «пересекает ли вход спред» (пост-онли биржа такую ногу
/// не ставит, `Expired`), и она же — цена, до которой цена доходит к нашей
/// заявке первой. `None` — план без входа-отскока.
pub(super) fn entry_market_px(plan: TradePlan) -> Option<f64> {
    match plan {
        TradePlan::SpreadHold => None,
        TradePlan::Bounce {
            entry_px,
            tick_px,
            ladder,
            ..
        } => match ladder.outer_tick() {
            #[allow(clippy::cast_precision_loss)]
            Some(t) => Some(t as f64 * tick_px),
            None => Some(entry_px),
        },
    }
}

/// Итог одного круга в терминах драйвера.
///
/// `large_enum_variant`: `Filled` несёт `Fill` (TK-272: +128 Б на окна `--tape-log-ws`), остальные варианты малы.
/// Значение живёт на стеке драйвера один круг и не копится; `Box` внёс бы аллокацию на круг ради десятков байт.
#[allow(clippy::large_enum_variant)]
#[derive(Debug, Clone, PartialEq)]
pub(super) enum RoundOutcome {
    /// Круг закрыт: обе ноги исполнены, причина выхода известна.
    Filled {
        fill: Fill,
        exit_ts: i64,
        /// Причина **последней** ноги выхода.
        reason: ExitReason,
        /// Выход шёл двумя ногами (E7).
        partial: bool,
    },
    /// Вход не исполнился за время жизни плана и снят **либо** ни одной ноги
    /// не поставила биржа (пост-онли заявка пересекла спред, `Expired`, В-72)
    /// — в обоих случаях позиции нет, а у сигнала не было входа. Статус
    /// первого входного ордера несётся наружу: `Rejected` — это отвергнутая
    /// заявка (например, пост-онли, пересекающая спред), а не «просто не
    /// дошло» — разница ровно та, ради которой делается замер. `legs_rejected`
    /// — ног, которых биржа не поставила (счётчик `n_rejected_postonly`).
    TimedOut {
        entry_status: Option<Status>,
        legs_rejected: u8,
        /// Почему вход кончился без позиции (F5, В-74): потолок, смерть стены
        /// или уход цены из полосы; `NotPlaced` — биржа не поставила ни одной
        /// ноги (В-72, «сигнал без входа»).
        reason: EntryCancelReason,
    },
    /// Данные кончились посреди круга.
    EndOfData,
    /// `Idle` без выхода и без таймаута — рассинхрон с данными.
    Inconsistent,
}

/// Может ли хоть одна сделка буфера исполнить эту ногу: для покупки —
/// сделка продавца-агрессора по цене **не выше** нашей (наш лимит впереди
/// неё — приоритет цены), для продажи — зеркально. Пусто — в этом шаге
/// опроса исполнения лентой не было, и нога исполнилась обновлением лучшей
/// цены (путь (3) `build_backtest`).
pub(super) fn trade_could_fill(
    trades: &[Event],
    side: HbtSide,
    price_tick: i64,
    tick: f64,
) -> bool {
    if tick <= 0.0 {
        return false;
    }
    trades.iter().any(|t| {
        let t_tick = (t.px / tick).round() as i64;
        match side {
            HbtSide::Buy => t.is(SELL_EVENT) && t_tick <= price_tick,
            HbtSide::Sell => t.is(BUY_EVENT) && t_tick >= price_tick,
            _ => false,
        }
    })
}

/// Исполненный объём заявки накопленным итогом: у крейта `exec_qty` — объём
/// **последнего** исполнения, а не сумма (частичное исполнение приходит
/// несколькими откликами), поэтому накопленное — `qty − leaves_qty`.
/// `pub(crate)` — тем же счётом живёт позиция круга в стратегии
/// (`lob::strategy`, F4/В-78): частичное исполнение `Bot::position` крейта не
/// видит.
pub(crate) fn executed_qty(order: &Order) -> f64 {
    (order.qty - order.leaves_qty).max(0.0)
}

/// Стоимость исполненного заявки накопленным итогом, `Σ exec_price ×
/// exec_qty` по всем её исполнениям (R7, ревью 23.09). `exec_price()` крейта
/// — цена **последнего** исполнения: рыночный выход, прошедший несколько
/// уровней книги, оценивался бы целиком по худшему уровню, а вход-лимитка,
/// часть которой взяла книгу тейкером, — по цене своего остатка. Итог ведёт
/// биржа модели (вендорная правка `Order::exec_notional`,
/// `docs/findings/hftbacktest-partialfill-fix-2026-09-20.md` §R7). Заявка,
/// чей учёт итога не вёл (нуль при ненулевом исполненном — отклик площадки
/// без этого поля), оценивается прежним `exec_price × исполненное`, а не
/// нулём: живой коннектор обязан вести итог, иначе средняя по свипу снова
/// будет ценой последнего уровня.
pub(crate) fn executed_notional(order: &Order) -> f64 {
    let executed = executed_qty(order);
    if order.exec_notional > 0.0 || executed <= 0.0 {
        order.exec_notional
    } else {
        order.exec_price() * executed
    }
}

/// Отложенный вердикт пути исполнения (F3): для ног из `pending` в буфере
/// последних сделок так и не нашлось сделки, которая могла бы их исполнить, —
/// значит исполнение пришло обновлением лучшей цены (путь (3)
/// `build_backtest`), а не лентой. Откладывается на шаг опроса, потому что
/// сделка в буфере метится **локальным** временем, а исполнение — биржевым:
/// при расхождении меток сделка оказывается в буфере следующим шагом, и
/// мгновенный вердикт звал бы крест там, где была сделка.
pub(super) fn pending_is_cross<B, MD>(
    bot: &B,
    asset_no: usize,
    entry_id: u64,
    side: HbtSide,
    pending: u64,
    legs: u8,
) -> bool
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    if pending == 0 {
        return false;
    }
    let tick = bot.depth(asset_no).tick_size();
    for i in 0..u32::from(legs.max(1)) {
        if i >= u64::BITS {
            break;
        }
        if pending & (1u64 << i) == 0 {
            continue;
        }
        let price_tick = bot
            .orders(asset_no)
            .get(&entry_id.saturating_add(u64::from(i)))
            .map(|o| o.price_tick);
        match price_tick {
            // Заявки уже нет (снята) — судить не по чему: считаем крестом
            // (оценка оптимизма вверх, а не вниз — консервативно для нас).
            None => return true,
            Some(px) => {
                if !trade_could_fill(bot.last_trades(asset_no), side, px, tick) {
                    return true;
                }
            }
        }
    }
    false
}

/// Есть ли у круга заявки, ещё живые в крейте (не исполнены целиком, не сняты, не отвергнуты): пока есть,
/// ответы биржи идут по своим часам — пустые шаги опроса не пропускаются (Э-04б).
pub(super) fn has_open_orders<B, MD>(bot: &B, asset_no: usize) -> bool
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    bot.orders(asset_no)
        .values()
        .any(|o| matches!(o.status, Status::New | Status::PartiallyFilled))
}

/// Шаг удержания с пропуском пустых шагов опроса (Э-04б, T-38). Пошаговый опрос проходит точки
/// `now + k · ON_EVENT_POLL_STEP_NS`; решение удержания без событий рынка меняется только на порогах
/// времени (`hold_wakeup_ns`). Поэтому: ждём следующее событие ленты не дальше первой точки сетки ≥ порога
/// и встаём на первую точку сетки ≥ этого события — крейт к ней обработал ровно те же события в том же
/// порядке, что и пошаговый опрос (`goto` идёт по одной очереди событий, границы шагов её не меняют).
/// `cap` — конец данных круга (меньшая из меток последней строки): дальше последней точки сетки до него
/// не прыгаем, чтобы исчерпание данных (`EndOfData`, часы крейта не двигаются) случилось на том же шаге,
/// что и у опроса.
pub(super) fn hold_step<B, MD>(
    bot: &mut B,
    wakeup_ns: i64,
    cap: i64,
    include_order_resp: bool,
) -> Result<ElapseResult, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let step = ON_EVENT_POLL_STEP_NS;
    let now = bot.current_timestamp();
    // Точка сетки ≥ порога, не ближе следующей; и последняя точка сетки строго до конца данных.
    let k_wake = wakeup_ns.saturating_sub(now).div_euclid(step)
        + i64::from(wakeup_ns.saturating_sub(now).rem_euclid(step) != 0);
    let k_cap = (cap.saturating_sub(now) - 1).div_euclid(step);
    let k = k_wake.min(k_cap);
    if k <= 1 {
        return bot.elapse(step);
    }
    let target = now.saturating_add(k.saturating_mul(step));
    HOLD_SKIPS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    match bot.wait_next_feed(include_order_resp, target - now)? {
        ElapseResult::MarketFeed | ElapseResult::OrderResponse => {
            let e = bot.current_timestamp();
            let ke = (e - now).div_euclid(step) + i64::from((e - now).rem_euclid(step) != 0);
            let g = now.saturating_add(ke.max(1).saturating_mul(step));
            bot.elapse(g - e)
        }
        other => Ok(other),
    }
}

pub(super) fn run_round<B, MD>(
    bot: &mut B,
    asset_no: usize,
    state: &mut StrategyState,
    entry_id: u64,
    legs: u8,
    side: HbtSide,
    skip_cap: Option<i64>,
) -> Result<(RoundOutcome, f64), B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut timed_out = false;
    // Почему вход кончился без позиции (F5, В-74): несётся из `on_event` до
    // `RoundOutcome::TimedOut`, чтобы по кругам формы посчитались снятия по
    // стене, полосе и потолку отдельно.
    let mut cancel_reason = EntryCancelReason::NotPlaced;
    // Путь исполнения входа (F3): `entry_seen` — ноги, исполнение которых уже
    // замечено; `entry_pending` — ноги, чей вердикт (сделка или крест) отложен
    // на шаг (локальная метка сделки отстаёт от биржевой); `fill_by_cross` —
    // итог по кругу. Битовая маска вместо вектора: ног у плана единицы.
    let mut entry_seen: u64 = 0;
    let mut entry_pending: u64 = 0;
    let mut fill_by_cross = false;
    // Ноги выхода в порядке отправки: одна у прежних форм, две у дробных (E7),
    // и больше при доборах остатка моделью очереди по объёму (F4). Третий
    // элемент — заявка несла **часть** позиции (`Action::partial`), а не весь
    // остаток: `n_partial` считает именно доли E7, а не число заявок выхода.
    let mut exits: Vec<(u64, ExitReason, bool)> = Vec::new();
    // Э-04б: решение удержания уже посчитано на текущей точке сетки (см. пропуск шагов ниже).
    let mut decided_in_hold = false;
    let mut stable = false;
    let mut solo_sig = SigMemo::default();
    // TK-049 (быстрый путь): один заход быстрого пути на круг; записи ног входа сохраняются перед заменой движка.
    let mut fast_tried = !fast_hold::fast_hold_on();
    let mut post_step = false;
    let mut saved: Vec<(u64, Order)> = Vec::new();
    loop {
        if !fast_tried
            && entry_pending == 0
            && exits.is_empty()
            && skip_cap.is_some()
            && state.is_holding()
            && state.hold_wakeup_ns(bot.current_timestamp()).is_some()
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
                if let fast_hold::FastOutcome::Swapped(r) = fast_hold::try_fast_hold(
                    bot,
                    asset_no,
                    state,
                    cap,
                    decided_in_hold,
                    stable,
                    solo_sig,
                ) {
                    *state = r.state.clone();
                    decided_in_hold = r.decided_in_hold;
                    stable = r.stable;
                    solo_sig = r.sig;
                    post_step = r.post_step;
                    saved = legs_saved;
                }
            }
        }
        let skip_step = std::mem::take(&mut post_step);
        // Э-04б: в удержании без заявок пустые шаги опроса пропускаются (`hold_step`), иначе — шаг 10 мс.
        // Только если решение удержания на этой точке сетки уже принято (`decided_in_hold`): шаг, на котором
        // круг **вошёл** в удержание (исполнился вход, вернулся из выхода), решения ещё не считал — первое
        // решение идёт следующим шагом и без событий (гейт 27.09: `gone50`/`eat50x80` на 03.08).
        let wakeup = match skip_cap {
            Some(_) if stable && entry_pending == 0 && state.is_entry_pending() => {
                state.step_wakeup_ns(bot.current_timestamp())
            }
            Some(_) if decided_in_hold && entry_pending == 0 && !has_open_orders(bot, asset_no) => {
                state.hold_wakeup_ns(bot.current_timestamp())
            }
            _ => None,
        };
        hftbacktest::prof::set_row_class(if has_open_orders(bot, asset_no) {
            1
        } else {
            usize::from(state.is_holding()) * 2
        });
        let stepped = match (wakeup, skip_cap) {
            _ if skip_step => ElapseResult::Ok,
            (Some(th), Some(cap)) => {
                STEP_KINDS[2].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                let resp = !state.is_holding() || has_open_orders(bot, asset_no);
                hold_step(bot, th, cap, resp)?
            }
            _ => {
                let k = usize::from(has_open_orders(bot, asset_no));
                STEP_KINDS[k].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                bot.elapse(ON_EVENT_POLL_STEP_NS)?
            }
        };
        hftbacktest::prof::set_row_class(3);
        if stepped == ElapseResult::EndOfData {
            // Хвост записи: круг неполон, `Fill` не строится — вердикт пути
            // исполнения не нужен.
            return Ok((RoundOutcome::EndOfData, state.position()));
        }
        // Отложенный вердикт: буфер за это время дорос сделками следующего
        // шага — если сделка, способная исполнить ногу, появилась, это лента.
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
            // Исполнено — накопленным (`qty − leaves_qty`), а не `exec_qty`:
            // у крейта `exec_qty` — объём **последнего** исполнения, а не сумма.
            if executed_qty(o) <= 0.0 {
                continue;
            }
            entry_seen |= bit;
            let tick = bot.depth(asset_no).tick_size();
            if !trade_could_fill(bot.last_trades(asset_no), side, o.price_tick, tick) {
                // Сделки, способной исполнить ногу, в буфере пока нет: вердикт
                // ждёт шага, чтобы локальная метка сделки догнала биржевое
                // исполнение. Если сделки не будет и на следующем шаге —
                // путь (3), обновление лучшей цены.
                entry_pending |= bit;
            }
        }
        // Буфер нужен только пока есть отложенный вердикт — иначе очищаем
        // (память не растёт с длиной круга). Перед очисткой сделки этого шага
        // отдаются стратегии: F7 (Б-75) считает по ним съеденное в стену, а
        // заново буфер не открывается — считаем ровно один раз на шаг.
        if entry_pending == 0 && !skip_step {
            state.observe_cancels(
                bot.depth(asset_no),
                bot.last_trades(asset_no),
                bot.current_timestamp(),
            );
            state.observe_wall_trades(bot.last_trades(asset_no));
            bot.clear_last_trades(Some(asset_no));
        }
        // Решение удержания принято на этой точке, только если круг был в удержании **до** вызова и остался.
        let held_before = state.hold_wakeup_ns(bot.current_timestamp()).is_some();
        let mark_before = state.phase_mark();
        solo_sig.stat_step(false, || {
            state.hold_input_sig(bot.depth(asset_no), bot.current_timestamp())
        });
        let skip =
            solo_sig.skip(state.hold_input_sig(bot.depth(asset_no), bot.current_timestamp()));
        let action = if skip {
            Action::Idle
        } else {
            on_event(bot, state)?
        };
        if !matches!(action, Action::Idle) {
            solo_sig.reset();
        }
        decided_in_hold = held_before && state.hold_wakeup_ns(bot.current_timestamp()).is_some();
        stable = state.phase_mark() == mark_before && matches!(action, Action::Idle);
        match action {
            Action::EntryTimedOut { reason, .. } => {
                timed_out = true;
                cancel_reason = reason;
            }
            Action::ExitSubmitted {
                order_id,
                reason,
                partial,
                ..
            } => exits.push((order_id, reason, partial)),
            Action::Idle | Action::EntrySubmitted { .. } => {}
        }
        if state.is_idle() {
            break;
        }
    }
    fill_by_cross |= pending_is_cross(bot, asset_no, entry_id, side, entry_pending, legs);
    // Буфер сделок очищается и на выходе из круга: сигналы бывают встык
    // (`t0` не двигает часы), и сделки прошлого круга не должны решать вердикт
    // следующего. Сделки последнего шага перед этим отдаются стратегии (F7).
    state.observe_cancels(
        bot.depth(asset_no),
        bot.last_trades(asset_no),
        bot.current_timestamp(),
    );
    state.observe_wall_trades(bot.last_trades(asset_no));
    bot.clear_last_trades(Some(asset_no));
    if timed_out {
        let entry_status = order_of(bot, asset_no, &saved, entry_id).map(|o| o.status);
        return Ok((
            RoundOutcome::TimedOut {
                entry_status,
                legs_rejected: rejected_legs_with(bot, asset_no, entry_id, legs, &saved),
                reason: cancel_reason,
            },
            state.position(),
        ));
    }
    let Some(&(_, reason, _)) = exits.last() else {
        return Ok((RoundOutcome::Inconsistent, state.position()));
    };
    // Исполненные ноги входа. Отбор — **по факту исполнения** (`exec_qty`), а
    // не по статусу `Filled`: модель очереди по объёму (F3) отдаёт ногу
    // частично исполненной (`PartiallyFilled`), а снятая после частичного
    // исполнения нога — `Canceled` с ненулевым `exec_qty`. У прежней модели
    // (`RiskAdverse`) множество то же: там исполнение всегда целое.
    //
    // Цена входа: `entry_px` — средняя с равными весами (как было: у лестницы
    // ноги равного размера), `entry_vwap` — средневзвешенная по исполненному
    // размеру; при полном исполнении они совпадают, и `roundtrip_net_bps`
    // считает по `entry_vwap` (F3).
    let mut entry_px_sum = 0.0;
    let mut entry_notional = 0.0;
    let mut entry_qty = 0.0;
    let mut entry_legs = 0usize;
    let mut entry_taker = false;
    let mut ordered = 0.0;
    for i in 0..u64::from(legs.max(1)) {
        let Some(o) = order_of(bot, asset_no, &saved, entry_id.saturating_add(i)) else {
            continue;
        };
        // Заказанное — только принятые биржей ноги: отвергнутая (`Rejected`,
        // `Expired`) нога — отказ, а не заказ, и `fill_frac` из-за неё падать
        // не должен (F4 считает её отдельным счётчиком).
        if !matches!(o.status, Status::Rejected | Status::Expired) {
            ordered += o.qty;
        }
        let exec = executed_qty(o);
        if exec > 0.0 {
            entry_qty += exec;
            // R7: средняя цена ноги — по всем её исполнениям, не цена
            // последнего уровня свипа.
            let notional = executed_notional(o);
            entry_px_sum += notional / exec;
            entry_notional += notional;
            entry_legs += 1;
            // Комиссия ноги — по флагу `maker` ордера крейта (В-63): у
            // лестницы вход тейкерский, если тейкером исполнилась хотя бы одна
            // нога (консервативно).
            entry_taker |= !o.maker;
        }
    }
    // R2-A: добавки доливки — часть позиции; в среднюю равных весов (`entry_px`) не входят.
    for &id in state.add_order_ids() {
        let Some(o) = order_of(bot, asset_no, &saved, id) else {
            continue;
        };
        if !matches!(o.status, Status::Rejected | Status::Expired) {
            ordered += o.qty;
        }
        let exec = executed_qty(o);
        if exec > 0.0 {
            entry_qty += exec;
            entry_notional += executed_notional(o);
            entry_taker |= !o.maker;
        }
    }
    let entry_px = if entry_legs == 0 {
        None
    } else {
        Some(entry_px_sum / crate::stats::count_f64(entry_legs))
    };
    let entry_vwap = if entry_qty > 0.0 {
        entry_notional / entry_qty
    } else {
        0.0
    };
    let fill_frac = if ordered > 0.0 {
        entry_qty / ordered
    } else {
        0.0
    };
    // Цена выхода — **средневзвешенная по размеру** исполненных ног (у
    // цельного выхода нога одна — это его же цена); время — последней ноги;
    // комиссия выхода — тейкерская, если тейкером ушла хотя бы одна нога
    // (консервативно, как у лестницы входа).
    let mut exit_qty = 0.0;
    let mut exit_notional = 0.0;
    let mut exit_ts = i64::MIN;
    let mut exit_taker = false;
    for (exit_id, _, _) in &exits {
        if let Some(o) = order_of(bot, asset_no, &saved, *exit_id).filter(|o| executed_qty(o) > 0.0)
        {
            let exec = executed_qty(o);
            exit_qty += exec;
            exit_notional += executed_notional(o);
            exit_ts = exit_ts.max(o.exch_timestamp);
            exit_taker |= !o.maker;
        }
    }
    // Выход состоялся, если исполненный объём **закрыл вход** — с точностью до
    // шага лота (округление дробного выхода, E7). Заявка выхода, не отдавшая
    // ни лота (маркет-выход в пустой книге — `Expired` у модели очереди по
    // объёму), круг не рассыпает: стратегия шлёт следующую на остаток (F4,
    // В-78), и в `exits` такие ноги остаются. Под полным исполнением
    // (`RiskAdverse`) условие совпадает с прежним «все ноги исполнились», так
    // что числа прежних прогонов не меняются.
    let exit_ok = exit_qty > 0.0 && exit_qty + lot_half(bot.depth(asset_no)) >= entry_qty;
    match (entry_px, exit_ok) {
        (Some(entry_px), true) => {
            let dir = if side == HbtSide::Buy { 1 } else { -1 };
            Ok((
                RoundOutcome::Filled {
                    fill: Fill {
                        dir,
                        entry_px,
                        exit_px: exit_notional / exit_qty,
                        qty: entry_qty,
                        entry_taker,
                        exit_taker,
                        entry_vwap,
                        fill_frac,
                        legs_filled: u8::try_from(entry_legs).unwrap_or(u8::MAX),
                        legs_rejected: rejected_legs_with(bot, asset_no, entry_id, legs, &saved),
                        fill_by_cross,
                        tape_press: state.tape_at_fill(),
                        tape_press_ws: state.tape_at_fill_ws(),
                        cxl_press: state.cxl_at_fill(),
                        chase_wait_ns: state.chase_wait_ns(),
                    },
                    exit_ts,
                    reason,
                    // Доля отдана, если хоть одна заявка выхода несла часть
                    // позиции (E7). Число заявок о доле не говорит: остаток
                    // добирается новыми заявками (F4).
                    partial: exits.iter().any(|(_, _, partial)| *partial),
                },
                state.position(),
            ))
        }
        _ => Ok((RoundOutcome::Inconsistent, state.position())),
    }
}

/// Ноги входа, которых биржа **не поставила**: пост-онли заявка (GTX),
/// пересёкшая спред, получает `Expired` в `ack_new` (В-72), отвергнутая —
/// `Rejected`. Такая нога — отказ, а не заказ: в `fill_frac` она не входит,
/// её считает `legs_rejected` (`n_rejected_postonly` в `forms.csv`).
pub(super) fn rejected_legs<B, MD>(bot: &B, asset_no: usize, entry_id: u64, legs: u8) -> u8
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    rejected_legs_with(bot, asset_no, entry_id, legs, &[])
}

/// Заявка по номеру: у движка, а если движок заменён быстрым путём удержания — из копии, снятой до замены.
pub(super) fn order_of<'a, B, MD>(
    bot: &'a B,
    asset_no: usize,
    saved: &'a [(u64, Order)],
    id: u64,
) -> Option<&'a Order>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    bot.orders(asset_no)
        .get(&id)
        .or_else(|| saved.iter().find(|(i, _)| *i == id).map(|(_, o)| o))
}

pub(super) fn rejected_legs_with<B, MD>(
    bot: &B,
    asset_no: usize,
    entry_id: u64,
    legs: u8,
    saved: &[(u64, Order)],
) -> u8
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut n = 0u8;
    for i in 0..u64::from(legs.max(1)) {
        if order_of(bot, asset_no, saved, entry_id.saturating_add(i))
            .is_some_and(|o| matches!(o.status, Status::Rejected | Status::Expired))
        {
            n = n.saturating_add(1);
        }
    }
    n
}
