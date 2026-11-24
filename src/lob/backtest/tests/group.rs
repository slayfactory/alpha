//! Э-08: группа форм с общим входом, память кругов, `retreat_shift`.

use super::*;

/// Память кругов (G10) берёт круг только при тех же сиротах на входе (в нумерации от базы прогона): круг
/// наследует сирот прошлого сигнала, а прошлый сигнал у наборов с разным допуском разный (ревью бага memo,
/// Исследователь 27.09). Те же сироты при другой базе номеров — попадание, другие — промах.
#[test]
fn round_memo_keys_on_inherited_orphans() {
    let (_, plan) = windowed_fixture();
    let sig = BounceSignal {
        touch_index: 0,
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
        qty: None,
    };
    let mut memo = RoundMemo::default();
    let carry = crate::lob::strategy::OrphanCarry::test_entry(105, 2);
    memo.store(&sig, None, 100, 7, carry, carry);
    // Та же партия в нумерации прогона с базой 1000 (1005 = 1000 + 5) — попадание.
    let same = crate::lob::strategy::OrphanCarry::test_entry(1005, 2);
    assert!(memo.recall(&sig, 1000, same).is_some());
    // Без сирот или с другой партией — промах: круг начинался бы иначе.
    assert!(memo
        .recall(&sig, 1000, crate::lob::strategy::OrphanCarry::NONE)
        .is_none());
    let other = crate::lob::strategy::OrphanCarry::test_entry(1006, 2);
    assert!(memo.recall(&sig, 1000, other).is_none());
    assert_eq!(memo.stats(), (1, 2));
}

// ---------------------------------------------------------------------------
// Э-08 (T-38, «один проход на вход»): группа форм с общим входом — общий
// `drive_signal_group`/`run_round_group` обязан дать те же круги, что сольный
// `drive_signal` по разу на форму (условия Судьи к Э-08).
// ---------------------------------------------------------------------------

/// Один сигнал К вариантов, сольно (свежий движок на вариант) — эталон для сравнения с группой.
pub(super) fn solo_signal_steps(
    feed: &[Event],
    variants: &[TradePlan],
    sig0: impl Fn(TradePlan) -> BounceSignal,
    cfg: &DriveConfig,
    data_end_ns: Option<i64>,
) -> Vec<SignalStep> {
    variants
        .iter()
        .map(|&plan| {
            let mut hbt = build_backtest(
                feed,
                1.0,
                1.0,
                ExecLatency::uniform(1_000_000),
                QueueModelKind::RiskAdverse,
            );
            hbt.elapse(0).unwrap();
            let mut next_id = 1u64;
            let mut carry = crate::lob::strategy::OrphanCarry::NONE;
            drive_signal(
                &mut hbt,
                0,
                &sig0(plan),
                cfg,
                &mut next_id,
                &mut carry,
                data_end_ns,
            )
            .unwrap()
        })
        .collect()
}

/// TK-347: диапазоны номеров заявок входа, вариантов и страховок остатка группы не пересекаются —
/// вариант с длинной погоней (сотни заявок) не задевает соседа («Order id exists», MYXUSDT 2026-02-13).
#[test]
fn group_id_ranges_are_disjoint_for_long_exit_chains() {
    let base = GROUP_ID_BASE;
    for n in [1u64, 2, 32, 96] {
        let mut starts: Vec<u64> = (0..n).map(|k| group_variant_id(base, k)).collect();
        starts.extend((0..n).map(|i| group_flatten_id(base, n, i)));
        // Вход — до первого варианта, каждому диапазону хватает ≥ 1000 номеров.
        assert!(
            starts[0] >= base + 1000,
            "вход не должен задевать вариант 0"
        );
        assert!(
            starts.windows(2).all(|w| w[1] - w[0] >= 1000),
            "диапазоны вариантов/страховок теснее 1000 номеров: {starts:?}"
        );
        assert!(starts.windows(2).all(|w| w[0] < w[1]), "порядок диапазонов");
    }
}

