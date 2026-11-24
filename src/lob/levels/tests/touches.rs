//! Касания (таск 35, В-42).

use super::*;

// ---------------------------------------------------------------------------
// Касания (таск 35, В-42).
// ---------------------------------------------------------------------------

/// Порог из тикета: `H3 = 5`, уровень — 10 лотов.
pub(super) fn cfg_touch() -> LevelsConfig {
    LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    }
}

pub(super) fn touch_rec(
    tick: i64,
    touch_index: u32,
    span: (i64, i64),
    sizes: (i64, i64),
    frontrun: i64,
    ended_by_death: bool,
    stack: u32,
) -> TouchRecord {
    TouchRecord {
        side: Side::Bid,
        price_tick: tick,
        touch_index,
        start_ms: span.0,
        end_ms: span.1,
        duration_ms: span.1 - span.0,
        level_birth_ms: 1000,
        size_at_touch: sizes.0,
        size_max_before: sizes.1,
        traded_during: 0,
        frontrun_lots: frontrun,
        frontrun_tick: None,
        swept_lots: frontrun,
        round_zeros: round_zeros(tick),
        ended_by_death,
        stack_levels: stack,
        stack_next_tick: None,
        traded_first_s: [0; 3],
        flow_1h_lots: 0,
        strength_e2: [-1, -1, -1],
        strength_held_e2: [-1, -1, -1, -1],
        repeat_count: 0,
        depth_behind_lots: 0,
    }
}

/// Критерий приёмки таска 35, фикстура тикета: бид-уровень 100 (10 лотов,
/// `H3 = 5`), лучший бид 101 (3 лота — не уровень). Кадр, где 101 исчез, —
/// уровень стал лучшим, касание началось; кадр, где 101 вернулся, — касание
/// кончилось, `touch_index = 0`, `frontrun_lots` — лоты 101 с последнего
/// кадра до касания, `traded_during` — сделка внутри касания; повтор —
/// `touch_index = 1`; смерть во время касания (в том же кадре, где 101
/// вернулся, — смерть имеет приоритет) — `ended_by_death`, `end_ms` —
/// `death_ms` записи смерти. Кадры идут раз в секунду, поэтому фронтран за
/// секунду до касания (В-45) совпадает со сметённым последним шагом
/// (`swept_lots`). `stack_levels` считает только живые уровни стороны с
/// размером не ниже `H3` в окне 25 bps от цены: у тика 100 окно — 0 тиков
/// (`stack_window_ticks`), в «завале» только сам уровень; 98 и 97 дальше.
/// Смерти уровней при этом — прежние.
#[test]
fn a_live_level_at_the_best_price_is_a_touch_with_index_frontrun_and_stack() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    tr.observe_frame_with_touches(
        1000,
        b,
        &[ob(101, 3), ob(100, 10), ob(99, 2), ob(98, 7), ob(97, 20)],
        &mut out,
        &mut touches,
    );
    assert_eq!(tr.live_count(), 3, "уровни 100, 98, 97");
    assert!(
        touches.is_empty(),
        "лучшая цена 101 — не уровень, касания нет"
    );
    // 101 исчез — 100 лучший: касание идёт, записи ещё нет.
    tr.observe_frame_with_touches(
        2000,
        b,
        &[ob(100, 12), ob(99, 2), ob(98, 7), ob(97, 4)],
        &mut out,
        &mut touches,
    );
    assert!(touches.is_empty(), "касание идёт — записи нет до конца");
    tr.observe_trade(TradeHit {
        tick: 100,
        lots: 4,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 2500,
    });
    // 101 вернулся — касание 0 кончилось уходом с лучшей цены.
    tr.observe_frame_with_touches(
        3000,
        b,
        &[ob(101, 4), ob(100, 12), ob(99, 2), ob(98, 7), ob(97, 4)],
        &mut out,
        &mut touches,
    );
    let mut want = touch_rec(100, 0, (2000, 3000), (12, 10), 3, false, 1);
    want.traded_during = 4;
    // E4: сделка на 4 лота с меткой 2500 — через 0.5 с после старта касания,
    // то есть внутри всех трёх окон реакции.
    want.traded_first_s = [4, 4, 4];
    // B2: цена первого фронтранера — ближайший лучший уровень (101), на том же
    // наблюдении, что лоты фронтрана.
    want.frontrun_tick = Some(101);
    // T2 (Г-07): позади 100 на кадре старта (2000) — 99(2) + 98(7) + 97(4) = 13.
    want.depth_behind_lots = 13;
    assert_eq!(touches, vec![want]);
    assert!(out.is_empty(), "смертей нет");
    touches.clear();
    // Повтор: 101 исчез снова — касание 1; фронтран — 4 лота 101 с кадра 3000.
    tr.observe_frame_with_touches(
        4000,
        b,
        &[ob(100, 12), ob(99, 2), ob(98, 7), ob(97, 4)],
        &mut out,
        &mut touches,
    );
    assert!(touches.is_empty());
    // 101 вернулся и в том же кадре 100 упал до 1 лота (< 20 % от 12):
    // смерть, а не уход с лучшей цены.
    tr.observe_frame_with_touches(
        5000,
        b,
        &[ob(101, 4), ob(100, 1), ob(99, 2), ob(98, 7), ob(97, 4)],
        &mut out,
        &mut touches,
    );
    let mut want = touch_rec(100, 1, (4000, 5000), (12, 12), 4, true, 1);
    // Фронтран снова 101 — и цена его та же (B2).
    want.frontrun_tick = Some(101);
    // Сила «×поток»: к старту второго касания (4000) за час прошла одна
    // сделка на 4 лота (метка 2500) — оборот 4.
    want.flow_1h_lots = 4;
    // T2 (Г-07): тот же кадр старта (4000), что у первого касания — 13.
    want.depth_behind_lots = 13;
    assert_eq!(touches, vec![want]);
    assert_eq!(out.len(), 1, "смерть 100");
    assert_eq!(out[0].price_tick, 100);
    assert_eq!(out[0].death, DeathKind::BelowFraction);
    assert_eq!(touches[0].end_ms, out[0].death_ms);
    assert_eq!(
        out[0].traded_lots, 4,
        "объём смерти — за всю жизнь, как раньше"
    );
    assert_eq!(tr.live_count(), 2, "98 и 97 живы");
}

