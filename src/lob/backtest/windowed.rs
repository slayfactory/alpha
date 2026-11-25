//! Прогон по окнам сигналов: входная часть плана, круг группы в окне, `drive_bounce_windowed`.

use super::*;

/// Прогон **по сетапам** (решение владельца 2026-09-18: «биржа только в
/// сетапах, ничего вхолостую»): движок живёт от касания до конца круга. На
/// сигнал строится `Backtest` над снимком книги в `t0` (`SignalWindows`,
/// одна книга на все формы) и событиями дальше — без копии; между сигналами
/// биржа не работает вовсе, и цена суток определяется числом касаний и
/// длиной кругов, а не числом событий в стакане.
///
/// Точность: снимок — книга движка (`FastMarketDepth` — копия `HashMapMarketDepth` крейта, Э-05) после тех же строк, по всем полям
/// (строит её своя книга окон `WindowDepth`, К3)
/// (обе метки `<= t0`, см. `SignalWindows`), со всеми полями лучших/крайних
/// тиков, один на обе стороны движка; строки с одной меткой за `t0` каждая
/// сторона доберёт из среза сама по разу, как и в сплошном прогоне; часы
/// окна прибиты к `t0` строкой-якорем. Гейт — побайтово те же круги, что у
/// `drive_bounce` (тест и `--driver full`).
///
/// `memo` — память кругов (G10): круги, уже посчитанные над теми же окнами с теми же планами (другим
/// набором той же формы), берутся из неё; итог побайтово тот же, что без памяти (гейт «те же байты»
/// в `bounce-grid --round-memo on|off`).
///
/// Круги по окнам. Строки `[Event]` крейт занимает как есть (прежний путь). Компактные строки (Р6)
/// разворачиваются на круг в буфер потока — от `w.start` до горизонта: первая попытка — не дальше
/// `FIRST_HORIZON_NS`, затем `t0 + (entry_ttl + deadline + запас) × 2^(попытка − 1)`; после круга
/// проверка: крейт не дошёл до конца развёрнутого (у последней строки и
/// `local_ts`, и `exch_ts` позже часов конца круга — каждая сторона крейта берёт строку, только когда её
/// часы дошли) либо развёрнут весь хвост суток. Иначе круг пересчитывается с удвоенным горизонтом
/// (`drive_bounce_with` восстанавливает номера заявок и сирот) — поэтому круги побайтно те же, что над
/// всем хвостом (условие Судьи к Р6).
/// Входная часть плана (Э-08): поля, которые читает фаза входа, — план с обнулёнными полями выхода
/// (стоп, тейк, дедлайн, трейл, досрочный выход, E7/F7). Формы с равной входной частью на сигнале
/// разворачивают один общий вход.
pub(super) fn entry_part(plan: TradePlan) -> TradePlan {
    let mut p = plan;
    if let TradePlan::Bounce {
        stop_px,
        take_px,
        deadline_ns,
        trail_bps,
        trail_activate_bps,
        early_exit_ns,
        take_frac,
        eaten_half_pct,
        eaten_all_pct,
        eaten_half_frac,
        exit_eat_pct,
        exit_gone_pct,
        gone_trail_bps,
        gone_stop,
        wall_eat,
        pyramid,
        ..
    } = &mut p
    {
        *stop_px = 0.0;
        *take_px = 0.0;
        *deadline_ns = 0;
        *trail_bps = 0.0;
        *trail_activate_bps = 0.0;
        *early_exit_ns = 0;
        *take_frac = 0.0;
        *eaten_half_pct = 0.0;
        *eaten_all_pct = 0.0;
        *eaten_half_frac = 0.0;
        *exit_eat_pct = 0.0;
        *exit_gone_pct = 0.0;
        *gone_trail_bps = 0.0;
        *gone_stop = crate::lob::strategy::GoneStop::Off;
        *wall_eat = crate::lob::strategy::WallEatExit::OFF;
        *pyramid = crate::lob::strategy::PyramidCfg::OFF;
    }
    p
}

