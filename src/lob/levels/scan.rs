//! Проход кадра по уровням: `scan_levels`, смерти, стопки и сведение выживших касаний.

use super::*;

/// Часть состояния кадра, общая для всех наблюдений (W4в): собирается один
/// раз в `LevelTracker::begin_frame`, дальше передаётся фазам по значению —
/// сам он не хранит ссылок ни на `LevelTracker`, ни на кадр.
#[derive(Debug, Clone, Copy)]
pub(super) struct FrameCtx {
    pub(super) s: u8,
    pub(super) frame: u64,
    pub(super) mode: H3Mode,
    /// `repeat_window_ms` конфигурации — окно `count_prior_births`.
    pub(super) window: i64,
    pub(super) warm_end: i64,
    pub(super) best_own: Option<i64>,
    pub(super) best_opp: Option<i64>,
    pub(super) approach_d: Option<i64>,
    pub(super) approach_min_age_ms: i64,
    pub(super) flow_1h: i64,
    pub(super) wall_log: bool,
}

/// Сколько рождений уже было на этом ключе строго внутри окна, и запись
/// текущего (W4в: свободная функция вместо метода — операция только над
/// картой рождений). Очередь чистится спереди: старые рождения выпадают сами;
/// полная чистка забытых ключей — отдельно, `LevelTracker::cleanup_births`.
pub(super) fn count_prior_births(
    births: &mut SortedVec<(u8, i64), VecDeque<i64>>,
    key: (u8, i64),
    ts_ms: i64,
    window_ms: i64,
) -> u32 {
    let q = births.get_or_insert_with(key, VecDeque::new);
    let cutoff = ts_ms - window_ms;
    while q.front().is_some_and(|&t| t <= cutoff) {
        q.pop_front();
    }
    let n = u32::try_from(q.len()).unwrap_or(u32::MAX);
    q.push_back(ts_ms);
    n
}