/// Число нулей в конце десятичной записи тика — критерий приёмки:
/// `100 → 2`, `1010 → 1`, `1234 → 0`; потолок «3+»; ноль и знак.
#[test]
fn round_zeros_counts_trailing_decimal_zeros_up_to_three() {
    assert_eq!(round_zeros(100), 2);
    assert_eq!(round_zeros(1010), 1);
    assert_eq!(round_zeros(1234), 0);
    assert_eq!(round_zeros(1000), 3);
    assert_eq!(round_zeros(120_000), ROUND_ZEROS_CAP);
    assert_eq!(round_zeros(0), 0);
    assert_eq!(round_zeros(-100), 2);
    assert_eq!(round_zeros(i64::MIN), 0);
}

/// В-43: рождение лучшей ценой — не касание, и пока уровень остаётся лучшим
/// с рождения, касания нет; ушёл с лучшей цены и вернулся — касание с
/// индексом 0, фронтран — лоты, стоявшие перед ним на последнем кадре до
/// возврата. Аск — та же логика, индекс 0 — низший тик; `stack_levels`
/// включает сам уровень.
#[test]
fn a_level_born_at_the_best_price_touches_only_after_leaving_and_returning() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let a = Side::Ask;
    tr.observe_frame_with_touches(1000, a, &[ob(200, 10), ob(201, 3)], &mut out, &mut touches);
    tr.observe_frame_with_touches(2000, a, &[ob(200, 15), ob(201, 3)], &mut out, &mut touches);
    // Ушёл с лучшей цены: конца касания нет — его и не было.
    tr.observe_frame_with_touches(
        3000,
        a,
        &[ob(199, 2), ob(200, 15), ob(201, 3)],
        &mut out,
        &mut touches,
    );
    assert!(touches.is_empty(), "родился лучшим и ушёл — касаний ноль");
    // Вернулся — касание 0.
    tr.observe_frame_with_touches(4000, a, &[ob(200, 15), ob(201, 3)], &mut out, &mut touches);
    assert!(touches.is_empty());
    tr.observe_frame_with_touches(
        5000,
        a,
        &[ob(199, 4), ob(200, 15), ob(201, 3)],
        &mut out,
        &mut touches,
    );
    assert_eq!(
        touches,
        vec![TouchRecord {
            side: Side::Ask,
            price_tick: 200,
            touch_index: 0,
            start_ms: 4000,
            end_ms: 5000,
            duration_ms: 1000,
            level_birth_ms: 1000,
            size_at_touch: 15,
            size_max_before: 15,
            traded_during: 0,
            frontrun_lots: 2,
            frontrun_tick: Some(199),
            swept_lots: 2,
            round_zeros: 2,
            ended_by_death: false,
            stack_levels: 1,
            stack_next_tick: None,
            traded_first_s: [0; 3],
            flow_1h_lots: 0,
            // Окно 50 bps от тика 200 — один тик: сосед 199 (фронтран, 3 лота) даёт 15/3 = 500 %.
            strength_e2: [-1, -1, 50_000],
            strength_held_e2: [-1, -1, -1, -1],
            repeat_count: 0,
            // T2 (Г-07): кадр старта (4000) — [ob(200,15), ob(201,3)], позади 200 — 201(3).
            depth_behind_lots: 3,
        }]
    );
    assert_eq!(touches[0].age_ms(), 3000);
    assert!(out.is_empty());
}