/// Групповой круг одного сигнала в свежем движке окна (Э-08) — те же попытки горизонта развёртки, что у
/// `drive_bounce_windowed` (Р6). `None` — окна или данных после `t0` нет. Вариант с сиротами на выходе
/// возвращается как `EndOfData` — в память он не попадёт (драйвер посчитает его сам).
#[allow(clippy::too_many_arguments)]
pub(super) fn group_round_in_window<R: EventRows + ?Sized>(
    events: &R,
    windows: &SignalWindows,
    rep: &BounceSignal,
    plans: &[TradePlan],
    cfg: &DriveConfig,
    exec_latency: ExecLatency,
    buf: &mut Vec<Event>,
    walls: Option<&[WallEvent]>,
) -> Result<Option<Vec<SignalStep>>, BacktestError> {
    let Some(w) = windows.window_at(rep.t0_ns) else {
        return Ok(None);
    };
    let start = events.skip_to(w.start);
    if start >= events.len() {
        return Ok(None);
    }
    let span_full = plans.iter().map(horizon_span_ns).max().unwrap_or(1);
    let mut attempt = 0u32;
    loop {
        let (rest, whole_tail): (&[Event], bool) = match events.as_events() {
            Some(all) => (&all[w.start..], true),
            None => {
                let span = attempt_span_ns(span_full, attempt);
                let until = rep.t0_ns.saturating_add(span);
                buf.clear();
                let mut i = start;
                while i < events.len() && events.row_local_ts(i) <= until {
                    buf.push(events.row(i));
                    i += 1;
                }
                (&buf[..], i >= events.len())
            }
        };
        note_attempt(attempt, rest.len());
        let last = rest.last().map(|e| (e.local_ts, e.exch_ts));
        let data_end = last.map(|(local, exch)| local.min(exch));
        let (steps, now) = with_backtest_over_window(
            &w.depth,
            rep.t0_ns,
            rest,
            windows.tick_size,
            windows.lot_size,
            exec_latency,
            cfg.queue_model,
            |bt| -> Result<(Option<Vec<SignalStep>>, i64), BacktestError> {
                if bt.elapse(0)? == ElapseResult::EndOfData {
                    return Ok((None, bt.current_timestamp()));
                }
                let mut gid = GROUP_ID_BASE;
                let mut carries = vec![OrphanCarry::NONE; plans.len()];
                let bt_addr = std::ptr::from_ref::<Backtest<FastMarketDepth>>(bt) as usize;
                let steps = fast_hold::with_fast_ctx(
                    bt_addr,
                    rest,
                    windows.tick_size,
                    windows.lot_size,
                    exec_latency,
                    cfg.queue_model,
                    || {
                        drive_signal_group(
                            bt,
                            0,
                            rep,
                            plans,
                            cfg,
                            &mut gid,
                            &mut carries,
                            data_end,
                            walls,
                        )
                    },
                )?;
                let steps = steps
                    .into_iter()
                    .zip(carries)
                    .map(|(s, c)| {
                        if c.is_empty() {
                            s
                        } else {
                            SignalStep::EndOfData
                        }
                    })
                    .collect();
                Ok((Some(steps), bt.current_timestamp()))
            },
        )?;
        let Some(steps) = steps else {
            return Ok(None);
        };
        note_used(attempt, rest, now);
        let enough = whole_tail
            || (!steps.iter().any(step_hit_end_of_data)
                && last.is_some_and(|(local, exch)| local > now && exch > now));
        if enough {
            return Ok(Some(steps));
        }
        attempt = attempt.saturating_add(1);
        HORIZON_RETRIES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
}

/// Г-65: в группе есть форма цели за второй стеной (`wall2`) — одиночная группа для неё допустима.
pub(super) fn group_has_wall2(g: &[(usize, BounceSignal)]) -> bool {
    g.iter().any(
        |(_, s)| matches!(s.plan, TradePlan::Bounce { pyramid, .. } if pyramid.eff().wall2 > 0),
    )
}

/// Э-08 («один проход на вход», план принят Судьёй, reviews/e08-exit-group-plan-2026-09-27.md): круги
/// группы форм считаются заранее одним движком на сигнал и кладутся в память кругов каждой формы — потом
/// обычный драйвер (`drive_bounce_windowed`) берёт их оттуда. Группа на сигнале — формы с равными
/// `sigma`, размером и входной частью плана (`entry_part`); вход общий, выходы — свои (`drive_signal_group`).
/// В память идёт только чистый круг варианта: без сирот на выходе, без страховки остатка и не упёршийся в
/// конец данных — остальное драйвер посчитает сам, как раньше. Ключ памяти — с пустыми сиротами на входе:
/// у формы с сиротами прошлого сигнала круг из памяти не возьмётся. Только без пропуска «занято»
/// (`busy_skip = false`: у каждого сигнала свой свежий движок). Возвращает число групповых кругов.
pub fn precompute_exit_group<R: EventRows + ?Sized>(
    events: &R,
    windows: &SignalWindows,
    forms_signals: &[&[BounceSignal]],
    cfg: &DriveConfig,
    exec_latency: ExecLatency,
    memos: &mut [&mut RoundMemo],
    walls: Option<&[WallEvent]>,
) -> Result<u64, BacktestError> {
    debug_assert_eq!(forms_signals.len(), memos.len());
    if cfg.busy_skip {
        return Ok(0);
    }
    // BTreeMap — группировка сигналов по t0 до прогона кругов, вне цикла событий.
    let mut by_t0: std::collections::BTreeMap<i64, Vec<(usize, BounceSignal)>> =
        std::collections::BTreeMap::new();
    for (k, sigs) in forms_signals.iter().enumerate() {
        for sig in sigs.iter() {
            if entry_side(sig.sigma).is_some() {
                by_t0.entry(sig.t0_ns).or_default().push((k, *sig));
            }
        }
    }
    let mut groups: u64 = 0;
    let mut buf: Vec<Event> = Vec::new();
    for members in by_t0.values() {
        let mut parts: Vec<Vec<(usize, BounceSignal)>> = Vec::new();
        diag_add(0, members.len() as u64);
        for &(k, sig) in members {
            if memos[k].contains(&sig, OrphanCarry::NONE) {
                diag_add(1, 1);
                continue;
            }
            // Г-65 (e65): форма цели за второй стеной считается только группой (журнал стен — при форке).
            if matches!(sig.plan, TradePlan::Bounce { pyramid, .. } if pyramid.on() && pyramid.eff().wall2 == 0)
            {
                continue;
            }
            let key = (sig.sigma, sig.qty, entry_part(sig.plan));
            match parts
                .iter_mut()
                .find(|g| (g[0].1.sigma, g[0].1.qty, entry_part(g[0].1.plan)) == key)
            {
                Some(g) => g.push((k, sig)),
                None => parts.push(vec![(k, sig)]),
            }
        }
        diag_add(2, parts.len() as u64);
        for part in parts
            .into_iter()
            .filter(|g| g.len() > 1 || group_has_wall2(g))
        {
            diag_add(3, 1);
            let rep = part[0].1;
            let plans: Vec<TradePlan> = part.iter().map(|(_, s)| s.plan).collect();
            let Some(steps) = group_round_in_window(
                events,
                windows,
                &rep,
                &plans,
                cfg,
                exec_latency,
                &mut buf,
                walls,
            )?
            else {
                diag_add(4, 1);
                continue;
            };
            groups += 1;
            EXIT_GROUP_ROUNDS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            for ((k, sig), step) in part.iter().zip(steps) {
                // Г-65 (TK-190): круг формы `wall2*`, упёршийся в конец данных, без сирот и страховки — окончательный
                // (развёрнут весь хвост: `enough` в `group_round_in_window`); сольный путь журнала стен не знает и
                // посчитал бы его как B1, поэтому для `wall2*` он идёт в память, как чистый.
                let wall2 = matches!(sig.plan, TradePlan::Bounce { pyramid, .. } if pyramid.eff().wall2 > 0);
                let clean = match &step {
                    SignalStep::EndOfData => false,
                    SignalStep::NotSubmitted { .. } => true,
                    SignalStep::Submitted {
                        outcome, residual, ..
                    } => {
                        residual.is_none() && (wall2 || !matches!(outcome, RoundOutcome::EndOfData))
                    }
                };
                if clean {
                    memos[*k].store(
                        sig,
                        Some(step),
                        0,
                        ID_STRIDE,
                        OrphanCarry::NONE,
                        OrphanCarry::NONE,
                    );
                }
            }
        }
    }
    Ok(groups)
}

pub fn drive_bounce_windowed<R: EventRows + ?Sized>(
    events: &R,
    windows: &SignalWindows,
    signals: &[BounceSignal],
    cfg: &DriveConfig,
    exec_latency: ExecLatency,
    memo: Option<&mut RoundMemo>,
) -> Result<BounceRun, BacktestError> {
    let mut buf: Vec<Event> = Vec::new();
    drive_bounce_with::<Backtest<FastMarketDepth>, FastMarketDepth, _>(
        0,
        signals,
        cfg,
        memo,
        |sig, attempt, step| {
            let Some(w) = windows.window_at(sig.t0_ns) else {
                return Ok(None);
            };
            let start = events.skip_to(w.start);
            if start >= events.len() {
                // Строк после `t0` нет — сплошной прогон здесь упёрся бы в
                // конец записи, не дойдя до сигнала.
                return Ok(None);
            }
            let (rest, whole_tail): (&[Event], bool) = match events.as_events() {
                Some(all) => (&all[w.start..], true),
                None => {
                    let span = attempt_span_ns(horizon_span_ns(&sig.plan), attempt);
                    let until = sig.t0_ns.saturating_add(span);
                    let i = hftbacktest::prof::timed(3, || {
                        buf.clear();
                        let mut i = start;
                        while i < events.len() && events.row_local_ts(i) <= until {
                            buf.push(events.row(i));
                            i += 1;
                        }
                        i
                    });
                    (&buf[..], i >= events.len())
                }
            };
            note_attempt(attempt, rest.len());
            let last = rest.last().map(|e| (e.local_ts, e.exch_ts));
            // Э-04б: конец данных круга — меньшая из меток последней строки (до неё крейт не исчерпан).
            let data_end = last.map(|(local, exch)| local.min(exch));
            hftbacktest::prof::timed(4, || {
                with_backtest_over_window(
                    &w.depth,
                    sig.t0_ns,
                    rest,
                    windows.tick_size,
                    windows.lot_size,
                    exec_latency,
                    cfg.queue_model,
                    |bt| {
                        let s = if bt.elapse(0)? == ElapseResult::EndOfData {
                            SignalStep::EndOfData
                        } else {
                            fast_hold::with_fast_ctx(
                                std::ptr::from_ref::<Backtest<FastMarketDepth>>(bt) as usize,
                                rest,
                                windows.tick_size,
                                windows.lot_size,
                                exec_latency,
                                cfg.queue_model,
                                || step(bt, data_end),
                            )?
                        };
                        Ok((s, bt.current_timestamp()))
                    },
                )
            })
            .map(|(s, now)| {
                note_used(attempt, rest, now);
                // Крейт упёрся в конец развёрнутого — в любом из трёх видов, которыми конец данных
                // выходит из круга; иначе (и последняя строка позже часов) он видел ровно то же, что
                // увидел бы над всем хвостом.
                let enough = whole_tail
                    || (!step_hit_end_of_data(&s)
                        && last.is_some_and(|(local, exch)| local > now && exch > now));
                Some((s, enough))
            })
        },
    )
}