/// Один проход по наблюдениям кадра (W4в, фаза 2): обновляет существующие
/// живые уровни (касание, подход, история силы) и заводит новые рождения.
/// Берёт только поля, которые реально трогает, — карту живых, карту
/// рождений, перенос возраста и буферы новорождённых/касаний кадра.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub(super) fn scan_levels<const R1: bool>(
    ctx: &FrameCtx,
    ts_ms: i64,
    levels: &[LevelObs],
    strength_ok: &[bool],
    strength_prefix: &[i64],
    live: &mut SortedVec<(u8, i64), Live>,
    births: &mut SortedVec<(u8, i64), VecDeque<i64>>,
    carry: &mut SortedVec<(u8, i64), i64>,
    carry_open_s: bool,
    newborns: &mut Vec<(u8, i64, i64)>,
    touched: &mut Vec<Touched>,
    armed: &mut Vec<(u8, i64)>,
    approaches: &mut Vec<ApproachRecord>,
    wall_events: &mut Vec<WallEvent>,
    twin: (&[u64], &str, &mut Vec<TwinRec>),
    r1: Option<&mut R1State>,
) {
    let (twin_seeds, twin_symbol, twins) = twin;
    // Константа вместо проверки на уровне: у выключенного R1 цикл без его ветвей и счётчиков.
    let mut r1 = if R1 { r1 } else { None };
    let FrameCtx {
        s,
        frame,
        mode,
        window,
        warm_end,
        best_own,
        best_opp,
        approach_d,
        approach_min_age_ms,
        flow_1h,
        wall_log,
    } = *ctx;

    // Сумма лотов строго лучше текущего наблюдения по цене — префикс
    // кадра до его индекса: на индексе 0 ноль, дальше копится. Рядом —
    // цена **ближайшего** уровня с ненулевым размером среди этих лучших
    // (B2): «первый фронтранer», на чью цену ставится вход от фронтрана.
    let mut better_lots: i64 = 0;
    // Занятых цен строго лучше наблюдения — для `frontrun_levels` R1.
    let mut better_n: u32 = 0;
    let mut near_better_tick: Option<i64> = None;
    // Направленная глубина (T2, П-02, Г-07): сумма размеров всего кадра на
    // этой стороне, один раз до цикла — внутри цикла «позади наблюдения i»
    // это `total_lots − better_lots (строго лучше i) − ob.size_lots (сам i)`,
    // без повторного обхода `levels`. Ноль аллокаций — тот же счётчик, что
    // уже копится по кадру.
    let total_lots: i64 = levels
        .iter()
        .fold(0i64, |acc, o| acc.saturating_add(o.size_lots));
    // Г-07 на `.50` (TK-012): та же сумма, но по первым `DEPTH50_LEVELS`
    // наблюдениям стороны — у наблюдения глубже неё «позади» ноль.
    let total50_lots: i64 = levels
        .iter()
        .take(DEPTH50_LEVELS)
        .fold(0i64, |acc, o| acc.saturating_add(o.size_lots));
    // К1 (T-23): палец поиска в `live` — уровни кадра идут от лучшего наружу,
    // ключ следующего рядом с ответом прошлого; ответ тот же, что у двоичного.
    let mut finger = 0usize;
    let mut r1_finger = 0usize;
    for (i, ob) in levels.iter().enumerate() {
        let key = (s, ob.tick);
        let best = i == 0;
        let strong = strength_ok.get(i).copied().unwrap_or(true);
        // Позади наблюдения i — строго дальше от середины (В-45: кадр идёт
        // от лучшей цены вглубь), не включая сам уровень.
        let depth_behind_lots = total_lots
            .saturating_sub(better_lots)
            .saturating_sub(ob.size_lots);
        let depth_behind50_lots = if i < DEPTH50_LEVELS {
            total50_lots
                .saturating_sub(better_lots)
                .saturating_sub(ob.size_lots)
        } else {
            0
        };
        // Считается лениво и не больше раза на наблюдение (W4г): нужна и
        // новому касанию, и сигналу подхода того же кадра — раньше уровень,
        // ставший касанием при включённом подходе, платил за неё дважды.
        let mut strength_e2_window: Option<[i64; STRENGTH_WINDOWS_BPS.len()]> = None;
        let mut strength_e2_now = || -> [i64; STRENGTH_WINDOWS_BPS.len()] {
            *strength_e2_window.get_or_insert_with(|| {
                std::array::from_fn(|k| {
                    neighbour_strength_e2(levels, strength_prefix, i, STRENGTH_WINDOWS_BPS[k] * 100)
                })
            })
        };
        match live.get_mut_from(&mut finger, &key) {
            Some(lv) => {
                lv.seen_frame = frame;
                lv.seen_top50 = ob.in_top50;
                lv.seen_size = ob.size_lots;
                lv.seen_strong = strong;
                // Р9 (T-17): сила окна истории нужна только выборке (не чаще
                // `STRENGTH_SAMPLE_MS`) и старту касания — считается лениво,
                // не больше раза на наблюдение; функция чистая, значения те же.
                let mut now_e2_cell: Option<i64> = None;
                let mut now_e2 = || {
                    *now_e2_cell.get_or_insert_with(|| {
                        neighbour_strength_e2(
                            levels,
                            strength_prefix,
                            i,
                            STRENGTH_HIST_WINDOW_BPS_E2,
                        )
                    })
                };
                if lv.strength_sample_due(ts_ms) {
                    lv.observe_strength(ts_ms, now_e2());
                }
                if ob.size_lots < lv.prev && lv.first_decrease_ms.is_none() {
                    lv.first_decrease_ms = Some(ts_ms);
                }
                if approach_d.is_some() {
                    // Г-55: размер до этого кадра стоял во всех пропущенных секундах.
                    let visible = lv.prev;
                    lv.eat
                        .observe_size(ts_ms.div_euclid(1_000), ob.size_lots, visible);
                }
                let prev_size = lv.prev;
                lv.prev = ob.size_lots;
                let max_before = lv.max;
                if ob.size_lots > lv.max {
                    lv.max = ob.size_lots;
                    lv.max_ms = ts_ms;
                }
                // Сметённое последним шагом — с последнего кадра до
                // касания: читается до того, как значение этого кадра
                // его перезапишет; фронтран — за секунду до касания по
                // слотам (В-45), после наблюдения этого кадра.
                let swept = lv.better_lots;
                lv.better_lots = better_lots;
                lv.observe_frontrun(ts_ms, better_lots, near_better_tick);
                let (frontrun, frontrun_tick) = lv.frontrun_before(ts_ms);
                if let Some(r1) = r1.as_deref_mut() {
                    r1.observe_level(
                        &mut r1_finger,
                        &ObsCtx {
                            ts_ms,
                            s,
                            tick: ob.tick,
                            size: ob.size_lots,
                            prev_size,
                            traded_total: lv.traded,
                            birth_ms: lv.birth_ms,
                            better_lots,
                            better_n,
                            best_own: best_own.unwrap_or(ob.tick),
                            best_opp,
                            d_bps: approach_d.unwrap_or(0),
                        },
                    );
                }
                // Касание — переход на лучшую цену уровня, жившего до
                // кадра (В-43): был не лучшим на последнем наблюдении и
                // родился раньше этой метки. Родившийся лучшей ценой (или
                // ставший ею в миллисекунду рождения) касается только
                // после ухода с лучшей цены и возврата.
                let arrives = best && !lv.was_best && lv.birth_ms < ts_ms;
                lv.was_best = best;
                match (&mut lv.touch, arrives, best) {
                    (None, true, _) => {
                        let now_e2 = now_e2();
                        lv.touch = Some(Touch {
                            start_ms: ts_ms,
                            start_frame: frame,
                            size_at: ob.size_lots,
                            size_max_before: max_before,
                            frontrun,
                            frontrun_tick,
                            swept,
                            window_ticks: stack_window_ticks(ob.tick),
                            traded_at_start: lv.traded,
                            stack: 0,
                            stack_next_tick: None,
                            traded_first_s: [0; REACTION_WINDOWS_S.len()],
                            flow_1h_lots: flow_1h,
                            end_pending: false,
                            strength_e2: strength_e2_now(),
                            strength_held_e2: std::array::from_fn(|k| {
                                lv.strength_held_e2(
                                    ts_ms,
                                    STRENGTH_HELD_WINDOWS_S[k] * 1_000,
                                    now_e2,
                                )
                            }),
                            repeat_count: lv.repeat,
                            depth_behind_lots,
                        });
                        touched.push(Touched {
                            key,
                            stack: 0,
                            stack_next_tick: None,
                        });
                    }
                    (Some(t), _, false) => {
                        // Конец, отложенный до свипа, разрешается в том же
                        // кадре — второго ожидающего конца не бывает.
                        debug_assert!(!t.end_pending);
                        t.end_pending = true;
                        touched.push(Touched {
                            key,
                            stack: 0,
                            stack_next_tick: None,
                        });
                    }
                    (None, false, _) | (Some(_), _, true) => {}
                }
                // TK-115: журнал стен — предикат арма B1 без полосы и «лучшей цены».
                if wall_log {
                    let dying = !ob.in_top50 || below_fraction(ob.size_lots, lv.max);
                    let on = !dying
                        && (lv.birth_ms >= warm_end || lv.carried)
                        && ts_ms.saturating_sub(lv.birth_ms) >= approach_min_age_ms
                        && mode.passes_birth(ob.tick, ob.size_lots)
                        && mode
                            .holds_at_size(ob.tick, ob.size_lots, &strength_e2_now())
                            .unwrap_or(false);
                    if on != lv.wall_on {
                        lv.wall_on = on;
                        wall_events.push(WallEvent {
                            ts_ms,
                            side: side_of(key),
                            price_tick: ob.tick,
                            wall: on,
                        });
                    }
                }
                // Сигнал подхода (F1): взвод и снятие по кадру. Умерший в
                // этом кадре уровень не трогается — снятие смертью
                // эмитится свипом. При `approach_bps = None` блока нет
                // вовсе: ни состояния, ни записей.
                if let Some(d_bps) = approach_d {
                    let dying = !ob.in_top50 || below_fraction(ob.size_lots, lv.max);
                    if !dying {
                        if let Some(best_opp_tick) = best_opp {
                            // Р1: сила и порог — только если дойдёт до взвода.
                            let arm_inputs = || {
                                let strength_e2 = strength_e2_now();
                                let holds = mode
                                    .holds_at_size(ob.tick, ob.size_lots, &strength_e2)
                                    .unwrap_or(false);
                                (holds, strength_e2)
                            };
                            let armed_now = observe_approach(
                                lv,
                                (d_bps, approach_min_age_ms),
                                s,
                                arm_inputs,
                                ApproachFrame {
                                    ts_ms,
                                    tick: ob.tick,
                                    size_lots: ob.size_lots,
                                    best,
                                    best_own_tick: best_own.unwrap_or(ob.tick),
                                    best_opp_tick,
                                    flow_1h_lots: flow_1h,
                                    depth_behind_lots,
                                    depth_behind50_lots,
                                    frontrun_lots: frontrun,
                                },
                                lv.birth_ms >= warm_end || lv.carried,
                                approaches,
                            );
                            if armed_now {
                                armed.push(key);
                                if !twin_seeds.is_empty() && (lv.birth_ms >= warm_end || lv.carried)
                                {
                                    // Г-03: плотность на тике двойника — то же правило рождения, что у пула.
                                    let dense = |p: i64| {
                                        levels.iter().enumerate().any(|(j, o)| {
                                            o.tick == p
                                                && o.in_top50
                                                && strength_ok.get(j).copied().unwrap_or(true)
                                                && mode.passes_birth(p, o.size_lots)
                                        })
                                    };
                                    for &seed in twin_seeds {
                                        twins.push(TwinRec {
                                            side: side_of(key),
                                            price_tick: ob.tick,
                                            arm_ms: ts_ms,
                                            seed,
                                            twin: crate::lob::twin::pick_twin(
                                                side_of(key),
                                                ob.tick,
                                                best_own.unwrap_or(ob.tick),
                                                best_opp_tick,
                                                crate::lob::twin::twin_key(
                                                    twin_symbol,
                                                    ts_ms,
                                                    seed,
                                                ),
                                                dense,
                                            ),
                                        });
                                    }
                                }
                                if let Some(r1) = r1.as_deref_mut() {
                                    r1.push_arm(PendingArm {
                                        ts_ms,
                                        key,
                                        birth_ms: lv.birth_ms,
                                        size: ob.size_lots,
                                        frontrun_lots: frontrun,
                                    });
                                }
                            }
                        }
                    }
                }
            }
            None => {
                if ob.in_top50 && strong && mode.passes_birth(ob.tick, ob.size_lots) {
                    let repeat = count_prior_births(births, key, ts_ms, window);
                    let carried_birth = if carry_open_s {
                        carry.remove(&key)
                    } else {
                        None
                    };
                    // Рождение лучшей ценой — не касание (В-43): «цена
                    // дошла» — это переход, а не появление; запоминается
                    // только `was_best`.
                    live.insert(
                        key,
                        Live {
                            birth_ms: carried_birth.unwrap_or(ts_ms),
                            carried: carried_birth.is_some(),
                            max: ob.size_lots,
                            max_ms: ts_ms,
                            prev: ob.size_lots,
                            first_decrease_ms: None,
                            repeat,
                            seen_frame: frame,
                            seen_top50: true,
                            seen_size: ob.size_lots,
                            seen_strong: true,
                            traded: 0,
                            rpi: 0,
                            better_lots,
                            slot_new: FrontrunSlot::new(ts_ms, better_lots, near_better_tick),
                            slot_old: None,
                            was_best: best,
                            touch_index: 0,
                            touch: None,
                            approach: None,
                            approach_index: 0,
                            approach_clear: true,
                            wall_on: false,
                            sh_ts: [0; STRENGTH_HIST_SLOTS],
                            sh_e2: [-1; STRENGTH_HIST_SLOTS],
                            sh_len: 0,
                            sh_next: 0,
                            eat: EatRing::new(ts_ms.div_euclid(1_000), ob.size_lots),
                        },
                    );
                    newborns.push((s, ob.tick, ob.size_lots));
                    if let Some(r1) = r1.as_deref_mut() {
                        r1.birth(
                            key,
                            ts_ms,
                            best_own.unwrap_or(ob.tick),
                            carried_birth.is_some(),
                            (better_lots, better_n),
                        );
                    }
                }
            }
        }
        better_lots = better_lots.saturating_add(ob.size_lots);
        if ob.size_lots > 0 {
            near_better_tick = Some(ob.tick);
            better_n = better_n.saturating_add(1);
        }
    }
}