/// Тот же сигнал, группой (`drive_signal_group`) — один общий вход, К своих выходов.
pub(super) fn group_signal_steps(
    feed: &[Event],
    variants: &[TradePlan],
    sig0: impl Fn(TradePlan) -> BounceSignal,
    cfg: &DriveConfig,
) -> Vec<SignalStep> {
    let mut hbt = build_backtest(
        feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    hbt.elapse(0).unwrap();
    let mut next_group_id = GROUP_ID_BASE;
    let mut carries = vec![crate::lob::strategy::OrphanCarry::NONE; variants.len()];
    drive_signal_group(
        &mut hbt,
        0,
        &sig0(variants[0]),
        variants,
        cfg,
        &mut next_group_id,
        &mut carries,
        None,
        None,
    )
    .unwrap()
}

/// `TradePlan::Bounce` не поддерживает `..base` (обновление есть только у структур, не у полей
/// варианта перечисления) — правит стоп/дедлайн явным `match`, остальные поля несёт исходный план.
pub(super) fn with_stop_and_deadline(plan: TradePlan, stop_px: f64, deadline_ns: i64) -> TradePlan {
    match plan {
        TradePlan::Bounce {
            entry_px,
            take_px,
            entry_ttl_ns,
            level_floor_qty,
            band_exit_bps,
            early_exit_ns,
            level_px,
            tick_px,
            post_only,
            trail_bps,
            trail_activate_bps,
            grid_legs,
            grid_step_px,
            ladder,
            take_frac,
            eaten_half_pct,
            eaten_all_pct,
            eaten_half_frac,
            level_qty,
            lot_qty,
            exit_eat_pct,
            exit_gone_pct,
            gone_trail_bps,
            gone_stop,
            wall_eat,
            pyramid,
            ..
        } => TradePlan::Bounce {
            entry_px,
            stop_px,
            take_px,
            deadline_ns,
            entry_ttl_ns,
            level_floor_qty,
            band_exit_bps,
            early_exit_ns,
            level_px,
            tick_px,
            post_only,
            trail_bps,
            trail_activate_bps,
            grid_legs,
            grid_step_px,
            ladder,
            take_frac,
            eaten_half_pct,
            eaten_all_pct,
            eaten_half_frac,
            level_qty,
            lot_qty,
            exit_eat_pct,
            exit_gone_pct,
            gone_trail_bps,
            gone_stop,
            wall_eat,
            pyramid,
        },
        TradePlan::SpreadHold => plan,
    }
}

/// К вариантов с общим входом расходятся тремя разными причинами выхода — тейк лимитом (мейкер,
/// план как есть — В-44), дедлайн (укорочен — рынком раньше тейка) и стоп (поднят к 102 — сделка
/// по 103 на 4 с его пересекает, рынком раньше тейка): группа обязана дать то же поле в поле, что
/// сольный `drive_signal` по разу на вариант, — исход, `fill`, время выхода, `idle_ns`, `crossed`/
/// `spread`, счётчики F8b/F8c и порядок (индекс варианта — тот же, что и в `variant_plans`).
#[test]
fn group_round_matches_three_solo_runs_with_divergent_exits() {
    let (feed, base_plan) = windowed_fixture();
    let take_variant = base_plan;
    let deadline_variant = with_stop_and_deadline(base_plan, 99.0, S);
    let stop_variant = with_stop_and_deadline(base_plan, 102.0, 60 * S);
    let variants = [take_variant, deadline_variant, stop_variant];
    let sig0 = |plan: TradePlan| BounceSignal {
        touch_index: 0,
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
        qty: None,
    };
    let cfg = DriveConfig {
        busy_skip: false,
        ..drive_cfg()
    };

    let solo = solo_signal_steps(&feed, &variants, sig0, &cfg, None);
    let group = group_signal_steps(&feed, &variants, sig0, &cfg);
    assert_eq!(
        group.len(),
        variants.len(),
        "группа обязана вернуть шаг на каждый вариант, в его порядке"
    );
    for (i, (g, s)) in group.iter().zip(solo.iter()).enumerate() {
        assert_eq!(g, s, "вариант {i}: групповой круг разошёлся с сольным");
    }
    // Три варианта и вправду разошлись причиной (иначе тест ничего не проверяет про форк —
    // конкретную причину каждого не фиксируем: она зависит от точных секунд синтетического
    // фида, а не от факта форка).
    let reason_of = |s: &SignalStep| match s {
        SignalStep::Submitted {
            outcome: RoundOutcome::Filled { reason, .. },
            ..
        } => Some(*reason),
        _ => None,
    };
    let reasons: Vec<_> = solo.iter().map(reason_of).collect();
    assert!(
        reasons.iter().all(Option::is_some),
        "все три круга обязаны исполниться — иначе тест не про форк, а про промах: {reasons:?}"
    );
    assert!(
        reasons[0] != reasons[1] || reasons[1] != reasons[2],
        "варианты обязаны разойтись хоть одной причиной выхода: {reasons:?}"
    );
}

/// TK-048: групповой круг с быстрым путём удержания (быстрый путь) даёт те же шаги, что без него.
#[test]
fn group_fast_hold_matches_group_without_it() {
    let (feed, base_plan) = windowed_fixture();
    let variants = [
        base_plan,
        with_stop_and_deadline(base_plan, 99.0, S),
        with_stop_and_deadline(base_plan, 102.0, 60 * S),
    ];
    let sig = BounceSignal {
        touch_index: 0,
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan: base_plan,
        profile: 0,
        qty: None,
    };
    let cfg = DriveConfig {
        busy_skip: false,
        hold_skip: true,
        ..drive_cfg()
    };
    let lat = ExecLatency::uniform(1_000_000);
    let windows = SignalWindows::build(&feed, &[S], 1.0, 1.0);
    let run = || {
        let mut buf = Vec::new();
        group_round_in_window(
            &feed[..],
            &windows,
            &sig,
            &variants,
            &cfg,
            lat,
            &mut buf,
            None,
        )
        .unwrap()
    };
    let a = run();
    let used = fast_hold::FAST_ROUNDS.load(std::sync::atomic::Ordering::Relaxed)
        + fast_hold::FAST_FALLBACKS.load(std::sync::atomic::Ordering::Relaxed);
    fast_hold::FORCE_ON.with(|f| f.set(true));
    let b = run();
    fast_hold::FORCE_ON.with(|f| f.set(false));
    assert_eq!(a, b, "быстрый путь изменил групповой круг");
    assert!(a.is_some(), "групповой круг обязан посчитаться");
    let after = fast_hold::FAST_ROUNDS.load(std::sync::atomic::Ordering::Relaxed)
        + fast_hold::FAST_FALLBACKS.load(std::sync::atomic::Ordering::Relaxed);
    assert!(after > used, "групповой быстрый путь обязан сработать");
}

/// Группа не читает `--hold-step` вовсе (см. `run_round_group`): итог не зависит от него.
/// Вместе с `group_round_matches_three_solo_runs_with_divergent_exits` (группа == сольно с
/// `poll`, `data_end_ns = None`) это и есть условие Судьи к Э-08: `skip + group on == poll +
/// group off`.
#[test]
fn group_ignores_hold_skip_flag() {
    let (feed, base_plan) = windowed_fixture();
    let variants = [base_plan, with_stop_and_deadline(base_plan, 99.0, 55 * S)];
    let sig0 = |plan: TradePlan| BounceSignal {
        touch_index: 0,
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
        qty: None,
    };
    let poll_cfg = DriveConfig {
        busy_skip: false,
        hold_skip: false,
        ..drive_cfg()
    };
    let skip_cfg = DriveConfig {
        hold_skip: true,
        ..poll_cfg
    };
    let group_poll = group_signal_steps(&feed, &variants, sig0, &poll_cfg);
    let group_skip = group_signal_steps(&feed, &variants, sig0, &skip_cfg);
    assert_eq!(
        group_poll, group_skip,
        "--hold-step не должен ничего менять в группе (Э-08 не пользуется Э-04б)"
    );
}

/// TK-049: движок, поднятый заново из `FastHandoff` посреди круга, ведёт заявки так же, как сплошной.
pub(super) fn fast_hold_handoff_case(qm: QueueModelKind, handoff_step: i64) {
    use super::super::fast_hold::{with_backtest_from_handoff, HoldTracker};
    use hftbacktest::depth::MarketDepth;
    const LEVELS: usize = 40;
    let mut feed = Vec::new();
    for i in 0..LEVELS {
        feed.push(depth_at(0, true, 1000.0 - i as f64, 3.0));
        feed.push(depth_at(0, false, 1001.0 + i as f64, 3.0));
    }
    let mut x = 0x9E37_79B9_7F4A_7C15u64;
    let mut next = || {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        x
    };
    for k in 0..40_000i64 {
        let r = next();
        let ts = 4 * S + k * 100_000;
        let mut e = if r % 3 == 0 {
            let sell = r & 8 == 0;
            let px = if sell {
                1000.0 - ((r >> 12) % 6) as f64
            } else {
                1001.0 + ((r >> 12) % 6) as f64
            };
            trade_at(ts, sell, px, 1.0 + ((r >> 20) % 4) as f64)
        } else {
            let bid = r & 1 == 0;
            let off = ((r >> 8) % 5) as f64;
            let px = if bid { 1000.0 - off } else { 1001.0 + off };
            let qty = if (r >> 20) % 4 == 0 {
                0.0
            } else {
                1.0 + ((r >> 24) % 5) as f64
            };
            depth_at(ts, bid, px, qty)
        };
        e.local_ts = ts + 500 + ((r >> 30) % 6) as i64 * 10;
        feed.push(e);
    }
    let t0 = 5 * S;
    let windows = SignalWindows::build(&feed, &[t0], 1.0, 1.0);
    let w = windows.window_at(t0).unwrap();
    let rest = &feed[w.start..];
    let lat = ExecLatency::uniform(1_000_000);
    let to_t = |bt: &mut Backtest<FastMarketDepth>| {
        bt.elapse(0).unwrap();
        for _ in 0..handoff_step {
            bt.elapse(ON_EVENT_POLL_STEP_NS).unwrap();
        }
        bt.current_timestamp()
    };
    let trace = |bt: &mut Backtest<FastMarketDepth>| {
        let px = bt.depth(0).best_bid_tick() as f64;
        let ax = bt.depth(0).best_ask_tick() as f64;
        bt.submit_buy_order(0, 1, px, 2.0, TimeInForce::GTC, OrdType::Limit, false)
            .unwrap();
        bt.submit_sell_order(0, 2, ax, 2.0, TimeInForce::GTC, OrdType::Limit, false)
            .unwrap();
        let mut out = Vec::new();
        for _ in 0..80 {
            bt.elapse(ON_EVENT_POLL_STEP_NS).unwrap();
            let o: Vec<_> = [1u64, 2]
                .iter()
                .map(|id| {
                    bt.orders(0)
                        .get(id)
                        .map(|o| (o.status as u8, o.exec_qty.to_bits(), o.leaves_qty.to_bits()))
                })
                .collect();
            out.push((
                bt.current_timestamp(),
                bt.depth(0).best_bid_tick(),
                bt.depth(0).best_ask_tick(),
                o,
            ));
        }
        out
    };
    let (t, cont) = with_backtest_over_window(&w.depth, t0, rest, 1.0, 1.0, lat, qm, |bt| {
        let t = to_t(bt);
        (t, trace(bt))
    });
    let mut tr = HoldTracker::new(&feed, w.start, w.depth.build(1.0, 1.0));
    tr.advance_to(t);
    let h = tr.handoff(t, 1.0, 1.0).expect("лента чистая");
    let restarted = with_backtest_from_handoff(&h, &feed, 1.0, 1.0, lat, qm, |bt| {
        bt.elapse(0).unwrap();
        assert_eq!(bt.current_timestamp(), t, "часы нового движка");
        trace(bt)
    });
    assert_eq!(cont, restarted, "qm={qm:?} шаг={handoff_step}");
    assert!(
        cont.iter()
            .any(|(_, _, _, o)| o.iter().flatten().any(|(_, e, _)| *e != 0)),
        "сценарий должен давать исполнения: {:?}",
        &cont[cont.len() - 3..]
    );
}

#[test]
fn fast_hold_handoff_equals_continuous() {
    for step in [3, 40, 150] {
        fast_hold_handoff_case(QueueModelKind::RiskAdverse, step);
        fast_hold_handoff_case(QueueModelKind::Prob { n: 3.0 }, step);
    }
}

/// Г-102: после стопа вход сигнала сдвигается от рынка на `floor(k·2^(−Δt/T))` тиков; до стопа и после затухания
/// до нуля сигнал идёт как есть (`None`). Покупка двигает вход вниз, продажа — вверх. Только с `r2` (иначе `eff()` — OFF).
#[cfg(feature = "r2")]
#[test]
fn retreat_shift_moves_entry_away_from_market_and_decays() {
    let (_, mut plan) = windowed_fixture();
    if let TradePlan::Bounce { pyramid, .. } = &mut plan {
        pyramid.retreat_k = 4;
        pyramid.retreat_half_min = 15;
    }
    let sig = |t0_ns: i64| BounceSignal {
        touch_index: 0,
        t0_ns,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
        qty: None,
    };
    let entry = |s: &BounceSignal| match s.plan {
        TradePlan::Bounce { entry_px, .. } => entry_px,
        _ => unreachable!(),
    };
    let min = 60 * S;
    let mut mem = None;
    assert!(
        retreat_shift(&mut mem, &sig(S), HbtSide::Buy).is_none(),
        "стопа не было"
    );
    mem.as_mut().unwrap().on_stop(10 * min);
    let at = |mem: &mut Option<RetreatMemory>, m: i64, side| {
        retreat_shift(mem, &sig(m * min), side).map(|s| entry(&s))
    };
    assert_eq!(
        at(&mut mem, 10, HbtSide::Buy),
        Some(97.0),
        "сразу после стопа −k"
    );
    assert_eq!(
        at(&mut mem, 10, HbtSide::Sell),
        Some(105.0),
        "продажа — вверх"
    );
    assert_eq!(at(&mut mem, 25, HbtSide::Buy), Some(99.0), "через T — k/2");
    assert_eq!(
        at(&mut mem, 70, HbtSide::Buy),
        None,
        "через 4T floor(4/16)=0"
    );
    mem.as_mut().unwrap().on_stop(70 * min);
    assert_eq!(
        at(&mut mem, 70, HbtSide::Buy),
        Some(97.0),
        "повторный стоп — снова k, не сумма"
    );
}
