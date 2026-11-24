//! Подход (F1 этапа F, В-73): взвод «на подходе» и снятие; переносы рождений через полночь.

use super::*;

// ---------------------------------------------------------------------------
// Подход (F1 этапа F, В-73): взвод «на подходе» и снятие.
// ---------------------------------------------------------------------------

/// Конфиг сигнала подхода: порог `H3 = 5` (как у касаний), полоса `d` bps,
/// пол возраста взвода `min_age_ms`.
pub(super) fn cfg_approach(d: i64, min_age_ms: i64) -> LevelsConfig {
    LevelsConfig {
        mode: H3Mode::Floor { h3_lots: 5 },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: Some(d),
        approach_min_age_ms: min_age_ms,
    }
}

/// Кадр одной стороны с выходом подходов — как `feed_frames`: бид, затем аск.
pub(super) fn frame(
    tr: &mut LevelTracker,
    ts_ms: i64,
    side: Side,
    levels: &[LevelObs],
    out: &mut Vec<LevelRecord>,
    touches: &mut Vec<TouchRecord>,
    ap: &mut Vec<ApproachRecord>,
) {
    tr.observe_frame_with_approaches(ts_ms, side, levels, out, touches, ap);
}

/// Фикстура подхода: бид-стена 10 000 (10 лотов) за лучшим бидом 10 020
/// (3 лота — не уровень), аск то 10 060 (60 bps от стены), то 10 015 (15 bps).
/// Сторона кадра смотрит на **чужую** лучшую цену: бид-кадр видит аск
/// прошлого кадра.
pub(super) const WALL: [LevelObs; 2] = [
    LevelObs {
        tick: 10020,
        size_lots: 3,
        in_top50: true,
    },
    LevelObs {
        tick: 10000,
        size_lots: 10,
        in_top50: true,
    },
];