/// Перенос возраста действует только на первый кадр стороны: дальше
/// рождение — новое (W4в, фаза 3).
pub(super) fn close_carry_after_first_frame(
    carry: &mut SortedVec<(u8, i64), i64>,
    carry_open: &mut [bool; 2],
    s: u8,
) {
    if carry_open[s as usize] {
        carry_open[s as usize] = false;
        carry.retain(|k, _| k.0 != s);
    }
}

/// Живые уровни стороны `s`, готовые к свипу этого кадра — не видны в нём,
/// ушли за топ-50 или упали ниже 20% максимума (W4в, фаза 4). Свип по своей
/// стороне: кадр несёт одну сторону, и отсутствие тика читается как ноль
/// только в ней — уровни второй стороны не трогаются (иначе бид и аск
/// убивали бы друг друга по очереди на каждом штампе). `range` вместо обхода
/// всей карты — уровни стороны лежат подряд по ключу `(сторона, тик)`.
/// Итерация уже идёт по возрастанию цены — порядок выдачи детерминирован.
pub(super) fn detect_sweep(
    live: &SortedVec<(u8, i64), Live>,
    sweep: &mut Vec<(u8, i64)>,
    s: u8,
    frame: u64,
) {
    sweep.clear();
    for (key, lv) in live.range((s, i64::MIN), (s, i64::MAX)) {
        if lv.seen_frame != frame || !lv.seen_top50 || below_fraction(lv.seen_size, lv.max) {
            sweep.push(*key);
        }
    }
}