/// В-43, кадры одной миллисекунды — один момент: уровень, родившийся не
/// лучшим и ставший лучшим другим кадром той же метки, читается как
/// родившийся лучшим — касания нет ни в этом кадре, ни пока он остаётся
/// лучшим; после ухода и возврата — касание 0.
#[test]
fn becoming_best_within_the_birth_millisecond_is_not_a_touch() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    tr.observe_frame_with_touches(1000, b, &[ob(101, 3), ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(1000, b, &[ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(2000, b, &[ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(3000, b, &[ob(101, 3), ob(100, 10)], &mut out, &mut touches);
    assert!(touches.is_empty());
    tr.observe_frame_with_touches(4000, b, &[ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(5000, b, &[ob(101, 3), ob(100, 10)], &mut out, &mut touches);
    assert_eq!(touches.len(), 1);
    assert_eq!(
        (
            touches[0].touch_index,
            touches[0].start_ms,
            touches[0].frontrun_lots
        ),
        (0, 4000, 3)
    );
}

/// Касания уровней прогрева считаются (индекс растёт), но не эмитируются —
/// тот же режим, что у их смертей; уровень, родившийся после прогрева,
/// эмитирует касание с индексом 0.
#[test]
fn warmup_touches_are_tracked_but_not_emitted() {
    let mut tr = LevelTracker::new(LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: 5000,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    });
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    // Прогревный 100: родился не лучшим, стал лучшим, ушёл — касание было,
    // записи нет.
    tr.observe_frame_with_touches(1000, b, &[ob(101, 3), ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(2000, b, &[ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(3000, b, &[ob(101, 3), ob(100, 10)], &mut out, &mut touches);
    assert!(
        touches.is_empty(),
        "касание прогревного уровня не эмитируется"
    );
    // 102 после прогрева: родился лучшим (не касание), ушёл, вернулся, ушёл.
    tr.observe_frame_with_touches(
        7000,
        b,
        &[ob(102, 10), ob(101, 3), ob(100, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        8000,
        b,
        &[ob(103, 3), ob(102, 10), ob(101, 3), ob(100, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        9000,
        b,
        &[ob(102, 10), ob(101, 3), ob(100, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        10_000,
        b,
        &[ob(103, 3), ob(102, 10), ob(101, 3), ob(100, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!((touches[0].price_tick, touches[0].touch_index), (102, 0));
    assert_eq!((touches[0].start_ms, touches[0].frontrun_lots), (9000, 3));
    assert_eq!(
        touches[0].stack_levels, 1,
        "102 в окне 0 тиков (25 bps от 102 тиков) — только сам; прогревный 100 дальше"
    );
    // 102 снят (смерть без касания — оно уже кончилось), 100 снова лучший:
    // второе касание прогревного уровня — по-прежнему без записи.
    tr.observe_frame_with_touches(11_000, b, &[ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(
        12_000,
        b,
        &[ob(101, 3), ob(100, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!(out.len(), 1, "смерть 102");
    assert_eq!(out[0].price_tick, 102);
}

/// Критерий приёмки тикета 35b (В-45): фронтран — за секунду до касания, а
/// не с последнего кадра. Бид 100 000 (10 лотов) родился под лучшим
/// 100 001 (5 лотов); за 1,5 с до касания впереди стоят те же 5 лотов, на
/// последнем кадре до касания (за 200 мс) — 1 лот (ноль впереди на кадре до
/// касания невозможен по определению: там уровень уже был бы лучшим, и
/// касание началось бы кадром раньше). Ожидание: `frontrun_lots = 5`,
/// `swept_lots = 1`. Обратный случай — за секунду до касания впереди 0
/// (уровень стоял лучшим с рождения — не касание), на последнем кадре 4
/// лота: `frontrun_lots = 0`, `swept_lots = 4` — корзина «фронтран 0»
/// достижима.
#[test]
fn frontrun_is_sampled_a_second_before_the_touch_and_swept_is_the_last_frame() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    let tick = 100_000;
    // Рождение под лучшим 100 001 (5 лотов): секунда 0.
    tr.observe_frame_with_touches(
        0,
        b,
        &[ob(tick + 1, 5), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    // 500 мс: впереди по-прежнему 5. 1000 мс: слот перевернулся, впереди 5.
    tr.observe_frame_with_touches(
        500,
        b,
        &[ob(tick + 1, 5), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        1000,
        b,
        &[ob(tick + 1, 5), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    // 2300 мс: впереди 1 лот (последний кадр до касания). 2500 мс: касание.
    tr.observe_frame_with_touches(
        2300,
        b,
        &[ob(tick + 1, 1), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(2500, b, &[ob(tick, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(
        3000,
        b,
        &[ob(tick + 1, 2), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!(
        (touches[0].frontrun_lots, touches[0].swept_lots),
        (5, 1),
        "за секунду до касания (кадр 1000 мс) впереди 5, на последнем кадре (2300 мс) — 1"
    );
    touches.clear();

    // Обратный случай: аск 200 000 родился лучшим (впереди 0), 1500 мс стоял
    // лучшим, затем перед ним встали 4 лота на 199 999 и через 200 мс ушли —
    // касание с фронтраном 0 и сметённым 4.
    let a = Side::Ask;
    let at = 200_000;
    tr.observe_frame_with_touches(10_000, a, &[ob(at, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(11_000, a, &[ob(at, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(11_500, a, &[ob(at, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(
        11_800,
        a,
        &[ob(at - 1, 4), ob(at, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(12_000, a, &[ob(at, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(
        13_000,
        a,
        &[ob(at - 1, 1), ob(at, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!(touches[0].side, Side::Ask);
    assert_eq!(
        (touches[0].frontrun_lots, touches[0].swept_lots),
        (0, 4),
        "за секунду до касания впереди ничего не было, последний шаг смёл 4"
    );
    assert!(out.is_empty());
}

/// В-45: два слота фронтрана — значение с кадра не позже секунды до касания
/// даже когда слот перевернулся в самом кадре касания: наблюдения на 0, 999
/// и касание на 1000 мс — читается кадр 0 (первое наблюдение старого слота),
/// а не 999 (его последнее, оно позже секунды до касания). Уровень моложе
/// секунды — его первое наблюдение (кадр рождения).
#[test]
fn frontrun_never_reads_a_frame_later_than_a_second_before_the_touch() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    let tick = 100_000;
    tr.observe_frame_with_touches(
        0,
        b,
        &[ob(tick + 1, 7), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        999,
        b,
        &[ob(tick + 1, 2), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(1000, b, &[ob(tick, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(
        1100,
        b,
        &[ob(tick + 1, 1), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!((touches[0].frontrun_lots, touches[0].swept_lots), (7, 2));
    touches.clear();

    // Моложе секунды: рождение на 5000 под 3 лотами, касание на 5400.
    let young = 300_000;
    tr.observe_frame_with_touches(
        5000,
        b,
        &[
            ob(young + 1, 3),
            ob(young, 10),
            ob(tick + 1, 1),
            ob(tick, 10),
        ],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        5200,
        b,
        &[
            ob(young + 1, 6),
            ob(young, 10),
            ob(tick + 1, 1),
            ob(tick, 10),
        ],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        5400,
        b,
        &[ob(young, 10), ob(tick + 1, 1), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        5600,
        b,
        &[
            ob(young + 1, 1),
            ob(young, 10),
            ob(tick + 1, 1),
            ob(tick, 10),
        ],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!(touches[0].price_tick, young);
    assert_eq!(
        (touches[0].frontrun_lots, touches[0].swept_lots),
        (3, 6),
        "старого слота нет — первое наблюдение (кадр рождения)"
    );
}

/// Критерий приёмки тикета 35b (В-45): «завал» — уровни ≥ `H3` той же
/// стороны не дальше 25 bps от цены уровня. У бида 100 000 окно — 250 тиков
/// (`stack_window_ticks`): 99 800 (200 тиков) считается, 99 700 (300 тиков)
/// — нет, сам уровень входит; уровень ниже `H3` в окне не считается.
#[test]
fn stack_counts_levels_within_25_bps_only() {
    assert_eq!(stack_window_ticks(100_000), 250);
    assert_eq!(
        stack_window_ticks(100),
        0,
        "25 bps от 100 тиков — четверть тика"
    );
    assert_eq!(stack_window_ticks(399), 0);
    assert_eq!(stack_window_ticks(400), 1);
    assert_eq!(stack_window_ticks(0), 0);
    assert_eq!(
        stack_window_ticks(-100_000),
        250,
        "модуль: арифметика тотальна"
    );

    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    let tick = 100_000;
    let frame_before = [
        ob(tick + 1, 3),
        ob(tick, 10),
        ob(tick - 200, 10),
        ob(tick - 250, 20),
        ob(tick - 251, 20),
        ob(tick - 300, 30),
    ];
    let frame_touch = [
        ob(tick, 10),
        ob(tick - 200, 10),
        ob(tick - 250, 20),
        ob(tick - 251, 20),
        ob(tick - 300, 30),
    ];
    tr.observe_frame_with_touches(1000, b, &frame_before, &mut out, &mut touches);
    assert_eq!(tr.live_count(), 5);
    tr.observe_frame_with_touches(2000, b, &frame_touch, &mut out, &mut touches);
    tr.observe_frame_with_touches(3000, b, &frame_before, &mut out, &mut touches);
    assert_eq!(touches.len(), 1);
    assert_eq!(
        touches[0].stack_levels, 3,
        "сам 100 000, 99 800 и 99 750 (ровно 250 тиков — включительно); 99 749 и 99 700 дальше"
    );
    assert!(out.is_empty());
}

/// Гейт GC таска 35: кадры с касаниями — начало и конец на каждом кадре,
/// сделки внутри — после прогрева не аллоцируют; то же для `observe_frame`
/// без выхода касаний (буфер-заглушка переиспользуется).
#[test]
fn touching_frames_allocate_nothing() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    let with_best = [ob(101, 3), ob(100, 10), ob(98, 7)];
    let level_best = [ob(100, 10), ob(98, 7)];
    // Прогрев: рождения, касание, конец касания растягивают буферы.
    tr.observe_frame_with_touches(1000, b, &with_best, &mut out, &mut touches);
    tr.observe_frame_with_touches(2000, b, &level_best, &mut out, &mut touches);
    tr.observe_frame_with_touches(3000, b, &with_best, &mut out, &mut touches);
    tr.observe_frame(4000, b, &level_best, &mut out);
    tr.observe_frame(5000, b, &with_best, &mut out);
    assert_eq!(touches.len(), 1);
    touches.clear();

    let (_, counts) = crate::alloc_count::measure(|| {
        for i in 0..1000i64 {
            let ts = 6000 + 2 * i;
            tr.observe_frame_with_touches(ts, b, &level_best, &mut out, &mut touches);
            tr.observe_trade(TradeHit {
                tick: 100,
                lots: 1,
                aggressor_is_buy: false,
                block: false,
                rpi: false,
                exch_ms: ts,
            });
            tr.observe_frame_with_touches(ts + 1, b, &with_best, &mut out, &mut touches);
            touches.clear();
            tr.observe_frame(ts + 1, b, &level_best, &mut out);
            tr.observe_frame(ts + 1, b, &with_best, &mut out);
        }
    });
    assert_eq!(counts.allocations, 0, "касания обязаны не аллоцировать");
    assert_eq!(tr.live_count(), 2);
    assert!(out.is_empty(), "смертей не было");
}