/// Взвод при входе в полосу и снятие касанием (F1, критерий приёмки):
/// цена аска 60 bps — вне полосы 20; 15 bps — взвод (возраст 2 с ≥ 500 мс);
/// стена стала лучшей ценой — подход снят касанием с `touch_start_ms`.
#[test]
fn approach_arms_within_the_band_and_disarms_on_touch() {
    let mut tr = LevelTracker::new(cfg_approach(20, 500));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10060, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert!(ap.is_empty(), "60 bps — вне полосы");
    // Аск подошёл на 15 bps; на следующем бид-кадре — взвод (записи ещё нет).
    frame(
        &mut tr,
        2000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert!(ap.is_empty(), "взвод — не запись; запись на снятии");
    // 10 020 исчез — стена стала лучшей ценой: касание и снятие подхода.
    frame(
        &mut tr,
        4000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(
        ap,
        vec![ApproachRecord {
            side: Side::Bid,
            price_tick: 10000,
            approach_index: 0,
            arm_ms: 3000,
            arm_dist_bps: 15,
            level_birth_ms: 1000,
            size_at_arm: 10,
            best_own_tick: 10020,
            best_opp_tick: 10015,
            flow_1h_lots: 0,
            // Кадр взвода: сосед 10 020 (3 лота) входит в ±20 и ±50 bps,
            // в ±10 bps (10 тиков) — нет.
            strength_e2: [-1, 33_333, 33_333],
            depth_behind_lots: 0,
            // T-28: на кадре взвода в окне стека одна плотность — сама стена;
            // впереди неё (строго лучше) 10 020 с 3 лотами за секунду до взвода.
            stack_levels_at_arm: 1,
            stack_span_bps_at_arm: 0,
            repeat_count_at_arm: 0,
            frontrun_lots_at_arm: 3,
            p08: Some(ArmP08 {
                traded_lots: 0,
                size_max: 10,
                size_monotonic: true,
                eat_60s_lots: 0,
                size_max_60s_lots: 10,
                depth_behind50_lots: 0,
            }),
            r1: None,
            touch_start_ms: Some(4000),
            disarm_ms: 4000,
            disarm_reason: ApproachEnd::Touch,
        }]
    );
    assert_eq!(ap[0].age_ms(), 2000);
    assert_eq!(ap[0].duration_ms(), 1000);
    assert!(touches.is_empty(), "касание идёт — записи ещё нет");
    assert!(out.is_empty());
}

/// Г-98: расстояние завала на взводе — bps между первой и последней плотностью окна стека:
/// стена 10 000 и плотность 9 980 (7 лотов, внутри окна 25 тиков) — две плотности, 20 тиков
/// при цене 10 000 = 20 bps; без второй плотности — 0.
#[test]
fn approach_stack_span_bps_measures_first_to_last_density() {
    let mut tr = LevelTracker::new(cfg_approach(20, 500));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    let bid = [ob(10020, 3), ob(10000, 10), ob(9980, 7)];
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &bid,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &bid,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        4000,
        Side::Bid,
        &[ob(10000, 10), ob(9980, 7)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    let wall = ap
        .iter()
        .find(|a| a.price_tick == 10000)
        .expect("подход стены");
    assert_eq!(wall.stack_levels_at_arm, 2);
    assert_eq!(wall.stack_span_bps_at_arm, 20);
}

/// T2 (Г-07): направленная глубина на подходе — та же стена 10 000, но за
/// ней (дальше от середины) стоит ещё одна плотность 9950 (7 лотов), вне
/// окна `DISTANCE_MAX_BPS`/арм-порога. Она не влияет на взвод (порог видит
/// только сам уровень стены), но входит в `depth_behind_lots`: весь остаток
/// кадра позади уровня, не симметричное окно `stack_levels`.
#[test]
fn approach_depth_behind_lots_sums_the_frame_behind_the_wall() {
    let mut tr = LevelTracker::new(cfg_approach(20, 500));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    let wall_with_behind: [LevelObs; 3] = [
        LevelObs {
            tick: 10020,
            size_lots: 3,
            in_top50: true,
        },
        LevelObs {
            tick: 10000,
            size_lots: 10,
            in_top50: true,
        },
        LevelObs {
            tick: 9950,
            size_lots: 7,
            in_top50: true,
        },
    ];
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10060, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert!(ap.is_empty(), "60 bps — вне полосы");
    // Аск подошёл на 15 bps; на следующем бид-кадре — взвод (та же
    // последовательность, что у `approach_arms_within_the_band_and_disarms_on_touch`,
    // только кадр взвода несёт лишнюю плотность 9950 позади стены).
    frame(
        &mut tr,
        2000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &wall_with_behind,
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert!(
        ap.is_empty(),
        "взвод — не запись; запись на снятии (как в соседнем тесте)"
    );
    // 10 020 исчез — стена стала лучшей ценой: касание и снятие подхода
    // записывает `ApproachRecord` со снимком полей взвода (кадр 3000).
    frame(
        &mut tr,
        4000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1);
    assert_eq!(
        ap[0].depth_behind_lots, 7,
        "позади стены 10000 на кадре взвода (3000) — только 9950 (7 лотов)"
    );
}

/// Снятие уходом цены за `2 × D` и повторный взвод: 60 bps > 40 — снятие
/// (без касания), 15 bps после ухода — новый взвод с индексом 1.
#[test]
fn approach_disarms_when_price_leaves_and_arms_again() {
    let mut tr = LevelTracker::new(cfg_approach(20, 0));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10060, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    // Аск вернулся на 60 bps: бид-кадр 5000 видит уход — снятие по цене.
    frame(
        &mut tr,
        4000,
        Side::Ask,
        &[ob(10060, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        5000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(ap[0].disarm_reason, ApproachEnd::PriceLeft);
    assert_eq!(ap[0].disarm_ms, 5000);
    assert_eq!(ap[0].touch_start_ms, None);
    assert_eq!(ap[0].approach_index, 0);
    assert_eq!(ap[0].duration_ms(), 2000);
    // Снова подошёл — взвод с индексом 1; стена стала лучшей — снятие касанием.
    frame(
        &mut tr,
        6000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        7000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        8000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 2, "{ap:?}");
    assert_eq!(ap[1].approach_index, 1);
    assert_eq!(ap[1].arm_ms, 7000);
    assert_eq!(ap[1].disarm_reason, ApproachEnd::Touch);
    assert_eq!(ap[1].disarm_ms, 8000);
}

/// Смерть уровня снимает взведённый подход (`LevelDeath`), и только тогда:
/// пока стена жива и цена в полосе, записи нет.
#[test]
fn approach_disarms_on_level_death() {
    let mut tr = LevelTracker::new(cfg_approach(20, 0));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert!(ap.is_empty());
    // Размер стены упал до 1 лота (< 20 % от 10) — смерть: подход снят ею.
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &[ob(10020, 3), ob(10000, 1)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(out.len(), 1, "смерть уровня записана");
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(ap[0].disarm_reason, ApproachEnd::LevelDeath);
    assert_eq!(ap[0].disarm_ms, 3000);
    assert_eq!(ap[0].arm_ms, 2000);
    assert_eq!(ap[0].touch_start_ms, None);
}

/// Пол возраста (В-71) держит взвод: при `min_age = 5 с` подход не взводится
/// в 3 с (возраст 2 с) и взводится в 6 с (возраст 5 с).
#[test]
fn approach_waits_for_the_age_floor() {
    let mut tr = LevelTracker::new(cfg_approach(20, 5_000));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    for ts in [2000, 3000, 4000, 5000] {
        frame(
            &mut tr,
            ts,
            Side::Bid,
            &WALL,
            &mut out,
            &mut touches,
            &mut ap,
        );
    }
    assert!(ap.is_empty(), "возраст меньше пола — взвода нет");
    frame(
        &mut tr,
        6000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        7000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(ap[0].arm_ms, 6000, "взвод на кадре возраста 5 с");
    assert_eq!(ap[0].disarm_reason, ApproachEnd::Touch);
}

/// Пока стена — лучшая цена своей стороны, подход не взводится (В-73: «в
/// упор» вход не ставим): цена уже дошла, это касание, а не подход.
#[test]
fn approach_does_not_arm_while_the_wall_is_best() {
    let mut tr = LevelTracker::new(cfg_approach(20, 0));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    // Стена сразу лучший бид (родилась лучшей — не касание по В-43).
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &[ob(10000, 10), ob(9990, 2)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    for ts in [2000, 3000, 4000] {
        frame(
            &mut tr,
            ts,
            Side::Bid,
            &[ob(10000, 10), ob(9990, 2)],
            &mut out,
            &mut touches,
            &mut ap,
        );
    }
    assert!(ap.is_empty(), "стена лучшая — подхода нет");
    // Ушла с лучшей цены к 10 020 — взвод возможен уже здесь.
    frame(
        &mut tr,
        5000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        6000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(ap[0].arm_ms, 5000);
}

/// Гейт F1: без `approach_bps` записи подходов прежние, а записи уровней и
/// касания при включённом сигнале — те же байты (состояние подхода их не
/// трогает).
#[test]
fn approaches_do_not_change_levels_and_touches() {
    let script: [(i64, Side, &[LevelObs]); 8] = [
        (1000, Side::Bid, &WALL),
        (1000, Side::Ask, &[ob(10060, 4)]),
        (2000, Side::Ask, &[ob(10015, 4)]),
        (3000, Side::Bid, &WALL),
        (4000, Side::Bid, &[ob(10000, 10)]),
        (5000, Side::Bid, &WALL),
        (6000, Side::Ask, &[ob(10060, 4)]),
        (7000, Side::Bid, &[ob(10020, 3), ob(10000, 1)]),
    ];
    let run = |cfg: LevelsConfig| {
        let mut tr = LevelTracker::new(cfg);
        let mut out = Vec::new();
        let mut touches = Vec::new();
        let mut ap = Vec::new();
        for &(ts, side, levels) in script.iter() {
            frame(&mut tr, ts, side, levels, &mut out, &mut touches, &mut ap);
        }
        (out, touches, ap)
    };
    let (out_off, touches_off, ap_off) = run(cfg_touch());
    let (out_on, touches_on, ap_on) = run(cfg_approach(20, 0));
    assert_eq!(out_off, out_on, "записи уровней не меняются");
    assert_eq!(touches_off, touches_on, "касания не меняются");
    assert!(ap_off.is_empty(), "без флага записей подхода нет");
    assert!(!ap_on.is_empty(), "с флагом — есть");
}

/// Порог В-66 у подхода — тот же, что у касания: слабый «×соседи» уровень не
/// взводится, сильный взводится; окно порога не из оси — взвода нет.
#[test]
fn approach_holds_the_same_strength_gate_as_touch() {
    // Стена 10 лотов, сосед 1 лот в ±20 bps: сила 1000 %. Порог 200 % — ок.
    let weak_neighbour = [ob(10020, 1), ob(10000, 10)];
    let strong_gate = LevelsConfig {
        mode: H3Mode::Strength {
            pct_e2: 20_000,
            window_bps_e2: 2_000,
        },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: Some(20),
        approach_min_age_ms: 0,
    };
    let mut tr = LevelTracker::new(strong_gate);
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &weak_neighbour,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Bid,
        &weak_neighbour,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(ap[0].disarm_reason, ApproachEnd::Touch);
    // Окно порога 15 bps не из оси `STRENGTH_WINDOWS_BPS` — взвода нет.
    let odd_gate = LevelsConfig {
        mode: H3Mode::Strength {
            pct_e2: 20_000,
            window_bps_e2: 1_500,
        },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: Some(20),
        approach_min_age_ms: 0,
    };
    let mut tr = LevelTracker::new(odd_gate);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &weak_neighbour,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Bid,
        &weak_neighbour,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert!(ap.is_empty(), "порог не проверить — взвода нет");
}

/// Перенос возраста через полночь (аудит дизайна 22.09 Т3): уровень, стоящий на той же цене в
/// первом кадре стороны новых суток, наследует рождение прошлых суток — касание несёт возраст
/// с настоящего рождения. Уровень, появившийся позже первого кадра, — новорождённый. Без
/// переноса (`new`) и в режиме с прогревом рождение — начало суток.
#[test]
fn carried_births_survive_midnight_only_for_levels_in_the_first_frame() {
    let b = Side::Bid;
    let mut out = Vec::new();
    let mut touches = Vec::new();
    let mut day1 = LevelTracker::new(cfg_touch());
    day1.observe_frame_with_touches(
        1000,
        b,
        &[ob(101, 3), ob(100, 10), ob(98, 7), ob(97, 20)],
        &mut out,
        &mut touches,
    );
    let carry = day1.live_births();
    assert_eq!(carry.len(), 3, "100, 98, 97");

    let day2_frames = |tr: &mut LevelTracker, touches: &mut Vec<TouchRecord>| {
        let mut out = Vec::new();
        const D2: i64 = 90_000_000;
        // Первый кадр суток: 100 и 97 стоят, 98 нет.
        tr.observe_frame_with_touches(
            D2,
            b,
            &[ob(101, 3), ob(100, 10), ob(97, 20)],
            &mut out,
            touches,
        );
        // 98 вернулся позже первого кадра; 101 исчез — касание 100.
        tr.observe_frame_with_touches(
            D2 + 1000,
            b,
            &[ob(100, 12), ob(98, 7), ob(97, 20)],
            &mut out,
            touches,
        );
        // 101 вернулся — касание кончилось.
        tr.observe_frame_with_touches(
            D2 + 2000,
            b,
            &[ob(101, 4), ob(100, 12), ob(98, 7), ob(97, 20)],
            &mut out,
            touches,
        );
    };

    let mut carried = LevelTracker::with_carried_births(cfg_touch(), carry.clone());
    let mut t_carried = Vec::new();
    day2_frames(&mut carried, &mut t_carried);
    assert_eq!(t_carried.len(), 1);
    assert_eq!(t_carried[0].level_birth_ms, 1000, "возраст с прошлых суток");
    let births = carried.live_births();
    let birth_at = |key: (u8, i64)| -> i64 {
        births
            .iter()
            .find(|&&(k, _)| k == key)
            .map(|&(_, birth_ms)| birth_ms)
            .unwrap_or_else(|| panic!("ключ {key:?} не найден в переносе"))
    };
    assert_eq!(
        birth_at((side_key(b), 97)),
        1000,
        "97 стоял в первом кадре — перенос"
    );
    assert_eq!(
        birth_at((side_key(b), 98)),
        90_001_000,
        "98 появился позже — новорождённый"
    );

    let mut fresh = LevelTracker::new(cfg_touch());
    let mut t_fresh = Vec::new();
    day2_frames(&mut fresh, &mut t_fresh);
    assert_eq!(t_fresh.len(), 1);
    assert_eq!(
        t_fresh[0].level_birth_ms, 90_000_000,
        "без переноса — начало суток"
    );

    let warm = LevelsConfig {
        warmup_ms: HOUR_MS,
        ..cfg_touch()
    };
    let with_warmup = LevelTracker::with_carried_births(warm, carry);
    assert_eq!(
        with_warmup.carry.len(),
        0,
        "режим с прогревом перенос не принимает"
    );
}

/// Чистка `births` (W4а): цена, на которой уровень родился и умер один раз
/// и больше никогда не рождался заново, не должна вечно занимать место в
/// карте рождений — очередь, чей `count_prior_births` не зовут повторно,
/// никогда сама себя не тримит. `cleanup_births` обходит карту целиком не
/// чаще раза в `repeat_window_ms`; здесь окно короткое, чтобы кадры теста
/// оставались маленькими числами.
#[test]
fn births_map_forgets_a_price_that_never_rebirths_after_the_window() {
    let short_window = LevelsConfig {
        repeat_window_ms: 1_000,
        ..cfg()
    };
    let mut tr = LevelTracker::new(short_window);
    let mut out = Vec::new();

    // Рождение на 1000, немедленная смерть (тик исчезает из следующего
    // кадра — читается как размер ноль, ниже 20% максимума).
    tr.observe_frame(0, Side::Bid, &[ob(1000, 200)], &mut out);
    assert_eq!(tr.births.len(), 1, "запись рождения на 1000 появилась");
    out.clear();
    tr.observe_frame(10, Side::Bid, &[], &mut out);
    assert_eq!(out.len(), 1, "1000 умер");
    out.clear();

    // Кадр внутри старого окна: чистка ещё не должна снести свежую запись.
    tr.observe_frame(500, Side::Bid, &[ob(2000, 200)], &mut out);
    out.clear();
    assert!(
        tr.births.get(&(side_key(Side::Bid), 1000)).is_some(),
        "1000 моложе окна — чистка его ещё не трогает"
    );

    // Кадр за окном от последнего рождения на 1000 (t=0): следующее рождение
    // на другой цене запускает периодическую чистку, и запись 1000 уходит
    // вместе с опустевшей очередью, а не висит нулевой длины.
    tr.observe_frame(2_000, Side::Bid, &[ob(9999, 200)], &mut out);
    assert!(
        tr.births.get(&(side_key(Side::Bid), 1000)).is_none(),
        "запись истёкшей цены удалена целиком, а не оставлена пустой"
    );
    assert!(
        tr.births.get(&(side_key(Side::Bid), 9999)).is_some(),
        "свежее рождение по-прежнему учтено"
    );
}

/// Р9 (T-17): порог номинала без деления — ровно `size ≥ notional_lots_at(…)`, включая края
/// `clamp(1, i64::MAX)` (нулевой номинал, цена ≤ 0, порог выше `i64::MAX`) и размеры вокруг порога.
#[test]
fn notional_passes_matches_lots_at() {
    let usds = [0i64, 1, 10_000_000_000_000, 123_456_789_012, i64::MAX / 4];
    let tick_e9s = [1i64, 10, 100_000, 1_000_000_000];
    let step_e9s = [1i64, 1_000, 1_000_000_000];
    let ticks = [-5i64, 0, 1, 7, 12_345, 1_000_000_007];
    let sizes = [
        i64::MIN,
        -1,
        0,
        1,
        2,
        3,
        99,
        100,
        101,
        1_000_000,
        i64::MAX - 1,
        i64::MAX,
    ];
    for &u in &usds {
        for &te in &tick_e9s {
            for &se in &step_e9s {
                for &t in &ticks {
                    let lots = H3Mode::notional_lots_at(u, te, se, t);
                    let around = [lots.saturating_sub(1), lots, lots.saturating_add(1)];
                    for &s in sizes.iter().chain(around.iter()) {
                        assert_eq!(
                            H3Mode::notional_passes(u, te, se, t, s),
                            s >= lots,
                            "usd={u} tick_e9={te} step_e9={se} tick={t} size={s} lots={lots}"
                        );
                    }
                }
            }
        }
    }
}
