//! Сплошной прогон: горизонты развёртки, откат и `drive_bounce`.

use super::*;

/// Цикл по сигналам, общий для обоих драйверов. `source(sig, step)` даёт шагу
/// движок и возвращает его результат; `None` — для сигнала нет данных (там,
/// где сплошной прогон упёрся бы в конец записи). Учёт кругов, промахов и
/// причин выхода — здесь, движка он не касается.
/// Круг упёрся в конец данных (Р6): `SignalStep::EndOfData`, исход круга `EndOfData` или остаток, который
/// не удалось закрыть до конца данных (`residual: Some(true)`) — все пути, которыми `ElapseResult::EndOfData`
/// выходит из `drive_signal`.
pub(super) fn step_hit_end_of_data(s: &SignalStep) -> bool {
    match s {
        SignalStep::EndOfData => true,
        SignalStep::Submitted {
            outcome, residual, ..
        } => matches!(outcome, RoundOutcome::EndOfData) || *residual == Some(true),
        _ => false,
    }
}

/// Запас горизонта развёртки круга сверх `entry_ttl + deadline` плана (Р6): подтверждения и отмены после
/// дедлайна. Точность не зависит от него — короткий горизонт ловит проверка и пересчитывает круг.
pub(super) const HORIZON_MARGIN_NS: i64 = 60_000_000_000;

/// Горизонт первой попытки развёртки (Р6-поправка, 26.09): большинство кругов (вход не исполнен, быстрый
/// выход) кончается за минуты, а полный горизонт плана — часы (`entry_ttl` + дедлайн); развёртка на весь
/// горизонт на каждый сигнал делала касания ×5 медленнее старого пути (CRV 22.09, 8 форм: 12 → 71 с).
/// Первая попытка — не дальше этого, вторая — сразу полный горизонт, дальше удвоение: лишняя работа
/// длинного круга ограничена первой попыткой. Точность от числа не зависит (проверка конца данных), только
/// время; значение — замер `prof/r6fix.log` (findings T-23).
pub(super) const FIRST_HORIZON_NS: i64 = 300_000_000_000;

// Тестовая подмена начального горизонта развёртки (условие Судьи 2 к Р6): искусственно малый горизонт
// заставляет пересчитывать почти каждый круг — итог обязан остаться побайтно тем же.
#[cfg(test)]
thread_local! {
    pub(crate) static HORIZON_SPAN_OVERRIDE_NS: std::cell::Cell<Option<i64>> =
        const { std::cell::Cell::new(None) };
}

/// Горизонт развёртки попытки `attempt` для полного горизонта `span_full` (Р6): нулевая — не дальше
/// `FIRST_HORIZON_NS`, первая сразу полный, дальше удвоение. Точность от значения не зависит (проверка конца
/// данных пересчитывает круг), только время.
pub(super) fn attempt_span_ns(span_full: i64, attempt: u32) -> i64 {
    match attempt {
        0 => span_full.min(FIRST_HORIZON_NS),
        a => span_full
            .checked_shl(a - 1)
            .filter(|v| *v > 0)
            .unwrap_or(i64::MAX),
    }
}

pub(super) fn horizon_span_ns(plan: &TradePlan) -> i64 {
    #[cfg(test)]
    if let Some(v) = HORIZON_SPAN_OVERRIDE_NS.with(std::cell::Cell::get) {
        return v.max(1);
    }
    plan.round_span_ns()
        .saturating_add(HORIZON_MARGIN_NS)
        .max(1)
}