/// «Завал» касаний, начавшихся в этом кадре (В-45; W4в, фаза 5): диапазон
/// `live` в окне тиков вокруг цены уровня — без обхода всей стороны.
/// Умирающие в этом кадре не считаются — то же «после свипа», что и у смерти.
pub(super) fn compute_touch_stacks(
    live: &SortedVec<(u8, i64), Live>,
    touched: &mut [Touched],
    s: u8,
    frame: u64,
    mode: H3Mode,
) {
    for tk in touched.iter_mut() {
        let Some(t) = live.get(&tk.key).and_then(|lv| lv.touch) else {
            continue;
        };
        if t.start_frame != frame {
            continue;
        }
        let (n, next, _) = stack_around(live, s, tk.key.1, t.window_ticks, frame, mode);
        (tk.stack, tk.stack_next_tick) = (n, next);
    }
}

/// «Завал» вокруг цены `tick` стороны `s` на этом кадре: живые уровни в окне `±window_ticks` с
/// порогом стека, умирающие в кадре не считаются (после свипа), и ближайшая плотность **за**
/// ценой (бид — ниже, аск — выше). Общее правило касания (`compute_touch_stacks`) и взвода
/// подхода (`compute_arm_stacks`, T-28).
pub(super) fn stack_around(
    live: &SortedVec<(u8, i64), Live>,
    s: u8,
    tick: i64,
    window_ticks: i64,
    frame: u64,
    mode: H3Mode,
) -> (u32, Option<i64>, i64) {
    let lo = tick.saturating_sub(window_ticks);
    let hi = tick.saturating_add(window_ticks);
    let mut n: u32 = 0;
    // Обход по возрастанию тика: для бида берётся последняя ниже цены, для аска — первая выше.
    let mut next_behind: Option<i64> = None;
    // Первая и последняя плотность окна (по возрастанию тика) — расстояние завала (Г-98).
    let mut span: Option<(i64, i64)> = None;
    for ((_, lv_tick), lv) in live.range((s, lo), (s, hi)) {
        let dying =
            lv.seen_frame != frame || !lv.seen_top50 || below_fraction(lv.seen_size, lv.max);
        if !dying && lv.seen_strong && mode.passes_stack(*lv_tick, lv.seen_size) {
            n = n.saturating_add(1);
            span = Some(span.map_or((*lv_tick, *lv_tick), |(first, _)| (first, *lv_tick)));
            let behind = if s == side_key(Side::Bid) {
                *lv_tick < tick
            } else {
                *lv_tick > tick && next_behind.is_none()
            };
            if behind {
                next_behind = Some(*lv_tick);
            }
        }
    }
    let span_ticks = span.map_or(0, |(first, last)| last - first);
    (n, next_behind, span_ticks)
}