/// Г-102: сигнал со входом, отодвинутым от стены на добавку памяти отступа (`RetreatMemory::offset_ticks` на `t0`),
/// ОТ рынка (штраф после стопа, X-09: вход труднее — покупка ниже, продажа выше); `None` — форма выключена или добавка ноль (сигнал идёт как есть). Память создаётся по первому сигналу.
pub(super) fn retreat_shift(
    memory: &mut Option<RetreatMemory>,
    sig: &BounceSignal,
    side: HbtSide,
) -> Option<BounceSignal> {
    let TradePlan::Bounce {
        pyramid, tick_px, ..
    } = sig.plan
    else {
        return None;
    };
    let p = pyramid.eff();
    if p.retreat_k == 0 {
        return None;
    }
    let mem = memory.get_or_insert_with(|| {
        RetreatMemory::new(RetreatCfg {
            k_ticks: u32::from(p.retreat_k),
            half_life_ns: i64::from(p.retreat_half_min) * 60_000_000_000,
        })
    });
    let ticks = mem.offset_ticks(sig.t0_ns);
    if ticks == 0 {
        return None;
    }
    let mut out = *sig;
    if let TradePlan::Bounce { entry_px, .. } = &mut out.plan {
        let d = f64::from(ticks) * tick_px;
        *entry_px += if matches!(side, HbtSide::Buy) { -d } else { d };
    }
    Some(out)
}