/// «Завал» на кадре взвода подходов этого кадра (T-28, Г-28 на подходе): то же правило и окно,
/// что у касания (`stack_around`, `stack_window_ticks`), пишется во взведённый `Approach`.
pub(super) fn compute_arm_stacks(
    live: &mut SortedVec<(u8, i64), Live>,
    armed: &[(u8, i64)],
    s: u8,
    frame: u64,
    mode: H3Mode,
) {
    for &key in armed {
        let (n, _, span) = stack_around(live, s, key.1, stack_window_ticks(key.1), frame, mode);
        if let Some(a) = live.get_mut(&key).and_then(|lv| lv.approach.as_mut()) {
            a.stack_levels = n;
            a.stack_span_bps = gap_bps(span, key.1);
        }
    }
}

/// Смерти этого свипа (W4в, фаза 6): убирает уровень из `live`, эмитит
/// оборванные смертью подход и касание (если были) и саму запись смерти.
/// Порядок `(сторона, тик)` уже задан обходом, которым `detect_sweep`
/// построил `sweep`.
#[allow(clippy::too_many_arguments)]
pub(super) fn resolve_deaths(
    wall_events: &mut Vec<WallEvent>,
    live: &mut SortedVec<(u8, i64), Live>,
    sweep: &mut Vec<(u8, i64)>,
    newborns: &[(u8, i64, i64)],
    touched: &[Touched],
    ts_ms: i64,
    frame: u64,
    warm_end: i64,
    out: &mut Vec<LevelRecord>,
    touches: &mut Vec<TouchRecord>,
    approaches: &mut Vec<ApproachRecord>,
    mut r1: Option<&mut R1State>,
) {
    for (ks, tick) in sweep.drain(..) {
        // Ключ только что найден в свипе, который построен обходом `live`
        // выше без единой вставки между, — отсутствие было бы дефектом
        // логики, а не данных. Паники при этом нет по режиму линтов:
        // в релизе дефект даст пропуск уровня (видимый), а в дебаге —
        // срабатывание ассёрта ниже.
        debug_assert!(live.get(&(ks, tick)).is_some());
        let Some(lv) = live.remove(&(ks, tick)) else {
            continue;
        };
        if lv.wall_on {
            wall_events.push(WallEvent {
                ts_ms,
                side: side_of((ks, tick)),
                price_tick: tick,
                wall: false,
            });
        }
        // Память смертей R1 ведётся и по уровням прогрева: «прошлая смерть» не зависит от эмиссии.
        if let Some(r1) = r1.as_deref_mut() {
            r1.on_death((ks, tick), &lv, ts_ms, newborns);
        }
        if lv.birth_ms < warm_end && !lv.carried {
            continue;
        }
        // Подход, не дождавшийся цены, снимается смертью уровня — перед
        // записью оборванного смертью касания.
        if let Some(a) = lv.approach {
            approaches.push(approach_record(
                (ks, tick),
                &lv,
                a,
                ts_ms,
                None,
                ApproachEnd::LevelDeath,
            ));
        }
        // Касание, оборванное смертью, идёт перед самой смертью: у
        // начавшегося в этом кадре «завал» — по свипу этого же кадра
        // (посчитан выше в `touched`).
        if let Some(t) = lv.touch {
            let stack = if t.start_frame == frame {
                touched
                    .iter()
                    .find(|tk| tk.key == (ks, tick))
                    .map_or((t.stack, t.stack_next_tick), |tk| {
                        (tk.stack, tk.stack_next_tick)
                    })
            } else {
                (t.stack, t.stack_next_tick)
            };
            touches.push(touch_record((ks, tick), &lv, t, ts_ms, stack, true));
        }
        let kind = if lv.seen_frame == frame && !lv.seen_top50 {
            DeathKind::LeftTop
        } else {
            DeathKind::BelowFraction
        };
        let repriced = newborns.iter().any(|&(ns, nt, nsize)| {
            ns == ks
                && nt.checked_sub(tick).is_some_and(|d| d == 1 || d == -1)
                && comparable_size(nsize, lv.max)
        });
        out.push(LevelRecord {
            side: side_of((ks, tick)),
            price_tick: tick,
            birth_ms: lv.birth_ms,
            death_ms: ts_ms,
            lifetime_ms: ts_ms - lv.birth_ms,
            size_max: lv.max,
            time_to_max_ms: lv.max_ms - lv.birth_ms,
            size_monotonic: lv.first_decrease_ms.is_none_or(|t| t > lv.max_ms),
            repeat_count: lv.repeat,
            repriced,
            death: kind,
            traded_lots: lv.traded,
            rpi_lots: lv.rpi,
        });
    }
}

/// Касания выживших (W4в, фаза 7): начавшимся в кадре — «завал» по свипу
/// (`compute_touch_stacks`), ушедшим с лучшей цены — запись и следующий
/// индекс. Ключ, которого в `live` уже нет, умер в `resolve_deaths` — его
/// касание уже выдано со смертью.
pub(super) fn finalize_surviving_touches(
    live: &mut SortedVec<(u8, i64), Live>,
    touched: &mut Vec<Touched>,
    ts_ms: i64,
    frame: u64,
    warm_end: i64,
    touches: &mut Vec<TouchRecord>,
) {
    for tk in touched.drain(..) {
        let key = tk.key;
        let Some(lv) = live.get_mut(&key) else {
            continue;
        };
        let Some(t) = &mut lv.touch else {
            continue;
        };
        if t.start_frame == frame {
            t.stack = tk.stack;
            t.stack_next_tick = tk.stack_next_tick;
        }
        if t.end_pending {
            let t = *t;
            if lv.birth_ms >= warm_end || lv.carried {
                touches.push(touch_record(
                    key,
                    lv,
                    t,
                    ts_ms,
                    (t.stack, t.stack_next_tick),
                    false,
                ));
            }
            lv.touch = None;
            lv.touch_index = lv.touch_index.saturating_add(1);
        }
    }
}