pub(super) fn drive_bounce_with<B, MD, S>(
    asset_no: usize,
    signals: &[BounceSignal],
    cfg: &DriveConfig,
    mut memo: Option<&mut RoundMemo>,
    mut source: S,
) -> Result<BounceRun, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
    // Источник круга: сигнал, номер попытки (Р6 — пересчёт с удвоенным горизонтом развёртки) и шаг
    // стратегии; отдаёт шаг и «данных хватило» (`false` — круг пересчитывается с восстановленными
    // номерами заявок и переносом сирот, как будто первой попытки не было).
    S: FnMut(
        &BounceSignal,
        u32,
        // Шаг стратегии получает конец данных круга (Э-04б: `None` — неизвестен, пропуска шагов нет).
        &mut dyn FnMut(&mut B, Option<i64>) -> Result<SignalStep, B::Error>,
    ) -> Result<Option<(SignalStep, bool)>, B::Error>,
{
    let mut order: Vec<BounceSignal> = signals.to_vec();
    order.sort_by_key(|s| s.t0_ns);
    let profile = signals.first().map(|s| s.profile).unwrap_or(0);
    let mut fills: Vec<Fill> = Vec::new();
    let mut misses = MissLedger::default();
    let mut observations: Vec<FillObservation> = Vec::new();
    let mut exits = ExitTally::default();
    let mut entry_rejected: u64 = 0;
    let mut rejected_postonly: u64 = 0;
    let mut entry_crossed: u64 = 0;
    // Снятия неисполненного входа по причинам (F5, В-74) — в `forms.csv`.
    let mut entry_cancelled_ttl: u64 = 0;
    let mut entry_cancelled_wall_dead: u64 = 0;
    let mut entry_cancelled_price_left: u64 = 0;
    let mut entry_cancelled_cxl_gate: u64 = 0;
    // F8b (В5/Р2): вход, снятый потолком ожидания подтверждения отмены, идёт
    // той же строкой, что и прочие снятия, — причиной `CancelTimeout`.
    let mut entry_cancelled_cancel_timeout: u64 = 0;
    let mut exit_cancel_timeout: u64 = 0;
    let mut orphan_fills: u64 = 0;
    let mut gate_measures: Vec<(i64, f64)> = Vec::new();
    let mut spread_at_entry: Vec<f64> = Vec::new();
    let mut busy_signal: Vec<usize> = Vec::new();
    let mut submitted_signal: Vec<usize> = Vec::new();
    let mut retreat_window_entries: u64 = 0;
    let mut busy_wait_ns_max: i64 = 0;
    let mut round_ns_max: i64 = 0;
    let mut fill_signal: Vec<usize> = Vec::new();
    let mut fill_reason: Vec<ExitReason> = Vec::new();
    let mut fill_exit_ns: Vec<i64> = Vec::new();
    let mut next_id = cfg.first_order_id;
    // F8c: сироты переносятся между кругами суток (см. `drive_signal`).
    let mut carry = OrphanCarry::NONE;
    let mut incomplete = false;
    let mut residual_flattened: u64 = 0;
    // Форма занята, пока стратегия не вернулась в `Idle` после предыдущего
    // сигнала (выход подтверждён или отмена входа подтверждена). Раньше
    // занятость считалась только до `exit_ts` исполненного круга, а сигнал в
    // хвосте круга или во время таймаута входа стартовал **с опозданием** —
    // в момент освобождения, по устаревшему касанию (артефакт драйвера,
    // 2026-09-18); теперь такой сигнал — промах «занято» в обоих драйверах.
    let mut idle_ns: i64 = i64::MIN;

    let miss_observation = |t0_ns: i64| FillObservation {
        day_cluster: day_index_ns(t0_ns),
        net_bps: 0.0,
        filled: false,
    };

    let mut trace: Vec<SignalTrace> = Vec::new();
    // Г-102: память отступа после стопа — одна на этот драйвер (вариант × инструмент × сутки), создаётся по первому сигналу.
    let mut retreat: Option<RetreatMemory> = None;
    let mut shifted: BounceSignal;
    for (sig_idx, sig) in order.iter().enumerate() {
        let Some(side) = entry_side(sig.sigma) else {
            continue;
        };
        if cfg.busy_skip && sig.t0_ns < idle_ns {
            busy_signal.push(sig_idx);
            busy_wait_ns_max = busy_wait_ns_max.max(idle_ns.saturating_sub(sig.t0_ns));
            misses.record(MissReason::PositionBusy);
            observations.push(miss_observation(sig.t0_ns));
            continue;
        }
        // Г-102: вход отходит от стены на добавку памяти отступа (от рынка); ноль — сигнал как есть.
        let sig = match retreat_shift(&mut retreat, sig, side) {
            Some(s) => {
                shifted = s;
                &shifted
            }
            None => sig,
        };
        // Память кругов (G10): круг, уже посчитанный над тем же окном с тем же планом, берётся
        // готовым; номера заявок и сироты продолжаются так, как если бы его считали здесь.
        let recalled = memo
            .as_deref_mut()
            .and_then(|m| m.recall(sig, next_id, carry));
        let step = match recalled {
            Some((step, ids_used, carry_out)) => {
                next_id = next_id.saturating_add(ids_used);
                if let Some(c) = carry_out {
                    carry = c;
                }
                step
            }
            None => {
                if matches!(sig.plan, TradePlan::Bounce { pyramid, .. } if pyramid.eff().wall2 > 0)
                {
                    WALL2_SOLO.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                }
                let id_base = next_id;
                let carry_base = carry;
                let mut attempt = 0u32;
                let step = loop {
                    let r = source(sig, attempt, &mut |bot: &mut B, data_end: Option<i64>| {
                        drive_signal(bot, asset_no, sig, cfg, &mut next_id, &mut carry, data_end)
                    })?;
                    match r {
                        Some((_, false)) => {
                            next_id = id_base;
                            carry = carry_base;
                            attempt = attempt.saturating_add(1);
                            HORIZON_RETRIES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        }
                        Some((step, true)) => break Some(step),
                        None => break None,
                    }
                };
                if let Some(m) = memo.as_deref_mut() {
                    m.store(
                        sig,
                        step.clone(),
                        id_base,
                        next_id.wrapping_sub(id_base),
                        carry_base,
                        carry,
                    );
                }
                step
            }
        };
        // Без пропуска «занято» (T-31) обрыв суток счёт не прерывает: сигналы после него — свои круги,
        // а обрывает ли шаг сутки, решает фильтр по следу (`SignalTrace`) — если этот шаг он примет.
        let end_day = |trace: &mut Vec<SignalTrace>, step: TraceStep, residual: Option<bool>| {
            if !cfg.busy_skip {
                trace.push(SignalTrace {
                    signal: sig_idx,
                    step,
                    idle_ns: i64::MIN,
                    residual,
                });
            }
            cfg.busy_skip
        };
        let Some(step) = step else {
            incomplete = true;
            if end_day(&mut trace, TraceStep::NoWindow, None) {
                break;
            }
            continue;
        };
        match step {
            SignalStep::EndOfData => {
                incomplete = true;
                if end_day(&mut trace, TraceStep::EndOfData, None) {
                    break;
                }
            }
            SignalStep::NotSubmitted { idle_ns: idle } => {
                idle_ns = idle;
                misses.record(MissReason::EntryTimeout);
                observations.push(miss_observation(sig.t0_ns));
                if !cfg.busy_skip {
                    trace.push(SignalTrace {
                        signal: sig_idx,
                        step: TraceStep::NotSubmitted,
                        idle_ns: idle,
                        residual: None,
                    });
                }
            }
            SignalStep::Submitted {
                crossed,
                spread,
                outcome,
                residual,
                idle_ns: idle,
                exit_cancel_timeouts,
                orphan_fills: orphans,
                gate_measure,
            } => {
                if let Some(m) = gate_measure {
                    gate_measures.push((sig.t0_ns, m));
                }
                idle_ns = idle;
                exit_cancel_timeout = exit_cancel_timeout.saturating_add(exit_cancel_timeouts);
                orphan_fills = orphan_fills.saturating_add(orphans);
                submitted_signal.push(sig_idx);
                if let Some(m) = retreat.as_ref() {
                    if sig.t0_ns.rem_euclid(NANOS_PER_DAY) < m.carry_window_ns() {
                        retreat_window_entries = retreat_window_entries.saturating_add(1);
                    }
                }
                if crossed {
                    entry_crossed = entry_crossed.saturating_add(1);
                }
                if let Some(s) = spread {
                    spread_at_entry.push(s);
                }
                let mut kind = TraceStep::Filled;
                match outcome {
                    RoundOutcome::EndOfData => {
                        incomplete = true;
                        if end_day(&mut trace, TraceStep::EndOfData, None) {
                            break;
                        }
                        continue;
                    }
                    RoundOutcome::Inconsistent => {
                        incomplete = true;
                        kind = TraceStep::Inconsistent;
                    }
                    RoundOutcome::TimedOut {
                        entry_status,
                        legs_rejected,
                        reason,
                    } => {
                        if matches!(entry_status, Some(Status::Rejected)) {
                            entry_rejected = entry_rejected.saturating_add(1);
                        }
                        // F5 (В-74): снятие по событию рынка и по потолку —
                        // разные строки `forms.csv`; «сигнал без входа»
                        // (`NotPlaced`) уже посчитан `rejected_postonly`.
                        match reason {
                            EntryCancelReason::Ttl => {
                                entry_cancelled_ttl = entry_cancelled_ttl.saturating_add(1);
                            }
                            EntryCancelReason::WallDead => {
                                entry_cancelled_wall_dead =
                                    entry_cancelled_wall_dead.saturating_add(1);
                            }
                            EntryCancelReason::PriceLeft => {
                                entry_cancelled_price_left =
                                    entry_cancelled_price_left.saturating_add(1);
                            }
                            EntryCancelReason::CxlGate => {
                                entry_cancelled_cxl_gate =
                                    entry_cancelled_cxl_gate.saturating_add(1);
                            }
                            EntryCancelReason::NotPlaced => {}
                            EntryCancelReason::CancelTimeout => {
                                entry_cancelled_cancel_timeout =
                                    entry_cancelled_cancel_timeout.saturating_add(1);
                            }
                        }
                        // В-72: нога, не поставленная биржей (пост-онли
                        // `Expired` или `Rejected`), — отказ, а не заказ;
                        // круг без единой поставленной ноги идёт сюда же и
                        // считается «сигналом без входа» (`EntryTimeout`), а
                        // не «занято».
                        rejected_postonly =
                            rejected_postonly.saturating_add(u64::from(legs_rejected));
                        kind = TraceStep::TimedOut;
                        misses.record(MissReason::EntryTimeout);
                        observations.push(miss_observation(sig.t0_ns));
                    }
                    RoundOutcome::Filled {
                        fill,
                        exit_ts,
                        reason,
                        partial,
                    } => {
                        match reason {
                            ExitReason::Take => exits.take += 1,
                            ExitReason::Stop => {
                                exits.stop += 1;
                                if let Some(m) = retreat.as_mut() {
                                    m.on_stop(exit_ts);
                                }
                            }
                            ExitReason::Deadline => exits.deadline += 1,
                            ExitReason::Horizon => exits.horizon += 1,
                            ExitReason::Trail => exits.trail += 1,
                            // Г-119: `Converge`/`Tape`/`Resilience` — отдельной колонки нет (форма в имени клетки); считается как `early`.
                            ExitReason::Early
                            | ExitReason::Converge
                            | ExitReason::LevelBack
                            | ExitReason::Tape
                            | ExitReason::Resilience => exits.early += 1,
                            ExitReason::Eaten => exits.eaten += 1,
                            ExitReason::EatenByTrades => exits.eaten_by_trades += 1,
                            ExitReason::WallGone => exits.wall_gone += 1,
                            // TK-014: обе причины `weat*` — в `n_eaten_by_trades` (колонки те же).
                            ExitReason::WallEatBtc | ExitReason::WallEatLocal => {
                                exits.eaten_by_trades += 1
                            }
                        }
                        if partial {
                            exits.partial += 1;
                        }
                        rejected_postonly =
                            rejected_postonly.saturating_add(u64::from(fill.legs_rejected));
                        let net = roundtrip_net_bps(&fill);
                        observations.push(FillObservation {
                            day_cluster: day_index_ns(sig.t0_ns),
                            net_bps: net.unwrap_or(0.0),
                            filled: net.is_some(),
                        });
                        fills.push(fill);
                        fill_signal.push(sig_idx);
                        fill_reason.push(reason);
                        fill_exit_ns.push(exit_ts);
                        round_ns_max = round_ns_max.max(exit_ts.saturating_sub(sig.t0_ns));
                    }
                }
                if let Some(ended) = residual {
                    residual_flattened = residual_flattened.saturating_add(1);
                    incomplete = true;
                    if ended {
                        if end_day(&mut trace, kind, residual) {
                            break;
                        }
                        continue;
                    }
                }
                if !cfg.busy_skip {
                    trace.push(SignalTrace {
                        signal: sig_idx,
                        step: kind,
                        idle_ns: idle,
                        residual,
                    });
                }
            }
        }
    }

    Ok(BounceRun {
        profile,
        signals: order.len() as u64,
        fills,
        fill_signal,
        fill_reason,
        fill_exit_ns,
        exits,
        entry_rejected,
        rejected_postonly,
        entry_crossed,
        entry_cancelled_ttl,
        entry_cancelled_wall_dead,
        entry_cancelled_price_left,
        entry_cancelled_cxl_gate,
        entry_cancelled_cancel_timeout,
        exit_cancel_timeout,
        orphan_fills,
        gate_measures,
        spread_at_entry,
        submitted_signal,
        retreat_window_entries,
        busy_signal,
        busy_wait_ns_max,
        round_ns_max,
        misses,
        observations,
        incomplete,
        residual_flattened,
        trace,
    })
}

/// Прогон сделки-отскока по сигналам **одного** профиля касаний: план у
/// каждого сигнала свой (цены считает уровень, не движок). Возвращает круги,
/// промахи по причинам и число выходов по каждой причине — из этого CLI
/// собирает `n_filled`/`fill`/`net_fill` и `n_stop`/`n_take`/`n_timeout`.
///
/// Это **сплошной** прогон: один движок на всю запись, между сигналами он
/// применяет к книге все события. Для сетки форм есть `drive_bounce_windowed`
/// — тот же цикл, движок только внутри кругов; сплошной остаётся эталоном
/// (гейт «побайтово те же круги», `lob bounce-grid --driver full`).
pub fn drive_bounce<B, MD>(
    bot: &mut B,
    asset_no: usize,
    signals: &[BounceSignal],
    cfg: &DriveConfig,
) -> Result<BounceRun, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    if bot.elapse(0)? == ElapseResult::EndOfData {
        let profile = signals.first().map(|s| s.profile).unwrap_or(0);
        return Ok(BounceRun::nothing(profile, signals.len() as u64));
    }
    let run = drive_bounce_with::<B, MD, _>(asset_no, signals, cfg, None, |_, _, step| {
        step(bot, None).map(|s| Some((s, true)))
    })?;
    bot.clear_inactive_orders(Some(asset_no));
    Ok(run)
}
